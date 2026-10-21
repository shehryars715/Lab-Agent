"""Where model-written programs run. `make_sandbox` picks the backend."""

from __future__ import annotations

from pathlib import Path

from labsagent.config import Settings
from labsagent.errors import SandboxError
from labsagent.sandbox.local import LocalSandbox


def make_sandbox(settings: Settings, workdir: Path):
    """The sandbox for one task attempt, per `settings.sandbox_backend`."""
    backend = (settings.sandbox_backend or "local").strip().lower()
    if backend == "local":
        return LocalSandbox(workdir=workdir, keep=True)
    if backend == "runner":
        from labsagent.sandbox.runner import RunnerSandbox

        if not settings.runner_token:
            raise SandboxError("SANDBOX_BACKEND=runner needs RUNNER_TOKEN")
        return RunnerSandbox(workdir, url=settings.runner_url, token=settings.runner_token)
    raise SandboxError(f"unknown SANDBOX_BACKEND {backend!r}; use 'local' or 'runner'")
