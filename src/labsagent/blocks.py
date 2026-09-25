"""The format-free content of a solved task.

WHY THIS EXISTS. `TaskOutcome` already held everything an exporter needs, but it
held it in *named fields* -- `code_text`, `transcript`, `figure_paths`,
`explanation`. Named fields can only ever be rendered in one order, so a lab
that wants explain-then-code-then-explain-again cannot be expressed at all, and
a notebook gets one fat cell per task instead of the several it wants.

A block list fixes that without throwing anything away. `TaskOutcome.blocks` is
optional: when the solver has not produced one, `blocks_for()` synthesises the
list from the named fields in exactly the order the DOCX writer has always used.
So every emitter can be written against blocks alone, while old manifests, the
resume path and `annotate_manual` keep working untouched.

THE LIST IS A SUPERSET, AND EMITTERS PROJECT. A transcript appears twice: once
as an `output` block carrying the text, and once as an `image` block carrying
the rendered terminal PNG. That is deliberate. The Word report wants the
screenshot, a notebook wants real stream output, and markdown wants a fenced
block -- so the IR carries both and each emitter takes the representation that
suits it. An IR that pre-chose one would be lossy for the others.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

BlockKind = Literal["prose", "code", "output", "image", "error"]

#: `role` values used on image blocks, so an emitter can tell the rendered
#: terminal screenshot apart from a figure the solution itself saved.
SCREENSHOT = "screenshot"
FIGURE = "figure"

NO_SOLUTION = "# no solution was produced for this task"


@dataclass(frozen=True)
class Block:
    """One piece of a task's answer.

    `text` carries prose, source or captured output; `path` carries images.
    `role` is a free-form tag an emitter may use to filter -- see SCREENSHOT
    and FIGURE.
    """

    kind: BlockKind
    text: str = ""
    lang: str = "python"
    path: Path | None = None
    role: str = ""
    #: A lead for prose that answers something -- the written question.
    title: str = ""


def _failure_note(outcome) -> str:
    # A STOPPED TASK IS NOT A FAILED ONE. "Did not complete after 1 attempt"
    # reads as the agent trying and losing; the truth is that something it
    # needed was not there, and the sentence says what.
    blocker = getattr(outcome, "blocker", None)
    if blocker:
        return f"Not done: {blocker}"
    return (
        f"This task did not complete after {outcome.attempts} attempt(s). "
        f"Error: {outcome.error or 'unknown'}"
    )


def gap_note(outcome) -> str:
    """"" unless the task passed with one part it could not do."""
    gap = getattr(outcome, "gap", None)
    return f"Not done: {gap}" if gap else ""


def blocks_for(outcome) -> list[Block]:
    """The blocks of one outcome: its own if it has them, else synthesised.

    The synthesised order mirrors `report/docx_builder.py:_annotate_one`
    exactly -- code, then either the failure note or the output, then the
    explanation. Keeping the two in step is what lets a new emitter and the old
    annotator describe the same task the same way.
    """
    if outcome.blocks:
        return list(outcome.blocks)

    task = getattr(outcome, "task", None)
    if task is not None and not getattr(task, "needs_code", True):
        # A theory question: words only. No "no solution was produced" code
        # block, because no solution was ever asked for.
        if outcome.status == "failed":
            return [Block("error", text=_failure_note(outcome))]
        return _prose_of(outcome)

    out: list[Block] = [Block("code", text=outcome.code_text or NO_SOLUTION)]

    if outcome.status == "failed":
        out.append(Block("error", text=_failure_note(outcome)))
        return out

    if outcome.transcript is not None:
        text = "\n".join(outcome.transcript.display_lines())
        if text.strip():
            out.append(Block("output", text=text))

    for shot in outcome.screenshot_paths:
        out.append(Block("image", path=Path(shot), role=SCREENSHOT))
    for figure in outcome.figure_paths:
        out.append(Block("image", path=Path(figure), role=FIGURE))

    if gap_note(outcome):
        out.append(Block("prose", text=gap_note(outcome), role="gap"))
    return out + _prose_of(outcome)


def _prose_of(outcome) -> list[Block]:
    """The overview, then one titled block per written answer."""
    out: list[Block] = []
    if outcome.explanation:
        out.append(Block("prose", text=outcome.explanation))
    for item in getattr(outcome, "answers", None) or []:
        answer = str(item.get("answer") or "").strip()
        if answer:
            out.append(
                Block("prose", text=answer, role="answer", title=str(item.get("question") or ""))
            )
    return out


def leading_prose(outcome) -> list[Block]:
    """Prose a CALLER placed before the first code block, if any.

    WHY THIS IS SHARED RATHER THAN LOCAL. Three emitters do not consume the
    block list: `report/docx_builder.py` (the annotate-in-place Word report),
    `package/notebook.py`, and -- for its statement header -- `emit/script.py`.
    They read `TaskOutcome`'s named fields, which is why they are byte-stable
    and why adding a block reached none of them. Anything a caller wants said
    before the code, such as which dataset the run used, therefore needs one
    definition of "the bit that goes at the top" instead of three.

    DELIBERATELY ONLY LEADING PROSE. `blocks_for()` SYNTHESISES a list whose
    prose -- the explanation -- comes LAST, so a naive walk would print every
    explanation a second time at the top of its own task. Reading `blocks`
    directly and stopping at the first non-prose block means a synthesised
    list contributes nothing, and only an explicit caller is honoured.
    """
    blocks = getattr(outcome, "blocks", None) or []
    out: list[Block] = []
    for block in blocks:
        if block.kind != "prose":
            break
        out.append(block)
    return out


def code_of(outcome) -> str:
    """Every code block of a task, joined. The `.py` emitter's whole job."""
    return "\n\n".join(b.text.rstrip() for b in blocks_for(outcome) if b.kind == "code")
