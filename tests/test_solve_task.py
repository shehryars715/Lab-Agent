"""`solve_task` end to end, offline: the explainer seam inside the real loop.

The unit tests in test_explainer.py prove the explainer works. These prove the
ORCHESTRATOR uses it -- which is a different claim, and the one that would break
silently if the call site were moved back inside the retry loop or dropped.

The agent here is a real deepagents agent with a scripted model, the same
technique as test_loop.py. The solution file is written into the workspace up
front rather than through the agent's write_file tool, so these tests pin OUR
contract instead of deepagents' filesystem tool schema.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from fakes import ScriptedModel
from labsagent import events as ev
from labsagent.agent.explainer import Explainer
from labsagent.config import Settings
from labsagent.models import Task
from labsagent.orchestrator import solve_task
from labsagent.runstore import RunStore
from labsagent.usage import RunUsage

SOURCE = 'n = int(input("Enter n: "))\nm = int(input("Enter m: "))\nprint(f"Sum = {n + m}")\n'

# What the solver would have written about its own work: accurate, and exactly
# the debugging-flavoured prose that does not belong under a task in a report.
SOLVER_NOTES = "I first tried reading both values on one line, which crashed, so I fixed it."

EXPLAINER_TEXT = "The program reads two integers with input(). It prints their sum."


class _FakeShots:
    """Satisfies ScreenshotBackend without loading fonts or drawing anything."""

    def render(self, transcript, out_path: Path) -> list[Path]:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"")
        return [out_path]


class _BillingModel:
    """An explain model that reports real usage_metadata, so cost is non-zero."""

    def invoke(self, messages):
        return AIMessage(
            content=EXPLAINER_TEXT,
            usage_metadata={
                "input_tokens": 500,
                "output_tokens": 40,
                "total_tokens": 540,
                "input_token_details": {"cache_read": 0},
            },
        )


def _script() -> list[AIMessage]:
    return [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "run_solution",
                    "args": {"entry_file": "task1.py", "stdin_values": ["5", "3"]},
                    "id": "c1",
                }
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "record_task_result",
                    "args": {
                        "entry_file": "task1.py",
                        "status": "passed",
                        "notes": SOLVER_NOTES,
                    },
                    "id": "c2",
                }
            ],
        ),
        AIMessage(content="Done."),
    ]


@pytest.fixture
def harness(tmp_path):
    """A run store with task1's solution already in its workspace."""
    task = Task(
        id="task1",
        title="Sum of Two Numbers",
        statement="Read two integers and print their sum.",
        sample_inputs=["5", "3"],
    )
    store = RunStore.create("99", root=tmp_path / "runs")
    workspace = store.workspace / task.id
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "task1.py").write_text(SOURCE, encoding="utf-8")

    def run(explainer=None, usage=None):
        return solve_task(
            task,
            store,
            Settings(deepseek_api_key=""),
            _FakeShots(),
            usage if usage is not None else RunUsage(),
            ev.Emitter(),
            {},
            model=ScriptedModel(script=_script()),
            explainer=explainer,
        )

    return run


def test_a_wired_explainer_replaces_the_solvers_own_notes(harness):
    """The whole point of Phase 5, stated as an assertion."""
    result = harness(explainer=lambda task, code, transcript: EXPLAINER_TEXT)

    assert result.outcome.status == "passed"
    assert result.outcome.explanation == EXPLAINER_TEXT
    assert "fixed it" not in (result.outcome.explanation or "")


def test_without_an_explainer_the_solver_notes_remain_the_fallback(harness):
    """Nothing changes for a caller that does not opt in."""
    result = harness(explainer=None)

    assert result.outcome.status == "passed"
    assert result.outcome.explanation == SOLVER_NOTES


def test_an_explainer_that_declines_falls_back_rather_than_blanking(harness):
    """A None reply means "no opinion", not "erase what was there"."""
    result = harness(explainer=lambda task, code, transcript: None)

    assert result.outcome.explanation == SOLVER_NOTES


def test_a_throwing_explainer_does_not_kill_a_task_that_passed(harness):
    """Cosmetics must never take down work that is already finished.

    `Explainer` swallows its own model errors, but the orchestrator sees `explainer`
    as any callable, so the guarantee has to live at the call site. Same
    judgement, and same reason, as the guard around artifact collection.
    """

    def boom(task, code, transcript):
        raise RuntimeError("explainer exploded")

    result = harness(explainer=boom)

    assert result.outcome.status == "passed"
    assert result.outcome.explanation == SOLVER_NOTES


def test_the_explainer_sees_the_final_code_and_transcript(harness):
    seen = {}

    def capture(task, code, transcript):
        seen.update(task=task, code=code, transcript=transcript)
        return EXPLAINER_TEXT

    harness(explainer=capture)

    assert seen["task"].id == "task1"
    assert 'int(input("Enter n: "))' in seen["code"]
    assert "Sum = 8" in "\n".join(seen["transcript"].display_lines())


def test_task_cost_counts_the_explain_phase_too(harness):
    """A solve-only delta under-reports the moment explaining costs money.

    This is why `solve_task` measures against `usage.total` rather than against
    the solve phase it opened: the explain call bills to a different phase, and
    this number is what the web UI shows beside each task.
    """
    usage = RunUsage()
    result = harness(explainer=Explainer(model=_BillingModel(), usage=usage), usage=usage)

    explain = usage.phase("explain")
    assert explain.calls == 1
    assert explain.cost_usd > 0
    assert result.cost_usd >= explain.cost_usd
    assert result.outcome.explanation == EXPLAINER_TEXT
