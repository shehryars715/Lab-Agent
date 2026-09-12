"""Phase 3: a whole lab, manual in -> submission zip out.

    uv run python examples/solve_lab.py [manual.docx]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from labsagent import events as ev  # noqa: E402
from labsagent.agent.build import build_model  # noqa: E402
from labsagent.capture.rendered import RenderedBackend  # noqa: E402
from labsagent.config import load_settings  # noqa: E402
from labsagent.ingest.docx_reader import read_manual  # noqa: E402
from labsagent.ingest.labspec import extract_labspec  # noqa: E402
from labsagent.orchestrator import run_lab  # noqa: E402
from labsagent.package.zipper import build_submission  # noqa: E402
from labsagent.report.docx_builder import annotate_manual  # noqa: E402
from labsagent.runstore import RunStore  # noqa: E402
from labsagent.usage import RunUsage  # noqa: E402

ROLL_NO = "22F-1234"

def ensure_fixture(path: Path) -> Path:
    """Generate the sample manual if absent.

    *.docx is git-ignored, so a fresh clone has no fixture. It is fully
    reproducible from tests/fixtures/make_manual.py, so generate rather than
    commit a binary.
    """
    if path.exists():
        return path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "fixtures"))
    from make_manual import build

    print(f"generating sample manual -> {path}")
    return build(path)



def main() -> int:
    manual_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ensure_fixture(
        Path("tests/fixtures/lab03_manual.docx")
    )
    settings = load_settings()
    if not settings.configured:
        print("DEEPSEEK_API_KEY not set (expected in .env)")
        return 1

    recorder = ev.Recorder()
    emitter = ev.Emitter(ev.console_consumer, recorder)
    usage = RunUsage(model=settings.model_name)
    model = build_model(settings)

    # 1. Ingest
    manual = read_manual(manual_path)
    spec, repairs, ingest_usage = extract_labspec(manual, model)
    usage.phase("ingest").merge(ingest_usage)
    emitter.emit(ev.IngestFinished(task_count=len(spec.tasks), anchor_repairs=len(repairs)))
    for repair in repairs:
        print(f"    anchor repaired: {repair.task_id} {repair.claimed} -> {repair.corrected}")

    # 2. Solve every task
    store = RunStore.create(spec.lab_number)
    manifest = run_lab(
        spec, store, settings, RenderedBackend(theme="light"),
        emitter=emitter, usage=usage, model=model,
    )

    # 3. Annotate the manual in place, then package
    report = annotate_manual(
        manual_path, store.report_dir / f"Lab{spec.lab_number}_Report.docx", manifest.outcomes
    )
    archive = build_submission(
        store.report_dir / f"Lab{spec.lab_number}_{ROLL_NO}.zip", report, manifest.outcomes
    )
    emitter.emit(ev.ArtifactWritten(task_id="-", artifact="report", path=str(report)))
    emitter.emit(ev.ArtifactWritten(task_id="-", artifact="archive", path=str(archive)))

    store.write_log("events.log", "\n".join(f"{e.at.isoformat()} {e.kind}" for e in recorder.events))

    print("\n--- usage by phase ---")
    for name, phase in usage.phases.items():
        print(f"  {name:<8} {phase.summary()}")
    print(f"  {'TOTAL':<8} {usage.total.summary()}")
    print(f"\nrun dir: {store.dir}")
    print(f"archive: {archive} ({archive.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
