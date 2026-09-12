"""Generate a fixture lab manual resembling a real one.

Deliberately mixes layouts: heading-styled tasks, a numbered-list task, and one
wrapped in a single-cell table -- the layout that makes a naive doc.paragraphs
walk return zero anchors.
"""

from __future__ import annotations

import sys
from pathlib import Path

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt


def build(out_path: Path) -> Path:
    doc = docx.Document()

    head = doc.add_paragraph()
    head.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = head.add_run("NATIONAL UNIVERSITY OF COMPUTING SCIENCES")
    run.bold = True
    run.font.size = Pt(14)

    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub.add_run("CS-102 Programming Fundamentals — Lab 03").italic = True

    doc.add_heading("Objectives", level=1)
    for line in (
        "Practise taking user input and producing formatted output.",
        "Implement basic control flow and looping constructs.",
        "Understand list manipulation in Python.",
    ):
        doc.add_paragraph(line, style="List Bullet")

    doc.add_heading("Task 1: Sum of Two Numbers", level=2)
    doc.add_paragraph(
        "Write a program that reads two integers from the user and prints their sum. "
        "Prompt the user with 'Enter n: ' and 'Enter m: ' respectively, and print the "
        "result in the form 'Sum = <value>'."
    )
    doc.add_paragraph("Sample run: n = 5, m = 3 gives Sum = 8.")

    doc.add_heading("Task 2: Even Numbers", level=2)
    doc.add_paragraph(
        "Write a program that reads a positive integer n from the user with the prompt "
        "'Enter limit: ' and prints all even numbers from 1 to n inclusive, separated by "
        "spaces on a single line."
    )

    doc.add_heading("Task 3: Reverse a List", level=2)
    table = doc.add_table(rows=1, cols=1)
    table.style = "Table Grid"
    cell = table.rows[0].cells[0]
    cell.paragraphs[0].add_run(
        "Write a program that reads 5 integers one per line, each prompted with "
        "'Enter value: ', stores them in a list, and prints the list reversed. "
        "Explain how your approach works."
    )
    cell.add_paragraph("Expected output format: Reversed: [e, d, c, b, a]")

    doc.add_paragraph()
    doc.add_paragraph("End of Lab 03.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/lab03_manual.docx")
    print("wrote", build(target))
