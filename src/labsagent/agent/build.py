"""Wire the solver agent.

The filesystem backend is rooted at the SANDBOX workspace, never the project
root. deepagents' own docs are blunt about this: FilesystemBackend "grants
agents direct filesystem read/write access... Agents can read any accessible
file, including secrets (API keys, credentials, .env files)". Our .env holds a
live key, so the root_dir is the security boundary, and tests/test_agent.py
verifies it actually holds.

TOOL SURFACE IS A BUDGET, NOT A BUFFET. A harness that ships ten tools charges
you for ten schemas on every turn of every task, whether or not the model calls
them. deepagents' defaults are built for open-ended agents that explore a
repository; ours writes one file and runs it. Trimming to what the job needs
cut the fixed per-call prefix by 58% -- and, more importantly, removed the tools
whose RESULTS were expensive (a `glob` that lists a workspace, an `ls` the agent
runs to orient itself).

`read_file` cannot be dropped: FilesystemMiddleware requires it. So it is made
harmless instead, by guarding what it can return -- see confined.py.
"""

from __future__ import annotations

from deepagents import (
    FilesystemMiddleware,
    GeneralPurposeSubagentProfile,
    HarnessProfileConfig,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends import FilesystemBackend
from langchain_deepseek import ChatDeepSeek

from labsagent.agent.confined import ConfinedBackend
from labsagent.agent.explainer import Explainer
from labsagent.agent.prompts import SOLVER_PROMPT
from labsagent.agent.tools import TaskRecorder, build_tools
from labsagent.config import Settings
from labsagent.sandbox.base import Sandbox

# write_file is what we use; read_file is mandatory for FilesystemMiddleware.
LEAN_FS_TOOLS = ["write_file", "read_file"]

_profiles_registered: set[str] = set()


def _suppress_general_purpose_subagent(model_name: str) -> None:
    """Drop the `task` tool (418 tokens/turn) by disabling the subagent it offers.

    deepagents adds a general-purpose subagent unless told otherwise, and the
    `task` tool exists to delegate to it. We have no subagents, so the tool is
    pure overhead. Registration is global and cumulative in deepagents, hence
    the guard -- registering twice merges profiles rather than replacing them.
    """
    key = f"deepseek:{model_name}"
    if key in _profiles_registered:
        return
    register_harness_profile(
        key,
        HarnessProfileConfig(
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)
        ),
    )
    _profiles_registered.add(key)


def build_model(settings: Settings, phase: str = "solve"):
    """One model per phase, because reasoning is worth different amounts in each.

    `phase="ingest"` is structured extraction -- copying text into a schema --
    where thinking buys nothing measurable. `phase="explain"` is description:
    the program already exists and already works, and the job is to say what it
    does. `phase="solve"` writes and debugs programs, where thinking does pay.

    A LOOKUP RATHER THAN A CHAIN OF IFS. With one special case an `if` is
    honest; the second is where it stops being a special case and becomes a
    table. Written as a chain, adding "explain" means editing a conditional and
    hoping the fallthrough is still right -- written as a dict, a new phase is a
    new row.
    """
    effort = {
        "ingest": settings.ingest_reasoning_effort,
        "explain": settings.explain_reasoning_effort,
    }.get(phase, settings.reasoning_effort)
    kwargs = {"reasoning_effort": effort} if effort else {}
    return ChatDeepSeek(
        model=settings.model_name,
        temperature=settings.temperature,
        api_key=settings.deepseek_api_key,
        **kwargs,
    )


def build_explainer(settings: Settings, usage=None, model=None) -> Explainer:
    """The report-prose writer, with the model choice made in exactly one place.

    Every composition root wants the same thing here, and the interesting part
    -- that this phase runs WITHOUT reasoning -- should not have to be
    remembered separately by the CLI, the web layer and the eval harness.
    """
    return Explainer(
        model=model if model is not None else build_model(settings, phase="explain"),
        usage=usage,
    )


def build_solver(sandbox: Sandbox, settings: Settings, model=None):
    """Return (agent, recorder). One agent per task -- fresh context each time."""
    recorder = TaskRecorder()
    # root_dir alone is not a boundary: it honours absolute paths. See confined.py.
    backend = ConfinedBackend(
        FilesystemBackend(root_dir=sandbox.workdir, virtual_mode=False),
        sandbox.workdir,
    )

    extra = {}
    if settings.lean_tools:
        _suppress_general_purpose_subagent(settings.model_name)
        # Passing our own FilesystemMiddleware REPLACES the default one:
        # deepagents merges custom middleware by `.name`.
        extra["middleware"] = [
            FilesystemMiddleware(backend=backend, tools=LEAN_FS_TOOLS)
        ]

    agent = create_deep_agent(
        model=model if model is not None else build_model(settings),
        tools=build_tools(sandbox, recorder, settings.timeout_s),
        system_prompt=SOLVER_PROMPT,
        backend=backend,
        **extra,
    )
    return agent, recorder
