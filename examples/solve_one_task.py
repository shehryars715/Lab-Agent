"""Phase 1: the agent writes, runs, and verifies one lab task. Live.

    uv run python examples/solve_one_task.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Windows consoles default to cp1252; model prose is full of non-ASCII.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402

from labsagent.agent.build import build_solver  # noqa: E402
from labsagent.capture.rendered import RenderedBackend  # noqa: E402
from labsagent.config import load_settings  # noqa: E402
from labsagent.sandbox.local import LocalSandbox  # noqa: E402
from labsagent.usage import Usage  # noqa: E402

TASK = (
    "Write a program that reads two integers from the user and prints their sum. "
    "Prompt the user with 'Enter n: ' and 'Enter m: ' respectively, and print the "
    "result in the form 'Sum = <value>'. Test it with n = 5 and m = 3, which should "
    "give Sum = 8. Name the file task1.py."
)


def main() -> int:
    settings = load_settings()
    if not settings.configured:
        print("DEEPSEEK_API_KEY not set (expected in .env)")
        return 1

    with LocalSandbox(keep=True) as sandbox:
        agent, recorder = build_solver(sandbox, settings)
        print(f"workspace: {sandbox.workdir}\nmodel: {settings.model_name}\n")

        result = agent.invoke({"messages": [{"role": "user", "content": TASK}]})
        messages = result["messages"]

        print("--- what the agent did ---")
        turn = 0
        usage = Usage(model=settings.model_name)
        for msg in messages:
            if isinstance(msg, AIMessage):
                turn += 1
                usage.add_message(msg)
                for call in msg.tool_calls or []:
                    args = {k: str(v)[:60] for k, v in call["args"].items()}
                    print(f"  [turn {turn}] -> {call['name']}({args})")
                if not msg.tool_calls and msg.content:
                    print(f"  [turn {turn}] final: {str(msg.content)[:140]}")
            elif isinstance(msg, ToolMessage):
                first = str(msg.content).strip().splitlines()[:1]
                print(f"            <- {first[0][:100] if first else '(empty)'}")

        print("\n--- what we recorded (not what it claimed) ---")
        print(f"  status       : {recorder.status}")
        print(f"  entry_file   : {recorder.entry_file}")
        print(f"  attempts     : {recorder.attempts}")
        print(f"  actually ok  : {recorder.last_ok}")
        print(f"  files written: {sandbox.list_files()}")

        if recorder.last_ok and recorder.last_transcript:
            shots = RenderedBackend(theme="light").render(
                recorder.last_transcript, Path("build/phase1/task1_output.png")
            )
            print(f"  screenshot   : {shots[0]}")
            print("\n--- the generated solution ---")
            for line in sandbox.read_file(recorder.entry_file).splitlines():
                print(f"    {line}")

        print(f"\n--- cost ---")
        print(f"  {turn} model turns | {usage.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
