"""Inputs a task relies on that are not in the lab document: a previous lab.

THE FAILURE THIS EXISTS FOR. Lab 03 builds on Lab 02: "restate your Lab 02
filtering decisions", "compare with the nearest customers from Lab 02 Task 4/5".
Lab 02 was never provided, and nothing in the harness could represent that. The
solver recomputed a stand-in and flagged it afterwards; the writer, seeing only
the stand-in's code, described it as the student's own Lab 02 ("Lab 02 filtered
the raw transactions by..."). A fabricated recap, presented as the student's
work, discovered after the money was spent.

THE RULE (the data question, generalised): every input a task relies on is
accounted for after reading and before solving -- present, or the STUDENT
decides. Ingest names what is missing (quoting the manual); the pipeline asks;
the student picks, per missing piece: give it, leave those parts out, or
recreate it (only when that is possible). No answer means the run stops.

ONE FACT, THREE RENDERINGS. A resolved prerequisite rides on the `Task`, so a
resume or a revision sees the same decision. Three consumers read it, each in
its own words -- the same idea as the block IR:

  solver_lines   what the solver may and may not do (orchestrator prompt)
  writer_lines   what the report prose may claim (explainer brief)
  report_notes   a deterministic line in the deliverable, never model-written
"""

from __future__ import annotations

from dataclasses import dataclass, replace

PROVIDED = "provided"
OMITTED = "omitted"
RECREATED = "recreated"
RESOLUTIONS = (PROVIDED, OMITTED, RECREATED)

#: Answer keys the pipeline consumes structurally, never as prose notes.
KEY_PREFIX = "prereq_"


@dataclass(frozen=True)
class Needed:
    """A missing input, as ingest found it, before the student has decided."""

    what: str
    detail: str = ""
    task_ids: tuple[str, ...] = ()
    recreatable: bool = False
    #: How it could be rebuilt, e.g. "the Online Retail data, as the manual allows".
    recreate_from: str = ""


@dataclass(frozen=True)
class Prerequisite:
    """A missing input and what the student decided to do about it."""

    what: str
    detail: str = ""
    resolution: str = OMITTED
    #: The student's own text, when they gave it.
    value: str = ""
    #: What it was recreated from, when recreated.
    source: str = ""

    def as_dict(self) -> dict:
        return {
            "what": self.what,
            "detail": self.detail,
            "resolution": self.resolution,
            "value": self.value,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Prerequisite":
        resolution = str(data.get("resolution") or OMITTED)
        return cls(
            what=str(data.get("what") or ""),
            detail=str(data.get("detail") or ""),
            # An unknown value degrades to the conservative reading: not there.
            resolution=resolution if resolution in RESOLUTIONS else OMITTED,
            value=str(data.get("value") or ""),
            source=str(data.get("source") or ""),
        )


def _name(p: Prerequisite) -> str:
    return f"{p.what} ({p.detail})" if p.detail else p.what


def solver_lines(task) -> str:
    """The solver's view. "" when there is nothing to say -- the common case.

    Placed BEFORE the student's instruction in the task prompt, so a later
    instruction or revision ("here are my Lab 02 values") outranks it.

    RECREATE IS WORDED AS THE STUDENT'S INSTRUCTION ON PURPOSE: the system
    prompt forbids building substitutes except when "a student instruction
    explicitly asks for a workaround", and this is exactly that.
    """
    items = list(getattr(task, "prerequisites", None) or [])
    if not items:
        return ""
    lines = ["\nThis task relies on work from outside this lab:"]
    for p in items:
        if p.resolution == PROVIDED:
            lines.append(
                f"- {_name(p)}: the student supplied it. Use exactly this and do not "
                f"recompute it:\n{p.value.strip()}"
            )
        elif p.resolution == RECREATED:
            lines.append(
                f"- {_name(p)}: the student does not have it and has explicitly asked "
                f"you to recreate it"
                + (f" from {p.source}" if p.source else " from what you have")
                + ". Do that as part of this program."
            )
        else:
            lines.append(
                f"- {_name(p)}: not available, and the student chose to leave out the "
                "parts that need it. Do not reconstruct, assume or recompute it. Do "
                "every other part of the task. The report already says what was left "
                "out, so do not mention it in `missing`."
            )
    return "\n".join(lines)


def writer_lines(task) -> str:
    """The report writer's view. Kept free of retry vocabulary: the brief is
    checked for words like attempt, retry and failed, and this is part of it."""
    items = list(getattr(task, "prerequisites", None) or [])
    if not items:
        return ""
    lines = ["\nAbout work from outside this lab:"]
    for p in items:
        if p.resolution == PROVIDED:
            lines.append(
                f"- {_name(p)}: the student supplied this, and you may state it: "
                f"{p.value.strip()[:1200]}"
            )
        elif p.resolution == RECREATED:
            lines.append(
                f"- {_name(p)}: recreated in this run"
                + (f" from {p.source}" if p.source else "")
                + ", NOT the student's original. Say so wherever you refer to it, and "
                "never present it as the student's earlier work."
            )
        else:
            lines.append(
                f"- {_name(p)}: not provided. Where a question needs it, say it was not "
                "provided. Never describe or invent it."
            )
    return "\n".join(lines)


def report_notes(outcome) -> list[str]:
    """Deterministic lines for the deliverable. Empty when there is nothing to
    say, so every report built without prerequisites is byte-identical."""
    task = getattr(outcome, "task", None)
    notes: list[str] = []
    for p in list(getattr(task, "prerequisites", None) or []):
        if p.resolution == RECREATED:
            notes.append(
                f"Recreated for this report: {_name(p)}"
                + (f", rebuilt here from {p.source}" if p.source else "")
                + ". This is not the original."
            )
        elif p.resolution == OMITTED:
            notes.append(
                f"Left out: the parts of this task that need {_name(p)}, which was not "
                "provided."
            )
    return notes


def has_omitted(task) -> bool:
    return any(p.resolution == OMITTED for p in (getattr(task, "prerequisites", None) or []))


def attach(spec, needed: list[Needed], decisions: dict[int, tuple[str, str]]):
    """The spec with each affected task carrying its resolved prerequisites.

    `decisions` maps the index into `needed` to (resolution, value). `replace`,
    not mutation: the spec is a frozen record that goes into the manifest.
    """
    per_task: dict[str, list[Prerequisite]] = {}
    for index, item in enumerate(needed):
        resolution, value = decisions.get(index, (OMITTED, ""))
        resolved = Prerequisite(
            what=item.what,
            detail=item.detail,
            resolution=resolution if resolution in RESOLUTIONS else OMITTED,
            value=value if resolution == PROVIDED else "",
            source=item.recreate_from if resolution == RECREATED else "",
        )
        for task_id in item.task_ids:
            per_task.setdefault(task_id, []).append(resolved)
    if not per_task:
        return spec
    return replace(
        spec,
        tasks=[
            replace(t, prerequisites=[*t.prerequisites, *per_task[t.id]])
            if t.id in per_task
            else t
            for t in spec.tasks
        ],
    )
