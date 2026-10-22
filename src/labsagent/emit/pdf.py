"""`.pdf` -- the report as a PDF, drawn from the same content as the rest.

WHY FROM THE CONTENT AND NOT FROM THE WORD FILE (decided 2026-10-22). The
faithful route is to convert the finished .docx with LibreOffice, and it was
weighed: about 350 MB more in the image and about 200 MB of memory per
conversion on a 1 GB server. The owner chose the small route instead: draw the
PDF straight from the block list every other emitter renders, with fpdf2 (pure
Python, no system packages).

The trade-off is stated, not hidden. This PDF does not copy the manual's own
formatting the way the annotated Word report does, and a change the editing
agent makes to the Word file does not reach it -- the chat says so when both
apply. Everything in the report spec does reach it: the cover, the layout,
what each answer holds, labels, sizes and page breaks.

Fonts: DejaVu Sans and Sans Mono, which matplotlib already ships (so nothing
is bundled and every alphabet a student writes in renders), with Cascadia Mono
as the code font's fallback.
"""

from __future__ import annotations

from pathlib import Path

from fpdf import FPDF
from fpdf.enums import TableCellFillMode, XPos, YPos
from fpdf.fonts import FontFace

from labsagent.blocks import has_screenshot_twin
from labsagent.emit import EmitContext, register
from labsagent.present import arrange

BODY_PT = 10.0
CODE_PT = 8.5
ACCENT = (79, 70, 229)
GREY = (89, 89, 89)
CODE_FILL = (242, 242, 242)
BANNER_FILL = (233, 235, 251)


def _font_files() -> dict[tuple[str, str], Path]:
    """The TTFs to embed, found where they already are."""
    import matplotlib

    ttf = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    files = {
        ("Body", ""): ttf / "DejaVuSans.ttf",
        ("Body", "B"): ttf / "DejaVuSans-Bold.ttf",
        ("Body", "I"): ttf / "DejaVuSans-Oblique.ttf",
        ("Mono", ""): ttf / "DejaVuSansMono.ttf",
    }
    if not files[("Mono", "")].exists():
        files[("Mono", "")] = (
            Path(__file__).resolve().parents[1] / "capture" / "assets" / "CascadiaMono-Regular.ttf"
        )
    return files


class _Report(FPDF):
    """A4 with page numbers on every page but the cover."""

    cover_pages = 0

    def footer(self) -> None:
        if self.page_no() <= self.cover_pages:
            return
        self.set_y(-12)
        self.set_font("Body", "", 8)
        self.set_text_color(*GREY)
        self.cell(0, 6, f"Page {self.page_no() - self.cover_pages}", align="C")
        self.set_text_color(0, 0, 0)


def _new(ctx: EmitContext) -> _Report:
    pdf = _Report(orientation="P", unit="mm", format="A4")
    pdf.set_margins(18, 16, 18)
    pdf.set_auto_page_break(auto=True, margin=16)
    for (family, style), path in _font_files().items():
        pdf.add_font(family, style, str(path))
    title = ctx.presentation.cover.title or ctx.spec.title or f"Lab {ctx.spec.lab_number}"
    pdf.set_title(title)
    if ctx.profile.name:
        pdf.set_author(ctx.profile.name)
    return pdf


def _text(pdf: FPDF, text: str, size: float, *, style: str = "", grey=False, align="L", gap=1.5):
    pdf.set_font("Body", style, size)
    if grey:
        pdf.set_text_color(*GREY)
    pdf.multi_cell(0, size * 0.5, text, align=align, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(gap)


def _rule(pdf: FPDF, thick: bool = False) -> None:
    pdf.set_draw_color(*ACCENT)
    pdf.set_line_width(0.9 if thick else 0.3)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(4)
    pdf.set_line_width(0.2)
    pdf.set_draw_color(0, 0, 0)


def _rows(pdf: FPDF, rows: list[tuple[str, str]], *, boxed: bool = True) -> None:
    if not rows:
        return
    pdf.set_font("Body", "", 10)
    # The banner leaves its tint as the current fill; a row must not inherit it.
    pdf.set_fill_color(255, 255, 255)
    with pdf.table(
        cell_fill_mode=TableCellFillMode.NONE,
        col_widths=(45, 95),
        width=140,
        align="C",
        borders_layout="ALL" if boxed else "NONE",
        first_row_as_headings=False,
        text_align="LEFT",
        line_height=6,
    ) as table:
        for label, value in rows:
            row = table.row()
            row.cell(label, style=FontFace(emphasis="BOLD"))
            row.cell(value)
    pdf.ln(4)


def _cover(pdf: _Report, info) -> None:
    """One page per cover, in the spirit of each Word layout."""
    pdf.add_page()
    layout = getattr(info, "layout", "classic")
    if layout == "banner":
        pdf.set_fill_color(*BANNER_FILL)
        pdf.rect(0, 0, pdf.w, 46, style="F")
        pdf.set_y(16)
        _text(pdf, info.heading(), 19, style="B")
        if info.department:
            _text(pdf, info.department.upper(), 9.5, grey=True)
        pdf.set_y(56)
        line = info.tagline or info.course
        if line:
            _text(pdf, line, 12, style="I" if info.tagline else "", grey=True, align="C")
        _rows(pdf, info.rows())
    elif layout == "rule":
        pdf.set_y(40)
        _rule(pdf, thick=True)
        eyebrow = " · ".join(
            p for p in (f"LAB {info.lab_number}" if info.lab_number else "", info.course.upper()) if p
        )
        if eyebrow:
            pdf.set_text_color(*ACCENT)
            _text(pdf, eyebrow, 9.5, style="B")
        _text(pdf, info.title or info.lab_title or info.heading(), 24, style="B", gap=3)
        if info.tagline:
            _text(pdf, info.tagline, 11.5, style="I", grey=True)
        _rule(pdf)
        _rows(pdf, [(label.upper(), value) for label, value in info.rows()], boxed=False)
    elif layout == "split":
        pdf.set_y(30)
        _rule(pdf, thick=True)
        eyebrow = " · ".join(p for p in (info.department, info.course) if p)
        if eyebrow:
            _text(pdf, eyebrow.upper(), 9.5, style="B", grey=True)
        _text(pdf, info.heading(), 19, style="B")
        if info.tagline:
            _text(pdf, info.tagline, 11, style="I", grey=True)
        _rule(pdf)
        if info.student_name.strip():
            _text(pdf, "Submitted by", 8.5, style="B", grey=True, gap=0.5)
            _text(pdf, info.student_name.strip(), 15, style="B")
        _rows(pdf, [r for r in info.identity_rows() if r[0] != "Name"], boxed=False)
        trailing = info.course_rows()
        if trailing:
            _rule(pdf)
            _rows(pdf, trailing)
    else:
        pdf.set_y(60)
        if info.department:
            _text(pdf, info.department.upper(), 11, grey=True, align="C")
        _text(pdf, info.heading(), 22, style="B", align="C", gap=2)
        if info.course:
            _text(pdf, info.course, 13, grey=True, align="C")
        if info.tagline:
            _text(pdf, info.tagline, 11, style="I", grey=True, align="C")
        pdf.ln(6)
        _rows(pdf, info.rows())
    pdf.cover_pages = pdf.page_no()


def _label(pdf: FPDF, text: str, size: float) -> None:
    if text:
        pdf.ln(1)
        _text(pdf, text, size, style="B", gap=0.5)


def _mono(pdf: FPDF, text: str, size: float, *, fill: bool) -> None:
    pdf.set_font("Mono", "", size)
    pdf.set_fill_color(*CODE_FILL)
    body = text.rstrip("\n").replace("\t", "    ") or " "
    pdf.multi_cell(0, size * 0.48, body, fill=fill, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)


def _picture(pdf: FPDF, path: Path) -> None:
    """As wide as the page allows, never taller than one page."""
    from PIL import Image

    with Image.open(path) as image:
        width_px, height_px = image.size
    width = pdf.epw
    height = width * height_px / max(1, width_px)
    room = pdf.h - pdf.t_margin - pdf.b_margin - 4
    if height > room:
        width, height = width * room / height, room
    if pdf.get_y() + height > pdf.h - pdf.b_margin:
        pdf.add_page()
    pdf.image(str(path), x=pdf.l_margin, w=width, h=height)
    pdf.ln(3)


def build_pdf(ctx: EmitContext, out_path: Path) -> Path:
    pres = ctx.presentation
    body_pt, code_pt = pres.body_pt or BODY_PT, pres.code_pt or CODE_PT
    pdf = _new(ctx)
    if ctx.cover is not None:
        _cover(pdf, ctx.cover)

    for position, outcome in enumerate(ctx.outcomes):
        task = outcome.task
        include = ctx.plan_for(task.id).include
        words = ctx.words_for(task.id)
        if position == 0 or pres.page_breaks:
            pdf.add_page()
        else:
            pdf.ln(4)
        _text(pdf, task.title or task.id, body_pt + 5, style="B", gap=2)
        if include.statement and task.statement.strip():
            _text(pdf, task.statement.strip(), body_pt, gap=2)

        labelled_output = False
        answers_headed = False
        blocks = arrange(outcome, ctx.style_for(task.id), include)
        for index, block in enumerate(blocks):
            if block.kind == "code":
                _label(pdf, block.title or words.code, body_pt)
                _mono(pdf, block.text, code_pt, fill=True)
                labelled_output = False
            elif block.kind == "error":
                _label(pdf, words.status, body_pt)
                _text(pdf, block.text, body_pt)
            elif block.kind == "image" and block.path is not None and Path(block.path).exists():
                if not labelled_output:
                    _label(pdf, words.output, body_pt)
                    labelled_output = True
                _picture(pdf, Path(block.path))
            elif block.kind == "output" and block.text.strip() and not has_screenshot_twin(blocks, index):
                if not labelled_output:
                    _label(pdf, words.output, body_pt)
                    labelled_output = True
                _mono(pdf, block.text, code_pt, fill=False)
            elif block.kind == "prose":
                if block.role == "answer" and words.answers and not answers_headed:
                    _label(pdf, words.answers, body_pt + 2)
                    answers_headed = True
                if block.title:
                    _label(pdf, block.title, body_pt)
                _text(pdf, block.text, body_pt, gap=2)

    if not ctx.outcomes and ctx.cover is None:
        pdf.add_page()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(out_path))
    return out_path


class PdfEmitter:
    name = "pdf"
    label = "PDF"
    kind = "report"
    extensions = (".pdf",)

    def emit(self, ctx: EmitContext) -> list[Path]:
        return [build_pdf(ctx, ctx.out_dir / f"{ctx.report_name()}.pdf")]


register(PdfEmitter())
