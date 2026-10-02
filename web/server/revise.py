"""Route a follow-up to the cheapest thing that satisfies it.

WHY THIS WAS REWRITTEN (2026-09-24). The old router answered one question --
"which tasks should be re-solved?" -- so EVERY follow-up was a re-solve. Asking
"why does task 3 only reach 75%?", "make the explanations shorter", "also give
me a .py" or "put my name on it" all paid for the solver loop, and "unsure"
meant redo everything. One real follow-up re-solved all five tasks of a lab --
45 calls, 241,619 input tokens -- and cost more than the lab itself.

It also had a single output channel, instruction -> solver. So feedback about
the DOCUMENT ("write the explanations as text in Word and in the notebook")
reached the solver as a coding instruction, and it wrote Python that generated
a .docx. The same "one instruction, one channel" bug the ingest path had fixed.

Now one call sorts the feedback into channels, and every empty field means
"change nothing" (the repo's own rule -- the old `[]` meant "redo everything"):

    answer      a question: reply in the chat, change no files
    resolve     code changes: re-solve ONLY these tasks, as an edit
    rewrite     write-up changes: re-run only the writer for these tasks
    artifacts   a new set of formats: re-emit, no model call
    style       a new layout: re-emit, no model call
    identity    name / ID for the cover: re-emit, no model call

The general pattern is ROUTING: classify with the cheapest call that can, then
dispatch to the least expensive handler that is sufficient. Most follow-ups
never reach the solver at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from langchain_core.callbacks import UsageMetadataCallbackHandler
from pydantic import BaseModel, Field

from labsagent.prerequisites import writer_lines
from labsagent.usage import Usage

REVISE_ATTEMPTS = 3

PROMPT = """A student has a finished lab submission open and sent a follow-up message.

Their message:
{feedback}

THE TASKS
{tasks}

The files currently produced: {formats}
The current layout style: {style}

Sort the message into what it actually asks for. Every field is optional, and an
EMPTY field means "change nothing". Never fill a field the message does not ask for.

- kind: "answer" if the message only asks a question or asks for an explanation in
  the chat. "change" if it asks for anything to be different in the files.
- reply: for kind "answer", your reply in the chat, 1-4 plain sentences. If you need
  a task's code or output to answer properly, leave reply empty and list those task
  ids in needs_code_for. For kind "change", one short sentence saying what you will
  change. If the message is too vague to act on, use kind "answer" and ask one
  short clarifying question.
- resolve: tasks whose CODE or PRINTED OUTPUT must change, and the change rewritten
  as a direct, specific instruction to the programmer. List every affected id; for
  the whole lab, list all of them.
- rewrite: tasks whose WRITTEN explanation or answers must change (longer, shorter,
  simpler, more detail, answer a question differently) while the code stays as it
  is, and how the writing should change.
- artifacts: the COMPLETE new set of files to produce, from docx, ipynb, py, md,
  zip -- only if the message asks for different files. "Also a .py" means the
  current set plus py.
- style: one of classic, walkthrough, findings, compact -- only if the message asks
  for a different layout.
- identity: name, cms_id, section, program -- only values the student typed.
- show / hide: parts of every answer to ADD or LEAVE OUT, from exactly "code",
  "output", "screenshots", "figures", "explanation". "Add screenshots" ->
  show ["screenshots"]; "remove the explanations" -> hide ["explanation"].

Anything about how the Word file or the notebook LOOKS (headings, where text goes,
text instead of printed output) is never a code change: the files are rebuilt for
you. Code changes are about what the program does or prints.

Return ONLY a JSON object matching this schema exactly:

{schema}
"""

SCHEMA_HINT = """{
  "kind": "change",
  "reply": "I'll switch task 3 to pandas.",
  "needs_code_for": [],
  "resolve": {"task_ids": ["task3"], "instruction": "Use pandas to load and display the data."},
  "rewrite": {"task_ids": [], "instruction": ""},
  "artifacts": [],
  "style": "",
  "identity": {},
  "show": [],
  "hide": []
}"""

FORMATS = ("docx", "ipynb", "py", "md", "zip")
STYLES = ("classic", "walkthrough", "findings", "compact")
PARTS = ("code", "output", "screenshots", "figures", "explanation")
IDENTITY_FIELDS = ("name", "cms_id", "section", "program")

VAGUE_REPLY = (
    "I'm not sure what to change there. Which task do you mean, and what should be "
    "different?"
)


class TaskChange(BaseModel):
    task_ids: list[str] = Field(default_factory=list)
    instruction: str = ""


class FollowUpPlan(BaseModel):
    kind: str = "answer"
    reply: str = ""
    needs_code_for: list[str] = Field(default_factory=list)
    resolve: TaskChange = Field(default_factory=TaskChange)
    rewrite: TaskChange = Field(default_factory=TaskChange)
    artifacts: list[str] = Field(default_factory=list)
    style: str = ""
    identity: dict[str, str] = Field(default_factory=dict)
    show: list[str] = Field(default_factory=list)
    hide: list[str] = Field(default_factory=list)


@dataclass
class FollowUp:
    """A validated routing decision. Every empty field changes nothing."""

    kind: str = "answer"
    reply: str = ""
    needs_code_for: list[str] = field(default_factory=list)
    resolve_ids: list[str] = field(default_factory=list)
    resolve_instruction: str = ""
    rewrite_ids: list[str] = field(default_factory=list)
    rewrite_instruction: str = ""
    artifacts: list[str] = field(default_factory=list)
    style: str = ""
    identity: dict[str, str] = field(default_factory=dict)
    show: list[str] = field(default_factory=list)
    hide: list[str] = field(default_factory=list)

    @property
    def is_answer(self) -> bool:
        return self.kind == "answer"

    @property
    def model_work(self) -> bool:
        """Does satisfying this need the solver or the writer?"""
        return bool(self.resolve_ids or self.rewrite_ids)


def to_followup(plan: FollowUpPlan, valid_ids, feedback: str = "") -> FollowUp:
    """Validate what came back. A model's answer is a proposal, not a fact.

    Unknown ids are dropped (a hallucinated id used to drop no outcome and then
    report success having changed nothing). A task in both `resolve` and
    `rewrite` is only re-solved: a re-solve rewrites its prose anyway.
    """
    valid = list(valid_ids)
    known = set(valid)

    def ids(values) -> list[str]:
        out: list[str] = []
        for value in values or []:
            value = str(value).strip()
            if value in known and value not in out:
                out.append(value)
        return out

    resolve = ids(plan.resolve.task_ids)
    rewrite = [t for t in ids(plan.rewrite.task_ids) if t not in resolve]
    artifacts = []
    for name in plan.artifacts or []:
        name = str(name).strip().lower().lstrip(".")
        if name in FORMATS and name not in artifacts:
            artifacts.append(name)
    style = (plan.style or "").strip().lower()
    identity = {
        k: str(v).strip()
        for k, v in (plan.identity or {}).items()
        if k in IDENTITY_FIELDS and str(v or "").strip()
    }
    followup = FollowUp(
        kind="change" if (plan.kind or "").strip().lower() == "change" else "answer",
        reply=(plan.reply or "").strip(),
        needs_code_for=ids(plan.needs_code_for),
        resolve_ids=resolve,
        resolve_instruction=(plan.resolve.instruction or feedback).strip() if resolve else "",
        rewrite_ids=rewrite,
        rewrite_instruction=(plan.rewrite.instruction or feedback).strip() if rewrite else "",
        artifacts=artifacts,
        style=style if style in STYLES else "",
        identity=identity,
        show=[p for p in (plan.show or []) if p in PARTS],
        hide=[p for p in (plan.hide or []) if p in PARTS],
    )
    # A "change" that names nothing to change is not a change. Say so rather
    # than silently re-solving -- the expensive default this module replaced.
    if followup.kind == "change" and not (
        followup.resolve_ids or followup.rewrite_ids or followup.artifacts
        or followup.style or followup.identity or followup.show or followup.hide
    ):
        followup.kind = "answer"
        followup.reply = followup.reply or VAGUE_REPLY
    if followup.is_answer and not followup.reply and not followup.needs_code_for:
        followup.reply = VAGUE_REPLY
    return followup


def _first_sentence(text: str | None) -> str:
    text = (text or "").strip()
    match = re.search(r"(?<=[.!?])\s", text)
    return text[: match.start()] if match else text[:200]


def _task_block(outcomes) -> str:
    lines = []
    for outcome in outcomes:
        task = outcome.task
        line = f"[{task.id}] {task.title} ({outcome.status})"
        summary = _first_sentence(outcome.explanation)
        if summary:
            line += f" -- {summary}"
        lines.append(line)
        for question in getattr(task, "written_questions", []) or []:
            lines.append(f"    written question: {question}")
    return "\n".join(lines) or "(no tasks recorded)"


def read_revision(
    feedback: str, outcomes, model, *, formats=(), style: str = "classic"
) -> tuple[FollowUp, Usage]:
    """One routing call. Never raises; a failed call asks for clarification."""
    structured = model.with_structured_output(FollowUpPlan, method="json_mode")
    prompt = PROMPT.format(
        feedback=feedback.strip(),
        tasks=_task_block(outcomes),
        formats=", ".join(formats) or "(default)",
        style=style or "classic",
        schema=SCHEMA_HINT,
    )
    handler = UsageMetadataCallbackHandler()
    valid = [o.task.id for o in outcomes]
    for _ in range(REVISE_ATTEMPTS):
        try:
            plan = structured.invoke(prompt, config={"callbacks": [handler]})
        except Exception:  # noqa: BLE001 -- retried, then degraded
            continue
        if isinstance(plan, FollowUpPlan):
            return to_followup(plan, valid, feedback), _usage(handler)
    return FollowUp(kind="answer", reply=VAGUE_REPLY), _usage(handler)


ANSWER_PROMPT = """You answer a student's question about their own solved lab, in a chat.

Use only the task, the program and the output below. Reply in 1-5 plain sentences:
no markdown, no code blocks. Quote real numbers from the output where they help.
If the question cannot be answered from this, say what is missing."""

MAX_CODE_CHARS = 3000
MAX_OUTPUT_LINES = 40


def answer_question(feedback: str, outcomes, model) -> tuple[str, Usage]:
    """ONE call, seeded with only the tasks the question is about."""
    parts = [f"The student's question: {feedback.strip()}"]
    for outcome in outcomes:
        task = outcome.task
        parts.append(f"\n[{task.id}] {task.title}\nTask: {task.statement.strip()[:1200]}")
        if outcome.code_text:
            parts.append("Program:\n" + outcome.code_text[:MAX_CODE_CHARS])
        if outcome.transcript is not None:
            parts.append("Output:\n" + "\n".join(outcome.transcript.lines[:MAX_OUTPUT_LINES]))
        if outcome.explanation:
            parts.append(f"Write-up: {outcome.explanation}")
        # Same facts the report writer gets about work from outside the lab,
        # or a chat answer invents the student's Lab 02 all over again.
        outside = writer_lines(task)
        if outside:
            parts.append(outside.strip())
    handler = UsageMetadataCallbackHandler()
    try:
        reply = model.invoke(
            [
                {"role": "system", "content": ANSWER_PROMPT},
                {"role": "user", "content": "\n".join(parts)},
            ],
            config={"callbacks": [handler]},
        )
        text = reply.content if isinstance(reply.content, str) else str(reply.content)
    except Exception:  # noqa: BLE001 -- a chat reply must never crash the job
        text = ""
    text = re.sub(r"(\*\*|__|`{1,3})", "", text or "").strip()
    return text or VAGUE_REPLY, _usage(handler)


def _usage(handler) -> Usage:
    total = Usage()
    for model_name, meta in (handler.usage_metadata or {}).items():
        total.model = model_name
        total.calls += 1
        total.input_tokens += meta.get("input_tokens", 0)
        total.output_tokens += meta.get("output_tokens", 0)
        total.cached_tokens += (meta.get("input_token_details") or {}).get("cache_read", 0)
    return total
