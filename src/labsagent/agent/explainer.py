"""Write the report prose in a context that never saw the debugging.

THE PROBLEM THIS SOLVES IS STRUCTURAL, NOT STYLISTIC. Until now the explanation
under each task came from `record_task_result(notes=...)` -- written by the
solver, as its final act, with its entire session still in context: three failed
attempts, two tracebacks, the tool schemas, the system prompt. Agents write
about what they have been looking at, so it produced prose about the journey
("I initially used a while loop but it looped forever, so I switched to
range()") when a lab report wants prose about the destination.

You cannot prompt that away. What is in context is what the model is
conditioned on, and "do not mention the debugging" competes against 50,000
tokens of debugging. The fix is to not assemble that context in the first
place: a separate call, whose entire input is the task statement, the final
code, and what it printed.

WHY WE COMPOSE THE BRIEF RATHER THAN DELEGATING. deepagents offers exactly this
shape -- `SubAgent(mode="isolated")`, which "receives only the delegated task
description". But that description is the `task()` tool's argument, written by
the PARENT out of the parent's own context, so the pollution walks straight back
in through the seam meant to stop it. Delegation also hands the model the choice
of whether to explain at all, and re-adds the `task` tool that build.py
deliberately removed at 418 tokens per turn of every task.

Building the brief ourselves makes the isolation a property of the code rather
than of the model's judgement: the debugging transcript cannot leak, because it
is never assembled.

The general rule: CONTEXT ISOLATION IS ENFORCED AT THE CALLER, NOT REQUESTED AT
THE CALLEE. If it matters what a model cannot see, the only reliable place to
decide that is where the messages are built.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from labsagent.models import Task, Transcript
from labsagent.usage import RunUsage

# Kept byte-stable for the same reason SOLVER_PROMPT is: it is the cacheable
# prefix of every explain call. Per-task values -- above all the LENGTH -- go in
# the brief, never here. This is the rule web/server/pipeline.py follows with
# NARRATION, applied a second time.
EXPLAINER_PROMPT = """You write the short explanation that appears under a task in a university
programming lab report.

You are given a task statement, the final working program, and what that program printed when it
ran. That is everything you need, and everything you get.

How to write it:

- Describe what the program does and how it does it. Name the constructs actually used -- input(),
  int(), an f-string, a list comprehension, a slice -- because naming them is what the explanation
  is for.
- Write about the program in the third person. "The program reads two integers" is right. "I wrote
  a program that reads two integers" is wrong.
- Never mention attempts, errors, fixes, debugging, or how the program came to be. You did not see
  any of that, and it does not belong in a report.
- Plain prose only. No markdown, no asterisks, no backticks, no headings, no bullet points, no
  code blocks.
- Write exactly as many sentences as the brief asks for. Not more.
- Match the VOICE of the example below, never its wording. It describes a different program than
  yours. Describe the program you were actually given.

The voice and length to match, for a two-sentence explanation:

    The program builds a running total across the prices in the list, applying the discount to each
    one before adding it. The formatted output uses an f-string with :.2f so the total always shows
    two decimal places.
"""

# How many sentences each kind of task gets. `wants_explanation` is set by
# ingest when the task text says "explain" or "discuss"; everything else gets
# the short form on purpose. A long machine-written essay under every task is
# more conspicuous than a two-sentence note, which is the failure mode this
# feature is most likely to produce.
SHORT_SENTENCES = 2
LONG_SENTENCES = 5

# Bounds on the brief. Output is what costs money here (2x the cache-miss input
# rate under thinking mode), but an unbounded transcript from a program that
# printed 10,000 lines would still dominate the input, and the explanation does
# not improve past the first screenful.
MAX_CODE_CHARS = 4000
MAX_OUTPUT_LINES = 40

# Same expression as web/server/trace.py, duplicated rather than imported: the
# core must not depend on the web layer. Both exist because a plain-prose
# surface -- a chat bubble, a DOCX paragraph -- renders markdown emphasis as
# literal punctuation. Any place model text lands somewhere without a markdown
# renderer needs this, so it recurs by nature.
_MARKDOWN = re.compile(r"(\*\*|__|`{1,3}|\*|_)(?=\S)|(?<=\S)(\*\*|__|`{1,3}|\*|_)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def plain_text(text: str) -> str:
    """Strip inline markdown, which a DOCX run shows as literal characters."""
    return _MARKDOWN.sub("", text).strip()


def clamp_sentences(text: str, limit: int) -> str:
    """Keep at most `limit` sentences, cutting only on sentence boundaries.

    The prompt asks for a length and the model mostly obliges; "mostly" is not a
    contract. Truncating in code makes it one. Cutting on boundaries rather than
    characters is what keeps this from being worse than the problem -- a
    half-sentence under a task reads as a bug, where a short paragraph reads as
    a short paragraph.
    """
    sentences = [s for s in _SENTENCE_END.split(text.strip()) if s.strip()]
    if len(sentences) <= limit:
        return " ".join(sentences)
    return " ".join(sentences[:limit])


def sentence_budget(task: Task) -> int:
    return LONG_SENTENCES if task.wants_explanation else SHORT_SENTENCES


def build_brief(task: Task, code_text: str, transcript: Transcript | None) -> str:
    """Everything the explainer is allowed to know, and nothing else.

    Note what is absent: the retry count, the errors, the attempts, the system
    prompt the solver ran under, any other task. This function IS the isolation
    boundary -- it is worth reading as the specification of one.
    """
    budget = sentence_budget(task)
    code = code_text.strip()
    if len(code) > MAX_CODE_CHARS:
        code = code[:MAX_CODE_CHARS] + "\n# ... truncated ..."

    parts = [
        f"Task: {task.title}",
        f"\nWhat the task asked for:\n{task.statement.strip()}",
        f"\nThe program:\n```python\n{code}\n```",
    ]

    if transcript is not None:
        lines = transcript.display_lines()
        if len(lines) > MAX_OUTPUT_LINES:
            lines = lines[:MAX_OUTPUT_LINES] + ["... output truncated ..."]
        parts.append("\nWhat it printed when it ran:\n" + "\n".join(lines))

    parts.append(
        f"\nWrite the explanation now, in exactly {budget} "
        f"{'sentence' if budget == 1 else 'sentences'}."
    )
    return "\n".join(parts)


@dataclass
class Explainer:
    """Callable seam: `(task, code_text, transcript) -> str | None`.

    A callable rather than a model, for the reason `Sandbox` and
    `ScreenshotBackend` are protocols: the orchestrator should depend on the
    smallest interface that does the job. Tests pass a lambda, the CLI and the
    web layer pass this, and none of them has to agree about model clients.

    Returns None rather than raising when anything goes wrong. An explanation is
    a decoration on a task that already succeeded; letting its failure propagate
    would let cosmetics kill a run whose real work is done. That is the same
    judgement `solve_task` already makes about artifact collection.
    """

    model: Any
    usage: RunUsage | None = None
    phase: str = "explain"

    def __call__(
        self, task: Task, code_text: str, transcript: Transcript | None
    ) -> str | None:
        if not code_text.strip():
            return None
        messages = [
            {"role": "system", "content": EXPLAINER_PROMPT},
            {"role": "user", "content": build_brief(task, code_text, transcript)},
        ]
        try:
            reply = self.model.invoke(messages)
        except Exception:  # noqa: BLE001
            return None

        if self.usage is not None:
            self.usage.phase(self.phase).add_message(reply)

        text = reply.content if isinstance(reply.content, str) else str(reply.content)
        cleaned = clamp_sentences(plain_text(text), sentence_budget(task))
        return cleaned or None
