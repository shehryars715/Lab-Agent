"""How one task is laid out: designed alternatives, chosen per lab.

WHY. Every deliverable used to have one skeleton -- `Code:`, `Output:`, one
paragraph -- whatever the lab was, so the fourth report looked exactly like the
first. The cover already solved this the right way (see report/cover.py): a few
DESIGNED alternatives that are all fine to submit, and the model's only job is
to pick one. This extends that rule from the cover to the body. The model never
composes a layout from primitives; it chooses a name from `STYLES`.

`arrange()` is the one place a style becomes an order of blocks, so every
emitter that renders blocks gets every style for free. `classic` returns
`blocks_for()` untouched, which is what keeps the anchored Word report
byte-identical for anyone who does not ask for anything else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from labsagent.blocks import FIGURE, SCREENSHOT, Block, blocks_for, leading_prose

STYLES = ("classic", "walkthrough", "findings", "compact")

#: The cover designs (report/cover.py owns the renderers; the names live here
#: so the spec can validate a request without importing python-docx).
LAYOUTS = ("classic", "rule", "banner", "split")

#: Every format a student can ask for. `emit.REGISTRY` must match this exactly
#: (a test holds them together); it lives here so validating a request never
#: imports the emitters.
FORMATS = ("docx", "ipynb", "py", "md", "zip", "pdf", "html", "txt", "files")

FORMAT_NAMES = {
    "docx": "the Word report",
    "ipynb": "the notebook",
    "py": "the Python script",
    "md": "the markdown copy",
    "zip": "the zip",
    "pdf": "a PDF",
    "html": "a web page",
    "txt": "a plain-text copy",
    "files": "one code file per task",
}


# WHAT AN ANSWER CONTAINS AND WHERE IT GOES -- READ, NOT TEMPLATED.
#
# Every report used to carry the same parts in the same place: "Code:", every
# terminal screenshot, an explanation, all inserted straight after the task --
# whatever the manual asked for, and even when the manual had an answer box
# of its own (the annotator deliberately jumped OUT of a task's table, "so
# every task is presented identically"). The parts and the place are facts
# about the manual and the student's message, so the ingest call reads them,
# with quotes, and code only renders what was read.
#
# The defaults are the owner's rule (2026-10-02): code and its output as text,
# plus answers to the manual's written questions. Screenshots and explanation
# appear only when the manual or the student asks for them. This reverses the
# 2026-09-26 "terminal output is shown as a terminal picture" default; the
# picture rule still holds whenever screenshots ARE included.

#: Parts the student's message may switch on or off ("include screenshots",
#: "no explanation", "drop the task statements"). Written answers are not
#: here: the manual asked for them.
PARTS = ("code", "output", "screenshots", "figures", "explanation", "statement")

#: What a manual's answer box can be meant for, read from its own label.
SLOT_PARTS = ("code", "output", "answer", "any")


@dataclass(frozen=True)
class Include:
    """Which parts of one task's answer reach the deliverable."""

    code: bool = True
    output: bool = True
    screenshots: bool = False
    #: A chart the task asked for IS its output, so figures stay on.
    figures: bool = True
    explanation: bool = False
    #: The task's own text above its answer. Formats built from scratch print
    #: it; the annotated manual never does -- the manual is already there.
    statement: bool = True

    def changed(self, show=(), hide=()) -> "Include":
        """The student's words win over the manual's: shown, then hidden."""
        values = {part: getattr(self, part) for part in PARTS}
        values.update({p: True for p in show if p in values})
        values.update({p: False for p in hide if p in values})
        return Include(**values)


@dataclass(frozen=True)
class Slot:
    """A place the MANUAL made for an answer: an empty cell or a placeholder.

    `idx` is a paragraph index in the anchor coordinate space; `part` is what
    the box is for, from its own label, or "any".
    """

    idx: int
    part: str = "any"


@dataclass(frozen=True)
class AnswerPlan:
    """One task's answer: what it includes, the manual's boxes, if any, and
    a layout of its own when the student asked for one."""

    include: Include = field(default_factory=Include)
    slots: tuple[Slot, ...] = ()
    #: None means "the report's style". Set only by a request about this task.
    style: str | None = None

    def changed(self, show=(), hide=()) -> "AnswerPlan":
        return replace(self, include=self.include.changed(show, hide))


def select(blocks: list[Block], include: Include) -> list[Block]:
    """Drop the parts this answer does not include.

    What always stays: the lead line (which data was used), the honesty notes
    (not done, recreated, left out), errors and written answers. Those are not
    presentation choices; leaving them out would make the report say less than
    the truth.
    """
    out: list[Block] = []
    for block in blocks:
        if block.kind == "image" and block.role == SCREENSHOT and not include.screenshots:
            continue
        if block.kind == "image" and block.role == FIGURE and not include.figures:
            continue
        if block.kind == "code" and not include.code:
            continue
        if block.kind == "output" and not include.output:
            continue
        # The explanation is the only prose with no role: notes, answers and
        # the lead line all carry one.
        if block.kind == "prose" and not block.role and not include.explanation:
            continue
        out.append(block)
    return out


@dataclass(frozen=True)
class Vocabulary:
    """The words a style uses for its parts."""

    code: str
    output: str
    #: Heading over the written answers; empty means "no heading".
    answers: str
    #: Over a "Not done" note.
    status: str = "Status:"


VOCABULARY = {
    "classic": Vocabulary("Code:", "Output:", ""),
    "walkthrough": Vocabulary("Implementation", "Result", "Discussion"),
    "findings": Vocabulary("How it was computed", "Program output", "Findings"),
    "compact": Vocabulary("Program", "Run", ""),
}

#: The labels a student may rename or remove ("call it Source code").
LABEL_KEYS = ("code", "output", "answers", "status")


def vocabulary(style: str, labels: dict[str, str] | None = None) -> Vocabulary:
    """The style's words, with the student's own wording on top. An empty
    label means "no label at all"."""
    words = VOCABULARY.get(style, VOCABULARY["classic"])
    overrides = {k: v for k, v in (labels or {}).items() if k in LABEL_KEYS}
    return replace(words, **overrides) if overrides else words


def _is_answer(block: Block) -> bool:
    return block.kind == "prose" and block.role == "answer"


def arrange(outcome, style: str = "classic", include: Include | None = None) -> list[Block]:
    """The blocks of one task, in the order this style presents them, holding
    only the parts `include` allows (all of them when it is None).

    Leading prose a caller put first (the "Data used" line) always stays first:
    it is context for the whole task, not part of any one layout.
    """
    blocks = _ordered(outcome, style)
    return blocks if include is None else select(blocks, include)


def _ordered(outcome, style: str) -> list[Block]:
    blocks = blocks_for(outcome)
    if style not in STYLES or style == "classic":
        return blocks

    lead = leading_prose(outcome)
    rest = blocks[len(lead):]

    answers = [b for b in rest if _is_answer(b)]
    overview = [b for b in rest if b.kind == "prose" and not _is_answer(b)]
    work = [b for b in rest if b.kind != "prose"]

    # REAL SECTIONS replace the one fat block: each part of the program with
    # the output IT printed and the figures IT drew -- measured by an
    # instrumented run, never split up after the fact. `classic` keeps the
    # single block on purpose (see the module docstring).
    sections = getattr(outcome, "sections", None) or []
    if sections and outcome.status == "passed":
        work = []
        for part in sections:
            work.append(Block("code", text=part.get("code", ""), title=part.get("title", "")))
            if str(part.get("output") or "").strip():
                work.append(Block("output", text=part["output"], role="section"))
                # The section's terminal picture, right after its text: a
                # document shows the picture (`has_screenshot_twin`), a notebook
                # shows the text. Sections from before 2026-09-26 have no
                # pictures and fall back to text.
                for shot in part.get("screenshots") or []:
                    work.append(Block("image", path=Path(shot), role=SCREENSHOT))
            for figure in part.get("figures") or []:
                work.append(Block("image", path=Path(figure), role=FIGURE))

    if style == "findings":
        # The answer is the point of an analysis task; the code is the evidence.
        return lead + answers + overview + work
    if style == "walkthrough":
        # Say what the program does, show it working, then discuss.
        return lead + overview + work + answers
    # compact: the work, then the words, with nothing repeated.
    return lead + work + overview + answers


# ---------------------------------------------------------------------------
# THE REPORT SPEC (2026-10-22).
#
# WHY. Every look decision used to live somewhere different -- the cover layout
# and tagline and the body style on the briefing's Plan, what each answer holds
# in a loose `plans` dict, the formats on the Intent -- and each was reachable
# only through a short closed list of fields. A wish that fitted none of them
# ("change the cover", "call it Source code", "no task statements") was
# dropped, or answered with "I'm not sure what to change there". So the
# product felt rigid exactly where students expect to just say what they want.
#
# Now ONE object holds every look decision, every message edits it through ONE
# patch schema (`LookPatch`, shared by the briefing and the follow-up router),
# `apply` is the only thing that changes it, it is saved in the manifest so a
# change still works after a restart, and what changed is said by `receipt`,
# computed from the diff -- never from the model's promise.

#: Cover rows a patch may set by name. Anything else becomes a row of its own
#: ("Submitted to", "University").
COVER_LABELS = ("Course", "Lab Number", "Lab Title", "Instructor", "Lab Engineer", "Date", "Department")

_COVER_ALIASES = {
    "teacher": "Instructor",
    "professor": "Instructor",
    "lecturer": "Instructor",
    "instructor name": "Instructor",
    "engineer": "Lab Engineer",
    "lab engineer name": "Lab Engineer",
    "course name": "Course",
    "subject": "Course",
    "dept": "Department",
    "lab no": "Lab Number",
    "lab": "Lab Number",
    "submission date": "Date",
}

#: Who the student is travels on the profile, not the cover: it also names the
#: files, and a second copy on the cover would be a value on two channels.
IDENTITY_LABELS = {
    "name": "name",
    "student name": "name",
    "student": "name",
    "submitted by": "name",
    "cms id": "cms_id",
    "cms": "cms_id",
    "student id": "cms_id",
    "id": "cms_id",
    "registration number": "cms_id",
    "registration no": "cms_id",
    "reg no": "cms_id",
    "reg number": "cms_id",
    "roll number": "cms_id",
    "roll no": "cms_id",
    "section": "section",
    "class": "section",
    "program": "program",
    "programme": "program",
}

_LABEL_ALIASES = {
    "code": "code",
    "program": "code",
    "implementation": "code",
    "source": "code",
    "source code": "code",
    "output": "output",
    "outputs": "output",
    "result": "output",
    "results": "output",
    "run": "output",
    "answers": "answers",
    "answer": "answers",
    "discussion": "answers",
    "findings": "answers",
    "written answers": "answers",
    "status": "status",
    "not done": "status",
}

BODY_PT = (8.0, 16.0)
CODE_PT = (6.0, 14.0)
MAX_CUSTOM_ROWS = 12


@dataclass(frozen=True)
class CoverSpec:
    """Everything about the cover a student can change."""

    #: None is "auto": a cover, unless the manual already has a title page of
    #: its own (a second cover above the university's is the tell of a tool).
    show: bool | None = None
    layout: str = "classic"
    #: Replaces "Lab NN: Title" when set.
    title: str = ""
    tagline: str = ""
    #: label -> value. "" takes a row off; a label the cover does not have adds
    #: one. Identity rows are not here (see IDENTITY_LABELS).
    fields: dict[str, str] = field(default_factory=dict)

    def shown(self, manual_has_title_page: bool = False) -> bool:
        return self.show if self.show is not None else not manual_has_title_page


@dataclass(frozen=True)
class Presentation:
    """How the report looks: one object, one place, saved with the run."""

    cover: CoverSpec = field(default_factory=CoverSpec)
    style: str = "classic"
    #: task id -> what its answer includes, the manual's boxes, its own style.
    tasks: dict[str, AnswerPlan] = field(default_factory=dict)
    #: LABEL_KEYS -> the student's wording; "" removes the label.
    labels: dict[str, str] = field(default_factory=dict)
    page_breaks: bool = False
    body_pt: float | None = None
    code_pt: float | None = None
    #: Empty means the default set (`emit.DEFAULT_ARTIFACTS`).
    formats: tuple[str, ...] = ()
    #: The files' shared name, when the student chose one.
    stem: str = ""
    #: Saved edits from the editing agent, replayed after every rebuild.
    edits: tuple[dict, ...] = ()

    @classmethod
    def of(cls, style: str = "classic", plans: dict | None = None, **kwargs) -> "Presentation":
        """The spec a caller (or a test) means by "this style, these plans"."""
        return cls(style=style or "classic", tasks=dict(plans or {}), **kwargs)

    def look_for(self, task_id: str) -> AnswerPlan:
        return self.tasks.get(task_id) or AnswerPlan()

    def include_for(self, task_id: str) -> Include:
        return self.look_for(task_id).include

    def style_for(self, task_id: str) -> str:
        own = self.look_for(task_id).style
        return own if own in STYLES else (self.style if self.style in STYLES else "classic")

    def words_for(self, task_id: str) -> Vocabulary:
        return vocabulary(self.style_for(task_id), self.labels)

    # -- saved in the manifest ----------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "cover": {
                "show": self.cover.show,
                "layout": self.cover.layout,
                "title": self.cover.title,
                "tagline": self.cover.tagline,
                "fields": dict(self.cover.fields),
            },
            "style": self.style,
            "tasks": {
                tid: {
                    "include": {p: getattr(plan.include, p) for p in PARTS},
                    "slots": [[s.idx, s.part] for s in plan.slots],
                    "style": plan.style,
                }
                for tid, plan in self.tasks.items()
            },
            "labels": dict(self.labels),
            "page_breaks": self.page_breaks,
            "body_pt": self.body_pt,
            "code_pt": self.code_pt,
            "formats": list(self.formats),
            "stem": self.stem,
            "edits": [dict(e) for e in self.edits],
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "Presentation":
        """Tolerant on purpose: an old or hand-edited manifest must still load."""
        data = data or {}
        raw_cover = data.get("cover") or {}
        cover = CoverSpec(
            show=raw_cover.get("show") if isinstance(raw_cover.get("show"), bool) else None,
            layout=raw_cover.get("layout") if raw_cover.get("layout") in LAYOUTS else "classic",
            title=str(raw_cover.get("title") or ""),
            tagline=str(raw_cover.get("tagline") or ""),
            fields={str(k): str(v or "") for k, v in (raw_cover.get("fields") or {}).items()},
        )
        tasks: dict[str, AnswerPlan] = {}
        for tid, raw in (data.get("tasks") or {}).items():
            raw = raw or {}
            inc = raw.get("include") or {}
            defaults = Include()
            include = Include(**{p: bool(inc.get(p, getattr(defaults, p))) for p in PARTS})
            slots = tuple(
                Slot(int(s[0]), str(s[1]))
                for s in (raw.get("slots") or [])
                if isinstance(s, (list, tuple)) and len(s) == 2
            )
            style = raw.get("style") if raw.get("style") in STYLES else None
            tasks[str(tid)] = AnswerPlan(include=include, slots=slots, style=style)
        return cls(
            cover=cover,
            style=data.get("style") if data.get("style") in STYLES else "classic",
            tasks=tasks,
            labels={
                k: str(v) for k, v in (data.get("labels") or {}).items() if k in LABEL_KEYS
            },
            page_breaks=bool(data.get("page_breaks", False)),
            body_pt=_points(data.get("body_pt"), BODY_PT),
            code_pt=_points(data.get("code_pt"), CODE_PT),
            formats=tuple(f for f in (data.get("formats") or []) if f in FORMATS),
            stem=str(data.get("stem") or ""),
            edits=tuple(dict(e) for e in (data.get("edits") or []) if isinstance(e, dict)),
        )


# -- the patch: what one message asks to change --------------------------------
#
# LOOSE ON PURPOSE, like every structured reply in this codebase: a field the
# model fills oddly must cost that field, never a retry. Every empty value
# means "leave it as it is".


class CoverPatch(BaseModel):
    show: bool | str | None = None
    layout: str | None = None
    title: str | None = None
    tagline: str | None = None
    #: label -> value; "" or null takes the row off.
    fields: dict[str, str | None] | None = None
    #: "title" and/or "tagline": back to the lab's own / none.
    clear: list[str] | None = None


class TaskLookPatch(BaseModel):
    task_ids: list[str] | None = None
    show: list[str] | None = None
    hide: list[str] | None = None
    style: str | None = None


class LookPatch(BaseModel):
    cover: CoverPatch | None = None
    style: str | None = None
    #: Parts for EVERY task.
    show: list[str] | None = None
    hide: list[str] | None = None
    #: Changes to particular tasks only.
    tasks: list[TaskLookPatch | str] | None = None
    #: LABEL_KEYS -> new wording; "" removes the label, null restores it.
    labels: dict[str, str | None] | None = None
    page_breaks: bool | str | None = None
    body_pt: float | str | None = None
    code_pt: float | str | None = None
    #: The COMPLETE new set of files, or empty for "unchanged".
    formats: list[str] | None = None
    file_name: str | None = None


def _truth(value) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in ("true", "yes", "on", "1", "show"):
        return True
    if text in ("false", "no", "off", "0", "hide", "remove", "none"):
        return False
    return None


def _choice(value, allowed) -> str:
    value = str(value or "").strip().lower()
    return value if value in allowed else ""


def _points(value, bounds) -> float | None:
    try:
        number = float(str(value).strip().lower().removesuffix("pt").strip())
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    low, high = bounds
    return round(min(high, max(low, number)) * 2) / 2


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def canonical_label(key: str) -> tuple[str, str]:
    """("identity", profile field) or ("cover", row label) for a cover key."""
    text = _squash(key).rstrip(":").strip()
    low = text.lower()
    if low in IDENTITY_LABELS:
        return "identity", IDENTITY_LABELS[low]
    if low in _COVER_ALIASES:
        return "cover", _COVER_ALIASES[low]
    for label in COVER_LABELS:
        if low == label.lower():
            return "cover", label
    return "cover", (text[:1].upper() + text[1:])[:40]


def split_identity(patch: LookPatch | None) -> tuple[LookPatch | None, dict[str, str]]:
    """Take who-the-student-is out of a cover patch: it rides on the profile.

    "Put my name as Ayesha Abid" must reach the file names as well as the
    cover, and only the profile does both.
    """
    if patch is None or patch.cover is None or not patch.cover.fields:
        return patch, {}
    identity: dict[str, str] = {}
    kept: dict[str, str | None] = {}
    for key, value in patch.cover.fields.items():
        kind, name = canonical_label(key)
        if kind == "identity":
            if _squash(value):
                identity[name] = _squash(value)[:120]
        else:
            kept[key] = value
    cover = patch.cover.model_copy(update={"fields": kept})
    return patch.model_copy(update={"cover": cover}), identity


def _stem(value) -> str:
    text = Path(_squash(value)).stem if _squash(value) else ""
    text = re.sub(r"[^A-Za-z0-9 _.\-]+", "", text).strip(" .")
    return re.sub(r"\s+", "_", text)[:60]


def apply(pres: Presentation, patch: LookPatch | None, task_ids=()) -> Presentation:
    """The only way a Presentation changes. Unknown values are ignored."""
    if patch is None:
        return pres
    ids = list(task_ids) or list(pres.tasks)

    cover = pres.cover
    cp = patch.cover
    if cp is not None:
        show = _truth(cp.show)
        if show is not None:
            cover = replace(cover, show=show)
        layout = _choice(cp.layout, LAYOUTS)
        if layout:
            cover = replace(cover, layout=layout)
        if _squash(cp.title):
            cover = replace(cover, title=_squash(cp.title)[:160])
        if _squash(cp.tagline):
            cover = replace(cover, tagline=_squash(cp.tagline)[:160])
        for name in cp.clear or []:
            name = str(name or "").strip().lower()
            if name in ("title", "tagline"):
                cover = replace(cover, **{name: ""})
        if cp.fields:
            fields = dict(cover.fields)
            for key, value in cp.fields.items():
                kind, label = canonical_label(key)
                if kind != "cover" or not label:
                    continue
                if label not in fields and label not in COVER_LABELS:
                    custom = [k for k in fields if k not in COVER_LABELS]
                    if len(custom) >= MAX_CUSTOM_ROWS:
                        continue
                fields[label] = _squash(value)[:160]
            cover = replace(cover, fields=fields)

    tasks = dict(pres.tasks)
    show = [p for p in (patch.show or []) if p in PARTS]
    hide = [p for p in (patch.hide or []) if p in PARTS]
    if show or hide:
        for tid in ids:
            tasks[tid] = (tasks.get(tid) or AnswerPlan()).changed(show, hide)
    for item in patch.tasks or []:
        if not isinstance(item, TaskLookPatch):
            continue
        targets = [t for t in (item.task_ids or []) if t in ids]
        own_show = [p for p in (item.show or []) if p in PARTS]
        own_hide = [p for p in (item.hide or []) if p in PARTS]
        own_style = _choice(item.style, STYLES)
        for tid in targets:
            plan = (tasks.get(tid) or AnswerPlan()).changed(own_show, own_hide)
            tasks[tid] = replace(plan, style=own_style) if own_style else plan

    labels = dict(pres.labels)
    for key, value in (patch.labels or {}).items():
        name = _LABEL_ALIASES.get(_squash(key).rstrip(":").lower())
        if name is None:
            continue
        if value is None:
            labels.pop(name, None)
        else:
            labels[name] = _squash(value)[:60]

    page_breaks = _truth(patch.page_breaks)
    formats = tuple(
        dict.fromkeys(
            f for f in (str(x or "").strip().lower().lstrip(".") for x in patch.formats or [])
            if f in FORMATS
        )
    )
    return replace(
        pres,
        cover=cover,
        style=_choice(patch.style, STYLES) or pres.style,
        tasks=tasks,
        labels=labels,
        page_breaks=pres.page_breaks if page_breaks is None else page_breaks,
        body_pt=_points(patch.body_pt, BODY_PT) or pres.body_pt,
        code_pt=_points(patch.code_pt, CODE_PT) or pres.code_pt,
        formats=formats or pres.formats,
        stem=_stem(patch.file_name) or pres.stem,
    )


def is_empty(patch: LookPatch | None) -> bool:
    """Does this patch ask for nothing at all?"""
    if patch is None:
        return True
    probe = Presentation()
    return apply(probe, patch, ["task1"]) == probe


# -- the receipt: what actually changed, in plain words ---------------------------

PART_NAMES = {
    "code": "the code",
    "output": "the printed output",
    "screenshots": "screenshots",
    "figures": "charts",
    "explanation": "explanations",
    "statement": "the task statements",
}

_LABEL_NAMES = {
    "code": "code",
    "output": "output",
    "answers": "written-answers",
    "status": "status",
}


def _task_phrase(task_ids) -> str:
    numbers = [str(t).removeprefix("task") for t in task_ids]
    if len(numbers) == 1:
        return f"task {numbers[0]}"
    return f"tasks {', '.join(numbers[:-1])} and {numbers[-1]}"


def receipt(
    before: Presentation,
    after: Presentation,
    task_ids=(),
    *,
    title_page: bool = False,
    default_formats=(),
) -> list[str]:
    """Each change as one plain sentence, computed from the two specs.

    TRUST THE DIFF, NOT THE PROMISE. The router used to reply with the model's
    own sentence about what it WOULD change, which could name a change that
    never happened. These sentences describe what did.
    """
    out: list[str] = []
    a, b = after.cover, before.cover

    shown_before, shown_after = b.shown(title_page), a.shown(title_page)
    if shown_before and not shown_after:
        out.append("Removed the cover.")
    elif shown_after and not shown_before:
        out.append("Added a cover.")
    if shown_after:
        if a.layout != b.layout:
            out.append(f"Cover design: {a.layout}.")
        if a.title != b.title:
            out.append(f"Cover title: {a.title}." if a.title else "Cover title is the lab's own again.")
        if a.tagline != b.tagline:
            out.append(f"Cover line under the title: {a.tagline}." if a.tagline else "Removed the line under the title.")
        for label in dict.fromkeys([*b.fields, *a.fields]):
            old, new = b.fields.get(label), a.fields.get(label)
            if old == new:
                continue
            out.append(
                f"Took {label} off the cover." if new == "" else f"The cover now says {label}: {new}."
            )

    if after.style != before.style:
        out.append(f"Layout: {after.style}.")

    ids = list(task_ids) or list(dict.fromkeys([*before.tasks, *after.tasks]))
    for part in PARTS:
        on = [t for t in ids if getattr(after.include_for(t), part) and not getattr(before.include_for(t), part)]
        off = [t for t in ids if getattr(before.include_for(t), part) and not getattr(after.include_for(t), part)]
        name = PART_NAMES[part]
        if on:
            out.append(f"Added {name}." if len(on) == len(ids) else f"Added {name} to {_task_phrase(on)}.")
        if off:
            out.append(f"Left out {name}." if len(off) == len(ids) else f"Left out {name} in {_task_phrase(off)}.")
    for tid in ids:
        if after.look_for(tid).style != before.look_for(tid).style and after.look_for(tid).style:
            out.append(f"{_task_phrase([tid]).capitalize()} layout: {after.look_for(tid).style}.")

    for key in LABEL_KEYS:
        if after.labels.get(key) == before.labels.get(key):
            continue
        new = after.labels.get(key)
        name = _LABEL_NAMES[key]
        if new is None:
            out.append(f"The {name} label is back to normal.")
        elif new == "":
            out.append(f"Removed the {name} label.")
        else:
            out.append(f"The {name} label now reads \"{new}\".")

    if after.page_breaks != before.page_breaks:
        out.append(
            "Each task now starts on a new page." if after.page_breaks
            else "Tasks no longer start on a new page."
        )
    if after.body_pt != before.body_pt and after.body_pt:
        out.append(f"Text size: {after.body_pt:g} pt.")
    if after.code_pt != before.code_pt and after.code_pt:
        out.append(f"Code size: {after.code_pt:g} pt.")

    was = list(before.formats or default_formats)
    now = list(after.formats or default_formats)
    added = [f for f in now if f not in was]
    dropped = [f for f in was if f not in now]
    if added:
        out.append("Added " + _and([FORMAT_NAMES.get(f, f) for f in added]) + ".")
    if dropped:
        out.append("Dropped " + _and([FORMAT_NAMES.get(f, f) for f in dropped]) + ".")
    if after.stem != before.stem and after.stem:
        out.append(f"The files are now named {after.stem}.")
    return out


def _and(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]
