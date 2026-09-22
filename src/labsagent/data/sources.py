"""Getting a file, from wherever the lab says it lives.

Three providers behind one `resolve()`: a path already on disk, an http(s) URL,
and a Kaggle slug. Adding a fourth means adding a branch here and nothing else,
which is the same bargain `Sandbox` and `Emitter` offer.

THE URL PROVIDER IS THE SECURITY-SENSITIVE ONE, because it is a server fetching
an address chosen by whoever wrote the document. That is server-side request
forgery in its textbook form: "download the data from http://169.254.169.254/..."
is a sentence a lab manual could plausibly contain and a cloud metadata endpoint
could plausibly answer. So the guard is not "looks like a URL" but:

  - http and https only, at the start AND after every redirect
  - the resolved address must be a public one -- loopback, private, link-local,
    reserved and multicast ranges are all refused
  - a byte cap enforced while streaming, not after
  - a connect and read timeout, so a slow endpoint cannot hold a worker thread

The known gap is DNS rebinding: we resolve the name to check it, and urllib
resolves it again to connect, so a hostile resolver could answer differently the
second time. Closing that needs connection-level address pinning, which is a
real amount of code for a tool that today runs on one machine for one person.
Recorded here rather than left to be discovered -- and it is the thing to fix
first if this ever serves more than one user.

ARCHIVES ARE EXTRACTED WITH THE SAME SUSPICION. A zip entry named
"../../.ssh/authorized_keys" is the oldest trick there is; every member is
resolved and checked to be inside the destination before anything is written.
"""

from __future__ import annotations

import ipaddress
import os
import re
import shutil
import socket
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from labsagent.data import Acquisition, DATA_SUFFIXES, MAX_DATASET_BYTES, Dataset
from labsagent.data.preview import profile
from labsagent.errors import DataError

#: Seconds before a stalled fetch is abandoned. A dataset is a convenience; a
#: hung worker thread is a job the browser waits on forever.
FETCH_TIMEOUT_S = 30

#: How many files we keep out of one archive or Kaggle dataset. A Kaggle
#: release can hold hundreds; the prompt can carry a handful.
MAX_FILES_PER_REF = 8

#: "owner/dataset-name" and nothing else -- no scheme, no spaces, exactly one
#: slash. Narrow on purpose: "data/train.csv" is a path, not a Kaggle slug, and
#: guessing wrong sends a local filename to a remote API.
KAGGLE_SLUG = re.compile(r"^[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*$")

_KAGGLE_HOSTS = ("kaggle.com", "www.kaggle.com")

USER_AGENT = "labsagent/0.1 (+dataset fetch)"


# --------------------------------------------------------------- classifying


def kaggle_slug(ref: str) -> str | None:
    """`owner/name` from a bare slug or a kaggle.com dataset URL, else None."""
    ref = (ref or "").strip().rstrip("/")
    if not ref:
        return None

    parsed = urllib.parse.urlparse(ref)
    if parsed.scheme in ("http", "https") and parsed.netloc.lower() in _KAGGLE_HOSTS:
        parts = [p for p in parsed.path.split("/") if p]
        # /datasets/<owner>/<name> and the older /<owner>/<name>
        if parts and parts[0] == "datasets":
            parts = parts[1:]
        if len(parts) >= 2 and parts[0] not in ("c", "competitions"):
            return f"{parts[0]}/{parts[1]}"
        return None

    if parsed.scheme:
        return None
    return ref if KAGGLE_SLUG.match(ref) else None


def classify(ref: str) -> str:
    """"path" | "url" | "kaggle". The order of these checks is the whole trick.

    A local path is tested FIRST by asking the filesystem, because a relative
    path like "data/iris.csv" also matches the Kaggle slug shape. Existence is
    a fact; both other answers are inferences.
    """
    ref = (ref or "").strip()
    if not ref:
        raise DataError("empty dataset reference")
    try:
        if Path(ref).exists():
            return "path"
    except OSError:
        # Windows raises rather than returning False for a syntactically
        # impossible path. That is not an answer to "is this a file", it is a
        # no -- and it must not surface as an OSError from a classifier.
        pass
    if kaggle_slug(ref):
        return "kaggle"
    if urllib.parse.urlparse(ref).scheme in ("http", "https"):
        return "url"
    raise DataError(
        f"I do not know how to get {ref!r}. Give me a file, a https:// link, "
        "or a Kaggle dataset like owner/dataset-name."
    )


# ------------------------------------------------------------------ helpers


def _safe_name(name: str, taken: set[str], dest_dir: Path | None = None) -> str:
    """A bare, unique filename. Any directory component is discarded.

    UNIQUE AGAINST THE DIRECTORY, not just against this call. Two references
    can each produce a file called `data.csv` -- a manual naming two URLs, or a
    Kaggle dataset alongside an upload -- and each `resolve()` starts with its
    own empty `taken`. Checking only that set meant the second silently
    overwrote the first, and the run then solved every task against one file
    while the report named two.
    """
    base = Path(str(name).replace("\\", "/")).name or "dataset"
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).lstrip(".") or "dataset"
    candidate, stem, suffix = base, Path(base).stem, Path(base).suffix

    def clashes(value: str) -> bool:
        if value.lower() in taken:
            return True
        return dest_dir is not None and (dest_dir / value).exists()

    n = 2
    while clashes(candidate):
        candidate = f"{stem}_{n}{suffix}"
        n += 1
    taken.add(candidate.lower())
    return candidate


def _is_data_file(path: Path) -> bool:
    return path.suffix.lower() in DATA_SUFFIXES


def _adopt(path: Path, dest_dir: Path, origin: str, ref: str, taken: set[str]) -> Dataset:
    """Copy a resolved file into the run's data directory and describe it."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = _safe_name(path.name, taken, dest_dir)
    target = dest_dir / name
    if path.resolve() != target.resolve():
        shutil.copy2(path, target)
    return Dataset(
        name=name,
        path=target,
        origin=origin,
        ref=ref or path.name,
        bytes=target.stat().st_size,
        preview=profile(target),
    )


#: Workbooks. Accepted as data, but never handed to the solver as-is when they
#: can be converted -- see `_adopt_all` and `data/normalise.py`.
EXCEL_SUFFIXES = (".xlsx", ".xlsm", ".xls")


def _adopt_all(
    path: Path,
    dest_dir: Path,
    origin: str,
    ref: str,
    taken: set[str],
    *,
    convert_excel: bool = True,
) -> list[Dataset]:
    """`_adopt`, except a workbook arrives as the CSV(s) it contains.

    WHY THE SOLVER NEVER MEETS THE WORKBOOK. The file is copied into every
    task's workspace and re-parsed by every attempt, and the .py that gets
    handed in would carry a `pd.read_excel` that depends on an engine the
    student may not have. Converting once here pays the parse cost once and
    hands in a script that runs anywhere pandas does.

    The original is kept in the data directory as the run's record of what it
    was given; it is simply not returned as a `Dataset`, so nothing copies or
    describes it.
    """
    adopted = _adopt(path, dest_dir, origin, ref, taken)
    if not convert_excel or adopted.path.suffix.lower() not in EXCEL_SUFFIXES:
        return [adopted]

    from labsagent.data.normalise import to_csv

    converted = to_csv(adopted.path, dest_dir)
    if not converted:
        # Conversion is an optimisation. openpyxl can still read it for the
        # profile, and the solver can still open it -- just more expensively.
        return [adopted]

    workbook = adopted.path.name
    # NOT `_safe_name` HERE. `to_csv` has already written these into `dest_dir`,
    # so asking for a free name would find the file itself and hand back
    # `sales_2.csv` for something on disk as `sales.csv`. Registering the real
    # name is what keeps a LATER reference from colliding with it.
    for csv_path in converted:
        taken.add(csv_path.name.lower())

    return [
        Dataset(
            name=csv_path.name,
            path=csv_path,
            origin=origin,
            ref=ref or workbook,
            bytes=csv_path.stat().st_size,
            # SAY WHERE IT CAME FROM. This line reaches the report, the
            # notebook and the submitted .py, and a CSV that appeared from
            # nowhere is a provenance gap in all three.
            preview=f"{profile(csv_path)}\n    (converted from {workbook})",
        )
        for csv_path in converted
    ]


def _pick(paths, limit: int = MAX_FILES_PER_REF) -> list[Path]:
    """Data files, biggest first, bounded. Non-data files are dropped."""
    files = sorted(
        (p for p in paths if p.is_file() and _is_data_file(p)),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    return files[:limit]


# -------------------------------------------------------------- url guarding


def _check_public(url: str) -> None:
    """Refuse anything that is not http(s) to a publicly routable address."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise DataError(f"refusing {parsed.scheme or 'scheme-less'} URL: only http and https")

    try:
        host, port = parsed.hostname, parsed.port
    except ValueError as exc:
        # urlsplit defers parsing the port, so "http://h:99999/" raises HERE
        # rather than at urlparse. Uncaught, it leaves the acquire step as a
        # ValueError with no reference attached to it.
        raise DataError(f"malformed URL {url!r}: {exc}") from exc

    if not host:
        raise DataError(f"no host in {url!r}")

    try:
        infos = socket.getaddrinfo(host, port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise DataError(f"could not resolve {host}: {exc}") from exc

    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise DataError(
                f"refusing to fetch {host}: it resolves to {address}, which is not a "
                "public address. If that file is on this machine, attach it instead."
            )


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """Re-checks every hop. An open redirect to 127.0.0.1 is the usual bypass."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _filename_from(response, url: str) -> str:
    disposition = response.headers.get("Content-Disposition", "")
    match = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", disposition)
    if match:
        return urllib.parse.unquote(match.group(1))
    name = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).name
    return name or "download.csv"


# ---------------------------------------------------------------- providers


def from_path(
    ref: str,
    dest_dir: Path,
    *,
    max_bytes: int = MAX_DATASET_BYTES,
    convert_excel: bool = True,
) -> list[Dataset]:
    """A file already on disk -- an upload, or a --data argument."""
    source = Path(ref)
    if not source.is_file():
        raise DataError(f"{ref} is not a file")
    size = source.stat().st_size
    if size > max_bytes:
        raise DataError(
            f"{source.name} is {size / 1024 / 1024:.0f} MB, over the "
            f"{max_bytes // 1024 // 1024} MB limit for a dataset."
        )
    if not _is_data_file(source):
        raise DataError(
            f"I do not read {source.suffix or 'extension-less'} files as data. "
            f"Try one of: {', '.join(DATA_SUFFIXES)}."
        )

    taken: set[str] = set()
    if source.suffix.lower() == ".zip":
        return _from_zip(
            source,
            dest_dir,
            origin="upload",
            ref=source.name,
            taken=taken,
            convert_excel=convert_excel,
        )
    return _adopt_all(
        source, dest_dir, "upload", source.name, taken, convert_excel=convert_excel
    )


def from_url(
    ref: str,
    dest_dir: Path,
    *,
    max_bytes: int = MAX_DATASET_BYTES,
    timeout_s: int = FETCH_TIMEOUT_S,
    convert_excel: bool = True,
) -> list[Dataset]:
    """Stream an http(s) URL to disk, refusing anything non-public or oversized."""
    _check_public(ref)

    opener = urllib.request.build_opener(_GuardedRedirect())
    request = urllib.request.Request(ref, headers={"User-Agent": USER_AGENT})
    dest_dir.mkdir(parents=True, exist_ok=True)

    try:
        with opener.open(request, timeout=timeout_s) as response:
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise DataError(
                    f"that file is {int(declared) / 1024 / 1024:.0f} MB, over the "
                    f"{max_bytes // 1024 // 1024} MB limit."
                )

            name = _safe_name(_filename_from(response, ref), set(), dest_dir)
            scratch = dest_dir / name
            written = 0
            with scratch.open("wb") as handle:
                # Capped WHILE streaming: a server that lies about
                # Content-Length, or omits it, must not be able to fill the disk.
                while chunk := response.read(1 << 16):
                    written += len(chunk)
                    if written > max_bytes:
                        handle.close()
                        scratch.unlink(missing_ok=True)
                        raise DataError(
                            f"that download passed the {max_bytes // 1024 // 1024} MB "
                            "limit and was stopped."
                        )
                    handle.write(chunk)
    except urllib.error.HTTPError as exc:
        raise DataError(f"{ref} returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        raise DataError(f"could not fetch {ref}: {exc}") from exc
    except OSError as exc:
        raise DataError(f"could not save the download: {exc}") from exc

    if scratch.suffix.lower() == ".zip":
        found = _from_zip(
            scratch,
            dest_dir,
            origin="url",
            ref=ref,
            taken=set(),
            convert_excel=convert_excel,
        )
        scratch.unlink(missing_ok=True)
        return found

    if not _is_data_file(scratch):
        scratch.unlink(missing_ok=True)
        raise DataError(
            f"{ref} gave a {scratch.suffix or 'typeless'} file, which I do not read as data."
        )

    return [
        Dataset(
            name=scratch.name,
            path=scratch,
            origin="url",
            ref=ref,
            bytes=scratch.stat().st_size,
            preview=profile(scratch),
        )
    ]


def from_kaggle(
    ref: str,
    dest_dir: Path,
    *,
    username: str = "",
    key: str = "",
    max_bytes: int = MAX_DATASET_BYTES,
    convert_excel: bool = True,
) -> list[Dataset]:
    """Download a Kaggle dataset by slug, via kagglehub's own local cache.

    The import and the credential check are both guarded so the failure is a
    sentence the student can act on. "kagglehub is not installed" and "add
    KAGGLE_KEY to .env" are fixable; a ModuleNotFoundError traceback in a chat
    window is not.
    """
    slug = kaggle_slug(ref)
    if not slug:
        raise DataError(f"{ref!r} is not a Kaggle dataset reference")

    try:
        import kagglehub
    except ImportError as exc:
        raise DataError(
            "Kaggle downloads need the kagglehub package: `uv add kagglehub`. "
            "Or download the file yourself and attach it."
        ) from exc

    # kagglehub reads credentials from the environment or ~/.kaggle/kaggle.json.
    # Ours come from Settings, so they are put in place for this process only.
    if username and key:
        os.environ.setdefault("KAGGLE_USERNAME", username)
        os.environ.setdefault("KAGGLE_KEY", key)
    elif not (os.environ.get("KAGGLE_KEY") or (Path.home() / ".kaggle" / "kaggle.json").exists()):
        raise DataError(
            f"Kaggle dataset {slug} needs credentials. Put KAGGLE_USERNAME and "
            "KAGGLE_KEY in .env (Kaggle > Settings > API > Create New Token), "
            "or download the file and attach it instead."
        )

    try:
        cached = Path(kagglehub.dataset_download(slug))
    except Exception as exc:  # noqa: BLE001 -- kagglehub raises several unrelated types
        raise DataError(f"could not download Kaggle dataset {slug}: {exc}"[:300]) from exc

    candidates = _pick([cached] if cached.is_file() else list(cached.rglob("*")))
    if not candidates:
        raise DataError(f"Kaggle dataset {slug} downloaded, but held no readable data files.")

    taken: set[str] = set()
    found: list[Dataset] = []
    for path in candidates:
        if path.stat().st_size > max_bytes:
            continue
        found.extend(
            _adopt_all(
                path, dest_dir, "kaggle", slug, taken, convert_excel=convert_excel
            )
        )
    if not found:
        raise DataError(
            f"every file in Kaggle dataset {slug} is over the "
            f"{max_bytes // 1024 // 1024} MB limit."
        )
    return found


# ------------------------------------------------------------------ archives


def _from_zip(
    archive: Path,
    dest_dir: Path,
    *,
    origin: str,
    ref: str,
    taken: set[str],
    convert_excel: bool = True,
) -> list[Dataset]:
    """Extract the data files out of a zip, refusing any member that escapes.

    `ZipFile.extract` sanitises paths on its own in modern Python, but this does
    not rely on that: the destination is resolved and checked, which is the same
    discipline `confined.py` applies to the agent's own filesystem.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    staging = dest_dir / f"_{archive.stem}_unpacked"
    staging.mkdir(parents=True, exist_ok=True)
    root = staging.resolve()

    try:
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                if member.is_dir():
                    continue
                target = (staging / member.filename).resolve()
                if root != target and root not in target.parents:
                    raise DataError(
                        f"{archive.name} contains {member.filename!r}, which would be "
                        "written outside the workspace. Refusing to unpack it."
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    except zipfile.BadZipFile as exc:
        raise DataError(f"{archive.name} is not a readable zip: {exc}") from exc

    found = [
        dataset
        for path in _pick(list(staging.rglob("*")))
        for dataset in _adopt_all(
            path, dest_dir, origin, ref, taken, convert_excel=convert_excel
        )
    ]
    shutil.rmtree(staging, ignore_errors=True)
    if not found:
        raise DataError(f"{archive.name} held no readable data files.")
    return found


# ------------------------------------------------------------------ dispatch


def resolve(
    ref: str,
    dest_dir: Path,
    *,
    max_bytes: int = MAX_DATASET_BYTES,
    timeout_s: int = FETCH_TIMEOUT_S,
    kaggle_username: str = "",
    kaggle_key: str = "",
    convert_excel: bool = True,
) -> list[Dataset]:
    """One reference -> the files it names. Raises `DataError`, never anything else."""
    kind = classify(ref)
    if kind == "path":
        return from_path(
            ref, dest_dir, max_bytes=max_bytes, convert_excel=convert_excel
        )
    if kind == "kaggle":
        return from_kaggle(
            ref,
            dest_dir,
            username=kaggle_username,
            key=kaggle_key,
            max_bytes=max_bytes,
            convert_excel=convert_excel,
        )
    return from_url(
        ref,
        dest_dir,
        max_bytes=max_bytes,
        timeout_s=timeout_s,
        convert_excel=convert_excel,
    )


def acquire(
    refs,
    dest_dir: Path,
    *,
    max_bytes: int = MAX_DATASET_BYTES,
    timeout_s: int = FETCH_TIMEOUT_S,
    kaggle_username: str = "",
    kaggle_key: str = "",
    convert_excel: bool = True,
    on_progress=None,
) -> Acquisition:
    """Resolve every reference, collecting failures instead of raising them.

    ONE BAD REFERENCE MUST NOT COST THE RUN. This is the policy `emit_all`
    already applies to a format that will not render, for the same reason: by
    the time a dead link is discovered the manual has been read and the tasks
    extracted, and throwing that away because one URL 404s is a worse outcome
    than solving four of five tasks and saying which one lacked its data.

    Duplicate references are resolved once -- a manual that names the same CSV
    in three tasks should not download it three times.
    """
    result = Acquisition()
    seen: set[str] = set()

    for ref in refs:
        ref = str(ref or "").strip()
        if not ref or ref in seen:
            continue
        seen.add(ref)
        result.requested.append(ref)
        if on_progress is not None:
            on_progress(ref)
        try:
            result.datasets.extend(
                resolve(
                    ref,
                    dest_dir,
                    max_bytes=max_bytes,
                    timeout_s=timeout_s,
                    kaggle_username=kaggle_username,
                    kaggle_key=kaggle_key,
                    convert_excel=convert_excel,
                )
            )
        except DataError as exc:
            result.failures.append((ref, str(exc)))
        except Exception as exc:  # noqa: BLE001 -- a provider bug is still not a failed run
            result.failures.append((ref, f"{type(exc).__name__}: {exc}"[:200]))

    return result
