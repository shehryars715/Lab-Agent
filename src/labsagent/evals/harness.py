"""Run the eval set and score it.

TWO NUMBERS, NOT ONE. Every task is scored on two independent questions:

    ran      -- did the pipeline produce a program that executed cleanly?
    matched  -- did that program print the right thing?

Collapsing them loses the finding. `ran` alone is the trade-off §16 accepts for
production, and as a measurement instrument it is close to useless: a prompt
change that makes every solution subtly wrong still scores 100%, because every
wrong program still exits 0. `matched` alone cannot tell a broken pipeline from
a wrong answer. Reported side by side, the pair says which one moved.

A case with no declared golden reports `matched` as None -- unscored, printed as
"-". That is the honest answer for a real manual nobody has written an expected
output for, and it is much better than inventing one.

THE SET IS SAMPLED, NOT RUN. `temperature=0` is not deterministic here: the same
task has taken 4, 6 and 6 turns, and the same ingest has produced 1,566 and
6,447 output tokens. One pass over the set is an anecdote. `--samples N` turns
it into a distribution, and the report shows the spread rather than hiding it
behind a mean.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from labsagent import capabilities
from labsagent import events as ev
from labsagent.capture.base import ScreenshotBackend
from labsagent.config import Settings
from labsagent.evals.cases import EvalCase, Expectation
from labsagent.models import TaskOutcome
from labsagent.orchestrator import run_lab
from labsagent.runstore import RunStore
from labsagent.usage import RunUsage

ModelFactory = Callable[[str], Any]


# --- scoring: pure, so it is testable without spending anything --------------


def normalize(text: str) -> str:
    """Compare what a reader would see, not what the bytes happen to be.

    Trailing whitespace and line-ending style are invisible in a screenshot and
    must not decide a pass. Everything else is significant: 'Total: 24.5' is a
    WRONG answer to a task that asked for two decimal places, and a matcher
    generous enough to accept it is not measuring anything.
    """
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


@dataclass
class TaskScore:
    position: int
    task_id: str
    ran: bool
    matched: bool | None
    attempts: int
    detail: str = ""
    #: run_solution calls across the task -- the write/run/fix cycles.
    runs: int = 0

    @property
    def ok(self) -> bool:
        """Everything that was scored, scored well."""
        return self.ran and self.matched is not False


def score_task(expectation: Expectation, outcome: TaskOutcome, position: int) -> TaskScore:
    """Score one task against what it was supposed to do."""
    passed = outcome.status == "passed"

    if expectation.should_fail:
        # The failure drill. Reporting this task as passed means the agent
        # fabricated a result, which is the worst thing this system can do --
        # worse than crashing, because a crash is visible.
        return TaskScore(
            position=position,
            task_id=outcome.task.id,
            ran=not passed,
            matched=None,
            attempts=outcome.attempts,
            runs=getattr(outcome, "runs", 0),
            detail="" if not passed else "FALSE SUCCESS: impossible task reported as passed",
        )

    if not passed:
        return TaskScore(
            position=position,
            task_id=outcome.task.id,
            ran=False,
            matched=None if expectation.stdout is None else False,
            attempts=outcome.attempts,
            runs=getattr(outcome, "runs", 0),
            detail=(outcome.error or "failed")[:120],
        )

    matched: bool | None = None
    detail = ""

    if expectation.stdout is not None:
        actual = normalize("\n".join(outcome.transcript.lines) if outcome.transcript else "")
        wanted = normalize(expectation.stdout)
        matched = actual == wanted
        if not matched:
            detail = f"expected {wanted.splitlines()[-1:]!r}, got {actual.splitlines()[-1:]!r}"

    if expectation.expects_figure and not outcome.figure_paths:
        matched = False
        detail = "no figure was produced"

    return TaskScore(
        position=position,
        task_id=outcome.task.id,
        ran=True,
        matched=matched,
        attempts=outcome.attempts,
            runs=getattr(outcome, "runs", 0),
        detail=detail,
    )


@dataclass
class SampleResult:
    case: str
    sample: int
    scores: list[TaskScore] = field(default_factory=list)
    tasks_found: int = 0
    tasks_expected: int = 0
    cost_usd: float = 0.0
    duration_s: float = 0.0
    usage: dict = field(default_factory=dict)
    error: str | None = None

    @property
    def ran_count(self) -> int:
        return sum(1 for s in self.scores if s.ran)

    @property
    def matched_count(self) -> int:
        return sum(1 for s in self.scores if s.matched is True)

    @property
    def scored_count(self) -> int:
        """Tasks that had a golden to compare against."""
        return sum(1 for s in self.scores if s.matched is not None)

    @property
    def attempts_total(self) -> int:
        return sum(s.attempts for s in self.scores)

    @property
    def runs_total(self) -> int:
        return sum(s.runs for s in self.scores)

    @property
    def clean(self) -> bool:
        """No error, the expected number of tasks, and every one of them ok."""
        return (
            self.error is None
            and self.tasks_found == self.tasks_expected
            and bool(self.scores)
            and all(s.ok for s in self.scores)
        )


def expects_gate(case: EvalCase) -> bool:
    return any(e.out_of_scope or e.prerequisite for e in case.expected.values())


def score_gate(case: EvalCase, spec, out_of_scope, needed) -> list[TaskScore]:
    """Score a case that stopped BEFORE solving: refused, or paused to ask.

    Mirrors the web pipeline, which solves nothing when either gate fires. For
    these scores `ran` means "the gate decided as declared" -- refused exactly
    the tasks it should, flagged exactly the prerequisites it should. Every
    ordinary case therefore guards against a false refusal: a gate that fires
    on it scores the case unclean.
    """
    refused = {o.task_id: o for o in out_of_scope}
    flagged = {task_id for item in needed for task_id in item.task_ids}
    scores = []
    for position, task in enumerate(spec.tasks, start=1):
        expectation = case.expected.get(position, Expectation())
        got_out, got_pre = task.id in refused, task.id in flagged
        # An impossible task refused up front has failed honestly, and early.
        want_out = expectation.out_of_scope or (expectation.should_fail and got_out)
        problems = []
        if got_out and not want_out:
            problems.append("wrongly refused: " + "; ".join(refused[task.id].reasons))
        elif want_out and not got_out:
            problems.append("not refused, but it is out of scope")
        if got_pre != expectation.prerequisite:
            problems.append(
                "flagged a prerequisite that is not needed" if got_pre else "missed the prerequisite"
            )
        verdict = "refused before solving" if got_out else "stopped to ask" if got_pre else ""
        scores.append(
            TaskScore(
                position=position,
                task_id=task.id,
                ran=not problems,
                matched=None,
                attempts=0,
                detail="; ".join(problems) or verdict,
            )
        )
    return scores


def score_sample(case: EvalCase, outcomes: list[TaskOutcome]) -> list[TaskScore]:
    """Pair outcomes with expectations BY POSITION, not by id.

    Ingest assigns the ids, so matching on them would make an eval that cannot
    see ingest dropping a task -- the two lists would simply line up on whatever
    survived. Position makes a missing task a missing score.
    """
    scores = []
    for position, outcome in enumerate(outcomes, start=1):
        expectation = case.expected.get(position, Expectation())
        scores.append(score_task(expectation, outcome, position))
    return scores


# --- running ----------------------------------------------------------------


def run_sample(
    case: EvalCase,
    sample: int,
    settings: Settings,
    screenshots: ScreenshotBackend,
    model_factory: ModelFactory,
    runs_root: Path,
    explainer_factory: Callable[[RunUsage], Any] | None = None,
    emitter: ev.Emitter | None = None,
) -> SampleResult:
    """One full pipeline run over one case: ingest, solve, score.

    Ingest is inside the measurement on purpose. "Cost per lab" that excludes
    extraction is not the cost of a lab, and anchor bugs -- the expensive kind --
    live in exactly the step it would exclude.

    Every failure is caught and recorded. An eval that dies on case 3 of 7 tells
    you nothing about cases 4 through 7, which is the opposite of the job.

    The explainer arrives as a FACTORY, not an instance, because it has to bill
    into the RunUsage this sample creates. Handed a ready-made explainer built
    against someone else's usage object, the explain cost would land somewhere
    the report never reads -- and a cost measurement with a silently missing
    phase is worse than no measurement, because it looks complete.
    """
    from labsagent.ingest.docx_reader import read_manual
    from labsagent.errors import SpecError
    from labsagent.ingest.labspec import extract_labspec

    result = SampleResult(
        case=case.name, sample=sample, tasks_expected=case.task_count
    )
    usage = RunUsage(model=settings.model_name)
    explainer = explainer_factory(usage) if explainer_factory is not None else None
    started = time.monotonic()

    try:
        manual = read_manual(case.manual_path)
        # `extract_labspec` returns a `Reading`, not a tuple. This still
        # unpacked three values, so EVERY case died with "cannot unpack
        # non-iterable Reading object" and the eval reported 0 tasks found --
        # a broken gate that looked like a broken pipeline.
        reading = extract_labspec(manual, model_factory("ingest"))
        usage.phase("ingest").merge(reading.usage)
        if reading.spec is None:
            raise SpecError(f"not read as a lab: {reading.what_this_is or 'unrecognised'}")
        spec = reading.spec
        result.tasks_found = len(spec.tasks)

        # THE PRE-SOLVE GATES, as the web pipeline applies them. This harness
        # goes straight from ingest to `run_lab`, so without this check the
        # eval could not see either gate -- neither a correct refusal nor a
        # wrong one.
        out_of_scope = capabilities.check(spec, reading.requirements)
        needed = list(reading.intent.prerequisites)
        if out_of_scope or needed or expects_gate(case):
            result.scores = score_gate(case, spec, out_of_scope, needed)
            result.duration_s = time.monotonic() - started
            result.cost_usd = usage.total.cost_usd
            result.usage = usage.as_dict()
            return result

        store = RunStore.create(spec.lab_number, root=runs_root)
        manifest = run_lab(
            spec,
            store,
            settings,
            screenshots,
            emitter=emitter or ev.Emitter(),
            usage=usage,
            model=model_factory("solve"),
            explainer=explainer,
        )
        result.scores = score_sample(case, manifest.outcomes)
    except Exception as exc:  # noqa: BLE001
        result.error = f"{type(exc).__name__}: {exc}"[:300]

    result.duration_s = time.monotonic() - started
    result.cost_usd = usage.total.cost_usd
    result.usage = usage.as_dict()
    return result


def run_case(
    case: EvalCase,
    samples: int,
    settings: Settings,
    screenshots: ScreenshotBackend,
    model_factory: ModelFactory,
    runs_root: Path,
    explainer_factory: Callable[[RunUsage], Any] | None = None,
    on_sample: Callable[[SampleResult], None] | None = None,
) -> list[SampleResult]:
    results = []
    for sample in range(1, samples + 1):
        outcome = run_sample(
            case, sample, settings, screenshots, model_factory, runs_root, explainer_factory
        )
        results.append(outcome)
        if on_sample is not None:
            on_sample(outcome)
    return results
