"""The multi-task loop.

Four decisions are encoded here, each deliberate:

1. FAILURE POLICY: mark failed, continue. A partial report beats a crashed run.
   The student gets 4 of 5 tasks done and the fifth honestly marked, rather than
   nothing.

2. ISOLATION: one sandbox for the lab, but each task rooted at its own
   subdirectory. This is isolation by confinement rather than by container --
   cheaper than a sandbox per task (which on E2B means a container spin-up each
   time), and it reuses ConfinedBackend, which already has to be correct for
   security reasons.

   BUT IT BINDS THE AGENT'S TOOLS, NOT THE CODE THEY RUN. `run_solution` is a
   plain subprocess with the task directory as its cwd and nothing else; a
   program the model writes can read anywhere the process can. A task once
   reached into `../task2/` to copy a file it had not been given. So the honest
   claim is that task 3 cannot ACCIDENTALLY touch task 1's solution, not that it
   is prevented from doing so -- that guarantee waits on the E2B backend. What
   this module can do is remove the motive, which is why a task's data products
   are handed forward explicitly rather than left to be found.

3. RETRIES: bounded per task, and -- the part that matters -- SandboxError does
   NOT consume the budget. Infrastructure flakiness is not the agent's fault.
   Conflating the two either burns attempts on network blips or hides a
   genuinely broken solution behind "it retried".

4. REFERENCES: a task saying "extend your Task 2 program" gets Task 2's
   statement and final code injected. This deliberately breaks task independence
   -- it is the one place where fresh-context-per-task is the wrong default.

5. DESCRIBING IS NOT DOING. The report prose is written by a separate call that
   never sees the debugging -- see agent/explainer.py. It is injected as a
   callable rather than built here, so the CLI, the tests and the web layer each
   decide independently whether to pay for it. With no explainer wired in, the
   solver's own notes remain the fallback and nothing changes.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from langgraph.errors import GraphRecursionError

from labsagent import events as ev
from labsagent.agent.build import build_solver, solver_config
from labsagent.agent.tools import RunRecord
from labsagent.budget import BudgetExceeded, LiveUsage
from labsagent.agent.explainer import Explainer
from labsagent.capture.base import ScreenshotBackend
from labsagent.data import (
    DATA_SUFFIXES,
    describe as describe_datasets,
    describe_produced,
    materialize,
    stage_outputs,
)
from labsagent.config import Settings
from labsagent.errors import SandboxError
from labsagent.models import LabSpec, RunManifest, Task, TaskOutcome
from labsagent.runner import run_solution as _run_file
from labsagent.runstore import RunStore, pending_tasks
from labsagent.sandbox.local import LocalSandbox
from labsagent.usage import RunUsage

# Moved to `intent.py`, which needs the same scan to work out what a scoped
# request secretly depends on. Re-exported here so existing importers -- and
# anyone reading this module top to bottom -- still find it.
from labsagent.intent import TASK_REF, referenced_task_ids  # noqa: F401


def normalize_entry(entry: str) -> str:
    """A run's entry file reduced to a bare, comparable filename.

    The agent may pass "task3.py", "/task3.py", "./workspace/Task3.PY" or a
    Windows-flavoured path for the same file, because its filesystem view is a
    virtual root. Comparing raw strings therefore answers the wrong question.
    """
    name = PurePosixPath(str(entry or "").replace("\\", "/")).name
    return name.lower()


def choose_run(
    runs,
    task_id: str,
    declared: str | None = None,
    known_ids=(),
) -> RunRecord | None:
    """The run that was actually this task's solution, or None.

    THIS FUNCTION IS THE FIX FOR A REAL FAILURE. It used to be
    `recorder.last_entry_file` -- whatever ran most recently -- and the comment
    below this one has recorded two instances of that going wrong. A third:
    an agent solved task 3 correctly, ran its solution, then ran a six-line
    `cleanup.py` to tidy a scratch file. The chore exited 0 with output, so it
    became the shipped code, the screenshot and the explained result, and the
    task was marked passed.

    So the question is not "what ran last" but "which run was the task". Only
    successful runs are eligible, and the ranks below are tried in order, each
    taking its LAST match:

      1. the file the task asked for -- the prompt says "write your solution to
         a file named task3.py" and `write_file` is the only way it could exist;
      2. the file the agent declared in record_task_result, which is a claim,
         so it is a tiebreaker and never a source;
      3. a near-miss on the task's own name, e.g. task3_v2.py;

    and NOTHING ELSE. There is deliberately no "or whatever ran" fallback: that
    fallback is the defect. A task with no eligible run is a task that failed,
    and the caller has a token-free rescue (`verify_entry`) before it says so.
    """
    others = {f"{other}.py" for other in known_ids if other != task_id}
    eligible = [r for r in runs if r.ok and r.transcript]
    wanted = f"{task_id}.py"

    def pick(predicate):
        matches = [r for r in eligible if predicate(normalize_entry(r.entry_file))]
        return matches[-1] if matches else None

    chosen = pick(lambda name: name == wanted)
    if chosen is not None:
        return chosen

    if declared:
        target = normalize_entry(declared)
        if target not in others:
            chosen = pick(lambda name: name == target)
            if chosen is not None:
                return chosen

    return pick(
        lambda name: name not in others
        and name.endswith(".py")
        and PurePosixPath(name).stem.startswith(task_id)
    )


def _is_stale(sandbox, record: RunRecord) -> bool:
    """True when the file has changed since the run we are about to ship.

    Unknown hashes mean "assume fine": the check exists to catch a real edit,
    not to force a re-run every time a hash could not be taken.
    """
    if not record.source_sha:
        return False
    import hashlib

    try:
        current = hashlib.sha256(
            sandbox.read_file(record.entry_file).encode("utf-8")
        ).hexdigest()
    except Exception:  # noqa: BLE001
        return False
    return current != record.source_sha


def verify_entry(sandbox, task: Task, settings: Settings) -> RunRecord | None:
    """Run `<task>.py` ourselves and record the result. Costs no model tokens.

    Two situations need this, and both are cheaper to fix with one subprocess
    than with another paid attempt:

      - the agent only ever ran a probe, but the solution file is sitting in
        the workspace exactly as instructed;
      - the agent EDITED the file after the run we picked, so that run's
        transcript no longer describes the code we are about to ship.

    Returns None if the file is missing or does not run cleanly, which puts the
    task back on the honest-failure path.
    """
    entry = f"{task.id}.py"
    try:
        sandbox.read_file(entry)
    except Exception:  # noqa: BLE001 -- missing file is just "no candidate"
        return None
    try:
        outcome = _run_file(
            sandbox, entry, list(task.sample_inputs or []), settings.timeout_s
        )
    except Exception:  # noqa: BLE001 -- a rescue must never raise into the loop
        return None
    if not outcome.ok:
        return None
    return RunRecord(
        entry_file=entry,
        ok=True,
        transcript=outcome.transcript,
        stdout=outcome.result.stdout,
        stderr=outcome.result.stderr,
        figures=list(outcome.figures),
        source_sha=None,
    )


def build_task_prompt(
    task: Task,
    done: dict[str, TaskOutcome],
    datasets=(),
    data_failures=(),
    previous_error: str | None = None,
) -> str:
    """The user message for one task, plus any task it explicitly references."""
    parts = [task.statement]

    # Solver-only steering. It rides on the Task so a resume re-asks the same
    # question, but it is deliberately NOT part of `statement`, which is what
    # the exporters print into the file you hand in.
    if task.instruction:
        parts.append(task.instruction)

    if task.sample_inputs:
        parts.append(f"\nTest it with these inputs, in order: {task.sample_inputs}")

    for ref_id in sorted(referenced_task_ids(task)):
        prior = done.get(ref_id)
        if not prior or not prior.code_text:
            continue
        parts.append(
            f"\nThis task refers to {ref_id}, which you already solved.\n"
            f"Its requirement was: {prior.task.statement}\n"
            f"Your solution was:\n```python\n{prior.code_text}```"
        )

    # AFTER everything describing the work, BEFORE the filename line. PLAN.md
    # records the ordering rule this follows: recency is the cheapest ranking,
    # so the structural invariant -- "write it to task3.py" -- is stated last
    # and narrowly. The data block is context, not the instruction.
    handoff = describe_produced(
        [item for ref_id in sorted(referenced_task_ids(task))
         for item in (done[ref_id].produced if ref_id in done else [])]
    )
    if handoff:
        parts.append(handoff)

    data_block = describe_datasets(datasets, data_failures)
    if data_block:
        parts.append(data_block)

    # LAST, BESIDE THE FILENAME. A retry that is not told why it is retrying
    # rediscovers the same dead end, and the workspace it is about to look at
    # has been emptied, which is surprising unless it is said.
    if previous_error:
        parts.append(
            f"\nYour previous attempt failed with: {previous_error[:300]}\n"
            "The workspace has been reset, so start from a clean file."
        )

    parts.append(f"\nWrite your solution to a file named {task.id}.py")
    return "\n".join(parts)


@dataclass
class SolveResult:
    outcome: TaskOutcome
    cost_usd: float
    #: "task" | "run" when a ceiling ended this task, else None. The run scope
    #: is what `run_lab` reads to stop starting tasks it cannot pay for.
    stopped_reason: str | None = None


def _produced_by(
    workspace: Path, before: set[str], task_id: str, artifacts_dir: Path, cap_bytes: int
) -> list[dict]:
    """The data files this task made, copied somewhere the next task can read.

    Deliberately narrow. Only data suffixes, so the task's own .py is not
    mistaken for a result; not the run harness's own scratch files, which are
    written beside the solution and one of which is a .txt; and only files that
    were not already here, so a staged dataset is not re-announced as an output
    of the task that read it.
    """
    from labsagent.data.preview import profile
    from labsagent.runner import INPUTS_NAME, SHIM_NAME

    ours = {INPUTS_NAME, SHIM_NAME}
    made: list[dict] = []
    destination = artifacts_dir / task_id
    for path in sorted(workspace.iterdir()):
        if not path.is_file() or path.name in before or path.name in ours:
            continue
        if path.suffix.lower() not in DATA_SUFFIXES:
            continue
        try:
            size = path.stat().st_size
            if size > cap_bytes:
                continue
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / path.name
            shutil.copy2(path, target)
        except OSError:
            # A file we cannot copy forward is one the next task will not be
            # told about. It is not a reason to fail a task that passed.
            continue
        made.append(
            {
                "name": path.name,
                "path": target.as_posix(),
                "bytes": size,
                "preview": profile(target),
            }
        )
    return made


def solve_task(
    task: Task,
    store: RunStore,
    settings: Settings,
    screenshots: ScreenshotBackend,
    usage: RunUsage,
    emitter: ev.Emitter,
    done: dict[str, TaskOutcome],
    model=None,
    explainer: Explainer | None = None,
    datasets=(),
    data_failures=(),
) -> SolveResult:
    """One task, bounded attempts, isolated workspace."""
    workspace = store.workspace / task.id
    workspace.mkdir(parents=True, exist_ok=True)

    # The data lands BEFORE the agent is built, so the file is simply there
    # when the first tool call happens. Two reasons it is not a tool the agent
    # calls: a download inside the attempt loop runs up to `max_retries_per_task`
    # times, and the sandbox inherits the full environment, so a Kaggle key
    # placed there is readable by model-written code.
    #
    # Copied once per task, not per attempt: `materialize` skips a file already
    # present at the same size, so three attempts do not recopy a 90 MB CSV.
    try:
        staged = materialize(datasets, workspace)
    except Exception as exc:  # noqa: BLE001 -- data is an input, not the run
        staged = []
        emitter.emit(ev.AttemptFailed(task_id=task.id, attempt=0, error=f"data: {exc}"[:300]))

    # WHAT EARLIER TASKS BUILT, PUT HERE RATHER THAN HUNTED FOR. Only the tasks
    # this one references: staging everything would copy a 40 MB intermediate
    # into five workspaces to serve one of them. Without this, later tasks wrote
    # their own scripts to go and copy `../task2/features.csv` -- paid-for turns,
    # and a reach outside the workspace that isolation was meant to prevent.
    inherited = [
        item
        for ref_id in sorted(referenced_task_ids(task))
        if ref_id in done
        for item in done[ref_id].produced
    ]
    staged += stage_outputs(inherited, workspace)

    # Everything present before the agent starts. Anything data-shaped that is
    # here afterwards is something this task made -- see `_produced_by`.
    started_with = {p.name for p in workspace.iterdir() if p.is_file()}

    phase = usage.phase("solve")
    # Measured on the TOTAL, not on the solve phase alone. The explain call
    # below bills to its own phase, so a solve-only delta would quietly
    # under-report what this task cost -- and this number is what the web UI
    # shows per task.
    before = usage.total.cost_usd
    # The whole snapshot, not just the money: "this task cost $0.04" is the
    # headline, and "because it sent 900k tokens" is the answer to the next
    # question. Both used to be lost at the end of the run.
    before_usage = replace(usage.total)
    outcome = TaskOutcome(task=task, status="failed", error="not attempted")
    attempt_errors: list[str] = []

    infra_retries = 0
    attempt = 0
    budget_stop: str | None = None

    while attempt < settings.max_retries_per_task:
        attempt += 1
        emitter.emit(
            ev.AttemptStarted(
                task_id=task.id, attempt=attempt, max_attempts=settings.max_retries_per_task
            )
        )

        # A RETRY STARTS FROM A CLEAN DIRECTORY, AND IS TOLD WHY. The two go
        # together: wiping alone makes the retry honest but no better informed,
        # and the note alone leaves it tripping over the last attempt's
        # half-written files. Datasets and handed-forward files are kept, so
        # nothing is re-fetched or recopied.
        if attempt > 1 and settings.wipe_workspace_between_attempts:
            RunStore.reset_workspace(workspace, keep=set(staged))
            try:
                materialize(datasets, workspace)
                stage_outputs(inherited, workspace)
            except Exception as exc:  # noqa: BLE001 -- data is an input, not the run
                emitter.emit(
                    ev.AttemptFailed(
                        task_id=task.id, attempt=attempt, error=f"data: {exc}"[:300]
                    )
                )
            started_with = {f.name for f in workspace.iterdir() if f.is_file()}

        # A fresh sandbox handle and agent per attempt: the agent's context is
        # reset, so its workspace view should be too.
        with LocalSandbox(workdir=workspace, keep=True) as sandbox:
            result = None
            agent, recorder = build_solver(sandbox, settings, model=model)
            # Usage is accumulated BY THIS, as it is billed, rather than summed
            # from the returned messages afterwards -- an aborted attempt never
            # returns any, and it is the aborted attempt whose cost matters.
            live = LiveUsage(
                phase,
                run_usage=usage,
                task_cap=settings.max_cost_per_task_usd,
                run_cap=settings.max_cost_per_run_usd,
            )
            try:
                result = agent.invoke(
                    {
                        "messages": [
                            {
                                "role": "user",
                                "content": build_task_prompt(
                                    task,
                                    done,
                                    datasets,
                                    data_failures,
                                    attempt_errors[-1] if attempt_errors else None,
                                ),
                            }
                        ]
                    },
                    config=solver_config(settings, callbacks=[live]),
                )
            except GraphRecursionError:
                # The attempt would not stop on its own. It still gets its
                # artifacts collected below: a task that produced a working
                # program and then kept fiddling has still produced one.
                stopped = f"stopped: turn limit ({settings.max_turns_per_attempt} turns) reached"
                emitter.emit(
                    ev.AttemptFailed(task_id=task.id, attempt=attempt, error=stopped)
                )
                outcome.error = stopped
                attempt_errors.append(stopped)
            except BudgetExceeded as exc:
                # A task that hit its own ceiling must not be retried: another
                # attempt pays the same money to reach the same wall. A run that
                # hit the run ceiling stops everything.
                emitter.emit(
                    ev.AttemptFailed(task_id=task.id, attempt=attempt, error=str(exc))
                )
                outcome.error = str(exc)
                attempt_errors.append(str(exc))
                budget_stop = exc.scope
            except SandboxError as exc:
                # Infrastructure, not the agent. Does not consume the budget.
                infra_retries += 1
                attempt -= 1
                emitter.emit(
                    ev.AttemptFailed(task_id=task.id, attempt=attempt, error=f"infra: {exc}")
                )
                if infra_retries > 3:
                    outcome.error = f"sandbox kept failing: {exc}"
                    break
                continue
            except Exception as exc:  # noqa: BLE001
                emitter.emit(
                    ev.AttemptFailed(task_id=task.id, attempt=attempt, error=str(exc))
                )
                outcome.error = str(exc)[:400]
                attempt_errors.append(str(exc)[:400])
                continue

            # TRUST EXECUTION, NOT THE AGENT'S CLAIM -- including for the
            # FILENAME. `choose_run` picks the run that was actually this
            # task's solution; see its docstring for why "whatever ran last"
            # was wrong and what it cost. The agent's declared `entry_file` is
            # a tiebreaker there, never a source.
            #
            # When no run qualifies, `verify_entry` runs `<task>.py` ourselves
            # before giving up -- one subprocess, no model tokens -- which
            # rescues the agent that solved the task and then only ran chores.
            #
            # AND THE AGENT MUST NOT HAVE DECLARED DEFEAT. Tested as
            # `!= "failed"` rather than `== "passed"` on purpose: an agent that
            # simply forgets its final record_task_result call has not lied
            # about anything, and demoting those to failures would trade a rare
            # false pass for a common false fail.
            chosen = choose_run(
                recorder.runs,
                task.id,
                recorder.entry_file,
                known_ids=done.keys(),
            )
            # A transcript that no longer describes the file we are about to
            # ship is worse than no transcript: the report would show output
            # the code cannot produce.
            if chosen is not None and _is_stale(sandbox, chosen):
                chosen = verify_entry(sandbox, task, settings)
            if chosen is None:
                chosen = verify_entry(sandbox, task, settings)

            if chosen is not None and recorder.status != "failed":
              try:
                code_text = sandbox.read_file(chosen.entry_file)

                code_path = store.code_dir / f"{task.id}.py"
                code_path.write_text(code_text, encoding="utf-8")
                emitter.emit(
                    ev.ArtifactWritten(task_id=task.id, artifact="code", path=str(code_path))
                )

                # Figures the program drew are a separate artifact class from the
                # terminal screenshot: both belong in the report.
                figure_paths = []
                for name in recorder.figures_for(chosen.entry_file):
                    dest = store.shots_dir / f"{task.id}_{Path(name).name}"
                    try:
                        dest.write_bytes((Path(sandbox.workdir) / name).read_bytes())
                    except OSError as exc:
                        # One unreadable figure is a missing decoration, not a
                        # failed task. Before this guard it fell through to the
                        # outer handler and demoted a task that had passed.
                        emitter.emit(
                            ev.AttemptFailed(
                                task_id=task.id, attempt=attempt, error=f"figure: {exc}"[:300]
                            )
                        )
                        continue
                    figure_paths.append(dest)
                    emitter.emit(
                        ev.ArtifactWritten(
                            task_id=task.id, artifact="figure", path=str(dest)
                        )
                    )

                shots = screenshots.render(
                    chosen.transcript, store.shots_dir / f"{task.id}_output.png"
                )
                for shot in shots:
                    emitter.emit(
                        ev.ArtifactWritten(
                            task_id=task.id, artifact="screenshot", path=str(shot)
                        )
                    )

                outcome = TaskOutcome(
                    task=task,
                    status="passed",
                    code_path=code_path,
                    code_text=code_text,
                    screenshot_paths=list(shots),
                    figure_paths=figure_paths,
                    transcript=chosen.transcript,
                    explanation=recorder.notes or None,
                    attempts=attempt,
                    error=None,
                    produced=_produced_by(
                        Path(sandbox.workdir),
                        started_with,
                        task.id,
                        store.artifacts_dir,
                        settings.handoff_max_mb * 1024 * 1024,
                    ),
                )
                break
              except (OSError, ValueError) as exc:
                # Collecting artifacts must never kill the RUN. Without this the
                # failure policy is a lie: one task whose file has moved takes
                # the other four down with it, and the report never gets built.
                emitter.emit(
                    ev.AttemptFailed(
                        task_id=task.id, attempt=attempt, error=f"artifact: {exc}"
                    )
                )
                outcome.error = f"could not collect artifacts: {exc}"[:400]
                attempt_errors.append(f"could not collect artifacts: {exc}"[:400])
                continue

            # A CEILING IS NOT A FLAKE, SO DO NOT RETRY IT. Another attempt
            # spends the same money to reach the same wall. This sits after
            # artifact collection on purpose: a task that produced a working
            # program and then kept spending has still produced one, and that
            # is worth keeping.
            if budget_stop:
                break

            # SAY WHICH FAILURE THIS WAS. "produced no output" is right when
            # nothing ran; it is misleading when plenty ran and none of it was
            # the task -- which is the case this loop now refuses to ship.
            if recorder.runs and not any(r.ok for r in recorder.runs):
                error = (recorder.last_stderr or "produced no output").strip()
            elif recorder.runs:
                error = (
                    f"no successful run of {task.id}.py "
                    f"(ran: {', '.join(sorted({r.entry_file for r in recorder.runs}))})"
                )
            else:
                error = (recorder.last_stderr or "produced no output").strip()
            emitter.emit(
                ev.AttemptFailed(task_id=task.id, attempt=attempt, error=error[:300])
            )
            # A LATER, EMPTIER ATTEMPT MUST NOT ERASE A BETTER DIAGNOSIS. An
            # attempt that made no tool calls at all reports "produced no
            # output", which is true and useless; if an earlier attempt already
            # said which files ran and that none of them was the task, that is
            # the sentence worth keeping.
            attempt_errors.append(error[:400])
            generic = error.startswith("produced no output")
            if not generic or not outcome.error or outcome.error == "not attempted":
                outcome.error = error[:400]

    # AFTER the loop, not inside it: an explanation is written once, about the
    # program that finally worked, and a task that needed three attempts should
    # not pay for three explanations of code that was thrown away.
    if outcome.status == "passed" and explainer is not None:
        # Guarded for the same reason artifact collection is: this task has
        # already succeeded, and a decoration must never be able to undo that.
        # `Explainer` swallows its own model errors, but `explainer` is any
        # callable as far as this function knows, so the guarantee belongs here
        # rather than in one particular implementation of the seam.
        try:
            written = explainer(outcome.task, outcome.code_text, outcome.transcript)
        except Exception as exc:  # noqa: BLE001
            emitter.emit(
                ev.AttemptFailed(
                    task_id=task.id, attempt=attempt, error=f"explain: {exc}"[:300]
                )
            )
            written = None
        if written:
            outcome.explanation = written

    outcome.attempts = attempt
    cost = usage.total.cost_usd - before
    after = usage.total
    outcome.cost_usd = cost
    outcome.usage = {
        "calls": after.calls - before_usage.calls,
        "input_tokens": after.input_tokens - before_usage.input_tokens,
        "output_tokens": after.output_tokens - before_usage.output_tokens,
        "cached_tokens": after.cached_tokens - before_usage.cached_tokens,
    }
    outcome.attempt_errors = attempt_errors
    outcome.stopped_reason = budget_stop or outcome.stopped_reason
    emitter.emit(
        ev.TaskFinished(
            task_id=task.id, status=outcome.status, attempts=attempt, cost_usd=cost
        )
    )
    return SolveResult(outcome=outcome, cost_usd=cost, stopped_reason=budget_stop)


def run_lab(
    spec: LabSpec,
    store: RunStore,
    settings: Settings,
    screenshots: ScreenshotBackend,
    emitter: ev.Emitter | None = None,
    usage: RunUsage | None = None,
    model=None,
    resume: bool = False,
    explainer: Explainer | None = None,
    datasets=(),
    data_failures=(),
) -> RunManifest:
    """Solve every task. Failures are recorded, never fatal.

    `datasets` are already-resolved local files (see `labsagent.data`). They are
    copied into every task's workspace and described in every task's prompt --
    not filtered per task, because working out which of five tasks needs the CSV
    is a judgement the manual rarely states and a wrong answer is a task that
    cannot run. The cost of being generous is one file copy.
    """
    emitter = emitter or ev.Emitter()
    usage = usage or RunUsage(model=settings.model_name)

    if resume and store.manifest_path.exists():
        manifest = store.load()
        todo = pending_tasks(manifest)
    else:
        manifest = RunManifest(
            run_id=store.run_id, started_at=datetime.now(timezone.utc), spec=spec
        )
        todo = list(spec.tasks)

    # Recorded on the manifest, so the run says what it was run against and a
    # revision can reuse the files instead of fetching them again.
    manifest.datasets = [d.as_dict() for d in datasets]

    emitter.emit(
        ev.RunStarted(
            run_id=store.run_id, lab_number=spec.lab_number, task_count=len(spec.tasks)
        )
    )

    done = {o.task.id: o for o in manifest.outcomes}

    for position, task in enumerate(todo, start=1):
        emitter.emit(
            ev.TaskStarted(
                task_id=task.id, title=task.title, index=position, total=len(todo)
            )
        )
        result = solve_task(
            task,
            store,
            settings,
            screenshots,
            usage,
            emitter,
            done,
            model=model,
            explainer=explainer,
            datasets=datasets,
            data_failures=data_failures,
        )
        manifest.outcomes.append(result.outcome)
        done[task.id] = result.outcome

        # THE RUN CEILING STOPS THE RUN, not just the task that crossed it.
        # Everything solved so far is kept and reported -- the failure policy
        # at the top of this module applies here too: a partial report beats a
        # run that silently kept spending.
        if result.stopped_reason == "run":
            for remaining in todo[position:]:
                manifest.outcomes.append(
                    TaskOutcome(
                        task=remaining,
                        status="failed",
                        error="run budget exhausted before this task was started",
                    )
                )
            manifest.token_usage = usage.total.as_dict()
            manifest.usage = usage.as_dict()
            manifest.cost_usd = usage.total.cost_usd
            store.save(manifest)
            break

        # Persist after EVERY task: a crash on task 4 must not lose tasks 1-3.
        manifest.token_usage = usage.total.as_dict()
        # The per-phase split as well as the total: collapsing to the total threw
        # away the solve/explain/ingest breakdown, which is the answer to "where
        # did the money go".
        manifest.usage = usage.as_dict()
        manifest.cost_usd = usage.total.cost_usd
        store.save(manifest)

    passed = sum(1 for o in manifest.outcomes if o.status == "passed")
    emitter.emit(
        ev.RunFinished(
            run_id=store.run_id,
            passed=passed,
            failed=len(manifest.outcomes) - passed,
            cost_usd=usage.total.cost_usd,
        )
    )
    return manifest
