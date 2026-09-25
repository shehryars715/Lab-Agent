"""stdin echo shim. Runs INSIDE the sandbox; imports nothing from labsagent.

Piping stdin means typed values never appear next to their prompts -- raw stdout
reads "Enter n: Enter m: Sum = 8". Rather than reconstruct the echo afterwards
(guesswork that breaks whenever prompts don't map 1:1 to inputs), we patch
input() to echo at the exact moment of consumption. The transcript is then
correct by construction: nothing is fabricated, because the echo happens where
the read happens.

Usage:  python shim.py <target.py> <inputs.txt>
"""

from __future__ import annotations

import builtins
import json
import os
import runpy
import sys
import types

EXIT_INPUT_EXHAUSTED = 42
SENTINEL_EXHAUSTED = "__LABSAGENT_INPUT_EXHAUSTED__"
SENTINEL_RAW_STDIN = "__LABSAGENT_RAW_STDIN__"
# Section mode. Plain ASCII on purpose: str.splitlines() treats control
# characters such as the record separator as line breaks, which split the
# marker off its own line and silently lost every section's output.
CELL_MARK = "@@LABSAGENT-CELL-3b9e@@"
FIG_MARK = "@@LABSAGENT-FIGS-3b9e@@"
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".svg")


def split_cells(source: str) -> list[tuple[str, str, int]]:
    """(title, code, first line index) per `# %% title` section.

    Only column-0 markers split, so a marker inside a function cannot cut a
    block in half. Code before the first marker is its own untitled section.
    """
    cells: list[tuple[str, str, int]] = []
    title, buf, start = "", [], 0
    for index, line in enumerate(source.splitlines(keepends=True)):
        if line.startswith("# %%"):
            if "".join(buf).strip():
                cells.append((title, "".join(buf), start))
            title, buf, start = line[4:].strip(), [], index + 1
        else:
            buf.append(line)
    if "".join(buf).strip():
        cells.append((title, "".join(buf), start))
    return cells


def _images() -> dict[str, float]:
    return {
        name: os.path.getmtime(name)
        for name in os.listdir(".")
        if name.lower().endswith(IMAGE_SUFFIXES)
    }


def run_cells(target: str) -> int:
    """Run the program section by section in ONE module, marking the boundaries.

    Instrumented, not reconstructed -- the same principle as the input echo:
    each section's output is exactly what it printed, because the marker is
    written at the moment the section starts. A real `__main__` module (not a
    bare dict) is what keeps dataclasses and pickling working as under runpy.
    """
    with open(target, encoding="utf-8") as fh:
        source = fh.read()
    module = types.ModuleType("__main__")
    module.__file__ = target
    sys.modules["__main__"] = module
    for number, (_title, code, start) in enumerate(split_cells(source)):
        before = _images()
        print(f"{CELL_MARK}{number}", flush=True)
        try:
            exec(compile("\n" * start + code, target, "exec"), module.__dict__)
        finally:
            sys.stdout.flush()
            after = _images()
            new = sorted(name for name, mtime in after.items() if before.get(name) != mtime)
            print(f"{FIG_MARK}{number}:{json.dumps(new)}", file=sys.stderr, flush=True)
    return 0


def main(argv: list[str]) -> int:
    cells = len(argv) == 4 and argv[1] == "--cells"
    if cells:
        argv = [argv[0], *argv[2:]]
    if len(argv) != 3:
        print(f"usage: {argv[0]} [--cells] <target.py> <inputs.txt>", file=sys.stderr)
        return 2

    target, inputs_path = argv[1], argv[2]

    try:
        with open(inputs_path, encoding="utf-8") as fh:
            supplied = fh.read().splitlines()
    except FileNotFoundError:
        supplied = []

    values = iter(supplied)
    consumed = 0

    def _input(prompt: str = "") -> str:
        nonlocal consumed
        try:
            value = next(values)
        except StopIteration:
            # The program wants more input than the manual supplied. Signal it
            # unambiguously rather than dying with a bare StopIteration.
            print(f"\n{SENTINEL_EXHAUSTED}:{consumed}", file=sys.stderr, flush=True)
            raise SystemExit(EXIT_INPUT_EXHAUSTED) from None
        consumed += 1
        # Exactly what a terminal shows: the prompt, then the typed value.
        print(f"{prompt}{value}", flush=True)
        return value

    # Programs that read sys.stdin directly bypass input() entirely, so the echo
    # never happens and the transcript would silently lose their values. Flag it
    # so the caller can warn rather than ship a misleading screenshot. The PTY
    # backend is the real fix for these.
    real_readline = sys.stdin.readline

    def _readline(*args, **kwargs):
        print(SENTINEL_RAW_STDIN, file=sys.stderr, flush=True)
        return real_readline(*args, **kwargs)

    builtins.input = _input
    try:
        sys.stdin.readline = _readline  # type: ignore[method-assign]
    except (AttributeError, TypeError):
        pass  # some stdin replacements are read-only; not worth failing over

    sys.argv = [target]
    try:
        if cells:
            return run_cells(target)
        runpy.run_path(target, run_name="__main__")
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
