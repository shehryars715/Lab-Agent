"""The command line.

`eval` is the first command here because it is the one that cannot live as an
example script: an eval you have to remember how to invoke is an eval nobody
runs, and an eval nobody runs is worse than no eval, because it looks like
coverage. `run` belongs here too and is still in examples/solve_lab.py; moving
it is a separate job with its own flags to get right.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from labsagent.config import PROJECT_ROOT, load_settings

app = typer.Typer(
    add_completion=False,
    help="Lab manual in, submission out.",
    no_args_is_help=True,
)

DEFAULT_EVAL_ROOT = PROJECT_ROOT / "build" / "evals"


@app.callback()
def _root() -> None:
    """Keep subcommand dispatch even while `eval` is the only command.

    With a single registered command and no callback, typer collapses the app
    into that command -- so `labsagent eval` parses "eval" as a stray argument
    and fails. The callback makes it a group, which is also the shape `run`
    will slot into.
    """


@app.command("eval")
def evaluate(
    samples: int = typer.Option(
        1,
        "--samples",
        "-n",
        min=1,
        help=(
            "Runs per case. temperature=0 is NOT deterministic here, so one pass is an "
            "anecdote -- use 3 or more before trusting a comparison."
        ),
    ),
    case: list[str] = typer.Option(
        None, "--case", "-c", help="Run only these cases (repeatable)."
    ),
    real: Path = typer.Option(
        None,
        "--real",
        help="Score a directory of actual .docx manuals instead. No goldens, so `ran` only.",
    ),
    out: Path = typer.Option(
        None, "--out", help="Write the JSON report here (default: build/evals/report.json)."
    ),
    no_explain: bool = typer.Option(
        False, "--no-explain", help="Skip the explanation pass while evaluating."
    ),
    list_cases: bool = typer.Option(
        False, "--list", help="Show the cases and exit without spending anything."
    ),
) -> None:
    """Run the eval set and report pass rate and cost per lab."""
    from labsagent.agent.build import build_explainer, build_model
    from labsagent.capture.rendered import RenderedBackend
    from labsagent.evals.cases import build_cases, real_cases
    from labsagent.evals.harness import run_sample
    from labsagent.evals.report import render_findings, render_table, summarize, write_json

    fixtures_dir = DEFAULT_EVAL_ROOT / "manuals"
    cases = (
        real_cases(real)
        if real is not None
        else build_cases(fixtures_dir, only=list(case) if case else None)
    )

    if not cases:
        typer.echo("no cases matched", err=True)
        raise typer.Exit(code=2)

    if list_cases:
        for item in cases:
            typer.echo(f"{item.name:14} {item.task_count} task(s)  {item.note}")
        return

    settings = load_settings()
    if not settings.configured:
        typer.echo("DEEPSEEK_API_KEY not set (expected in .env)", err=True)
        raise typer.Exit(code=1)

    total_runs = len(cases) * samples
    typer.echo(
        f"{len(cases)} case(s) x {samples} sample(s) = {total_runs} pipeline run(s). "
        f"This spends real money."
    )

    screenshots = RenderedBackend(theme="light")
    runs_root = DEFAULT_EVAL_ROOT / "runs"
    results = []

    for item in cases:
        for sample in range(1, samples + 1):
            label = f"{item.name} [{sample}/{samples}]"
            typer.echo(f"  {label} ...", nl=False)
            result = run_sample(
                item,
                sample,
                settings,
                screenshots,
                lambda phase: build_model(settings, phase=phase),
                runs_root,
                explainer_factory=(
                    None if no_explain else (lambda usage: build_explainer(settings, usage))
                ),
            )
            results.append(result)
            state = (
                "ERROR"
                if result.error
                else ("clean" if result.clean else f"{result.ran_count}/{len(result.scores)}")
            )
            typer.echo(f" {state}  ${result.cost_usd:.5f}  {result.duration_s:.0f}s")

    summaries = summarize(results)
    typer.echo("\n" + render_table(summaries))

    findings = render_findings(summaries)
    if findings:
        typer.echo("\nfindings\n" + findings)

    report_path = write_json(summaries, samples, out or (DEFAULT_EVAL_ROOT / "report.json"))
    typer.echo(f"\nreport: {report_path}")

    if any(not s.clean for s in results):
        raise typer.Exit(code=1)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    app()


if __name__ == "__main__":
    main()
