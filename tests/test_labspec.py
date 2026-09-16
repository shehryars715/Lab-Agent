"""Ingestion tests. Anchor reconciliation is tested offline -- no tokens spent."""

from __future__ import annotations

import pytest

from labsagent.errors import SpecError
from labsagent.ingest.docx_reader import read_manual
from labsagent.ingest.labspec import ExtractedLab, ExtractedTask, to_labspec


@pytest.fixture
def manual(manual_path):
    return read_manual(manual_path)


def _lab(**overrides) -> ExtractedLab:
    task = ExtractedTask(
        task_number=1,
        title="Sum of Two Numbers",
        statement="Read two integers and print their sum.",
        sample_inputs=["5", "3"],
        anchor_idx=overrides.pop("anchor_idx", 8),
        anchor_quote=overrides.pop("anchor_quote", "Sample run: n = 5, m = 3"),
        **overrides,
    )
    return ExtractedLab(lab_number="03", title="Lab 03", course="CS-102", tasks=[task])


def test_reader_includes_table_paragraphs(manual):
    assert any("reads 5 integers" in p.text for p in manual.paragraphs)


def test_numbered_view_is_indexed_for_the_model(manual):
    text = manual.as_numbered_text()
    assert "[0]" in text and "[8]" in text
    assert "[Heading 2] Task 1" in text


def test_correct_anchor_needs_no_repair(manual):
    spec, anchors, repairs = to_labspec(_lab(anchor_idx=8), manual)

    assert anchors["task1"] == 8
    assert repairs == []


def test_miscounted_anchor_is_corrected_by_its_quote(manual):
    """The whole point of the redundant field: models miscount, but quote well."""
    spec, anchors, repairs = to_labspec(_lab(anchor_idx=6), manual)

    assert anchors["task1"] == 8, "quote should have overridden the bad index"
    assert len(repairs) == 1
    assert repairs[0].claimed == 6 and repairs[0].corrected == 8


def test_out_of_range_anchor_is_recovered_from_its_quote(manual):
    spec, anchors, repairs = to_labspec(_lab(anchor_idx=999), manual)

    assert anchors["task1"] == 8
    assert "out of range" in repairs[0].reason


def test_unusable_anchor_fails_loudly(manual):
    with pytest.raises(SpecError, match="matches no paragraph"):
        to_labspec(_lab(anchor_idx=999, anchor_quote="text that appears nowhere at all"), manual)


def test_fuzzy_quote_still_recovers(manual):
    """A near-miss quote (model paraphrased slightly) should still land."""
    spec, anchors, repairs = to_labspec(
        _lab(anchor_idx=2, anchor_quote="Sample run: n=5, m=3 gives Sum=8"), manual
    )

    assert anchors["task1"] == 8
    assert repairs and "fuzzy" in repairs[0].reason


def test_tasks_are_ordered_by_number(manual):
    lab = ExtractedLab(
        lab_number="03",
        title="Lab 03",
        tasks=[
            ExtractedTask(task_number=2, title="B", statement="s", anchor_idx=10,
                          anchor_quote="Write a program that reads a positive"),
            ExtractedTask(task_number=1, title="A", statement="s", anchor_idx=8,
                          anchor_quote="Sample run: n = 5, m = 3"),
        ],
    )
    spec, _, _ = to_labspec(lab, manual)

    assert [t.id for t in spec.tasks] == ["task1", "task2"]


def test_empty_task_list_is_an_error(manual):
    with pytest.raises(SpecError, match="no tasks"):
        to_labspec(ExtractedLab(lab_number="03", title="Lab 03", tasks=[]), manual)


def test_anchors_are_dropped_for_a_non_docx_source(manual):
    """A .txt line index is not a Word paragraph index. Keeping the quote
    reconciliation but dropping the coordinate is the whole distinction."""
    from dataclasses import replace as dc_replace

    as_text = dc_replace(manual, path=manual.path.with_suffix(".txt"))
    spec, anchors, repairs = to_labspec(_lab(anchor_idx=8), as_text)

    assert spec.tasks, "the tasks still come through"
    assert anchors == {}, "but the document coordinates do not"
