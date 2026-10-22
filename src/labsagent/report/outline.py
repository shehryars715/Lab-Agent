"""A text outline of a finished file, for the editing agent to read.

A MODEL CAN'T USE WHAT IT CAN'T SEE. The editing agent works on a .docx it
cannot read -- `read_file` refuses binary files, on purpose -- so without this
it would have to spend turns printing the document to itself before it could
change anything. The outline is built by code, once, and goes in its first
message: what is in the file, in order, under the names python-docx gives it
(`doc.paragraphs[i]`, `doc.tables[i]`), so the script it writes can find
things. Capped, because a 40-page report does not need every line to restyle
its headings.
"""

from __future__ import annotations

import re
from pathlib import Path

MAX_LINES = 220
MAX_CHARS = 12_000
SNIPPET = 80


def _cut(text: str, n: int = SNIPPET) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _docx(path: Path) -> list[str]:
    import docx
    from docx.oxml.ns import qn

    doc = docx.Document(str(path))
    body = doc.element.body
    lines = [
        f"Word document: {len(doc.paragraphs)} paragraphs, {len(doc.tables)} tables, "
        f"{len(doc.inline_shapes)} pictures, {len(doc.sections)} section(s).",
        "Body in order. P<i> is doc.paragraphs[i], T<i> is doc.tables[i]; "
        "[Style] is the paragraph's style name.",
    ]
    p_index = t_index = 0
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            para = doc.paragraphs[p_index]
            marks = []
            if child.findall(f".//{qn('w:drawing')}"):
                marks.append("picture")
            if any(br.get(qn("w:type")) == "page" for br in child.iter(qn("w:br"))):
                marks.append("page break")
            text = _cut(para.text)
            if text or marks:
                style = para.style.name if para.style is not None else ""
                tail = f" ({', '.join(marks)})" if marks else ""
                lines.append(f"P{p_index} [{style}] {text}{tail}".rstrip())
            p_index += 1
        elif child.tag == qn("w:tbl"):
            table = doc.tables[t_index]
            rows, cols = len(table.rows), len(table.columns)
            first = " | ".join(_cut(c.text, 30) for c in table.rows[0].cells) if rows else ""
            lines.append(f"T{t_index} table {rows}x{cols}: {first}")
            t_index += 1
    for i, section in enumerate(doc.sections):
        header = _cut(" ".join(p.text for p in section.header.paragraphs), 60)
        footer = _cut(" ".join(p.text for p in section.footer.paragraphs), 60)
        if header or footer:
            lines.append(f"Section {i}: header '{header}', footer '{footer}'")
    return lines


def _ipynb(path: Path) -> list[str]:
    import nbformat

    nb = nbformat.read(str(path), as_version=4)
    lines = [f"Notebook: {len(nb.cells)} cells. C<i> is nb.cells[i]."]
    for i, cell in enumerate(nb.cells):
        first = _cut((cell.source or "").splitlines()[0] if cell.source else "")
        kinds = sorted({o.get("output_type", "") for o in cell.get("outputs", [])})
        tail = f" (outputs: {', '.join(kinds)})" if kinds else ""
        lines.append(f"C{i} {cell.cell_type}: {first}{tail}")
    return lines


def _text(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() == ".html":
        # The page is one long line of markup in places; show its structure.
        raw = re.sub(r"(<(?:h[1-6]|p|pre|section|table|img|header)\b)", r"\n\1", raw)
    lines = [f"{path.suffix.lstrip('.')} file, {len(raw.splitlines())} lines. L<n> is line n."]
    lines += [f"L{n} {_cut(line, 100)}" for n, line in enumerate(raw.splitlines(), 1) if line.strip()]
    return lines


def outline(path: Path | str) -> str:
    """The outline of one file, capped. Never raises: a file it cannot read
    gets a one-line outline and the agent can print it for itself."""
    path = Path(path)
    try:
        if path.suffix.lower() == ".docx":
            lines = _docx(path)
        elif path.suffix.lower() == ".ipynb":
            lines = _ipynb(path)
        else:
            lines = _text(path)
    except Exception as exc:  # noqa: BLE001
        return f"(could not outline {path.name}: {type(exc).__name__})"
    out: list[str] = []
    size = 0
    for line in lines:
        if len(out) >= MAX_LINES or size + len(line) > MAX_CHARS:
            out.append(f"... ({len(lines) - len(out)} more lines; print the file to see them)")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)
