"""Where do the tokens actually go?

A per-phase cost total tells you a run cost $0.04. It does not tell you that
most of it was spent resending tool schemas you never call. To optimise you need
ATTRIBUTION, and attribution needs the WIRE PAYLOAD -- the exact JSON sent to the
API -- not the tidy object graph above it.

THE PROBE POINT. Every OpenAI-compatible LangChain model funnels through
`_get_request_payload`, which returns the literal request dict. Wrapping that one
method captures everything: the system prompt after middleware has rewritten it,
the full tool schemas after binding, and every accumulated message. Higher up you
see intentions; here you see the bill.

THE CALIBRATION TRICK. tiktoken is not DeepSeek's tokenizer, so raw counts are
wrong. But the API reports the true `input_tokens` for that same payload, so one
division gives a scale factor and the whole breakdown is rescaled to match
reality. You get real numbers out of an approximate tokenizer because you
measured the error instead of assuming it away.

Read attribution as SHARE, not as bill: cached input costs ~1/50th of fresh
input, so a huge constant prefix can be cheap while a small varying suffix is
expensive. `uncached_totals` is the one that maps to money.
"""

from __future__ import annotations

import json
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

try:
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")

    def _count(text: str) -> int:
        return len(_ENC.encode(text, disallowed_special=()))

except Exception:  # pragma: no cover - tiktoken is optional
    def _count(text: str) -> int:
        return max(1, len(text) // 4)


def _text_of(value: Any) -> str:
    """Flatten any content shape to text. Content may be str, list of blocks, or None."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_text_of(v) for v in value)
    if isinstance(value, dict):
        return "".join(_text_of(v) for k, v in value.items() if k not in ("type", "index"))
    return str(value)


# Order matters: a cached prefix covers the payload from the front, so buckets
# are charged in the order they physically appear in the request.
BUCKET_ORDER = [
    "system_prompt",
    "tool_schemas",
    "user_task",
    "assistant_reasoning",
    "assistant_text",
    "assistant_tool_calls",
    "tool_results",
]


@dataclass
class CallRecord:
    """One model call, broken down by what filled the context window."""

    seq: int
    phase: str
    task_id: str
    n_messages: int
    buckets: dict[str, int] = field(default_factory=dict)
    tool_schema_sizes: dict[str, int] = field(default_factory=dict)
    tool_result_by_tool: dict[str, int] = field(default_factory=dict)
    raw_total: int = 0
    # filled in from the response
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0

    @property
    def scale(self) -> float:
        """True tokens per estimated token, for this exact payload."""
        if not self.raw_total or not self.input_tokens:
            return 1.0
        return self.input_tokens / self.raw_total

    def calibrated(self) -> dict[str, float]:
        s = self.scale
        return {k: v * s for k, v in self.buckets.items()}


def measure_payload(payload: dict) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    """Split one wire payload into buckets, tool schema sizes, and result sources."""
    buckets: dict[str, int] = defaultdict(int)
    schemas: dict[str, int] = {}
    by_tool: dict[str, int] = defaultdict(int)

    for spec in payload.get("tools") or []:
        fn = spec.get("function", spec)
        name = fn.get("name", "?")
        size = _count(json.dumps(spec))
        schemas[name] = size
        buckets["tool_schemas"] += size

    # tool_call_id -> tool name, so a tool RESULT can be attributed to its tool
    origin: dict[str, str] = {}

    for message in payload.get("messages") or []:
        role = message.get("role", "?")
        body = _count(_text_of(message.get("content")))

        calls = message.get("tool_calls") or []
        call_bytes = _count(json.dumps(calls)) if calls else 0
        for call in calls:
            fn = call.get("function", {})
            origin[call.get("id", "")] = fn.get("name", "?")

        reasoning = _count(_text_of(message.get("reasoning_content")))

        if role == "system":
            buckets["system_prompt"] += body
        elif role == "user":
            buckets["user_task"] += body
        elif role == "assistant":
            buckets["assistant_text"] += body
            buckets["assistant_tool_calls"] += call_bytes
            buckets["assistant_reasoning"] += reasoning
        elif role == "tool":
            buckets["tool_results"] += body
            by_tool[origin.get(message.get("tool_call_id", ""), "?")] += body
        else:
            buckets["other_" + str(role)] += body

    return dict(buckets), schemas, dict(by_tool)


@dataclass
class TokenProbe:
    """Records every wire payload and pairs it with the usage the API reported.

    With `trace=True` it also keeps a de-duplicated log of every tool call and
    result. Attribution says WHICH tool is expensive; the trace says WHY, and
    you almost always need both -- "read_file costs 171k tokens" is not
    actionable until you can see what it kept reading.
    """

    calls: list[CallRecord] = field(default_factory=list)
    phase: str = "solve"
    task_id: str = "-"
    trace: bool = False
    events: list[dict] = field(default_factory=list)
    _seen: set[str] = field(default_factory=set)

    def note(self, phase: str, task_id: str = "-") -> None:
        self.phase, self.task_id = phase, task_id

    def _trace_payload(self, payload: dict) -> None:
        """Log each tool call and result ONCE, the turn it first appears.

        Without de-duplication every accumulated message is re-logged on every
        turn and the trace is quadratic noise.
        """
        origin: dict[str, str] = {}
        for message in payload.get("messages") or []:
            for call in message.get("tool_calls") or []:
                cid = call.get("id", "")
                fn = call.get("function", {})
                origin[cid] = fn.get("name", "?")
                key = "call:" + cid
                if key in self._seen:
                    continue
                self._seen.add(key)
                args = fn.get("arguments", "")
                self.events.append({
                    "turn": len(self.calls),
                    "task": self.task_id,
                    "kind": "call",
                    "tool": fn.get("name", "?"),
                    "arg_tokens": _count(str(args)),
                    "args": str(args)[:220],
                })
            if message.get("role") == "tool":
                cid = message.get("tool_call_id", "")
                key = "result:" + cid
                if key in self._seen:
                    continue
                self._seen.add(key)
                body = _text_of(message.get("content"))
                self.events.append({
                    "turn": len(self.calls),
                    "task": self.task_id,
                    "kind": "result",
                    "tool": origin.get(cid, "?"),
                    "result_tokens": _count(body),
                    "preview": body[:220],
                })

    def record_payload(self, payload: dict) -> CallRecord:
        buckets, schemas, by_tool = measure_payload(payload)
        record = CallRecord(
            seq=len(self.calls) + 1,
            phase=self.phase,
            task_id=self.task_id,
            n_messages=len(payload.get("messages") or []),
            buckets=buckets,
            tool_schema_sizes=schemas,
            tool_result_by_tool=by_tool,
            raw_total=sum(buckets.values()),
        )
        self.calls.append(record)
        if self.trace:
            self._trace_payload(payload)
        return record

    def attach_usage(self, message) -> None:
        """Pair the most recent unattached payload with a response's usage."""
        meta = getattr(message, "usage_metadata", None)
        if not meta:
            return
        for record in reversed(self.calls):
            if record.input_tokens == 0:
                record.input_tokens = meta.get("input_tokens", 0)
                record.output_tokens = meta.get("output_tokens", 0)
                details = meta.get("input_token_details") or {}
                record.cached_tokens = details.get("cache_read", 0)
                return

    # --- reporting ---------------------------------------------------------

    def totals(self) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for record in self.calls:
            for key, value in record.calibrated().items():
                out[key] += value
        return dict(out)

    def uncached_totals(self) -> dict[str, float]:
        """Attribute only the BILLABLE (cache-miss) input.

        A payload's cached prefix is its first N tokens, so buckets are consumed
        in message order until the cached budget runs out; what remains is what
        was actually paid for at the full rate.
        """
        out: dict[str, float] = defaultdict(float)
        for record in self.calls:
            remaining = record.cached_tokens
            cal = record.calibrated()
            keys = BUCKET_ORDER + [k for k in cal if k not in BUCKET_ORDER]
            for key in keys:
                size = cal.get(key, 0.0)
                if size <= 0:
                    continue
                covered = min(size, remaining)
                remaining -= covered
                out[key] += size - covered
        return dict(out)

    def as_dict(self) -> dict:
        schemas: dict[str, int] = {}
        for record in self.calls:
            schemas.update(record.tool_schema_sizes)
        by_tool: dict[str, float] = defaultdict(float)
        for record in self.calls:
            for name, size in record.tool_result_by_tool.items():
                by_tool[name] += size * record.scale
        return {
            "calls": len(self.calls),
            "input_tokens": sum(c.input_tokens for c in self.calls),
            "cached_tokens": sum(c.cached_tokens for c in self.calls),
            "output_tokens": sum(c.output_tokens for c in self.calls),
            "attribution_all_input": {
                k: round(v) for k, v in sorted(self.totals().items(), key=lambda kv: -kv[1])
            },
            "attribution_billable_input": {
                k: round(v)
                for k, v in sorted(self.uncached_totals().items(), key=lambda kv: -kv[1])
            },
            "tool_schema_tokens": dict(sorted(schemas.items(), key=lambda kv: -kv[1])),
            "tool_schema_total_per_turn": sum(schemas.values()),
            "tool_result_tokens_by_tool": {
                k: round(v) for k, v in sorted(by_tool.items(), key=lambda kv: -kv[1])
            },
            "per_call": [
                {
                    "seq": c.seq,
                    "phase": c.phase,
                    "task": c.task_id,
                    "messages": c.n_messages,
                    "input": c.input_tokens,
                    "cached": c.cached_tokens,
                    "output": c.output_tokens,
                    "buckets": {k: round(v) for k, v in c.calibrated().items()},
                }
                for c in self.calls
            ],
        }


@contextmanager
def probing(probe: TokenProbe):
    """Wrap ChatDeepSeek so every request and response passes through the probe.

    Monkeypatching is the right tool here precisely BECAUSE it is temporary and
    reversible: the production path stays untouched, and an instrument you have
    to thread through five layers is an instrument you will not switch on.
    """
    from langchain_deepseek import ChatDeepSeek

    original_payload = ChatDeepSeek._get_request_payload
    original_generate = ChatDeepSeek._generate

    def patched_payload(self, input_, *, stop=None, **kwargs):
        payload = original_payload(self, input_, stop=stop, **kwargs)
        try:
            probe.record_payload(payload)
        except Exception:  # an instrument must never break the run
            pass
        return payload

    def patched_generate(self, messages, stop=None, run_manager=None, **kwargs):
        result = original_generate(
            self, messages, stop=stop, run_manager=run_manager, **kwargs
        )
        try:
            for gen in result.generations:
                probe.attach_usage(gen.message)
        except Exception:
            pass
        return result

    ChatDeepSeek._get_request_payload = patched_payload
    ChatDeepSeek._generate = patched_generate
    try:
        yield probe
    finally:
        ChatDeepSeek._get_request_payload = original_payload
        ChatDeepSeek._generate = original_generate
