"""Core data types. Everything downstream of ingest consumes these, never raw DOCX."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from labsagent.blocks import Block


@dataclass(frozen=True)
class Task:
    """One lab task. Format-free on purpose.

    This used to carry `anchor_idx`, a paragraph index into the uploaded .docx.
    That made a Word document a mandatory input to the core model: a task read
    out of a PDF, a notebook or a pasted message has no such coordinate, and
    the DOCX writer raised `IndexError` on the -1 default after every task had
    already been solved and paid for.

    Anchors now live in a side table -- `RunManifest.anchors`, produced by the
    .docx reader and consumed only by the DOCX emitter. A task no longer knows
    what a paragraph is.
    """

    id: str
    title: str
    statement: str
    sample_inputs: list[str] = field(default_factory=list)
    sample_output: str | None = None
    wants_explanation: bool = False
    #: Parts of the task answered in WORDS, not by the program -- "why does it
    #: fail", "compare the two". Kept apart from the code so the solver prints
    #: evidence and the writer turns it into prose. Printing the essay was the
    #: defect: it landed in a terminal screenshot instead of the report.
    written_questions: list[str] = field(default_factory=list)
    #: False for a pure theory question: nothing to run, only words to write.
    needs_code: bool = True
    #: Steering for the SOLVER only -- narration rules, the student's
    #: code-shaping notes. Kept apart from `statement` because `statement` is
    #: what the deliverables print: fold the two together and the .py, .md and
    #: .ipynb all carry "before your first tool call, say in ONE short
    #: sentence..." into the submitted file. Persisted with the task so a
    #: resumed run asks the same question it originally asked.
    instruction: str = ""
    #: "basic" | "standard", read by ingest. A basic task's first attempt runs
    #: without the model's thinking step and with a tighter turn cap; if it
    #: fails, the retry runs as standard. Persisted so a resume runs the same way.
    effort: str = "standard"


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
    """What one task produced.

    `blocks` is the format-free rendering of this answer and is optional on
    purpose: when it is None, `blocks.blocks_for()` synthesises the list from
    the named fields below in the order the DOCX writer has always used. That
    keeps every manifest ever written loadable, and lets the solver start
    emitting richer interleaved content task by task rather than all at once.
    """

    task: Task
    status: Literal["passed", "failed"]
    code_path: Path | None = None
    code_text: str = ""
    screenshot_paths: list[Path] = field(default_factory=list)
    figure_paths: list[Path] = field(default_factory=list)
    transcript: Transcript | None = None
    explanation: str | None = None
    #: The written answers, as {"question", "answer"} dicts, in task order.
    answers: list[dict] = field(default_factory=list)
    #: Real per-part output, one {"title", "code", "output", "figures"} per
    #: `# %%` section, from an instrumented run after the task passed. Empty
    #: when the program has no sections; every emitter then uses the whole run.
    sections: list[dict] = field(default_factory=list)
    attempts: int = 0
    error: str | None = None
    blocks: list[Block] | None = None
    #: What THIS task cost, and the usage behind it. Only run-level totals were
    #: ever persisted, so "which task was expensive" could not be answered from
    #: a finished run -- which is most of what you want to know about one.
    cost_usd: float = 0.0
    usage: dict = field(default_factory=dict)
    #: Every attempt's error, in order, kept even when a later attempt passed.
    #: `error` is None once a task succeeds, so the reason it needed three
    #: attempts used to be unrecoverable.
    attempt_errors: list[str] = field(default_factory=list)
    #: "turn_limit" | "task" | "run" when a ceiling ended this task.
    stopped_reason: str | None = None
    #: Data files this task produced, for the next task that references it.
    produced: list[dict] = field(default_factory=list)
    #: WHY THE TASK STOPPED, for the student, in one plain sentence -- set when
    #: the environment lacks something the task needs (a library, a readable
    #: format, the data). `status` stays "failed" so every consumer that knows
    #: two statuses keeps working; this field is what says "not tried, and why"
    #: instead of "did not complete after N attempts".
    blocker: str | None = None
    #: A task that PASSED except for one part it could not do, said plainly.
    #: The honest version of what used to be a stand-in that claimed a pass.
    gap: str | None = None
    #: run_solution calls across every attempt -- the write/run/fix cycles a
    #: person watching the run sees, which were recorded nowhere before.
    runs: int = 0


@dataclass
class RunManifest:
    run_id: str
    started_at: datetime
    spec: LabSpec
    outcomes: list[TaskOutcome] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)
    #: The full `RunUsage.as_dict()` -- total AND per-phase. `token_usage` is
    #: the collapsed total and is kept so older manifests still load, but it
    #: threw away the solve/explain/ingest split that says where money went.
    usage: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    #: task id -> paragraph index in the source .docx. Empty for every other
    #: input type. Persisted so a revision can still rebuild the Word report.
    anchors: dict[str, int] = field(default_factory=dict)
    #: Data files this run was given, as `Dataset.as_dict()`. Persisted so a
    #: revision reuses the CSV that is already on disk instead of downloading
    #: it a second time -- and so the record says what the code was run against,
    #: which is the difference between a reproducible result and a number.
    datasets: list[dict] = field(default_factory=list)
    #: One entry per follow-up: what kind it was, what it touched, what it
    #: cost. `usage`/`cost_usd` above are the RUNNING TOTAL -- a revision used
    #: to overwrite them, so the first pass's cost was lost.
    followups: list[dict] = field(default_factory=list)
