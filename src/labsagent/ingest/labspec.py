"""RawManual -> LabSpec via one structured LLM call.

STRUCTURED OUTPUT is the concept here. Instead of asking for prose and parsing
it with regexes, we pin the response to a schema.

There are three mechanisms and they are NOT equivalent:

  function_calling  forced tool_choice; model must call a schema-shaped tool
  json_schema       response_format with server-side schema validation
  json_mode         response_format json_object -- valid JSON only, no schema

DeepSeek V4.1 runs in thinking mode and rejects a forced tool_choice, so the
first two fail here. json_mode works, which means schema enforcement moves
CLIENT-SIDE: the schema goes in the prompt and Pydantic is the real validator.
Know which of the three you are actually getting -- "structured output" is not
one guarantee, it is three different ones.

THE ANCHOR PROBLEM. We ask the model for `anchor_idx`, a paragraph index. Models
are unreliable at positional counting: they will confidently return 7 when the
answer is 8. But they are very good at quoting text they can see.

So we ask for BOTH: the index, and a short quote of the paragraph being anchored
to. Then we reconcile -- if the quote does not match the paragraph at that
index, we search for the quote and trust it instead. The redundant field costs a
handful of tokens and converts a silent misplacement into a self-correcting one.

The general technique: when a model must produce something it is weak at, have
it also produce something it is strong at, and verify one against the other.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field

from langchain_core.callbacks import UsageMetadataCallbackHandler
from pydantic import BaseModel, Field

from labsagent.capabilities import (
    NEEDS,
    SOLVER_LANGUAGE,
    Requirement,
    is_python,
    normalise_library,
)
from labsagent.errors import SpecError
from labsagent.ingest.docx_reader import RawManual
from labsagent.ingest.readers import produces_anchors
from labsagent.intent import Intent, detect_formats, detect_parts
from labsagent.models import LabSpec, Task
from labsagent.prerequisites import Needed
from labsagent.present import PARTS, SLOT_PARTS, AnswerPlan, Include, Slot
from labsagent.usage import Usage

EXTRACTION_PROMPT = """You are reading a document a student uploaded, and deciding what it is.

Do NOT assume it is a lab manual. It might be a university programming lab, a
notebook-style data assignment, or something else entirely -- a CV, an invoice,
an essay, a blank page. Saying so plainly is a correct and useful answer. It is
much worse to invent tasks that are not there than to report that you found
none.

Each line below is one paragraph of the document, prefixed with its index in
square brackets, e.g. "[7]". Style names appear in brackets after the index when
the paragraph is not body text. A paragraph inside a table also carries [cell],
and an empty cell reads (empty).

First classify the document:

- document_kind: "lab" for a programming lab whose tasks the student must write
  code for; "notebook_lab" when the code is largely given and the deliverable is
  a notebook; "other" for anything that is not an assignment at all.
- confidence: 0.0 to 1.0, how sure you are of document_kind. Be honest. Use a
  low value when the document is ambiguous or you are guessing.
- what_this_is: one short phrase describing the document as you actually found
  it, e.g. "a two-page CV" or "Lab 03 on loops and functions". Always fill this
  in, especially when document_kind is "other" -- it is what the student is told.

If document_kind is "other", return an empty tasks list and stop. Do not invent
tasks to fill the schema.

Otherwise, identify every TASK the student must complete. Ignore objectives,
headers, titles and closing remarks. A task is either something to program, or a
question to answer in words (a theory or discussion question with nothing to run).

For each task report:

- task_number: 1, 2, 3... in document order.
- title: the task's heading, or a short descriptive title if it has none.
- statement: the COMPLETE requirement text, verbatim where possible, including
  any prompt strings, expected output format and sample runs the task mentions.
  This is the only thing the solver will see, so it must be self-contained.
- sample_inputs: the exact input values a test run should use, in order, as
  strings. Derive them from any sample run the manual gives. If the manual gives
  no values, choose simple sensible ones that satisfy the task.
- wants_explanation: true only if the task explicitly asks the student to
  explain, discuss, describe or comment on their approach.
- wants_screenshot: true only if the document asks for screenshots, snapshots or
  pictures of the program's output -- for this task, or for every task ("attach
  a screenshot of the output of each program"). A chart or plot the program
  draws is not a screenshot.
- screenshot_evidence: when wants_screenshot is true, the document's words that
  ask for it, copied exactly, under 80 characters. Otherwise "".
- written_questions: the parts of the task the student must answer IN WORDS rather
  than by what the program prints -- explain, analyse, compare, discuss, interpret,
  justify, observe, "why". One short question per entry, e.g. "Why does the
  perceptron fail on XOR?". Empty when the task only asks for a program.
- needs_code: false ONLY for a pure theory question that asks for no program at
  all AND whose answer does not depend on this lab's data or results. "Which
  region shows the most variability in profit?" needs code: its answer has to be
  computed, not recalled. Anything that says implement, write, compute, plot or
  train is true.
- effort: "basic" for a short introductory exercise -- read input, arithmetic, a
  loop, string or list handling, one simple chart or one library call -- that a
  student would finish in under about 30 lines. "standard" for anything with
  several stages, an algorithm implemented from scratch, model training, or a
  multi-step data pipeline. When unsure, "standard".
- anchor_idx: the index of the LAST paragraph belonging to this task -- the
  paragraph the student's code and output should be inserted after. This is the
  final paragraph of the task's description, NOT the heading, and NOT the next
  task's heading.
- anchor_quote: the first 40 characters of the paragraph at anchor_idx, copied
  exactly. This is used to verify anchor_idx, so it must be an exact copy.
- answer_slots: ONLY where the document itself gives this task's answer a place
  of its own: an (empty) cell under or beside the task, or a line such as "Write
  your code here", "Paste the output here" or "Answer:" left blank for the
  student. One entry per place: {{"part": what the place is for, from its own
  label -- "code", "output" (output or screenshot), "answer" (words) or "any";
  "idx": the index of the (empty) cell or the placeholder line itself, never of
  the label beside it; "quote": its first 40 characters copied exactly, or ""
  for an (empty) cell}}. Never the task statement, never another task's
  text, never a cell holding instructions or sample data. Empty for most
  manuals: when there is no such place, the answer goes after the task.
- language: the programming language the student must WRITE this task's
  solution in, lowercase: "python", "html/css", "javascript", "java", "c++",
  "c", "sql", "r", "matlab", and so on. "none" for a question answered only in
  words. The language is what the student writes, not what the program
  outputs: a Python program that writes an HTML file, a CSV or a chart is
  "python". When the task names no language, use the lab's (a lab whose tools
  are Python, Colab or Jupyter is "python").
- language_evidence: only when language is not "python" or "none": the words in
  the document that show it, copied exactly, under 80 characters, e.g. "create
  a personal portfolio website from scratch using HTML and CSS". Otherwise "".
- needs: what the PROGRAM itself needs, to run or to show its result, that a
  Python process with no screen and no internet does not have. Only these:
    "display"   a window, GUI, browser or game screen: tkinter, turtle, pygame,
                a web page to view. NOT a chart saved to a file, NOT plt.show(),
                NOT printing to the terminal.
    "server"    a program that keeps running and waits for connections: a web
                server, a socket chat server and client.
    "internet"  the program calls a web API or scrapes a site while it runs.
                NOT downloading the lab's dataset -- that happens before any
                code runs.
    "hardware"  physical devices: Arduino, sensors, a camera, a microphone, a GPU.
    "software"  a program other than Python must be used: MATLAB, a database
                server, Packet Tracer, Wireshark. NOT Jupyter, Colab, an IDE,
                or an Excel/CSV file.
  Each entry: {{"need": one of the above, "why": a short phrase, "evidence":
  the document's words, copied exactly, under 80 characters}}. Only what the
  task text requires, never what its topic suggests. How the student submits
  (take a screenshot, zip the files, upload to LMS) is not a need. A task done
  in another tool rather than in code ("clean the data in Tableau Prep") has
  the "software" need even though it has no program. Empty for almost every
  task.
- libraries: third-party libraries the task text REQUIRES by name, e.g.
  ["tensorflow"]. Not one it merely mentions or allows ("you may use ...").
  Empty when it names none.

Also report the lab number, the lab title, and the course code if present.

Finally, resolve what the student asked for into `intent`. Their request, which
may be empty, is quoted below.

- tasks_wanted: the task_numbers they asked for, e.g. [3] for "only task 3".
  Leave EMPTY when they did not narrow it down -- empty means all of them. Only
  use numbers that exist in the tasks you just listed.
- artifacts: which files to produce, from exactly this set:
    "docx"  a Word report, the manual annotated in place
    "ipynb" a Jupyter/Colab notebook with outputs already in it
    "py"    a plain Python script
    "md"    a markdown write-up
    "zip"   an archive of everything
  Leave EMPTY only when they named no file type at all. If the document itself
  says what to submit -- "submit only the .ipynb on LMS" -- honour that.

  ANY mention of a file type or extension belongs HERE and never in notes.
  Worked example. Request: "Provide the completed lab as an executed .ipynb
  notebook file that includes the run outputs."
      artifacts: ["ipynb"]
      notes:     ""
  The words about being executed and including outputs describe the NOTEBOOK,
  which is produced for you after the code runs. They are not instructions to
  the person writing the code, and putting them in notes makes the solver try
  to build the notebook itself.
- notes: ONLY the parts of their request that should change the CODE you write
  -- a library to use, a style, a constraint, a value to assume. This is the
  only part of their request the solver will ever see, so a coding instruction
  left out here is lost entirely.
  Do NOT repeat file formats or task selection here. Those are already handled
  by `artifacts` and `tasks_wanted`, and repeating them makes the solver try to
  produce the files itself in Python instead of just solving the task.
  Empty if there is nothing.
- show / hide: parts of every answer the student's message asks to ADD or to
  LEAVE OUT, from exactly: "code", "output", "screenshots", "figures",
  "explanation". "Include screenshots of the output" -> show ["screenshots"];
  "no explanations, just the code" -> hide ["explanation"]. Empty when the
  message says nothing about what the answers contain. These are about the
  report, never the code, so they never go in notes.
- datasets: data the DOCUMENT tells the student to obtain, which is not in the
  document itself. Copy each reference exactly as written, one per entry:
    a link            "https://example.edu/data/sales.csv"
    a Kaggle dataset  "uciml/iris"  or the full kaggle.com URL as written
    a named file      "housing.csv", when the manual says to use a file by name
  Leave EMPTY when the manual supplies its data inline, generates it in code,
  uses a library's built-in dataset, or says the student may pick any dataset.
  "Use any dataset of your choice" is NOT a reference -- it names nothing.
  Do not invent a URL, do not guess a Kaggle slug, and do not put a description
  here: only text the document actually contains. A paragraph ending in
  "(link: ... -> https://...)" carries a hyperlink; copy that URL.
- data_unlinked: datasets the DOCUMENT tells the student to obtain but gives NO
  link, slug or file name for anywhere -- e.g. "the Superstore dataset from
  Kaggle" when no link appears. One short description each. Empty when every
  dataset it names has a link or a file name, or when any dataset will do.
- prerequisites: things the tasks rely on from OUTSIDE this document that
  nothing here supplies: results, code, decisions or files from a PREVIOUS lab
  or assignment, or something the student made earlier. "Restate your Lab 02
  query customer" relies on Lab 02's results. One entry per thing:
    what           short name, under 50 characters: "Your Lab 02 results"
    detail         what exactly, under 120 characters: "query customer,
                   filtering decisions, nearest customers from Task 4/5"
    tasks          the task_numbers that rely on it
    quote          the document's words that refer to it, copied exactly,
                   under 80 characters
    recreatable    true when it can be rebuilt from what this lab provides --
                   the earlier work used the same dataset this lab uses -- or
                   when the document itself allows rebuilding it ("if you did
                   not retain these, briefly reconstruct ..."). false only for
                   what just the student has: their photo, their survey
                   answers, their own earlier code.
    recreate_from  when recreatable, from what, under 60 characters: "the
                   Online Retail dataset, as the manual allows"
  NOT a prerequisite: a dataset (that belongs in datasets), an earlier task IN
  THIS document, a library, general knowledge, or anything the student's
  request already supplies. Empty for a self-contained lab, which is most labs.

Return ONLY a JSON object matching this schema exactly:

{schema}

The student's request (may be empty):

{request}

Document:

{document}
"""


class ExtractedNeed(BaseModel):
    need: str | None = ""
    why: str | None = ""
    evidence: str | None = ""


class ExtractedSlot(BaseModel):
    # Loose, like every field a check reads afterwards: a malformed box must
    # cost that box, never an ingest retry.
    part: str | None = "any"
    idx: int | str | None = None
    quote: str | None = ""


class ExtractedTask(BaseModel):
    task_number: int = Field(description="1-based position in document order")
    title: str
    statement: str = Field(description="complete, self-contained requirement text")
    sample_inputs: list[str] = Field(default_factory=list)
    wants_explanation: bool = False
    wants_screenshot: bool | str | None = False
    screenshot_evidence: str | None = ""
    answer_slots: list[ExtractedSlot | str] = Field(default_factory=list)
    written_questions: list[str] = Field(default_factory=list)
    needs_code: bool = True
    # A string, not a Literal: an unexpected value must degrade to "standard",
    # not fail validation and burn an ingest retry -- see `lab_number` below.
    effort: str = "standard"
    anchor_idx: int = Field(description="index of the task's LAST paragraph")
    anchor_quote: str = Field(description="first 40 chars at anchor_idx, verbatim")
    # LOOSE ON PURPOSE, like `effort`: these feed a gate that can refuse a
    # whole lab, and a malformed value must degrade to "no requirement", never
    # fail validation and burn an ingest retry. Grounding happens afterwards.
    language: str | None = "python"
    language_evidence: str | None = ""
    needs: list[ExtractedNeed | str] = Field(default_factory=list)
    libraries: list[str] = Field(default_factory=list)


class ExtractedPrerequisite(BaseModel):
    what: str | None = ""
    detail: str | None = ""
    tasks: list[int | str] = Field(default_factory=list)
    quote: str | None = ""
    recreatable: bool | str | None = False
    recreate_from: str | None = ""


class ExtractedIntent(BaseModel):
    """What the student asked for, resolved against the tasks just read.

    Resolving it inside the extraction call is what makes it trustworthy: the
    model picks task numbers from the list it is producing in the same breath,
    so it cannot name a task that does not exist. Same self-verification idea as
    the anchor quote.
    """

    tasks_wanted: list[int] = Field(default_factory=list, description="empty = all")
    artifacts: list[str] = Field(default_factory=list, description="empty = default")
    notes: str = ""
    datasets: list[str] = Field(
        default_factory=list, description="data the DOCUMENT names and does not supply"
    )
    data_unlinked: list[str] = Field(
        default_factory=list, description="datasets it requires with no link or file"
    )
    prerequisites: list[ExtractedPrerequisite | str] = Field(
        default_factory=list, description="inputs from outside this document"
    )
    show: list[str] = Field(default_factory=list, description="answer parts to add")
    hide: list[str] = Field(default_factory=list, description="answer parts to leave out")


class ExtractedLab(BaseModel):
    # NULLABLE ON PURPOSE. A document that is not a lab has no lab number and
    # no lab title, and the model correctly answers `null` for both. Typing
    # them as plain `str` made Pydantic reject that entirely correct reply --
    # so a confidently-classified CV failed validation, burned all three
    # retries, and surfaced as a parse error instead of "this is a CV".
    lab_number: str | None = ""
    title: str | None = ""
    course: str | None = None
    tasks: list[ExtractedTask] = Field(default_factory=list)
    document_kind: str = "lab"
    confidence: float = 1.0
    what_this_is: str = ""
    intent: ExtractedIntent = Field(default_factory=ExtractedIntent)


@dataclass
class AnchorRepair:
    task_id: str
    claimed: int
    corrected: int
    reason: str


def _starts(paragraph, quote: str) -> bool:
    """Does this paragraph begin with the quote -- as stored, or as the model
    saw it with its links appended? A short paragraph that is mostly a link
    can only be quoted in the second form."""
    head = quote[:30]
    shown = paragraph.shown() if hasattr(paragraph, "shown") else paragraph.text
    return paragraph.text.strip().startswith(head) or shown.strip().startswith(head)


def _reconcile_anchor(
    extracted: ExtractedTask, manual: RawManual
) -> tuple[int, AnchorRepair | None]:
    """Trust the quote over the index. Models quote well and count badly."""
    paragraphs = manual.paragraphs
    quote = (extracted.anchor_quote or "").strip()
    task_id = f"task{extracted.task_number}"

    def matches(idx: int) -> bool:
        if not (0 <= idx < len(paragraphs)):
            return False
        return _starts(paragraphs[idx], quote) if quote else False

    if matches(extracted.anchor_idx):
        return extracted.anchor_idx, None

    if not (0 <= extracted.anchor_idx < len(paragraphs)):
        reason = f"index {extracted.anchor_idx} out of range"
    else:
        reason = "quote does not match the paragraph at that index"

    if quote:
        # Exact prefix match anywhere in the document.
        for p in paragraphs:
            if _starts(p, quote):
                return p.idx, AnchorRepair(task_id, extracted.anchor_idx, p.idx, reason)

        # Fall back to closest fuzzy match.
        texts = [p.text.strip() for p in paragraphs]
        close = difflib.get_close_matches(quote, texts, n=1, cutoff=0.6)
        if close:
            idx = texts.index(close[0])
            return idx, AnchorRepair(task_id, extracted.anchor_idx, idx, reason + " (fuzzy)")

    if 0 <= extracted.anchor_idx < len(paragraphs):
        return extracted.anchor_idx, None

    raise SpecError(
        f"{task_id}: anchor_idx {extracted.anchor_idx} is out of range and its "
        f"quote {quote[:40]!r} matches no paragraph"
    )


def to_labspec(
    extracted: ExtractedLab, manual: RawManual
) -> tuple[LabSpec, dict[str, int], list[AnchorRepair]]:
    """Returns the spec, the anchor side table, and any repairs made.

    Anchors come back separately rather than on each `Task` because they are a
    coordinate into one specific .docx -- meaningless for a PDF, a notebook or
    a pasted lab, and needed only by the DOCX emitter.
    """
    tasks: list[Task] = []
    anchors: dict[str, int] = {}
    repairs: list[AnchorRepair] = []

    for item in sorted(extracted.tasks, key=lambda t: t.task_number):
        anchor, repair = _reconcile_anchor(item, manual)
        if repair:
            repairs.append(repair)
        task_id = f"task{item.task_number}"
        anchors[task_id] = anchor
        questions = [q.strip() for q in item.written_questions if str(q).strip()][:6]
        tasks.append(
            Task(
                id=task_id,
                title=item.title,
                statement=item.statement,
                sample_inputs=list(item.sample_inputs),
                wants_explanation=item.wants_explanation or bool(questions),
                written_questions=questions,
                needs_code=bool(item.needs_code),
                effort="basic" if str(item.effort).strip().lower() == "basic" else "standard",
            )
        )

    if not tasks:
        raise SpecError(f"no tasks found in {manual.path.name}")

    spec = LabSpec(
        lab_number=extracted.lab_number or "",
        title=extracted.title or "",
        course=extracted.course,
        tasks=tasks,
        skipped_images=list(manual.image_names),
    )

    # ANCHORS ONLY MEAN SOMETHING FOR A .docx. A line index into a .txt, or a
    # cell index in a notebook, is not a Word paragraph index -- but it looks
    # exactly like one, so the DOCX emitter cheerfully took the annotate-in-
    # place branch and tried to open a plain text file as a Word document.
    #
    # The reconciliation above still runs for every format, because verifying
    # the model's quote against the document is a quality check worth having
    # regardless. Only the coordinates are dropped.
    if not produces_anchors(manual.path):
        anchors = {}

    return spec, anchors, repairs


SCHEMA_HINT = """{
  "document_kind": "lab | notebook_lab | other",
  "confidence": 0.9,
  "what_this_is": "short phrase describing the document",
  "lab_number": "03",
  "title": "string",
  "course": "string or null",
  "tasks": [
    {
      "task_number": 1,
      "title": "string",
      "statement": "string",
      "sample_inputs": ["5", "3"],
      "wants_explanation": false,
      "wants_screenshot": false,
      "screenshot_evidence": "",
      "written_questions": [],
      "needs_code": true,
      "effort": "basic",
      "anchor_idx": 8,
      "anchor_quote": "first 40 chars of the paragraph at anchor_idx",
      "answer_slots": [],
      "language": "python",
      "language_evidence": "",
      "needs": [],
      "libraries": []
    }
  ],
  "intent": {
    "tasks_wanted": [],
    "artifacts": [],
    "notes": "",
    "datasets": [],
    "data_unlinked": [],
    "prerequisites": [],
    "show": [],
    "hide": []
  }
}"""


INGEST_ATTEMPTS = 3


@dataclass
class Reading:
    """The result of looking at an upload once.

    Everything the one ingest call learned: what the document is, how sure we
    are, the tasks if there are any, where they sit in the source .docx, and
    what the student asked to be done with them. `spec` is None when the
    document is not an assignment at all -- which is an answer, not a failure.
    """

    kind: str
    confidence: float
    what_this_is: str
    intent: Intent
    usage: Usage
    spec: LabSpec | None = None
    anchors: dict[str, int] = dataclass_field(default_factory=dict)
    repairs: list[AnchorRepair] = dataclass_field(default_factory=list)
    #: task id -> what it needs beyond Python, after grounding. A side table
    #: like `anchors`: only the capability gate reads it, so `Task` does not
    #: carry it and the manifest does not persist it.
    requirements: dict[str, Requirement] = dataclass_field(default_factory=dict)
    #: task id -> what its answer contains and the manual's boxes for it. A
    #: side table too: only the emitters read it, never the solver.
    plans: dict[str, AnswerPlan] = dataclass_field(default_factory=dict)

    @property
    def is_lab(self) -> bool:
        return self.spec is not None and bool(self.spec.tasks)


def _intent_from(
    extracted: ExtractedLab, known_ids: list[str], request: str = "", manual=None
) -> Intent:
    """Fold the model's `intent` block into the typed request object."""
    wanted = [f"task{n}" for n in extracted.intent.tasks_wanted]
    wanted = [t for t in wanted if t in known_ids]
    artifacts = [a.strip().lower().lstrip(".") for a in extracted.intent.artifacts]
    artifacts = [a for a in artifacts if a]

    # BACKSTOP. Observed in the wild: given "provide the completed lab as an
    # executed .ipynb notebook file", the model filed the whole sentence under
    # `notes` -- a CODE instruction -- and left `artifacts` empty, so the run
    # produced a .docx and told the solver to write the notebook itself. A
    # literal ".ipynb" is not a judgement call, so when the model expressed no
    # preference at all we read the request directly. A preference it DID
    # express is never overridden.
    if not artifacts:
        artifacts = detect_formats(request)
    show, hide = _parts(extracted.intent.show), _parts(extracted.intent.hide)
    # The same backstop, for the same reason: only when the model said nothing.
    if not show and not hide:
        show, hide = detect_parts(request)
    return Intent(
        show=show,
        hide=hide,
        kind=extracted.document_kind if extracted.document_kind in
        ("lab", "notebook_lab", "other") else "lab",
        confidence=max(0.0, min(1.0, float(extracted.confidence))),
        what_this_is=extracted.what_this_is.strip(),
        task_ids=wanted or None,
        artifacts=artifacts,
        notes=extracted.intent.notes.strip(),
        datasets=_datasets(extracted, manual, request),
        data_unlinked=[
            d.strip() for d in extracted.intent.data_unlinked if str(d).strip()
        ][:3],
        prerequisites=_prerequisites(extracted, manual, known_ids),
    )


# --- grounding -------------------------------------------------------------
#
# THE GATE CAN REFUSE A WHOLE LAB, SO WHAT FEEDS IT MUST BE READ, NOT GUESSED.
# The model is asked to QUOTE the manual for every language other than Python,
# every need and every prerequisite -- the anchor_quote technique again: have
# it produce something it is good at (copying visible text) next to the
# judgement it may get wrong, and verify one against the other. A claim whose
# quote is not in the document is dropped, which fails OPEN: back to the
# behaviour before the gate existed, never a refusal built on a hallucination.
#
# Comparison is on lowercase letters and digits only, because a PDF extracts
# "Task" as "T ask" and a model normalises quotes and dashes.

_SQUASH = re.compile(r"[^a-z0-9]+")
MIN_QUOTE = 6
QUOTE_HEAD = 40

# Evidence that shows the model mistook something ordinary for a need. Each is
# a phrase the prompt already rules out; a guard is the guarantee behind it.
_NOT_A_NEED = {
    # Fetching the lab's dataset happens in the pipeline, before any code runs.
    # Not "download" alone: "downloads the JSON at <url> at run time" IS live.
    "internet": re.compile(r"dataset|data set|kaggle|\.csv|\.xlsx|\.zip", re.I),
    # A chart is saved to a file; plt.show() is replaced by savefig.
    "display": re.compile(r"plot|chart|graph|plt\.|figure|visuali[sz]|histogram|heatmap", re.I),
    # Notebooks, IDEs and spreadsheet files are not "other software".
    "software": re.compile(
        r"jupyter|colab|notebook|\bexcel\b|xlsx|spyder|vs ?code|pycharm|anaconda|\bide\b",
        re.I,
    ),
}


def _squash(text: str) -> str:
    return _SQUASH.sub("", str(text or "").lower())


def _haystack(manual, request: str = "") -> list[str]:
    """Every paragraph as stored and as the model saw it (links appended)."""
    if manual is None:
        return [_squash(request)] if request else []
    texts: list[str] = []
    for p in manual.paragraphs:
        texts.append(_squash(p.text))
        shown = p.shown() if hasattr(p, "shown") else ""
        if shown and shown != p.text:
            texts.append(_squash(shown))
    if request:
        texts.append(_squash(request))
    return texts


def _grounded(quote: str, haystack: list[str]) -> bool:
    head = _squash(quote)[:QUOTE_HEAD]
    return len(head) >= MIN_QUOTE and any(head in text for text in haystack)


def _mentions(name: str, haystack: list[str]) -> bool:
    """Does the document name this library, under any of its spellings?"""
    from labsagent.capabilities import LIBRARY_ALIASES, normalise_library

    wanted = normalise_library(name)
    spellings = {wanted, str(name or "").lower()} | {
        alias for alias, target in LIBRARY_ALIASES.items() if target == wanted
    }
    squashed = {_squash(s) for s in spellings if len(_squash(s)) >= 3}
    return any(s in text for s in squashed for text in haystack)


def _requirements(
    extracted: ExtractedLab, manual, request: str, data_refs=()
) -> dict[str, Requirement]:
    """Each task's needs beyond Python, keeping only what the document backs up.

    `data_refs` are the dataset references this reading resolved. An
    "internet" need whose evidence is one of them is the pipeline's job --
    it fetches data before any code runs -- not the program's.
    """
    haystack = _haystack(manual, request)
    refs = [_squash(r)[:QUOTE_HEAD] for r in data_refs if len(_squash(r)) >= MIN_QUOTE]
    out: dict[str, Requirement] = {}
    for item in extracted.tasks:
        language = str(item.language or "python").strip().lower()
        evidence = str(item.language_evidence or "")
        # A claimed non-Python language needs a quote from the document, and a
        # quote that itself says "Python" ("a Python program that generates an
        # HTML report") is the model contradicting its own label.
        if not is_python(language) and (
            not _grounded(evidence, haystack) or "python" in evidence.lower()
        ):
            language = SOLVER_LANGUAGE

        needs: list[tuple[str, str]] = []
        for raw in item.needs:
            if isinstance(raw, str):
                continue  # a bare word carries no evidence to check
            need = str(raw.need or "").strip().lower()
            proof = str(raw.evidence or "")
            if need not in NEEDS or not _grounded(proof, haystack):
                continue
            veto = _NOT_A_NEED.get(need)
            if veto is not None and veto.search(proof):
                continue
            if need == "internet" and any(ref in _squash(proof) for ref in refs):
                continue
            if need not in {n for n, _ in needs}:
                needs.append((need, str(raw.why or "").strip()[:80]))

        libraries = tuple(
            dict.fromkeys(
                normalise_library(lib)
                for lib in item.libraries
                if str(lib or "").strip() and _mentions(lib, haystack)
            )
        )
        if not is_python(language) or needs or libraries:
            out[f"task{item.task_number}"] = Requirement(
                language=language, needs=tuple(needs), libraries=libraries
            )
    return out


_DOC_NAME = re.compile(
    r"\b(lab|assignment|homework|project|practical|experiment)\s*(?:no\.?\s*)?0*(\d+)\b", re.I
)


def _documents_named(text: str, own: str = "") -> set[tuple[str, int]]:
    """{("lab", 2)} for "Lab 02" -- another document this text names, never
    the lab's own number."""
    own_number = int(re.sub(r"\D", "", own) or -1)
    return {
        (kind.lower(), int(number))
        for kind, number in _DOC_NAME.findall(text or "")
        if not (kind.lower() == "lab" and int(number) == own_number)
    }


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "y")
    return bool(value)


def _prerequisites(extracted: ExtractedLab, manual, known_ids: list[str]) -> list[Needed]:
    """What the tasks need from outside this document, grounded, at most three.

    Grounded against the DOCUMENT only, not the request: a prerequisite is
    something the manual relies on, and one the request supplies is not
    missing.
    """
    if manual is None:
        return []
    haystack = _haystack(manual)
    known = set(known_ids)
    found: list[Needed] = []
    for raw in extracted.intent.prerequisites:
        if isinstance(raw, str):
            continue
        what = str(raw.what or "").strip()
        if not what or not _grounded(str(raw.quote or ""), haystack):
            continue
        task_ids: list[str] = []
        for number in raw.tasks:
            digits = re.sub(r"\D", "", str(number))
            task_id = f"task{int(digits)}" if digits else ""
            if task_id in known and task_id not in task_ids:
                task_ids.append(task_id)
        if not task_ids:
            continue
        # BACKSTOP, NEVER AN OVERRIDE. Which tasks the model lists varies run
        # to run (Lab 03: tasks 1,2,5,6 on one reading, 1,2 on the next), and a
        # task left off is a task told nothing -- free to invent Lab 02 again.
        # A task whose own statement names the same outside document ("Lab
        # 02") relies on it by definition, so it is added here.
        named = _documents_named(" ".join([what, str(raw.detail or ""), str(raw.quote or "")]),
                                 extracted.lab_number or "")
        if named:
            for item in sorted(extracted.tasks, key=lambda t: t.task_number):
                task_id = f"task{item.task_number}"
                if (task_id in known and task_id not in task_ids
                        and named & _documents_named(item.statement, extracted.lab_number or "")):
                    task_ids.append(task_id)
            task_ids.sort(key=lambda t: int(t.removeprefix("task")))
        recreatable = _truthy(raw.recreatable)
        found.append(
            Needed(
                what=what[:60],
                detail=str(raw.detail or "").strip()[:160],
                task_ids=tuple(task_ids),
                recreatable=recreatable,
                recreate_from=str(raw.recreate_from or "").strip()[:80] if recreatable else "",
            )
        )
    return found[:3]


def _datasets(extracted: ExtractedLab, manual, request: str) -> list[str]:
    """The model's references, checked against the document, plus the ones it
    missed. See `data.refs.reconcile_datasets` for why both halves exist."""
    model_refs = [r.strip() for r in extracted.intent.datasets if str(r).strip()]
    if manual is None:
        return model_refs
    from labsagent.data.refs import reconcile_datasets

    texts = [p.text for p in manual.paragraphs]
    texts += [url for p in manual.paragraphs for _, url in getattr(p, "links", ())]
    return reconcile_datasets(model_refs, texts, request)


#: Spellings a model or a student uses for each part.
_PART_NAMES = {
    "screenshot": "screenshots", "screenshots": "screenshots", "snapshots": "screenshots",
    "explanation": "explanation", "explanations": "explanation",
    "figure": "figures", "figures": "figures", "plot": "figures", "plots": "figures",
    "chart": "figures", "charts": "figures",
    "code": "code", "output": "output", "outputs": "output",
}


def _parts(values) -> list[str]:
    out: list[str] = []
    for value in values or []:
        part = _PART_NAMES.get(str(value or "").strip().lower())
        if part in PARTS and part not in out:
            out.append(part)
    return out


def _slots_for(item, position, task_ids, anchors, manual, claimed: set[int]) -> tuple[Slot, ...]:
    """The manual's own answer boxes for one task, each one checked.

    The model is good at seeing "this empty cell is where Task 2's code goes"
    and bad at counting, so every box must survive three checks or it is
    dropped, which falls back to the old behaviour (after the task), never to
    a wrong place:
    - it lies between the previous task's anchor and the next task's;
    - a box with text starts with the quote the model copied (`_starts`), and
      a box without text is an empty CELL -- a blank line is not a box;
    - no other task already claimed it.
    """
    paragraphs = manual.paragraphs
    low = anchors.get(task_ids[position - 1], -1) if position > 0 else -1
    high = (
        anchors.get(task_ids[position + 1], len(paragraphs))
        if position + 1 < len(task_ids)
        else len(paragraphs)
    )
    found: list[Slot] = []
    for raw in item.answer_slots or []:
        if isinstance(raw, str):
            continue
        try:
            idx = int(str(raw.idx).strip())
        except (TypeError, ValueError):
            continue
        if not (low < idx < min(high, len(paragraphs))) or idx in claimed:
            continue
        paragraph = paragraphs[idx]
        quote = str(raw.quote or "").strip()
        if paragraph.text.strip():
            if not quote or not _starts(paragraph, quote):
                continue
        elif not paragraph.in_table:
            continue
        # A LABEL'S BOX IS THE EMPTY CELL AFTER IT. Observed live: asked for
        # the box, the model pointed at the "Code" label cell on one run and at
        # the empty cell beside it on the next -- and writing under the label
        # puts the code in the label column. The prompt asks for the empty
        # cell; this makes it so.
        following = idx + 1
        if (
            paragraph.in_table
            and 0 < len(paragraph.text.strip()) <= 30
            and following < min(high, len(paragraphs))
            and paragraphs[following].in_table
            and not paragraphs[following].text.strip()
            and following not in claimed
        ):
            idx = following
        part = str(raw.part or "any").strip().lower()
        part = "output" if part.startswith("screenshot") else part
        claimed.add(idx)
        found.append(Slot(idx=idx, part=part if part in SLOT_PARTS else "any"))
        if len(found) == 4:
            break
    return tuple(sorted(found, key=lambda s: s.idx))


def _answer_plans(extracted: ExtractedLab, manual, anchors, intent: Intent) -> dict[str, AnswerPlan]:
    """What each task's answer contains and where the manual wants it.

    THE DEFAULT IS WHAT THE OWNER DECIDED: code and output as text. A
    screenshot needs the manual's own words asking for one (quoted and
    grounded, like every claim a decision rests on), an explanation needs the
    task to ask for it, and the student's message wins over both. Boxes exist
    only for a .docx, the one format whose paragraphs we can write into.
    """
    haystack = _haystack(manual)
    order = sorted(extracted.tasks, key=lambda t: t.task_number)
    task_ids = [f"task{t.task_number}" for t in order]
    claimed: set[int] = set()
    plans: dict[str, AnswerPlan] = {}
    for position, item in enumerate(order):
        screenshots = _truthy(item.wants_screenshot) and _grounded(
            str(item.screenshot_evidence or ""), haystack
        )
        include = Include(screenshots=screenshots, explanation=bool(item.wants_explanation))
        slots = (
            _slots_for(item, position, task_ids, anchors, manual, claimed) if anchors else ()
        )
        plans[task_ids[position]] = AnswerPlan(
            include=include.changed(intent.show, intent.hide), slots=slots
        )
    return plans


def extract_labspec(manual: RawManual, model, request: str = "") -> Reading:
    """One constrained call that classifies, extracts and resolves the request.

    NOTE ON METHOD. DeepSeek V4.1 runs in thinking mode and rejects a forced
    `tool_choice`, so both `function_calling` and `json_schema` fail with
    "Thinking mode does not support this tool_choice". `json_mode` works, but it
    only guarantees SYNTACTICALLY VALID JSON -- schema conformance is not
    enforced server-side.

    So enforcement moves to us: the schema goes in the prompt, and Pydantic is
    the real validator. That is the trade with json_mode everywhere, not just
    here -- know which of the three you are getting.

    WHY THREE JOBS IN ONE CALL. Classifying the document, listing its tasks and
    resolving "only task 3, as a notebook" are all the same act of reading it.
    Splitting them across calls would pay for that reading more than once, and
    would let the request name a task the extraction never found.
    """
    structured = model.with_structured_output(ExtractedLab, method="json_mode")
    prompt = EXTRACTION_PROMPT.format(
        schema=SCHEMA_HINT,
        request=(request or "").strip() or "(no request given)",
        document=manual.as_numbered_text(),
    )

    # with_structured_output swallows the AIMessage, so usage_metadata never
    # reaches the caller. A callback handler is the supported way to observe it
    # -- do NOT hardcode a previously measured figure, which silently goes stale
    # and then lies in your cost report.
    handler = UsageMetadataCallbackHandler()

    # RETRY, because json_mode has no server-side schema enforcement. Observed
    # in the wild: the model answered with the literal response_format object,
    # {"type": "json_object"}, which is valid JSON and wrong. Ingest is a single
    # call that gates the entire run, so one bad sample must not be terminal --
    # the cheapest fix for a stochastic failure is another sample.
    last: Exception | None = None
    for attempt in range(1, INGEST_ATTEMPTS + 1):
        try:
            extracted = structured.invoke(prompt, config={"callbacks": [handler]})
        except Exception as exc:  # noqa: BLE001 - classified below, then re-raised
            last = exc
            continue

        if extracted.tasks:
            spec, anchors, repairs = to_labspec(extracted, manual)
            intent = _intent_from(extracted, [t.id for t in spec.tasks], request, manual)
            return Reading(
                kind=extracted.document_kind,
                confidence=extracted.confidence,
                what_this_is=extracted.what_this_is,
                intent=intent,
                usage=_usage_from_handler(handler),
                spec=spec,
                anchors=anchors,
                repairs=repairs,
                # Built here, not in `to_labspec`, whose three-value return a
                # dozen tests unpack.
                requirements=_requirements(
                    extracted, manual, request, [*intent.datasets, *intent.data_unlinked]
                ),
                plans=_answer_plans(extracted, manual, anchors, intent),
            )

        # NO TASKS. Which of the two reasons matters, and they used to be
        # conflated: "this is not an assignment" was retried three times and
        # then raised `SpecError`, so uploading a CV cost three extractions and
        # produced a stack-trace string in the chat. A confident "other" is a
        # finished answer -- return it and let the caller say so kindly.
        if extracted.document_kind == "other":
            return Reading(
                kind="other",
                confidence=extracted.confidence,
                what_this_is=extracted.what_this_is,
                intent=_intent_from(extracted, [], request),
                usage=_usage_from_handler(handler),
            )

        # Claimed to be a lab but listed nothing: a failed extraction, retry.
        last = SpecError(f"attempt {attempt} returned no tasks")

    raise SpecError(f"extraction failed for {manual.path.name}: {last}") from last


def _usage_from_handler(handler) -> Usage:
    """Flatten the handler's per-model dict into one Usage."""
    total = Usage()
    for model_name, meta in (handler.usage_metadata or {}).items():
        total.model = model_name
        total.calls += 1
        total.input_tokens += meta.get("input_tokens", 0)
        total.output_tokens += meta.get("output_tokens", 0)
        total.cached_tokens += (meta.get("input_token_details") or {}).get("cache_read", 0)
    return total
