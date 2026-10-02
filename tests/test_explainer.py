"""The explanation pass: isolation, length enforcement, and failure tolerance.

The load-bearing test here is `test_brief_excludes_the_debugging_history`. Every
other test checks behaviour; that one checks the PROPERTY the whole module
exists for, and it is the one that should fail loudly if someone later decides
to "give the explainer a bit more context to work with".
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from labsagent.agent.explainer import (
    LONG_SENTENCES,
    SHORT_SENTENCES,
    Explainer,
    build_brief,
    clamp_sentences,
    plain_text,
    sentence_budget,
)
from labsagent.models import ExecResult, Task, Transcript
from labsagent.usage import RunUsage

CODE = 'n = int(input("Enter n: "))\nprint(f"Sum = {n}")\n'


def _task(**kw) -> Task:
    base = dict(
        id="task1",
        title="Sum of Two Numbers",
        statement="Read two integers and print their sum.",
    )
    return Task(**{**base, **kw})


def _transcript(stdout: str = "Enter n: 5\nSum = 5\n") -> Transcript:
    return Transcript.from_exec(
        "python task1.py",
        ExecResult(exit_code=0, stdout=stdout, stderr="", duration_s=0.1, timed_out=False),
    )


def _reply(text: str, in_tokens: int = 400, out_tokens: int = 30) -> AIMessage:
    return AIMessage(
        content=text,
        usage_metadata={
            "input_tokens": in_tokens,
            "output_tokens": out_tokens,
            "total_tokens": in_tokens + out_tokens,
            "input_token_details": {"cache_read": 0},
        },
    )


class _FakeModel:
    """Returns a fixed reply and remembers what it was asked."""

    def __init__(self, text: str = "The program reads an integer. It prints the sum."):
        self.text = text
        self.seen: list = []

    def invoke(self, messages):
        self.seen.append(messages)
        return _reply(self.text)


# --- the isolation property -------------------------------------------------


def test_brief_excludes_the_debugging_history():
    """The brief is built from the outcome, so there is no seam for it to leak.

    This is the whole point of composing the brief ourselves rather than letting
    the solver delegate: the debugging is not filtered out of the context, it is
    never put in.
    """
    brief = build_brief(_task(), CODE, _transcript())

    for leak in ("Traceback", "attempt", "retry", "NameError", "failed", "fix"):
        assert leak.lower() not in brief.lower(), f"{leak!r} leaked into the brief"


def test_brief_carries_what_the_explainer_actually_needs():
    brief = build_brief(_task(), CODE, _transcript())

    assert "Read two integers and print their sum." in brief
    assert 'int(input("Enter n: "))' in brief
    assert "Sum = 5" in brief


def test_brief_survives_a_missing_transcript():
    brief = build_brief(_task(), CODE, None)

    assert "What it printed" not in brief
    assert 'int(input("Enter n: "))' in brief


def test_long_output_is_truncated_rather_than_sent_whole():
    noisy = _transcript("\n".join(f"line {i}" for i in range(500)))
    brief = build_brief(_task(), CODE, noisy)

    assert "output truncated" in brief
    assert "line 499" not in brief


# --- length policy ----------------------------------------------------------


def test_wants_explanation_drives_the_budget():
    assert sentence_budget(_task()) == SHORT_SENTENCES
    assert sentence_budget(_task(wants_explanation=True)) == LONG_SENTENCES


def test_brief_states_the_sentence_count():
    # A ceiling, not a quota: "exactly two sentences" is how every note came out
    # the same length, which is half of what made them read as machine-made.
    assert f"at most {SHORT_SENTENCES} sentences" in build_brief(_task(), CODE, None)
    assert (
        f"at most {LONG_SENTENCES} sentences"
        in build_brief(_task(wants_explanation=True), CODE, None)
    )


def test_clamp_cuts_on_sentence_boundaries():
    text = "One here. Two here. Three here. Four here."

    assert clamp_sentences(text, 2) == "One here. Two here."


def test_clamp_leaves_short_text_alone():
    assert clamp_sentences("Only one.", 2) == "Only one."


def test_clamp_never_produces_a_half_sentence():
    """A 60-word single sentence is under budget, so it passes through whole."""
    long_one = "The program " + "reads and prints values " * 20 + "in order."

    assert clamp_sentences(long_one, 2) == long_one


def test_clamp_handles_question_and_exclamation_marks():
    assert clamp_sentences("Why? Because. And more.", 2) == "Why? Because."


# --- markdown stripping -----------------------------------------------------


def test_markdown_is_stripped_because_docx_shows_it_literally():
    assert plain_text("The **program** uses `int()` here.") == "The program uses int() here."


def test_plain_text_leaves_ordinary_prose_untouched():
    text = "The program reads two integers and prints their sum."

    assert plain_text(text) == text


# --- the callable seam ------------------------------------------------------


def test_explainer_returns_clamped_plain_prose():
    model = _FakeModel("The **program** reads an integer. It prints it. A third one. A fourth.")

    written = Explainer(model=model)(_task(), CODE, _transcript())

    assert written == "The program reads an integer. It prints it."


def test_explainer_bills_to_its_own_phase():
    usage = RunUsage()

    Explainer(model=_FakeModel(), usage=usage)(_task(), CODE, _transcript())

    assert usage.phase("explain").calls == 1
    assert usage.phase("explain").output_tokens == 30
    assert "solve" not in usage.phases


def test_explainer_sends_the_stable_prompt_as_a_system_message():
    """A per-call system prompt would convert every cache hit into a miss."""
    model = _FakeModel()

    Explainer(model=model)(_task(), CODE, _transcript())
    Explainer(model=model)(_task(id="task2"), CODE, _transcript())

    first, second = model.seen[0][0], model.seen[1][0]
    assert first["role"] == "system"
    assert first["content"] == second["content"]


def test_a_broken_model_call_returns_none_rather_than_killing_the_run():
    class Exploding:
        def invoke(self, messages):
            raise RuntimeError("api down")

    assert Explainer(model=Exploding())(_task(), CODE, _transcript()) is None


def test_no_code_means_no_call_at_all():
    model = _FakeModel()

    assert Explainer(model=model)(_task(), "", None) is None
    assert model.seen == []


def test_an_empty_reply_becomes_none_not_an_empty_paragraph():
    assert Explainer(model=_FakeModel("   "))(_task(), CODE, _transcript()) is None
