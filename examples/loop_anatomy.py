"""Anatomy of an agent loop -- watch it turn, one message at a time.

    uv run python examples/loop_anatomy.py          # scripted, free, offline
    uv run python examples/loop_anatomy.py --live   # real DeepSeek call

An "agent" is not a special kind of model. It is an ordinary chat model inside a
while-loop with three rules:

    1. Send the whole conversation to the model.
    2. If the reply contains tool calls, run them and append the results.
    3. If it contains no tool calls, stop. That is the stop condition.

Everything else -- planning tools, virtual filesystems, subagents -- is built on
top of those three rules. deepagents supplies them plus a lot of scaffolding;
the loop itself is still just this.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402

from deepagents import create_deep_agent  # noqa: E402

CALLS: list[str] = []


@tool
def add_numbers(a: int, b: int) -> int:
    """Add two integers and return their sum."""
    CALLS.append(f"add_numbers(a={a}, b={b})")
    return a + b


@tool
def multiply_numbers(a: int, b: int) -> int:
    """Multiply two integers and return their product."""
    CALLS.append(f"multiply_numbers(a={a}, b={b})")
    return a * b


TOOLS = [add_numbers, multiply_numbers]
PROMPT = "You are a calculator. Use the tools for every arithmetic step."
QUESTION = "What is (5 + 3) multiplied by 4?"


def build_model(live: bool):
    if live:
        from langchain_deepseek import ChatDeepSeek

        return ChatDeepSeek(model="deepseek-flash", temperature=0), "deepseek-flash (live)"

    from fakes import ScriptedModel

    script = [
        AIMessage(
            content="",
            tool_calls=[{"name": "add_numbers", "args": {"a": 5, "b": 3}, "id": "c1"}],
        ),
        AIMessage(
            content="",
            tool_calls=[{"name": "multiply_numbers", "args": {"a": 8, "b": 4}, "id": "c2"}],
        ),
        AIMessage(content="(5 + 3) x 4 = 32."),
    ]
    return ScriptedModel(script=script), "scripted (offline, free)"


def describe(msg) -> str:
    kind = type(msg).__name__
    calls = getattr(msg, "tool_calls", None)
    if calls:
        args = ", ".join(f"{c['name']}({c['args']})" for c in calls)
        return f"{kind:<13} -> calls {args}"
    if kind == "ToolMessage":
        return f"{kind:<13} <- returns {msg.content!r}"
    return f"{kind:<13}    {str(msg.content)[:72]!r}"


def main() -> int:
    live = "--live" in sys.argv
    model, label = build_model(live)
    print(f"model: {label}\nquestion: {QUESTION}\n")

    agent = create_deep_agent(model=model, tools=TOOLS, system_prompt=PROMPT)
    result = agent.invoke({"messages": [{"role": "user", "content": QUESTION}]})
    messages = result["messages"]

    turn = 0
    print("--- the loop, message by message ---")
    for msg in messages:
        if isinstance(msg, AIMessage):
            turn += 1
            print(f"\n  [turn {turn}] model was sent the whole conversation so far")
        print(f"     {describe(msg)}")

    final = messages[-1]
    print("\n--- why it stopped ---")
    print(f"  last message is an AIMessage with tool_calls = {getattr(final, 'tool_calls', None) or 'none'}")
    print("  no tool calls -> the loop's exit condition is satisfied -> return")

    print("\n--- what this cost in context ---")
    print(f"  model invocations : {turn}")
    print(f"  tools executed    : {len(CALLS)}  {CALLS}")
    print(f"  messages in final : {len(messages)}")
    print(
        "\n  Each invocation resent EVERY earlier message. That is why input tokens\n"
        "  grow faster than the conversation does, and why a byte-stable system\n"
        "  prompt (cacheable) matters so much in a long agent run."
    )

    if live:
        usage = getattr(final, "usage_metadata", None)
        if usage:
            in_tok = usage.get("input_tokens", 0)
            out_tok = usage.get("output_tokens", 0)
            cost = in_tok / 1e6 * 0.14 + out_tok / 1e6 * 0.28
            print(f"\n  final turn usage: {in_tok} in / {out_tok} out  (~${cost:.6f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
