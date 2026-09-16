"""Any text-ish upload -> the same numbered view the DOCX reader produces.

WHY THIS EXISTS. The upload gate was `data.startswith(b"PK")` -- the ZIP magic
bytes -- so the only acceptable input was a .docx (and, incidentally, any
.xlsx, .pptx or renamed .zip, which sailed through and then failed further in).
A PDF lab, a Colab notebook handed out as the assignment, or a lab pasted into
the chat all bounced at the door with a 415.

Every reader here returns a `RawManual`, so everything downstream -- the
extraction prompt, the task list, the solver -- is unchanged and does not know
or care where the text came from. That is the whole trick: one shape in, one
pipeline behind it.

ANCHORS ARE THE EXCEPTION, and deliberately so. A paragraph index only means
something for the document it indexes, so only `read_manual` produces them. A
run from any other source simply has no anchors, and the DOCX emitter builds a
fresh document instead of annotating one. That used to be an `IndexError` after
the whole run had been paid for.
"""

from __future__ import annotations

import json
from pathlib import Path

from labsagent.errors import IngestError
from labsagent.ingest.docx_reader import ManualParagraph, RawManual, read_manual

#: Suffixes that produce anchors, and therefore an annotated-in-place report.
ANCHORED_SUFFIXES = (".docx",)

TEXT_SUFFIXES = (".md", ".txt", ".text", ".rst", ".py")


def _from_lines(path: Path, entries: list[tuple[str, str]]) -> RawManual:
    """Build a RawManual from (text, style) pairs, dropping blank entries."""
    paragraphs = [
        ManualParagraph(idx=i, text=text, style=style, in_table=False)
        for i, (text, style) in enumerate(
            [(t.rstrip(), s) for t, s in entries if t.strip()]
        )
    ]
    if not paragraphs:
        raise IngestError(f"{path} contains no readable text")
    return RawManual(path=path, paragraphs=paragraphs, image_names=[])


def read_text(path: Path) -> RawManual:
    """Markdown, plain text, or a .py file. One line, one paragraph."""
    path = Path(path)
    try:
        body = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise IngestError(f"could not read {path}: {exc}") from exc

    code = path.suffix.lower() == ".py"
    entries = [
        (line, "Code" if code else ("Heading" if line.lstrip().startswith("#") else "Normal"))
        for line in body.splitlines()
    ]
    return _from_lines(path, entries)


def read_notebook(path: Path) -> RawManual:
    """A .ipynb handed out AS the lab -- the DS311 shape.

    Markdown cells carry the instructions and split by line so headings stay
    visible; a code cell stays whole, because splitting source into lines would
    lose the fact that it is one runnable unit.
    """
    path = Path(path)
    try:
        nb = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as exc:
        raise IngestError(f"could not read notebook {path}: {exc}") from exc

    entries: list[tuple[str, str]] = []
    for cell in nb.get("cells", []):
        source = cell.get("source", "")
        if isinstance(source, list):
            source = "".join(source)
        if cell.get("cell_type") == "code":
            entries.append((source, "Code"))
        else:
            for line in source.splitlines():
                style = "Heading" if line.lstrip().startswith("#") else "Normal"
                entries.append((line, style))
    return _from_lines(path, entries)


def read_pdf(path: Path) -> RawManual:
    path = Path(path)
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover -- declared in pyproject
        raise IngestError("PDF support needs pypdf: uv pip install pypdf") from exc

    try:
        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise IngestError(f"could not read PDF {path}: {exc}") from exc

    return _from_lines(path, [(line, "Normal") for line in text.splitlines()])


def read_pasted(text: str, name: str = "pasted-lab.txt") -> RawManual:
    """No file at all: the chat message IS the document."""
    entries = [
        (line, "Heading" if line.lstrip().startswith("#") else "Normal")
        for line in (text or "").splitlines()
    ]
    return _from_lines(Path(name), entries)


READERS = {
    ".docx": read_manual,
    ".ipynb": read_notebook,
    ".pdf": read_pdf,
    **{suffix: read_text for suffix in TEXT_SUFFIXES},
}

#: What the browser's file picker should offer.
ACCEPTED_SUFFIXES = tuple(sorted(READERS))


def read_document(path: Path) -> RawManual:
    """Dispatch on extension. An unknown suffix is read as plain text."""
    path = Path(path)
    reader = READERS.get(path.suffix.lower(), read_text)
    return reader(path)


def produces_anchors(path: Path) -> bool:
    return Path(path).suffix.lower() in ANCHORED_SUFFIXES
