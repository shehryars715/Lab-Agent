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

    answer        a question: reply in the chat, change no files
    resolve       code changes: re-solve ONLY these tasks, as an edit
    rewrite       write-up changes: re-run only the writer for these tasks
    identity      name / ID: a rebuild, no model call
    look          how the files look (`present.LookPatch`): a rebuild
    edit_request  a look wish no field can carry: the editing agent
    unsupported   what no file can do: said back, plainly

WHY `look` AND `edit_request` (2026-10-22). The router used to have a short
closed list -- formats, four styles, five parts -- and NO cover field at all,
so "change the cover", "call it Source code" or "no task statements" came back
as "I'm not sure what to change there". A request that fits no field is not a
vague request. Now the whole report spec is one patch, and anything about the
look that the patch cannot carry has somewhere to go.

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
from labsagent.present import (
    FORMAT_NAMES,
    LookPatch,
    Presentation,
    is_empty,
    split_identity,
)
from labsagent.usage import Usage

REVISE_ATTEMPTS = 3

PROMPT = """A student has a finished lab submission open and sent a follow-up message.

Their message:
{feedback}

THE TASKS
{tasks}

HOW THE FILES LOOK NOW
{look}

Sort the message into what it actually asks for. Every field is optional, and an
EMPTY field means "change nothing". Never fill a field the message does not ask for.

- kind: "answer" if the message only asks a question or asks for an explanation in
  the chat. "change" if it asks for anything to be different in the files.
- reply: for kind "answer", your reply in the chat, 1-4 plain sentences. If you need
  a task's code or output to answer properly, leave reply empty and list those task
  ids in needs_code_for. For kind "change", one short sentence saying what you will
  change. Only when the message names nothing you could act on at all, use kind
  "answer" and ask one short clarifying question.
- resolve: tasks whose CODE or PRINTED OUTPUT must change, and the change rewritten
  as a direct, specific instruction to the programmer. List every affected id; for
  the whole lab, list all of them.
- rewrite: tasks whose WRITTEN explanation or answers must change (longer, shorter,
  simpler, more detail, answer a question differently) while the code stays as it
  is, and how the writing should change.
- identity: name, cms_id, section, program -- only values the student typed about
  themselves.
- look: how the files look. All optional; leave out what the message does not ask:
    cover:   show (true or false), layout (classic, rule, banner or split), title,
             tagline, fields -- lines on the cover by name, e.g. {{"Instructor":
             "Dr. X", "Submitted to": "Y", "Date": ""}} ("" takes a line off; a
             name the cover has no line for adds one), clear (["tagline"] removes
             the line under the title, ["title"] restores the lab's own title)
    style:   classic, walkthrough, findings or compact, for every task
    show / hide: parts of EVERY answer to add or leave out, from exactly "code",
             "output", "screenshots", "figures", "explanation", "statement"
             ("statement" is the task's own text above its answer; "figures" are
             charts the program drew; "screenshots" are pictures of the terminal)
    tasks:   [{{"task_ids": ["task2"], "show": [], "hide": [], "style": ""}}] for a
             change to particular tasks only
    labels:  {{"code": "...", "output": "", "answers": "...", "status": "..."}} --
             rename a label; "" removes it
    page_breaks: true or false -- each task on a new page
    body_pt / code_pt: text or code size in points ("bigger text" -> 12)
    formats: the COMPLETE new set of files, from {formats_list} -- only if the
             message asks for different files. "Also a PDF" means the current set
             plus pdf. "files" is one code file per task.
    file_name: what to call the files, if they asked
- edit_request: anything else about how the files LOOK that `look` cannot carry --
  colours, a named font, a table of contents, a logo, borders, alignment, moving a
  section, text they dictate to add somewhere -- as one direct instruction for the
  person editing the Word file. Never drop such a wish and never call it vague:
  this is where it goes. Changing what an answer SAYS is `rewrite`, not this.
- unsupported: what no file can do ("submit it on LMS", "email my teacher"), each
  as a short phrase with why.

Anything about how the Word file or the notebook LOOKS (headings, where text goes,
text instead of printed output) is never a code change: the files are rebuilt for
you. Code changes are about what the program does or prints.

Return ONLY a JSON object matching this schema exactly:

{schema}
"""

# THE EXAMPLE CHANGES NO LOOK. An exemplar gets copied, so a sample cover line
# here would turn up on real covers.
SCHEMA_HINT = """{
  "kind": "change",
  "reply": "I'll switch task 3 to pandas.",
  "needs_code_for": [],
  "resolve": {"task_ids": ["task3"], "instruction": "Use pandas to load and display the data."},
  "rewrite": {"task_ids": [], "instruction": ""},
  "identity": {},
  "look": {},
  "edit_request": "",
  "unsupported": []
}"""

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
    identity: dict[str, str] = Field(default_factory=dict)
    look: LookPatch | None = None
    edit_request: str | None = ""
    unsupported: list[str] | None = None


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
    identity: dict[str, str] = field(default_factory=dict)
    #: How the files should look; None or an empty patch changes nothing.
    look: LookPatch | None = None
    #: A look wish no field of `look` can carry -- for the editing agent.
    edit_request: str = ""
    #: What no file can do, said back as such.
    unsupported: list[str] = field(default_factory=list)

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
    `rewrite` is only re-solved: a re-solve rewrites its prose anyway. The
    look patch is validated where it is applied (`present.apply`), which
    ignores every value it does not know.
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
    # "My name is X" can arrive as a cover line; it rides on the profile.
    look, said = split_identity(plan.look)
    identity = {
        k: str(v).strip()
        for k, v in {**said, **(plan.identity or {})}.items()
        if k in IDENTITY_FIELDS and str(v or "").strip()
    }
    look = look if not is_empty(look) else None
    followup = FollowUp(
        kind="change" if (plan.kind or "").strip().lower() == "change" else "answer",
        reply=(plan.reply or "").strip(),
        needs_code_for=ids(plan.needs_code_for),
        resolve_ids=resolve,
        resolve_instruction=(plan.resolve.instruction or feedback).strip() if resolve else "",
        rewrite_ids=rewrite,
        rewrite_instruction=(plan.rewrite.instruction or feedback).strip() if rewrite else "",
        identity=identity,
        look=look,
        edit_request=(plan.edit_request or "").strip()[:600],
        unsupported=[
            str(u).strip()[:160] for u in (plan.unsupported or []) if str(u or "").strip()
        ][:3],
    )
    # THE FIELDS DECIDE, NOT THE LABEL. Seen live: "my name is Ayesha Abid,
    # roll number 22F-1" came back as kind "answer" WITH the identity filled
    # in -- trusting the label would have replied in the chat and dropped the
    # name. Anything the message asked to change makes it a change.
    actionable = bool(
        followup.resolve_ids or followup.rewrite_ids or followup.identity
        or followup.look or followup.edit_request
    )
    cannot = " ".join(f"I can't {u.rstrip('.')}." for u in followup.unsupported)
    if actionable:
        followup.kind = "change"
    # A "change" that names nothing to change is not a change. Say so rather
    # than silently re-solving -- the expensive default this module replaced.
    # Something no file can do is said back as such, and rebuilds nothing.
    elif followup.kind == "change":
        followup.kind = "answer"
        followup.reply = cannot or followup.reply or VAGUE_REPLY
    elif cannot and not followup.reply:
        followup.reply = cannot
    if followup.is_answer and not followup.reply and not followup.needs_code_for:
        followup.reply = VAGUE_REPLY
    return followup


def describe_look(pres: Presentation, *, title_page: bool = False, default_formats=()) -> str:
    """The report spec as the router reads it: what is true now."""
    cover = pres.cover
    lines = []
    if cover.shown(title_page):
        bits = [f"layout {cover.layout}"]
        if cover.title:
            bits.append(f'title "{cover.title}"')
        if cover.tagline:
            bits.append(f'line under the title "{cover.tagline}"')
        changed = [f"{k}: {v}" if v else f"{k}: (removed)" for k, v in cover.fields.items()]
        if changed:
            bits.append("lines changed by the student: " + "; ".join(changed))
        lines.append("Cover: " + ", ".join(bits))
    else:
        lines.append(
            "Cover: none"
            + (" (the manual has its own title page)" if title_page and cover.show is None else "")
        )
    lines.append(f"Layout of each task: {pres.style}")
    if pres.labels:
        lines.append(
            "Labels: " + "; ".join(f"{k} -> {v or '(none)'}" for k, v in pres.labels.items())
        )
    if pres.page_breaks:
        lines.append("Each task starts on a new page")
    if pres.body_pt or pres.code_pt:
        lines.append(f"Text size: {pres.body_pt or 'default'}, code size: {pres.code_pt or 'default'}")
    formats = list(pres.formats or default_formats)
    lines.append(
        "Files: " + ", ".join(f"{f} ({FORMAT_NAMES.get(f, f)})" for f in formats)
    )
    if pres.stem:
        lines.append(f"Files named: {pres.stem}")
    for edit in pres.edits:
        if edit.get("status") == "applied":
            lines.append(f"Edited by hand earlier: {edit.get('request', '')}")
    return "\n".join(lines)


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


def read_revision(feedback: str, outcomes, model, *, look: str = "") -> tuple[FollowUp, Usage]:
    """One routing call. Never raises; a failed call asks for clarification."""
    from labsagent.present import FORMATS

    structured = model.with_structured_output(FollowUpPlan, method="json_mode")
    prompt = PROMPT.format(
        feedback=feedback.strip(),
        tasks=_task_block(outcomes),
        look=look or "(the defaults)",
        formats_list=", ".join(FORMATS),
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
