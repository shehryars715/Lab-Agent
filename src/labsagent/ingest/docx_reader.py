"""DOCX -> a numbered, model-readable view of the manual.

Deliberately dumb: this module does no interpretation at all. It flattens the
document into an indexed paragraph list (table cells included) and hands it on.
Parsing and interpretation are separate jobs, and keeping them separate means
the parser is testable without a model and the interpreter is testable without
a document.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import docx

from labsagent.errors import IngestError
from labsagent.report.docx_utils import flatten_paragraphs


@dataclass(frozen=True)
class ManualParagraph:
    idx: int
    text: str
    style: str
    in_table: bool


@dataclass(frozen=True)
class RawManual:
    path: Path
    paragraphs: list[ManualParagraph]
    image_names: list[str]

    def as_numbered_text(self, max_chars: int = 400) -> str:
        """The view the model sees. Indices are the anchor coordinate space."""
        lines = []
        for p in self.paragraphs:
            body = p.text.strip()
            if len(body) > max_chars:
                body = body[:max_chars] + "..."
            tag = f" [{p.style}]" if p.style != "Normal" else ""
            lines.append(f"[{p.idx}]{tag} {body}")
        return "\n".join(lines)


def read_manual(path: Path) -> RawManual:
    path = Path(path)
    try:
        doc = docx.Document(str(path))
    except Exception as exc:
        raise IngestError(f"could not open {path}: {exc}") from exc

    flat = flatten_paragraphs(doc)
    paragraphs = [
        ManualParagraph(
            idx=i,
            text=p.text,
            style=p.style.name if p.style else "Normal",
            # A task in a table needs its work inserted after the whole table,
            # which docx_builder handles -- but knowing is useful for debugging.
            in_table=p._parent is not doc,
        )
        for i, p in enumerate(flat)
    ]

    # DeepSeek is text-only: images are extracted for the record, never read.
    images = [
        r.target_ref.rsplit("/", 1)[-1]
        for r in doc.part.rels.values()
        if "image" in r.reltype
    ]

    if not paragraphs:
        raise IngestError(f"{path} contains no paragraphs")

    return RawManual(path=path, paragraphs=paragraphs, image_names=images)
