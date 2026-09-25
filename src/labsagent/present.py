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

from dataclasses import dataclass
from pathlib import Path

from labsagent.blocks import FIGURE, Block, blocks_for, leading_prose

STYLES = ("classic", "walkthrough", "findings", "compact")


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


def arrange(outcome, style: str = "classic") -> list[Block]:
    """The blocks of one task, in the order this style presents them.

    Leading prose a caller put first (the "Data used" line) always stays first:
    it is context for the whole task, not part of any one layout.
    """
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
