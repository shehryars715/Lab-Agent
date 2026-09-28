"""What each eval case is, and what counts as getting it right.

DECLARED GOLDENS, NOT RECORDED ONES. The usual golden-file workflow runs the
system once and saves whatever came out. That produces a REGRESSION test: it
tells you when behaviour changed, and it is perfectly happy to enshrine a wrong
answer as the standard, because it never knew what the right answer was.

The goldens below are written by hand from the task statements instead. That
makes them a CORRECTNESS test -- they say what the program SHOULD print, so a
run that prints something else is wrong rather than merely different. It is the
difference between "the agent still does what it did last week" and "the agent
is still right", and only the second one is worth a pass rate.

This is affordable here precisely because `manuals.py` over-specifies every
task. You cannot hand-write a golden for a task whose correct output is
ambiguous, which is a good reason to notice when a task IS ambiguous.

Recorded goldens still have a place: `--set real` points at actual course
manuals, whose correct output nobody has written down. Those cases score `ran`
and leave `matched` unscored rather than pretending.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from labsagent.evals.manuals import MANUALS, build_all


@dataclass(frozen=True)
class Expectation:
    """What one task in one case should do."""

    stdout: str | None = None
    expects_figure: bool = False
    # A case that is SUPPOSED to fail. Passing it is the failure, and the
    # scoring inverts accordingly -- see harness.score_task. A refusal at the
    # capability gate also counts: it is failing honestly, and earlier.
    should_fail: bool = False
    # The capability gate must refuse this task before any code is written.
    out_of_scope: bool = False
    # Ingest must name an input from outside the lab that this task relies on,
    # so the run stops to ask instead of inventing it.
    prerequisite: bool = False


@dataclass(frozen=True)
class EvalCase:
    name: str
    manual_path: Path
    lab_number: str
    # Keyed by position (1-based), not by task id: the id is assigned by ingest,
    # and an eval that trusted ingest's ids could not detect ingest losing a task.
    expected: dict[int, Expectation]
    note: str = ""

    @property
    def task_count(self) -> int:
        return len(self.expected)


# Written from the task statements in manuals.py, matching the shim's echo
# format exactly: `print(f"{prompt}{value}")`, so a prompt and its typed value
# share one line.
GOLDENS: dict[str, dict[int, Expectation]] = {
    "mixed_layout": {
        1: Expectation(stdout="Enter n: 5\nEnter m: 3\nSum = 8"),
        2: Expectation(stdout="Enter limit: 10\n2 4 6 8 10"),
        3: Expectation(
            stdout=(
                "Enter value: 1\nEnter value: 2\nEnter value: 3\nEnter value: 4\n"
                "Enter value: 5\nReversed: [5, 4, 3, 2, 1]"
            )
        ),
    },
    # 3.50 * 7 = 24.50, and "24.5" is a WRONG answer to a task that says two
    # decimal places. A run-to-green check cannot see the difference.
    "formatting": {
        1: Expectation(stdout="Price: 3.50\nQuantity: 7\nTotal: 24.50"),
    },
    "accumulate": {
        1: Expectation(stdout="Enter n: 10\nSum of 1 to 10 = 55"),
    },
    "strings": {
        1: Expectation(stdout="Enter word: python\nPYTHON\nLength = 6"),
    },
    "lists": {
        1: Expectation(
            stdout=(
                "Enter number: 4\nEnter number: 9\nEnter number: 2\nEnter number: 7\n"
                "Enter number: 1\nMax = 9\nMin = 1"
            )
        ),
    },
    "plot": {
        1: Expectation(
            stdout=(
                "Enter sales: 12\nEnter sales: 19\nEnter sales: 7\nEnter sales: 25\n"
                "Chart saved"
            ),
            expects_figure=True,
        ),
    },
    "impossible": {
        1: Expectation(should_fail=True),
    },
    "web_page": {
        1: Expectation(out_of_scope=True),
    },
    # Stopped to ask, so nothing is solved: task 1 is scored on NOT being
    # flagged, which is the false-positive half of the check.
    "builds_on_lab": {
        1: Expectation(),
        2: Expectation(prerequisite=True),
    },
    "missing_file": {
        1: Expectation(should_fail=True),
    },
}


def build_cases(out_dir: Path, only: list[str] | None = None) -> list[EvalCase]:
    """Render the manuals and pair each with its expectations."""
    paths = build_all(out_dir)
    cases = []
    for key, spec in MANUALS.items():
        if only and key not in only:
            continue
        cases.append(
            EvalCase(
                name=key,
                manual_path=paths[key],
                lab_number=spec.lab_number,
                expected=GOLDENS[key],
                note=spec.note,
            )
        )
    return cases


def real_cases(manual_dir: Path) -> list[EvalCase]:
    """Cases from a local directory of actual course manuals.

    These have no declared goldens, because nobody has written down what a real
    lab's correct output is. They score `ran` and report `matched` as unscored,
    which is the honest answer -- a fabricated golden would be worse than none.
    """
    manual_dir = Path(manual_dir)
    cases = []
    for path in sorted(manual_dir.glob("*.docx")):
        if path.name.startswith("~$"):  # Word's lock files
            continue
        cases.append(
            EvalCase(
                name=path.stem,
                manual_path=path,
                lab_number="??",
                expected={},
                note="real manual; no declared goldens",
            )
        )
    return cases
