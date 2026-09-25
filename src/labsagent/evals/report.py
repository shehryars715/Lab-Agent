"""Turn sample results into something you can act on.

REPORT THE SPREAD, NOT JUST THE MEAN. The whole reason this takes samples is
that the system is stochastic, and a table of means would throw away exactly
the information the sampling was for. A case that costs $0.001 every time and a
case that costs $0.001 on average -- because it cost $0.0002 twice and $0.0026
once -- are different systems, and only the second one is going to surprise you
in front of a deadline.

So cost and attempts print as mean with a range whenever more than one sample
ran, and any case whose samples disagree about PASSING is called out. A flaky
case is a finding, not noise to be averaged away.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from labsagent.evals.harness import SampleResult


@dataclass
class CaseSummary:
    name: str
    samples: list[SampleResult] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.samples)

    @property
    def clean_count(self) -> int:
        return sum(1 for s in self.samples if s.clean)

    @property
    def ran(self) -> tuple[int, int]:
        return (
            sum(s.ran_count for s in self.samples),
            sum(len(s.scores) for s in self.samples),
        )

    @property
    def matched(self) -> tuple[int, int]:
        return (
            sum(s.matched_count for s in self.samples),
            sum(s.scored_count for s in self.samples),
        )

    @property
    def costs(self) -> list[float]:
        return [s.cost_usd for s in self.samples]

    @property
    def durations(self) -> list[float]:
        return [s.duration_s for s in self.samples]

    @property
    def attempts(self) -> list[int]:
        return [s.attempts_total for s in self.samples]

    @property
    def runs(self) -> list[int]:
        return [s.runs_total for s in self.samples]

    @property
    def flaky(self) -> bool:
        """Samples of the same case disagreed about whether it passed."""
        return self.count > 1 and 0 < self.clean_count < self.count

    @property
    def errors(self) -> list[str]:
        return [s.error for s in self.samples if s.error]

    @property
    def findings(self) -> list[str]:
        out = []
        for sample in self.samples:
            if sample.tasks_found != sample.tasks_expected:
                out.append(
                    f"sample {sample.sample}: ingest found {sample.tasks_found} tasks, "
                    f"expected {sample.tasks_expected}"
                )
            for score in sample.scores:
                if score.detail:
                    out.append(f"sample {sample.sample} task{score.position}: {score.detail}")
        return out


def summarize(results: list[SampleResult]) -> list[CaseSummary]:
    by_case: dict[str, CaseSummary] = {}
    for result in results:
        by_case.setdefault(result.case, CaseSummary(name=result.case)).samples.append(result)
    return list(by_case.values())


def _spread(values: list[float], fmt: str) -> str:
    """Mean, plus the range when samples actually disagreed."""
    if not values:
        return "-"
    mean = sum(values) / len(values)
    low, high = min(values), max(values)
    text = format(mean, fmt)
    if len(values) > 1 and high - low > (abs(mean) * 0.02):
        text += f"  [{format(low, fmt)}-{format(high, fmt)}]"
    return text


def _ratio(pair: tuple[int, int]) -> str:
    hit, total = pair
    return "-" if total == 0 else f"{hit}/{total}"


def render_table(summaries: list[CaseSummary]) -> str:
    rows = [
        (
            "case",
            "n",
            "clean",
            "ran",
            "matched",
            "attempts",
            "runs",
            "cost $",
            "secs",
        )
    ]
    for summary in sorted(summaries, key=lambda s: s.name):
        rows.append(
            (
                summary.name + (" *" if summary.flaky else ""),
                str(summary.count),
                f"{summary.clean_count}/{summary.count}",
                _ratio(summary.ran),
                _ratio(summary.matched),
                _spread([float(a) for a in summary.attempts], ".1f"),
                _spread([float(r) for r in summary.runs], ".1f"),
                _spread(summary.costs, ".5f"),
                _spread(summary.durations, ".0f"),
            )
        )

    total_samples = sum(s.count for s in summaries)
    total_clean = sum(s.clean_count for s in summaries)
    ran = (
        sum(s.ran[0] for s in summaries),
        sum(s.ran[1] for s in summaries),
    )
    matched = (
        sum(s.matched[0] for s in summaries),
        sum(s.matched[1] for s in summaries),
    )
    all_costs = [c for s in summaries for c in s.costs]
    rows.append(
        (
            "TOTAL",
            str(total_samples),
            f"{total_clean}/{total_samples}",
            _ratio(ran),
            _ratio(matched),
            "",
            str(sum(r for s in summaries for r in s.runs)),
            f"{sum(all_costs):.5f}",
            f"{sum(d for s in summaries for d in s.durations):.0f}",
        )
    )

    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines = []
    for index, row in enumerate(rows):
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
        if index == 0 or index == len(rows) - 2:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def render_findings(summaries: list[CaseSummary]) -> str:
    """Everything the table had to compress away."""
    blocks = []
    for summary in sorted(summaries, key=lambda s: s.name):
        lines = summary.findings + [f"error: {e}" for e in summary.errors]
        if summary.flaky:
            lines.insert(
                0, f"FLAKY: passed {summary.clean_count} of {summary.count} samples"
            )
        if lines:
            blocks.append(summary.name + "\n" + "\n".join(f"    {line}" for line in lines))
    return "\n".join(blocks)


def as_dict(summaries: list[CaseSummary], samples: int) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "samples_per_case": samples,
        "cases": {
            summary.name: {
                "clean": summary.clean_count,
                "samples": summary.count,
                "flaky": summary.flaky,
                "ran": list(summary.ran),
                "matched": list(summary.matched),
                "cost_usd": summary.costs,
                "duration_s": [round(d, 2) for d in summary.durations],
                "attempts": summary.attempts,
                "runs": summary.runs,
                "findings": summary.findings,
                "errors": summary.errors,
                "usage": [s.usage for s in summary.samples],
            }
            for summary in sorted(summaries, key=lambda s: s.name)
        },
        "total_cost_usd": sum(c for s in summaries for c in s.costs),
    }


def write_json(summaries: list[CaseSummary], samples: int, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(as_dict(summaries, samples), indent=2), encoding="utf-8"
    )
    return out_path
