"""Who is submitting this.

ASK ONLY FOR WHAT YOU CANNOT DERIVE. The manual already names the course, the
section, the date and the instructor (see ingest/cover.py). Only the student's
own name and CMS number are unknowable, and the web app collects those after the
files exist (POST /api/runs/{id}/identity), keeping them in the browser.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

# The identity fields, in display order. The only ones a manual cannot supply.
ASK_ORDER = (
    ("name", "Your full name"),
    ("cms_id", "Your CMS / roll number"),
)



@dataclass
class StudentProfile:
    """Identity for the cover page and the submission filename."""

    name: str = ""
    cms_id: str = ""
    section: str = ""
    program: str = ""

    @property
    def complete(self) -> bool:
        return bool(self.name.strip() and self.cms_id.strip())

    def missing(self) -> list[str]:
        return [key for key, _ in ASK_ORDER if not getattr(self, key).strip()]

    def slug(self) -> str:
        """Filename-safe id for the zip and notebook, e.g. 22F-1234."""
        cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", self.cms_id.strip()).strip("-")
        return cleaned or "submission"

    def as_display(self) -> dict[str, str]:
        return {k: v for k, v in asdict(self).items() if v}
