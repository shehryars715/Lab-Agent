"""Pull cover-page facts out of the manual, for free.

THE PRINCIPLE: only ask the human for what the document cannot tell you.

A lab manual's front matter already names the course, the section, the date, the
instructor and the lab engineer -- every field of a cover page except who is
submitting it. Asking the student to retype all of that is busywork, and sending
the page to a model to "extract" it is paying for something a regex does
perfectly. So this module is deliberately free and offline: labelled lines are
matched by label, and the handful of unlabelled conventions (a course code, a
section code, a date) by shape.

Anything it cannot find comes back None, and `profile.py` asks about exactly
those. Confidence is expressed by returning nothing rather than by guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Labelled front matter: "Instructor: Dr. Nazia Perwaiz"
_LABELLED = {
    "instructor": re.compile(r"^\s*(?:course\s+)?instructor\s*[:\-–]\s*(.+)$", re.I),
    "lab_engineer": re.compile(r"^\s*(?:lab\s+)?(?:engineer|assistant|ta)\s*[:\-–]\s*(.+)$", re.I),
    "teacher": re.compile(r"^\s*teacher\s*[:\-–]\s*(.+)$", re.I),
    "course": re.compile(r"^\s*course(?:\s+title)?\s*[:\-–]\s*(.+)$", re.I),
    "section": re.compile(r"^\s*(?:section|class)\s*[:\-–]\s*(.+)$", re.I),
    "date": re.compile(r"^\s*date\s*[:\-–]\s*(.+)$", re.I),
    "department": re.compile(r"^\s*(?:department|faculty|school)\s*[:\-–]\s*(.+)$", re.I),
}

# Unlabelled conventions, matched by shape rather than by name.
_COURSE_CODE = re.compile(r"^\s*([A-Z]{2,4}\s?-?\s?\d{3,4})\b\s*(.*)$")
_SECTION_CODE = re.compile(r"^\s*([A-Z]{2,6}\s*[–\-]\s*\d{1,2}\s*[A-Z]?)\s*$")
_DATE_LINE = re.compile(
    r"^\s*(\d{1,2}(?:st|nd|rd|th)?\s+[A-Z][a-z]+\s+\d{4}"
    r"|[A-Z][a-z]+\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*$"
)
_FACULTY = re.compile(r"^\s*(faculty|department|school)\s+of\s+.+$", re.I)

# How far into the document front matter can plausibly reach.
FRONT_MATTER_LINES = 40


@dataclass
class CoverFacts:
    """What the manual itself knows. None means 'not found', never 'guessed'."""

    course: str | None = None
    section: str | None = None
    date: str | None = None
    instructor: str | None = None
    lab_engineer: str | None = None
    department: str | None = None
    sources: dict[str, str] = field(default_factory=dict)

    def missing(self, wanted: tuple[str, ...]) -> list[str]:
        return [name for name in wanted if not getattr(self, name, None)]


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t.:;,-–")


def extract_cover_facts(lines: list[str], limit: int = FRONT_MATTER_LINES) -> CoverFacts:
    """Read front matter. `lines` is the flattened paragraph text of the manual.

    First match wins for each field: front matter reads top-down, and a later
    mention of "instructor" is far more likely to be prose inside a task than a
    second, better answer.
    """
    facts = CoverFacts()

    for raw in lines[:limit]:
        text = _clean(raw)
        if not text:
            continue

        for field_name, pattern in _LABELLED.items():
            match = pattern.match(text)
            if not match:
                continue
            target = "instructor" if field_name == "teacher" else field_name
            if getattr(facts, target, None) is None and hasattr(facts, target):
                setattr(facts, target, _clean(match.group(1)))
                facts.sources[target] = text
            break
        else:
            # Unlabelled shapes, only if the field is still empty.
            if facts.course is None:
                match = _COURSE_CODE.match(text)
                if match and len(text) < 80:
                    code, rest = _clean(match.group(1)), _clean(match.group(2))
                    facts.course = f"{code} {rest}".strip()
                    facts.sources["course"] = text
                    continue
            if facts.section is None and _SECTION_CODE.match(text):
                facts.section = _clean(text)
                facts.sources["section"] = text
                continue
            if facts.date is None and _DATE_LINE.match(text):
                facts.date = _clean(text)
                facts.sources["date"] = text
                continue
            if facts.department is None and _FACULTY.match(text):
                facts.department = _clean(text)
                facts.sources["department"] = text
                continue

    return facts
