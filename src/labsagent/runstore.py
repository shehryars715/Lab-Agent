"""Run directories and the manifest.

THE MANIFEST IS THE SOURCE OF TRUTH, not the filesystem. "Does task2.py exist?"
is a bad completion check -- the file may be a half-written attempt, or left
from a previous run. "Does the manifest say task2 passed?" is a good one,
because the manifest is only written after the fact is established.

Three ideas worth taking away:

1. ATOMIC WRITES. json.dump straight to the target means a crash mid-write
   leaves a truncated, unparseable manifest -- you lose the whole run's record
   at exactly the moment you most need it. Write to a temp file in the same
   directory, then os.replace, which is atomic on both POSIX and Windows.
   The same pattern protects any file you cannot afford to find half-written.

2. SERIALISATION BOUNDARIES. dataclasses.asdict looks convenient and then
   chokes: Path and datetime are not JSON types. Every persistence layer has a
   boundary where rich in-memory types become dumb portable ones. Make that
   boundary explicit and reversible rather than discovering it in a traceback.

3. RESUMABILITY IS A PROPERTY OF STATE, NOT OF CODE. Because outcomes are
   recorded per task, a resumed run simply skips tasks the manifest already
   marks done. No special resume path, no replay logic.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from labsagent.models import LabSpec, RunManifest, Task, TaskOutcome, Transcript

MANIFEST_NAME = "manifest.json"
DEFAULT_ROOT = Path("runs")


# --- serialisation boundary ------------------------------------------------


def _task_to_dict(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "statement": task.statement,
        "sample_inputs": list(task.sample_inputs),
        "sample_output": task.sample_output,
        "wants_explanation": task.wants_explanation,
        "instruction": task.instruction,
    }


def _task_from_dict(data: dict[str, Any]) -> Task:
    return Task(
        id=data["id"],
        title=data["title"],
        statement=data["statement"],
        sample_inputs=list(data.get("sample_inputs", [])),
        sample_output=data.get("sample_output"),
        wants_explanation=data.get("wants_explanation", False),
        instruction=data.get("instruction", ""),
    )


def _spec_to_dict(spec: LabSpec) -> dict[str, Any]:
    return {
        "lab_number": spec.lab_number,
        "title": spec.title,
        "course": spec.course,
        "tasks": [_task_to_dict(t) for t in spec.tasks],
        "skipped_images": list(spec.skipped_images),
    }


def _spec_from_dict(data: dict[str, Any]) -> LabSpec:
    return LabSpec(
        lab_number=data["lab_number"],
        title=data["title"],
        course=data.get("course"),
        tasks=[_task_from_dict(t) for t in data.get("tasks", [])],
        skipped_images=list(data.get("skipped_images", [])),
    )


def _outcome_to_dict(outcome: TaskOutcome) -> dict[str, Any]:
    return {
        "task": _task_to_dict(outcome.task),
        "status": outcome.status,
        # Paths are stored as POSIX-style strings so a run directory stays
        # readable if it is ever moved between machines.
        "code_path": outcome.code_path.as_posix() if outcome.code_path else None,
        "code_text": outcome.code_text,
        "screenshot_paths": [p.as_posix() for p in outcome.screenshot_paths],
        "figure_paths": [p.as_posix() for p in outcome.figure_paths],
        "transcript": (
            {
                "command": outcome.transcript.command,
                "lines": list(outcome.transcript.lines),
                "prompt": outcome.transcript.prompt,
            }
            if outcome.transcript
            else None
        ),
        "explanation": outcome.explanation,
        "attempts": outcome.attempts,
        "error": outcome.error,
    }


def _outcome_from_dict(data: dict[str, Any]) -> TaskOutcome:
    raw = data.get("transcript")
    return TaskOutcome(
        task=_task_from_dict(data["task"]),
        status=data["status"],
        code_path=Path(data["code_path"]) if data.get("code_path") else None,
        code_text=data.get("code_text", ""),
        screenshot_paths=[Path(p) for p in data.get("screenshot_paths", [])],
        figure_paths=[Path(p) for p in data.get("figure_paths", [])],
        transcript=(
            Transcript(command=raw["command"], lines=raw["lines"], prompt=raw["prompt"])
            if raw
            else None
        ),
        explanation=data.get("explanation"),
        attempts=data.get("attempts", 0),
        error=data.get("error"),
    )


def manifest_to_dict(manifest: RunManifest) -> dict[str, Any]:
    return {
        "run_id": manifest.run_id,
        "started_at": manifest.started_at.isoformat(),
        "spec": _spec_to_dict(manifest.spec),
        "outcomes": [_outcome_to_dict(o) for o in manifest.outcomes],
        "token_usage": dict(manifest.token_usage),
        "cost_usd": manifest.cost_usd,
        "anchors": dict(manifest.anchors),
    }


def manifest_from_dict(data: dict[str, Any]) -> RunManifest:
    return RunManifest(
        run_id=data["run_id"],
        started_at=datetime.fromisoformat(data["started_at"]),
        spec=_spec_from_dict(data["spec"]),
        outcomes=[_outcome_from_dict(o) for o in data.get("outcomes", [])],
        token_usage=dict(data.get("token_usage", {})),
        cost_usd=data.get("cost_usd", 0.0),
        anchors={str(k): int(v) for k, v in (data.get("anchors") or {}).items()},
    )


# --- the store --------------------------------------------------------------


def atomic_write_text(path: Path, text: str) -> None:
    """Write so the file is never observed half-written.

    A crash during a plain write leaves a truncated file. Writing to a temp file
    in the SAME directory (so it is on the same filesystem) and then replacing
    means a reader sees either the old file or the new one, never a fragment.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)  # atomic on POSIX and Windows
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@dataclass
class RunStore:
    """One run's directory tree."""

    dir: Path

    @property
    def workspace(self) -> Path:
        return self.dir / "workspace"

    @property
    def code_dir(self) -> Path:
        return self.dir / "code"

    @property
    def shots_dir(self) -> Path:
        return self.dir / "screenshots"

    @property
    def report_dir(self) -> Path:
        return self.dir / "report"

    @property
    def logs_dir(self) -> Path:
        return self.dir / "logs"

    @property
    def manifest_path(self) -> Path:
        return self.dir / MANIFEST_NAME

    @classmethod
    def create(
        cls,
        lab_number: str,
        root: Path = DEFAULT_ROOT,
        now: datetime | None = None,
    ) -> "RunStore":
        stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
        store = cls(dir=Path(root) / f"{stamp}_lab{lab_number}")
        for sub in (
            store.dir,
            store.workspace,
            store.code_dir,
            store.shots_dir,
            store.report_dir,
            store.logs_dir,
        ):
            sub.mkdir(parents=True, exist_ok=True)
        return store

    @classmethod
    def open(cls, path: Path) -> "RunStore":
        store = cls(dir=Path(path))
        if not store.manifest_path.exists():
            raise FileNotFoundError(f"no {MANIFEST_NAME} in {path}")
        return store

    @classmethod
    def latest(cls, root: Path = DEFAULT_ROOT) -> "RunStore | None":
        root = Path(root)
        if not root.is_dir():
            return None
        runs = sorted(
            (d for d in root.iterdir() if d.is_dir() and (d / MANIFEST_NAME).exists()),
            key=lambda d: d.name,
        )
        return cls(dir=runs[-1]) if runs else None

    @property
    def run_id(self) -> str:
        return self.dir.name

    def save(self, manifest: RunManifest) -> Path:
        atomic_write_text(
            self.manifest_path,
            json.dumps(manifest_to_dict(manifest), indent=2, ensure_ascii=False),
        )
        return self.manifest_path

    def load(self) -> RunManifest:
        return manifest_from_dict(
            json.loads(self.manifest_path.read_text(encoding="utf-8"))
        )

    def write_log(self, name: str, text: str) -> Path:
        path = self.logs_dir / name
        atomic_write_text(path, text)
        return path


def completed_task_ids(manifest: RunManifest) -> set[str]:
    """Tasks already settled. Resuming skips these -- no replay logic needed.

    A 'failed' task counts as settled: it consumed its retry budget and is
    recorded in the report. Re-running it would spend money to reach the same
    place. Delete its outcome from the manifest to force a retry.
    """
    return {o.task.id for o in manifest.outcomes if o.status in ("passed", "failed")}


def pending_tasks(manifest: RunManifest) -> list[Task]:
    done = completed_task_ids(manifest)
    return [t for t in manifest.spec.tasks if t.id not in done]
