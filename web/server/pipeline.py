"""The pipeline, composed. Manual in, artifacts out, with a pause in the middle.

NOTHING IN `labsagent` IS MODIFIED, SUBCLASSED, OR MONKEY-PATCHED. Every step
below is a call into the package's public API. That is not a stylistic
preference -- it is what the core was built for. `events.py` says so directly:

    "The core must not know whether it is being watched by a terminal, a web
     socket, a log file, or nothing. Today the CLI is the only consumer; in
     Phase 7 a browser becomes a second one."

So this module is that second consumer, and the seam it plugs into was cut
before it existed. Compare with `examples/solve_lab.py`: the sequence of calls
is nearly identical, and the differences are all additions (a pause, an event
sink, extra instructions) rather than edits.

THE ONE PLACE THE ORDER CHANGES, AND WHY. `solve_lab.py` resolves the student
profile BEFORE anything else -- "ask before spending money, not after". Here it
runs after solving and before annotating, which is what the brief asked for:
ask partway through, once the work is done. The cost of getting it wrong is
bounded and small (a lab is ~$0.0016, and by that point it is already spent),
and the benefit is real: by the time we ask, the tasks have been extracted, so
the dialog can say "Found 3 tasks" instead of asking blind.
"""

from __future__ import annotations

import zipfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from labsagent import events as ev
from labsagent.agent.build import build_model
from labsagent.capture.rendered import RenderedBackend
from labsagent.config import PROJECT_ROOT, Settings
from labsagent.ingest.cover import extract_cover_facts
from labsagent.ingest.docx_reader import read_manual
from labsagent.ingest.labspec import extract_labspec
from labsagent.models import LabSpec, TaskOutcome
from labsagent.orchestrator import run_lab
from labsagent.package.notebook import write_notebook
from labsagent.package.zipper import build_submission
from labsagent.profile import ASK_ORDER, StudentProfile
from labsagent.report.cover import cover_from
from labsagent.report.docx_builder import annotate_manual
from labsagent.runstore import RunStore
from labsagent.usage import RunUsage

from .briefing import read_briefing
from .jobs import Job
from .revise import read_revision
from .trace import tracing_tools

# How long the browser gets to answer before the run continues without it.
# Ten minutes is chosen against the alternative failure: a tab closed by
# accident should not hold a worker thread until the process exits.
ASK_TIMEOUT_S = 600.0

RUNS_ROOT = PROJECT_ROOT / "runs"
UPLOADS_ROOT = RUNS_ROOT / "_web_uploads"


# Steering the agent to narrate, without touching `agent/prompts.py`.
#
# WHY THIS IS HERE AND NOT IN THE SYSTEM PROMPT. `prompts.py` documents
# `SOLVER_PROMPT` as byte-stable on purpose: it is resent on every turn of
# every task, so a changed prefix converts cache HITS into cache MISSES at 50x
# the price. Editing it would also change the behaviour of the CLI and the
# tests, which have no chat to narrate into. The task statement is per-task and
# already uncached, so a web-only instruction belongs here.
#
# Kept terse and bounded on purpose. Every narrated sentence is output tokens
# billed at 2x the cache-miss input rate, and it accumulates in the agent's own
# context for the rest of that task.
NARRATION = (
    "\n\nAs you work: before your first tool call, say in ONE short sentence what you "
    "are about to do and why. After a failed run, say in one short sentence what went "
    "wrong. Do not narrate every step, do not restate the task, and do not summarise at "
    "the end -- a separate step does that."
)


def apply_instructions(spec: LabSpec, instructions: str) -> LabSpec:
    """Fold narration and the student's extra instructions into every task.

    WHY `replace` AND NOT MUTATION. `Task` and `LabSpec` are frozen
    dataclasses -- the codebase's way of saying "a parsed spec is a fact, not a
    scratchpad". Mutating `spec.tasks[0].statement` would work at runtime and
    is still the wrong move, because `runstore.py` serialises this spec into
    the manifest as the record of the run. A record that can be edited in place
    is not a record.

    WHY IT MUST GO IN THE MANIFEST. `pending_tasks()` reads
    `manifest.spec.tasks` to decide what a resume still has to do. If the
    augmented text were injected later -- at prompt-build time, say -- a
    resumed run would ask a *different question* than the original and quietly
    produce a mixed result. Putting it in the spec we hand to `run_lab` means
    the question is frozen at the moment it is first asked.
    """
    extra = (instructions or "").strip()
    note = NARRATION
    if extra:
        note += (
            "\n\nAdditional instructions from the student. Follow these; where they "
            f"conflict with the task text above, these win:\n{extra}"
        )
    return replace(spec, tasks=[replace(t, statement=t.statement + note) for t in spec.tasks])


def wire_event(event: ev.Event) -> dict[str, Any]:
    """An event dataclass -> JSON-safe dict for the browser.

    Two transformations, both deliberate.

    JSON SAFETY. `dataclasses.asdict` is the convenient call and it chokes here
    for the same reason it chokes in `runstore.py`: `at` is a `datetime`, which
    is not a JSON type. Every persistence or transport layer has a boundary
    where rich in-memory types become dumb portable ones -- this is that
    boundary for the wire, and it is worth being able to point at.

    REDACTION. `ArtifactWritten.path` is an absolute path on this machine. The
    browser has no use for it -- downloads go through the artifact whitelist,
    which is keyed by name -- and sending it publishes the server's directory
    layout to anyone who opens devtools. An internal event is not the same
    thing as a wire format, and this is where that distinction gets enforced.
    """
    payload = {k: v for k, v in asdict(event).items() if k != "at"}
    payload.pop("path", None)
    payload["kind"] = event.kind
    payload["at"] = event.at.isoformat()
    return payload


def profile_from(values: dict[str, str]) -> StudentProfile:
    """Build the identity from what the browser sent. Nothing else.

    THE BROWSER IS THE SOURCE OF TRUTH, AND THE SERVER IS STATELESS ABOUT IT.

    The obvious implementation is `resolve_profile()`, which is the CLI's
    mechanism: it reads `labsagent.toml`, asks about anything missing, and
    writes the answers back. Using it here looked like free reuse -- and it did
    work, until a browser form silently rewrote the author's own name, cached
    from the CLI, because that is exactly what "asked once, remembered" does.

    So this does not call it. A web form is a less trusted input than a
    terminal on the owner's machine, and the two should not share a config file
    by accident. `PLAN.md` §7 already reached this conclusion -- identity in
    browser `localStorage` -- and it is the right one.

    The trade is real and worth naming: the CLI and the web can now disagree
    about who you are. That is the price of neither being able to overwrite the
    other, and it is the cheaper failure of the two.

    `resolve_profile` is untouched and remains the CLI's path. This is a second
    implementation of a different policy, not a replacement.
    """
    return StudentProfile(
        name=(values.get("name") or "").strip(),
        cms_id=(values.get("cms_id") or "").strip(),
        section=(values.get("section") or "").strip(),
        program=(values.get("program") or "").strip(),
    )


def build_questions(
    seed: dict[str, str], facts: Any, proposed: list[dict[str, Any]]
) -> tuple[list[dict], list[dict]]:
    """What to ask, and what we already know.

    Two sources, one list. The identity fields are asked only when nothing
    already supplies them -- the browser usually has them from last time, and
    the manual usually has the section. The agent's own questions follow.

    This used to ask for name and roll number on every single run, prefilled
    from a cached file, which is a formality dressed up as a question. Now the
    first run asks, and later runs do not, which is what "remembered" is
    supposed to mean.

    The second return value is what the manual itself supplied. Showing that is
    how "I only ask for what I cannot derive" becomes visible instead of
    looking like the tool forgot to ask.
    """
    questions: list[dict[str, Any]] = []

    def have(key: str) -> bool:
        return bool((seed.get(key) or "").strip())

    for key, label in ASK_ORDER:
        if have(key):
            continue
        questions.append(
            {"key": key, "label": label, "value": "", "required": True, "hint": ""}
        )

    # Optional, and only when neither the browser nor the manual knows.
    if not have("section") and not getattr(facts, "section", None):
        questions.append(
            {
                "key": "section",
                "label": "Section",
                "value": "",
                "required": False,
                "hint": "Not in the manual either — the cover omits it if you skip.",
            }
        )
    if not have("program"):
        questions.append(
            {
                "key": "program",
                "label": "Program / degree",
                "value": "",
                "required": False,
                "hint": "",
            }
        )

    questions.extend(proposed)

    known = [
        {"label": label, "value": value}
        for label, value in (
            ("Course", facts.course),
            ("Section", facts.section),
            ("Instructor", facts.instructor),
            ("Lab engineer", facts.lab_engineer),
            ("Date", facts.date),
        )
        if value
    ]
    return questions, known


@dataclass
class RunContext:
    """What a run produced that a later revision needs to reuse.

    Held on the Job so `revise_job` can amend the same run directory rather
    than starting a second one. Amending matters: the artifacts a revision
    replaces should be replaced in place, so the chat shows one report that got
    better rather than two reports that disagree.
    """

    store: RunStore
    spec: LabSpec
    facts: Any
    profile: StudentProfile
    plan: Any
    manual_path: Path
    settings: Settings


def _emit_report_and_package(
    job: Job, context: RunContext, outcomes: list[TaskOutcome], usage: RunUsage
) -> None:
    """Build the report and the archive, and register every download.

    Shared by the first run and by every revision. A revision calls this again
    with the amended outcomes, so the report and the zip are rebuilt from the
    same code path -- there is no second implementation to drift.
    """
    store, spec, profile = context.store, context.spec, context.profile

    job.phase("building", "Building the report")
    report = annotate_manual(
        context.manual_path,
        store.report_dir / f"Lab{spec.lab_number}_Report.docx",
        outcomes,
        cover=cover_from(
            spec,
            profile,
            context.facts,
            layout=context.plan.layout,
            tagline=context.plan.tagline,
        ),
    )
    job.register("report", report, label="Report", kind="report")

    job.phase("packaging", "Packaging")
    roll = profile.slug()
    notebook = write_notebook(
        store.report_dir / f"Lab{spec.lab_number}_{roll}.ipynb",
        spec,
        outcomes,
        student=profile.as_display(),
    )
    archive = build_submission(
        store.report_dir / f"Lab{spec.lab_number}_{roll}.zip",
        report,
        outcomes,
    )
    with zipfile.ZipFile(archive, "a") as zf:
        zf.write(notebook, arcname=notebook.name)

    # Code files are registered last so they appear after the two headline
    # downloads in the UI, in task order.
    for outcome in outcomes:
        if outcome.code_path and Path(outcome.code_path).exists():
            job.register(
                f"code:{outcome.task.id}",
                outcome.code_path,
                label=outcome.task.title or outcome.task.id,
                kind="code",
            )

    job.register("package", archive, label="Complete package", kind="package")


def run_job(
    job: Job,
    *,
    manual_path: Path,
    instructions: str,
    profile_seed: dict[str, str],
    settings: Settings,
) -> None:
    """Do the work. Runs on a worker thread; never raises.

    "Never raises" is load-bearing. An exception escaping here would leave the
    job in `running` forever and the browser waiting on a stream that will
    never end -- a hang, which is the worst failure mode to debug because
    nothing is logged and nothing is red. Every path ends in `job.finish()`.
    """
    try:
        if not settings.configured:
            job.finish(error="DEEPSEEK_API_KEY is not set. Add it to .env and restart.")
            return

        usage = RunUsage(model=settings.model_name)

        # -- 1. read ------------------------------------------------------
        job.phase("reading", "Reading the manual")
        manual = read_manual(manual_path)
        facts = extract_cover_facts([p.text for p in manual.paragraphs])
        job.publish({"type": "manual_read", "paragraphs": len(manual.paragraphs)})

        # -- 2. understand -------------------------------------------------
        job.phase("planning", "Working out the tasks")
        spec, repairs, ingest_usage = extract_labspec(
            manual, build_model(settings, phase="ingest")
        )
        usage.phase("ingest").merge(ingest_usage)

        if not spec.tasks:
            job.finish(
                error=(
                    "No tasks could be found in this manual. It may use a layout the "
                    "reader does not recognise -- try adding the tasks as extra "
                    "instructions."
                )
            )
            return

        # The extra-instructions seam. Everything downstream sees the augmented
        # spec; the original manual is never touched.
        spec = apply_instructions(spec, instructions)

        job.publish(
            {
                "type": "spec",
                "lab_number": spec.lab_number,
                "title": spec.title,
                "course": spec.course,
                "task_count": len(spec.tasks),
                "tasks": [{"id": t.id, "title": t.title} for t in spec.tasks],
                "anchor_repairs": len(repairs),
            }
        )

        # -- 3. brief: decide what to ask, and how the cover should look ----
        #
        # This is the pause that matters. It sits BEFORE the solve, so an
        # answer can change the code. The previous version asked after the
        # solve, which meant every reply could only reach the cover page --
        # the money was spent and the programs were already written.
        job.phase("planning", "Working out what to ask")
        seed_profile = profile_from(profile_seed)
        plan, brief_usage = read_briefing(
            spec, facts, seed_profile, instructions, build_model(settings, phase="ingest")
        )
        usage.phase("briefing").merge(brief_usage)

        job.publish({"type": "plan", "question_count": len(plan.questions)})
        job.publish(
            {
                "type": "narration",
                "text": (
                    f"Read {len(spec.tasks)} task{'s' if len(spec.tasks) != 1 else ''}. "
                    + (
                        f"{len(plan.questions)} thing"
                        f"{'s' if len(plan.questions) != 1 else ''} I can't decide myself — "
                        "asking before I start, so the answers can shape the code."
                        if plan.questions
                        else "Everything I need is in the manual, so I'll start now."
                    )
                ),
            }
        )

        questions, known = build_questions(profile_seed, facts, plan.questions)
        job.publish({"type": "questions_ready", "known": known})
        answers = job.ask(questions, timeout_s=ASK_TIMEOUT_S) if questions else {}
        profile = profile_from({**profile_seed, **answers})

        # The extra-instructions seam, applied AFTER the pause so anything the
        # student typed while the agent was thinking is included. Everything
        # downstream sees the augmented spec; the manual is never touched.
        spec = apply_instructions(spec, instructions)

        # -- 4. solve ------------------------------------------------------
        job.phase("solving", "Solving each task")
        store = RunStore.create(spec.lab_number, root=RUNS_ROOT)

        # Two consumers, and the core knows about neither. `Emitter` fans out
        # and swallows a failing consumer's exception, so a bug in the web
        # renderer cannot take down a run that is otherwise going fine.
        recorder = ev.Recorder()
        emitter = ev.Emitter(
            lambda event: job.publish({"type": "event", "event": wire_event(event)}),
            recorder,
        )

        # `tracing_tools` is scoped around the whole solve phase, and the
        # tracer is told which task is running by these same task-level
        # events. A tool callback does not know its own task; the event
        # stream does. Both arrive on this thread, in order.
        def note_current_task(event: ev.Event) -> None:
            if isinstance(event, ev.TaskStarted):
                tracer.task_id = event.task_id

        with tracing_tools(job.publish) as tracer:
            emitter.subscribe(note_current_task)
            manifest = run_lab(
                spec,
                store,
                settings,
                RenderedBackend(theme="light"),
                emitter=emitter,
                usage=usage,
                model=build_model(settings),
            )

        # Remember what a revision would need. A re-run has to reach the same
        # run directory, the same manual and the same cover decisions, and
        # rebuilding any of that from scratch would produce a different report
        # rather than an amended one.
        job.context = RunContext(
            store=store,
            spec=spec,
            facts=facts,
            profile=profile,
            plan=plan,
            manual_path=manual_path,
            settings=settings,
        )

        _emit_report_and_package(job, job.context, manifest.outcomes, usage)

        recorder_events = recorder.events
        store.write_log(
            "events.log", "\n".join(f"{e.at.isoformat()} {e.kind}" for e in recorder_events)
        )

        passed = sum(1 for o in manifest.outcomes if o.status == "passed")
        job.finish(
            summary={
                "passed": passed,
                "failed": len(manifest.outcomes) - passed,
                "total": len(manifest.outcomes),
                "cost_usd": usage.total.cost_usd,
                "run_id": store.run_id,
                "lab_number": spec.lab_number,
                "identity_saved": bool(answers),
            }
        )

    except Exception as exc:  # noqa: BLE001 -- a crash here is a hung browser tab
        import traceback

        traceback.print_exc()
        job.finish(error=f"{type(exc).__name__}: {exc}"[:500])


def revise_job(job: Job, feedback: str) -> None:
    """Redo part of a finished run, in the same run directory.

    Same contract as `run_job`: never raises, always ends in `job.finish()`.

    The mechanism is `run_lab(resume=True)`, which is the core's own
    resumability rather than a parallel implementation of it. Two preparory
    steps are load-bearing and both are explained in `revise.py`: the manifest
    must record the new instruction BEFORE the resume reads it, and the tasks
    the feedback does not concern must keep their outcomes so they are not
    re-solved.
    """
    context: RunContext | None = getattr(job, "context", None)
    if context is None:
        job.finish(error="This run is no longer available to revise. Start a new one.")
        return

    try:
        settings = context.settings
        store = context.store
        usage = RunUsage(model=settings.model_name)
        recorder = ev.Recorder()
        emitter = ev.Emitter(
            lambda event: job.publish({"type": "event", "event": wire_event(event)}),
            recorder,
        )

        manifest = store.load()
        outcomes = manifest.outcomes

        job.phase("planning", "Working out what to change")
        revision, rev_usage = read_revision(
            feedback, outcomes, build_model(settings, phase="ingest")
        )
        usage.phase("revise").merge(rev_usage)

        targets = revision.task_ids
        if revision.everything:
            job.publish(
                {"type": "narration", "text": "Re-solving every task with your change."}
            )
        else:
            pretty = ", ".join(targets)
            job.publish(
                {"type": "narration", "text": f"Re-solving {pretty} with your change."}
            )

        # 1. Rewrite the spec the resume will read. `run_lab` takes the task
        #    list from `manifest.spec` when resuming and ignores the argument,
        #    so an instruction that only lived in the argument would vanish.
        manifest.spec = apply_instructions(context.spec, revision.instruction)

        # 2. Drop only the targeted outcomes, so unaffected tasks keep the work
        #    already paid for. `completed_task_ids` treats them as settled.
        keep = [
            o
            for o in outcomes
            if not revision.everything and o.task.id not in set(targets)
        ]
        dropped = len(outcomes) - len(keep)
        manifest.outcomes = keep
        store.save(manifest)

        job.publish(
            {
                "type": "spec",
                "lab_number": manifest.spec.lab_number,
                "title": manifest.spec.title,
                "course": manifest.spec.course,
                "task_count": len(manifest.spec.tasks),
                "tasks": [
                    {"id": t.id, "title": t.title} for t in manifest.spec.tasks
                ],
                "revision": {"targets": targets, "dropped": dropped},
            }
        )

        def note_current_task(event: ev.Event) -> None:
            if isinstance(event, ev.TaskStarted):
                tracer.task_id = event.task_id

        job.phase("solving", "Solving")
        with tracing_tools(job.publish) as tracer:
            emitter.subscribe(note_current_task)
            resumed = run_lab(
                manifest.spec,
                store,
                settings,
                RenderedBackend(theme="light"),
                emitter=emitter,
                usage=usage,
                model=build_model(settings),
                resume=True,
            )

        _emit_report_and_package(job, context, resumed.outcomes, usage)

        passed = sum(1 for o in resumed.outcomes if o.status == "passed")
        job.finish(
            summary={
                "passed": passed,
                "failed": len(resumed.outcomes) - passed,
                "total": len(resumed.outcomes),
                "cost_usd": usage.total.cost_usd,
                "run_id": store.run_id,
                "lab_number": manifest.spec.lab_number,
                "revised": targets or ["all"],
            }
        )

    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        job.finish(error=f"{type(exc).__name__}: {exc}"[:500])
