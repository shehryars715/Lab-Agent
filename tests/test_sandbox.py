"""Conformance suite for the Sandbox protocol.

Phase 4 runs this same suite against the E2B backend unchanged. If it does not
pass identically, the protocol is wrong -- fix the protocol, not the test.
"""

from __future__ import annotations

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
