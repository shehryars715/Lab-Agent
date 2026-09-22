"""The domain tool surface.

deepagents already supplies file tools (ls/read_file/write_file/edit_file), a
planning todo tool, and subagent delegation. We add only what it cannot know
about: how to execute a lab solution, and how to declare a task finished.

Tools are built by a FACTORY rather than defined at module level, because each
tool needs a live sandbox and a place to record results. A closure is the
simplest dependency injection there is -- no globals, no context vars, and the
sandbox cannot leak between runs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.tools import BaseTool, tool

from labsagent.runner import run_solution as _run_solution
from labsagent.sandbox.base import DEFAULT_TIMEOUT_S, Sandbox


@dataclass(frozen=True)
class RunRecord:
    """One `run_solution` call, kept whole.

    WHY A LIST AND NOT A `last`. `figures` learned this lesson first (see its
    note below): "the most recent call" is the right answer for a single call
    and the wrong answer for a task. An agent that runs its solution and then
    runs a chore -- a cleanup, a probe, a listing -- leaves the chore as the
    most recent everything, and the chore exited 0, so it looked like success.
    The report then shipped the chore as the solution. Keeping every run lets
    the caller pick the one that was actually the task.
    """

    entry_file: str
    ok: bool
    transcript: Any
    stdout: str
    stderr: str
    figures: list[str] = field(default_factory=list)
    #: sha256 of `entry_file` as it was WHEN THIS RAN. An agent that edits the
    #: file after running it leaves a transcript that no longer describes the
    #: code we are about to ship; comparing this against the file on disk is
    #: how that is caught. None when the file could not be read.
    source_sha: str | None = None


@dataclass
class TaskRecorder:
    """Collects what the agent reports, plus what actually happened.

    The agent's own claim of success is never trusted on its own -- `runs` and
    `attempts` are recorded by us, from real execution.
    """

    #: What the agent DECLARED in record_task_result. A claim, not a fact: an
    #: agent that wrote to "workspace/task3.py" will still cheerfully report
    #: "task3.py". Used only to break ties between real runs.
    entry_file: str | None = None
    status: str | None = None
    notes: str = ""
    attempts: int = 0
    warnings: list[str] = field(default_factory=list)
    #: Every run_solution call this ATTEMPT made, in order.
    runs: list[RunRecord] = field(default_factory=list)

    @property
    def last(self) -> RunRecord | None:
        return self.runs[-1] if self.runs else None

    # The `last_*` views below are what callers outside the harvest path want:
    # "what happened most recently". The harvest path deliberately does NOT use
    # them -- see `orchestrator.choose_run`.
    @property
    def last_entry_file(self) -> str | None:
        return self.last.entry_file if self.last else None

    @property
    def last_ok(self) -> bool:
        return bool(self.last and self.last.ok)

    @property
    def last_stdout(self) -> str:
        return self.last.stdout if self.last else ""

    @property
    def last_stderr(self) -> str:
        return self.last.stderr if self.last else ""

    @property
    def last_transcript(self) -> Any:
        return self.last.transcript if self.last else None

    @property
    def figures(self) -> list[str]:
        """Every figure this ATTEMPT produced -- not just the last call's.

        WHY IT CANNOT BE "last". `run_solution` reports figures as the set of
        image files that are NEW since that call started, which is the right
        per-call answer and the wrong per-task one. An agent that runs its
        solution, then a diagnostic probe, then its solution again ends with an
        empty delta twice over: the probe draws nothing, and the re-run
        OVERWRITES the .png rather than creating it. Assigning the last value
        therefore threw away a chart that was sitting in the workspace, and the
        report shipped without the figure the task asked for.

        Accumulating is scoped correctly by construction: `build_solver` makes a
        fresh recorder per attempt, so a discarded attempt cannot contribute.
        """
        seen: list[str] = []
        for record in self.runs:
            for name in record.figures:
                if name not in seen:
                    seen.append(name)
        return seen

    def figures_for(self, entry_file: str) -> list[str]:
        """Figures drawn by runs of one file, by the same accumulate rule.

        Scoping matters once a chore can no longer be mistaken for the
        solution: a probe that happens to draw something is not this task's
        figure. Falls back to everything when the scoped set is empty, because
        a lost chart is a worse failure than an extra one.
        """
        from labsagent.orchestrator import normalize_entry

        target = normalize_entry(entry_file)
        scoped: list[str] = []
        for record in self.runs:
            if normalize_entry(record.entry_file) != target:
                continue
            for name in record.figures:
                if name not in scoped:
                    scoped.append(name)
        return scoped or self.figures

    @property
    def finished(self) -> bool:
        return self.status is not None


def _sha_of(sandbox: Sandbox, entry_file: str) -> str | None:
    """sha256 of a file in the sandbox, or None if it cannot be read.

    Never raises: this is provenance for a later staleness check, and a run
    must not fail because the hash could not be taken.
    """
    try:
        return hashlib.sha256(sandbox.read_file(entry_file).encode("utf-8")).hexdigest()
    except Exception:  # noqa: BLE001 -- any read failure just means "unknown"
        return None


def build_tools(
    sandbox: Sandbox,
    recorder: TaskRecorder,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> list[BaseTool]:
    """Return the domain tools bound to this run's sandbox and recorder."""

    @tool
    def run_solution(entry_file: str, stdin_values: list[str] | None = None) -> str:
        """Execute a Python file and return what a terminal would have shown.

        Use this after writing your solution to check that it works. Pass the
        inputs the program should receive on stdin, in order, as a list of
        strings -- for example ["5", "3"] for a program that asks for two
        numbers. Pass an empty list if the program takes no input.

        Returns the exit code and the program's output, with input values shown
        inline beside their prompts exactly as a real terminal would display
        them. A non-zero exit code means the program crashed; the traceback is
        included so you can fix it.
        """
        outcome = _run_solution(sandbox, entry_file, stdin_values or [], timeout_s)

        recorder.attempts += 1
        recorder.warnings = outcome.warnings
        recorder.runs.append(
            RunRecord(
                entry_file=entry_file,
                ok=outcome.ok,
                transcript=outcome.transcript,
                stdout=outcome.result.stdout,
                stderr=outcome.result.stderr,
                figures=list(outcome.figures),
                source_sha=_sha_of(sandbox, entry_file),
            )
        )

        parts = [f"exit_code: {outcome.result.exit_code}"]
        if outcome.result.timed_out:
            parts.append(f"TIMED OUT after {timeout_s}s")
        parts.append(f"stdout:\n{outcome.result.stdout or '(empty)'}")
        if outcome.result.stderr.strip():
            parts.append(f"stderr:\n{outcome.result.stderr}")
        for warning in outcome.warnings:
            parts.append(f"warning: {warning}")
        return "\n\n".join(parts)

    @tool
    def record_task_result(
        entry_file: str,
        status: Literal["passed", "failed"],
        notes: str = "",
    ) -> str:
        """Declare this task finished and stop working on it.

        Call this exactly once, as your final action. Use status "passed" when
        run_solution exited 0 and produced the output the task asked for. Use
        "failed" when you cannot get it working -- a recorded failure is far
        more useful than a false success, because the report shows what went
        wrong instead of claiming a result that is not there.
        """
        recorder.entry_file = entry_file
        recorder.status = status
        recorder.notes = notes
        return f"Recorded {entry_file} as {status}. Stop now and summarise briefly."

    return [run_solution, record_task_result]
