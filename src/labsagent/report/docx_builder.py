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

from labsagent.blocks import leading_prose
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

    cursor = _label(cursor, CODE_LABEL)
    if outcome.code_text:
        cursor = _code_block(cursor, outcome.code_text)
    else:
        cursor = _code_block(cursor, "# no solution was produced for this task")

    if outcome.status == "failed":
        cursor = _label(cursor, "Status:")
        cursor = _explanation(
            cursor,
            f"This task did not complete after {outcome.attempts} attempt(s). "
            f"Error: {outcome.error or 'unknown'}",
        )
        return

    cursor = _label(cursor, OUTPUT_LABEL)
    for image_path in outcome.screenshot_paths:
        cursor = _image(cursor, image_path)
    for figure_path in outcome.figure_paths:
        cursor = _image(cursor, figure_path)

    if outcome.explanation:
        cursor = _explanation(cursor, outcome.explanation)


def annotate_manual(
    manual_path: Path,
    out_path: Path,
    outcomes: list[TaskOutcome],
    cover: CoverInfo | None = None,
    anchors: dict[str, int] | None = None,
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
        _annotate_one(anchor, outcome)

    doc.save(str(out_path))
    return out_path
