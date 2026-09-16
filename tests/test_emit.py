"""The output seam: the registry, the isolation promise, and each emitter."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from labsagent.emit import DEFAULT_ARTIFACTS, REGISTRY, EmitContext, emit_all, order
from labsagent.models import LabSpec, Task, TaskOutcome, Transcript
from labsagent.profile import StudentProfile


def _spec() -> LabSpec:
    return LabSpec(
        lab_number="03",
        title="Lab 03",
        course="CS-102",
        tasks=[Task(id="task1", title="Sum", statement="Read two ints and print the sum.")],
    )


def _outcomes(tmp_path: Path) -> list[TaskOutcome]:
    code = tmp_path / "task1.py"
    code.write_text("print(1 + 2)\n", encoding="utf-8")
    return [
        TaskOutcome(
            task=_spec().tasks[0],
            status="passed",
            code_path=code,
            code_text="print(1 + 2)\n",
            transcript=Transcript(command="python task1.py", lines=["3"]),
            explanation="It adds two numbers.",
            attempts=1,
        )
    ]


@pytest.fixture
def ctx(tmp_path: Path) -> EmitContext:
    return EmitContext(
        spec=_spec(),
        outcomes=_outcomes(tmp_path),
        out_dir=tmp_path / "out",
        profile=StudentProfile(name="A Student", cms_id="22F-1234"),
    )


def test_every_format_is_registered():
    assert set(REGISTRY) == {"docx", "ipynb", "py", "md", "zip"}


def test_the_archive_runs_last_so_it_can_package_the_rest():
    assert order(["zip", "docx", "ipynb"]) == ["docx", "ipynb", "zip"]


def test_unknown_formats_are_dropped_not_fatal():
    assert order(["docx", "pdf", "docx"]) == ["docx"]


def test_a_single_request_yields_a_single_file(ctx):
    written = emit_all(ctx, ["py"])

    assert [p.name for p in written] == ["Lab03_22F-1234.py"]


def test_one_failing_emitter_does_not_sink_the_others(ctx, monkeypatch):
    """The failure this whole seam exists to stop: a rendering bug used to cost
    a fully-solved, fully-paid run every one of its artifacts."""
    def boom(self, c):
        raise IndexError("anchor 47 outside document")

    monkeypatch.setattr(type(REGISTRY["docx"]), "emit", boom)
    seen: list[str] = []
    written = emit_all(ctx, ["docx", "py", "md"], on_error=lambda n, e: seen.append(n))

    assert seen == ["docx"]
    assert sorted(p.suffix for p in written) == [".md", ".py"]


def test_script_carries_the_code_and_the_statement(ctx):
    body = emit_all(ctx, ["py"])[0].read_text(encoding="utf-8")

    assert "print(1 + 2)" in body
    assert "Read two ints" in body
    assert "# Sum" in body


def test_markdown_fences_code_and_output(ctx):
    body = emit_all(ctx, ["md"])[0].read_text(encoding="utf-8")

    assert "```python" in body and "print(1 + 2)" in body
    assert "```text" in body, "the transcript should appear as a fenced block"
    assert body.startswith("# Lab 03")


def test_notebook_embeds_real_output(ctx):
    nb = json.loads(emit_all(ctx, ["ipynb"])[0].read_text(encoding="utf-8"))
    code_cells = [c for c in nb["cells"] if c["cell_type"] == "code"]
    outputs = [o for c in code_cells for o in c.get("outputs", [])]

    assert any(o.get("output_type") == "stream" and "3" in "".join(o["text"])
               for o in outputs), "the notebook should open already showing results"


def test_archive_packages_whatever_ran_before_it(ctx):
    written = emit_all(ctx, ["py", "md", "zip"])
    archive = next(p for p in written if p.suffix == ".zip")

    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()

    assert "Lab03_22F-1234.py" in names and "Lab03_22F-1234.md" in names
    assert "code/task1.py" in names, "the per-task source still ships under code/"


def test_docx_falls_back_to_a_fresh_document_without_anchors(ctx):
    """A PDF or a pasted lab has no paragraph coordinates. That used to be an
    IndexError raised after every task had already been solved and paid for."""
    written = emit_all(ctx, ["docx"])

    assert [p.name for p in written] == ["Lab03_Report.docx"]
    assert written[0].stat().st_size > 0


def test_docx_annotates_in_place_when_anchors_exist(ctx, manual_path):
    """The original design is preserved as one emitter among several."""
    import docx

    from labsagent.report.docx_utils import flatten_paragraphs

    ctx.manual_path = manual_path
    ctx.anchors = {"task1": 8}
    out = emit_all(ctx, ["docx"])[0]
    texts = [p.text for p in flatten_paragraphs(docx.Document(str(out)))]

    assert "Code:" in texts and "Output:" in texts
    assert any("Task 1" in t for t in texts), "original manual content survives"


def test_the_default_set_still_contains_what_it_always_produced(ctx):
    """docx + notebook + zip is what every run made before -- the notebook was
    simply buried inside the archive with no download of its own."""
    assert set(DEFAULT_ARTIFACTS) == {"docx", "ipynb", "zip"}


def test_a_non_docx_source_never_takes_the_annotate_branch(ctx, tmp_path):
    """REGRESSION. Anchors used to be minted for every input format, so a .txt
    upload arrived carrying line indices that look exactly like Word paragraph
    indices. The emitter took the annotate-in-place branch and python-docx blew
    up with PackageNotFoundError trying to open plain text as a document.

    Found by the live matrix, not by unit tests -- the earlier fresh-document
    test set manual_path=None, which is the one case that could not hit it.
    """
    source = tmp_path / "lab.txt"
    source.write_text("Task 1: do a thing", encoding="utf-8")
    ctx.manual_path = source
    ctx.anchors = {"task1": 0}

    written = emit_all(ctx, ["docx"])

    assert [p.name for p in written] == ["Lab03_Report.docx"]
    assert written[0].stat().st_size > 0
