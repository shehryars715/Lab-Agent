"""Pins the agent-loop contract using a scripted model -- no network, no key."""

from __future__ import annotations

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from deepagents import create_deep_agent
from fakes import ScriptedModel

RAN: list[tuple[int, int]] = []


@tool
def add_numbers(a: int, b: int) -> int:
    """Add two integers and return their sum."""
    RAN.append((a, b))
    return a + b


def _agent(script: list[AIMessage]):
    return create_deep_agent(
        model=ScriptedModel(script=script),
        tools=[add_numbers],
        system_prompt="You add numbers.",
    )


def test_tool_call_is_dispatched_and_result_fed_back():
    RAN.clear()
    agent = _agent(
        [
            AIMessage(
                content="",
                tool_calls=[{"name": "add_numbers", "args": {"a": 5, "b": 3}, "id": "c1"}],
            ),
            AIMessage(content="The sum is 8."),
        ]
    )
    messages = agent.invoke({"messages": [{"role": "user", "content": "5+3?"}]})["messages"]

    assert RAN == [(5, 3)], "the tool function itself must actually execute"
    tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].content == "8"


def test_loop_stops_on_an_ai_message_without_tool_calls():
    """The stop condition, stated as a test."""
    RAN.clear()
    agent = _agent([AIMessage(content="42, no tools needed.")])
    messages = agent.invoke({"messages": [{"role": "user", "content": "hi"}]})["messages"]

    assert RAN == []
    assert not getattr(messages[-1], "tool_calls", None)
    assert messages[-1].content == "42, no tools needed."


def test_loop_runs_multiple_turns_until_satisfied():
    RAN.clear()
    agent = _agent(
        [
            AIMessage(
                content="",
                tool_calls=[{"name": "add_numbers", "args": {"a": 1, "b": 1}, "id": "c1"}],
            ),
            AIMessage(
                content="",
                tool_calls=[{"name": "add_numbers", "args": {"a": 2, "b": 2}, "id": "c2"}],
            ),
            AIMessage(content="Done."),
        ]
    )
    messages = agent.invoke({"messages": [{"role": "user", "content": "go"}]})["messages"]

    assert RAN == [(1, 1), (2, 2)], "loop must keep turning while tools are requested"
    assert len([m for m in messages if isinstance(m, ToolMessage)]) == 2


def test_conversation_accumulates_rather_than_resets():
    """Why input tokens grow superlinearly: nothing is dropped between turns."""
    RAN.clear()
    agent = _agent(
        [
            AIMessage(
                content="",
                tool_calls=[{"name": "add_numbers", "args": {"a": 1, "b": 1}, "id": "c1"}],
            ),
            AIMessage(content="Done."),
        ]
    )
    messages = agent.invoke({"messages": [{"role": "user", "content": "go"}]})["messages"]

    kinds = [type(m).__name__ for m in messages]
    assert kinds == ["HumanMessage", "AIMessage", "ToolMessage", "AIMessage"]
