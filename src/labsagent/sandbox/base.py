"""The Sandbox protocol -- one of the two load-bearing interfaces.

Get this right and Phase 4 (E2B) is a swap, not a rewrite. E2B's run_code()
defaults to a 30s timeout per call, so `timeout` is explicit here from day one
and both backends agree on it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from labsagent.models import ExecResult

DEFAULT_TIMEOUT_S = 30


@runtime_checkable
class Sandbox(Protocol):
    """An isolated place to put files and run commands."""

    def write_file(self, path: str, content: str) -> None: ...

    def read_file(self, path: str) -> str: ...

    def list_files(self, path: str = ".") -> list[str]: ...

    def run(
        self,
        cmd: list[str],
        stdin: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_S,
    ) -> ExecResult: ...

    def close(self) -> None: ...

    @property
    def workdir(self) -> Path: ...
