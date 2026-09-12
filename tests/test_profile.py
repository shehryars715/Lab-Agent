"""Identity: extracted where possible, asked only where necessary, cached always."""

from __future__ import annotations

import pytest

from labsagent.ingest.cover import extract_cover_facts
from labsagent.profile import (
    StudentProfile,
    config_path,
    load_profile,
    resolve_profile,
    save_profile,
)

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


class TestProfileStore:
    def test_missing_file_is_an_empty_profile_not_an_error(self, tmp_path):
        assert load_profile(tmp_path) == StudentProfile()

    def test_roundtrip(self, tmp_path):
        original = StudentProfile(name="Shehryar", cms_id="22F-1234", section="BSDS-02A")
        save_profile(original, tmp_path)
        assert load_profile(tmp_path) == original

    def test_malformed_toml_degrades_to_empty(self, tmp_path):
        config_path(tmp_path).write_text("this is not [ valid toml", encoding="utf-8")
        assert load_profile(tmp_path) == StudentProfile()

    def test_save_preserves_other_tables(self, tmp_path):
        config_path(tmp_path).write_text(
            '[model]\nname = "deepseek-flash"\n\n[student]\nname = "Old"\n',
            encoding="utf-8",
        )
        save_profile(StudentProfile(name="New", cms_id="22F-1"), tmp_path)
        text = config_path(tmp_path).read_text(encoding="utf-8")
        assert "[model]" in text and 'name = "deepseek-flash"' in text
        assert load_profile(tmp_path).name == "New"

    def test_legacy_roll_no_key_is_still_read(self, tmp_path):
        config_path(tmp_path).write_text(
            '[student]\nname = "S"\nroll_no = "21F-9999"\n', encoding="utf-8"
        )
        assert load_profile(tmp_path).cms_id == "21F-9999"

    def test_quotes_in_a_name_survive(self, tmp_path):
        save_profile(StudentProfile(name='A "Nick" B', cms_id="22F-1"), tmp_path)
        assert load_profile(tmp_path).name == 'A "Nick" B'

    @pytest.mark.parametrize(
        "cms,expected",
        [("22F-1234", "22F-1234"), ("22F 1234", "22F-1234"), ("", "submission"),
         ("../../etc/passwd", "etc-passwd")],
    )
    def test_slug_is_filename_safe(self, cms, expected):
        assert StudentProfile(cms_id=cms).slug() == expected


class TestResolve:
    def test_complete_profile_asks_nothing(self, tmp_path, monkeypatch):
        save_profile(StudentProfile(name="S", cms_id="22F-1"), tmp_path)
        monkeypatch.setattr(
            "builtins.input", lambda *a: pytest.fail("should not have prompted")
        )
        profile, asked = resolve_profile(tmp_path, interactive=True)
        assert asked is False and profile.name == "S"

    def test_non_interactive_never_blocks(self, tmp_path, monkeypatch):
        """A prompt in a pipe is an outage, not a question."""
        monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("blocked on input"))
        profile, asked = resolve_profile(tmp_path, interactive=False)
        assert asked is False
        assert profile.name and profile.cms_id  # placeholders, not empties

    def test_asks_only_for_what_is_missing(self, tmp_path, monkeypatch):
        save_profile(StudentProfile(name="Shehryar"), tmp_path)
        asked_labels = []

        def fake_input(prompt=""):
            asked_labels.append(prompt)
            return "22F-1234"

        monkeypatch.setattr("builtins.input", fake_input)
        profile, asked = resolve_profile(tmp_path, interactive=True)
        assert len(asked_labels) == 1
        assert "CMS" in asked_labels[0]
        assert profile.name == "Shehryar" and profile.cms_id == "22F-1234"
        assert asked is True

    def test_answers_are_persisted_so_the_next_run_is_silent(self, tmp_path, monkeypatch):
        answers = iter(["Shehryar", "22F-1234"])
        monkeypatch.setattr("builtins.input", lambda *a: next(answers))
        resolve_profile(tmp_path, interactive=True)
        assert load_profile(tmp_path) == StudentProfile(name="Shehryar", cms_id="22F-1234")

    def test_overrides_win_over_the_cached_file(self, tmp_path):
        save_profile(StudentProfile(name="Old", cms_id="00F-0"), tmp_path)
        profile, asked = resolve_profile(
            tmp_path, interactive=False, overrides={"name": "New"}
        )
        assert profile.name == "New" and asked is False

    def test_eof_during_prompt_does_not_crash(self, tmp_path, monkeypatch):
        def boom(*_a):
            raise EOFError

        monkeypatch.setattr("builtins.input", boom)
        profile, _ = resolve_profile(tmp_path, interactive=True)
        assert profile.name == ""  # empty, but the run survives
