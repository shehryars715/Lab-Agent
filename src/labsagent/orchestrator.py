"""The multi-task loop.

Four decisions are encoded here, each deliberate:

1. FAILURE POLICY: mark failed, continue. A partial report beats a crashed run.
   The student gets 4 of 5 tasks done and the fifth honestly marked, rather than
   nothing.

2. ISOLATION: one sandbox for the lab, but each task rooted at its own
   subdirectory. Task 3 cannot read or overwrite task 1's solution. This is
   isolation by confinement rather than by container -- cheaper than a sandbox
   per task (which on E2B means a container spin-up each time), and it reuses
   ConfinedBackend, which already has to be correct for security reasons.

3. RETRIES: bounded per task, and -- the part that matters -- SandboxError does
   NOT consume the budget. Infrastructure flakiness is not the agent's fault.
   Conflating the two either burns attempts on network blips or hides a
   genuinely broken solution behind "it retried".

4. REFERENCES: a task saying "extend your Task 2 program" gets Task 2's
   statement and final code injected. This deliberately breaks task independence
   -- it is the one place where fresh-context-per-task is the wrong default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.messages import AIMessage

from labsagent import events as ev
from labsagent.agent.build import build_solver
from labsagent.capture.base import ScreenshotBackend
from labsagent.config import Settings
from labsagent.errors import SandboxError
from labsagent.models import LabSpec, RunManifest, Task, TaskOutcome
from labsagent.runstore import RunStore, pending_tasks
from labsagent.sandbox.local import LocalSandbox
from labsagent.usage import RunUsage

TASK_REF = re.compile(r"\btask\s*(\d+)\b", re.IGNORECASE)


def referenced_task_ids(task: Task) -> set[str]:
    """Task ids this statement points at, excluding itself."""
    own = task.id.removeprefix("task")
    return {
        f"task{n}" for n in TASK_REF.findall(task.statement) if n != own
    }


def build_task_prompt(task: Task, done: dict[str, TaskOutcome]) -> str:
    """The user message for one task, plus any task it explicitly references."""
    parts = [task.statement]

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

    parts.append(f"\nWrite your solution to a file named {task.id}.py")
    return "\n".join(parts)


@dataclass
class SolveResult:
    outcome: TaskOutcome
    cost_usd: float


def solve_task(
    task: Task,
    store: RunStore,
    settings: Settings,
    screenshots: ScreenshotBackend,
    usage: RunUsage,
    emitter: ev.Emitter,
    done: dict[str, TaskOutcome],
    model=None,
) -> SolveResult:
    """One task, bounded attempts, isolated workspace."""
    workspace = store.workspace / task.id
    workspace.mkdir(parents=True, exist_ok=True)

    phase = usage.phase("solve")
    before = phase.cost_usd
    outcome = TaskOutcome(task=task, status="failed", error="not attempted")

    infra_retries = 0
    attempt = 0

    while attempt < settings.max_retries_per_task:
        attempt += 1
        emitter.emit(
            ev.AttemptStarted(
                task_id=task.id, attempt=attempt, max_attempts=settings.max_retries_per_task
            )
        )

        # A fresh sandbox handle and agent per attempt: the agent's context is
        # reset, so its workspace view should be too.
        with LocalSandbox(workdir=workspace, keep=True) as sandbox:
            agent, recorder = build_solver(sandbox, settings, model=model)
            try:
                result = agent.invoke(
                    {"messages": [{"role": "user", "content": build_task_prompt(task, done)}]}
                )
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
                continue

            for message in result.get("messages", []):
                if isinstance(message, AIMessage):
                    phase.add_message(message)

            # Trust execution, not the agent's claim -- including for the
            # FILENAME. `last_entry_file` is the path run_solution actually ran,
            # so it is known to exist; `entry_file` is only what the agent says
            # it ran, and an agent that wrote to "workspace/task3.py" will still
            # cheerfully report "task3.py".
            if recorder.last_ok and recorder.last_transcript:
              try:
                entry = recorder.last_entry_file or recorder.entry_file or f"{task.id}.py"
                code_text = sandbox.read_file(entry)

                code_path = store.code_dir / f"{task.id}.py"
                code_path.write_text(code_text, encoding="utf-8")
                emitter.emit(
                    ev.ArtifactWritten(task_id=task.id, artifact="code", path=str(code_path))
                )

                # Figures the program drew are a separate artifact class from the
                # terminal screenshot: both belong in the report.
                figure_paths = []
                for name in recorder.last_figures:
                    dest = store.shots_dir / f"{task.id}_{Path(name).name}"
                    dest.write_bytes((Path(sandbox.workdir) / name).read_bytes())
                    figure_paths.append(dest)
                    emitter.emit(
                        ev.ArtifactWritten(
                            task_id=task.id, artifact="figure", path=str(dest)
                        )
                    )

                shots = screenshots.render(
                    recorder.last_transcript, store.shots_dir / f"{task.id}_output.png"
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
                    transcript=recorder.last_transcript,
                    explanation=recorder.notes or None,
                    attempts=attempt,
                    error=None,
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
                continue

            error = (recorder.last_stderr or "produced no output").strip()
            emitter.emit(
                ev.AttemptFailed(task_id=task.id, attempt=attempt, error=error[:300])
            )
            outcome.error = error[:400]

    outcome.attempts = attempt
    cost = phase.cost_usd - before
    emitter.emit(
        ev.TaskFinished(
            task_id=task.id, status=outcome.status, attempts=attempt, cost_usd=cost
        )
    )
    return SolveResult(outcome=outcome, cost_usd=cost)


def run_lab(
    spec: LabSpec,
    store: RunStore,
    settings: Settings,
    screenshots: ScreenshotBackend,
    emitter: ev.Emitter | None = None,
    usage: RunUsage | None = None,
    model=None,
    resume: bool = False,
) -> RunManifest:
    """Solve every task. Failures are recorded, never fatal."""
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
            task, store, settings, screenshots, usage, emitter, done, model=model
        )
        manifest.outcomes.append(result.outcome)
        done[task.id] = result.outcome

        # Persist after EVERY task: a crash on task 4 must not lose tasks 1-3.
        manifest.token_usage = usage.total.as_dict()
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
