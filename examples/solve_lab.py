"""A whole lab, document in -> whatever you asked for out.

    uv run python examples/solve_lab.py [manual.docx] ["only task 3, as a notebook"]

Output used to be three hardcoded files written by a block of code duplicated
(and already drifted) from the web pipeline. Both now go through the same
emitter registry, so a new format is added once and both entry points get it.
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from labsagent import events as ev  # noqa: E402
from labsagent.agent.build import build_explainer, build_model  # noqa: E402
from labsagent.capture.rendered import RenderedBackend  # noqa: E402
from labsagent.config import load_settings  # noqa: E402
from labsagent.ingest.cover import extract_cover_facts  # noqa: E402
from labsagent.emit import DEFAULT_ARTIFACTS, EmitContext, emit_all  # noqa: E402
from labsagent.ingest.labspec import extract_labspec  # noqa: E402
from labsagent.ingest.readers import read_document  # noqa: E402
from labsagent.intent import scope  # noqa: E402
from labsagent.orchestrator import run_lab  # noqa: E402
from labsagent.blocks import blocks_for  # noqa: E402
from labsagent.data import provenance_block  # noqa: E402
from labsagent.data.sources import acquire  # noqa: E402
from labsagent.probe import TokenProbe, probing  # noqa: E402
from labsagent.profile import resolve_profile  # noqa: E402
from labsagent.report.cover import cover_from  # noqa: E402
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

  --data=REF      data for the lab to work on: a file, a https:// link, or a
                  Kaggle dataset like owner/name. Repeatable.
  --want=a,b,c    which formats to produce: docx, ipynb, py, md, zip
  --probe         attribute every token to a bucket and write probe.json
  --no-explain    skip the per-task explanation pass (slightly cheaper)
  --no-notebook   skip the .ipynb export (shorthand for dropping it from --want)
  --no-zip        skip the submission archive (likewise)
  --no-think      disable DeepSeek reasoning in the SOLVER too (cheaper, less capable)
  --think-ingest  re-enable reasoning during task extraction (it is off by default)
  --fat-tools     ship deepagents' full tool surface (the pre-optimisation default)

Any non-flag argument after the manual is treated as a request, in the same
words you would type into the chat: "only task 3", "give me a .py". It is read
during ingest, so it can narrow what gets solved as well as what gets written.

The artifact flags save DISK and wall-clock, not tokens: every exporter runs
locally after the model work is finished and costs nothing to produce. Only
--no-think and --fat-tools change what you are billed.

--data is resolved BEFORE solving and the files are copied into every task
workspace, so the solution opens them by bare name. Whatever the manual itself
names is picked up too, so the flag is for adding or overriding, not for
repeating what the document already says.
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
    request = " ".join(positional[1:])
    want = next(
        (a.split("=", 1)[1].split(",") for a in flags if a.startswith("--want=")), None
    )
    data_refs = [a.split("=", 1)[1] for a in argv if a.startswith("--data=")]
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

    # THE INSTRUMENT, off by default. `probe.py` is what found the 70% saving
    # (binary reads, fat tools, per-phase reasoning) and it had no caller at
    # all -- an instrument nobody can switch on gets measured once and then
    # rots. One flag is the difference between a tool and an artefact.
    probe = TokenProbe(trace=True) if "--probe" in flags else None

    recorder = ev.Recorder()
    emitter = ev.Emitter(ev.console_consumer, recorder)
    usage = RunUsage(model=settings.model_name)
    model = build_model(settings)

    # 1. Ingest -- its own model, because reasoning pays off differently here.
    manual = read_document(manual_path)
    facts = extract_cover_facts([p.text for p in manual.paragraphs])
    reading = extract_labspec(manual, build_model(settings, phase="ingest"), request)
    usage.phase("ingest").merge(reading.usage)

    # Not every upload is a lab, and saying so is an answer rather than a crash.
    if not reading.is_lab:
        print()
        print(f"This does not look like a lab: {reading.what_this_is or 'unrecognised'}")
        print("Nothing to solve. Try a lab manual, or say what you want done with it.")
        return 0

    spec, repairs = reading.spec, reading.repairs
    emitter.emit(ev.IngestFinished(task_count=len(spec.tasks), anchor_repairs=len(repairs)))
    for repair in repairs:
        print(f"    anchor repaired: {repair.task_id} {repair.claimed} -> {repair.corrected}")

    # Narrow to what was asked for, dragging in anything those tasks need to run.
    spec, pulled = scope(spec, reading.intent)
    if pulled:
        print(f"    also solving {', '.join(pulled)} -- the tasks you asked for need them")

    # 2. Data, before any solving. The flag and whatever the manual itself named
    #    are the same kind of thing by the time they get here -- a reference --
    #    so they go through one resolver and arrive as files.
    store = RunStore.create(spec.lab_number)
    refs = data_refs + [r for r in reading.intent.datasets if r not in data_refs]
    datasets = []
    if refs:
        print(f"    data: resolving {len(refs)} reference(s)")
        got = acquire(
            refs,
            store.data_dir,
            max_bytes=settings.max_dataset_bytes,
            kaggle_username=settings.kaggle_username,
            kaggle_key=settings.kaggle_key,
        )
        datasets = got.datasets
        for dataset in datasets:
            print(f"      {dataset.name}  ({dataset.source_note})")
        # Never fatal: a task that does not need the missing file still runs.
        for ref, reason in got.failures:
            print(f"      could not get {ref}: {reason}")

    # 3. Solve every task
    probe_ctx = probing(probe) if probe is not None else contextlib.nullcontext()
    with probe_ctx:
        manifest = run_lab(
            spec, store, settings, RenderedBackend(theme="light"),
            emitter=emitter, usage=usage, model=model,
            # Describing is a separate job from doing, so it gets a separate
            # call with a separate context -- see agent/explainer.py.
            explainer=(
                None if "--no-explain" in flags else build_explainer(settings, usage)
            ),
            datasets=datasets,
        )

    # 4. Emit whatever was asked for. Explicit --want beats the request, which
    #    beats the default; the --no-* flags subtract from whichever won.
    artifacts = list(want or reading.intent.artifacts or DEFAULT_ARTIFACTS)
    if "--no-notebook" in flags:
        artifacts = [a for a in artifacts if a != "ipynb"]
    if "--no-zip" in flags:
        artifacts = [a for a in artifacts if a != "zip"]

    # What the code was run against, stated once. A block, so every emitter
    # renders it in its own idiom -- prose in the report, a comment in the .py.
    note = provenance_block(datasets)
    if note is not None and manifest.outcomes:
        first = manifest.outcomes[0]
        first.blocks = [note, *blocks_for(first)]

    ctx = EmitContext(
        spec=spec,
        outcomes=manifest.outcomes,
        out_dir=store.report_dir,
        profile=profile,
        cover=cover_from(spec, profile, facts),
        manual_path=manual_path,
        anchors=reading.anchors,
    )
    written = emit_all(
        ctx,
        artifacts,
        on_file=lambda em, path: emitter.emit(
            ev.ArtifactWritten(task_id="-", artifact=em.name, path=str(path))
        ),
        on_error=lambda name, exc: print(f"    {name} failed: {type(exc).__name__}: {exc}"),
    )
    archive = next((p for p in written if p.suffix == ".zip"), None)

    store.write_log("events.log", "\n".join(f"{e.at.isoformat()} {e.kind}" for e in recorder.events))

    if probe is not None:
        store.write_log("probe.json", json.dumps(probe.as_dict(), indent=2))
        print()
        print("--- token attribution ---")
        for bucket, value in sorted(
            probe.totals().items(), key=lambda kv: -kv[1]
        ):
            print(f"  {bucket:<22} {value:>10,.0f}")
        print(f"  probe written to {store.dir / 'probe.json'}")

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
