"""Phase 0 walking skeleton -- no LLM, no agent, no network.

Everything is hardcoded: the solutions, the stdin values, and the anchor
indices. The point is to prove the artifact pipeline end to end before any model
is involved, because the artifact pipeline is where this project actually
bleeds.

Run:  uv run python -m labsagent.demo
"""

from __future__ import annotations

from pathlib import Path

from labsagent.capture.rendered import RenderedBackend
from labsagent.models import LabSpec, Task, TaskOutcome
from labsagent.package.zipper import build_submission
from labsagent.report.docx_builder import annotate_manual
from labsagent.runner import run_solution
from labsagent.sandbox.local import LocalSandbox

MANUAL = Path("tests/fixtures/lab03_manual.docx")
BUILD = Path("build/demo")
ROLL_NO = "22F-1234"

# Hardcoded for Phase 0. Phase 2 derives all of this from the manual.
# Anchors are a side table now -- a paragraph index belongs to the document,
# not to the task.
ANCHORS = {"task1": 8, "task2": 10, "task3": 13}

HARDCODED = [
    (
        Task(
            id="task1",
            title="Sum of Two Numbers",
            statement="Read two integers and print their sum.",
            sample_inputs=["5", "3"],
        ),
        'n = int(input("Enter n: "))\n'
        'm = int(input("Enter m: "))\n'
        'print(f"Sum = {n + m}")\n',
        "The program reads two integers with input(), converts each with int(), and "
        "prints their sum using an f-string.",
    ),
    (
        Task(
            id="task2",
            title="Even Numbers",
            statement="Print even numbers from 1 to n.",
            sample_inputs=["10"],
        ),
        'limit = int(input("Enter limit: "))\n'
        "evens = [str(i) for i in range(2, limit + 1, 2)]\n"
        'print(" ".join(evens))\n',
        "A list comprehension steps through the range in twos, collecting even values, "
        "which are then joined into a single space-separated line.",
    ),
    (
        Task(
            id="task3",
            title="Reverse a List",
            statement="Read 5 integers and print the list reversed.",
            sample_inputs=["1", "2", "3", "4", "5"],
            wants_explanation=True,
        ),
        "values = []\n"
        "for _ in range(5):\n"
        '    values.append(int(input("Enter value: ")))\n'
        'print(f"Reversed: {values[::-1]}")\n',
        "Each value is appended to a list inside a fixed five-iteration loop. The slice "
        "[::-1] walks the list with a step of -1, producing a reversed copy without "
        "mutating the original.",
    ),
]


def _ensure_manual() -> None:
    """*.docx is git-ignored; the fixture is reproducible, so generate it."""
    if MANUAL.exists():
        return
    import sys

    sys.path.insert(0, str(Path('tests/fixtures').resolve()))
    from make_manual import build

    print(f'generating sample manual -> {MANUAL}')
    build(MANUAL)


def main() -> int:
    _ensure_manual()
    spec = LabSpec(
        lab_number="03",
        title="Programming Fundamentals Lab 03",
        course="CS-102",
        tasks=[t for t, _, _ in HARDCODED],
    )

    shots_dir = BUILD / "screenshots"
    code_dir = BUILD / "code"
    code_dir.mkdir(parents=True, exist_ok=True)

    backend = RenderedBackend(theme="light")
    print(f"font: {backend.font_provenance}")

    outcomes: list[TaskOutcome] = []

    with LocalSandbox() as sandbox:
        for task, source, explanation in HARDCODED:
            entry = f"{task.id}.py"
            sandbox.write_file(entry, source)

            run = run_solution(sandbox, entry, task.sample_inputs)
            for warning in run.warnings:
                print(f"  warning [{task.id}]: {warning}")

            code_path = code_dir / entry
            code_path.write_text(source, encoding="utf-8")

            shots = (
                backend.render(run.transcript, shots_dir / f"{task.id}_output.png")
                if run.ok
                else []
            )

            outcomes.append(
                TaskOutcome(
                    task=task,
                    status="passed" if run.ok else "failed",
                    code_path=code_path,
                    code_text=source,
                    screenshot_paths=shots,
                    transcript=run.transcript,
                    explanation=explanation,
                    attempts=1,
                    error=None if run.ok else run.result.stderr.strip()[:300],
                )
            )
            print(f"  {task.id}: {outcomes[-1].status} ({len(shots)} screenshot(s))")

    report = annotate_manual(
        MANUAL, BUILD / f"Lab{spec.lab_number}_Report.docx", outcomes, anchors=ANCHORS
    )
    archive = build_submission(
        BUILD / f"Lab{spec.lab_number}_{ROLL_NO}.zip", report, outcomes
    )

    print(f"\nreport:  {report}")
    print(f"archive: {archive} ({archive.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
