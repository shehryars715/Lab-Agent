"""What the user actually asked for.

WHY THIS TYPE EXISTS. The only user input that ever reached the engine was a
free-text `instructions` string, and `apply_instructions` stapled it onto every
*task statement* -- i.e. onto the solver's prompt. So "just give me a .py" was
appended to the code-writing instructions for every task, where it could change
how the code was written but could never change what was produced. There was no
channel to emission at all, and format was decided by an f-string.

`Intent` is that channel. It is produced by the model during ingest, it is
checked against the task list that same call read, and everything downstream --
scoping, emitting, what the chat says -- reads it instead of guessing.

WHY IT ALSO CARRIES THE CLASSIFICATION. Deciding "is this a lab?" and deciding
"what should I make from it?" are the same judgement about the same document,
made from the same reading of it. Splitting them across two model calls would
pay twice for one act of comprehension.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Literal

from labsagent.models import LabSpec, Task

DocumentKind = Literal["lab", "notebook_lab", "other"]

#: Below this, we ask rather than assume. A model's self-reported confidence is
#: a weak signal, so this is deliberately permissive: it exists to catch the
#: genuinely ambiguous upload, not to second-guess every run.
ASK_BELOW = 0.5

TASK_REF = re.compile(r"\btask\s*(\d+)\b", re.IGNORECASE)


def referenced_task_ids(task: Task) -> set[str]:
    """Task ids this statement points at, excluding itself.

    Lives here rather than in the orchestrator because it now has two jobs: it
    tells the solver which earlier solution to show the agent, and it tells
    `scope()` which tasks a subset secretly depends on.
    """
    own = task.id.removeprefix("task")
    return {f"task{n}" for n in TASK_REF.findall(task.statement) if n != own}


#: Unambiguous ways of naming a deliverable. Deliberately narrow: `script`
#: alone is not here, because "the script should handle negatives" is a note
#: about the code, not a request for a .py.
FORMAT_HINTS: dict[str, tuple[str, ...]] = {
    "ipynb": (r"\.ipynb\b", r"\bipynb\b", r"\bnote\s?books?\b", r"\bjupyter\b", r"\bcolab\b"),
    "py": (r"\.py\b", r"\bpython\s+(?:script|file|source)\b", r"\bas\s+a\s+script\b"),
    "md": (r"\.md\b", r"\bmarkdown\b"),
    "docx": (r"\.docx\b", r"\bdocx\b", r"\bword\s+(?:document|doc|report|file)\b"),
    "zip": (r"\.zip\b", r"\bzip\b", r"\barchives?\b"),
}

#: "no zip", "without a zip", "do not give me a notebook".
_NEGATED = re.compile(
    r"(?:\bno\b|\bnot\b|\bwithout\b|\bskip\b|\bdon'?t\b|\bexcept\b)[^.]{0,20}$",
    re.IGNORECASE,
)


def detect_formats(text: str) -> list[str]:
    """Deliverables named outright in a request.

    WHY THIS EXISTS ALONGSIDE THE MODEL. The ingest call is asked to sort a
    request into scope, artifacts and notes, and it does that well for phrasing
    it recognises. It does not do it reliably: given "provide the completed lab
    as an executed .ipynb notebook file that includes the run outputs" it filed
    the whole sentence under `notes` -- a CODE instruction -- so the run emitted
    a .docx, and the sentence was handed to the solver as though writing the
    notebook were its job.

    A literal ".ipynb" is not a judgement call. Where the signal is
    unambiguous, a regex is more reliable than a model, and this runs only as a
    backstop when the model expressed no preference at all, so a request it DID
    understand is never second-guessed.
    """
    found: list[str] = []
    low = (text or "").lower()
    for name, patterns in FORMAT_HINTS.items():
        for pattern in patterns:
            for match in re.finditer(pattern, low):
                # "and no zip" must not be read as "and a zip".
                if _NEGATED.search(low[max(0, match.start() - 24):match.start()]):
                    continue
                if name not in found:
                    found.append(name)
                break
    return found


@dataclass(frozen=True)
class Intent:
    """The resolved request. `task_ids` of None means "everything"."""

    kind: DocumentKind = "lab"
    confidence: float = 1.0
    what_this_is: str = ""
    task_ids: list[str] | None = None
    artifacts: list[str] = field(default_factory=list)
    notes: str = ""

    @property
    def is_lab(self) -> bool:
        return self.kind in ("lab", "notebook_lab")

    @property
    def uncertain(self) -> bool:
        return self.confidence < ASK_BELOW

    def with_notes(self, extra: str) -> "Intent":
        extra = (extra or "").strip()
        if not extra:
            return self
        joined = f"{self.notes}\n{extra}".strip() if self.notes else extra
        return replace(self, notes=joined)


def scope(spec: LabSpec, intent: Intent) -> tuple[LabSpec, list[str]]:
    """Narrow the spec to what was asked for, plus what that needs to run.

    Returns the narrowed spec and the ids that were pulled in but not
    requested, so the caller can say so out loud. Asking for task 3 when task 3
    uses the dataframe task 1 loaded has three possible answers -- solve it
    alone and ship something that cannot run, refuse, or quietly include the
    prerequisite. We include it and announce it, because an artifact that does
    not run is not a deliverable.

    Unknown ids are ignored rather than raising: the model picks them from the
    task list it just read, so a miss means the request did not match anything,
    and falling back to "solve everything" is the safer failure.
    """
    if intent.task_ids is None:
        return spec, []

    by_id = {t.id: t for t in spec.tasks}
    wanted = {tid for tid in intent.task_ids if tid in by_id}
    if not wanted:
        return spec, []

    # Transitive closure: a prerequisite may itself have a prerequisite.
    keep = set(wanted)
    frontier = set(wanted)
    while frontier:
        nxt: set[str] = set()
        for tid in frontier:
            for ref in referenced_task_ids(by_id[tid]):
                if ref in by_id and ref not in keep:
                    keep.add(ref)
                    nxt.add(ref)
        frontier = nxt

    narrowed = replace(spec, tasks=[t for t in spec.tasks if t.id in keep])
    pulled = sorted(keep - wanted, key=lambda t: [x.id for x in spec.tasks].index(t))
    return narrowed, pulled
