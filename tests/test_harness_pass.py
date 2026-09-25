"""The 2026-09-24 harness pass: written answers, styles, sections, follow-ups,
optional questions. Offline -- no model, no network."""

from __future__ import annotations

import sys
from pathlib import Path

import nbformat

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from labsagent.agent.explainer import build_brief, parse_writeup  # noqa: E402
from labsagent.blocks import blocks_for  # noqa: E402
from labsagent.capture.shim import split_cells  # noqa: E402
from labsagent.models import LabSpec, Task, TaskOutcome, Transcript  # noqa: E402
from labsagent.orchestrator import build_task_prompt  # noqa: E402
from labsagent.package.notebook import build_notebook  # noqa: E402
from labsagent.present import arrange  # noqa: E402
from labsagent.prose import looks_like_prose, prose_lines  # noqa: E402
from labsagent.runner import run_sections  # noqa: E402
from labsagent.sandbox.local import LocalSandbox  # noqa: E402
from web.server.pipeline import answered_notes, build_questions, revise_instruction  # noqa: E402
from web.server.revise import FollowUpPlan, TaskChange, to_followup  # noqa: E402


def _task(**kw) -> Task:
    base = dict(id="task1", title="XOR", statement="Explain why the perceptron fails on XOR.")
    base.update(kw)
    return Task(**base)


def _outcome(task=None, **kw) -> TaskOutcome:
    task = task or _task(written_questions=["Why does it fail?"])
    base = dict(
        task=task,
        status="passed",
        code_text="print(0.75)",
        transcript=Transcript(command="python task1.py", lines=["0.75"]),
        explanation="The program trains a perceptron.",
        answers=[{"question": "Why does it fail?", "answer": "XOR is not linearly separable."}],
    )
    base.update(kw)
    return TaskOutcome(**base)


# --- 1. written answers are text ------------------------------------------


def test_prose_is_told_apart_from_results():
    assert looks_like_prose("The perceptron cannot separate the classes because they interleave.")
    assert not looks_like_prose("Accuracy: 0.75")
    assert not looks_like_prose("x1=0 x2=1 target=1 prediction=0")
    assert not looks_like_prose("=== ANALYSIS: WHY THE PERCEPTRON FAILS ON XOR ===")
    assert len(prose_lines("Accuracy: 0.75\nThe best run of all of them still leaves one point on the wrong side.")) == 1


def test_the_solver_is_told_which_answers_are_written_separately():
    prompt = build_task_prompt(_task(written_questions=["Why does it fail?"]), {})
    assert "Why does it fail?" in prompt
    assert "do not print these answers" in prompt
    assert prompt.rstrip().endswith("task1.py")  # the filename rule stays last


def test_the_brief_numbers_the_questions_and_leaks_nothing():
    brief = build_brief(_task(written_questions=["Why does it fail?"]), "print(1)", None)
    assert "1. Why does it fail?" in brief
    for leak in ("Traceback", "attempt", "retry", "failed", "fix"):
        assert leak.lower() not in brief.lower()


def test_a_writeup_parses_json_and_falls_back_to_plain_text():
    parsed = parse_writeup(
        '```json\n{"overview": "It trains. It predicts. Extra.", "answers": ["Not separable."]}\n```',
        ["Why?"],
    )
    assert parsed.overview == "It trains. It predicts."
    assert parsed.answers == [{"question": "Why?", "answer": "Not separable."}]
    assert parse_writeup("Just prose back.", ["Why?"]).overview == "Just prose back."
    assert parse_writeup("   ", ["Why?"]) is None


def test_a_theory_task_has_no_code_block():
    outcome = _outcome(task=_task(needs_code=False, written_questions=["Why?"]), code_text="")
    assert [b.kind for b in blocks_for(outcome)] == ["prose", "prose"]


def test_the_notebook_writes_answers_as_markdown_and_has_no_setup_cell():
    nb = build_notebook(LabSpec(lab_number="10", title="NN", tasks=[]), [_outcome()])
    text = nbformat.writes(nb)
    assert "pip install" not in text
    md = [c.source for c in nb.cells if c.cell_type == "markdown"]
    assert any("XOR is not linearly separable." in s for s in md)
    code = [c for c in nb.cells if c.cell_type == "code"]
    assert "not linearly separable" not in "".join(o.get("text", "") for c in code for o in c.outputs)


# --- 2. styles and sections -------------------------------------------------


def test_classic_is_untouched_and_findings_leads_with_the_answers():
    outcome = _outcome()
    assert arrange(outcome, "classic") == blocks_for(outcome)
    assert arrange(outcome, "findings")[0].role == "answer"
    assert arrange(outcome, "walkthrough")[-1].role == "answer"


def test_sections_become_separate_cells_with_their_own_output():
    outcome = _outcome(
        sections=[
            {"title": "Data", "code": "x = 1\nprint(x)", "output": "1", "figures": []},
            {"title": "Train", "code": "print(x + 1)", "output": "2", "figures": []},
        ]
    )
    nb = build_notebook(LabSpec(lab_number="10", title="NN", tasks=[]), [outcome], style="walkthrough")
    code = [c for c in nb.cells if c.cell_type == "code"]
    assert [c.outputs[0]["text"].strip() for c in code] == ["1", "2"]
    assert any(c.source == "### Train" for c in nb.cells)


def test_cells_split_only_at_top_level_markers():
    source = "import math\n# %% Load\nx = 1\ndef f():\n    # %% not a split\n    return x\n# %% Show\nprint(f())\n"
    assert [t for t, _code, _start in split_cells(source)] == ["", "Load", "Show"]


def test_a_section_run_records_real_per_section_output(tmp_path):
    with LocalSandbox(workdir=tmp_path, keep=True) as sandbox:
        sandbox.write_file("task1.py", "# %% One\nx = 2\nprint(x)\n# %% Two\nprint(x * 3)\n")
        sections = run_sections(sandbox, "task1.py")
    assert [s["title"] for s in sections] == ["One", "Two"]
    assert [s["lines"] for s in sections] == [["2"], ["6"]]


def test_a_program_without_sections_keeps_the_single_block(tmp_path):
    with LocalSandbox(workdir=tmp_path, keep=True) as sandbox:
        sandbox.write_file("task1.py", "print(1)\n")
        assert run_sections(sandbox, "task1.py") is None


# --- 3. follow-ups ------------------------------------------------------------


def test_a_change_that_names_nothing_becomes_a_question_not_a_full_redo():
    followup = to_followup(FollowUpPlan(kind="change"), ["task1", "task2"])
    assert followup.is_answer and followup.reply
    assert not followup.resolve_ids


def test_routing_drops_unknown_ids_and_never_resolves_and_rewrites_one_task():
    plan = FollowUpPlan(
        kind="change",
        resolve=TaskChange(task_ids=["task1", "task9"], instruction="use a while loop"),
        rewrite=TaskChange(task_ids=["task1", "task2"], instruction="shorter"),
        artifacts=["docx", "pdf", ".py"],
        style="nonsense",
    )
    followup = to_followup(plan, ["task1", "task2"])
    assert followup.resolve_ids == ["task1"]
    assert followup.rewrite_ids == ["task2"]
    assert followup.artifacts == ["docx", "py"]
    assert followup.style == ""


def test_revision_instructions_accumulate_and_carry_the_earlier_code():
    first = revise_instruction("", "use pandas", "import csv")
    second = revise_instruction(first, "print a table", "import pandas")
    assert "use pandas" in second and "print a table" in second
    assert "import pandas" in second and "import csv" not in second
    assert second.count("This is a revision.") == 1


# --- 4. questions ---------------------------------------------------------------


def test_identity_is_never_asked_up_front():
    class Facts:
        course = section = instructor = lab_engineer = date = None

    questions, _known = build_questions({}, Facts(), [])
    assert questions == []


def test_a_skipped_question_passes_its_stated_default_to_the_solver():
    questions = [{"key": "dataset", "label": "Which dataset?", "default": "Generate one."}]
    assert "Generate one." in answered_notes(questions, {})
    assert "Generate one." not in answered_notes(questions, {"dataset": "iris.csv"})
