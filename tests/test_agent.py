"""Agent wiring, and the filesystem boundary that protects our API key."""

from __future__ import annotations

from pathlib import Path

from deepagents.backends import FilesystemBackend

from labsagent.agent.confined import ConfinedBackend
from labsagent.config import Settings


def _confined(sandbox):
    return ConfinedBackend(
        FilesystemBackend(root_dir=sandbox.workdir, virtual_mode=False), sandbox.workdir
    )


def test_raw_backend_honours_absolute_paths(sandbox, tmp_path):
    """Documents WHY ConfinedBackend exists. If this ever starts failing,
    upstream fixed it and the wrapper may be droppable."""
    secret = tmp_path / "secret.txt"
    secret.write_text("sk-TOPSECRET", encoding="utf-8")

    raw = FilesystemBackend(root_dir=sandbox.workdir, virtual_mode=False)
    assert "sk-TOPSECRET" in str(raw.read(str(secret))), "upstream behaviour changed"


def test_confined_backend_blocks_absolute_escape(sandbox, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("sk-TOPSECRET", encoding="utf-8")

    assert "sk-TOPSECRET" not in str(_confined(sandbox).read(str(secret)))


def test_confined_backend_blocks_relative_escape(sandbox):
    assert "denied" in str(_confined(sandbox).read("../../.env"))


def test_confined_backend_allows_workspace_paths(sandbox):
    sandbox.write_file("task1.py", "print('hi')")
    result = str(_confined(sandbox).read("task1.py"))

    assert "denied" not in result
    assert "hi" in result


def test_confined_backend_blocks_writes_outside_root(sandbox, tmp_path):
    target = tmp_path / "evil.txt"
    _confined(sandbox).write(str(target), "pwned")

    assert not target.exists()


def test_project_env_is_never_reachable(sandbox):
    """The concrete threat: our own .env holds a live key."""
    env = Path(__file__).resolve().parents[1] / ".env"
    if not env.exists():
        return
    assert "sk-" not in str(_confined(sandbox).read(str(env)))


def test_settings_read_the_env_file():
    s = Settings()
    assert s.model_name == "deepseek-flash"
    assert s.temperature == 0.0


def test_virtual_root_paths_map_into_the_workspace(sandbox):
    """deepagents shows the model a filesystem rooted at "/". "/task1.py" is a
    VIRTUAL path, not a host path -- denying it makes the agent hunt for its own
    workspace and burn turns."""
    be = _confined(sandbox)

    assert be.to_host("/task1.py") == sandbox.workdir.resolve() / "task1.py"
    assert be.to_host("task1.py") == sandbox.workdir.resolve() / "task1.py"
    assert be.to_host("/") == sandbox.workdir.resolve()


def test_escapes_still_denied_through_the_virtual_root(sandbox):
    assert _confined(sandbox).to_host("/../../.env") is None


def test_agent_can_write_then_read_via_virtual_path(sandbox):
    be = _confined(sandbox)
    be.write("/task1.py", "print('hi')")

    assert (sandbox.workdir / "task1.py").exists()
    assert "hi" in str(be.read("/task1.py"))
