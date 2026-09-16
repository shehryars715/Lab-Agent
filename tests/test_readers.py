"""Every input shape reduces to the same numbered view."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from labsagent.errors import IngestError
from labsagent.ingest.readers import (
    ACCEPTED_SUFFIXES,
    read_document,
    read_pasted,
    produces_anchors,
)


def test_markdown_becomes_numbered_paragraphs(tmp_path: Path):
    path = tmp_path / "lab.md"
    path.write_text("# Lab 7\n\nTask 1: print hello\n", encoding="utf-8")
    manual = read_document(path)

    assert [p.text for p in manual.paragraphs] == ["# Lab 7", "Task 1: print hello"]
    assert "[0] [Heading] # Lab 7" in manual.as_numbered_text()


def test_a_notebook_lab_is_readable(tmp_path: Path):
    """The DS311 shape: the assignment is handed out AS a notebook."""
    path = tmp_path / "lab.ipynb"
    path.write_text(
        json.dumps(
            {
                "cells": [
                    {"cell_type": "markdown", "source": ["# Data Mining\n", "Task 1: load it\n"]},
                    {"cell_type": "code", "source": ["import pandas as pd\n", "df = read()\n"]},
                ]
            }
        ),
        encoding="utf-8",
    )
    manual = read_document(path)
    styles = [p.style for p in manual.paragraphs]

    assert styles == ["Heading", "Normal", "Code"]
    assert "import pandas" in manual.paragraphs[2].text
    assert "\n" in manual.paragraphs[2].text, "a code cell stays one runnable unit"


def test_a_python_file_is_all_code(tmp_path: Path):
    path = tmp_path / "given.py"
    path.write_text("x = 1\ny = 2\n", encoding="utf-8")

    assert {p.style for p in read_document(path).paragraphs} == {"Code"}


def test_a_pasted_lab_needs_no_file():
    manual = read_pasted("# Lab 2\nTask 1: sum two integers")

    assert len(manual.paragraphs) == 2
    assert manual.path.name == "pasted-lab.txt"


def test_an_empty_document_is_an_ingest_error(tmp_path: Path):
    path = tmp_path / "blank.txt"
    path.write_text("   \n\n  \n", encoding="utf-8")

    with pytest.raises(IngestError, match="no readable text"):
        read_document(path)


def test_only_docx_carries_anchors(tmp_path: Path):
    """Everything else has no paragraph coordinates, so the DOCX emitter must
    build a fresh document rather than try to annotate one."""
    assert produces_anchors(Path("lab.docx"))
    assert not produces_anchors(Path("lab.pdf"))
    assert not produces_anchors(Path("lab.ipynb"))


def test_the_docx_reader_is_still_the_docx_reader(manual_path):
    manual = read_document(manual_path)

    assert any("reads 5 integers" in p.text for p in manual.paragraphs), (
        "table-cell paragraphs must still be flattened in"
    )


def test_accepted_suffixes_are_what_the_browser_is_offered():
    assert ".docx" in ACCEPTED_SUFFIXES and ".ipynb" in ACCEPTED_SUFFIXES
    assert ".pdf" in ACCEPTED_SUFFIXES and ".md" in ACCEPTED_SUFFIXES
