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

from labsagent.report.cover import LAYOUTS
from labsagent.usage import Usage

MAX_QUESTIONS = 3
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

PART ONE -- QUESTIONS.

Ask ONLY about things that would change the code you write, and that you cannot
work out from the task text or choose sensibly yourself.

Good questions are about genuine forks in the road:
  - data that is referenced but not supplied ("which CSV?")
  - a choice the grader may care about ("numpy or plain Python?")
  - an output format the task leaves open ("plain text or a table?")
  - a value only the student knows ("your student ID for the header?")

Bad questions -- do NOT ask these:
  - anything listed under ALREADY KNOWN
  - restating the task ("shall I print the sum?")
  - preferences with an obvious default ("what filename?")
  - anything you can decide yourself by reading the tasks

Return AT MOST {max_questions}. Returning ZERO is correct and common: most labs
are fully specified, and inventing a question to look useful wastes the
student's time.

For each question give:
  key      short lowercase slug, no spaces, e.g. "dataset_source"
  label    the question as shown to the student, under 60 characters
  hint     a one-line clarification, or "" if none is needed
  reason   why the answer changes what you write, under 90 characters

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

In `artifacts_reason`, say in ONE short line why -- the student sees it and can
redirect you before you start. Quote the manual if it told you.

Return ONLY a JSON object matching this schema exactly:

{schema}

Lab task descriptions:

{tasks}
"""

SCHEMA_HINT = """{
  "questions": [
    {
      "key": "dataset_source",
      "label": "Which dataset should task 3 use?",
      "hint": "The manual says 'the dataset' but does not attach one.",
      "reason": "Determines whether I generate data or read a file."
    }
  ],
  "cover": { "layout": "rule", "tagline": "Perceptron training, no framework." },
  "artifacts": ["docx", "zip"],
  "artifacts_reason": "The manual asks for a Word report."
}"""

_SLUG = re.compile(r"[^a-z0-9_]+")


class ProposedQuestion(BaseModel):
    key: str
    label: str
    hint: str = ""
    reason: str = ""


class CoverChoice(BaseModel):
    layout: str = "classic"
    tagline: str = ""


class Briefing(BaseModel):
    questions: list[ProposedQuestion] = Field(default_factory=list)
    cover: CoverChoice = Field(default_factory=CoverChoice)
    artifacts: list[str] = Field(default_factory=list)
    artifacts_reason: str = ""


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


def _known_block(facts, profile, instructions: str) -> str:
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
    return "\n".join(lines) if lines else "  (nothing beyond the tasks below)"


def _to_plan(briefing: Briefing, taken: set[str]) -> Plan:
    """Validate what came back. A model's answer is a proposal, not a fact.

    The layout is checked against the real list rather than trusted: an
    invented layout name would otherwise reach `build_cover`, and while that
    falls back safely, silently accepting typos here would hide a prompt
    problem behind a working report.
    """
    questions: list[dict[str, Any]] = []
    for item in briefing.questions[:MAX_QUESTIONS]:
        label = (item.label or "").strip()
        if not label:
            continue
        questions.append(
            {
                "key": _slug(item.key or label, taken),
                "label": label[:120],
                "hint": (item.hint or "").strip()[:200],
                "reason": (item.reason or "").strip()[:200],
                "required": False,  # a guess is always allowed; see pipeline
            }
        )

    layout = (briefing.cover.layout or "").strip().lower()
    tagline = (briefing.cover.tagline or "").strip()
    known = {"docx", "ipynb", "py", "md", "zip"}
    artifacts = [a.strip().lower().lstrip(".") for a in briefing.artifacts]
    return Plan(
        questions=questions,
        layout=layout if layout in LAYOUTS else "classic",
        tagline=tagline[:160],
        artifacts=[a for a in artifacts if a in known],
        artifacts_reason=(briefing.artifacts_reason or "").strip()[:160],
    )


def read_briefing(spec, facts, profile, instructions: str, model) -> tuple[Plan, Usage]:
    """One structured call. Never raises -- a failed briefing costs the questions."""
    structured = model.with_structured_output(Briefing, method="json_mode")
    tasks = _task_block(spec)
    prompt = PROMPT.format(
        lab_number=spec.lab_number,
        title=spec.title,
        course=spec.course or "(not stated)",
        tasks=tasks,
        known=_known_block(facts, profile, instructions),
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
            return _to_plan(briefing, taken), _usage(handler)

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
