"""XML-level helpers python-docx does not expose.

In-place annotation needs three things the public API lacks: inserting after an
arbitrary paragraph, paragraph shading, and a paragraph walk that includes table
cells.
"""

from __future__ import annotations

from typing import Iterator

from docx.document import Document as DocumentObject
from docx.oxml.ns import qn
from docx.oxml.parser import OxmlElement
from docx.shared import Pt
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph


def iter_block_items(parent) -> Iterator[Paragraph | Table]:
    """Yield paragraphs and tables in true document order.

    doc.paragraphs silently skips anything inside a table. Many lab manuals wrap
    their tasks in a one-column table, so a naive walk finds zero anchors with
    no obvious cause.
    """
    if isinstance(parent, DocumentObject):
        parent_elm = parent.element.body
    elif isinstance(parent, _Cell):
        parent_elm = parent._tc
    else:
        raise ValueError(f"cannot iterate blocks of {type(parent)!r}")

    for child in parent_elm.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, parent)
        elif child.tag == qn("w:tbl"):
            yield Table(child, parent)


def flatten_paragraphs(doc: DocumentObject) -> list[Paragraph]:
    """Every paragraph in document order, table cells included.

    This list defines the index space that Task.anchor_idx refers to.
    """
    out: list[Paragraph] = []
    for block in iter_block_items(doc):
        if isinstance(block, Paragraph):
            out.append(block)
        else:
            for row in block.rows:
                for cell in row.cells:
                    for inner in iter_block_items(cell):
                        if isinstance(inner, Paragraph):
                            out.append(inner)
    return out


def insert_paragraph_after(paragraph: Paragraph, text: str | None = None) -> Paragraph:
    """Insert a new empty paragraph directly after `paragraph`.

    python-docx has no native insert-after; this is the XML route. Note that
    inserting twice after the SAME anchor yields reverse order -- callers must
    advance their cursor to each newly created paragraph.
    """
    new_p = OxmlElement("w:p")
    paragraph._p.addnext(new_p)
    para = Paragraph(new_p, paragraph._parent)
    if text:
        para.add_run(text)
    return para


def enclosing_table(paragraph: Paragraph):
    """Return the outermost w:tbl ancestor of `paragraph`, or None.

    Outermost matters: nested tables would otherwise strand content inside the
    outer one.
    """
    tbl = None
    node = paragraph._p.getparent()
    while node is not None:
        if node.tag == qn("w:tbl"):
            tbl = node
        node = node.getparent()
    return tbl


def insert_paragraph_after_table(tbl, parent) -> Paragraph:
    """Insert a new paragraph immediately after a table, at body level."""
    new_p = OxmlElement("w:p")
    tbl.addnext(new_p)
    return Paragraph(new_p, parent)


def shade(paragraph: Paragraph, fill: str) -> None:
    """Apply a solid background fill. python-docx does not expose w:shd."""
    pr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    pr.append(shd)


def table_borders(table, color: str = "auto", size: int = 4) -> None:
    """Box every cell, without depending on a style the document may not have.

    `table.style = "Table Grid"` is the usual way to do this and it is a trap
    the moment the document is not one you created. Word writes only the styles
    a document actually USES into styles.xml, so a manual that never contained
    a bordered table simply has no 'Table Grid' and python-docx raises
    `KeyError: no style with name 'Table Grid'`.

    That is not hypothetical: of the two real manuals in `labs/`, Lab 10 ships
    57 styles and has it, Lab 13 ships 21 and does not. Which one you got was
    luck, and on Lab 13 it cost the entire Word report after every task had
    been solved and paid for.

    Direct formatting also makes the cover look the same everywhere, because
    even where 'Table Grid' exists its appearance is whatever THAT document
    defines it to be. Same reasoning as `style_code_run` below.
    """
    pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), str(size))
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), color)
        borders.append(element)
    pr.append(borders)


def style_code_run(run, font: str = "Consolas", size_pt: float = 9.0) -> None:
    """Direct run formatting, deliberately not a named style.

    Adding a style called 'Code' risks colliding with one the manual already
    defines -- and silently restyling the manual is exactly what we promised not
    to do.
    """
    run.font.name = font
    run.font.size = Pt(size_pt)
    # East-Asian font name must be set separately or Word may substitute.
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), font)


def tighten(paragraph: Paragraph, before: float = 0, after: float = 0) -> None:
    fmt = paragraph.paragraph_format
    fmt.space_before = Pt(before)
    fmt.space_after = Pt(after)
