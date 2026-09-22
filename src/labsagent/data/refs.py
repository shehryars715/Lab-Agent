"""Turning one answer into the list of references it names.

WHY THIS IS NOT `raw.split()`. The briefing offers the data question with its
value already filled in, built by joining what we propose with ", ". The answer
came back and was split on commas AND whitespace alike -- so

    Online Retail.xlsx, https://archive.ics.uci.edu/dataset/352/online+retail

became three references: 'Online', 'Retail.xlsx', and the URL. All three failed,
the run solved five tasks against no data at all, and the solver spent a paid
quarter of an hour reconstructing a file it had never been given. A filename
with a space in it was simply unusable.

THE RULE IS THAT AN EXPLICIT SEPARATOR WINS. If a comma appears, the comma is
the separator and spaces belong to the filenames -- a comma is what WE put
there, so its presence means the canonical form was used. If the answer spans
several lines, the newline is the separator, for the same reason. Only a single
line with no comma is split on whitespace, which is what someone typing two bare
filenames means.

TWO FUNCTIONS, BECAUSE THERE ARE TWO GRAMMARS. `split_refs` reads a field whose
whole value IS a list of references. `candidate_tokens` reads PROSE -- an answer
to a question the agent invented, where "use numpy, not plain Python" must yield
nothing at all. There, splitting more eagerly is correct, because `classify()`
is the filter and a wrong guess costs nothing.
"""

from __future__ import annotations

#: Stripped from the end of a token. A reference typed into a sentence picks up
#: the sentence's punctuation; a leading "." is NOT stripped, because
#: "./data.csv" is a path.
_TRAILING = ".,;:)"

_QUOTES = "\"'"


def _scan(line: str, separators: str) -> list[str]:
    r"""Split on any character in `separators`, honouring quotes.

    Hand-rolled rather than `shlex`, which eats backslashes: `shlex.split` turns
    "C:\Users\x.csv" into "C:Usersx.csv", and a Windows path is the single most
    likely thing to be pasted into this box.
    """
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None

    for char in line:
        if quote:
            if char == quote:
                quote = None
            else:
                current.append(char)
            continue
        if char in _QUOTES:
            quote = char
            continue
        if char in separators:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)

    parts.append("".join(current))
    return parts


def _clean(token: str) -> str:
    return token.strip().rstrip(_TRAILING).strip()


def _dedupe(tokens) -> list[str]:
    found: list[str] = []
    for token in tokens:
        if token and token not in found:
            found.append(token)
    return found


def has_comma(line: str) -> bool:
    """True when a comma appears outside quotes."""
    quote: str | None = None
    for char in line:
        if quote:
            if char == quote:
                quote = None
            continue
        if char in _QUOTES:
            quote = char
        elif char == ",":
            return True
    return False


def split_refs(raw: str) -> list[str]:
    """The references named by a field whose value is a reference list.

    See the explicit-separator rule above.
    """
    lines = [line for line in str(raw or "").splitlines() if line.strip()]
    # AN EXPLICIT SEPARATOR DEMOTES WHITESPACE. Putting each reference on its
    # own line says "these are the references" just as plainly as a comma does,
    # so a multi-line answer splits on newlines and nothing else -- otherwise a
    # line reading "Online Retail.xlsx" would become two references.
    multiline = len(lines) > 1

    found: list[str] = []
    for line in lines:
        if has_comma(line):
            separators = ","
        elif multiline:
            found.append(_clean(line))
            continue
        else:
            separators = " \t"
        found.extend(_clean(part) for part in _scan(line, separators))
    return _dedupe(found)


def candidate_tokens(raw: str) -> list[str]:
    """Everything in a PROSE answer that might be a reference, widest first.

    Each comma-delimited segment is offered whole BEFORE its individual words,
    so "Online Retail.xlsx, use pandas" still offers "Online Retail.xlsx" as one
    candidate -- while "use numpy, not plain Python" offers segments and words
    that `classify()` will reject, which is the correct outcome for prose.
    """
    found: list[str] = []
    for line in str(raw or "").splitlines():
        for segment in _scan(line, ","):
            cleaned = _clean(segment)
            if not cleaned:
                continue
            found.append(cleaned)
            for word in _scan(cleaned, " \t"):
                found.append(_clean(word))
    return _dedupe(found)
