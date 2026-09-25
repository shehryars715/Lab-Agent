"""Identity: extracted from the manual where possible; the rest comes from the browser."""

from __future__ import annotations

import pytest

from labsagent.ingest.cover import extract_cover_facts
from labsagent.profile import StudentProfile

FRONT_MATTER = [
    "Faculty of Computing",
    "",
    "Lab 10: Neural Networks-Part A",
    "CS245 Machine Learning",
    "BSDS – 02A",
    "8th April 2026",
    "Lab Engineer: Alishba Zulfiqar",
    "Instructor: Dr. Nazia Perwaiz",
    "Lab Tasks",
]


class TestCoverFacts:
    def test_extracts_every_field_from_real_front_matter(self):
        facts = extract_cover_facts(FRONT_MATTER)
        assert facts.instructor == "Dr. Nazia Perwaiz"
        assert facts.lab_engineer == "Alishba Zulfiqar"
        assert facts.course == "CS245 Machine Learning"
        assert facts.section == "BSDS – 02A"
        assert facts.date == "8th April 2026"
        assert facts.department == "Faculty of Computing"

    def test_absent_field_is_none_never_guessed(self):
        """The whole point: not-found must be distinguishable from found."""
        facts = extract_cover_facts(["Lab Tasks", "Task 1: do a thing"])
        assert facts.instructor is None
        assert facts.course is None
        assert facts.missing(("instructor", "course")) == ["instructor", "course"]

    def test_first_match_wins_so_prose_cannot_override_front_matter(self):
        lines = FRONT_MATTER + ["Ask your instructor: Someone Else for help"]
        assert extract_cover_facts(lines).instructor == "Dr. Nazia Perwaiz"

    def test_ignores_matter_past_the_front_limit(self):
        lines = ["filler"] * 50 + ["Instructor: Dr. Late"]
        assert extract_cover_facts(lines).instructor is None

    def test_labelled_variants(self):
        facts = extract_cover_facts(
            ["Course Instructor: Dr. A", "Teaching Assistant: B", "Date: 01/02/2026"]
        )
        assert facts.instructor == "Dr. A"
        assert facts.date == "01/02/2026"


def test_the_slug_is_filename_safe():
    assert StudentProfile(name="A", cms_id="22F 1234/x").slug() == "22F-1234-x"
    assert StudentProfile().slug() == "submission"
