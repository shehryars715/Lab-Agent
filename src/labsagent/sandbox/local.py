"""Local subprocess sandbox.

Isolation here is weak by design -- it's a development backend. The E2B backend
(Phase 4) provides real isolation behind the identical protocol.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from labsagent.errors import SandboxError
from labsagent.models import ExecResult
from labsagent.sandbox.base import DEFAULT_TIMEOUT_S, OUTPUT_CAP_BYTES

# WHAT A PROGRAM INHERITS: AN ALLOWLIST, NEVER THE SERVER'S ENVIRONMENT.
#
# This used to be `{**os.environ}`. On the deployed server compose loads the
# whole .env into the environment, so every program the model wrote could read
# DEEPSEEK_API_KEY and LABSAGENT_PASSWORD -- and an ordinary OS-lab task ("print
# the environment variables") would have put both into a report a student
# hands in. Verified with a probe on 2026-10-02.
#
# An allowlist rather than a denylist, because a denylist has to know every
# secret's name in advance, and the next one added to .env would leak by
# default. What stays is what Python and the plotting/data libraries need to
# start, find a temp directory and a home for their caches, and print text.
_INHERITED = frozenset(
    {
        "PATH", "HOME", "LANG", "TZ", "TMPDIR", "TEMP", "TMP",
        # Windows: without these, Python's own startup and numpy's DLL
        # loading fail in ways that look nothing like a missing variable.
        "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT",
        "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
        "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS",
    }
)


def program_env() -> dict[str, str]:
    """The environment a model-written program runs with."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in _INHERITED or key.upper().startswith("LC_")
    }
    # Headless plotting. Without Agg, matplotlib tries to open a window and
    # either hangs or dies in a non-interactive subprocess.
    env["MPLBACKEND"] = "Agg"
    return env


def _drain(
    proc: subprocess.Popen, stdin: str | None, timeout: float
) -> tuple[bytes, bytes, bool]:
    """Collect a process's output until it exits, times out, or passes the cap.

    One thread per pipe: reading one pipe to the end while the other fills
    deadlocks as soon as the child blocks writing to the full one. Returns with
    `proc.returncode` still None on a timeout or a cap stop; the caller kills.
    """
    buffers = (bytearray(), bytearray())
    total = [0]
    lock = threading.Lock()
    over = threading.Event()

    def pump(pipe, buffer: bytearray) -> None:
        try:
            while chunk := pipe.read1(65536):
                with lock:
                    room = OUTPUT_CAP_BYTES - total[0]
                    if room <= 0:
                        over.set()
                        return
                    buffer.extend(chunk[:room])
                    total[0] += len(chunk)
                    if total[0] > OUTPUT_CAP_BYTES:
                        over.set()
                        return
        except (OSError, ValueError):
            return

    readers = [
        threading.Thread(target=pump, args=(proc.stdout, buffers[0]), daemon=True),
        threading.Thread(target=pump, args=(proc.stderr, buffers[1]), daemon=True),
    ]
    for reader in readers:
        reader.start()

    if proc.stdin is not None:
        def feed() -> None:
            try:
                proc.stdin.write((stdin or "").encode("utf-8"))
                proc.stdin.close()
            except (OSError, ValueError):
                pass

        threading.Thread(target=feed, daemon=True).start()

    deadline = time.monotonic() + timeout
    while not over.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            proc.wait(timeout=min(0.1, remaining))
            break
        except subprocess.TimeoutExpired:
            continue

    truncated = over.is_set()
    if proc.returncode is not None and not truncated:
        # Exited on its own: the pipes reach EOF promptly unless a grandchild
        # kept them open, which is what the bounded join is for.
        for reader in readers:
            reader.join(timeout=2)
        truncated = over.is_set()
    if truncated and proc.returncode is not None:
        # It finished, but only after flooding past the cap; report the stop.
        proc.returncode = None
    return bytes(buffers[0]), bytes(buffers[1]), truncated


def _text(raw: bytes) -> str:
    """Bytes to text the way `text=True` did: UTF-8, universal newlines.

    Windows programs end lines with CRLF; every consumer (transcripts,
    screenshots, tests) expects LF, which is what text mode used to give.
    """
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the program and everything it started, then reap it."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    for pipe in (proc.stdout, proc.stderr, proc.stdin):
        try:
            if pipe is not None:
                pipe.close()
        except OSError:
            pass


class LocalSandbox:
    """Runs commands in a scratch directory using the current interpreter."""

    def __init__(self, workdir: Path | None = None, keep: bool = False) -> None:
        self._owned = workdir is None
        self._keep = keep
        # MUST be resolved. _resolve() returns absolute paths, and callers pass
        # relative workdirs (runs/<id>/workspace/task1). A relative _workdir makes
        # every Path.relative_to() against it raise "is not in the subpath of",
        # and FilesystemBackend(root_dir=...) inherits the same breakage.
        self._workdir = (
            Path(workdir).resolve()
            if workdir
            else Path(tempfile.mkdtemp(prefix="labsagent-")).resolve()
        )
        self._workdir.mkdir(parents=True, exist_ok=True)
        self._closed = False

    @property
    def workdir(self) -> Path:
        return self._workdir

    def _resolve(self, path: str) -> Path:
        # The agent lives in a virtual filesystem rooted at "/", so it will pass
        # "/task1.py". Joining that raw discards self._workdir entirely (same
        # trap as os.path.join("/a", "/b") == "/b"). Strip the leading separator
        # unless the path is drive-qualified, i.e. a genuine host path.
        candidate = Path(path)
        if candidate.drive:
            target = candidate.resolve()
        else:
            target = (self._workdir / str(path).lstrip("/\\")).resolve()
        # Keep writes inside the workspace even if the agent proposes "../".
        # is_relative_to, not a string prefix: "task1" is a prefix of "task10",
        # so the old check let task1 reach task10/.
        if not target.is_relative_to(self._workdir):
            raise SandboxError(f"path escapes workspace: {path}")
        return target

    def write_file(self, path: str, content: str) -> None:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def read_file(self, path: str) -> str:
        return self._resolve(path).read_text(encoding="utf-8")

    def list_files(self, path: str = ".") -> list[str]:
        root = self._resolve(path)
        if not root.exists():
            return []
        return sorted(
            str(p.relative_to(self._workdir)).replace("\\", "/")
            for p in root.rglob("*")
            if p.is_file()
        )

    def run(
        self,
        cmd: list[str],
        stdin: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_S,
    ) -> ExecResult:
        if self._closed:
            raise SandboxError("sandbox is closed")

        resolved = [sys.executable if c == "python" else c for c in cmd]
        started = time.monotonic()
        # STREAMED, NOT `capture_output=True`. capture_output buffers without
        # limit, so a print loop grows the SERVER's memory until the kernel
        # kills it. Reading in chunks lets us count and stop at the cap.
        #
        # Its own process group/session, so a timeout kills the program's
        # children too -- killing only the direct child left grandchildren
        # spinning with nothing to reap them (audit finding 6).
        group = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        try:
            proc = subprocess.Popen(
                resolved,
                cwd=self._workdir,
                env=program_env(),
                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **group,
            )
        except OSError as exc:
            raise SandboxError(f"could not launch {resolved[0]!r}: {exc}") from exc

        out, err, truncated = _drain(proc, stdin, timeout)
        timed_out = proc.returncode is None and not truncated
        if timed_out or truncated:
            _kill_tree(proc)
        stderr = _text(err)
        if timed_out:
            stderr += f"\n[timed out after {timeout}s]"
        return ExecResult(
            exit_code=-1 if (timed_out or truncated) else proc.returncode,
            # Decoded on every path. On Linux `TimeoutExpired.stdout` is bytes
            # even with text=True, which used to crash the run tool on exactly
            # the timeouts it exists to report (audit finding 5).
            stdout=_text(out),
            stderr=stderr,
            duration_s=time.monotonic() - started,
            timed_out=timed_out,
            truncated=truncated,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owned and not self._keep:
            shutil.rmtree(self._workdir, ignore_errors=True)

    def __enter__(self) -> "LocalSandbox":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
