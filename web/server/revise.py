"""Turn "redo task 3 with pandas" into a partial re-solve.

`runstore.py` already anticipated this. Its docstring for `completed_task_ids`
says: "Delete its outcome from the manifest to force a retry." That is the whole
mechanism. Dropping a task's outcome and calling `run_lab(..., resume=True)`
re-solves exactly that task, keeps the others' results, and appends the new
outcome in its place -- no special resume path, because resumability is a
property of the recorded state rather than of the code.

Two things have to happen before that call, and both are easy to miss:

1. THE MANIFEST'S SPEC MUST BE REWRITTEN. When `resume=True`, `run_lab` reads
   the task list out of `manifest.spec` and ignores the spec argument. So new
   feedback passed as an argument would be silently discarded -- the run would
   redo the task with the OLD instructions and look like the feedback had no
   effect. The spec is replaced in the manifest first, then the resume reads it.

2. THE TARGETS MUST BE CHOSEN, NOT GUESSED. Re-solving every task because one
   needs changing costs a full lab (~$0.0016 and two minutes) for no reason.
   The model picks which tasks the feedback actually concerns.

If the model cannot decide, the fallback is to redo everything with the raw
feedback. That is the expensive answer rather than the wrong one, which is the
right way round: a slow correct result beats a fast one that ignored you.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.callbacks import UsageMetadataCallbackHandler
from pydantic import BaseModel

from labsagent.usage import Usage

REVISE_ATTEMPTS = 3

PROMPT = """A student has looked at a completed lab submission and asked for a change.

Their feedback:
{feedback}

THE TASKS AS THEY WERE SOLVED
{tasks}

Decide two things.

1. task_ids -- which tasks the feedback actually concerns, as a JSON list of ids
   from the list above (e.g. ["task3"]). Only include a task if the change would
   alter its code or its output. If the feedback is about the lab as a whole, or
   you are unsure, return an empty list, which means redo everything.

2. instruction -- the feedback rewritten as a direct instruction to the solver,
   self-contained and specific. Keep the student's intent and any names they
   used. Do not add requirements they did not ask for.

Return ONLY a JSON object matching this schema exactly:

{schema}
"""

SCHEMA_HINT = """{
  "task_ids": ["task3"],
  "instruction": "Use pandas to load and display the data instead of plain lists."
}"""


class RevisionPlan(BaseModel):
    task_ids: list[str] = []
    instruction: str = ""


@dataclass
class Revision:
    task_ids: list[str] = field(default_factory=list)
    instruction: str = ""

    @property
    def everything(self) -> bool:
        return not self.task_ids


def _task_block(outcomes) -> str:
    lines = []
    for outcome in outcomes:
        lines.append(
            f"[{outcome.task.id}] {outcome.task.title}  "
            f"({outcome.status}, {outcome.attempts} attempt(s))"
        )
        if outcome.error:
            lines.append(f"    last error: {outcome.error[:200]}")
    return "\n".join(lines) or "(no tasks recorded)"


def read_revision(feedback: str, outcomes, model) -> tuple[Revision, Usage]:
    """Which tasks to redo, and how to phrase it. Never raises."""
    structured = model.with_structured_output(RevisionPlan, method="json_mode")
    prompt = PROMPT.format(
        feedback=feedback.strip(), tasks=_task_block(outcomes), schema=SCHEMA_HINT
    )
    handler = UsageMetadataCallbackHandler()

    valid = {o.task.id for o in outcomes}
    for _ in range(REVISE_ATTEMPTS):
        try:
            plan = structured.invoke(prompt, config={"callbacks": [handler]})
        except Exception:  # noqa: BLE001 -- retried, then degraded
            continue
        if not isinstance(plan, RevisionPlan):
            continue
        # A hallucinated id would drop no outcome, `pending_tasks` would return
        # nothing, and the run would report success having changed nothing at
        # all -- the worst outcome, because it looks like it worked.
        targets = [t for t in plan.task_ids if t in valid]
        return (
            Revision(task_ids=targets, instruction=(plan.instruction or feedback).strip()),
            _usage(handler),
        )

    return Revision(task_ids=[], instruction=feedback.strip()), _usage(handler)


def _usage(handler) -> Usage:
    total = Usage()
    for model_name, meta in (handler.usage_metadata or {}).items():
        total.model = model_name
        total.calls += 1
        total.input_tokens += meta.get("input_tokens", 0)
        total.output_tokens += meta.get("output_tokens", 0)
        total.cached_tokens += (meta.get("input_token_details") or {}).get("cache_read", 0)
    return total
