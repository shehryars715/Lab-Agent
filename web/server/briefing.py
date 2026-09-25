"""Read the lab once, before working on it, and decide two things.

The old interface asked for a name and a roll number and nothing else, because
those were the only things it had been told to ask for. That is not the same as
the only things worth knowing. A lab manual routinely leaves out something the
solver genuinely cannot decide:

    "Task 3: load the dataset and report the mean."
        -> which dataset? is one provided, or should it be generated?

    "Task 2: use the appropriate library."
        -> numpy, or plain Python? the grader may care.

    "Task 4: extend your Task 2 program."
        -> covered by the orchestrator already, but the manual may assume
           something about Task 2's output format that it never states.

None of those are answerable from the document, all of them change the code,
and a solver that guesses produces something that runs and is wrong. So the
model reads the extracted tasks and proposes what it actually needs.

WHY THIS RUNS BEFORE SOLVING, NOT AFTER. The previous pause happened after the
solve, which meant every answer could only reach the cover page -- the code was
already written and the money already spent. Moving the pause to between ingest
and solve is the difference between questions that are decorative and questions
that do work.

WHY ONE CALL AND NOT TWO. The same reading produces both the questions and the
cover design (layout choice and tagline), so asking for both in one structured
call costs one round trip and about $0.0003 instead of two of each. They are
different decisions from the same context; splitting them would mean paying to
read the same manual twice.

Structured output follows `ingest/labspec.py` exactly: DeepSeek rejects a forced
tool_choice in thinking mode, so `json_mode` plus client-side Pydantic is the
only mechanism that works here -- which means the schema goes in the prompt and
a malformed response is a retry, not an exception.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

from langchain_core.callbacks import UsageMetadataCallbackHandler
from pydantic import BaseModel, Field

from labsagent.present import STYLES
from labsagent.report.cover import LAYOUTS
from labsagent.usage import Usage

# ONE, AND USUALLY NONE (2026-09-25). Every question pauses the run, and on the
# real Lab 2 run both of the two asked were about things already settled. A
# question now has to name the task it blocks -- see `_to_plan`.
MAX_QUESTIONS = 1
BRIEFING_ATTEMPTS = 3

PROMPT = """You are about to solve a university programming lab. Before you start, \
decide what you need to ask the student, and how the report's cover page should look.

THE LAB
Number: {lab_number}
Title: {title}
Course: {course}

TASKS
{tasks}

ALREADY KNOWN (never ask for these)
{known}

PART ONE -- QUESTIONS. Almost always: none.

A question stops the whole run until the student answers, so ask ONLY when a
task literally cannot be written without a fact that only the student has --
for example "use the constants from your lecture notes" with no constants
given. If you could write a reasonable program without the answer, do not ask:
decide yourself.

NEVER ask about:
  - data or datasets (handled separately, before you are asked anything)
  - which library, method or approach to use -- choose one yourself
  - output format, layout, style, plots or wording -- choose yourself
  - what to do if something is missing or fails -- there are no fallbacks
  - anything listed under ALREADY KNOWN
  - the student's name, ID, section or program, or which files to produce

Return AT MOST {max_questions}. Returning ZERO is correct and expected.

For a question you truly must ask, give:
  key      short lowercase slug, no spaces
  label    the question as shown to the student, under 60 characters
  hint     a one-line clarification, or "" if none is needed
  reason   why the task cannot be written without it, under 90 characters
  default  the value you will assume if they skip it, under 80 characters
  blocks   the task ids that cannot be written without it, e.g. ["task2"]

PART TWO -- COVER.

Pick the cover layout that suits this lab best:
  classic  centred, formal, table of details. The safe default.
  rule     left-aligned and editorial; rules instead of boxes. Good for a lab
           with a real title worth setting large.
  banner   a tinted band across the top carrying the title. Good for a lab that
           belongs to an obvious course or series.
  split    a submission sheet: what the lab is above, who submitted it below.
           Good when the student's identity is the point.

Then write a tagline: ONE short line, under 14 words, describing what this
particular lab actually does. It appears under the title. It must be specific
to this lab's contents, not a restatement of the course. Example:
"A single-layer perceptron, trained from scratch without a framework."

PART THREE -- WHAT TO PRODUCE.

Say which files this lab should come back as, in `artifacts`, from exactly this
set:
  docx   a Word report: the manual itself, annotated in place under each task
  ipynb  a Jupyter/Colab notebook with the outputs already in it
  py     a plain Python script, just the code
  md     a markdown write-up
  zip    an archive of the above

Follow the manual's own submission instructions when it gives any -- "submit
only the .ipynb on LMS" means ["ipynb"]. Follow the student's request when they
made one. When neither says anything, ["docx", "ipynb", "zip"] is the sensible
default for a lab that wants a written report.

In `artifacts_reason`, say in ONE short line why. Quote the manual if it told you.

PART FOUR -- HOW EACH TASK IS PRESENTED.

Pick the `style` that suits THIS lab's content:
  classic      code, then its output, then a short explanation. Safe default.
  walkthrough  step by step: each part of the program with its own output.
               Good for multi-step algorithms and data pipelines.
  findings     the written answers first, then the code that produced them.
               Good when the tasks are mostly analysis or discussion.
  compact      code and output with a one-line caption. Good for short
               input/output exercises.

Return ONLY a JSON object matching this schema exactly:

{schema}

Lab task descriptions:

{tasks}
"""

# THE EXAMPLE ASKS NOTHING. It used to be a data question whose default was
# "Generate a small sample dataset in the code" -- an exemplar gets copied, so
# the one example the model saw was both the question not to ask and the
# improvisation not to make.
SCHEMA_HINT = """{
  "questions": [],
  "cover": { "layout": "rule", "tagline": "Perceptron training, no framework." },
  "artifacts": ["docx", "zip"],
  "artifacts_reason": "The manual asks for a Word report.",
  "style": "walkthrough"
}"""

_SLUG = re.compile(r"[^a-z0-9_]+")


class ProposedQuestion(BaseModel):
    key: str
    label: str
    hint: str = ""
    reason: str = ""
    default: str = ""
    #: Task ids this question blocks. A question that blocks nothing is a
    #: preference, and a preference does not stop the run.
    blocks: list[str] = Field(default_factory=list)


class CoverChoice(BaseModel):
    layout: str = "classic"
    tagline: str = ""


class Briefing(BaseModel):
    questions: list[ProposedQuestion] = Field(default_factory=list)
    cover: CoverChoice = Field(default_factory=CoverChoice)
    artifacts: list[str] = Field(default_factory=list)
    artifacts_reason: str = ""
    style: str = ""


@dataclass
class Plan:
    """What the briefing produced, already validated."""

    questions: list[dict[str, Any]]
    layout: str
    tagline: str
    #: Formats the agent thinks this lab wants, shown at the pause so the
    #: student can redirect once, before any solving money is spent.
    artifacts: list[str] = dataclass_field(default_factory=list)
    artifacts_reason: str = ""
    #: How each task is laid out -- one of `labsagent.present.STYLES`.
    style: str = "classic"

    @property
    def asked(self) -> bool:
        return bool(self.questions)


def _slug(value: str, taken: set[str]) -> str:
    base = _SLUG.sub("_", (value or "").strip().lower()).strip("_") or "answer"
    if base[0].isdigit():
        base = f"q_{base}"
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}_{n}", n + 1
    taken.add(candidate)
    return candidate


def _task_block(spec) -> str:
    lines = []
    for task in spec.tasks:
        lines.append(f"[{task.id}] {task.title}")
        lines.append(f"    {task.statement.strip()[:600]}")
        if task.sample_inputs:
            lines.append(f"    sample inputs: {task.sample_inputs}")
        lines.append("")
    return "\n".join(lines)


def _known_block(facts, profile, instructions: str, data: list[str] | None = None) -> str:
    bits = [
        ("Course", facts.course),
        ("Section", facts.section),
        ("Instructor", facts.instructor),
        ("Lab engineer", facts.lab_engineer),
        ("Date", facts.date),
        ("Department", facts.department),
    ]
    lines = [f"  {label}: {value}" for label, value in bits if value]
    if profile.name:
        lines.append(f"  Student name: {profile.name}")
    if profile.cms_id:
        lines.append(f"  Student ID: {profile.cms_id}")
    if profile.section:
        lines.append(f"  Student section: {profile.section}")
    if instructions.strip():
        lines.append(f"  The student has already added these instructions: {instructions.strip()}")
    # THE POINT OF LISTING DATA HERE. "data that is referenced but not supplied
    # (which CSV?)" is the first example of a good question in the prompt above,
    # and it was a good question precisely because nothing could answer it. Now
    # something can -- so when the file is already in hand, asking for it again
    # is the formality this block exists to prevent.
    for name in data or []:
        lines.append(f"  Data, already arranged before any code is written: {name}")
    return "\n".join(lines) if lines else "  (nothing beyond the tasks below)"


def _to_plan(briefing: Briefing, taken: set[str], task_ids=None) -> Plan:
    """Validate what came back. A model's answer is a proposal, not a fact.

    The layout is checked against the real list rather than trusted: an
    invented layout name would otherwise reach `build_cover`, and while that
    falls back safely, silently accepting typos here would hide a prompt
    problem behind a working report.
    """
    questions: list[dict[str, Any]] = []
    known_ids = set(task_ids or [])
    for item in briefing.questions:
        if len(questions) >= MAX_QUESTIONS:
            break
        label = (item.label or "").strip()
        if not label:
            continue
        # A QUESTION MUST NAME WHAT IT BLOCKS. "Plain text or a table?" blocks
        # nothing -- the solver can pick -- and is dropped here, whatever the
        # prompt managed to persuade the model of.
        blocked = [t for t in item.blocks if not known_ids or t in known_ids]
        if not blocked:
            continue
        questions.append(
            {
                "key": _slug(item.key or label, taken),
                "label": label[:120],
                "hint": (item.hint or "").strip()[:200],
                "reason": (item.reason or "").strip()[:200],
                "default": (item.default or "").strip()[:160],
                "required": False,  # a guess is always allowed; see pipeline
            }
        )

    layout = (briefing.cover.layout or "").strip().lower()
    tagline = (briefing.cover.tagline or "").strip()
    known = {"docx", "ipynb", "py", "md", "zip"}
    style = (briefing.style or "").strip().lower()
    artifacts = [a.strip().lower().lstrip(".") for a in briefing.artifacts]
    return Plan(
        questions=questions,
        layout=layout if layout in LAYOUTS else "classic",
        tagline=tagline[:160],
        artifacts=[a for a in artifacts if a in known],
        artifacts_reason=(briefing.artifacts_reason or "").strip()[:160],
        style=style if style in STYLES else "classic",
    )


def read_briefing(
    spec, facts, profile, instructions: str, model, data: list[str] | None = None
) -> tuple[Plan, Usage]:
    """One structured call. Never raises -- a failed briefing costs the questions.

    `data` is the names of files the student already attached. Passed so the
    model stops asking which dataset to use when it is looking at one.
    """
    structured = model.with_structured_output(Briefing, method="json_mode")
    tasks = _task_block(spec)
    prompt = PROMPT.format(
        lab_number=spec.lab_number,
        title=spec.title,
        course=spec.course or "(not stated)",
        tasks=tasks,
        known=_known_block(facts, profile, instructions, data),
        max_questions=MAX_QUESTIONS,
        schema=SCHEMA_HINT,
    )

    handler = UsageMetadataCallbackHandler()
    taken: set[str] = set()

    for _ in range(BRIEFING_ATTEMPTS):
        try:
            briefing = structured.invoke(prompt, config={"callbacks": [handler]})
        except Exception:  # noqa: BLE001 -- retried, then degraded
            continue
        if isinstance(briefing, Briefing):
            return _to_plan(briefing, taken, [t.id for t in spec.tasks]), _usage(handler)

    # A run that cannot plan is still a run. The cover falls back to classic,
    # the tagline is empty, and no questions are asked -- all of which are the
    # behaviour of the tool before this module existed.
    return Plan(questions=[], layout="classic", tagline="", artifacts=[]), _usage(handler)


def _usage(handler) -> Usage:
    total = Usage()
    for model_name, meta in (handler.usage_metadata or {}).items():
        total.model = model_name
        total.calls += 1
        total.input_tokens += meta.get("input_tokens", 0)
        total.output_tokens += meta.get("output_tokens", 0)
        total.cached_tokens += (meta.get("input_token_details") or {}).get("cache_read", 0)
    return total
