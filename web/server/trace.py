"""Stream what the agent is *doing*, not just where it is.

The core emits task-level events: TaskStarted, AttemptStarted, TaskFinished.
That is enough to draw a stepper, and not enough to fill a minute of waiting --
a task is one model-driven loop that writes a file, runs it, reads the failure,
and tries again, all invisibly.

The tool calls are that loop made visible:

    write_file    task1.py
    run_solution  task1.py   -> exit_code: 1, Traceback ...
    write_file    task1.py          (it is fixing itself)
    run_solution  task1.py   -> exit_code: 0, hello
    record_task_result  passed

HOW THIS REACHES THE TOOL CALLS WITHOUT TOUCHING THE CORE.

`orchestrator.solve_task` calls `agent.invoke(...)`, and there is no hook
parameter on that call. The seam that does exist is the `model` argument:
`run_lab(..., model=...)` threads through to `build_solver`, and LangChain
Runnables carry configuration down to every run they start.

`register_configure_hook` is that mechanism, made global. It is the supported
extension point `tracing_v2_enabled` uses to attach the LangSmith tracer to
every run in a process, and it works here for the same reason. A `ContextVar`
holds the handler, the hook folds it into every runnable's config, and the
`with` block in `pipeline.run_job` scopes it to one run on one thread.

Two properties make this safe to do from outside the library:

- The `ContextVar` is scoped to the worker thread, so two concurrent runs
  cannot see each other's callbacks.
- `BaseCallbackHandler.raise_error` is False by default, so an exception in
  here is logged and dropped rather than propagated into the agent's loop. A
  broken progress display must never be able to break a run -- the same
  contract `events.Emitter` states for its consumers.
"""

from __future__ import annotations

import ast
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tracers.context import register_configure_hook

_TRACER: ContextVar[Any] = ContextVar("labsagent_tool_tracer", default=None)
register_configure_hook(_TRACER, inheritable=False)

# Only these are worth a line. read_file is not: the agent is told not to
# re-read what it just wrote, and when it does, that is noise rather than
# progress. Filtering here rather than in the UI keeps the wire quiet -- the
# same reasoning as trimming the tool schemas in agent/build.py.
_SHOWN = {"write_file", "run_solution", "record_task_result"}

_EXIT = re.compile(r"exit_code:\s*(-?\d+)")


def _inputs(input_str: Any, kwargs: dict) -> dict:
    """The tool's arguments, from whichever shape the callback hands us.

    `on_tool_start` gives a `serialized` mapping and an `input_str`, and
    `input_str` is the repr of the arguments rather than the arguments. Newer
    versions also pass them through kwargs. Both are handled, because losing
    the filename would turn "running task2.py" into "running something", which
    is exactly the detail worth showing.
    """
    given = kwargs.get("inputs")
    if isinstance(given, dict):
        return given
    if isinstance(input_str, dict):
        return input_str
    if isinstance(input_str, str):
        try:
            parsed = ast.literal_eval(input_str.strip())
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, SyntaxError):
            pass
    return {}


def _basename(value: Any) -> str:
    return Path(str(value)).name if value else ""


_MARKDOWN = re.compile(r"(\*\*|__|`{1,3}|\*|_)(?=\S)|(?<=\S)(\*\*|__|`{1,3}|\*|_)")


def _plain(text: str) -> str:
    """Strip inline markdown emphasis, which the chat renders as literal text.

    The model writes `**How it works:**` and `int` because it is writing for a
    markdown renderer. The chat shows narration as plain prose, so the markers
    survive as visible asterisks and backticks -- which reads as broken rather
    than as emphasis.

    Stripping rather than rendering is the deliberate choice: a markdown
    renderer is a dependency, an XSS surface, and it would encourage the model
    to write longer, more structured narration when what this needs is one
    plain sentence.
    """
    return _MARKDOWN.sub("", text).strip()


def _text_of(response: Any) -> str:
    """The visible text of an LLMResult, across the shapes it comes in.

    `generations` is a list of lists -- one inner list per prompt. Content can
    be a plain string or a list of content blocks depending on whether the
    model returned multimodal parts, and this is defensive about that because
    the cost of getting it wrong is a crash inside a callback, which would be
    swallowed and silently kill all narration.
    """
    try:
        generations = getattr(response, "generations", None) or []
        for batch in generations:
            for generation in batch:
                content = getattr(getattr(generation, "message", None), "content", None)
                if isinstance(content, str) and content.strip():
                    return content.strip()
                if isinstance(content, list):
                    parts = [
                        block.get("text", "")
                        for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    ]
                    joined = " ".join(p for p in parts if p).strip()
                    if joined:
                        return joined
    except Exception:  # noqa: BLE001 -- see docstring
        return ""
    return ""


class ToolTracer(BaseCallbackHandler):
    """Turns tool calls into `activity` frames for the browser.

    `task_id` is set from the outside by the event consumer on TaskStarted,
    because a callback fires with no idea which task it belongs to. Both run on
    the same thread in the same run, so a plain attribute is enough; the
    alternative -- parsing a task id out of a filename -- would be guessing.
    """

    def __init__(self, emit: Callable[[dict], None]) -> None:
        self.emit = emit
        self.task_id = ""
        self._open: dict[Any, str] = {}
        self._last_text = ""

    def _send(self, text: str, tone: str = "") -> None:
        self.emit({"type": "activity", "task_id": self.task_id, "text": text, "tone": tone})

    def on_llm_end(self, response, **kwargs) -> None:
        """The model's own words, between its tool calls.

        This is what separates a chat from a log. `response` carries the full
        generation, and the visible text is the model thinking out loud --
        "I'll read two numbers and print their sum" -- as distinct from the
        `tool_calls` it made, which the UI already shows as actions.

        Not narrated: anything the model returns as a pure tool call with no
        prose. The agent is told to narrate once before its first tool call, so
        silence here is normal rather than a bug, and emitting an empty frame
        for it would put blank lines in the transcript.
        """
        text = _plain(_text_of(response))
        if not text:
            return
        # Tool-call arguments are echoed back in some provider responses as
        # JSON. That is not narration, and rendering it would show the user
        # raw function arguments dressed up as a sentence.
        if text.lstrip().startswith("{") and text.rstrip().endswith("}"):
            return
        if text == self._last_text:
            return
        self._last_text = text
        self.emit({"type": "narration", "task_id": self.task_id, "text": text})

    def on_tool_start(self, serialized, input_str, *, run_id=None, **kwargs) -> None:
        name = (serialized or {}).get("name", "")
        if name not in _SHOWN:
            return
        self._open[run_id] = name
        args = _inputs(input_str, kwargs)

        if name == "write_file":
            self._send(f"writing {_basename(args.get('file_path')) or 'the solution'}")
        elif name == "run_solution":
            self._send(f"running {_basename(args.get('entry_file')) or 'it'}")
        elif name == "record_task_result":
            status = args.get("status", "")
            self._send(f"recording result: {status}", "" if status == "passed" else "warn")

    def on_tool_end(self, output, *, run_id=None, **kwargs) -> None:
        name = self._open.pop(run_id, "")
        if name != "run_solution":
            return
        # The exit code is the one fact worth surfacing from a run: a 0 says
        # the attempt worked, and a non-zero is the reason the next thing that
        # happens is another write_file.
        match = _EXIT.search(str(output))
        if not match:
            return
        code = int(match.group(1))
        self._send(
            "exit 0" if code == 0 else f"exit {code} — fixing",
            "ok" if code == 0 else "warn",
        )

    def on_tool_error(self, error, *, run_id=None, **kwargs) -> None:
        if self._open.pop(run_id, ""):
            self._send(f"tool failed: {str(error)[:80]}", "bad")


@contextmanager
def tracing_tools(emit: Callable[[dict], None]):
    """Scope a tracer to this thread for the duration of the block."""
    tracer = ToolTracer(emit)
    token = _TRACER.set(tracer)
    try:
        yield tracer
    finally:
        _TRACER.reset(token)
