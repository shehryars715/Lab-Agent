"""Annotate a copy of the lab manual in place.

The manual IS the report template. Original text, numbering, headers, logos and
styles are never regenerated, so they survive by construction. That deletes all
document-design work and replaces it with a narrower problem: resolving anchors
and inserting XML correctly.

Inserted after each task's anchor:

    Code:
        <source, monospace, shaded>
    Output:
        <screenshot>
        <short explanation>
"""

from __future__ import annotations

import shutil
from pathlib import Path

import docx
from docx.shared import Inches, Pt
from docx.text.paragraph import Paragraph

from labsagent.blocks import (
    _failure_note,
    gap_note,
    has_screenshot_twin,
    leading_prose,
    outside_notes,
)
from labsagent.models import TaskOutcome
from labsagent.report.cover import CoverInfo, build_cover
from labsagent.report.docx_utils import (
    enclosing_table,
    flatten_paragraphs,
    insert_paragraph_after,
    insert_paragraph_after_table,
    shade,
    style_code_run,
    tighten,
)

CODE_FILL = "F2F2F2"
CODE_LABEL = "Code:"
OUTPUT_LABEL = "Output:"
IMAGE_WIDTH_IN = 6.0


def _label(cursor: Paragraph, text: str) -> Paragraph:
    para = insert_paragraph_after(cursor)
    run = para.add_run(text)
    run.bold = True
    tighten(para, before=6, after=2)
    return para


def _code_block(cursor: Paragraph, source: str) -> Paragraph:
    """One shaded paragraph per line, so the block page-breaks naturally."""
    lines = source.rstrip("\n").split("\n") or [""]
    for line in lines:
        cursor = insert_paragraph_after(cursor)
        # A space keeps empty lines from collapsing the shaded band.
        run = cursor.add_run(line if line.strip() else " ")
        style_code_run(run)
        shade(cursor, CODE_FILL)
        tighten(cursor)
        cursor.paragraph_format.left_indent = Inches(0.25)
    return cursor


def _image(cursor: Paragraph, image_path: Path) -> Paragraph:
    cursor = insert_paragraph_after(cursor)
    cursor.add_run().add_picture(str(image_path), width=Inches(IMAGE_WIDTH_IN))
    tighten(cursor, before=2, after=4)
    return cursor


def _explanation(cursor: Paragraph, text: str) -> Paragraph:
    cursor = insert_paragraph_after(cursor)
    run = cursor.add_run(text)
    run.font.size = Pt(10)
    tighten(cursor, before=2, after=8)
    return cursor


def _annotate_one(anchor: Paragraph, outcome: TaskOutcome) -> None:
    """Insert one task's work. The cursor advances to each newly created
    paragraph -- inserting repeatedly after the same anchor reverses order."""
    cursor = anchor

    # Anything the caller put before the code -- today, which dataset this run
    # used. Guarded by `leading_prose` returning empty for every outcome that
    # does not set `blocks`, so a report built the way every previous report
    # was built is byte-identical, which this module's docstring promises.
    for block in leading_prose(outcome):
        cursor = _explanation(cursor, block.text)

    # A theory question gets words, not an empty "Code:" block.
    if not getattr(outcome.task, "needs_code", True):
        if outcome.status == "failed":
            cursor = _label(cursor, "Status:")
            cursor = _explanation(cursor, f"Not answered: {outcome.error or 'unknown'}")
            return
        for note in outside_notes(outcome):
            cursor = _explanation(cursor, note.text)
        if outcome.explanation:
            cursor = _explanation(cursor, outcome.explanation)
        _answers(cursor, outcome)
        return

    cursor = _label(cursor, CODE_LABEL)
    if outcome.code_text:
        cursor = _code_block(cursor, outcome.code_text)
    else:
        cursor = _code_block(cursor, "# no solution was produced for this task")

    if outcome.status == "failed":
        cursor = _label(cursor, "Status:")
        cursor = _explanation(cursor, _failure_note(outcome))
        return

    cursor = _label(cursor, OUTPUT_LABEL)
    for image_path in outcome.screenshot_paths:
        cursor = _image(cursor, image_path)
    for figure_path in outcome.figure_paths:
        cursor = _image(cursor, figure_path)

    # "Recreated for this report" / "Left out" -- written by code, and empty
    # for a self-contained lab, so every earlier report is byte-identical.
    for note in outside_notes(outcome):
        cursor = _explanation(cursor, note.text)
    # Empty for every outcome without a gap, so older reports are unchanged.
    if gap_note(outcome):
        cursor = _explanation(cursor, gap_note(outcome))
    if outcome.explanation:
        cursor = _explanation(cursor, outcome.explanation)
    _answers(cursor, outcome)


def _answers(cursor: Paragraph, outcome: TaskOutcome) -> Paragraph:
    """Each written answer as real text: the question in bold, then the answer.

    This is the fix for the analysis that used to arrive as a terminal
    screenshot. Nothing is added for an outcome without answers, so every
    report built before this existed is unchanged.
    """
    for item in outcome.answers or []:
        answer = str(item.get("answer") or "").strip()
        if not answer:
            continue
        question = str(item.get("question") or "").strip()
        if question:
            cursor = _label(cursor, question)
        cursor = _explanation(cursor, answer)
    return cursor


def _annotate_arranged(anchor: Paragraph, outcome: TaskOutcome, style: str) -> None:
    """One task's work in a non-classic style, inserted under its anchor."""
    from labsagent.present import arrange, vocabulary

    words = vocabulary(style)
    cursor = anchor
    blocks = arrange(outcome, style)
    labelled_output = False
    answers_headed = False
    for index, block in enumerate(blocks):
        if block.kind == "code":
            cursor = _label(cursor, block.title or words.code)
            cursor = _code_block(cursor, block.text)
            labelled_output = False
        elif block.kind == "error":
            cursor = _label(cursor, "Status:")
            cursor = _explanation(cursor, block.text)
        elif block.kind == "image" and block.path is not None and Path(block.path).exists():
            if not labelled_output:
                cursor = _label(cursor, words.output)
                labelled_output = True
            cursor = _image(cursor, Path(block.path))
        elif (
            block.kind == "output"
            and block.text.strip()
            # TERMINAL OUTPUT IS SHOWN AS THE TERMINAL. This used to be one
            # flag for the whole task, and section layouts dropped the only
            # picture -- so every non-classic report printed shaded text. The
            # text now appears only for an output that has no picture.
            and not has_screenshot_twin(blocks, index)
        ):
            cursor = _label(cursor, words.output)
            labelled_output = True
            cursor = _code_block(cursor, block.text)
        elif block.kind == "prose":
            if block.role == "answer" and words.answers and not answers_headed:
                cursor = _label(cursor, words.answers)
                answers_headed = True
            if block.title:
                cursor = _label(cursor, block.title)
            cursor = _explanation(cursor, block.text)


def annotate_manual(
    manual_path: Path,
    out_path: Path,
    outcomes: list[TaskOutcome],
    cover: CoverInfo | None = None,
    anchors: dict[str, int] | None = None,
    style: str = "classic",
) -> Path:
    """Copy the manual and insert each task's work beneath its anchor.

    A `cover` is prepended AFTER anchors have been resolved to Paragraph
    objects. Order matters: prepending shifts every integer index in the
    document, but object references survive it -- the same reason anchors are
    resolved before any mutation at all.
    """
    manual_path, out_path = Path(manual_path), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(manual_path, out_path)

    doc = docx.Document(str(out_path))
    paragraphs = flatten_paragraphs(doc)

    # Resolve every anchor to a Paragraph OBJECT before mutating anything.
    # Integer indices shift the moment the first insert lands; object references
    # stay valid across sibling inserts.
    resolved: list[tuple[Paragraph, TaskOutcome]] = []
    for outcome in outcomes:
        idx = (anchors or {}).get(outcome.task.id, -1)
        if not (0 <= idx < len(paragraphs)):
            raise IndexError(
                f"{outcome.task.id}: anchor {idx} outside document "
                f"(0..{len(paragraphs) - 1})"
            )
        anchor = paragraphs[idx]

        # A task wrapped in a table would otherwise have its code and a 6in
        # screenshot inserted INSIDE the bordered cell -- cramped, and visually
        # inconsistent with tasks that sit on the open page. Escape to body
        # level so every task is presented identically.
        tbl = enclosing_table(anchor)
        if tbl is not None:
            anchor = insert_paragraph_after_table(tbl, doc)

        resolved.append((anchor, outcome))

    if cover is not None:
        build_cover(doc, cover)

    for anchor, outcome in resolved:
        # `classic` keeps the original writer, byte for byte. Any other style
        # is laid out by `present.arrange` and inserted block by block.
        if style == "classic":
            _annotate_one(anchor, outcome)
        else:
            _annotate_arranged(anchor, outcome, style)

    doc.save(str(out_path))
    return out_path
