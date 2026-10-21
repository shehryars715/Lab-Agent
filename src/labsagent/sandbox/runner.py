"""The runner-backed sandbox: files on the shared disk, programs in a container.

WHY A SUBCLASS OF LocalSandbox. The app and the runner see the same run
folder at the same absolute path, so writing, reading and listing files stays
exactly what LocalSandbox already does correctly (including the workspace
check). Only `run()` changes: instead of a subprocess on this machine, it asks
the runner sidecar to run the command in a throwaway container that can see
this task's folder and nothing else -- no network, no other runs, no secrets.

WHY THE APP DOES NOT START CONTAINERS ITSELF. Starting a container needs the
Docker socket, and the socket is root on the host. The app parses untrusted
.docx and .pdf files; the runner parses one small JSON body. So only the
runner holds the socket, and it accepts one request shape with fixed limits.

A transport failure raises SandboxError, which the orchestrator already treats
as infrastructure: it does not use up one of the task's attempts.
"""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import httpx

from labsagent.errors import SandboxError
from labsagent.models import ExecResult
from labsagent.sandbox.base import DEFAULT_TIMEOUT_S
from labsagent.sandbox.local import LocalSandbox

# On top of the program's own timeout: container start-up and clean-up.
_OVERHEAD_S = 20


def scrub(workdir: Path) -> list[str]:
    """Delete everything in the task folder that is not a plain file or folder.

    THE CONTAINER CANNOT SEE OUT, BUT IT CAN LEAVE A NOTE FOR US. A program may
    create `chart.png -> /srv/labsagent/runs/<someone else>/report/lab.docx`.
    Inside the container that link points at nothing. Back on the host, the app
    collects new .png files as figures and copies handed-forward files -- and
    reading that link would put another student's report into this one. A FIFO
    is the other trap: reading it blocks the app forever. So after every run,
    anything that is not a regular file or directory is removed before the app
    looks at the folder. Returns what was removed, for tests and logs.
    """
    removed: list[str] = []
    for root, dirs, files in os.walk(workdir, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(root) / name
            try:
                mode = path.lstat().st_mode
            except OSError:
                continue
            if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                try:
                    path.unlink()
                    removed.append(str(path.relative_to(workdir)))
                except OSError:
                    pass
        # Never descend into a directory that was itself a link.
        dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
    return removed


class RunnerSandbox(LocalSandbox):
    def __init__(self, workdir, *, url: str, token: str, keep: bool = True) -> None:
        super().__init__(workdir=workdir, keep=keep)
        self._url = url.rstrip("/")
        self._token = token

    def run(
        self,
        cmd: list[str],
        stdin: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_S,
    ) -> ExecResult:
        if stdin:
            # Never needed: the echo shim reads its inputs from a file. Refused
            # rather than silently dropped, so a future caller finds out here.
            raise SandboxError("the runner does not take stdin; use the inputs file")
        # The agent's virtual filesystem is rooted at "/", so it names files
        # "/task1.py". Inside the container that would be the container's own
        # root, not the task folder; the runner refuses absolute paths anyway.
        argv = [cmd[0], *(a.lstrip("/") for a in cmd[1:])]
        started = time.monotonic()
        try:
            response = httpx.post(
                f"{self._url}/run",
                json={"task_dir": str(self.workdir), "argv": argv, "timeout_s": timeout},
                headers={"X-Runner-Token": self._token},
                timeout=timeout + _OVERHEAD_S,
            )
        except httpx.HTTPError as exc:
            raise SandboxError(f"runner unreachable: {exc}") from exc
        finally:
            scrub(self.workdir)
        if response.status_code != 200:
            raise SandboxError(f"runner refused ({response.status_code}): {response.text[:300]}")
        body = response.json()
        return ExecResult(
            exit_code=int(body["exit_code"]),
            stdout=body.get("stdout", ""),
            stderr=body.get("stderr", ""),
            duration_s=float(body.get("duration_s", time.monotonic() - started)),
            timed_out=bool(body.get("timed_out")),
            truncated=bool(body.get("truncated")),
        )
