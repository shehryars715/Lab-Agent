"""The block IR, and the promise that it changes nothing until it is used."""

from __future__ import annotations

from pathlib import Path

from labsagent.blocks import FIGURE, SCREENSHOT, Block, blocks_for, code_of
from labsagent.models import Task, TaskOutcome, Transcript


def _outcome(**over) -> TaskOutcome:
    base = dict(
        task=Task(id="task1", title="T", statement="s"),
        status="passed",
        code_text="print(1)\n",
        transcript=Transcript(command="python task1.py", lines=["1"]),
        explanation="It prints one.",
        attempts=1,
    )
    base.update(over)
    return TaskOutcome(**base)


def test_synthesised_order_matches_the_docx_writer():
    """code -> output -> images -> prose is the order annotate_manual uses."""
    out = _outcome(
        screenshot_paths=[Path("shot.png")], figure_paths=[Path("fig.png")]
    )
    kinds = [b.kind for b in blocks_for(out)]

    assert kinds == ["code", "output", "image", "image", "prose"]


def test_screenshots_and_figures_stay_distinguishable():
    """An emitter must be able to take the figure and skip the terminal PNG."""
    out = _outcome(screenshot_paths=[Path("shot.png")], figure_paths=[Path("fig.png")])
    roles = [b.role for b in blocks_for(out) if b.kind == "image"]

    assert roles == [SCREENSHOT, FIGURE]


def test_a_failed_task_stops_at_the_error():
    out = _outcome(status="failed", attempts=3, error="NameError: x", explanation=None)
    blocks = blocks_for(out)

    assert [b.kind for b in blocks] == ["code", "error"]
    assert "3 attempt" in blocks[1].text and "NameError" in blocks[1].text


def test_missing_code_still_produces_a_code_block():
    """The report says so rather than silently omitting the section."""
    out = _outcome(code_text="", transcript=None, explanation=None)
    blocks = blocks_for(out)

    assert blocks[0].kind == "code"
    assert "no solution was produced" in blocks[0].text


def test_explicit_blocks_win_over_the_named_fields():
    """The whole point of the additive field: the solver can take over."""
    mine = [Block("prose", text="First, the idea."), Block("code", text="x = 1")]
    out = _outcome(blocks=mine)

    assert blocks_for(out) == mine


def test_code_of_joins_every_code_block():
    out = _outcome(blocks=[Block("code", text="a = 1"), Block("prose", text="then"),
                           Block("code", text="b = 2")])

    assert code_of(out) == "a = 1\n\nb = 2"
