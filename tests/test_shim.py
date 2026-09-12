"""The load-bearing test for the whole screenshot story.

If this passes, transcripts are correct by construction rather than by
reconstruction -- which is the difference between an honest screenshot and a
fabricated one.
"""

from __future__ import annotations

from labsagent.runner import run_solution

TWO_INPUTS = (
    'n = int(input("Enter n: "))\n'
    'm = int(input("Enter m: "))\n'
    'print(f"Sum = {n + m}")\n'
)


def test_inputs_are_echoed_beside_their_prompts(sandbox):
    sandbox.write_file("t.py", TWO_INPUTS)
    out = run_solution(sandbox, "t.py", ["5", "3"])

    assert out.ok
    assert out.result.stdout == "Enter n: 5\nEnter m: 3\nSum = 8\n"


def test_raw_piping_would_lose_the_values(sandbox):
    """Documents the problem the shim exists to solve."""
    sandbox.write_file("t.py", TWO_INPUTS)
    raw = sandbox.run(["python", "t.py"], stdin="5\n3\n")

    assert raw.stdout == "Enter n: Enter m: Sum = 8\n"
    assert "Enter n: 5" not in raw.stdout


def test_transcript_display_includes_command_and_prompt(sandbox):
    sandbox.write_file("t.py", TWO_INPUTS)
    lines = run_solution(sandbox, "t.py", ["5", "3"]).transcript.display_lines()

    assert lines[0].endswith("python t.py")
    assert lines[1:4] == ["Enter n: 5", "Enter m: 3", "Sum = 8"]


def test_program_asking_for_more_input_than_supplied_fails_clearly(sandbox):
    sandbox.write_file("t.py", TWO_INPUTS)
    out = run_solution(sandbox, "t.py", ["5"])

    assert not out.ok
    assert any("more input" in w for w in out.warnings)
    assert "__LABSAGENT" not in out.result.stderr  # sentinels never reach a screenshot


def test_program_with_no_input_is_unaffected(sandbox):
    sandbox.write_file("t.py", 'print("hello")\n')
    out = run_solution(sandbox, "t.py", [])

    assert out.ok
    assert out.result.stdout == "hello\n"


def test_direct_stdin_read_is_flagged(sandbox):
    sandbox.write_file("t.py", 'import sys\nv = sys.stdin.readline()\nprint("got", v.strip())\n')
    out = run_solution(sandbox, "t.py", ["7"])

    assert any("sys.stdin" in w for w in out.warnings)


def test_dunder_main_block_runs(sandbox):
    sandbox.write_file("t.py", 'if __name__ == "__main__":\n    print("ran")\n')
    out = run_solution(sandbox, "t.py", [])

    assert out.result.stdout == "ran\n"
