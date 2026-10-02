"""The pipeline, composed. Manual in, artifacts out, with a pause in the middle.

NOTHING IN `labsagent` IS MODIFIED, SUBCLASSED, OR MONKEY-PATCHED. Every step
below is a call into the package's public API. That is not a stylistic
preference -- it is what the core was built for. `events.py` says so directly:

    "The core must not know whether it is being watched by a browser, a log
     file, a test, or nothing."

So this module is that consumer, and the seam it plugs into was cut
before it existed.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any

from labsagent import capabilities, credits
from labsagent import events as ev
from labsagent.agent.build import build_explainer, build_model
from labsagent.blocks import blocks_for
from labsagent.capture.rendered import RenderedBackend
from labsagent.data import Acquisition, candidate_tokens, provenance_block, split_refs
from labsagent.data.sources import acquire, classify
from labsagent.config import PROJECT_ROOT, Settings
from labsagent.ingest.cover import extract_cover_facts
from labsagent.ingest.readers import read_document, read_pasted
from labsagent.emit import DEFAULT_ARTIFACTS, REGISTRY, EmitContext, emit_all
from labsagent.ingest.labspec import extract_labspec
from labsagent.intent import Intent, scope
from labsagent.models import LabSpec, TaskOutcome
from labsagent.orchestrator import run_lab
from labsagent.present import AnswerPlan
from labsagent.prerequisites import (
    KEY_PREFIX,
    OMITTED,
    PROVIDED,
    RECREATED,
    Needed,
    attach,
    has_omitted,
)
from labsagent.profile import ASK_ORDER, StudentProfile
from labsagent.report.cover import cover_from
from labsagent.runstore import RunStore
from labsagent.usage import RunUsage

from .briefing import read_briefing
from .jobs import Job
from .revise import answer_question, read_revision
from .trace import tracing_tools

# How long the browser gets to answer before the run continues without it.
# Ten minutes is chosen against the alternative failure: a tab closed by
# accident should not hold a worker thread until the process exits.
ASK_TIMEOUT_S = 180.0

RUNS_ROOT = PROJECT_ROOT / "runs"
UPLOADS_ROOT = RUNS_ROOT / "_web_uploads"


# The solver no longer narrates into the chat; see apply_instructions.
NARRATION = ""  # retired 2026-09-24 -- see apply_instructions


#: Answer keys that describe the student, not the work. Everything else the
#: pause collects is an answer to a question the AGENT asked, and has to reach
#: the solver -- which, until now, it did not.
IDENTITY_KEYS = {k for k, _ in ASK_ORDER} | {"section", "program"}

#: Answers the pipeline consumes structurally rather than passing to the
#: solver as prose.
RESERVED_KEYS = IDENTITY_KEYS | {"artifacts", "datasets"}


def is_structural(key: str) -> bool:
    """Consumed by code, never passed on as prose. The prerequisite answers are
    a family (`prereq_1`, `prereq_1_value`, ...), so an exact-match set cannot
    hold them -- and a prerequisite choice leaking into the notes would reach
    the solver as "- prereq_1: omit"."""
    return key in RESERVED_KEYS or str(key).startswith(KEY_PREFIX)


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
        if not is_structural(key) and str(value).strip()
    ]
    # A SKIPPED QUESTION STILL HAS AN ANSWER: the default the card promised.
    # Passing it on is what makes "if you skip, I'll ..." true.
    for q in questions:
        key, default = q.get("key"), str(q.get("default") or "").strip()
        if is_structural(key) or not default or str(answers.get(key) or "").strip():
            continue
        # NEUTRAL, NOT "THESE WIN". A skipped default arrived in the solver's
        # prompt under "where they conflict with the task text, these win" --
        # which is how "add a manual fallback if the import fails" became a
        # requirement nobody asked for, and a 323-line solution.
        lines.append(
            f"- {labels.get(key, key)}: not answered; assume {default}, "
            "but only where the task leaves this open"
        )
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
        # A prerequisite the student pastes in is earlier WORK, not data to
        # download -- a link inside it must not be fetched as a dataset.
        if is_structural(key):
            continue
        for token in candidate_tokens(str(value or "")):
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
    # NO NARRATION ANY MORE. The solver used to be told to narrate before its
    # first tool call and after every failure, and those sentences streamed
    # into the chat -- written in the solver's working register ("KeyError on
    # 'species', fixing"), several per task. Progress is now said by the
    # harness, in plain words, from events. It also stops paying output tokens
    # for sentences the solver then carried in its own context.
    note = ""
    if extra:
        note = (
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


def data_question(intent: Intent, uploads) -> dict | None:
    """The one question worth stopping the run for, or None.

    DECIDED BY CODE, NOT BY THE MODEL. The briefing model used to own "which
    dataset?", and asked it about data that was already on its way while
    missing the case that mattered: the manual names a dataset, links nothing,
    and nothing was attached. That case is a fact the pipeline can check --
    ingest says what the manual requires, the harvester says what it links --
    so it is checked here, and it is the only data question there is.
    """
    if uploads or intent.datasets or not intent.data_unlinked:
        return None
    wanted = " and ".join(intent.data_unlinked[:2])
    return {
        "key": "datasets",  # reserved: `_acquire` reads it as the references
        "label": "Where do I get the dataset?",
        "reason": (
            f"The manual asks for {wanted} but doesn't include a link, and the "
            "tasks can't be done without it."
        ),
        "hint": "Paste the Kaggle link (or owner/name), or a https:// link straight to the file.",
        "value": "",
        "default": "",
        "required": True,
    }


#: The card's choices -> what each means to the solver, writer and report.
PREREQ_CHOICES = {"provide": PROVIDED, "omit": OMITTED, "recreate": RECREATED}


def _task_phrase(task_ids) -> str:
    """("task1", "task2", "task5") -> "Tasks 1, 2 and 5"."""
    numbers = [str(t).replace("task", "") for t in task_ids]
    if len(numbers) == 1:
        return f"Task {numbers[0]}"
    return "Tasks " + ", ".join(numbers[:-1]) + " and " + numbers[-1]


def prerequisite_questions(needed: list[Needed], spec) -> tuple[list[Needed], list[dict]]:
    """One required field per missing input the SCOPED tasks rely on.

    OWNED BY CODE, LIKE THE DATA QUESTION. Ingest read (and quoted) what the
    manual relies on from outside itself; whether it is here is a fact; and
    what to do without it is the student's decision, never the agent's. Each
    field offers exactly the choices that are honest for that input: "recreate"
    only when ingest judged it rebuildable from what this lab provides.

    Returns the inputs that survived scoping (asking about Lab 02 when the
    student only wants task 3, which never mentions it, is noise) and their
    fields, in the same order.
    """
    in_scope = {t.id for t in spec.tasks}
    kept = [
        replace(item, task_ids=tuple(t for t in item.task_ids if t in in_scope))
        for item in needed
    ]
    kept = [item for item in kept if item.task_ids]
    questions: list[dict] = []
    for n, item in enumerate(kept, start=1):
        options = [
            {"value": "provide", "label": "I'll give it"},
            {"value": "omit", "label": "Leave those parts out"},
        ]
        if item.recreatable:
            options.append(
                {
                    "value": "recreate",
                    "label": "Recreate it"
                    + (f" from {item.recreate_from}" if item.recreate_from else ""),
                    "hint": "It will be labelled as recreated in the report.",
                }
            )
        one = len(item.task_ids) == 1
        questions.append(
            {
                "key": f"{KEY_PREFIX}{n}",
                "kind": "prerequisite",
                "label": item.what,
                "reason": (
                    f"{_task_phrase(item.task_ids)} rel{'ies' if one else 'y'} on this, "
                    "and it isn't in what you sent."
                ),
                "hint": item.detail,
                "options": options,
                "value": "",
                "default": "",
                "required": True,
            }
        )
    return kept, questions


def resolve_prerequisites(
    kept: list[Needed], questions: list[dict], answers: dict[str, str]
) -> dict[int, tuple[str, str]] | None:
    """The student's decision for every missing input, or None to stop.

    None for anything short of a complete, valid answer: a timeout or "Stop
    here" ({}), a missing or unknown choice, "recreate" where it was never
    offered, or "I'll give it" with nothing given. The user decided this
    (2026-09-26): without an explicit answer the run solves nothing.
    """
    decisions: dict[int, tuple[str, str]] = {}
    for index, (item, question) in enumerate(zip(kept, questions)):
        key = question["key"]
        resolution = PREREQ_CHOICES.get(str(answers.get(key) or "").strip().lower())
        if resolution is None or (resolution == RECREATED and not item.recreatable):
            return None
        value = str(answers.get(f"{key}_value") or "").strip()
        if resolution == PROVIDED and not value:
            return None
        decisions[index] = (resolution, value[:4000])
    return decisions


def prerequisite_stop(kept: list[Needed]) -> str:
    """Why the run stopped, in one paragraph (the UI shows it in a <p>)."""
    whats = "; ".join(item.what[0].lower() + item.what[1:] for item in kept if item.what)
    can_recreate = any(item.recreatable for item in kept)
    return (
        f"I stopped before writing any code: this lab relies on work from outside it "
        f"that isn't here ({whats}). Send the lab again with it pasted into your "
        "message, or choose to leave those parts out"
        + (" or to recreate them" if can_recreate else "")
        + " when I ask."
    )


def closing_needs(outcomes) -> str:
    """What is left to finish, in the words each task stopped with. "" if nothing.

    FINISH THE REST, THEN ASK (decided 2026-09-25). A task that stopped for want
    of something did not hold up the others; this is where the student hears
    about it, once, with every blocker in one place.
    """
    # A gap on a task whose missing input the student CHOSE to leave out is
    # that choice, not a request: asking for it again here would be asking
    # for what they already declined. A blocker is still always reported.
    items = [
        (o.task.title or o.task.id, o.blocker or o.gap)
        for o in outcomes
        if getattr(o, "blocker", None)
        or (getattr(o, "gap", None) and not has_omitted(o.task))
    ]
    if not items:
        return ""
    lines = [f"- {title}: {sentence}" for title, sentence in items]
    return (
        "To finish, I need:" + chr(10) + chr(10).join(lines) + chr(10)
        + "Reply here once you have it, or tell me to do that part another way."
    )


def _looks_like_a_file(ref: str) -> bool:
    """Does this exist on disk? Asked, never inferred -- and never raises.

    Windows raises rather than returning False for a syntactically impossible
    path, which is a "no", not an error to propagate. Same reasoning as
    `sources.classify`.
    """
    try:
        return Path(ref).is_file()
    except OSError:
        return False


def _reask_for_data(job: Job, acquired, uploads: list[Path]) -> dict[str, str] | None:
    """Ask once more for the data, or None if nobody answered.

    `job.ask` returns {} on timeout rather than raising, so an unattended tab
    lands here with nothing -- and must take the stop path, not the carry-on
    path. That distinction is the whole reason this returns None instead of {}.
    """
    job.publish(
        {
            "type": "data_unresolved",
            "failures": [{"ref": ref, "reason": reason} for ref, reason in acquired.failures],
        }
    )
    job.publish(
        {
            "type": "narration",
            "text": (
                "None of the data references worked, so there is nothing to solve "
                "against. Correct it below and I will try again."
            ),
        }
    )
    answers = job.ask(
        [
            {
                "key": "datasets",
                "label": "Data to use",
                "value": ", ".join(acquired.requested),
                "required": True,
                "hint": (
                    "A file you attached, a https:// link straight to the file, or a "
                    "Kaggle dataset like owner/name. Separate several with commas."
                ),
            }
        ],
        timeout_s=ASK_TIMEOUT_S,
    )
    return answers or None


def _acquire(
    job: Job,
    *,
    uploads: list[Path],
    answers: dict[str, str],
    intent: Intent,
    store: RunStore,
    settings: Settings,
) -> Acquisition:
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
        # ONE PATH, TYPED WHOLE, IS ONE REFERENCE. "C:\Users\Online Retail.xlsx"
        # has no comma to split on and several spaces that are not separators,
        # so ask the two things that can settle it -- is this an attachment we
        # hold, or a file that exists -- before applying any rule at all.
        whole = raw.strip().strip("\"'")
        if whole and (whole.lower() in by_name or _looks_like_a_file(whole)):
            refs = [whole]
        else:
            refs = split_refs(raw)
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
        return Acquisition()

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
        convert_excel=settings.convert_excel_to_csv,
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
                "text": f"Got the data: {names}.",
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
                    f"I could not get {ref}: {reason}"
                ),
            }
        )

    # CARRYING ON IS ONLY HONEST WHEN SOMETHING ARRIVED. With some data in
    # hand, a dead link costs the tasks that needed it and nothing else -- the
    # policy `acquire` exists for. With NONE, the same sentence is a promise to
    # solve the lab against nothing, which is what the caller now refuses.
    if result.datasets and result.failures:
        job.publish(
            {
                "type": "narration",
                "text": (
                    "I will carry on with what I have -- attach the missing file "
                    "and ask me to redo the tasks that need it."
                ),
            }
        )

    # WHAT YOU ATTACHED BUT DID NOT NAME. Not promoted to a dataset: clearing
    # the data field deliberately means "no data", and guessing past that would
    # silently substitute a file nobody asked for.
    used = {str(d.ref).lower() for d in result.datasets} | {
        d.name.lower() for d in result.datasets
    }
    unused = [Path(u).name for u in uploads if Path(u).name.lower() not in used]
    if unused:
        job.publish({"type": "data_unused", "names": unused})
        job.publish(
            {
                "type": "narration",
                "text": (
                    f"You attached {', '.join(unused)}, but the data field did not "
                    "list it, so I did not use it."
                ),
            }
        )

    return result


def wire_event(event: ev.Event) -> dict[str, Any]:
    """An event dataclass -> JSON-safe dict for the browser.

    Three transformations, all deliberate.

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
    # CREDITS, NOT DOLLARS. The core prices in USD (what the provider bills);
    # the browser only ever sees credits. Live events carry EXACT credits so a
    # running total does not round each task up; the settled charge is the
    # job summary's `credits`.
    if "cost_usd" in payload:
        payload["credits"] = credits.exact(payload.pop("cost_usd"))
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

    Identity lives in browser `localStorage` and arrives with each request, so
    the server has nothing to remember and nothing to overwrite.
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
    # Identity is no longer asked here -- it only ever reached the cover and
    # the filenames, both of which are built at packaging. `seed` is kept in
    # the signature so callers are unchanged.
    questions: list[dict[str, Any]] = []
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
    #: task id -> what its answer includes and the manual's boxes for it.
    #: A follow-up that says "add screenshots" changes only this.
    plans: dict = dataclass_field(default_factory=dict)
    #: The screenshot look chosen for this lab, so a revision draws the same.
    screenshots: Any = None


_NOTEBOOK_TOOL = re.compile(r"\b(colab|jupyter)\b", re.I)


def screenshot_backend(spec, manual, kind: str, manual_path) -> RenderedBackend:
    """The window the student would have run this lab in (decided 2026-10-03).

    A lab done in Jupyter or Colab -- an .ipynb handed out as the lab, a lab
    the reading classed as a notebook lab, or a manual that names either tool
    -- gets a notebook cell. Everything else gets Windows Terminal, in the
    lab's own folder under a generic user.
    """
    text = " ".join(p.text for p in getattr(manual, "paragraphs", []) or [])
    notebook = (
        (manual_path is not None and Path(manual_path).suffix.lower() == ".ipynb")
        or kind == "notebook_lab"
        or _NOTEBOOK_TOOL.search(text) is not None
    )
    folder = f"Lab {spec.lab_number}".strip() if spec.lab_number else "Lab"
    return RenderedBackend(look="notebook" if notebook else "terminal", folder=folder)


def _only_when_wanted(explainer, plans: dict):
    """The writer, called only for the words the report will actually show.

    An explanation nobody asked for is no longer printed, so it is no longer
    paid for either: one model call per passed task, saved. Written answers
    are always written (the manual asked them), and so is a theory task,
    whose overview IS its answer.
    """

    def explain(task, code_text, transcript):
        wanted = (plans.get(task.id) or AnswerPlan()).include.explanation
        if wanted or task.written_questions or not getattr(task, "needs_code", True):
            return explainer(task, code_text, transcript)
        return None

    return explain


def _explain_missing(context, outcomes, usage: RunUsage) -> None:
    """Write the explanations a follow-up just asked for, and only those."""
    explainer = build_explainer(context.settings, usage)
    for outcome in outcomes:
        if outcome.status != "passed" or outcome.explanation:
            continue
        written = explainer(outcome.task, outcome.code_text, outcome.transcript)
        if isinstance(written, str):
            outcome.explanation = written
        elif written is not None:
            outcome.explanation = written.overview


def _emit(
    job: Job, context: RunContext, outcomes: list[TaskOutcome], usage: RunUsage
) -> list[str]:
    """Produce whatever was asked for, and register each file as it lands.

    Shared by the first run and by every revision, so there is no second
    implementation to drift -- which is exactly what went wrong before: a
    second copy of this sequence had diverged on which artifacts it honoured
    and which events it emitted.

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
        style=getattr(context.plan, "style", "classic") or "classic",
        tagline=getattr(context.plan, "tagline", "") or "",
        plans=context.plans,
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

        # -- 2b. can this environment do these tasks at all? ----------------
        #
        # A LIMIT IS CHECKED WHERE THE WORK IS SCOPED, NOT WHERE IT RUNS. An
        # HTML/CSS lab used to reach the solver, whose last instruction is
        # "write task1.py", and came back as Python that prints HTML -- marked
        # passed, with the web pages never delivered. The solver was the first
        # and worst place to discover the lab was not Python.
        #
        # REFUSE THE WHOLE LAB (decided 2026-09-26), on the SCOPED spec -- so
        # "only tasks 2-4" still works when task 1 is out of scope. Before the
        # briefing call and before any run directory: nothing is charged,
        # exactly like a document that is not a lab.
        out_of_scope = capabilities.check(spec, reading.requirements)
        if out_of_scope:
            job.publish(
                {
                    "type": "out_of_scope",
                    "tasks": [
                        {"id": o.task_id, "title": o.title, "reasons": o.reasons}
                        for o in out_of_scope
                    ],
                }
            )
            job.finish(error=capabilities.refusal(out_of_scope, len(spec.tasks)))
            return

        # A LIBRARY THAT ISN'T INSTALLED is one step, not the lab: said now,
        # before any money is spent, and the solver leaves that step out
        # honestly ("Not done: ...") instead of imitating the library.
        notice = capabilities.library_notice(
            capabilities.missing_libraries(spec, reading.requirements)
        )
        if notice:
            job.publish({"type": "narration", "text": notice})

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
            # THE WHOLE DATA PLAN, not just the uploads. Told only about files
            # already attached, it asked "where should the Superstore dataset
            # come from?" about a Kaggle link that was about to be downloaded.
            data=[Path(d).name for d in (dataset_paths or [])] + list(intent.datasets),
        )
        usage.phase("briefing").merge(brief_usage)

        asked_for_data = data_question(intent, dataset_paths or [])
        # WORK FROM OUTSIDE THIS LAB that nothing here supplies -- asked about
        # here, by code, before any code is written. See prerequisites.py.
        needed, prereq_fields = prerequisite_questions(intent.prerequisites, spec)
        job.publish({"type": "plan", "question_count": len(plan.questions)})
        n = len(spec.tasks)
        follow = ""
        if asked_for_data:
            follow += (
                " The manual asks for " + " and ".join(intent.data_unlinked[:2])
                + " but doesn't include a link. Paste it below and I'll download it."
            )
        for item in needed:
            one = len(item.task_ids) == 1
            # "which you didn't send" reads right for "results" and "file" alike.
            follow += (
                f" {_task_phrase(item.task_ids)} rel{'ies' if one else 'y'} on "
                f"{item.what[0].lower() + item.what[1:]}, which you didn't send."
            )
        if needed:
            follow += " Tell me how to handle that before I start."
        elif plan.questions and not asked_for_data:
            follow = " One quick question first — skip it if you like."
        job.publish(
            {"type": "narration", "text": f"Found {n} task{'s' if n != 1 else ''}." + follow}
        )

        # ONLY GENUINE QUESTIONS PAUSE THE RUN (2026-09-24). This used to add
        # name and CMS ID as REQUIRED fields, a data confirmation, and an
        # always-present "Files to produce" field -- so every run stopped for up
        # to ten minutes even when the agent had nothing to ask. None of those
        # change the code: identity is asked at packaging, formats are resolved
        # from the request and can be changed afterwards for free, and data is
        # fetched as named and asked about only if none of it arrives.
        questions, known = build_questions(profile_seed, facts, plan.questions)
        questions = [*prereq_fields, *questions]
        if asked_for_data:
            questions = [asked_for_data, *questions]

        proposed = list(intent.artifacts or plan.artifacts or DEFAULT_ARTIFACTS)
        job.publish(
            {"type": "questions_ready", "known": known, "proposed_artifacts": proposed}
        )
        answers = job.ask(questions, timeout_s=ASK_TIMEOUT_S) if questions else {}
        profile = profile_from(profile_seed)

        # NO LINK, NO ANSWER, NO RUN. Solving against invented data is the
        # improvisation this whole pass exists to stop, so a skipped data
        # question ends the run before any code is written -- and says why.
        if asked_for_data and not str(answers.get("datasets") or "").strip():
            job.finish(
                error=(
                    "I can't do this lab without "
                    + " and ".join(intent.data_unlinked[:2])
                    + ". Paste its Kaggle link, or attach the file, and send the lab again."
                )
            )
            return

        # NO DECISION, NO RUN. For each missing input the student said give it,
        # leave it out, or recreate it -- or the run stops here, having written
        # nothing. Improvising a stand-in is what this whole check replaces.
        if needed:
            decisions = resolve_prerequisites(needed, prereq_fields, answers)
            if decisions is None:
                job.finish(error=prerequisite_stop(needed))
                return
            spec = attach(spec, needed, decisions)

        # The agent's own answers fold into the intent, and the intent reaches
        # the solver. The pause was moved before the solve so replies could
        # shape the code; this is the wire that finally makes that true.
        intent = intent.with_notes(answered_notes(questions, answers))

        intent = replace(intent, artifacts=[a for a in proposed if a in REGISTRY])

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
        acquired = _acquire(
            job,
            uploads=dataset_paths or [],
            answers=answers,
            intent=intent,
            store=store,
            settings=settings,
        )

        # A LAB THAT ASKED FOR DATA AND GOT NONE DOES NOT GET SOLVED. Not the
        # same as a lab with no data, and not the same as one dead link among
        # three -- both of those still run, which is the whole point of
        # `acquire` collecting failures instead of raising them. This is the
        # case where every reference failed, and carrying on means paying for
        # five tasks written against nothing. That is the run this check exists
        # because of.
        #
        # Re-asked ONCE, because the usual cause is a fixable typo and the
        # student is already watching this tab. Then stopped, with the reasons
        # still on screen and the artifacts of ingest still on disk, so a
        # corrected answer resumes rather than restarts.
        if acquired.total_failure:
            answers = _reask_for_data(job, acquired, dataset_paths or [])
            if answers is not None:
                acquired = _acquire(
                    job,
                    uploads=dataset_paths or [],
                    answers=answers,
                    intent=intent,
                    store=store,
                    settings=settings,
                )
        if acquired.total_failure:
            job.finish(
                error=(
                    "I could not get the data this lab needs, so I stopped before "
                    "writing any code. "
                    + "; ".join(f"{ref}: {reason}" for ref, reason in acquired.failures)
                    + " Attach the file and start again."
                )[:500]
            )
            return

        datasets = acquired.datasets
        data_failures = list(acquired.failures)

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

        screenshots = screenshot_backend(spec, manual, reading.kind, manual_path)
        with tracing_tools(job.publish) as tracer:
            emitter.subscribe(note_current_task)
            manifest = run_lab(
                spec,
                store,
                settings,
                screenshots,
                emitter=emitter,
                usage=usage,
                model=build_model(settings),
                explainer=_only_when_wanted(build_explainer(settings, usage), reading.plans),
                datasets=datasets,
                data_failures=data_failures,
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
            plans=dict(reading.plans),
            screenshots=screenshots,
        )

        _emit(job, job.context, manifest.outcomes, usage)
        needs = closing_needs(manifest.outcomes)
        if needs:
            job.publish({"type": "narration", "text": needs})

        recorder_events = recorder.events
        ev.write_events_log(store, recorder_events)

        # Settle the charge: every phase of the run (ingest, briefing, solve,
        # explain), rounded up once, recorded with the rate it was charged at.
        manifest.credits = credits.charge(usage.total.cost_usd)
        manifest.usd_per_credit = credits.USD_PER_CREDIT
        store.save(manifest)

        passed = sum(1 for o in manifest.outcomes if o.status == "passed")
        job.finish(
            summary={
                "passed": passed,
                "failed": len(manifest.outcomes) - passed,
                "total": len(manifest.outcomes),
                "credits": manifest.credits,
                "run_id": store.run_id,
                "lab_number": spec.lab_number,
                "blocked": sum(1 for o in manifest.outcomes if o.blocker),
                "identity_saved": bool(answers),
            }
        )

    except Exception as exc:  # noqa: BLE001 -- a crash here is a hung browser tab
        import traceback

        traceback.print_exc()
        job.finish(error=f"{type(exc).__name__}: {exc}"[:500])


_CHANGES = "\n\nChanges the student asked for, oldest first (the latest wins):\n"
_SEED = "\n\nThis is a revision."
MAX_SEED_CODE = 6000


def revise_instruction(prior: str, change: str, code: str) -> str:
    """A task's solver instruction after one more change request.

    ACCUMULATES. This used to be rebuilt from the ORIGINAL spec every time, so a
    second follow-up silently dropped the first one's change. Earlier changes are
    kept as a list; only the seed (the earlier code) is replaced.

    SEEDED WITH THE EARLIER CODE, so a re-solve is an edit, not a rewrite from
    zero. "Use a while loop in task 1" should cost a write and a run, not the
    whole plan-write-debug loop again.
    """
    base = (prior or "").split(_SEED)[0]
    changes: list[str] = []
    if _CHANGES in base:
        base, listed = base.split(_CHANGES, 1)
        changes = [line[2:] for line in listed.splitlines() if line.startswith("- ")]
    change = (change or "").strip()
    if change and change not in changes:
        changes.append(change)
    out = base + (_CHANGES + "\n".join(f"- {c}" for c in changes) if changes else "")
    if code.strip():
        out += (
            _SEED
            + " Edit your earlier solution below rather than starting over, and keep"
            " what already works:\n```python\n"
            + code[:MAX_SEED_CODE].rstrip()
            + "\n```"
        )
    return out


def _record_followup(manifest, feedback: str, followup, usage: RunUsage, prior_cost: float):
    """Add this follow-up to the run's record instead of overwriting it."""
    charged = credits.charge(usage.total.cost_usd)
    manifest.followups.append(
        {
            "feedback": feedback[:500],
            "kind": followup.kind,
            "resolved": list(followup.resolve_ids),
            "rewritten": list(followup.rewrite_ids),
            "artifacts": list(followup.artifacts),
            "style": followup.style,
            "usage": usage.total.as_dict(),
            "cost_usd": usage.total.cost_usd,
            "credits": charged,
        }
    )
    manifest.cost_usd = prior_cost + usage.total.cost_usd
    # Added per follow-up, not recomputed from the dollar total: each charge
    # rounds up once, when it settles.
    manifest.credits += charged
    manifest.usd_per_credit = credits.USD_PER_CREDIT


def _rewrite(context: RunContext, outcomes, followup, usage: RunUsage) -> None:
    """Re-run ONLY the writer for these tasks. The code and output are untouched."""
    explainer = build_explainer(context.settings, usage)
    note = followup.rewrite_instruction
    for outcome in outcomes:
        if outcome.task.id not in followup.rewrite_ids or outcome.status != "passed":
            continue
        asked = replace(
            outcome.task,
            statement=(
                f"{outcome.task.statement}\n\nThe student asked for this change to the "
                f"written part: {note}"
            ),
        )
        written = explainer(asked, outcome.code_text, outcome.transcript)
        if isinstance(written, str) and written:
            outcome.explanation = written
        elif written is not None and not isinstance(written, str):
            outcome.explanation = written.overview or outcome.explanation
            outcome.answers = list(written.answers) or outcome.answers


def revise_job(job: Job, feedback: str) -> None:
    """Handle a follow-up on a finished run, in the same run directory.

    Same contract as `run_job`: never raises, always ends in `job.finish()`.

    ROUTE FIRST, THEN DO THE LEAST WORK THAT SATISFIES IT -- see `revise.py`.
    A question costs one or two small calls and changes no files. A change to
    the write-up re-runs only the writer. A change of format, layout or name
    only rebuilds the files. Only a change to what a program does reaches the
    solver, and then only for the tasks it names, seeded with their earlier
    code.
    """
    context: RunContext | None = getattr(job, "context", None)
    if context is None:
        job.finish(error="This run is no longer available to revise. Start a new one.")
        return

    try:
        settings = context.settings
        store = context.store
        usage = RunUsage(model=settings.model_name)
        manifest = store.load()
        prior_cost = manifest.cost_usd
        prior_usage = dict(manifest.usage)
        outcomes = manifest.outcomes

        job.phase("planning", "Reading your message")
        followup, rev_usage = read_revision(
            feedback,
            outcomes,
            build_model(settings, phase="ingest"),
            formats=list(context.intent.artifacts or DEFAULT_ARTIFACTS),
            style=getattr(context.plan, "style", "classic"),
        )
        usage.phase("revise").merge(rev_usage)

        # -- a question: answer it, change nothing -------------------------
        if followup.is_answer:
            job.publish({"type": "followup", "action": "answer"})
            reply = followup.reply
            if followup.needs_code_for:
                wanted = [o for o in outcomes if o.task.id in followup.needs_code_for]
                reply, answer_usage = answer_question(
                    feedback, wanted, build_model(settings, phase="explain")
                )
                usage.phase("answer").merge(answer_usage)
            job.publish({"type": "narration", "text": reply})
            _record_followup(manifest, feedback, followup, usage, prior_cost)
            store.save(manifest)
            job.finish(summary={"answered": True, "credits": manifest.credits})
            return

        # -- a change: say what, in plain words ------------------------------
        skip = ["read", "brief"] if followup.resolve_ids else ["read", "brief", "solve"]
        job.publish({"type": "followup", "action": "change", "skip": skip})
        if followup.reply:
            job.publish({"type": "narration", "text": followup.reply})

        # Format, layout and identity cost nothing but a rebuild.
        if followup.artifacts:
            context.intent = replace(context.intent, artifacts=followup.artifacts)
        if followup.style:
            context.plan.style = followup.style
        if followup.identity:
            context.profile = profile_from(
                {**asdict(context.profile), **followup.identity}
            )
        # WHAT THE ANSWERS CONTAIN is presentation: a rebuild, and a writer
        # call only for explanations that were never written.
        if followup.show or followup.hide:
            context.plans = {
                t.id: (context.plans.get(t.id) or AnswerPlan()).changed(
                    followup.show, followup.hide
                )
                for t in manifest.spec.tasks
            }
            if "explanation" in followup.show:
                job.phase("solving", "Writing the explanations")
                _explain_missing(context, outcomes, usage)

        # Only the tasks whose CODE must change reach the solver.
        if followup.resolve_ids:
            targets = set(followup.resolve_ids)
            by_id = {o.task.id: o for o in outcomes}
            manifest.spec = replace(
                manifest.spec,
                tasks=[
                    replace(
                        t,
                        instruction=revise_instruction(
                            t.instruction,
                            followup.resolve_instruction,
                            by_id[t.id].code_text if t.id in by_id else "",
                        ),
                    )
                    if t.id in targets
                    else t
                    for t in manifest.spec.tasks
                ],
            )
            manifest.outcomes = [o for o in outcomes if o.task.id not in targets]
            store.save(manifest)

            job.publish(
                {
                    "type": "spec",
                    "lab_number": manifest.spec.lab_number,
                    "title": manifest.spec.title,
                    "course": manifest.spec.course,
                    "task_count": len(manifest.spec.tasks),
                    "tasks": [{"id": t.id, "title": t.title} for t in manifest.spec.tasks],
                    "revision": {"targets": sorted(targets), "dropped": len(targets)},
                }
            )

            recorder = ev.Recorder()
            emitter = ev.Emitter(
                lambda event: job.publish({"type": "event", "event": wire_event(event)}),
                recorder,
            )

            def note_current_task(event: ev.Event) -> None:
                if isinstance(event, ev.TaskStarted):
                    tracer.task_id = event.task_id

            job.phase("solving", "Updating the tasks")
            with tracing_tools(job.publish) as tracer:
                emitter.subscribe(note_current_task)
                manifest = run_lab(
                    manifest.spec,
                    store,
                    settings,
                    context.screenshots or RenderedBackend(),
                    emitter=emitter,
                    usage=usage,
                    model=build_model(settings),
                    resume=True,
                    explainer=_only_when_wanted(
                        build_explainer(settings, usage), context.plans
                    ),
                    # The same files, not a fresh download: a live URL is not
                    # guaranteed to serve the same bytes twice.
                    datasets=context.datasets,
                )
            # run_lab resumes in task order of completion; put them back in
            # the order the lab lists them.
            order = [t.id for t in manifest.spec.tasks]
            manifest.outcomes.sort(key=lambda o: order.index(o.task.id) if o.task.id in order else 0)

        if followup.rewrite_ids:
            job.phase("solving", "Rewriting the explanations")
            _rewrite(context, manifest.outcomes, followup, usage)

        # EVERY current format is rebuilt after ANY change. The old path could
        # rebuild only the notebook and leave a stale Word report beside it.
        _emit(job, context, manifest.outcomes, usage)
        needs = closing_needs(manifest.outcomes)
        if needs:
            job.publish({"type": "narration", "text": needs})

        manifest.usage = prior_usage  # the first pass's breakdown stays
        _record_followup(manifest, feedback, followup, usage, prior_cost)
        store.save(manifest)

        passed = sum(1 for o in manifest.outcomes if o.status == "passed")
        job.finish(
            summary={
                "passed": passed,
                "failed": len(manifest.outcomes) - passed,
                "total": len(manifest.outcomes),
                "credits": manifest.credits,
                "run_id": store.run_id,
                "lab_number": manifest.spec.lab_number,
                "revised": followup.resolve_ids + followup.rewrite_ids,
            }
        )

    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        job.finish(error=f"{type(exc).__name__}: {exc}"[:500])


def repackage_job(job: Job, identity: dict[str, str]) -> None:
    """Put the student's name on the files: a rebuild, and no model call at all.

    Identity is asked at packaging now, not before solving -- it only ever
    reached the cover and the filenames. So adding it later is exactly as good
    as having had it first, and costs a few hundred milliseconds of python-docx.
    """
    context: RunContext | None = getattr(job, "context", None)
    if context is None:
        job.finish(error="This run is no longer available. Start a new one.")
        return
    try:
        context.profile = profile_from({**asdict(context.profile), **identity})
        manifest = context.store.load()
        _emit(job, context, manifest.outcomes, RunUsage(model=context.settings.model_name))
        job.finish(summary={"identity_saved": True, "credits": manifest.credits})
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        job.finish(error=f"{type(exc).__name__}: {exc}"[:500])
