"""Run a solution file in a sandbox and get back a faithful Transcript.

This is what the Phase 1 `run_solution` agent tool will wrap.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from labsagent.capture.shim import (
    EXIT_INPUT_EXHAUSTED,
    SENTINEL_EXHAUSTED,
    SENTINEL_RAW_STDIN,
)
from labsagent.models import ExecResult, Transcript
from labsagent.sandbox.base import DEFAULT_TIMEOUT_S, Sandbox

SHIM_SRC = Path(__file__).parent / "capture" / "shim.py"
SHIM_NAME = "_labsagent_shim.py"
INPUTS_NAME = "_labsagent_inputs.txt"


IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".svg")


@dataclass
class RunOutcome:
    result: ExecResult
    transcript: Transcript
    warnings: list[str]
    figures: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.figures is None:
            self.figures = []

    @property
    def ok(self) -> bool:
        # A program that draws a chart but prints nothing still succeeded.
        return (self.result.exit_code == 0 and not self.result.timed_out
                and (bool(self.result.stdout.strip()) or bool(self.figures)))


def _clean_stderr(stderr: str) -> str:
    """Strip our own sentinel lines so they never reach a screenshot."""
    keep = [
        ln
        for ln in stderr.splitlines()
        if not ln.startswith(SENTINEL_EXHAUSTED) and ln.strip() != SENTINEL_RAW_STDIN
    ]
    return "\n".join(keep)


def run_solution(
    sandbox: Sandbox,
    entry_file: str,
    stdin_values: list[str] | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> RunOutcome:
    """Execute `entry_file` under the echo shim.

    Returns a Transcript whose lines read exactly as a real terminal session
    would, because input() echoes at the moment of consumption rather than being
    reconstructed afterwards.
    """
    values = list(stdin_values or [])

    sandbox.write_file(SHIM_NAME, SHIM_SRC.read_text(encoding="utf-8"))
    sandbox.write_file(INPUTS_NAME, "\n".join(values) + ("\n" if values else ""))

    before = set(sandbox.list_files())

    raw = sandbox.run(
        ["python", SHIM_NAME, entry_file, INPUTS_NAME],
        timeout=timeout_s,
    )

    # Plots are a second output channel: many tasks produce figures rather than
    # printed text, and a run that draws a chart but prints nothing is still a
    # successful run.
    figures = sorted(
        f
        for f in set(sandbox.list_files()) - before
        if f.lower().endswith(IMAGE_SUFFIXES)
    )

    warnings: list[str] = []
    if SENTINEL_RAW_STDIN in raw.stderr:
        warnings.append(
            f"{entry_file} reads sys.stdin directly instead of input(); those values "
            "will not appear in the transcript. A PTY backend is the real fix."
        )
    if raw.exit_code == EXIT_INPUT_EXHAUSTED or SENTINEL_EXHAUSTED in raw.stderr:
        warnings.append(
            f"{entry_file} asked for more input than the {len(values)} value(s) supplied."
        )

    result = ExecResult(
        exit_code=raw.exit_code,
        stdout=raw.stdout,
        stderr=_clean_stderr(raw.stderr),
        duration_s=raw.duration_s,
        timed_out=raw.timed_out,
    )

    return RunOutcome(
        result=result,
        transcript=Transcript.from_exec(f"python {entry_file}", result),
        warnings=warnings,
        figures=figures,
    )
