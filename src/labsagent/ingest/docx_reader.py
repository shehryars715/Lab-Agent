"""DOCX -> a numbered, model-readable view of the manual.

Deliberately dumb: this module does no interpretation at all. It flattens the
document into an indexed paragraph list (table cells included) and hands it on.
Parsing and interpretation are separate jobs, and keeping them separate means
the parser is testable without a model and the interpreter is testable without
a document.
"""

from __future__ import annotations

import re
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
    #: (shown text, target URL) for every external link in the paragraph. A
    #: separate field, not folded into `text`: `text` is what anchor quotes are
    #: verified against and what cover facts are scraped from, and a URL
    #: appended there would move both.
    links: tuple[tuple[str, str], ...] = ()

    def shown(self) -> str:
        """The text as the model sees it: words first, then where they point."""
        body = self.text.strip()
        return f"{body} {_render_links(self.links)}".strip() if self.links else body


def _render_links(links) -> str:
    return " ".join(
        f"(link: {text} -> {url})" if text and text != url else f"(link: {url})"
        for text, url in links
    )


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
            # A BOX THE MODEL CANNOT SEE IS A BOX IT CANNOT FILL. An empty
            # answer cell used to arrive as a bare "[42]", indistinguishable
            # from a blank line, so a manual's own "write your code here" table
            # could never be found. Cells say so, and an empty one says that too.
            if p.in_table:
                tag += " [cell]"
                if not body:
                    body = "(empty)"
            # AFTER the truncation, so a long paragraph never loses its link.
            if p.links:
                body = f"{body} {_render_links(p.links)}"
            lines.append(f"[{p.idx}]{tag} {body}")
        return "\n".join(lines)


#: A field-code hyperlink: `HYPERLINK "https://..."`, in a `w:instrText` run or
#: a `w:fldSimple` attribute (where the quotes arrive as &quot;).
_FIELD_LINK = re.compile(r'HYPERLINK\s+(?:"|&quot;)([^"&]+)(?:"|&quot;)')


def _links_of(paragraph) -> tuple[tuple[str, str], ...]:
    """External link targets in one paragraph. Never raises.

    WHY THIS EXISTS. `paragraph.text` keeps a hyperlink's DISPLAY text and
    drops its target, so "Download Superstore Dataset from Kaggle" reached
    the model with the URL -- the only part that says which dataset -- gone.
    Two real manuals carried their Kaggle link nowhere else.
    """
    found: list[tuple[str, str]] = []
    try:
        for link in paragraph.hyperlinks:
            address = (link.address or "").strip()
            if address.lower().startswith(("http://", "https://", "www.")):
                found.append(((link.text or "").strip(), (link.url or address).strip()))
    except Exception:  # noqa: BLE001 -- a malformed link costs the link, not the manual
        pass
    try:
        known = {url for _, url in found}
        for match in _FIELD_LINK.finditer(paragraph._p.xml):
            url = match.group(1).strip()
            if url.lower().startswith(("http://", "https://", "www.")) and url not in known:
                known.add(url)
                found.append(("", url))
    except Exception:  # noqa: BLE001
        pass
    return tuple(found)


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
            links=_links_of(p),
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
