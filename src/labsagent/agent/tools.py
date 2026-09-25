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
import importlib.util
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from langchain_core.tools import BaseTool, tool

from labsagent.prose import PROSE_WARN_LINES, prose_lines
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
    #: The agent's one plain sentence for the student when something the task
    #: needs is not available -- with "blocked", or with "passed" when one
    #: part could not be done.
    missing: str = ""
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


#: "No module named 'seaborn'" -- the moment the solver learns a library is
#: absent, which is the moment it used to start writing a stand-in for it.
_MISSING_MODULE = re.compile(r"ModuleNotFoundError: No module named '([\w.]+)'")


def missing_module(stderr: str, workdir) -> str | None:
    """The top-level package a run could not import, if it is truly absent.

    A module the agent wrote itself (a helper file in the workspace) is not
    missing from the environment; neither is one that imports fine here.
    """
    match = _MISSING_MODULE.search(stderr or "")
    if not match:
        return None
    top = match.group(1).split(".")[0]
    if (Path(workdir) / f"{top}.py").exists() or (Path(workdir) / top).is_dir():
        return None
    try:
        if importlib.util.find_spec(top) is not None:
            return None
    except (ImportError, ValueError):
        pass
    return top


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
        # A NUDGE AT THE MOMENT OF ACTION. The prompt already says "print
        # results, not essays"; a prompt asks and mostly obliges. The tool
        # result is the one place the agent is guaranteed to look next, so the
        # reminder costs a sentence and never fails the task.
        # THE MOMENT IT WOULD START IMPROVISING. A missing library used to be
        # met with a hand-written stand-in -- a fake seaborn.histplot that then
        # "passed" -- or a probe script trying pip. The prompt says not to; this
        # says it again at the one place the agent is guaranteed to look next.
        absent = missing_module(outcome.result.stderr, sandbox.workdir)
        if absent:
            parts.append(
                f"warning: {absent} is not installed here and cannot be installed (no pip, "
                "no internet). Do not write your own version of it or a stand-in. If the "
                "task can be done properly with the libraries that are available, do "
                f"that. If the task needs {absent}, stop: call record_task_result with "
                'status "blocked" and say in `missing` what could not be done.'
            )
        prose = prose_lines(outcome.result.stdout)
        if len(prose) >= PROSE_WARN_LINES:
            parts.append(
                f"warning: your program printed {len(prose)} lines of explanatory prose. "
                "Print results only; the written answer is produced separately from "
                "this output."
            )
        return "\n\n".join(parts)

    # RETURN_DIRECT ENDS THE LOOP HERE. The reply used to say "Stop now", and
    # the model still needed one more paid turn to read it and stop -- one call
    # in every task, a fifth of a healthy basic one. langchain's agent factory
    # exits when every tool called in a turn is return_direct.
    @tool(return_direct=True)
    def record_task_result(
        entry_file: str,
        status: Literal["passed", "failed", "blocked"],
        notes: str = "",
        missing: str = "",
    ) -> str:
        """Declare this task finished. Call it exactly once, as your final action.

        status:
          "passed"  run_solution exited 0 and printed what the task asked for.
          "failed"  you had everything you needed but could not get it working.
                    An honest failure is far more useful than a false success.
          "blocked" the task needs something this environment does not have:
                    a library that is not installed, a file format nothing here
                    can read, data you were not given, internet access, a
                    screen, camera or GPU, or a language other than Python.
                    Stop as soon as you know. Never build a substitute.

        missing: with "blocked", or with "passed" when one part of the task could
        not be done, ONE plain sentence for the student saying what is missing
        and what they can do -- no code, no error names. For example: "I can't
        open .sav files here, so please send the data as a CSV or Excel file."
        """
        recorder.entry_file = entry_file
        recorder.status = status
        recorder.notes = notes
        recorder.missing = missing
        return f"Recorded {entry_file} as {status}."

    return [run_solution, record_task_result]
