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

FOUR LAYOUTS, NOT ONE GENERATED ONE. Every cover this tool produces used to be
the same table under the same centred heading, which is a tell: the fourth lab
you submit looks exactly like the first, and the first looks like everyone
else's. So there are now four designs, and the caller picks.

Why four hand-built designs rather than a model composing a layout from
primitives: python-docx cannot position anything. It can only add paragraphs
and tables in order and style them. Given layout primitives, a model would
produce variations on paragraph order that range from fine to unpresentable,
with no way to tell which you got until you open Word. Four designed
alternatives are all things you would be happy to submit; the model's job is to
choose between them and write one line of text, which is the part a model is
genuinely good at.

`classic` is the default and is byte-identical to what this module produced
before the layouts existed, so the existing tests are unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml.ns import qn
from docx.oxml.parser import OxmlElement
from docx.shared import Pt, RGBColor

from labsagent.report.docx_utils import shade, table_borders, tighten

FIELD_LABEL_WIDTH_IN = 1.9
TITLE_SIZE_PT = 22
SUBTITLE_SIZE_PT = 13

GREY = RGBColor(0x59, 0x59, 0x59)
ACCENT = RGBColor(0x4F, 0x46, 0xE5)
BANNER_FILL = "E9EBFB"
RULE_FILL = "4F46E5"

LAYOUTS = ("classic", "rule", "banner", "split")


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

    # Chosen by the caller; `build_cover` falls back to `classic` for anything
    # it does not recognise, because a typo in a layout name must not cost you
    # the whole report.
    layout: str = "classic"
    # One content-aware line under the title, e.g. "Neural networks from
    # scratch, no framework." Empty renders nothing.
    tagline: str = ""

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

    def heading(self) -> str:
        heading = f"Lab {self.lab_number}" if self.lab_number else "Lab Report"
        return f"{heading}: {self.lab_title}" if self.lab_title else heading

    def identity_rows(self) -> list[tuple[str, str]]:
        """The subset that identifies the *student*, for the split layout."""
        wanted = {"Name", "CMS ID", "Section"}
        return [row for row in self.rows() if row[0] in wanted]

    def course_rows(self) -> list[tuple[str, str]]:
        wanted = {"Course", "Lab Number", "Lab Title", "Instructor", "Lab Engineer", "Date"}
        return [row for row in self.rows() if row[0] in wanted]


# --- small formatting helpers ---------------------------------------------


def _para(doc, text: str, size: float, *, bold=False, italic=False, grey=False,
          accent=False, center=False, before=2, after=6):
    para = doc.add_paragraph()
    if center:
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = para.add_run(text)
    run.bold = bold
    run.italic = italic
    run.font.size = Pt(size)
    if grey:
        run.font.color.rgb = GREY
    if accent:
        run.font.color.rgb = ACCENT
    tighten(para, before=before, after=after)
    return para


def _spacer(doc, points: float = 10):
    para = doc.add_paragraph()
    tighten(para, before=0, after=points)
    return para


def _rule(doc, *, thick: bool = False, before: float = 4, after: float = 10):
    """A horizontal rule, drawn as a bottom border on an empty paragraph.

    Word has no horizontal-rule element; a bordered empty paragraph at a fixed
    small height is the standard stand-in. The border lives on `w:pBdr`, which
    python-docx does not expose.
    """
    para = doc.add_paragraph()
    tighten(para, before=before, after=after)
    pr = para._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "18" if thick else "8")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), RULE_FILL)
    borders.append(bottom)
    pr.append(borders)
    return para


def _field_table(doc, rows: list[tuple[str, str]], *, bordered: bool = True):
    table = doc.add_table(rows=0, cols=2)
    if bordered:
        # NOT `table.style = "Table Grid"` -- see `table_borders`. This runs
        # against the student's own manual, which may not define that style.
        table_borders(table)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
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
    return table


def _page_break(doc):
    tail = doc.add_paragraph()
    tail.add_run().add_break(WD_BREAK.PAGE)
    tighten(tail)
    return tail


# --- the four layouts ------------------------------------------------------


def _layout_classic(doc, info: CoverInfo) -> list:
    created = []
    if info.department:
        created.append(_para(doc, info.department.upper(), 11, grey=True, center=True))

    created.append(_para(doc, info.heading(), TITLE_SIZE_PT, bold=True, center=True))

    if info.course:
        created.append(_para(doc, info.course, SUBTITLE_SIZE_PT, grey=True, center=True))

    created.append(_spacer(doc))

    rows = info.rows()
    if rows:
        created.append(_field_table(doc, rows))

    created.append(_page_break(doc))
    return created


def _layout_rule(doc, info: CoverInfo) -> list:
    """Editorial and left-aligned: rules instead of centring, labels not boxes."""
    created = [_rule(doc, thick=True, before=0, after=14)]

    eyebrow = " · ".join(
        part
        for part in (
            f"LAB {info.lab_number}" if info.lab_number else "",
            info.course.upper() if info.course else "",
        )
        if part
    )
    if eyebrow:
        created.append(_para(doc, eyebrow, 9.5, bold=True, accent=True, before=0, after=6))

    created.append(
        _para(doc, info.lab_title or info.heading(), 24, bold=True, before=0, after=8)
    )

    if info.tagline:
        created.append(_para(doc, info.tagline, 11.5, italic=True, grey=True, before=0, after=10))

    created.append(_rule(doc, before=2, after=16))

    # Deliberately not a bordered table: an unboxed two-column list reads as
    # typesetting rather than as a form.
    rows = info.rows()
    if rows:
        table = doc.add_table(rows=0, cols=2)
        table.autofit = False
        for label, value in rows:
            cells = table.add_row().cells
            left = cells[0].paragraphs[0]
            run = left.add_run(label.upper())
            run.font.size = Pt(8.5)
            run.font.color.rgb = GREY
            tighten(left, before=4, after=4)

            right = cells[1].paragraphs[0]
            rrun = right.add_run(value)
            rrun.font.size = Pt(11)
            tighten(right, before=4, after=4)
        created.append(table)

    created.append(_page_break(doc))
    return created


def _layout_banner(doc, info: CoverInfo) -> list:
    """A tinted band carrying the title, then the details beneath it."""
    created = []

    band = doc.add_paragraph()
    tighten(band, before=0, after=0)
    band_run = band.add_run(info.heading())
    band_run.bold = True
    band_run.font.size = Pt(19)
    band_run.font.color.rgb = RGBColor(0x1F, 0x21, 0x36)
    shade(band, BANNER_FILL)

    if info.department:
        dept_band = doc.add_paragraph()
        tighten(dept_band, before=0, after=0)
        dept_run = dept_band.add_run(info.department.upper())
        dept_run.font.size = Pt(9.5)
        dept_run.font.color.rgb = GREY
        shade(dept_band, BANNER_FILL)
        created.extend([band, dept_band])
    else:
        created.append(band)

    created.append(_spacer(doc, 14))

    if info.tagline:
        created.append(_para(doc, info.tagline, 12, italic=True, center=True, grey=True))
    elif info.course:
        created.append(_para(doc, info.course, 12.5, center=True, grey=True))

    rows = info.rows()
    if rows:
        created.append(_spacer(doc, 6))
        created.append(_field_table(doc, rows))

    created.append(_page_break(doc))
    return created


def _layout_split(doc, info: CoverInfo) -> list:
    """A submission sheet: the brief on top, the submitter beneath it."""
    created = [_rule(doc, thick=True, before=0, after=14)]

    eyebrow = " · ".join(
        part for part in (info.department, info.course) if part
    )
    if eyebrow:
        created.append(_para(doc, eyebrow.upper(), 9.5, bold=True, grey=True, before=0, after=6))

    # The heading is unconditional. An earlier version of this layout showed
    # only department, course and identity -- so a manual whose front matter
    # named none of them produced a cover with nothing on it at all. A cover
    # has to say what it is covering even when it knows nothing else.
    created.append(_para(doc, info.heading(), 19, bold=True, before=0, after=6))

    if info.tagline:
        created.append(_para(doc, info.tagline, 11, italic=True, grey=True, before=0, after=4))

    created.append(_rule(doc, before=10, after=14))

    name = info.student_name.strip()
    if name:
        created.append(_para(doc, "Submitted by", 8.5, bold=True, grey=True, before=0, after=2))
        created.append(_para(doc, name, 15, bold=True, before=0, after=4))

    identity = [(label, value) for label, value in info.identity_rows() if label != "Name"]
    if identity:
        created.append(_field_table(doc, identity, bordered=False))

    trailing = info.course_rows()
    if trailing:
        created.append(_rule(doc, before=14, after=10))
        created.append(_field_table(doc, trailing))

    created.append(_page_break(doc))
    return created


_RENDERERS = {
    "classic": _layout_classic,
    "rule": _layout_rule,
    "banner": _layout_banner,
    "split": _layout_split,
}


def build_cover(doc, info: CoverInfo) -> None:
    """Prepend a cover sheet to an open Document, in place."""
    renderer = _RENDERERS.get(info.layout, _layout_classic)
    created = renderer(doc, info)

    # Move everything built above to the front of the body, preserving order.
    # Inserting at 0..n in order is what keeps it unreversed.
    body = doc.element.body
    for position, item in enumerate(created):
        element = item._element if hasattr(item, "_element") else item._tbl
        body.remove(element)
        body.insert(position, element)


def cover_from(
    spec,
    profile,
    facts,
    layout: str = "classic",
    tagline: str = "",
) -> CoverInfo:
    """Assemble a CoverInfo from the three things that know something about it.

    Precedence is deliberate: the student's own profile wins for section (they
    may have moved), and the manual wins for everything it states, because it is
    the authoritative document for the course it belongs to.

    `layout` and `tagline` default to the previous behaviour, so existing
    callers -- the tests -- are unaffected by the new designs.
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
        layout=layout or "classic",
        tagline=tagline or "",
    )
