"""The web pipeline's seams. Previously untested in full."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from labsagent.intent import Intent
from labsagent.models import LabSpec, Task
from web.server.pipeline import (
    IDENTITY_KEYS,
    RESERVED_KEYS,
    _not_a_lab,
    answered_notes,
    apply_instructions,
)


@dataclass
class _Reading:
    what_this_is: str
    intent: Intent


def _spec() -> LabSpec:
    return LabSpec(
        lab_number="03",
        title="Lab 03",
        tasks=[Task(id="task1", title="T", statement="Print the sum.")],
    )


# --- the pause actually reaching the run -----------------------------------


def test_the_agents_own_answers_become_notes():
    """THE BUG THIS FIXES: job.ask collected these, profile_from kept four
    identity keys, and every other reply was dropped on the floor."""
    questions = [
        {"key": "name", "label": "Your name"},
        {"key": "dataset_source", "label": "Which dataset should task 1 use?"},
    ]
    notes = answered_notes(questions, {"name": "A Student", "dataset_source": "iris.csv"})

    assert "iris.csv" in notes
    assert "Which dataset should task 1 use?" in notes, "the question gives the answer meaning"
    assert "A Student" not in notes, "identity belongs on the cover, not in the prompt"


def test_blank_answers_are_not_passed_on():
    assert answered_notes([{"key": "q", "label": "Q"}], {"q": "   "}) == ""
    assert answered_notes([], {}) == ""


def test_the_artifacts_answer_is_structural_not_prose():
    """It selects emitters; pasting it into the solver prompt would be noise."""
    assert "artifacts" in RESERVED_KEYS and "artifacts" not in IDENTITY_KEYS
    assert answered_notes([{"key": "artifacts", "label": "Files"}], {"artifacts": "py"}) == ""


def test_notes_steer_the_solver_without_entering_the_deliverable():
    """REGRESSION. Steering used to be concatenated onto `statement`, and every
    exporter prints `statement` -- so a submitted .py opened with "As you work:
    before your first tool call, say in ONE short sentence...". Invisible while
    the only deliverable annotated the manual in place; obvious the moment .py,
    .md and .ipynb became first-class."""
    spec = apply_instructions(_spec(), "seed is 42")
    task = spec.tasks[0]

    assert "seed is 42" in task.instruction, "the solver must still hear it"
    assert "seed is 42" not in task.statement, "the student must not hand it in"
    assert task.statement == "Print the sum.", "the statement is untouched"
    assert "before your first tool call" not in task.statement


def test_only_code_shaping_notes_reach_the_solver():
    """REGRESSION. The raw request used to go into every task statement, so
    asking for "the word report, the notebook, a markdown copy and a zip" told
    the SOLVER to make them -- and it did, writing a 5,996-byte program that
    generated a .docx and a .zip in answer to "read two integers and print
    their sum". Formats travel on the Intent; only notes travel here."""
    spec = apply_instructions(_spec(), "use pandas")

    assert "use pandas" in spec.tasks[0].instruction
    assert "zip" not in spec.tasks[0].instruction.lower()


def test_applying_twice_is_now_harmless():
    """The double-apply bug is structurally impossible now: `instruction` is
    ASSIGNED, where `statement` used to be concatenated onto. The pipeline
    still calls this exactly once -- but calling it twice can no longer
    duplicate anything."""
    once = apply_instructions(_spec(), "use pandas")
    twice = apply_instructions(once, "use pandas")

    assert once.tasks[0].instruction == twice.tasks[0].instruction
    assert once.tasks[0].instruction.count("use pandas") == 1


# --- not every upload is a lab ---------------------------------------------


def test_a_confident_non_lab_is_named_not_stack_traced():
    msg = _not_a_lab(_Reading("a two-page CV", Intent(kind="other", confidence=0.95)))

    assert "a two-page CV" in msg
    assert "SpecError" not in msg and "Traceback" not in msg
    assert "upload a lab" in msg.lower()


def test_an_uncertain_guess_reads_as_a_question():
    msg = _not_a_lab(_Reading("some kind of spec", Intent(kind="other", confidence=0.2)))

    assert "not certain" in msg.lower()
    assert "some kind of spec" in msg


def test_no_guess_at_all_still_says_something_useful():
    msg = _not_a_lab(_Reading("", Intent(kind="other", confidence=0.9)))

    assert msg.strip() and "not look like a lab" in msg
