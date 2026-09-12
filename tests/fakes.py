"""A chat model that returns a fixed script of replies.

Why this exists: an agent loop is a control-flow structure, and control flow is
much easier to understand -- and to test -- when the "thinking" is deterministic.
This lets us watch the loop turn with zero tokens, zero latency, and zero
network, and it becomes a permanent test fixture so the suite never needs an API
key.

This is a general technique, not a trick for this project: when testing a system
that calls a non-deterministic dependency, script the dependency.
"""

from __future__ import annotations

from typing import Any, Sequence

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class ScriptedModel(BaseChatModel):
    """Replays `script` one AIMessage per invocation."""

    script: list[AIMessage]
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedModel":
        # The real client would attach tool schemas to each request. We accept
        # and ignore them: the script already decides what gets "called".
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        idx = min(self.calls, len(self.script) - 1)
        object.__setattr__(self, "calls", self.calls + 1)
        return ChatResult(generations=[ChatGeneration(message=self.script[idx])])
