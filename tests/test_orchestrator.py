"""Orchestration: reference detection and prompt assembly, tested offline."""

from __future__ import annotations

from labsagent.models import Task, TaskOutcome
from labsagent.orchestrator import build_task_prompt, referenced_task_ids


def _task(tid: str, statement: str, **kw) -> Task:
    return Task(id=tid, title="T", statement=statement, **kw)


def test_no_reference_in_a_standalone_task():
    assert referenced_task_ids(_task("task1", "Read two integers and print the sum.")) == set()


def test_reference_to_another_task_is_detected():
    t = _task("task3", "Extend your Task 2 program to also print the average.")
    assert referenced_task_ids(t) == {"task2"}


def test_self_reference_is_ignored():
    """'In Task 3, write...' must not make task3 depend on itself."""
    assert referenced_task_ids(_task("task3", "In Task 3 you will write a parser.")) == set()


def test_multiple_references():
    t = _task("task4", "Combine your Task 1 and task 2 solutions.")
    assert referenced_task_ids(t) == {"task1", "task2"}


def test_prompt_includes_sample_inputs_and_filename():
    t = _task("task1", "Read two integers.", sample_inputs=["5", "3"])
    prompt = build_task_prompt(t, {})

    assert "['5', '3']" in prompt
    assert "task1.py" in prompt


def test_prompt_injects_referenced_task_code():
    prior = TaskOutcome(
        task=_task("task2", "Print even numbers."),
        status="passed",
        code_text="print('evens')",
    )
    t = _task("task3", "Extend your Task 2 program.")
    prompt = build_task_prompt(t, {"task2": prior})

    assert "Print even numbers." in prompt
    assert "print('evens')" in prompt


def test_prompt_skips_a_reference_that_never_succeeded():
    """task3 references task2, but task2 failed and has no code."""
    failed = TaskOutcome(task=_task("task2", "Print evens."), status="failed", code_text="")
    prompt = build_task_prompt(_task("task3", "Extend your Task 2 program."), {"task2": failed})

    assert "already solved" not in prompt


def test_prompt_without_references_stays_minimal():
    prompt = build_task_prompt(_task("task1", "Read two integers."), {})

    assert "already solved" not in prompt
    assert len(prompt) < 300


# --- choosing which run was the solution ------------------------------------


from labsagent.agent.tools import RunRecord  # noqa: E402
from labsagent.orchestrator import choose_run, normalize_entry  # noqa: E402


def _run(entry: str, ok: bool = True, transcript: object = "t") -> RunRecord:
    return RunRecord(entry_file=entry, ok=ok, transcript=transcript, stdout="", stderr="")


def test_a_virtual_root_path_normalises_to_a_bare_name():
    for entry in ("task3.py", "/task3.py", "./workspace/Task3.PY", r"workspace\task3.py"):
        assert normalize_entry(entry) == "task3.py"


def test_the_task_file_beats_a_later_chore():
    runs = [_run("task3.py"), _run("cleanup.py")]
    assert choose_run(runs, "task3").entry_file == "task3.py"


def test_a_failed_run_of_the_task_file_is_not_eligible():
    runs = [_run("task3.py", ok=False), _run("cleanup.py")]
    assert choose_run(runs, "task3") is None


def test_the_last_run_of_the_task_file_wins():
    first, second = _run("task3.py", transcript="old"), _run("task3.py", transcript="new")
    assert choose_run([first, second], "task3").transcript == "new"


def test_another_tasks_file_is_never_harvested():
    runs = [_run("task1.py"), _run("task2.py")]
    assert choose_run(runs, "task3", known_ids=["task1", "task2"]) is None


def test_a_declared_file_that_never_ran_is_ignored():
    runs = [_run("cleanup.py")]
    assert choose_run(runs, "task3", declared="task3.py") is None


def test_a_declared_file_breaks_the_tie_when_the_task_file_never_ran():
    runs = [_run("solution.py")]
    assert choose_run(runs, "task3", declared="solution.py").entry_file == "solution.py"


def test_a_near_miss_on_the_task_name_is_accepted_last():
    runs = [_run("task3_v2.py")]
    assert choose_run(runs, "task3").entry_file == "task3_v2.py"


def test_nothing_ran_is_no_candidate():
    assert choose_run([], "task3") is None


def test_the_prompt_says_when_no_data_resolved():
    """The block has to reach the solver, not just exist."""
    from labsagent.orchestrator import build_task_prompt

    task = Task(id="task1", title="T", statement="Analyse the sales data.")
    prompt = build_task_prompt(task, {}, (), [("sales.csv", "404")])
    assert "No data was resolved" in prompt
    assert "sales.csv -- 404" in prompt


def test_the_prompt_is_unchanged_when_no_data_was_wanted():
    from labsagent.orchestrator import build_task_prompt

    task = Task(id="task1", title="T", statement="Print hello.")
    assert build_task_prompt(task, {}, (), []) == build_task_prompt(task, {})
