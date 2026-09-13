"""Past runs, read back from disk.

A run costs about a fifth of a cent and two minutes, and `runs/` already holds
more than ten of them. Re-downloading an artifact you already paid for is
strictly better than re-running to get it, which makes this the half of the
tool that a once-only interface was missing.

TWO RULES, both about not trusting anything that came off disk.

1. THE RUN IS IDENTIFIED BY A DIRECTORY THAT EXISTS. `run_dir()` takes a
   caller-supplied string, lists the real run directories, and returns a match
   from that list or nothing. `../../.env` is not refused; it is simply not in
   the listing. Same reasoning as the artifact whitelist in `app.py`.

2. THE ARTIFACT LIST IS BUILT FROM THE FILESYSTEM, NOT FROM THE MANIFEST.
   `manifest.json` records `code_path` for each task, and serving those paths
   would mean a manifest that has been edited -- by hand, by a future version,
   by anything -- decides what this server hands out. So the manifest is used
   only for LABELS, and every path served is re-derived from a directory
   listing rooted at the run. Data on disk is input; it is not authority.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .pipeline import RUNS_ROOT


def run_dir(run_id: str) -> Path | None:
    """The matching run directory, or None. Never a path join."""
    if not RUNS_ROOT.is_dir():
        return None
    for candidate in RUNS_ROOT.iterdir():
        if candidate.is_dir() and candidate.name == run_id and candidate.name != "_web_uploads":
            return candidate
    return None


def _manifest(directory: Path) -> dict[str, Any]:
    path = directory / "manifest.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A truncated or hand-edited manifest costs you the labels, not the
        # downloads -- the files are still there and still servable.
        return {}


def _labels(manifest: dict[str, Any]) -> dict[str, str]:
    """task id -> title, for naming the code files. Labels only."""
    return {
        outcome["task"]["id"]: outcome["task"].get("title") or outcome["task"]["id"]
        for outcome in manifest.get("outcomes", [])
        if outcome.get("task", {}).get("id")
    }


def artifacts_for(directory: Path) -> list[dict[str, Any]]:
    """The downloadable files in one run, in the order the UI should show them.

    Each entry carries its own `path`, and that is safe precisely because of
    where it came from: every path below is the result of a `glob()` on a
    directory that `run_dir()` matched against a real listing. The returned
    list IS the whitelist -- there is no second lookup that could disagree
    with it, and no caller-side reconstruction to get out of step.
    """
    manifest = _manifest(directory)
    labels = _labels(manifest)
    report_dir = directory / "report"
    code_dir = directory / "code"
    found: list[dict[str, Any]] = []

    def add(key: str, path: Path, label: str, kind: str) -> None:
        if path.is_file():
            found.append(
                {
                    "key": key,
                    "label": label,
                    "kind": kind,
                    "filename": path.name,
                    "bytes": path.stat().st_size,
                    "path": path,
                }
            )

    if report_dir.is_dir():
        for path in sorted(report_dir.glob("*.docx")):
            add("report", path, "Report", "report")
        # One zip per run. `max` rather than `next` so a stray extra archive
        # resolves deterministically instead of by directory order.
        zips = sorted(report_dir.glob("*.zip"))
        if zips:
            add("package", zips[-1], "Complete package", "package")

    if code_dir.is_dir():
        for path in sorted(code_dir.glob("*.py")):
            add(f"code:{path.stem}", path, labels.get(path.stem, path.stem), "code")

    return found


def summarise(directory: Path) -> dict[str, Any]:
    """One row for the run list."""
    manifest = _manifest(directory)
    outcomes = manifest.get("outcomes", [])
    spec = manifest.get("spec", {})
    passed = sum(1 for o in outcomes if o.get("status") == "passed")

    return {
        "run_id": directory.name,
        "lab_number": spec.get("lab_number", ""),
        "title": spec.get("title", ""),
        "started_at": manifest.get("started_at", ""),
        "total": len(outcomes),
        "passed": passed,
        "failed": len(outcomes) - passed,
        "cost_usd": manifest.get("cost_usd", 0.0),
    }


def list_runs(limit: int = 12) -> list[dict[str, Any]]:
    """The most recent runs, newest first.

    Sorted by directory name rather than by mtime: `RunStore` names them
    `%Y%m%d-%H%M%S_labNN`, so the name IS the timestamp and it survives being
    copied or restored from a backup, where an mtime would not.
    """
    if not RUNS_ROOT.is_dir():
        return []
    directories = sorted(
        (
            d
            for d in RUNS_ROOT.iterdir()
            if d.is_dir() and d.name != "_web_uploads" and (d / "manifest.json").exists()
        ),
        key=lambda d: d.name,
        reverse=True,
    )
    return [summarise(d) for d in directories[:limit]]
