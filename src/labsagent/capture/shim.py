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
import runpy
import sys

EXIT_INPUT_EXHAUSTED = 42
SENTINEL_EXHAUSTED = "__LABSAGENT_INPUT_EXHAUSTED__"
SENTINEL_RAW_STDIN = "__LABSAGENT_RAW_STDIN__"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} <target.py> <inputs.txt>", file=sys.stderr)
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
        runpy.run_path(target, run_name="__main__")
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
