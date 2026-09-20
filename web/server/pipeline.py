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

from dataclasses import asdict, dataclass, replace
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any

from labsagent import events as ev
from labsagent.agent.build import build_explainer, build_model
from labsagent.blocks import blocks_for
from labsagent.capture.rendered import RenderedBackend
from labsagent.data import provenance_block
from labsagent.data.sources import acquire, classify
from labsagent.config import PROJECT_ROOT, Settings
from labsagent.ingest.cover import extract_cover_facts
from labsagent.ingest.readers import read_document, read_pasted
from labsagent.emit import DEFAULT_ARTIFACTS, REGISTRY, EmitContext, emit_all
from labsagent.ingest.labspec import extract_labspec
from labsagent.intent import Intent, detect_formats, scope
from labsagent.models import LabSpec, TaskOutcome
from labsagent.orchestrator import run_lab
from labsagent.profile import ASK_ORDER, StudentProfile
from labsagent.report.cover import cover_from
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


#: Answer keys that describe the student, not the work. Everything else the
#: pause collects is an answer to a question the AGENT asked, and has to reach
#: the solver -- which, until now, it did not.
IDENTITY_KEYS = {k for k, _ in ASK_ORDER} | {"section", "program"}

#: Answers the pipeline consumes structurally rather than passing to the
#: solver as prose.
RESERVED_KEYS = IDENTITY_KEYS | {"artifacts", "datasets"}


def answered_notes(questions: list[dict], answers: dict[str, str]) -> str:
    """The agent's own questions and what the student replied, as prose.

    WHY THIS EXISTS. The pause was theatre for everything except identity.
    `job.ask` collected the answers, `profile_from` kept `name`, `cms_id`,
    `section` and `program`, and every other reply -- the answer to the
    question the AGENT chose to ask, the one the pause was moved before the
    solve in order to honour -- was dropped on the floor. Asking a question and
    discarding the answer is worse than not asking.
    """
    labels = {q.get("key"): q.get("label", q.get("key", "")) for q in questions}
    lines = [
        f"- {labels.get(key, key)}: {str(value).strip()}"
        for key, value in answers.items()
        if key not in RESERVED_KEYS and str(value).strip()
    ]
    return "You asked, and the student answered:" + chr(10) + chr(10).join(lines) if lines else ""


def dataset_refs_in(answers: dict[str, str]) -> list[str]:
    """Dataset references the student typed into ANY answer, not just ours.

    WHY SCAN EVERYTHING. The dataset question we add below only appears when
    there is something concrete to confirm. The agent, meanwhile, writes its
    own questions, and `briefing.py` has always listed "data that is referenced
    but not supplied ('which CSV?')" as a good one -- so the reply naming a
    dataset routinely arrives under a key we did not choose and cannot predict.

    Treating that reply as prose only is what the whole feature is trying to
    stop: the student answers "https://.../sales.csv", the solver is told the
    student said that, and nothing downloads anything.

    `classify` is the test rather than a regex, so this accepts exactly what
    the resolver accepts -- a URL, a Kaggle slug, or a path that exists.
    """
    found: list[str] = []
    for key, value in (answers or {}).items():
        if key in RESERVED_KEYS:
            continue
        for token in str(value or "").replace(",", " ").split():
            token = token.strip().strip(".;\"'")
            if not token or token in found:
                continue
            try:
                classify(token)
            except Exception:  # noqa: BLE001 -- not a reference is the normal answer
                continue
            found.append(token)
    return found


def apply_instructions(spec: LabSpec, notes: str = "") -> LabSpec:
    """Fold narration and the code-shaping part of the request into every task.

    NOTES, NOT THE RAW REQUEST. This used to take the student's whole message
    and staple it onto every task statement. Once formats became requestable,
    that turned into a real bug: "give me the word report, the notebook, a
    markdown copy and a zip" reached the SOLVER as if it were part of the
    task, and the agent dutifully wrote a Python program that generated a
    notebook, a .docx and a zip itself -- doing the emitters' job, in the
    solution. A 5,996-byte answer to "read two integers and print their sum".

    One instruction, one channel. The ingest call already splits the request
    three ways -- `tasks_wanted` for scope, `artifacts` for what to produce,
    `notes` for what should change the code -- and only the last of those has
    any business in a solver prompt.

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
    extra = (notes or "").strip()
    note = NARRATION
    if extra:
        note += (
            "\n\nAdditional instructions from the student. Follow these; where they "
            f"conflict with the task text above, these win:\n{extra}"
        )
    # ON `instruction` AND NOT `statement`. These two used to be concatenated,
    # which was invisible while the only deliverable annotated the manual in
    # place. The moment .py, .md and .ipynb became first-class, the leak showed:
    # every one of them prints `statement`, so a submitted file opened with
    # "As you work: before your first tool call, say in ONE short sentence..."
    # `statement` is what the student hands in; `instruction` is what steers the
    # solver. Both still ride in the manifest, so a resume is unchanged.
    return replace(spec, tasks=[replace(t, instruction=note) for t in spec.tasks])


def _acquire(
    job: Job,
    *,
    uploads: list[Path],
    answers: dict[str, str],
    intent: Intent,
    store: RunStore,
    settings: Settings,
) -> list:
    """Turn every dataset reference into a file on disk. Never raises.

    WHAT WINS. If the student saw the "Data to use" field, whatever is in it
    when they submit is the answer -- including an empty field, which means
    "none, pick something sensible yourself". That is the same rule the rest of
    the pause follows: the agent proposes, you confirm or redirect. If the
    field was never shown, the uploads and the manual references stand.

    A FAILURE HERE IS NOT A FAILED RUN. One dead link must not cost you the
    four tasks that never needed it -- the policy `emit_all` already applies to
    a format that will not render. The chat says which reference failed and
    why, and the solve proceeds.
    """
    # An answer names an attached file ("sales.csv"), not its path on disk.
    by_name = {Path(u).name.lower(): Path(u) for u in uploads}

    if "datasets" in answers:
        raw = str(answers.get("datasets") or "")
        refs = [t.strip() for t in raw.replace(",", " ").split() if t.strip()]
    else:
        refs = [str(u) for u in uploads] + list(intent.datasets)

    # References typed into the AGENT's own questions, which have keys we did
    # not choose. See `dataset_refs_in`.
    refs += [r for r in dataset_refs_in(answers) if r not in refs]

    # ONLY A BARE FILENAME IS LOOKED UP. Mapping every reference by its last
    # path segment would resolve "https://example.com/sales.csv" to an upload
    # that happens to be called sales.csv -- silently substituting a different
    # file for the one that was asked for. A name with no separator and no
    # scheme is the only thing that can mean "the file I attached".
    def as_path(ref: str) -> str:
        if "/" in ref or "\\" in ref or ":" in ref:
            return ref
        return str(by_name.get(ref.lower(), ref))

    refs = [as_path(r) for r in refs]

    if not refs:
        return []

    # Reuses `planning` rather than introducing a fifth phase: the progress bar
    # has four segments for every run, and a run with no data must not look
    # like it skipped a step.
    job.phase("planning", "Getting the data")
    job.publish({"type": "data_started", "refs": refs})

    result = acquire(
        refs,
        store.data_dir,
        max_bytes=settings.max_dataset_bytes,
        kaggle_username=settings.kaggle_username,
        kaggle_key=settings.kaggle_key,
        on_progress=lambda ref: job.publish({"type": "data_fetching", "ref": ref}),
    )

    for dataset in result.datasets:
        job.publish(
            {
                "type": "data_ready",
                "name": dataset.name,
                "origin": dataset.origin,
                "ref": dataset.ref,
                "bytes": dataset.bytes,
            }
        )

    if result.datasets:
        names = ", ".join(d.name for d in result.datasets)
        job.publish(
            {
                "type": "narration",
                "text": f"Data ready: {names}. Every task gets a copy in its workspace.",
            }
        )

    # SAY WHY, NOT JUST WHAT -- the same lesson `_emit` records. A missing file
    # the student cannot diagnose from the thread is one they have to come and
    # ask about.
    for ref, reason in result.failures:
        job.publish({"type": "data_failed", "ref": ref, "reason": reason})
        job.publish(
            {
                "type": "narration",
                "text": (
                    f"I could not get {ref}: {reason} "
                    "I will carry on without it -- attach the file and ask me to "
                    "redo the tasks that need it."
                ),
            }
        )

    return result.datasets


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


def _not_a_lab(reading) -> str:
    """What to say when the upload is not an assignment.

    Naming the document is the whole point. "SpecError: extraction failed for
    cv.docx" tells the student nothing; "this looks like a two-page CV" tells
    them exactly what happened and that the tool is not broken. The confidence
    split matters too -- a hedged guess should read as a question, not a
    verdict, because the cost of wrongly refusing a real lab is high.
    """
    what = (reading.what_this_is or "").strip()
    if reading.intent.uncertain:
        return (
            (f"I am not certain what this is -- my best guess is {what}. " if what
             else "I could not tell what this document is. ")
            + "I did not find anything to solve in it. If it is a lab, re-send it "
            "and tell me which tasks you mean; if it is not, tell me what you "
            "would like done with it."
        )
    return (
        (f"This looks like {what}, not a lab manual. " if what
         else "This does not look like a lab manual. ")
        + "There are no tasks in it to solve. Upload a lab, or tell me what you "
        "want done with this file and I will have a go."
    )


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
    #: Only a .docx upload produces these; the DOCX emitter is the sole reader.
    anchors: dict[str, int] = dataclass_field(default_factory=dict)
    #: What the student asked for, so a revision emits the same set of files.
    intent: Intent = dataclass_field(default_factory=Intent)
    #: Already-resolved data files. Held so a revision re-solves against the
    #: same CSV instead of downloading it again -- and gets the same answer.
    datasets: list = dataclass_field(default_factory=list)


def _emit(
    job: Job, context: RunContext, outcomes: list[TaskOutcome], usage: RunUsage
) -> list[str]:
    """Produce whatever was asked for, and register each file as it lands.

    Shared by the first run and by every revision, so there is no second
    implementation to drift -- which is exactly what went wrong before: this
    sequence also existed in `examples/solve_lab.py`, and the two had already
    diverged on which artifacts they honoured and which events they emitted.

    WHY EACH EMITTER IS ISOLATED. This step used to be all-or-nothing. A bad
    anchor raised `IndexError` inside the DOCX writer, the catch-all in
    `run_job` turned it into a job error, and not a single artifact was
    registered -- even though every task had been solved and paid for, and
    `manifest.json` on disk held all of it. Now one format failing costs you
    that format and nothing else, and the chat says which one and why.

    Returns the emitter names that failed, so the caller can mention them.
    """
    store, spec, profile = context.store, context.spec, context.profile
    wanted = list(context.intent.artifacts or DEFAULT_ARTIFACTS)

    job.phase("emitting", "Producing your files")

    # WHAT THE CODE WAS RUN AGAINST, stated once in the report. A result
    # computed from a dataset nobody names is not reproducible, and the grader
    # cannot tell "clustered the supplied data" from "clustered something".
    #
    # Added as a BLOCK rather than to each emitter, which is what the block IR
    # is for: every format renders it, each in its own idiom -- a paragraph in
    # the .docx and .md, a comment at the top of the .py.
    note = provenance_block(context.datasets)
    if note is not None and outcomes:
        first = outcomes[0]
        first.blocks = [note, *blocks_for(first)]

    ctx = EmitContext(
        spec=spec,
        outcomes=outcomes,
        out_dir=store.report_dir,
        profile=profile,
        cover=cover_from(
            spec,
            profile,
            context.facts,
            layout=context.plan.layout,
            tagline=context.plan.tagline,
        ),
        manual_path=context.manual_path,
        anchors=context.anchors,
    )

    failed: list[tuple[str, str]] = []

    def landed(emitter, path: Path) -> None:
        job.register(emitter.name, path, label=emitter.label, kind=emitter.kind)

    def broke(name: str, exc: Exception) -> None:
        reason = f"{type(exc).__name__}: {exc}"[:200]
        failed.append((name, reason))
        job.publish({"type": "emit_failed", "format": name, "reason": reason})

    emit_all(ctx, wanted, on_file=landed, on_error=broke)

    # Code files are registered last so they appear after the headline
    # downloads in the UI, in task order.
    for outcome in outcomes:
        if outcome.code_path and Path(outcome.code_path).exists():
            job.register(
                f"code:{outcome.task.id}",
                outcome.code_path,
                label=outcome.task.title or outcome.task.id,
                kind="code",
            )

    # SAY WHY, NOT JUST WHAT. This line used to read "Could not produce: docx.
    # Everything else is above." -- which tells you a file is missing and gives
    # you no way to find out why. The reason was already in the `emit_failed`
    # frame, visible only in devtools. A failure you cannot diagnose from the
    # thread is a failure you have to come and ask about.
    if failed:
        job.publish(
            {
                "type": "narration",
                "text": " ".join(
                    f"Could not produce the {name} file ({reason})."
                    for name, reason in failed
                )
                + " Everything else is above.",
            }
        )
    return [name for name, _ in failed]


def run_job(
    job: Job,
    *,
    manual_path: Path | None,
    instructions: str,
    profile_seed: dict[str, str],
    settings: Settings,
    dataset_paths: list[Path] | None = None,
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
        job.phase("reading", "Reading what you sent")
        # No file means the message IS the document. It is then passed as the
        # request as well: a pasted lab usually carries its own instructions
        # ("just task 1"), and pasted labs are short enough that reading them
        # twice costs little.
        manual = read_document(manual_path) if manual_path else read_pasted(instructions)
        facts = extract_cover_facts([p.text for p in manual.paragraphs])
        job.publish({"type": "manual_read", "paragraphs": len(manual.paragraphs)})

        # -- 2. understand -------------------------------------------------
        job.phase("planning", "Working out the tasks")
        # The request goes INTO the ingest call, so the model resolves "only
        # task 3" against the task list it is reading in the same breath and
        # cannot name a task that does not exist.
        reading = extract_labspec(
            manual, build_model(settings, phase="ingest"), instructions
        )
        usage.phase("ingest").merge(reading.usage)

        # NOT EVERY UPLOAD IS A LAB. This used to be a hallucination path: the
        # extraction prompt opened by asserting the document WAS a lab manual,
        # so a CV or an invoice was read with that framing and the model
        # obligingly invented tasks, which were then solved, annotated and
        # zipped. The guard that lived here was unreachable -- extraction
        # raised rather than returning an empty list -- so the honest failure
        # surfaced as a raw "SpecError: ..." string in the chat.
        if not reading.is_lab:
            job.finish(error=_not_a_lab(reading))
            return

        spec, repairs, intent = reading.spec, reading.repairs, reading.intent

        # Narrow to what was asked for. Prerequisites come along, because an
        # artifact that cannot run is not a deliverable -- and they are named,
        # because arriving with more than you asked for should never be silent.
        spec, pulled = scope(spec, intent)

        job.publish(
            {
                "type": "spec",
                "lab_number": spec.lab_number,
                "title": spec.title,
                "course": spec.course,
                "task_count": len(spec.tasks),
                "tasks": [{"id": t.id, "title": t.title} for t in spec.tasks],
                "anchor_repairs": len(repairs),
                "scoped": intent.task_ids is not None,
                "pulled_in": pulled,
            }
        )
        if pulled:
            job.publish(
                {
                    "type": "narration",
                    "text": (
                        f"You asked for {', '.join(intent.task_ids)}. "
                        f"{'It needs' if len(pulled) == 1 else 'They need'} "
                        f"{', '.join(pulled)} to run, so I am including "
                        f"{'that' if len(pulled) == 1 else 'those'} too."
                    ),
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
            spec,
            facts,
            seed_profile,
            instructions,
            build_model(settings, phase="ingest"),
            data=[Path(d).name for d in (dataset_paths or [])],
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

        # WHAT DATA I WILL USE, in the pause that already exists. Shown only
        # when there is something concrete to confirm -- files you attached, or
        # a reference the manual named. A lab that needs no data must not be
        # asked about data, which is the same discipline `build_questions`
        # applies to identity: ask for what cannot be derived, and nothing else.
        attached = [Path(d).name for d in (dataset_paths or [])]
        proposed_data = attached + [
            r for r in intent.datasets if r not in attached
        ]
        if proposed_data:
            questions.append(
                {
                    "key": "datasets",
                    "label": "Data to use",
                    "value": ", ".join(proposed_data),
                    "required": False,
                    "hint": (
                        "A file you attached, a https:// link, or a Kaggle dataset "
                        "like owner/name. Clear this and I will pick a suitable "
                        "built-in dataset instead."
                    ),
                }
            )

        # WHAT I WILL PRODUCE, offered as one more field in the pause that
        # already exists rather than as a new control. Decision 3: the agent
        # proposes, you confirm or redirect, once, before anything is spent.
        # An explicit request in the chat wins over the agent's proposal.
        proposed = list(intent.artifacts or plan.artifacts or DEFAULT_ARTIFACTS)
        questions.append(
            {
                "key": "artifacts",
                "label": "Files to produce",
                "value": ", ".join(proposed),
                "required": False,
                "hint": plan.artifacts_reason
                or "Any of: docx, ipynb, py, md, zip. Edit if you want something else.",
            }
        )
        job.publish(
            {"type": "questions_ready", "known": known, "proposed_artifacts": proposed}
        )
        answers = job.ask(questions, timeout_s=ASK_TIMEOUT_S) if questions else {}
        profile = profile_from({**profile_seed, **answers})

        # The agent's own answers fold into the intent, and the intent reaches
        # the solver. The pause was moved before the solve so replies could
        # shape the code; this is the wire that finally makes that true.
        intent = intent.with_notes(answered_notes(questions, answers))

        chosen = [
            a.strip().lower().lstrip(".")
            for a in str(answers.get("artifacts", "")).replace(",", " ").split()
        ]
        intent = replace(intent, artifacts=[a for a in chosen if a in REGISTRY] or proposed)

        # ONE application, not two. This ran at ingest AND again here, on the
        # accumulating spec, so every task statement carried the narration
        # block and the student's instructions twice on every web run.
        spec = apply_instructions(spec, intent.notes)

        # -- 4. data -------------------------------------------------------
        #
        # AFTER the pause and BEFORE the solve, which is the only place it can
        # go. Before the pause, a download commits to whichever dataset the
        # manual happened to name, with no chance to correct it. Inside the
        # solve, it runs once per attempt, and `max_retries_per_task` is 3.
        store = RunStore.create(spec.lab_number, root=RUNS_ROOT)
        datasets = _acquire(
            job,
            uploads=dataset_paths or [],
            answers=answers,
            intent=intent,
            store=store,
            settings=settings,
        )

        # -- 5. solve ------------------------------------------------------
        job.phase("solving", "Solving each task")

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
                explainer=build_explainer(settings, usage),
                datasets=datasets,
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
            anchors=reading.anchors,
            intent=intent,
            datasets=datasets,
        )

        _emit(job, job.context, manifest.outcomes, usage)

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

        # A REVISION MAY CHANGE THE FORMAT. It could not before: `_emit` reads
        # `context.intent.artifacts`, frozen at the first run, so "actually
        # give me a notebook" re-solved every targeted task and handed back the
        # same .docx. `read_revision` only ever extracted task ids and an
        # instruction, so there was no path from the feedback to the emitters.
        wanted = detect_formats(feedback)
        if wanted and wanted != list(context.intent.artifacts):
            context.intent = replace(context.intent, artifacts=wanted)
            job.publish(
                {
                    "type": "narration",
                    "text": f"Switching the output to: {', '.join(wanted)}.",
                }
            )

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
                explainer=build_explainer(settings, usage),
                # The same files, not a fresh download. A revision that
                # re-fetched would also risk re-solving against DIFFERENT data
                # -- a live URL is not guaranteed to serve the same bytes twice
                # -- and the report would then describe two different runs.
                datasets=context.datasets,
            )

        _emit(job, context, resumed.outcomes, usage)

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
