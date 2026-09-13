"""The eval set's own tests, offline.

An eval is a measuring instrument, and an uncalibrated instrument is worse than
none: it produces numbers that look like evidence. So the scoring logic is pure
and tested here without spending anything, and the fixture set is checked for
internal consistency -- a golden that points at a task which no longer exists
would silently stop scoring rather than fail.
"""

from __future__ import annotations

from labsagent.evals.cases import Expectation, build_cases
from labsagent.evals.harness import (
    SampleResult,
    TaskScore,
    normalize,
    score_sample,
    score_task,
)
from labsagent.evals.manuals import MANUALS
from labsagent.evals.report import render_table, summarize
from labsagent.models import ExecResult, Task, TaskOutcome, Transcript


def _outcome(stdout: str = "", status: str = "passed", **kw) -> TaskOutcome:
    transcript = Transcript.from_exec(
        "python task1.py",
        ExecResult(exit_code=0, stdout=stdout, stderr="", duration_s=0.1, timed_out=False),
    )
    return TaskOutcome(
        task=Task(id="task1", title="T", statement="S"),
        status=status,
        transcript=transcript,
        attempts=kw.pop("attempts", 1),
        **kw,
    )


# --- normalisation ----------------------------------------------------------


def test_trailing_whitespace_and_line_endings_do_not_decide_a_pass():
    assert normalize("Sum = 8  \r\n") == normalize("Sum = 8")


def test_blank_lines_around_the_output_are_ignored():
    assert normalize("\n\nSum = 8\n\n") == "Sum = 8"


def test_interior_content_is_significant():
    """A generous matcher measures nothing. 24.5 != 24.50."""
    assert normalize("Total: 24.5") != normalize("Total: 24.50")


# --- scoring one task -------------------------------------------------------


def test_a_correct_program_scores_both_ran_and_matched():
    score = score_task(Expectation(stdout="Sum = 8"), _outcome("Sum = 8\n"), 1)

    assert score.ran is True
    assert score.matched is True
    assert score.ok


def test_a_program_that_runs_but_prints_the_wrong_thing_is_caught():
    """The regression run-to-green cannot see: it executed perfectly and is wrong."""
    score = score_task(Expectation(stdout="Total: 24.50"), _outcome("Total: 24.5\n"), 1)

    assert score.ran is True, "it really did run"
    assert score.matched is False
    assert not score.ok
    assert "24.50" in score.detail


def test_a_failed_task_is_not_credited_with_a_match():
    score = score_task(
        Expectation(stdout="Sum = 8"), _outcome("", status="failed", error="boom"), 1
    )

    assert score.ran is False
    assert score.matched is False
    assert "boom" in score.detail


def test_a_case_without_a_golden_leaves_matched_unscored():
    score = score_task(Expectation(), _outcome("anything at all\n"), 1)

    assert score.ran is True
    assert score.matched is None, "unscored, not failed"
    assert score.ok


def test_a_missing_figure_fails_a_task_that_was_supposed_to_draw_one():
    score = score_task(
        Expectation(stdout="Chart saved", expects_figure=True), _outcome("Chart saved\n"), 1
    )

    assert score.matched is False
    assert "figure" in score.detail


def test_a_produced_figure_satisfies_the_expectation():
    outcome = _outcome("Chart saved\n", figure_paths=["sales_chart.png"])
    score = score_task(Expectation(stdout="Chart saved", expects_figure=True), outcome, 1)

    assert score.matched is True


# --- the failure drill ------------------------------------------------------


def test_failing_an_impossible_task_is_the_correct_outcome():
    score = score_task(
        Expectation(should_fail=True), _outcome("", status="failed", error="dns"), 1
    )

    assert score.ran is True, "correctly failing IS the pass for this case"
    assert score.ok
    assert score.detail == ""


def test_passing_an_impossible_task_is_reported_as_a_false_success():
    """The worst outcome this system can produce, and it must be loud."""
    score = score_task(Expectation(should_fail=True), _outcome("Mean score: 72.5\n"), 1)

    assert score.ran is False
    assert not score.ok
    assert "FALSE SUCCESS" in score.detail


# --- scoring a sample -------------------------------------------------------


def test_a_dropped_task_shows_up_as_a_missing_score():
    """Ingest losing a task must be visible, which id-based pairing would hide."""
    from labsagent.evals.cases import EvalCase
    from pathlib import Path

    case = EvalCase(
        name="two_tasks",
        manual_path=Path("unused.docx"),
        lab_number="01",
        expected={1: Expectation(stdout="A"), 2: Expectation(stdout="B")},
    )
    scores = score_sample(case, [_outcome("A\n")])
    result = SampleResult(
        case=case.name, sample=1, scores=scores, tasks_found=1, tasks_expected=2
    )

    assert len(scores) == 1
    assert not result.clean, "one task of two is not a clean run"


def test_a_clean_sample_requires_every_task_to_be_ok():
    scores = [
        TaskScore(position=1, task_id="task1", ran=True, matched=True, attempts=1),
        TaskScore(position=2, task_id="task2", ran=True, matched=False, attempts=2),
    ]
    result = SampleResult(case="c", sample=1, scores=scores, tasks_found=2, tasks_expected=2)

    assert result.ran_count == 2
    assert result.matched_count == 1
    assert result.attempts_total == 3
    assert not result.clean


# --- aggregation ------------------------------------------------------------


def _sample(case: str, sample: int, clean: bool, cost: float) -> SampleResult:
    score = TaskScore(
        position=1, task_id="task1", ran=clean, matched=clean, attempts=1
    )
    return SampleResult(
        case=case,
        sample=sample,
        scores=[score],
        tasks_found=1,
        tasks_expected=1,
        cost_usd=cost,
        duration_s=10.0,
    )


def test_samples_that_disagree_about_passing_are_flagged_flaky():
    summaries = summarize(
        [_sample("c", 1, True, 0.001), _sample("c", 2, False, 0.002)]
    )

    assert len(summaries) == 1
    assert summaries[0].flaky, "a case that passes sometimes is a finding, not an average"


def test_a_case_that_always_passes_is_not_flaky():
    summaries = summarize([_sample("c", 1, True, 0.001), _sample("c", 2, True, 0.001)])

    assert not summaries[0].flaky
    assert summaries[0].clean_count == 2


def test_the_table_shows_the_cost_range_when_samples_disagree():
    """Hiding a 10x spread behind a mean is how a budget surprise happens."""
    table = render_table(summarize([_sample("c", 1, True, 0.0002), _sample("c", 2, True, 0.0026)]))

    assert "0.00020-0.00260" in table.replace(" ", "")
    assert "TOTAL" in table


def test_the_table_renders_with_a_single_sample():
    table = render_table(summarize([_sample("c", 1, True, 0.001)]))

    assert "c" in table and "1/1" in table


# --- fixture-set consistency ------------------------------------------------


def test_every_manual_has_goldens_for_every_task(tmp_path):
    """A golden pointing at a task that no longer exists stops scoring silently."""
    for case in build_cases(tmp_path):
        spec = MANUALS[case.name]
        assert case.expected, f"{case.name} has no expectations"
        assert set(case.expected) == set(range(1, len(spec.tasks) + 1)), (
            f"{case.name}: goldens {sorted(case.expected)} do not cover "
            f"{len(spec.tasks)} task(s)"
        )


def test_the_set_covers_a_failure_case(tmp_path):
    """Without one, the failure path is never exercised by the eval at all."""
    cases = {c.name: c for c in build_cases(tmp_path)}

    assert any(
        e.should_fail for case in cases.values() for e in case.expected.values()
    ), "the eval set must contain at least one task that cannot pass"


def test_manuals_render_to_real_docx_files(tmp_path):
    cases = build_cases(tmp_path)

    assert len(cases) >= 5, "the plan asks for 5-10 cases"
    for case in cases:
        assert case.manual_path.exists()
        assert case.manual_path.stat().st_size > 1000


def test_selecting_a_subset_runs_only_those_cases(tmp_path):
    cases = build_cases(tmp_path, only=["formatting", "strings"])

    assert sorted(c.name for c in cases) == ["formatting", "strings"]
