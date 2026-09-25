"""Is this line of program output a sentence someone meant to write in a report?

WHY IT EXISTS. A task that says "explain why the perceptron fails on XOR" used
to be answered by `print()`: the solver's only output channel is stdout, so the
analysis became thirty lines of terminal output -- a two-page screenshot in the
Word report and a stream under the code cell in the notebook. The prompt now
says "print results, not essays", and this is the check behind it: the solver's
tool result carries a warning when the output reads like prose, and the eval
reports how much prose reached stdout.

A HEURISTIC, AND DELIBERATELY A CAUTIOUS ONE. It only has to tell "Accuracy:
0.75" and "x1=0 x2=1 target=1" apart from "The perceptron cannot separate the
classes because...". False negatives cost nothing -- the warning simply does not
fire. False positives cost one extra sentence in a tool result. So a line needs
length, several function words and few digits before it counts.
"""

from __future__ import annotations

import re

#: At this many prose lines in one run, the tool result says so.
PROSE_WARN_LINES = 4

_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")
_FUNCTION_WORDS = frozenset(
    "the a an of to and or but because so that which this these those is are was "
    "were be been it its as by for with from into than then when while if not "
    "can cannot does do on in at".split()
)


def looks_like_prose(line: str) -> bool:
    """True for a line that reads as an English sentence rather than a result."""
    text = line.strip()
    if not text or text.isupper():
        return False
    words = _WORD.findall(text)
    if len(words) < 8:
        return False
    function = sum(1 for w in words if w.lower() in _FUNCTION_WORDS)
    if function < 2:
        return False
    digits = sum(ch.isdigit() for ch in text)
    return digits <= len(text) * 0.08


def prose_lines(output: str) -> list[str]:
    """Every prose-like line in a block of program output."""
    return [line for line in (output or "").splitlines() if looks_like_prose(line)]
