"""Scoping: solve what was asked for, plus whatever that needs to run."""

from __future__ import annotations

from labsagent.intent import (
    ASK_BELOW,
    Intent,
    detect_formats,
    referenced_task_ids,
    scope,
)
from labsagent.models import LabSpec, Task


def _spec() -> LabSpec:
    return LabSpec(
        lab_number="03",
        title="Lab 03",
        tasks=[
            Task(id="task1", title="Load", statement="Read the CSV into a dataframe."),
            Task(id="task2", title="Clean", statement="Drop nulls."),
            Task(id="task3", title="Plot", statement="Using the dataframe from task 1, plot it."),
            Task(id="task4", title="Report", statement="Summarise task 3's plot."),
        ],
    )


def test_no_scope_returns_everything_untouched():
    spec = _spec()
    narrowed, pulled = scope(spec, Intent())

    assert narrowed is spec and pulled == []


def test_a_standalone_task_comes_back_alone():
    """'only task 2' means the task list IS task 2 -- not four with one solved."""
    narrowed, pulled = scope(_spec(), Intent(task_ids=["task2"]))

    assert [t.id for t in narrowed.tasks] == ["task2"]
    assert pulled == []


def test_a_prerequisite_is_pulled_in_and_named():
    narrowed, pulled = scope(_spec(), Intent(task_ids=["task3"]))

    assert [t.id for t in narrowed.tasks] == ["task1", "task3"]
    assert pulled == ["task1"], "the caller has to be able to say what it added"


def test_prerequisites_are_transitive():
    """task4 needs task3, which needs task1. All three, in document order."""
    narrowed, pulled = scope(_spec(), Intent(task_ids=["task4"]))

    assert [t.id for t in narrowed.tasks] == ["task1", "task3", "task4"]
    assert pulled == ["task1", "task3"]


def test_an_unknown_id_falls_back_to_everything():
    """Safer than solving nothing when the request matched no real task."""
    narrowed, pulled = scope(_spec(), Intent(task_ids=["task99"]))

    assert len(narrowed.tasks) == 4 and pulled == []


def test_a_task_does_not_depend_on_itself():
    task = Task(id="task3", title="T", statement="Extend task 3 and task 1.")

    assert referenced_task_ids(task) == {"task1"}


def test_confidence_drives_the_ask():
    assert Intent(confidence=ASK_BELOW - 0.01).uncertain
    assert not Intent(confidence=ASK_BELOW).uncertain
    assert Intent(kind="other").is_lab is False
    assert Intent(kind="notebook_lab").is_lab is True


def test_notes_accumulate_without_clobbering():
    i = Intent(notes="use pandas").with_notes("seed is 42")

    assert "use pandas" in i.notes and "seed is 42" in i.notes
    assert i.with_notes("") .notes == i.notes


class TestDetectFormats:
    """REGRESSION. A real request -- "Provide the completed lab as an executed
    .ipynb notebook file that includes the run outputs" -- was filed by the
    model under notes rather than artifacts, so the run produced a .docx and
    handed the sentence to the SOLVER as a coding instruction. A literal
    extension is not a judgement call; this is the deterministic backstop.
    """

    def test_the_request_that_produced_a_docx(self):
        assert detect_formats(
            "Provide the completed lab as an executed .ipynb notebook file "
            "that includes the run outputs."
        ) == ["ipynb"]

    def test_every_format_is_recognised(self):
        assert detect_formats("word report") == ["docx"]
        assert detect_formats("a .py file") == ["py"]
        assert detect_formats("as a markdown write-up") == ["md"]
        assert detect_formats("zip it up") == ["zip"]
        assert detect_formats("a jupyter notebook") == ["ipynb"]

    def test_several_at_once_keep_registry_order(self):
        got = detect_formats("the word report, a notebook, the python file and a zip")
        assert set(got) == {"docx", "ipynb", "py", "zip"}

    def test_a_negated_format_is_not_requested(self):
        got = detect_formats("I want the notebook and the python script, and no zip")
        assert "zip" not in got
        assert set(got) == {"ipynb", "py"}

    def test_code_instructions_are_not_formats(self):
        """The inverse mistake: reading a note about the code as a deliverable."""
        assert detect_formats("the script should handle negative numbers") == []
        assert detect_formats("use pandas and seed the RNG with 42") == []
        assert detect_formats("solve everything please") == []

    def test_a_revision_can_ask_for_a_different_format(self):
        assert detect_formats("redo task 2 but give me a notebook instead") == ["ipynb"]
