"""Conformance suite for the Sandbox protocol.

Phase 4 runs this same suite against the E2B backend unchanged. If it does not
pass identically, the protocol is wrong -- fix the protocol, not the test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from labsagent.errors import SandboxError
from labsagent.sandbox.base import Sandbox


def test_satisfies_protocol(sandbox):
    assert isinstance(sandbox, Sandbox)


def test_write_then_read_roundtrip(sandbox):
    sandbox.write_file("a/b.txt", "hello")
    assert sandbox.read_file("a/b.txt") == "hello"


def test_list_files_is_recursive_and_sorted(sandbox):
    sandbox.write_file("z.txt", "1")
    sandbox.write_file("a/y.txt", "2")
    assert sandbox.list_files() == ["a/y.txt", "z.txt"]


def test_exit_code_is_reported(sandbox):
    sandbox.write_file("f.py", "raise SystemExit(3)\n")
    assert sandbox.run(["python", "f.py"]).exit_code == 3


def test_timeout_is_flagged_not_raised(sandbox):
    sandbox.write_file("slow.py", "import time; time.sleep(5)\n")
    result = sandbox.run(["python", "slow.py"], timeout=1)

    assert result.timed_out
    assert not result.ok


def test_path_escape_is_refused(sandbox):
    with pytest.raises(SandboxError):
        sandbox.write_file("../escaped.txt", "nope")


def test_ok_requires_output_not_just_a_clean_exit(sandbox):
    """Run-to-green means exit 0 AND output. A silent program is not a pass."""
    sandbox.write_file("silent.py", "pass\n")
    assert not sandbox.run(["python", "silent.py"]).ok


def test_virtual_root_paths_resolve_into_the_workspace(sandbox):
    """The agent passes "/task1.py" from its virtual filesystem. The sandbox is
    its second door into the workspace and needs the same mapping."""
    sandbox.write_file("/task1.py", "print('hi')")

    assert (sandbox.workdir / "task1.py").exists()
    assert sandbox.read_file("/task1.py") == "print('hi')"


def test_virtual_root_escape_still_refused(sandbox):
    with pytest.raises(SandboxError):
        sandbox.write_file("/../escaped.txt", "nope")


def test_relative_workdir_is_resolved(tmp_path, monkeypatch):
    """A relative workdir made every relative_to() raise, breaking list_files and
    the agent's filesystem backend. Regression: 0/3 tasks failed before any model
    call because of this."""
    from labsagent.sandbox.local import LocalSandbox

    monkeypatch.chdir(tmp_path)
    (tmp_path / "ws").mkdir()

    with LocalSandbox(workdir=Path("ws"), keep=True) as sb:
        assert sb.workdir.is_absolute()
        sb.write_file("a.py", "print(1)")
        assert sb.list_files() == ["a.py"]


def test_a_program_cannot_read_the_servers_secrets(monkeypatch):
    """Compose loads .env into the server's environment, and programs used to
    inherit all of it. An OS-lab "print the environment" task would have put
    the API key and the sign-in password into the student's report."""
    from labsagent.sandbox.local import LocalSandbox

    for name in ("DEEPSEEK_API_KEY", "LABSAGENT_PASSWORD", "KAGGLE_KEY", "E2B_API_KEY"):
        monkeypatch.setenv(name, "secret-value-from-the-server")

    with LocalSandbox() as sb:
        sb.write_file(
            "/env.py",
            "import os\nfor k, v in sorted(os.environ.items()):\n    print(k, v)\n",
        )
        result = sb.run(["python", "env.py"])

    assert result.exit_code == 0, result.stderr
    assert "secret-value-from-the-server" not in result.stdout
    assert "MPLBACKEND Agg" in result.stdout, "headless plotting is still set"


def test_the_data_libraries_still_start_in_the_clean_environment():
    """The allowlist must keep what numpy, pandas and matplotlib need -- on
    Windows a missing SYSTEMROOT breaks DLL loading in ways that look nothing
    like an environment problem."""
    from labsagent.sandbox.local import LocalSandbox

    with LocalSandbox() as sb:
        sb.write_file(
            "/plot.py",
            "import numpy, pandas, matplotlib.pyplot as plt\n"
            "plt.plot([1, 2, 3]); plt.savefig('p.png')\n"
            "print('ok', pandas.DataFrame({'a': [1]}).shape)\n",
        )
        result = sb.run(["python", "plot.py"], timeout=120)

    assert result.ok, result.stderr
    assert "ok (1, 1)" in result.stdout
