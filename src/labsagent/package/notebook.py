"""Export a solved lab as a Jupyter/Colab notebook (.ipynb).

Many courses want the notebook, not a Word report -- "Submit only the .ipynb
file on LMS". The notebook is the same solved tasks in a different container, so
it is an exporter over TaskOutcome, not a separate pipeline.

Outputs are embedded as real notebook outputs (stream text for stdout, base64
PNG for figures) rather than pasted in as markdown images. That means the
notebook opens in Colab already showing results, exactly as if it had been run.

BLOCK-DRIVEN SINCE 2026-09-24. This module used to read TaskOutcome's named
fields in one fixed order -- heading, one fat code cell, "**Explanation.**" --
so every notebook had the same skeleton and a `blocks`-only addition never
reached it. It now walks `present.arrange()`, the same list every other emitter
renders, so a style, a written answer or a section is added once and appears
here too. Code becomes a code cell; the output that follows it becomes that
cell's real outputs; prose becomes markdown.
"""

from __future__ import annotations

import base64
from pathlib import Path

import nbformat as nbf

from labsagent.blocks import SCREENSHOT
from labsagent.models import LabSpec, TaskOutcome
from labsagent.present import arrange, vocabulary


def _image_output(path: Path) -> nbf.NotebookNode:
    data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return nbf.v4.new_output(
        "display_data",
        data={"image/png": data, "text/plain": [f"<Figure: {Path(path).name}>"]},
        metadata={},
    )


def _stdout_of(outcome: TaskOutcome, block) -> str:
    """Raw program output for a code cell -- without the terminal prompt lines.

    A section block already carries exactly its own output. The whole-program
    block carries the terminal rendering (`PS C:\\lab> python task1.py`), which
    belongs in a screenshot, not under a notebook cell.
    """
    if block.role == "section":
        return block.text.strip()
    if outcome.transcript is not None:
        return "\n".join(outcome.transcript.lines).strip()
    return block.text.strip()


def _task_cells(outcome: TaskOutcome, style: str) -> list[nbf.NotebookNode]:
    task = outcome.task
    words = vocabulary(style)
    if style == "classic":
        heading = "\n".join([f"## {task.title}", "", task.statement])
    else:
        quoted = "\n".join(f"> {line}" if line.strip() else ">" for line in task.statement.splitlines())
        heading = f"## {task.title}\n\n{quoted}"
    cells = [nbf.v4.new_markdown_cell(heading)]

    code_cell = None
    answers_headed = False
    for block in arrange(outcome, style):
        if block.kind == "code":
            if block.title:
                cells.append(nbf.v4.new_markdown_cell(f"### {block.title}"))
            code_cell = nbf.v4.new_code_cell(block.text.rstrip())
            code_cell.execution_count = None
            cells.append(code_cell)
        elif block.kind == "output" and code_cell is not None and outcome.status == "passed":
            text = _stdout_of(outcome, block)
            if text:
                code_cell.outputs.append(
                    nbf.v4.new_output("stream", name="stdout", text=text + "\n")
                )
        elif block.kind == "image" and block.path is not None:
            if block.role == SCREENSHOT:
                continue  # a notebook shows real output, not a picture of it
            if code_cell is not None and Path(block.path).exists():
                code_cell.outputs.append(_image_output(Path(block.path)))
        elif block.kind == "error":
            cells.append(nbf.v4.new_markdown_cell(f"> **Not completed.** {outcome.error or 'unknown error'}"))
        elif block.kind == "prose":
            if block.role == "answer" and words.answers and not answers_headed:
                cells.append(nbf.v4.new_markdown_cell(f"### {words.answers}"))
                answers_headed = True
            text = f"**{block.title}**\n\n{block.text}" if block.title else block.text
            cells.append(nbf.v4.new_markdown_cell(text))
    return cells


def build_notebook(
    spec: LabSpec,
    outcomes: list[TaskOutcome],
    student: dict[str, str] | None = None,
    style: str = "classic",
    tagline: str = "",
) -> nbf.NotebookNode:
    nb = nbf.v4.new_notebook()

    header = [f"# {spec.title}"]
    if tagline:
        header.append(f"*{tagline}*")
    if spec.course:
        header.append(f"**Course:** {spec.course}")
    for key, value in (student or {}).items():
        header.append(f"**{key.replace('_', ' ').title()}:** {value}")
    # No fixed "# Setup -- !pip install ..." cell any more: it was the same
    # comment in every notebook, including a lab that printed square numbers,
    # and it was the single most obvious sign of a template.
    cells: list[nbf.NotebookNode] = [nbf.v4.new_markdown_cell("\n\n".join(header))]

    for outcome in outcomes:
        cells.extend(_task_cells(outcome, style))

    nb.cells = cells
    nb.metadata = {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python"},
        "colab": {"provenance": []},
    }
    return nb


def write_notebook(
    path: Path,
    spec: LabSpec,
    outcomes: list[TaskOutcome],
    student: dict[str, str] | None = None,
    style: str = "classic",
    tagline: str = "",
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    nbf.write(build_notebook(spec, outcomes, student, style, tagline), str(path))
    return path
