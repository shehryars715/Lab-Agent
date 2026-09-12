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
from labsagent.ingest.cover import extract_cover_facts  # noqa: E402
from labsagent.ingest.docx_reader import read_manual  # noqa: E402
from labsagent.ingest.labspec import extract_labspec  # noqa: E402
from labsagent.orchestrator import run_lab  # noqa: E402
from labsagent.package.notebook import write_notebook  # noqa: E402
from labsagent.package.zipper import build_submission  # noqa: E402
from labsagent.profile import resolve_profile  # noqa: E402
from labsagent.report.cover import cover_from  # noqa: E402
from labsagent.report.docx_builder import annotate_manual  # noqa: E402
from labsagent.runstore import RunStore  # noqa: E402
from labsagent.usage import RunUsage  # noqa: E402

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



USAGE = """usage: solve_lab.py [manual.docx] [options]

  --no-notebook   skip the .ipynb export
  --no-zip        skip the submission archive
  --no-think      disable DeepSeek reasoning in the SOLVER too (cheaper, less capable)
  --think-ingest  re-enable reasoning during task extraction (it is off by default)
  --fat-tools     ship deepagents' full tool surface (the pre-optimisation default)

The artifact flags save DISK and wall-clock, not tokens: the notebook and zip are
built locally after the model work is finished and cost nothing to produce. Only
--no-think and --fat-tools change what you are billed.
"""


def main() -> int:
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print(USAGE)
        return 0
    flags = {a for a in argv if a.startswith("--")}
    positional = [a for a in argv if not a.startswith("--")]

    manual_path = Path(positional[0]) if positional else ensure_fixture(
        Path("tests/fixtures/lab03_manual.docx")
    )
    settings = load_settings()
    if "--no-think" in flags:
        settings.reasoning_effort = "none"
    if "--think-ingest" in flags:
        settings.ingest_reasoning_effort = None
    if "--fat-tools" in flags:
        settings.lean_tools = False
    if not settings.configured:
        print("DEEPSEEK_API_KEY not set (expected in .env)")
        return 1

    # Identity first: ask before spending money, not after. Only name and CMS
    # are asked -- the manual supplies course, section, instructor and date.
    profile, _ = resolve_profile()

    recorder = ev.Recorder()
    emitter = ev.Emitter(ev.console_consumer, recorder)
    usage = RunUsage(model=settings.model_name)
    model = build_model(settings)

    # 1. Ingest -- its own model, because reasoning pays off differently here.
    manual = read_manual(manual_path)
    facts = extract_cover_facts([p.text for p in manual.paragraphs])
    spec, repairs, ingest_usage = extract_labspec(
        manual, build_model(settings, phase="ingest")
    )
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
    roll = profile.slug()
    report = annotate_manual(
        manual_path,
        store.report_dir / f"Lab{spec.lab_number}_Report.docx",
        manifest.outcomes,
        cover=cover_from(spec, profile, facts),
    )
    emitter.emit(ev.ArtifactWritten(task_id="-", artifact="report", path=str(report)))

    notebook = None
    if "--no-notebook" not in flags:
        notebook = write_notebook(
            store.report_dir / f"Lab{spec.lab_number}_{roll}.ipynb",
            spec,
            manifest.outcomes,
            student=profile.as_display(),
        )
        emitter.emit(
            ev.ArtifactWritten(task_id="-", artifact="notebook", path=str(notebook))
        )

    archive = None
    if "--no-zip" not in flags:
        archive = build_submission(
            store.report_dir / f"Lab{spec.lab_number}_{roll}.zip", report, manifest.outcomes
        )
        if notebook is not None:
            with __import__("zipfile").ZipFile(archive, "a") as zf:
                zf.write(notebook, arcname=notebook.name)
        emitter.emit(
            ev.ArtifactWritten(task_id="-", artifact="archive", path=str(archive))
        )

    store.write_log("events.log", "\n".join(f"{e.at.isoformat()} {e.kind}" for e in recorder.events))

    print("\n--- usage by phase ---")
    for name, phase in usage.phases.items():
        print(f"  {name:<8} {phase.summary()}")
    print(f"  {'TOTAL':<8} {usage.total.summary()}")
    print(f"\nrun dir: {store.dir}")
    if archive is not None:
        print(f"archive: {archive} ({archive.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
