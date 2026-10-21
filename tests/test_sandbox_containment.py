"""LocalSandbox containment: the output cap, timeouts, and the workspace check.

These run real subprocesses (no model, no network), because the bugs they
guard against only exist in real process behaviour: an unbounded print loop,
a timeout that returns bytes, a sibling directory that shares a name prefix.
"""

from __future__ import annotations

import pytest

from labsagent.errors import SandboxError
from labsagent.sandbox.base import OUTPUT_CAP_BYTES
from labsagent.sandbox.local import LocalSandbox


def _run(tmp_path, source: str, timeout: int = 20, stdin: str | None = None):
    box = LocalSandbox(workdir=tmp_path / "task1", keep=True)
    box.write_file("prog.py", source)
    return box.run(["python", "prog.py"], stdin=stdin, timeout=timeout)


def test_a_print_loop_is_stopped_at_the_cap(tmp_path):
    result = _run(tmp_path, "while True:\n    print('x' * 1000)\n")
    assert result.truncated
    assert not result.timed_out
    assert result.exit_code == -1
    assert len(result.stdout.encode()) <= OUTPUT_CAP_BYTES


def test_a_timeout_after_output_returns_text(tmp_path):
    # 8 s, not 2: a cold Python start under a busy test run can take more than
    # 2 s to reach its first print, which made this fail now and then.
    result = _run(tmp_path, "import time\nprint('started', flush=True)\ntime.sleep(60)\n", timeout=8)
    assert result.timed_out
    assert "started" in result.stdout
    assert isinstance(result.stdout, str)
    assert "timed out" in result.stderr


def test_a_normal_run_is_unchanged(tmp_path):
    result = _run(tmp_path, "a = input()\nprint('got', a)\n", stdin="7\n")
    assert result.exit_code == 0
    assert result.stdout.strip() == "got 7"
    assert not result.truncated and not result.timed_out


def test_stderr_and_exit_code_survive(tmp_path):
    result = _run(tmp_path, "import sys\nprint('bad', file=sys.stderr)\nsys.exit(3)\n")
    assert result.exit_code == 3
    assert "bad" in result.stderr


def test_a_sibling_with_a_shared_prefix_is_outside(tmp_path):
    (tmp_path / "task10").mkdir()
    box = LocalSandbox(workdir=tmp_path / "task1", keep=True)
    with pytest.raises(SandboxError):
        box.write_file("../task10/x.py", "print(1)")
