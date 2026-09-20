"""Export a solved lab as a Jupyter/Colab notebook (.ipynb).

Many courses want the notebook, not a Word report -- "Submit only the .ipynb
file on LMS". The notebook is the same solved tasks in a different container, so
it is an exporter over TaskOutcome, not a separate pipeline.

Outputs are embedded as real notebook outputs (stream text for stdout, base64
PNG for figures) rather than pasted in as markdown images. That means the
notebook opens in Colab already showing results, exactly as if it had been run.
"""

from __future__ import annotations

import base64
from pathlib import Path

import nbformat as nbf

from labsagent.models import LabSpec, TaskOutcome

COLAB_SETUP = (
    "# Setup -- Colab has these, a local kernel may need them\n"
    "# !pip install numpy matplotlib scipy scikit-learn pandas -q\n"
)


def _leading_prose(outcome):
    """Prose blocks the caller put BEFORE the first code block, if any."""
    blocks = getattr(outcome, "blocks", None)
    if not blocks:
        return []
    out = []
    for block in blocks:
        if block.kind != "prose":
            break
        out.append(block)
    return out


def _image_output(path: Path) -> nbf.NotebookNode:
    data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return nbf.v4.new_output(
        "display_data",
        data={"image/png": data, "text/plain": [f"<Figure: {Path(path).name}>"]},
        metadata={},
    )


def build_notebook(
    spec: LabSpec,
    outcomes: list[TaskOutcome],
    student: dict[str, str] | None = None,
) -> nbf.NotebookNode:
    nb = nbf.v4.new_notebook()
    cells: list[nbf.NotebookNode] = []

    header = [f"# {spec.title}"]
    if spec.course:
        header.append(f"**Course:** {spec.course}")
    for key, value in (student or {}).items():
        header.append(f"**{key.replace('_', ' ').title()}:** {value}")
    cells.append(nbf.v4.new_markdown_cell("\n\n".join(header)))
    cells.append(nbf.v4.new_code_cell(COLAB_SETUP))

    for outcome in outcomes:
        task = outcome.task
        heading = [f"## {task.title}", "", task.statement]
        cells.append(nbf.v4.new_markdown_cell("\n".join(heading)))

        # PROSE THAT COMES BEFORE THE CODE, as its own markdown cell.
        #
        # This module reads TaskOutcome's named fields rather than the block
        # list, which is why it is the one emitter a `blocks`-only addition
        # does not reach for free. The provenance line -- "Data used: iris.csv
        # (from Kaggle dataset uciml/iris)" -- is exactly that kind of
        # addition, and a notebook that calls read_csv without saying where the
        # file came from is the least reproducible of the four formats.
        #
        # Deliberately narrow: only LEADING prose, and only when the caller set
        # `blocks` explicitly. `blocks_for()` synthesises a list whose prose
        # (the explanation) comes last, so calling it here would duplicate the
        # explanation cell at the bottom of every task.
        for block in _leading_prose(outcome):
            cells.append(nbf.v4.new_markdown_cell(block.text))

        if outcome.status != "passed":
            cells.append(
                nbf.v4.new_markdown_cell(
                    f"> **Not completed.** {outcome.error or 'unknown error'}"
                )
            )
            if outcome.code_text:
                cells.append(nbf.v4.new_code_cell(outcome.code_text.rstrip()))
            continue

        outputs: list[nbf.NotebookNode] = []
        stdout = outcome.transcript and "\n".join(outcome.transcript.lines).strip()
        if stdout:
            outputs.append(
                nbf.v4.new_output("stream", name="stdout", text=stdout + "\n")
            )
        for figure in outcome.figure_paths:
            if Path(figure).exists():
                outputs.append(_image_output(Path(figure)))

        cell = nbf.v4.new_code_cell(outcome.code_text.rstrip())
        cell.outputs = outputs
        cell.execution_count = None
        cells.append(cell)

        if outcome.explanation:
            cells.append(nbf.v4.new_markdown_cell(f"**Explanation.** {outcome.explanation}"))

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
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    nbf.write(build_notebook(spec, outcomes, student), str(path))
    return path
