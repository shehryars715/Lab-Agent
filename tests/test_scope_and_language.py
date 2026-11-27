"""Harness v2, Phase 4: which language each task is written in, and the scope card.

D7 -- the precedence: the student's words > the task's own quoted language >
the lab's language > the manual's code samples > Python.
D2 -- ask, don't refuse: one task this environment cannot do no longer refuses
the lab; the student chooses "do the other N" or "stop".
"""

from __future__ import annotations

import pytest

from labsagent import languages as langs
from labsagent.capabilities import OutOfScope
from labsagent.ingest.labspec import (
    ExtractedLab,
    _with_languages,
    detect_code_language,
    task_languages,
)
from labsagent.ingest.readers import read_pasted
from labsagent.models import LabSpec, Task

OS_LAB = read_pasted(
    "Lab 06: Processes on Linux\n"
    "Task 1: Write a C program that creates a child process using fork().\n"
    "Task 2: Make the parent wait for the child and print its exit status.\n"
    "Example:\n#include <stdio.h>\n#include <unistd.h>\nint main() { printf(\"hi\"); }\n"
)


def _lab(tasks, **intent):
    return ExtractedLab.model_validate(
        {"lab_number": "6", "title": "Lab 06", "tasks": tasks, "intent": intent}
    )


def _task(n, language="python", evidence="", needs_code=True):
    return {
        "task_number": n, "title": f"Task {n}", "statement": f"Statement {n}.",
        "anchor_idx": 0, "anchor_quote": "", "language": language,
        "language_evidence": evidence, "needs_code": needs_code,
    }


def test_a_quoted_language_wins_over_python():
    lab = _lab([_task(1, "c", "Write a C program that creates a child process")])
    chosen, why = task_languages(lab, OS_LAB)
    assert chosen == {1: "c"} and why == "as the manual says"


def test_the_lab_language_rescues_a_task_whose_own_quote_failed():
    lab = _lab([
        _task(1, "c", "Write a C program that creates a child process"),
        _task(2, "c", "a quote the manual does not contain at all"),
    ])
    assert task_languages(lab, OS_LAB)[0] == {1: "c", 2: "c"}


def test_an_ungrounded_claim_alone_is_still_python():
    lab = _lab([_task(1, "java", "words that are not in the manual anywhere")])
    plain = read_pasted("Task 1: Add two numbers and print the sum.")
    assert task_languages(lab, plain)[0] == {1: "python"}


def test_the_code_samples_are_a_backstop_when_nothing_was_said():
    lab = _lab([_task(1), _task(2)])
    chosen, why = task_languages(lab, OS_LAB)
    assert chosen == {1: "c", 2: "c"} and why == "as the manual's examples are"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("#include <iostream>\nusing namespace std;\ncout << x;", "cpp"),
        ("public static void main(String[] a)\nSystem.out.println(1);\nnew Scanner(System.in)", "java"),
        ("#include <stdio.h>\nint main() {\nprintf(\"%d\", x);", "c"),
        # One sign of Python anywhere: a Python lab showing C for comparison stays Python.
        ("#include <stdio.h>\nint main() {\nprintf(\"%d\", x);\nprint(x)", None),
        ("Add two numbers.", None),
    ],
)
def test_detect_code_language_needs_real_evidence(text, expected):
    assert detect_code_language(read_pasted(text)) == expected


def test_the_students_words_win_and_must_really_be_in_their_message():
    lab = _lab([_task(1, "c", "Write a C program that creates a child process"), _task(2)],
               language="java", language_quote="do it in Java")
    chosen, why = task_languages(lab, OS_LAB, request="please do it in Java")
    assert chosen == {1: "java", 2: "java"} and why == "as you asked"
    # The model claimed a wish the message does not contain: ignored.
    assert task_languages(lab, OS_LAB, request="only task 2 please")[0][1] == "c"


def test_an_empty_wish_is_not_a_wish_for_python():
    lab = _lab([_task(1, "c", "Write a C program that creates a child process")], language="")
    assert task_languages(lab, OS_LAB, request="make a python notebook too")[0] == {1: "c"}


def test_the_c_in_can_is_not_c():
    lab = _lab([_task(1)], language="c")
    plain = read_pasted("Task 1: Add two numbers.")
    assert task_languages(lab, plain, request="can you do task 1")[0] == {1: "python"}


def test_a_theory_task_keeps_python():
    lab = _lab([_task(1, needs_code=False), _task(2, "c", "Write a C program that creates a child process")])
    assert task_languages(lab, OS_LAB)[0] == {1: "python", 2: "c"}


def test_tasks_carry_a_registry_language_and_nothing_else():
    spec = LabSpec(lab_number="6", title="L", tasks=[
        Task(id="task1", title="a", statement="s"),
        Task(id="task2", title="b", statement="s"),
        Task(id="task3", title="c", statement="s"),
    ])
    out = _with_languages(spec, {1: "c++", 2: "html/css", 3: "python"})
    assert [t.language for t in out.tasks] == ["cpp", "python", "python"]


# --------------------------------------------------------------- the card

def test_the_scope_card_offers_the_rest_or_stop():
    from web.server.pipeline import SCOPE_KEY, scope_question

    spec = LabSpec(lab_number="3", title="L", tasks=[
        Task(id=f"task{n}", title=f"T{n}", statement="s") for n in range(1, 6)
    ])
    out = [OutOfScope(task_id="task3", title="Paint app",
                      reasons=["it needs a screen or browser to show the result (tkinter)"])]
    card = scope_question(out, spec)
    assert card["key"] == SCOPE_KEY and card["required"] and card["kind"] == "prerequisite"
    assert [o["value"] for o in card["options"]] == ["rest", "stop"]
    assert card["options"][0]["label"] == "Do the other 4"
    assert "Task 3 (Paint app): it needs a screen" in card["reason"]


def test_a_skipped_task_reads_as_not_done_with_its_reason():
    from web.server.pipeline import skipped_sentence

    assert skipped_sentence("it is written in MATLAB") == (
        "This task is written in MATLAB, which can't be done here."
    )


def test_the_language_line_says_what_and_why_and_nothing_for_python():
    from web.server.pipeline import language_line

    c = LabSpec(lab_number="6", title="L", tasks=[
        Task(id="task1", title="a", statement="s", language="c"),
        Task(id="task2", title="b", statement="s", language="c"),
    ])
    assert language_line(c, "as the manual says") == "I'll write the code in C, as the manual says."
    py = LabSpec(lab_number="6", title="L", tasks=[Task(id="task1", title="a", statement="s")])
    assert language_line(py, "") == ""
    mixed = LabSpec(lab_number="6", title="L", tasks=[
        Task(id="task1", title="a", statement="s"),
        Task(id="task2", title="b", statement="s", language="java"),
    ])
    assert language_line(mixed, "") == "I'll write task 1 in Python and task 2 in Java."


def test_registry_languages_resolve_for_the_gate():
    assert langs.resolve("c") is langs.C
