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
