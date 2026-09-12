"""A cover sheet, prepended to the report.

THE INSERTION PROBLEM. python-docx can only APPEND: `add_paragraph` and
`add_table` attach to the end of the body. A cover page is by definition the
opposite. The fix is the same one the annotator uses -- drop to lxml, where the
body is just a list of elements, and move what you built to the front.

Build-then-move beats insert-as-you-go because the high-level API stays
available the whole time. You get real Paragraph and Table objects with styles,
borders and cell access, and only the final position is handled by hand. One
line of XML instead of reimplementing tables on top of it.

The page break belongs to the LAST cover paragraph rather than being a separate
empty one: a trailing empty paragraph after a break shows up as a stray blank
line at the top of page two, which is exactly the kind of small wrongness that
makes a generated document look generated.
"""

from __future__ import annotations

from dataclasses import dataclass

from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.shared import Pt, RGBColor

from labsagent.report.docx_utils import tighten

FIELD_LABEL_WIDTH_IN = 1.9
TITLE_SIZE_PT = 22
SUBTITLE_SIZE_PT = 13


@dataclass
class CoverInfo:
    """Everything printed on the cover sheet. Empty fields are simply omitted."""

    lab_number: str = ""
    lab_title: str = ""
    course: str = ""
    student_name: str = ""
    cms_id: str = ""
    section: str = ""
    instructor: str = ""
    lab_engineer: str = ""
    date: str = ""
    department: str = ""

    def rows(self) -> list[tuple[str, str]]:
        """Label/value pairs, skipping anything we do not actually know."""
        candidates = [
            ("Name", self.student_name),
            ("CMS ID", self.cms_id),
            ("Section", self.section),
            ("Course", self.course),
            ("Lab Number", f"Lab {self.lab_number}" if self.lab_number else ""),
            ("Lab Title", self.lab_title),
            ("Instructor", self.instructor),
            ("Lab Engineer", self.lab_engineer),
            ("Date", self.date),
        ]
        return [(label, value.strip()) for label, value in candidates if value and value.strip()]


def _centered(doc, text: str, size_pt: float, *, bold: bool = False, grey: bool = False):
    para = doc.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = para.add_run(text)
    run.bold = bold
    run.font.size = Pt(size_pt)
    if grey:
        run.font.color.rgb = RGBColor(0x59, 0x59, 0x59)
    tighten(para, before=2, after=6)
    return para


def build_cover(doc, info: CoverInfo) -> None:
    """Prepend a cover sheet to an open Document, in place."""
    created = []

    if info.department:
        created.append(_centered(doc, info.department.upper(), 11, grey=True))

    heading = f"Lab {info.lab_number}" if info.lab_number else "Lab Report"
    if info.lab_title:
        heading = f"{heading}: {info.lab_title}"
    created.append(_centered(doc, heading, TITLE_SIZE_PT, bold=True))

    if info.course:
        created.append(_centered(doc, info.course, SUBTITLE_SIZE_PT, grey=True))

    spacer = doc.add_paragraph()
    tighten(spacer, before=0, after=10)
    created.append(spacer)

    rows = info.rows()
    if rows:
        table = doc.add_table(rows=0, cols=2)
        table.style = "Table Grid"
        table.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for label, value in rows:
            cells = table.add_row().cells
            left = cells[0].paragraphs[0]
            left_run = left.add_run(label)
            left_run.bold = True
            left_run.font.size = Pt(10.5)
            tighten(left, before=3, after=3)

            right = cells[1].paragraphs[0]
            right_run = right.add_run(value)
            right_run.font.size = Pt(10.5)
            tighten(right, before=3, after=3)
        created.append(table)

    # The break lives on the last element so page two starts clean.
    tail = doc.add_paragraph()
    tail.add_run().add_break(WD_BREAK.PAGE)
    tighten(tail)
    created.append(tail)

    # Move everything built above to the front of the body, preserving order.
    body = doc.element.body
    for position, item in enumerate(created):
        element = item._element if hasattr(item, "_element") else item._tbl
        body.remove(element)
        body.insert(position, element)


def cover_from(
    spec,
    profile,
    facts,
) -> CoverInfo:
    """Assemble a CoverInfo from the three things that know something about it.

    Precedence is deliberate: the student's own profile wins for section (they
    may have moved), and the manual wins for everything it states, because it is
    the authoritative document for the course it belongs to.
    """
    return CoverInfo(
        lab_number=getattr(spec, "lab_number", "") or "",
        lab_title=getattr(spec, "title", "") or "",
        course=(getattr(facts, "course", None) or getattr(spec, "course", None) or ""),
        student_name=getattr(profile, "name", "") or "",
        cms_id=getattr(profile, "cms_id", "") or "",
        section=(getattr(profile, "section", "") or getattr(facts, "section", None) or ""),
        instructor=getattr(facts, "instructor", None) or "",
        lab_engineer=getattr(facts, "lab_engineer", None) or "",
        date=getattr(facts, "date", None) or "",
        department=getattr(facts, "department", None) or "",
    )
