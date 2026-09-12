"""Who is submitting this, asked once and remembered.

THREE IDEAS WORTH TAKING AWAY:

1. ASK ONLY FOR WHAT YOU CANNOT DERIVE. The manual already names the course, the
   section, the date and the instructor (see ingest/cover.py). Prompting for
   those would be busywork that makes the tool feel worse the more it is used.
   Only the student's own name and CMS are genuinely unknowable, so only those
   are asked -- and only the first time.

2. A PROMPT IS A SIDE EFFECT, SO IT NEEDS A NON-INTERACTIVE PATH. Code that
   calls input() unconditionally deadlocks the moment it runs in CI, in a
   subprocess, or behind a web request -- and the failure looks like a hang, not
   an error. `interactive` is decided by asking stdin whether it is a terminal,
   and the non-interactive path degrades to placeholders instead of blocking.

3. CONFIG IS A CACHE, NOT A CONTRACT. The file is written for convenience and
   may be absent, stale or partial. Every read tolerates all three: a missing
   file is an empty profile, and a field the file lacks is simply re-asked.
"""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

CONFIG_NAME = "labsagent.toml"
PLACEHOLDER = {"name": "Your Name", "cms_id": "00X-0000"}

# Asked in this order; the first two are the only truly unknowable ones.
ASK_ORDER = (
    ("name", "Your full name"),
    ("cms_id", "Your CMS / roll number"),
)

_CMS_SHAPE = re.compile(r"^[0-9]{2}[A-Za-z]?[-\s]?[A-Za-z]?[0-9]{3,4}[-\s]?[A-Za-z0-9]*$")


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


def config_path(root: Path | None = None) -> Path:
    return Path(root or Path.cwd()) / CONFIG_NAME


def load_profile(root: Path | None = None) -> StudentProfile:
    """Read the cached profile. A missing or malformed file is an empty profile."""
    path = config_path(root)
    if not path.exists():
        return StudentProfile()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return StudentProfile()
    student = data.get("student") or {}
    return StudentProfile(
        name=str(student.get("name", "")),
        cms_id=str(student.get("cms_id", student.get("roll_no", ""))),
        section=str(student.get("section", "")),
        program=str(student.get("program", "")),
    )


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def save_profile(profile: StudentProfile, root: Path | None = None) -> Path:
    """Write [student] back, preserving any other sections already in the file.

    Hand-rolled rather than pulling in a TOML writer: four string fields do not
    justify a dependency, and the escaping surface is exactly one function.
    """
    path = config_path(root)
    block = "[student]\n" + "".join(
        f'{key} = "{_toml_escape(value)}"\n' for key, value in asdict(profile).items()
    )

    if path.exists():
        text = path.read_text(encoding="utf-8")
        # Replace the [student] table in place, leaving later tables alone.
        pattern = re.compile(r"^\[student\][^\[]*", re.M)
        text = pattern.sub(block, text) if pattern.search(text) else text.rstrip() + "\n\n" + block
    else:
        text = block

    path.write_text(text, encoding="utf-8")
    return path


def _ask(label: str, current: str = "") -> str:
    suffix = f" [{current}]" if current else ""
    try:
        answer = input(f"  {label}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        return current
    return answer or current


def resolve_profile(
    root: Path | None = None,
    interactive: bool | None = None,
    overrides: dict[str, str] | None = None,
) -> tuple[StudentProfile, bool]:
    """Load, ask about whatever is still missing, persist. Returns (profile, asked).

    `interactive=None` means "decide by looking": a terminal gets prompts, a
    pipe or a CI job does not, because a blocked input() there is an outage.
    """
    profile = load_profile(root)

    for key, value in (overrides or {}).items():
        if value and hasattr(profile, key):
            setattr(profile, key, value)

    missing = profile.missing()
    if not missing:
        return profile, False

    if interactive is None:
        interactive = sys.stdin is not None and sys.stdin.isatty()

    if not interactive:
        for key in missing:
            setattr(profile, key, PLACEHOLDER.get(key, ""))
        print(
            "  note: no terminal to ask on, so the cover page uses placeholders "
            f"({', '.join(missing)}). Set them in {config_path(root)}.",
            file=sys.stderr,
        )
        return profile, False

    print("\nFirst run -- I need a couple of details for the cover page.")
    print("(Saved to %s, so you will not be asked again.)\n" % config_path(root).name)
    for key, label in ASK_ORDER:
        if key not in missing:
            continue
        answer = _ask(label, getattr(profile, key))
        if key == "cms_id" and answer and not _CMS_SHAPE.match(answer):
            # A warning, not a rejection: roll-number formats vary by campus and
            # refusing an unfamiliar one would be worse than accepting it.
            print(f"    (accepting '{answer}' -- it does not look like the usual 22F-1234 shape)")
        setattr(profile, key, answer)

    save_profile(profile, root)
    print(f"\n  saved -> {config_path(root)}\n")
    return profile, True
