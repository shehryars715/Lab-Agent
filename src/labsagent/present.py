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

from dataclasses import dataclass, field, replace
from pathlib import Path

from labsagent.blocks import FIGURE, SCREENSHOT, Block, blocks_for, leading_prose

STYLES = ("classic", "walkthrough", "findings", "compact")


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
#: "no explanation"). Written answers are not here: the manual asked for them.
PARTS = ("code", "output", "screenshots", "figures", "explanation")

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
    """One task's answer: what it includes, and the manual's boxes, if any."""

    include: Include = field(default_factory=Include)
    slots: tuple[Slot, ...] = ()

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


VOCABULARY = {
    "classic": Vocabulary("Code:", "Output:", ""),
    "walkthrough": Vocabulary("Implementation", "Result", "Discussion"),
    "findings": Vocabulary("How it was computed", "Program output", "Findings"),
    "compact": Vocabulary("Program", "Run", ""),
}


def vocabulary(style: str) -> Vocabulary:
    return VOCABULARY.get(style, VOCABULARY["classic"])


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
