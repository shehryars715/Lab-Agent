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

import json
import re
from dataclasses import dataclass, field
from typing import Any

from labsagent.models import Task, Transcript
from labsagent.prerequisites import writer_lines
from labsagent.usage import RunUsage

# Kept byte-stable for the same reason SOLVER_PROMPT is: it is the cacheable
# prefix of every explain call. Per-task values -- above all the LENGTH -- go in
# the brief, never here. This is the rule web/server/pipeline.py follows with
# NARRATION, applied a second time.
EXPLAINER_PROMPT = """You write the short written part of a student's lab submission for one
task: a note on what their program does, and answers to any written questions. Write it the way
the student would write it themselves.

You are given the task statement, the final program, and what it printed when it ran. That is
everything you need, and everything you get. Some tasks have no program: they are questions
answered in words alone.

How it should read:

- Like a student telling their instructor what they did: plain, direct and specific. First person
  is natural ("I keep a running total..."), and so are contractions. It should not sound like a
  textbook or a product description.
- Say what the program does for THIS task, in the task's own terms. Mention a technique only when
  it matters to the result. Do not walk through the functions or constructs one by one.
- Vary how sentences begin. Never open with "This program", "The program", "In this task",
  "Overall" or "In summary".
- No filler and no stock phrases: never "utilize", "leverage", "demonstrate", "showcase", "ensure",
  "robust", "efficient", "seamless", "comprehensive", "it is worth noting", "in conclusion". No em
  dashes and no semicolon chains. Short sentences are fine; mix them with longer ones.
- Written questions: answer each one in its first sentence, then give the reason. Use the
  program's actual numbers. Never invent a number the output does not show; if it does not show
  something, say what the program shows instead.
- Never mention attempts, errors, fixes, debugging, or how the program came to be. You did not see
  any of that, and it does not belong in a submission.
- Never state facts about work you were not shown: a previous lab, the student's earlier choices
  or files. What this program computes is this program's result, not the student's earlier work.
  If a question asks about something the brief does not contain, say it was not provided.
- Plain prose only. No markdown, no asterisks, no backticks, no headings, no bullet points, no
  code blocks.
- Stay within the length the brief gives. Shorter is better than padded.

When the brief lists NO written questions, reply with the note as plain text and nothing else.
When it DOES list written questions, reply with ONLY a JSON object, answers in question order:

    {"overview": "...", "answers": ["answer to question 1", "answer to question 2"]}

Two examples of the voice. They are about different programs than yours: match how they sound,
never what they say.

    I add up the prices with the 10% discount taken off each one first. The total prints with two
    decimals, so 3.5 comes out as 3.50.

    Q: Why does accuracy drop when k gets large?
    A: Because a big k pulls in neighbours from the other classes. Accuracy fell from 0.96 at k=3
    to 0.81 at k=25 as the vote got noisier.
"""

# How many sentences each kind of task gets. `wants_explanation` is set by
# ingest when the task text says "explain" or "discuss"; everything else gets
# the short form on purpose. A long machine-written essay under every task is
# more conspicuous than a two-sentence note, which is the failure mode this
# feature is most likely to produce.
SHORT_SENTENCES = 2
LONG_SENTENCES = 5
#: Per written answer. A question gets a paragraph, not an essay.
ANSWER_SENTENCES = 4

# Bounds on the brief. Output is what costs money here (2x the cache-miss input
# rate under thinking mode), but an unbounded transcript from a program that
# printed 10,000 lines would still dominate the input, and the explanation does
# not improve past the first screenful.
MAX_CODE_CHARS = 4000
MAX_OUTPUT_LINES = 40
#: A task with written questions gets more of its output: the answers quote it.
MAX_EVIDENCE_LINES = 80

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
    questions = list(getattr(task, "written_questions", []) or [])
    budget = SHORT_SENTENCES if questions else sentence_budget(task)
    code = code_text.strip()
    if len(code) > MAX_CODE_CHARS:
        code = code[:MAX_CODE_CHARS] + "\n# ... truncated ..."

    parts = [
        f"Task: {task.title}",
        f"\nWhat the task asked for:\n{task.statement.strip()}",
    ]
    if code:
        parts.append(f"\nThe program:\n```python\n{code}\n```")
    else:
        # NOT AN INVITATION TO RECALL RESULTS. "Answer from knowledge" alone
        # got "The Technology category typically shows..." for a question
        # about THIS lab's data -- a number-shaped guess. Say so instead.
        parts.append(
            "\nThis task has no program. Answer from knowledge of the subject. If a "
            "question asks about specific results or data that were not computed "
            "here, say that the answer comes from running the analysis rather than "
            "guessing figures."
        )

    if transcript is not None:
        limit = MAX_EVIDENCE_LINES if questions else MAX_OUTPUT_LINES
        lines = transcript.display_lines()
        if len(lines) > limit:
            lines = lines[:limit] + ["... output truncated ..."]
        parts.append("\nWhat it printed when it ran:\n" + "\n".join(lines))

    # What the report may claim about work from outside this lab -- supplied,
    # recreated here, or not provided. Without it the writer saw this run's
    # own filtering code and described it as the student's Lab 02.
    outside = writer_lines(task)
    if outside:
        parts.append(outside)

    if questions:
        numbered = "\n".join(f"{n}. {q}" for n, q in enumerate(questions, start=1))
        parts.append(
            f"\nWritten questions to answer, in this order:\n{numbered}\n"
            f"\nReply with the JSON object. Keep the note to at most {budget} "
            f"sentences and each answer to at most {ANSWER_SENTENCES}."
        )
    else:
        parts.append(
            f"\nWrite the note now, in at most {budget} "
            f"{'sentence' if budget == 1 else 'sentences'}."
        )
    return "\n".join(parts)


@dataclass
class WriteUp:
    """The text half of a task's answer: an overview and one answer per question."""

    overview: str = ""
    answers: list[dict] = field(default_factory=list)


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
#: A reply that is (or tries to be) the JSON object, even a broken one.
_LOOKS_LIKE_JSON = re.compile(r'^\s*\{|"(?:overview|answers)"\s*:')
_JSON_STRING = r'"((?:[^"\\]|\\.)*)"'


def _json_object(raw: str) -> dict | None:
    """The first JSON object in the reply, tolerating what models add.

    `strict=False` accepts a literal newline inside a string -- a model writing
    a two-paragraph answer does exactly that, and strict parsing rejects it.
    `raw_decode` stops at the object's end, so trailing chatter is ignored.
    """
    start = raw.find("{")
    if start < 0:
        return None
    try:
        data, _ = json.JSONDecoder(strict=False).raw_decode(raw[start:])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _unescape(value: str) -> str:
    try:
        return json.loads(f'"{value}"', strict=False)
    except ValueError:
        return value.replace('\\"', '"').replace("\\n", " ")


def _salvage(raw: str) -> dict:
    """What can be read out of a JSON reply that will not parse -- unescaped
    quotes, a cut-off final string. Whole strings only; a truncated one is
    dropped rather than printed half-finished."""
    data: dict = {}
    # Up to the next key first: that survives an unescaped quote INSIDE the
    # text ("the "best" one"), which a JSON-string pattern would cut short.
    found = re.search(r'"overview"\s*:\s*"(.*?)"\s*,\s*"answers"\s*:', raw, re.S) or re.search(
        r'"overview"\s*:\s*' + _JSON_STRING, raw, re.S
    )
    if found:
        data["overview"] = _unescape(found.group(1))
    listed = re.search(r'"answers"\s*:\s*\[(.*)', raw, re.S)
    if listed:
        body = listed.group(1)
        if '"answer"' in body:
            data["answers"] = [
                _unescape(s) for s in re.findall(r'"answer"\s*:\s*' + _JSON_STRING, body, re.S)
            ]
        else:
            closed = body.rfind("]")
            items = body[:closed] if closed >= 0 else body
            pieces = re.split(r'"\s*,\s*"', items.strip())
            complete = []
            for n, piece in enumerate(pieces):
                last = n == len(pieces) - 1
                text = piece.strip()
                if n == 0:
                    text = text.removeprefix('"')
                if last:
                    # A final string with no closing quote was cut off.
                    if not text.endswith('"'):
                        break
                    text = text[:-1]
                complete.append(_unescape(text))
            data["answers"] = [a for a in complete if a.strip()]
    return data


def overview_of(text: str) -> str:
    """A plain-prose reply as-is; a JSON-shaped one reduced to its overview.

    THE LEAK THIS CLOSES. A write-up whose JSON would not parse used to be kept
    whole as "the overview", so the Word report printed `{"overview": "...",
    "answers": [...]}` verbatim under the task. Text that looks like JSON is
    never prose: it is parsed, salvaged, or dropped.
    """
    raw = _FENCE.sub("", (text or "").strip())
    if not _LOOKS_LIKE_JSON.search(raw):
        return raw
    data = _json_object(raw) or _salvage(raw)
    return str(data.get("overview") or "")


def parse_writeup(text: str, questions: list[str]) -> WriteUp | None:
    """Lenient: JSON when it parses (or can be salvaged), else plain prose is
    the overview -- but a JSON-shaped reply is never printed as prose.

    A bad reply must never cost the task its prose, and it must never raise --
    the write-up is a decoration on a task that already succeeded.
    """
    raw = _FENCE.sub("", (text or "").strip())
    data = None
    if _LOOKS_LIKE_JSON.search(raw):
        data = _json_object(raw) or _salvage(raw)
        if not data:
            return None
    if not isinstance(data, dict):
        overview = clamp_sentences(plain_text(raw), LONG_SENTENCES)
        return WriteUp(overview=overview) if overview else None

    overview = clamp_sentences(plain_text(str(data.get("overview") or "")), SHORT_SENTENCES)
    replies = data.get("answers") or []
    answers = []
    for question, reply in zip(questions, replies):
        if isinstance(reply, dict):
            reply = reply.get("answer", "")
        answer = clamp_sentences(plain_text(str(reply or "")), ANSWER_SENTENCES)
        if answer:
            answers.append({"question": question, "answer": answer})
    if not overview and not answers:
        return None
    return WriteUp(overview=overview, answers=answers)


@dataclass
class Explainer:
    """Callable seam: `(task, code_text, transcript) -> str | None`.

    A callable rather than a model, for the reason `Sandbox` and
    `ScreenshotBackend` are protocols: the orchestrator should depend on the
    smallest interface that does the job. Tests pass a lambda, the web layer
    and the eval pass this, and none of them has to agree about model clients.

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
    ) -> "str | WriteUp | None":
        """A plain string for a task with no written questions (today's shape),
        a `WriteUp` for one that has them. `solve_task` accepts both."""
        questions = list(getattr(task, "written_questions", []) or [])
        if not code_text.strip() and not questions:
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
        if questions:
            return parse_writeup(text, questions)
        # Asked for plain text, a model still sometimes answers with the JSON
        # object -- the same leak as above, on the other path.
        cleaned = clamp_sentences(plain_text(overview_of(text)), sentence_budget(task))
        return cleaned or None
