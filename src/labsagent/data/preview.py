"""What a data file contains, in ~150 tokens.

WHY THIS EXISTS. The solver cannot look. `lean_tools` ships `write_file`,
`read_file`, `run_solution` and `record_task_result` and nothing else (see
`agent/build.py`), so there is no `ls` to discover a file with and no `glob` to
find one. And `ConfinedBackend.read` truncates anything past 64 KB, so a real
dataset read through the tool arrives incomplete AND expensive.

So the file is described rather than handed over. The failure this prevents is
specific and was the most likely one: a solver that guesses `df["Species"]`
when the column is `species` writes code that looks right, runs, and dies on a
KeyError -- after the task has been solved, explained and paid for.

EVERYTHING HERE IS BOUNDED. A profile of a 200 MB file must not read 200 MB, so
columns and dtypes come from a capped sample and the row count comes from
counting newlines rather than from loading rows. Where that makes a number
approximate, it is printed as approximate -- a profile that quietly lies about
its own precision is worse than one that admits it.
"""

from __future__ import annotations

import csv
from pathlib import Path

#: Rows shown under "first rows". Three is enough to reveal a date format, a
#: sentinel like -999, or a categorical spelling, and short enough to stay cheap.
SAMPLE_ROWS = 3

#: Rows read to infer dtypes. Past this the inference does not improve; it just
#: costs time on a big file.
INFER_ROWS = 1000

#: Below this, the row count is exact because reading the file is cheap. Above
#: it, newlines are counted instead and the figure is marked approximate.
EXACT_SHAPE_BYTES = 5 * 1024 * 1024

#: A hundred columns of names would swamp the task text it is attached to.
MAX_COLUMNS = 40

#: Fallback excerpt for anything that is not tabular at all.
EXCERPT_CHARS = 400

_SEPARATORS = {".tsv": "\t", ".tab": "\t"}


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _count_rows(path: Path) -> int:
    """Data lines, by counting newlines in binary. Header excluded."""
    total = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            total += chunk.count(b"\n")
    return max(0, total - 1)


#: First bytes of formats that are containers, not text. A .xlsx is a zip, and
#: `errors="replace"` will happily render one as several hundred replacement
#: characters -- which is exactly what went into a task prompt when the Excel
#: engine was missing, in place of the column names the solver needed.
_BINARY_MAGIC = (
    b"PK\x03\x04",          # zip, and therefore xlsx / xlsm
    b"\xd0\xcf\x11\xe0",    # OLE2, and therefore legacy .xls
    b"\x89PNG",
    b"\x1f\x8b",            # gzip
    b"%PDF",
)


def _is_binary(path: Path) -> bool:
    """A container or an embedded NUL. Never raises; unreadable means "no"."""
    try:
        with path.open("rb") as handle:
            head = handle.read(4096)
    except OSError:
        return False
    return head.startswith(_BINARY_MAGIC) or b"\x00" in head


def _excerpt(path: Path) -> str:
    """Head of the file, for anything pandas cannot make a table of.

    Binary is refused outright. "Here is a file I could not read" is useful to
    the solver; several hundred bytes of a zip container rendered as text is
    not -- it is noise that costs tokens on every turn of every task, and it
    invites the model to go and parse the container by hand. One run did.
    """
    if _is_binary(path):
        suffix = path.suffix.lower() or "no extension"
        return (
            f"    binary file ({suffix}); not previewed. If a task needs data "
            "from it, say so and record the task as failed."
        )
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:EXCERPT_CHARS]
    except OSError as exc:
        return f"    (could not be read: {exc})"
    body = "\n".join(f"      {line}" for line in head.splitlines()[:6])
    return f"    not tabular; first bytes:\n{body}"


def _read_sample(path: Path, suffix: str):
    """A capped DataFrame, or None if this file is not tabular."""
    try:
        import pandas as pd
    except ImportError:  # pragma: no cover -- declared in pyproject
        return None

    try:
        if suffix in (".xlsx", ".xls", ".xlsm"):
            return pd.read_excel(path, nrows=INFER_ROWS)
        if suffix in (".json", ".jsonl", ".ndjson"):
            return pd.read_json(path, lines=suffix in (".jsonl", ".ndjson"))
        if suffix == ".parquet":
            return pd.read_parquet(path)
        sep = _SEPARATORS.get(suffix)
        # sep=None asks pandas to sniff the delimiter, which needs the python
        # engine. Worth it: a semicolon-separated export is common in the wild
        # and reads as one fat column otherwise.
        return pd.read_csv(
            path,
            nrows=INFER_ROWS,
            sep=sep,
            engine="python" if sep is None else "c",
        )
    except Exception:  # noqa: BLE001 -- any parse failure means "not tabular"
        return None


def _sample_rows(frame, columns: list[str]) -> list[str]:
    lines = []
    for _, row in frame.head(SAMPLE_ROWS).iterrows():
        cells = [str(row[c]) for c in columns]
        lines.append(", ".join(cell[:24] for cell in cells))
    return lines


def profile(path: Path) -> str:
    """One indented block describing this file, ready to paste into a prompt.

    Never raises. A file that cannot be parsed still gets a name, a size and an
    excerpt, because "here is a file I could not read" is useful to the solver
    and an exception here would take down a run over a decoration.
    """
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        return f"  {path.name} -- unreadable ({exc})"

    suffix = path.suffix.lower()
    frame = _read_sample(path, suffix)

    if frame is None or not len(getattr(frame, "columns", [])):
        return f"  {path.name} -- {_human(size)}\n{_excerpt(path)}"

    columns = list(frame.columns)[:MAX_COLUMNS]
    hidden = len(frame.columns) - len(columns)

    if size <= EXACT_SHAPE_BYTES and len(frame) < INFER_ROWS:
        # The capped read already saw the whole file, so this is exact.
        shape = f"{len(frame):,} rows x {len(frame.columns)} columns"
    elif suffix in (".csv", ".tsv", ".tab", ".txt", ".data", ".jsonl", ".ndjson"):
        shape = f"~{_count_rows(path):,} rows x {len(frame.columns)} columns"
    else:
        shape = f"{len(frame.columns)} columns"

    names = ", ".join(f"{c} ({frame[c].dtype})" for c in columns)
    if hidden:
        names += f", ... and {hidden} more"

    lines = [
        f"  {path.name} -- {shape}, {_human(size)}",
        f"    columns: {names}",
    ]
    rows = _sample_rows(frame, columns)
    if rows:
        lines.append(f"    first {len(rows)} row(s): " + " | ".join(rows))
    if len(frame) >= INFER_ROWS:
        lines.append(f"    (types inferred from the first {INFER_ROWS:,} rows)")
    return "\n".join(lines)


def sniff_delimiter(path: Path) -> str | None:
    """Best-guess separator, for messages. None when it cannot tell."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return csv.Sniffer().sniff(handle.read(4096)).delimiter
    except (OSError, csv.Error):
        return None
