"""Run a solution file in a sandbox and get back a faithful Transcript.

This is what the Phase 1 `run_solution` agent tool will wrap.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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
#: The pseudo-terminal driver for every language but Python (Harness v2, D4).
DRIVER_SRC = Path(__file__).parent / "capture" / "driver.py"
DRIVER_NAME = "_labsagent_run.py"
JOB_NAME = "_labsagent_job.json"
#: Every file the harness writes beside a solution. Never a result, never
#: handed forward, never handed in.
HARNESS_FILES = frozenset({SHIM_NAME, INPUTS_NAME, DRIVER_NAME, JOB_NAME})


IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".svg")


@dataclass
class RunOutcome:
    result: ExecResult
    transcript: Transcript
    warnings: list[str]
    figures: list[str] = None  # type: ignore[assignment]
    #: The files the program was built from, as the driver reports them (D8).
    #: Empty for Python, whose one file is the entry.
    sources: list[str] = None  # type: ignore[assignment]
    #: The build failed: `result.stderr` is the compiler's complaint.
    compile_error: bool = False

    def __post_init__(self) -> None:
        if self.figures is None:
            self.figures = []
        if self.sources is None:
            self.sources = []

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


def run_sections(
    sandbox: Sandbox,
    entry_file: str,
    stdin_values: list[str] | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> list[dict] | None:
    """Run a passed solution section by section: real output per `# %%` part.

    Returns [{"title", "code", "lines", "figures"}] or None. None is the normal
    answer for a program with fewer than two sections, one whose sections do
    not each compile on their own, or one that does not run cleanly this way --
    and every caller treats None as "keep the single block". Costs no model
    tokens: it is one subprocess, run after the task has already passed.
    """
    from labsagent.capture.shim import CELL_MARK, FIG_MARK, split_cells

    try:
        source = sandbox.read_file(entry_file)
    except Exception:  # noqa: BLE001 -- no file, no sections
        return None
    cells = split_cells(source)
    if len(cells) < 2:
        return None
    for _title, code, _start in cells:
        try:
            compile(code, entry_file, "exec")
        except SyntaxError:
            return None

    values = list(stdin_values or [])
    sandbox.write_file(SHIM_NAME, SHIM_SRC.read_text(encoding="utf-8"))
    sandbox.write_file(INPUTS_NAME, "\n".join(values) + ("\n" if values else ""))
    try:
        raw = sandbox.run(
            ["python", SHIM_NAME, "--cells", entry_file, INPUTS_NAME], timeout=timeout_s
        )
    except Exception:  # noqa: BLE001 -- a decoration must never fail the task
        return None
    if raw.exit_code != 0 or raw.timed_out:
        return None

    outputs: list[list[str]] = [[] for _ in cells]
    current = -1
    for line in raw.stdout.splitlines():
        if line.startswith(CELL_MARK):
            try:
                current = int(line[len(CELL_MARK):])
            except ValueError:
                return None
            continue
        if 0 <= current < len(outputs):
            outputs[current].append(line)
    figures: list[list[str]] = [[] for _ in cells]
    import json

    for line in raw.stderr.splitlines():
        if line.startswith(FIG_MARK):
            number, _, names = line[len(FIG_MARK):].partition(":")
            try:
                figures[int(number)] = list(json.loads(names))
            except (ValueError, IndexError):
                continue
    return [
        {"title": title, "code": code.strip("\n"), "lines": lines, "figures": figs}
        for (title, code, _start), lines, figs in zip(cells, outputs, figures)
    ]


def run_solution(
    sandbox: Sandbox,
    entry_file: str,
    stdin_values: list[str] | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    language: str = "python",
) -> RunOutcome:
    """Execute `entry_file` under the echo shim.

    Returns a Transcript whose lines read exactly as a real terminal session
    would, because input() echoes at the moment of consumption rather than being
    reconstructed afterwards.

    `language` picks the path: Python runs under the shim (below); every other
    language goes to the pseudo-terminal driver (Harness v2, D4).
    """
    from labsagent import languages as langs

    lang = langs.resolve(language) or langs.PYTHON
    if lang.echo != "shim":
        return _run_on_pty(sandbox, lang, entry_file, stdin_values, timeout_s)
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
        truncated=raw.truncated,
    )

    return RunOutcome(
        result=result,
        transcript=Transcript.from_exec(f"python {entry_file}", result),
        warnings=warnings,
        figures=figures,
    )


def _strip_marks(stderr: str) -> tuple[str, dict]:
    """The driver's marker lines out of stderr, and what they said."""
    import json

    from labsagent.capture import driver

    marks: dict = {"compile_error": False, "build_output": "", "sources": []}
    keep: list[str] = []
    for line in stderr.splitlines():
        if line.strip() == driver.MARK_COMPILE:
            marks["compile_error"] = True
        elif line.startswith(driver.MARK_BUILD):
            try:
                marks["build_output"] = str(json.loads(line[len(driver.MARK_BUILD):]))
            except ValueError:
                pass
        elif line.startswith(driver.MARK_SOURCES):
            try:
                marks["sources"] = [str(s) for s in json.loads(line[len(driver.MARK_SOURCES):])]
            except ValueError:
                pass
        else:
            keep.append(line)
    return "\n".join(keep).rstrip(), marks


def _run_on_pty(sandbox: Sandbox, lang, entry_file, stdin_values, timeout_s) -> RunOutcome:
    """Build and run `entry_file` on a pseudo-terminal, in the sandbox.

    The driver goes into the workspace beside the code, as the shim does, with a
    job naming only the language, the file and the inputs: the commands are the
    driver's own (D6). Its stdout is already what the screen showed, typed
    input included where the program read it.

    A driver that refuses its job, or cannot run here at all (it needs POSIX:
    on a Windows machine use the runner), is the infrastructure failing, not
    the agent: SandboxError, which does not use up an attempt.
    """
    import json

    from labsagent.capture import driver
    from labsagent.errors import SandboxError

    entry = str(entry_file).replace("\\", "/").lstrip("/")
    if entry.startswith("./"):
        entry = entry[2:]
    values = list(stdin_values or [])
    sandbox.write_file(DRIVER_NAME, DRIVER_SRC.read_text(encoding="utf-8"))
    sandbox.write_file(
        JOB_NAME,
        json.dumps({"language": lang.key, "entry": entry, "inputs": values, "timeout": timeout_s}),
    )
    before = set(sandbox.list_files())
    raw = sandbox.run(
        ["python", DRIVER_NAME, JOB_NAME], timeout=timeout_s + driver.BUILD_TIMEOUT_S
    )
    if raw.exit_code == driver.EXIT_BAD_JOB and not raw.stdout:
        raise SandboxError(f"the {lang.display} driver could not run: {raw.stderr.strip()[:300]}")

    figures = sorted(
        f
        for f in set(sandbox.list_files()) - before
        if f.lower().endswith(IMAGE_SUFFIXES) and not f.startswith(driver.BUILD_DIR + "/")
    )
    stderr, marks = _strip_marks(raw.stderr)
    warnings: list[str] = []
    if raw.exit_code == EXIT_INPUT_EXHAUSTED or SENTINEL_EXHAUSTED in raw.stderr:
        warnings.append(
            f"{entry} asked for more input than the {len(values)} value(s) supplied."
        )
    if marks["build_output"] and not marks["compile_error"]:
        warnings.append(f"the compiler warned: {marks['build_output'][:1500]}")

    result = ExecResult(
        exit_code=raw.exit_code,
        stdout=raw.stdout,
        stderr=_clean_stderr(stderr),
        duration_s=raw.duration_s,
        timed_out=raw.timed_out,
        truncated=raw.truncated,
    )
    setup, command = lang.commands(entry, "windows")
    transcript = replace(
        Transcript.from_exec(command, result),
        language=lang.key,
        entry=entry,
        setup=tuple(setup),
    )
    return RunOutcome(
        result=result,
        transcript=transcript,
        warnings=warnings,
        figures=figures,
        sources=marks["sources"] or [entry],
        compile_error=marks["compile_error"],
    )
