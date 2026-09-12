"""Token and cost accounting.

THE CACHE ARITHMETIC IS THE POINT. `usage_metadata["input_tokens"]` is the TOTAL
input, and cached tokens are a SUBSET of it -- not an extra line item. So:

    billable_miss = input_tokens - cached_tokens

Charging the whole input at the cache-miss rate overstates cost badly. On a
typical agent turn, ~95% of input is a cache hit at 1/50th the price, so the
naive sum can be an order of magnitude too high. Getting this wrong makes you
optimise the wrong thing.

Prices are USD per million tokens, from deepseek.ai/pricing (verified
2026-09-13). A 2x peak surcharge (UTC 01:00-04:00, 06:00-10:00) is announced but
not currently active; if it activates, these become floor prices.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PRICING = {
    # model: (cache_miss_in, cache_hit_in, output) per 1M tokens
    "deepseek-flash": (0.14, 0.0028, 0.28),
    "deepseek-v4-pro": (0.435, 0.003625, 0.87),
}
DEFAULT_MODEL = "deepseek-flash"


@dataclass
class Usage:
    """Accumulated usage for a run, phase, or single call."""

    model: str = DEFAULT_MODEL
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0

    @property
    def billable_input(self) -> int:
        """Input charged at the cache-MISS rate."""
        return max(0, self.input_tokens - self.cached_tokens)

    @property
    def cache_hit_rate(self) -> float:
        return self.cached_tokens / self.input_tokens if self.input_tokens else 0.0

    @property
    def cost_usd(self) -> float:
        miss_rate, hit_rate, out_rate = PRICING.get(self.model, PRICING[DEFAULT_MODEL])
        return (
            self.billable_input / 1e6 * miss_rate
            + self.cached_tokens / 1e6 * hit_rate
            + self.output_tokens / 1e6 * out_rate
        )

    @property
    def cost_if_uncached_usd(self) -> float:
        """What this would have cost with no cache. Shows what caching bought."""
        miss_rate, _, out_rate = PRICING.get(self.model, PRICING[DEFAULT_MODEL])
        return self.input_tokens / 1e6 * miss_rate + self.output_tokens / 1e6 * out_rate

    def add_message(self, message) -> "Usage":
        """Accumulate one AIMessage's usage_metadata."""
        meta = getattr(message, "usage_metadata", None)
        if not meta:
            return self
        self.calls += 1
        self.input_tokens += meta.get("input_tokens", 0)
        self.output_tokens += meta.get("output_tokens", 0)
        details = meta.get("input_token_details") or {}
        self.cached_tokens += details.get("cache_read", 0)
        return self

    def add_messages(self, messages) -> "Usage":
        for message in messages:
            self.add_message(message)
        return self

    def merge(self, other: "Usage") -> "Usage":
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cached_tokens += other.cached_tokens
        return self

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "cache_hit_rate": round(self.cache_hit_rate, 4),
            "cost_usd": round(self.cost_usd, 8),
            "cost_if_uncached_usd": round(self.cost_if_uncached_usd, 8),
        }

    def summary(self) -> str:
        saved = self.cost_if_uncached_usd - self.cost_usd
        return (
            f"{self.calls} calls | "
            f"{self.input_tokens:,} in ({self.cache_hit_rate:.0%} cached) / "
            f"{self.output_tokens:,} out | "
            f"${self.cost_usd:.6f}  (cache saved ${saved:.6f})"
        )


@dataclass
class RunUsage:
    """Per-phase breakdown, so you can see WHERE the money goes."""

    model: str = DEFAULT_MODEL
    phases: dict[str, Usage] = field(default_factory=dict)

    def phase(self, name: str) -> Usage:
        return self.phases.setdefault(name, Usage(model=self.model))

    @property
    def total(self) -> Usage:
        combined = Usage(model=self.model)
        for usage in self.phases.values():
            combined.merge(usage)
        return combined

    def as_dict(self) -> dict:
        return {
            "total": self.total.as_dict(),
            "phases": {name: u.as_dict() for name, u in self.phases.items()},
        }
