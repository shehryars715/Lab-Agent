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
from labsagent.models import Task, TaskOutcome
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


def test_figures_survive_a_later_run_that_draws_nothing(tmp_path):
    """A chart must not be lost because the agent ran something else after it.

    `run_solution` reports figures as the files that are NEW since that call,
    which is right per call and wrong per task. Three real calls in sequence --
    solution, diagnostic probe, solution again -- produce a figure, then
    nothing, then nothing (the second run OVERWRITES the .png rather than
    creating it). Assigning the last value threw the chart away.
    """
    from labsagent.agent.tools import TaskRecorder, build_tools
    from labsagent.sandbox.local import LocalSandbox

    with LocalSandbox(workdir=tmp_path / "ws", keep=True) as sandbox:
        recorder = TaskRecorder()
        run_solution, _ = build_tools(sandbox, recorder)

        # Writes a 1x1 PNG. Deterministic, and no matplotlib needed.
        sandbox.write_file(
            "task1.py",
            "import base64\n"
            "open('chart.png','wb').write(base64.b64decode("
            "'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='))\n"
            "print('done')\n",
        )
        sandbox.write_file("probe.py", "print('just checking')\n")

        run_solution.invoke({"entry_file": "task1.py", "stdin_values": []})
        assert recorder.figures == ["chart.png"]

        run_solution.invoke({"entry_file": "probe.py", "stdin_values": []})
        run_solution.invoke({"entry_file": "task1.py", "stdin_values": []})

        assert recorder.figures == ["chart.png"], "the chart is still on disk and still ours"


# --- harvesting the right file ----------------------------------------------
#
# A run that wrote and ran a correct solution, then ran a housekeeping script,
# used to ship the housekeeping script. `runs/20260922-175718_lab02` did exactly
# that: `code/task3.py` was a six-line file that deletes `ls.py`, its screenshot
# read "cleaned", and the task was marked passed. These pin the fix.


def _chore_script(chore: str = "cleanup.py") -> list[AIMessage]:
    """Solve the task, then tidy up. Both runs exit 0."""
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
                    "name": "run_solution",
                    "args": {"entry_file": chore, "stdin_values": []},
                    "id": "c2",
                }
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "record_task_result",
                    "args": {"entry_file": "task1.py", "status": "passed", "notes": ""},
                    "id": "c3",
                }
            ],
        ),
        AIMessage(content="Done."),
    ]


def _solve(store, task, script, settings=None):
    return solve_task(
        task,
        store,
        settings or Settings(deepseek_api_key=""),
        _FakeShots(),
        RunUsage(),
        ev.Emitter(),
        {},
        model=ScriptedModel(script=script),
    )


def _task() -> Task:
    return Task(
        id="task1",
        title="Sum of Two Numbers",
        statement="Read two integers and print their sum.",
        sample_inputs=["5", "3"],
    )


def _workspace(tmp_path, *, solution: str | None = SOURCE, **extra: str):
    task = _task()
    store = RunStore.create("99", root=tmp_path / "runs")
    workspace = store.workspace / task.id
    workspace.mkdir(parents=True, exist_ok=True)
    if solution is not None:
        (workspace / "task1.py").write_text(solution, encoding="utf-8")
    for name, text in extra.items():
        (workspace / name.replace("__", ".")).write_text(text, encoding="utf-8")
    return task, store


CHORE = "import os\nprint('cleaned')\n"


def test_a_chore_run_after_the_solution_does_not_become_the_solution(tmp_path):
    """The bug, verbatim: the last thing that ran was not the task."""
    task, store = _workspace(tmp_path, cleanup__py=CHORE)
    result = _solve(store, task, _chore_script())

    assert result.outcome.status == "passed"
    assert result.outcome.code_text == SOURCE, "the chore must not be shipped as the solution"
    assert "cleaned" not in "\n".join(result.outcome.transcript.lines)
    assert "Sum = 8" in "\n".join(result.outcome.transcript.lines)
    assert store.code_dir.joinpath("task1.py").read_text(encoding="utf-8") == SOURCE


def test_a_chore_that_shares_no_name_with_the_task_is_never_chosen(tmp_path):
    """Even when the agent declares the chore, only a real run of the task counts."""
    task, store = _workspace(tmp_path, probe__py=CHORE)
    script = _chore_script("probe.py")
    script[2].tool_calls[0]["args"]["entry_file"] = "probe.py"
    result = _solve(store, task, script)

    assert result.outcome.code_text == SOURCE


def test_a_probe_only_attempt_is_rescued_without_spending_tokens(tmp_path):
    """The solution is on disk and works; only a probe was ever run."""
    task, store = _workspace(tmp_path, probe__py=CHORE)
    script = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "run_solution",
                    "args": {"entry_file": "probe.py", "stdin_values": []},
                    "id": "c1",
                }
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "record_task_result",
                    "args": {"entry_file": "task1.py", "status": "passed", "notes": ""},
                    "id": "c2",
                }
            ],
        ),
        AIMessage(content="Done."),
    ]
    result = _solve(store, task, script)

    assert result.outcome.status == "passed"
    assert result.outcome.code_text == SOURCE
    assert "Sum = 8" in "\n".join(result.outcome.transcript.lines)


def test_no_successful_run_of_the_task_file_fails_honestly(tmp_path):
    """A chore ran, the task file does not exist. Better a failure than a lie."""
    task, store = _workspace(tmp_path, solution=None, cleanup__py=CHORE)
    result = _solve(store, task, _chore_script())

    assert result.outcome.status == "failed"
    assert "no successful run of task1.py" in (result.outcome.error or "")


def test_a_transcript_stale_against_an_edited_file_is_re_run(tmp_path):
    """The agent edited the file after running it, so the output is not its output."""
    task, store = _workspace(tmp_path)
    edited = SOURCE.replace("Sum =", "Total =")
    script = [
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
                    "name": "write_file",
                    "args": {"file_path": "task1.py", "content": edited},
                    "id": "c2",
                }
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "record_task_result",
                    "args": {"entry_file": "task1.py", "status": "passed", "notes": ""},
                    "id": "c3",
                }
            ],
        ),
        AIMessage(content="Done."),
    ]
    result = _solve(store, task, script)

    assert result.outcome.status == "passed"
    assert result.outcome.code_text == edited
    lines = "\n".join(result.outcome.transcript.lines)
    assert "Total = 8" in lines, "the transcript must describe the code we shipped"
    assert "Sum = 8" not in lines


# --- ceilings ---------------------------------------------------------------
#
# Nothing bounded an attempt: deepagents defaults `recursion_limit` to 9,999 and
# `max_retries_per_task` bounds attempts rather than turns inside one. One
# attempt ran seven minutes unchecked, and the retry budget bought two more.


def _never_stops() -> list[AIMessage]:
    """One run_solution call, forever. ScriptedModel clamps on its last entry."""
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
        )
    ]


def test_an_attempt_that_will_not_stop_is_stopped(tmp_path):
    task, store = _workspace(tmp_path)
    settings = Settings(deepseek_api_key="", max_turns_per_attempt=3)
    model = ScriptedModel(script=_never_stops())

    result = solve_task(
        task, store, settings, _FakeShots(), RunUsage(), ev.Emitter(), {}, model=model
    )

    assert model.calls <= 3 * (2 * settings.max_turns_per_attempt + 2), "the cap bit"
    # It ran task1.py successfully many times, so the work is still harvested --
    # a task that produced a working program and then span has still produced one.
    assert result.outcome.code_text == SOURCE


def test_the_turn_cap_is_derived_from_the_setting():
    from labsagent.agent.build import solver_config

    config = solver_config(Settings(deepseek_api_key="", max_turns_per_attempt=10))
    assert config["recursion_limit"] == 22, "two graph steps per model turn, plus slack"


class _ExpensiveModel(ScriptedModel):
    """Reports a fortune in usage on every call, so a ceiling must fire."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        result = super()._generate(messages, stop, run_manager, **kwargs)
        result.generations[0].message.usage_metadata = {
            "input_tokens": 10_000_000,
            "output_tokens": 10_000_000,
            "total_tokens": 20_000_000,
            "input_token_details": {"cache_read": 0},
        }
        return result


def test_a_task_that_crosses_its_ceiling_is_not_retried(tmp_path):
    """Retrying pays the same money to hit the same wall."""
    task, store = _workspace(tmp_path)
    settings = Settings(
        deepseek_api_key="", max_cost_per_task_usd=0.01, max_cost_per_run_usd=100.0
    )
    model = _ExpensiveModel(script=_never_stops())

    result = solve_task(
        task, store, settings, _FakeShots(), RunUsage(), ev.Emitter(), {}, model=model
    )

    assert result.stopped_reason == "task"
    assert result.outcome.attempts == 1, "the retry budget was not spent on a wall"


def test_the_run_ceiling_reports_its_own_scope(tmp_path):
    task, store = _workspace(tmp_path)
    settings = Settings(
        deepseek_api_key="", max_cost_per_task_usd=100.0, max_cost_per_run_usd=0.01
    )
    model = _ExpensiveModel(script=_never_stops())

    result = solve_task(
        task, store, settings, _FakeShots(), RunUsage(), ev.Emitter(), {}, model=model
    )

    assert result.stopped_reason == "run"


def test_usage_is_counted_even_when_the_attempt_is_aborted(tmp_path):
    """The whole reason the counter is a callback: an abort returns no messages."""
    task, store = _workspace(tmp_path)
    usage = RunUsage()
    settings = Settings(
        deepseek_api_key="", max_cost_per_task_usd=0.01, max_cost_per_run_usd=100.0
    )
    solve_task(
        task,
        store,
        settings,
        _FakeShots(),
        usage,
        ev.Emitter(),
        {},
        model=_ExpensiveModel(script=_never_stops()),
    )

    assert usage.total.calls > 0
    assert usage.total.cost_usd > 0


def test_a_task_records_what_it_cost_and_why_it_retried(tmp_path):
    """Only run-level totals were persisted, so "which task was expensive"
    could not be answered from a finished run."""
    task, store = _workspace(tmp_path, cleanup__py=CHORE)
    usage = RunUsage()
    result = solve_task(
        task,
        store,
        Settings(deepseek_api_key=""),
        _FakeShots(),
        usage,
        ev.Emitter(),
        {},
        model=ScriptedModel(script=_chore_script()),
        explainer=None,
    )
    assert result.outcome.cost_usd == result.cost_usd
    assert set(result.outcome.usage) == {
        "calls",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
    }


def test_the_reason_a_task_retried_survives_it_passing(tmp_path):
    task, store = _workspace(tmp_path, solution=None)
    script = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "run_solution",
                    "args": {"entry_file": "task1.py", "stdin_values": []},
                    "id": "c1",
                }
            ],
        ),
        AIMessage(content="Done."),
    ]
    result = solve_task(
        task,
        store,
        Settings(deepseek_api_key=""),
        _FakeShots(),
        RunUsage(),
        ev.Emitter(),
        {},
        model=ScriptedModel(script=script),
    )
    assert result.outcome.status == "failed"
    assert result.outcome.attempt_errors, "every attempt's error is kept, in order"


# --- handing a task's output to the next task -------------------------------
#
# Previously only the prior task's CODE was injected, never its data products,
# so task 2, 4 and 5 each burned turns writing scripts to copy
# `../task2/retained_transactions.csv`. The same 40 MB CSV ended up on disk
# three times and the run directory came to 188 MB.


WRITES_A_CSV = (
    "rows = ['a,b', '1,2']\n"
    "open('features.csv', 'w').write('\\n'.join(rows) + '\\n')\n"
    "print('built features.csv')\n"
)


def test_a_tasks_data_output_is_recorded_and_kept(tmp_path):
    task, store = _workspace(tmp_path, solution=WRITES_A_CSV)
    result = _solve(store, task, _script())

    assert [p["name"] for p in result.outcome.produced] == ["features.csv"]
    assert (store.artifacts_dir / "task1" / "features.csv").exists()
    # Profiled like any other data file, so the next task is told its columns
    # rather than being left to open it and find out.
    assert "columns: a" in result.outcome.produced[0]["preview"]


def test_the_solvers_own_py_is_not_a_data_product(tmp_path):
    task, store = _workspace(tmp_path)
    result = _solve(store, task, _script())
    assert result.outcome.produced == []


def test_a_referenced_tasks_output_is_staged_and_named(tmp_path):
    """The next task finds the file already there, and is told what is in it."""
    from labsagent.data import stage_outputs
    from labsagent.orchestrator import build_task_prompt

    source = tmp_path / "features.csv"
    source.write_text("a,b\n1,2\n", encoding="utf-8")
    produced = [
        {"name": "features.csv", "path": str(source), "bytes": 8, "preview": "  features.csv -- 1 rows"}
    ]

    workspace = tmp_path / "task2"
    assert stage_outputs(produced, workspace) == ["features.csv"]
    assert (workspace / "features.csv").exists()

    prior = TaskOutcome(
        task=Task(id="task1", title="a", statement="b"),
        status="passed",
        code_text="x = 1",
        produced=produced,
    )
    prompt = build_task_prompt(
        Task(id="task2", title="t", statement="Extend your Task 1 program."),
        {"task1": prior},
    )
    assert "features.csv" in prompt
    assert "already in your working directory" in prompt


def test_staging_is_idempotent(tmp_path):
    from labsagent.data import stage_outputs

    source = tmp_path / "features.csv"
    source.write_text("a,b\n", encoding="utf-8")
    produced = [{"name": "features.csv", "path": str(source)}]
    workspace = tmp_path / "ws"

    stage_outputs(produced, workspace)
    stamp = (workspace / "features.csv").stat().st_mtime_ns
    stage_outputs(produced, workspace)
    assert (workspace / "features.csv").stat().st_mtime_ns == stamp, "not recopied"


def test_a_prompt_without_a_reference_names_no_handoff(tmp_path):
    from labsagent.orchestrator import build_task_prompt

    task = Task(id="task2", title="t", statement="Print hello.")
    prior = TaskOutcome(
        task=Task(id="task1", title="a", statement="b"),
        status="passed",
        produced=[{"name": "features.csv", "path": "x"}],
    )
    assert "features.csv" not in build_task_prompt(task, {"task1": prior})


def test_a_retry_starts_clean_and_is_told_why(tmp_path):
    """Attempt 3 once "passed" in 22 seconds off attempt 1's leftovers, and
    reported a different number than the attempt that wrote them."""
    task, store = _workspace(tmp_path, solution=None)
    workspace = store.workspace / task.id
    (workspace / "leftover.csv").write_text("stale", encoding="utf-8")

    prompts: list[str] = []

    class _Watching(ScriptedModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            prompts.append(str(messages[-1].content))
            return super()._generate(messages, stop, run_manager, **kwargs)

    script = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "run_solution",
                    "args": {"entry_file": "task1.py", "stdin_values": []},
                    "id": "c1",
                }
            ],
        ),
        AIMessage(content="Gave up."),
    ]
    solve_task(
        task,
        store,
        Settings(deepseek_api_key=""),
        _FakeShots(),
        RunUsage(),
        ev.Emitter(),
        {},
        model=_Watching(script=script),
    )

    assert not (workspace / "leftover.csv").exists(), "the retry did not inherit it"
    retries = [p for p in prompts if "previous attempt failed" in p]
    assert retries, "the retry is told why it is retrying"
    assert "workspace has been reset" in retries[0]


def test_the_first_attempt_is_not_told_about_a_failure(tmp_path):
    from labsagent.orchestrator import build_task_prompt

    task = Task(id="task1", title="t", statement="Print hello.")
    assert "previous attempt" not in build_task_prompt(task, {})
