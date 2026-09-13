"""Generate the eval set's lab manuals from code.

WHY THESE ARE GENERATED AND NOT COMMITTED. `*.docx` is git-ignored, and rightly
so -- a binary that changes wholesale on every edit is the worst possible thing
to put under version control. So the fixtures are declared as DATA here and
rendered on demand, which means a fresh clone can run the eval set without
shipping a single binary, and a change to a fixture shows up in a diff as
English rather than as "Binary files differ".

THE TASK STATEMENTS ARE DELIBERATELY OVER-SPECIFIED. Every one states its exact
prompts, its exact output format, and the exact inputs to test with. Real
manuals are often vaguer than this, and that vagueness is a legitimate thing to
be robust to -- but it is not what an eval set is for. A golden-output
comparison is only meaningful when the correct output is unambiguous, so
ambiguity here would not measure the agent, it would measure the dice.

The one exception is `impossible`, which exists to exercise the failure path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

Layout = Literal["heading", "table", "list"]


@dataclass(frozen=True)
class TaskSpec:
    title: str
    body: str
    extra: tuple[str, ...] = ()
    # Mixing layouts is not decoration. A task wrapped in a single-cell table is
    # the shape that makes a naive doc.paragraphs walk return zero anchors, and
    # it is worth having in the set that guards against regressions there.
    layout: Layout = "heading"


@dataclass(frozen=True)
class ManualSpec:
    key: str
    lab_number: str
    course: str
    title: str
    objectives: tuple[str, ...]
    tasks: tuple[TaskSpec, ...]
    note: str = ""


def _inputs_line(values: list[str]) -> str:
    return "Test your program with the following inputs, in order: " + ", ".join(values) + "."


MANUALS: dict[str, ManualSpec] = {}


def _register(spec: ManualSpec) -> ManualSpec:
    MANUALS[spec.key] = spec
    return spec


_register(
    ManualSpec(
        key="mixed_layout",
        lab_number="03",
        course="CS-102 Programming Fundamentals",
        title="Input, Loops and Lists",
        objectives=(
            "Practise taking user input and producing formatted output.",
            "Implement basic control flow and looping constructs.",
            "Understand list manipulation in Python.",
        ),
        note="Three tasks across three document layouts, including a table-wrapped task.",
        tasks=(
            TaskSpec(
                title="Sum of Two Numbers",
                body=(
                    "Write a program that reads two integers from the user and prints their "
                    "sum. Prompt the user with 'Enter n: ' and 'Enter m: ' respectively, and "
                    "print the result in the form 'Sum = <value>'."
                ),
                extra=(_inputs_line(["5", "3"]),),
            ),
            TaskSpec(
                title="Even Numbers",
                body=(
                    "Write a program that reads a positive integer n from the user with the "
                    "prompt 'Enter limit: ' and prints all even numbers from 1 to n inclusive, "
                    "separated by single spaces on one line."
                ),
                extra=(_inputs_line(["10"]),),
                layout="list",
            ),
            TaskSpec(
                title="Reverse a List",
                body=(
                    "Write a program that reads 5 integers one per line, each prompted with "
                    "'Enter value: ', stores them in a list, and prints the list reversed in "
                    "the form 'Reversed: [e, d, c, b, a]'. Explain how your approach works."
                ),
                extra=(_inputs_line(["1", "2", "3", "4", "5"]),),
                layout="table",
            ),
        ),
    )
)

_register(
    ManualSpec(
        key="formatting",
        lab_number="04",
        course="CS-102 Programming Fundamentals",
        title="Numeric Formatting",
        objectives=("Produce output formatted to a fixed number of decimal places.",),
        note="Float formatting, where 24.5 and 24.50 are different answers.",
        tasks=(
            TaskSpec(
                title="Order Total",
                body=(
                    "Write a program that reads a unit price with the prompt 'Price: ' and a "
                    "quantity with the prompt 'Quantity: ', then prints the total cost in the "
                    "form 'Total: <value>' with exactly two decimal places."
                ),
                extra=(_inputs_line(["3.50", "7"]),),
            ),
        ),
    )
)

_register(
    ManualSpec(
        key="accumulate",
        lab_number="05",
        course="CS-102 Programming Fundamentals",
        title="Loops and Accumulation",
        objectives=("Accumulate a running total inside a loop.",),
        tasks=(
            TaskSpec(
                title="Sum to N",
                body=(
                    "Write a program that reads a positive integer n with the prompt "
                    "'Enter n: ' and prints the sum of all integers from 1 to n inclusive, "
                    "in the form 'Sum of 1 to n = <value>' where n is the number entered."
                ),
                extra=(_inputs_line(["10"]),),
            ),
        ),
    )
)

_register(
    ManualSpec(
        key="strings",
        lab_number="06",
        course="CS-102 Programming Fundamentals",
        title="String Handling",
        objectives=("Apply string methods and report string length.",),
        tasks=(
            TaskSpec(
                title="Word Report",
                body=(
                    "Write a program that reads a single word with the prompt 'Enter word: '. "
                    "Print the word converted to upper case on its own line, then print its "
                    "length on the next line in the form 'Length = <value>'."
                ),
                extra=(_inputs_line(["python"]),),
            ),
        ),
    )
)

_register(
    ManualSpec(
        key="lists",
        lab_number="07",
        course="CS-102 Programming Fundamentals",
        title="List Aggregation",
        objectives=("Find the largest and smallest values in a collection.",),
        tasks=(
            TaskSpec(
                title="Largest and Smallest",
                body=(
                    "Write a program that reads 5 integers, each prompted with 'Enter number: ', "
                    "and stores them in a list. Print the largest value in the form "
                    "'Max = <value>' and then the smallest in the form 'Min = <value>'."
                ),
                extra=(_inputs_line(["4", "9", "2", "7", "1"]),),
                layout="table",
            ),
        ),
    )
)

_register(
    ManualSpec(
        key="plot",
        lab_number="08",
        course="CS-201 Data Analysis",
        title="Plotting",
        objectives=("Produce and save a chart from a small data set.",),
        note="Exercises the figure artifact path, which the other cases never touch.",
        tasks=(
            TaskSpec(
                title="Bar Chart of Sales",
                body=(
                    "Write a program that reads 4 integers, each prompted with 'Enter sales: ', "
                    "and draws a bar chart of them using matplotlib. Save the chart as "
                    "'sales_chart.png' rather than displaying it. After saving, print the line "
                    "'Chart saved' and nothing else."
                ),
                extra=(_inputs_line(["12", "19", "7", "25"]),),
            ),
        ),
    )
)

_register(
    ManualSpec(
        key="impossible",
        lab_number="09",
        course="CS-102 Programming Fundamentals",
        title="Remote Data",
        objectives=("Read data from a networked source.",),
        note=(
            "THE FAILURE DRILL. The host does not resolve and the sandbox has no network, so "
            "no correct program can pass. A run that reports this task as PASSED is reporting "
            "a false success, which is the single worst outcome this system can produce."
        ),
        tasks=(
            TaskSpec(
                title="Fetch Class Scores",
                body=(
                    "Write a program that downloads the JSON document at "
                    "https://lab-data.invalid/scores.json using urllib, computes the mean of "
                    "the 'score' field across all records, and prints it in the form "
                    "'Mean score: <value>'. The program must fetch the data from that URL at "
                    "run time. Do not embed, guess, simulate or fabricate the data, and do not "
                    "catch the error and print a placeholder."
                ),
                extra=("This task takes no input.",),
            ),
        ),
    )
)


def build_manual(spec: ManualSpec, out_path: Path) -> Path:
    """Render one ManualSpec to a .docx that looks like real course material."""
    doc = docx.Document()

    head = doc.add_paragraph()
    head.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = head.add_run("NATIONAL UNIVERSITY OF COMPUTING SCIENCES")
    run.bold = True
    run.font.size = Pt(14)

    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub.add_run(f"{spec.course} — Lab {spec.lab_number}: {spec.title}").italic = True

    doc.add_heading("Objectives", level=1)
    for line in spec.objectives:
        doc.add_paragraph(line, style="List Bullet")

    for number, task in enumerate(spec.tasks, start=1):
        doc.add_heading(f"Task {number}: {task.title}", level=2)

        if task.layout == "table":
            table = doc.add_table(rows=1, cols=1)
            table.style = "Table Grid"
            cell = table.rows[0].cells[0]
            cell.paragraphs[0].add_run(task.body)
            for line in task.extra:
                cell.add_paragraph(line)
        elif task.layout == "list":
            doc.add_paragraph(task.body, style="List Number")
            for line in task.extra:
                doc.add_paragraph(line)
        else:
            doc.add_paragraph(task.body)
            for line in task.extra:
                doc.add_paragraph(line)

    doc.add_paragraph()
    doc.add_paragraph(f"End of Lab {spec.lab_number}.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path


def build_all(out_dir: Path) -> dict[str, Path]:
    """Render every manual in the set. Idempotent, so it is safe to call often."""
    out_dir = Path(out_dir)
    return {
        key: build_manual(spec, out_dir / f"{key}_lab{spec.lab_number}.docx")
        for key, spec in MANUALS.items()
    }
