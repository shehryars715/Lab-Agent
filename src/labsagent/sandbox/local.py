"""Local subprocess sandbox.

Isolation here is weak by design -- it's a development backend. The E2B backend
(Phase 4) provides real isolation behind the identical protocol.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from labsagent.errors import SandboxError
from labsagent.models import ExecResult
from labsagent.sandbox.base import DEFAULT_TIMEOUT_S


class LocalSandbox:
    """Runs commands in a scratch directory using the current interpreter."""

    def __init__(self, workdir: Path | None = None, keep: bool = False) -> None:
        self._owned = workdir is None
        self._keep = keep
        self._workdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="labsagent-"))
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
        if not str(target).startswith(str(self._workdir.resolve())):
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
        try:
            proc = subprocess.run(
                resolved,
                cwd=self._workdir,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            return ExecResult(
                exit_code=-1,
                stdout=exc.stdout or "",
                stderr=(exc.stderr or "") + f"\n[timed out after {timeout}s]",
                duration_s=time.monotonic() - started,
                timed_out=True,
            )
        except OSError as exc:
            raise SandboxError(f"could not launch {resolved[0]!r}: {exc}") from exc

        return ExecResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_s=time.monotonic() - started,
            timed_out=False,
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
