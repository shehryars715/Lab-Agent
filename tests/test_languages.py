"""Harness v2, Phase 2: the language registry, with Python unchanged.

The load-bearing test is the first one. The solver prompt is the largest
constant in every request, and it is cached as a prefix: a refactor that moves
one byte of it costs every Python task a cache miss and may change behaviour.
`fixtures/solver_prompt_python.txt` is the prompt as it was BEFORE the registry,
written out from the old code; the template must reproduce it exactly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from labsagent import capabilities, languages as langs
from labsagent.agent.prompts import SOLVER_PROMPT, solver_prompt
from labsagent.agent.tools import TaskRecorder, build_tools
from labsagent.capabilities import Requirement
from labsagent.models import LabSpec, Task
from labsagent.orchestrator import build_task_prompt, choose_run
from labsagent.runstore import _task_from_dict, _task_to_dict

FIXTURE = Path(__file__).parent / "fixtures" / "solver_prompt_python.txt"


def test_the_python_prompt_is_byte_identical_to_the_one_before_the_registry():
    assert SOLVER_PROMPT == FIXTURE.read_bytes().decode("utf-8")
    assert solver_prompt("python") is SOLVER_PROMPT or solver_prompt("python") == SOLVER_PROMPT


@pytest.mark.parametrize("key", ["c", "cpp", "java", "javascript"])
def test_every_other_prompt_is_its_own_and_carries_no_python(key):
    prompt = solver_prompt(key)
    lang = langs.REGISTRY[key]
    assert lang.entry("task1") in prompt
    assert "record_task_result" in prompt and '"blocked"' in prompt
    # Python's mechanics must not leak into another language's contract.
    for python_only in ("input()", "pd.read_csv", "# %%", "plt.savefig", "no pip"):
        assert python_only not in prompt, (key, python_only)
    # Static, so it caches: the same object every time.
    assert solver_prompt(key) is prompt


def test_resolve_takes_every_spelling_and_refuses_what_is_not_here():
    assert langs.resolve("C++") is langs.CPP
    assert langs.resolve(" c plus plus ") is langs.CPP
    assert langs.resolve("Node.js") is langs.JAVASCRIPT
    assert langs.resolve("Java") is langs.JAVA
    assert langs.resolve("") is langs.PYTHON  # no language said = Python, as before
    assert langs.resolve("matlab") is None
    assert langs.resolve("html/css") is None


def test_entry_names_follow_each_language():
    assert langs.PYTHON.entry("task3") == "task3.py"
    assert langs.C.entry("task3") == "task3.c"
    assert langs.CPP.entry("task3") == "task3.cpp"
    assert langs.JAVA.entry("task3") == "Task3.java"  # the public-class rule
    assert langs.JAVASCRIPT.entry("task3") == "task3.js"


def test_a_task_without_a_language_is_python():
    task = Task(id="task1", title="t", statement="s")
    assert langs.of(task) is langs.PYTHON
    assert langs.of(Task(id="task1", title="t", statement="s", language="java")) is langs.JAVA


def test_enabled_always_has_python_first_and_drops_unknowns():
    assert langs.enabled(()) == (langs.PYTHON,)
    assert langs.enabled(["java", "python", "cobol", "c"]) == (langs.PYTHON, langs.JAVA, langs.C)


def _spec(*tasks):
    return LabSpec(lab_number="7", title="Lab", tasks=list(tasks))


def test_the_gate_still_refuses_another_language_unless_this_deployment_runs_it():
    spec = _spec(Task(id="task1", title="Scheduler", statement="Write it in C"))
    reqs = {"task1": Requirement(language="c")}
    # Default: Python alone -- exactly the old gate.
    out = capabilities.check(spec, reqs)
    assert out and "it is written in C" in out[0].reasons[0]
    # A runner whose image carries gcc enables C.
    assert capabilities.check(spec, reqs, languages=("python", "c")) == []
    # A language not in the registry is refused whatever is enabled.
    reqs = {"task1": Requirement(language="matlab")}
    assert capabilities.check(spec, reqs, languages=("python", "c"))[0].reasons == [
        "it is written in MATLAB"
    ]


def test_can_do_is_unchanged_for_python_and_lists_what_else_runs():
    assert capabilities.can_do() == (
        "Right now I can only write and run Python 3 programs, using the standard "
        "library plus numpy, pandas, matplotlib, seaborn, scipy, scikit-learn and openpyxl, "
        "offline and with no screen, and nothing else can be installed."
    )
    more = capabilities.can_do(("python", "c", "java"))
    assert "C and Java programs with their standard libraries" in more


@pytest.mark.parametrize(
    "lang, stderr, expected",
    [
        (langs.C, "task1.c:1:10: fatal error: conio.h: No such file or directory", "conio.h"),
        (langs.CPP, "fatal error: boost/any.hpp: No such file or directory", "boost/any.hpp"),
        (langs.JAVA, "Task1.java:1: error: package javafx.application does not exist",
         "javafx.application"),
        (langs.JAVASCRIPT, "Error: Cannot find module 'express'", "express"),
        (langs.JAVASCRIPT, "Error: Cannot find module './helper'", None),  # its own file
        (langs.C, "task1.c:3:5: error: expected ';' before 'return'", None),
    ],
)
def test_each_language_recognises_a_missing_library(lang, stderr, expected, tmp_path):
    assert lang.missing_dependency(stderr, tmp_path) == expected


def test_a_header_the_agent_wrote_is_not_missing(tmp_path):
    (tmp_path / "stack.h").write_text("int f(void);")
    stderr = "fatal error: stack.h: No such file or directory"
    assert langs.C.missing_dependency(stderr, tmp_path) is None


def test_libraries_each_language_judges():
    assert langs.C.library_available("stdio.h")
    assert langs.C.library_available("pthread")
    assert not langs.C.library_available("conio.h")
    assert not langs.CPP.library_available("graphics.h")
    assert langs.JAVA.library_available("java.util.Scanner")
    assert not langs.JAVA.library_available("JavaFX")
    assert langs.JAVASCRIPT.library_available("readline")
    assert langs.JAVASCRIPT.library_available("prompt-sync")
    assert not langs.JAVASCRIPT.library_available("express")
    # Python delegates to the gate's own rule.
    assert langs.PYTHON.library_available("sklearn")
    assert not langs.PYTHON.library_available("colorspacious")


def test_missing_libraries_asks_the_tasks_own_language():
    spec = _spec(Task(id="task1", title="t", statement="s", language="c"))
    reqs = {"task1": Requirement(language="c", libraries=("stdio.h", "conio.h"))}
    assert capabilities.missing_libraries(spec, reqs) == [("task1", ["conio.h"])]


class _Run:
    def __init__(self, entry, ok=True):
        self.entry_file, self.ok, self.transcript = entry, ok, object()


def test_choose_run_uses_the_languages_entry_and_suffix():
    runs = [_Run("Task3.java"), _Run("Probe.java")]
    assert choose_run(runs, "task3", language="java").entry_file == "Task3.java"
    near = [_Run("task3_v2.c"), _Run("cleanup.c")]
    assert choose_run(near, "task3", language="c").entry_file == "task3_v2.c"
    # A Python file is never a C task's near-miss.
    assert choose_run([_Run("task3_v2.py")], "task3", language="c") is None


def test_the_last_line_of_the_task_message_names_the_languages_file():
    py = Task(id="task2", title="t", statement="Add two numbers.")
    java = Task(id="task2", title="t", statement="Add two numbers.", language="java")
    assert build_task_prompt(py, {}).endswith("Write your solution to a file named task2.py")
    assert build_task_prompt(java, {}).endswith("Write your solution to a file named Task2.java")


def test_language_round_trips_and_python_manifests_are_unchanged():
    java = Task(id="task1", title="t", statement="s", language="java")
    assert _task_from_dict(_task_to_dict(java)).language == "java"
    py = Task(id="task1", title="t", statement="s")
    assert "language" not in _task_to_dict(py)  # byte-identical manifests
    assert _task_from_dict(_task_to_dict(py)).language == "python"


class _Box:
    workdir = Path(".")


def test_python_tool_descriptions_are_unchanged_and_others_get_their_own():
    py_run, py_record = build_tools(_Box(), TaskRecorder())
    assert "Execute a Python file" in py_run.description
    assert "a language other than Python" in py_record.description
    c_run, c_record = build_tools(_Box(), TaskRecorder(), language="c")
    assert "Python" not in c_run.description and "^D" in c_run.description
    assert "Python" not in c_record.description
