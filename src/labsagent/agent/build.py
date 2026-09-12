"""Wire the solver agent.

The filesystem backend is rooted at the SANDBOX workspace, never the project
root. deepagents' own docs are blunt about this: FilesystemBackend "grants
agents direct filesystem read/write access... Agents can read any accessible
file, including secrets (API keys, credentials, .env files)". Our .env holds a
live key, so the root_dir is the security boundary, and tests/test_agent.py
verifies it actually holds.
"""

from __future__ import annotations

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_deepseek import ChatDeepSeek

from labsagent.agent.confined import ConfinedBackend
from labsagent.agent.prompts import SOLVER_PROMPT
from labsagent.agent.tools import TaskRecorder, build_tools
from labsagent.config import Settings
from labsagent.sandbox.base import Sandbox


def build_model(settings: Settings):
    return ChatDeepSeek(
        model=settings.model_name,
        temperature=settings.temperature,
        api_key=settings.deepseek_api_key,
    )


def build_solver(sandbox: Sandbox, settings: Settings, model=None):
    """Return (agent, recorder). One agent per task -- fresh context each time."""
    recorder = TaskRecorder()
    # root_dir alone is not a boundary: it honours absolute paths. See confined.py.
    backend = ConfinedBackend(
        FilesystemBackend(root_dir=sandbox.workdir, virtual_mode=False),
        sandbox.workdir,
    )

    agent = create_deep_agent(
        model=model if model is not None else build_model(settings),
        tools=build_tools(sandbox, recorder, settings.timeout_s),
        system_prompt=SOLVER_PROMPT,
        backend=backend,
    )
    return agent, recorder
