"""What this harness can do, stated once, and checked before any code is written.

THE FAILURE THIS EXISTS FOR. A web-engineering lab ("build a portfolio site in
HTML and CSS") went straight to the solver, whose last instruction is "write
your solution to a file named task1.py". A capable agent satisfied both halves:
it wrote Python that prints HTML, the run passed, and the student received a
Word report full of HTML inside Python strings -- with the actual .html pages
never delivered at all. Nothing upstream of the solver knew that the harness can
do exactly one thing, so the solver was the first and worst place to find out.

THE RULE: A LIMIT IS CHECKED WHERE THE WORK IS SCOPED, NOT WHERE IT RUNS. The
ingest call reads what each task requires (language, what the program needs to
run, libraries it names) and QUOTES the manual for each; this module compares
that against the one environment there is. Code decides, from a fact the model
read -- the same split as the data question in the pipeline.

ONE SOURCE OF TRUTH. The solver prompt, the gate and the refusal message all
read the constants below, so what the student is told, what the gate checks and
what the solver is promised cannot drift apart. `AVAILABLE_LIBRARIES` is
imported by a test that tries to import every entry.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field

#: The one language the solver writes and runs.
SOLVER_LANGUAGE = "python"

#: Every third-party library the solver is promised. Nothing else is installed
#: as far as a task is concerned -- a package that happens to be importable as
#: a dependency of the harness itself is not a promise.
AVAILABLE_LIBRARIES = (
    "numpy", "pandas", "matplotlib", "seaborn", "scipy", "scikit-learn", "openpyxl",
)

#: Import names and common spellings -> the name in AVAILABLE_LIBRARIES.
LIBRARY_ALIASES = {
    "sklearn": "scikit-learn",
    "scikit learn": "scikit-learn",
    "scikitlearn": "scikit-learn",
    "np": "numpy",
    "pd": "pandas",
    "plt": "matplotlib",
    "pyplot": "matplotlib",
    "matplotlib.pyplot": "matplotlib",
    "sns": "seaborn",
}

#: What the environment lacks, as the closed vocabulary the ingest call picks
#: from. The phrases are what the student is told in a refusal.
NEEDS = {
    "display": "a screen or browser to show the result",
    "server": "a program that keeps running and waits for connections",
    "internet": "live internet access while the program runs",
    "hardware": "physical hardware",
    "software": "software other than Python",
}

#: Needs that make sense for a task with no program at all: the work itself
#: is done in another tool, or on a device.
TOOL_NEEDS = {"software", "hardware"}


def can_do() -> str:
    """The environment in one sentence, for a student."""
    libs = ", ".join(AVAILABLE_LIBRARIES[:-1]) + " and " + AVAILABLE_LIBRARIES[-1]
    return (
        f"Right now I can only write and run Python 3 programs, using the standard "
        f"library plus {libs}, offline and with no screen, and nothing else can be "
        "installed."
    )


def normalise_library(name: str) -> str:
    """"sklearn" -> "scikit-learn"; "pandas.DataFrame" -> "pandas"."""
    raw = re.sub(r"\s+", " ", str(name or "").strip().lower())
    if raw in LIBRARY_ALIASES:
        return LIBRARY_ALIASES[raw]
    head = raw.split(".")[0].replace("_", "-")
    return LIBRARY_ALIASES.get(head, head)


def library_available(name: str) -> bool:
    """Promised third-party library, or the standard library. Nothing else.

    The standard library counts even for modules that need something else to
    work -- tkinter and turtle need a screen -- because that is a NEED, read
    separately, not a missing library.
    """
    lib = normalise_library(name)
    if not lib:
        return True
    return lib in AVAILABLE_LIBRARIES or lib.replace("-", "_") in sys.stdlib_module_names


@dataclass(frozen=True)
class Requirement:
    """What one task needs, as read (and quoted) by ingest, after grounding.

    Only what survived grounding is here: a language other than Python, a need
    or a library is recorded only when the manual's own words back it up. So
    an empty Requirement is the ordinary case, and means "runs here".
    """

    language: str = SOLVER_LANGUAGE
    #: (need, why) pairs; `need` is a key of NEEDS.
    needs: tuple[tuple[str, str], ...] = ()
    libraries: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutOfScope:
    task_id: str
    title: str
    reasons: list[str] = field(default_factory=list)


def _language_phrase(language: str) -> str:
    shown = {
        "html": "HTML", "css": "CSS", "html/css": "HTML and CSS", "javascript": "JavaScript",
        "js": "JavaScript", "java": "Java", "c": "C", "c++": "C++", "cpp": "C++",
        "c#": "C#", "sql": "SQL", "r": "R", "matlab": "MATLAB", "php": "PHP",
        "bash": "shell script", "shell": "shell script", "assembly": "assembly",
    }
    return shown.get(language.lower(), language)


def is_python(language: str) -> bool:
    lang = str(language or "").strip().lower()
    # Empty means the model did not say. Failing open here keeps the gate from
    # refusing a lab over a missing field; grounding already demands a quote
    # for anything that is NOT Python.
    return lang in ("", "python", "python3", "python 3", "py", "none")


def check(spec, requirements: dict[str, Requirement]) -> list[OutOfScope]:
    """Every task in `spec` this environment cannot do, with the reasons.

    Only the tasks IN the spec are checked, and the caller passes the scoped
    spec: a student who asks for "only tasks 2-4" of a lab whose task 1 is HTML
    gets tasks 2-4. A pure theory question needs nothing and is never out of
    scope.
    """
    out: list[OutOfScope] = []
    for task in spec.tasks:
        req = requirements.get(task.id)
        if req is None:
            continue
        # A WORDS-ONLY TASK HAS NO PROGRAM, SO NO LANGUAGE OR LIBRARY -- but it
        # can still require a TOOL. "Clean the dataset in Tableau Prep Builder"
        # was read as words-only (it is not Python), skipped here, and would
        # have come back as an essay about using Tableau. Only a tool or a
        # device can put such a task out of scope.
        coded = getattr(task, "needs_code", True)
        reasons: list[str] = []
        if coded and not is_python(req.language):
            reasons.append(f"it is written in {_language_phrase(req.language)}")
        for need, why in req.needs:
            if need in NEEDS and (coded or need in TOOL_NEEDS):
                reasons.append(f"it needs {NEEDS[need]}" + (f" ({why})" if why else ""))
        # A missing LIBRARY is deliberately not here -- see `missing_libraries`.
        if reasons:
            out.append(OutOfScope(task_id=task.id, title=task.title or task.id, reasons=reasons))
    return out


def missing_libraries(spec, requirements: dict[str, Requirement]) -> list[tuple[str, list[str]]]:
    """(task id, libraries) for tasks that name a library that isn't installed.

    NOT A REFUSAL (decided 2026-09-26). A named library is usually one step of
    a task -- "use colorspacious to simulate colour blindness" in an otherwise
    ordinary plotting lab -- and refusing the whole lab over it cost the
    reference Superstore lab everything. So it is said UP FRONT, and the solver
    does the rest: its missing-module guard and `missing` already turn that
    step into a plain "Not done: ..." in the report, never a stand-in.
    """
    found: list[tuple[str, list[str]]] = []
    for task in spec.tasks:
        req = requirements.get(task.id)
        if req is None or not getattr(task, "needs_code", True):
            continue
        missing = [lib for lib in req.libraries if not library_available(lib)]
        if missing:
            found.append((task.id, missing))
    return found


def library_notice(found: list[tuple[str, list[str]]]) -> str:
    """The up-front sentence for `missing_libraries`. "" when there are none."""
    if not found:
        return ""
    parts = []
    for task_id, libs in found:
        names = " and ".join(libs)
        noun = "library" if len(libs) == 1 else "libraries"
        parts.append(f"{task_id.replace('task', 'Task ')} asks for the {names} {noun}")
    verb = "isn't" if len(found) == 1 and len(found[0][1]) == 1 else "aren't"
    return (
        "; ".join(parts)
        + f", which {verb} installed here, so I'll do everything else and mark that "
        "part as not done."
    )


def refusal(out: list[OutOfScope], total: int) -> str:
    """What the student is told, in one paragraph (the web UI shows it in a <p>).

    Names each task and why, states the environment, and says what to do. The
    last sentence only appears when narrowing would help -- telling a student
    with a one-task HTML lab to "ask for the other tasks" is noise.
    """
    parts = []
    for item in out[:4]:
        label = item.task_id.replace("task", "Task ")
        parts.append(f"{label} ({item.title}): " + "; ".join(item.reasons[:2]))
    lead = "I can't do this lab. " + ". ".join(parts) + ". "
    tail = can_do() + " I stopped before writing any code."
    if len(out) < total:
        ids = ", ".join(i.task_id.replace("task", "") for i in out)
        tail += (
            f" If the other tasks are what you need, send it again and say which "
            f"ones, leaving out {'task' if len(out) == 1 else 'tasks'} {ids}."
        )
    return (lead + tail)[:900]
