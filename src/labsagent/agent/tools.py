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

from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.tools import BaseTool, tool

from labsagent.runner import run_solution as _run_solution
from labsagent.sandbox.base import DEFAULT_TIMEOUT_S, Sandbox


@dataclass
class TaskRecorder:
    """Collects what the agent reports, plus what actually happened.

    The agent's own claim of success is never trusted on its own -- `attempts`
    and `last_ok` are recorded by us, from real execution.
    """

    entry_file: str | None = None
    status: str | None = None
    notes: str = ""
    attempts: int = 0
    last_ok: bool = False
    last_stdout: str = ""
    last_stderr: str = ""
    last_transcript: Any = None
    warnings: list[str] = field(default_factory=list)

    @property
    def finished(self) -> bool:
        return self.status is not None


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
        recorder.last_ok = outcome.ok
        recorder.last_stdout = outcome.result.stdout
        recorder.last_stderr = outcome.result.stderr
        recorder.last_transcript = outcome.transcript
        recorder.warnings = outcome.warnings

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
