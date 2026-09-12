"""Core data types. Everything downstream of ingest consumes these, never raw DOCX."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class Task:
    """One lab task, located inside the manual document.

    `anchor_idx` is the index (in the flattened paragraph list produced by
    ingest.docx_reader) of the LAST paragraph belonging to this task. Inserted
    content goes immediately after it.
    """

    id: str
    title: str
    statement: str
    sample_inputs: list[str] = field(default_factory=list)
    sample_output: str | None = None
    anchor_idx: int = -1
    wants_explanation: bool = False


@dataclass(frozen=True)
class LabSpec:
    lab_number: str
    title: str
    course: str | None = None
    tasks: list[Task] = field(default_factory=list)
    skipped_images: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool

    @property
    def ok(self) -> bool:
        """Run-to-green: exited cleanly, did not time out, produced output."""
        return self.exit_code == 0 and not self.timed_out and bool(self.stdout.strip())


@dataclass(frozen=True)
class Transcript:
    """A terminal session as it should be displayed.

    `lines` are already correctly interleaved -- prompts and their typed values
    sit together because the echo happened at the moment of consumption inside
    the shim, not by reconstruction afterwards.
    """

    command: str
    lines: list[str]
    prompt: str = r"PS C:\lab>"

    @classmethod
    def from_exec(cls, command: str, result: ExecResult, prompt: str = r"PS C:\lab>") -> "Transcript":
        body = result.stdout.splitlines()
        if result.stderr.strip():
            body += result.stderr.rstrip().splitlines()
        return cls(command=command, lines=body, prompt=prompt)

    def display_lines(self) -> list[str]:
        """Full rendering including the command line and trailing cursor."""
        return [f"{self.prompt} {self.command}", *self.lines, f"{self.prompt} "]


@dataclass
class TaskOutcome:
    task: Task
    status: Literal["passed", "failed"]
    code_path: Path | None = None
    code_text: str = ""
    screenshot_paths: list[Path] = field(default_factory=list)
    figure_paths: list[Path] = field(default_factory=list)
    transcript: Transcript | None = None
    explanation: str | None = None
    attempts: int = 0
    error: str | None = None


@dataclass
class RunManifest:
    run_id: str
    started_at: datetime
    spec: LabSpec
    outcomes: list[TaskOutcome] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0
