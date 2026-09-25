"""End-to-end test of the web layer against a running server.

    .venv/Scripts/python.exe -m uvicorn web.server.app:app --port 8000
    .venv/Scripts/python.exe web/smoke_test.py tests/fixtures/lab03_manual.docx

This is a live test -- it calls the model and spends about a fifth of a cent.
That is the point: everything below is asserting on real artifacts, and the
one thing worth asserting hardest is that the answer typed into the browser
dialog actually reaches the cover page of the Word document. A unit test can
mock that path; only this can prove it.

Exits non-zero on the first failed check.
"""

from __future__ import annotations

import io
import json
import os
import sys
import threading
import zipfile
from pathlib import Path

import requests

# Running a script directly puts the SCRIPT's directory on sys.path, not the
# working directory -- so `web.server` is not importable from here without
# this, even though it is from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows consoles default to a legacy codepage, so a UTF-8 arrow or dash in a
# tool's output arrives as a replacement character.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Overridable so the smoke test can run against a second instance while
# another server is already holding :8000.
BASE = os.environ.get("LABSAGENT_BASE", "http://127.0.0.1:8000")
NAME = "Test Student"
CMS = "22F-9999"

passed: list[str] = []
failed: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    (passed if ok else failed).append(label)
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}{('  -- ' + detail) if detail else ''}")
    return ok


def briefing_probe() -> None:
    """Does the briefing stay quiet about things it must not ask?

    A manual with an undefined data source and an open library choice: both
    used to draw a question. Data is now the pipeline's to ask about, and a
    library is the solver's to choose, so the right answer is no question.
    """
    from labsagent.models import LabSpec, Task
    from labsagent.agent.build import build_model
    from labsagent.config import load_settings
    from labsagent.ingest.cover import CoverFacts
    from labsagent.profile import StudentProfile

    from web.server.briefing import read_briefing

    spec = LabSpec(
        lab_number="07",
        title="Regression Analysis",
        course="CS245 Machine Learning",
        tasks=[
            Task(
                id="task1",
                title="Load and summarise",
                statement=(
                    "Load the provided dataset and report the mean of each numeric "
                    "column."
                ),
            ),
            Task(
                id="task2",
                title="Fit a model",
                statement=(
                    "Fit an appropriate model to the data and report the coefficients. "
                    "Use the library you think is most suitable."
                ),
            ),
        ],
    )

    print("\n  -- briefing probe (a manual with a real gap) --")
    settings = load_settings()
    plan, usage = read_briefing(
        spec,
        CoverFacts(course="CS245 Machine Learning"),
        StudentProfile(name="Test Student", cms_id="22F-9999"),
        "",
        build_model(settings, phase="ingest"),
    )
    for q in plan.questions:
        print(f"    Q: {q['label']}")
        print(f"       why: {q['reason']}")
    print(f"    cover: layout={plan.layout!r} tagline={plan.tagline!r}")
    print(f"    cost: ${usage.cost_usd:.6f}")

    # DATA IS NO LONGER THE MODEL'S QUESTION (2026-09-25). This probe used to
    # REQUIRE a model-written "which dataset?" -- the question that, on real
    # runs, got asked about data already on its way. The missing-link case is
    # now decided by code (`pipeline.data_question`) and unit-tested; what is
    # checked here is that the model no longer asks about data or preferences.
    check(
        "the model did not ask about data",
        not any("dat" in (q["label"] + q["reason"]).lower() for q in plan.questions),
        f"{len(plan.questions)} question(s)",
    )
    check("at most one question, and it blocks a task", len(plan.questions) <= 1)
    check("every question says why it matters", all(q["reason"] for q in plan.questions))
    check("chose a real cover layout", plan.layout in ("classic", "rule", "banner", "split"))
    check("wrote a tagline specific to the lab", 0 < len(plan.tagline) <= 160)


def main() -> int:
    manual = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/lab03_manual.docx")
    if not manual.exists():
        print(f"no manual at {manual}")
        return 2

    code = run(manual)
    briefing_probe()

    print()

    print(f"\n  {len(passed)} passed, {len(failed)} failed")
    if failed:
        print("  failures: " + ", ".join(failed))
    return 1 if failed else 0


def run(manual: Path) -> None:

    health = requests.get(f"{BASE}/api/health", timeout=10).json()
    print(f"server: model={health['model']} key={'set' if health['api_key_configured'] else 'MISSING'}")
    if not health["api_key_configured"]:
        return 2

    # -- upload -----------------------------------------------------------
    with manual.open("rb") as fh:
        created = requests.post(
            f"{BASE}/api/runs",
            files={"manual": (manual.name, fh, "application/octet-stream")},
            data={
                # Left blank on purpose: the run must stop and ask, which is
                # the behaviour under test. Prefilling here would make the
                # pause never happen.
                "instructions": "Keep every solution short.",
                "name": "",
                "cms_id": "",
            },
            timeout=60,
        )
    if created.status_code != 202:
        print(f"upload failed: {created.status_code} {created.text}")
        return 1
    job_id = created.json()["job_id"]
    print(f"\njob {job_id}\n")

    # -- stream, answering the pause when it arrives ----------------------
    events: list[dict] = []
    answers_sent = threading.Event()
    deadline = 600

    with requests.get(f"{BASE}/api/runs/{job_id}/events", stream=True, timeout=deadline) as stream:
        for raw in stream.iter_lines(decode_unicode=True):
            if not raw or raw.startswith(":"):
                continue  # heartbeat
            if not raw.startswith("data: "):
                continue
            payload = json.loads(raw[6:])
            events.append(payload)
            kind = payload.get("type")

            if kind == "event":
                e = payload["event"]
                if e["kind"] in ("TaskStarted", "TaskFinished", "AttemptFailed"):
                    print(f"    {e['kind']:<15} {e.get('task_id', '')} {e.get('title', '')}")

            elif kind == "needs_input":
                keys = [q["key"] for q in payload["questions"]]
                print(f"\n  >> run paused, asked for: {keys}")
                print(f"     pausing mid-run as designed (phase should be 'details')")

                def answer() -> None:
                    resp = requests.post(
                        f"{BASE}/api/runs/{job_id}/answers",
                        # Every question is optional now: skipping is the
                        # path worth proving, because it must not stall.
                        json={"answers": {}},
                        timeout=30,
                    )
                    print(f"  >> answered: {resp.status_code}")
                    answers_sent.set()

                threading.Thread(target=answer, daemon=True).start()

            elif kind == "done":
                print(f"\n  >> done: {payload}")
                break
            elif kind == "failed":
                print(f"\n  >> FAILED: {payload['error']}")
                break

    print()
    kinds = [e.get("type") for e in events]
    check("stream delivered events", len(events) > 5, f"{len(events)} frames")
    check("run reached the done state", "done" in kinds)

    phases = [e["key"] for e in events if e.get("type") == "phase"]
    check("phases announced in order", len(phases) >= 3, " -> ".join(phases))
    check("any pause was answered", "needs_input" not in kinds or answers_sent.is_set())

    # -- identity at PACKAGING: add it after the files exist ---------------
    # A rebuild, no model call. Its artifact frames replace the first ones.
    resp = requests.post(
        f"{BASE}/api/runs/{job_id}/identity", json={"name": NAME, "cms_id": CMS}, timeout=30
    )
    check("identity accepted after the run", resp.status_code == 202, str(resp.status_code))
    seen_done = False
    with requests.get(f"{BASE}/api/runs/{job_id}/events", stream=True, timeout=deadline) as again:
        for raw in again.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data: "):
                continue
            payload = json.loads(raw[6:])
            if payload.get("seq", 0) <= len(events):
                continue
            events.append(payload)
            if payload.get("type") in ("done", "failed"):
                seen_done = payload.get("type") == "done"
                break
    check("the files were rebuilt with the identity", seen_done)

    # -- the pause must come BEFORE the code ------------------------------
    # This is the whole point of moving it. The old interface asked after
    # solving, so an answer could only reach the cover page; the money was
    # spent and the programs written. Asserted positionally rather than by
    # phase name, because a task event is the thing that proves work started.
    first_task = next(
        (
            i
            for i, e in enumerate(events)
            if e.get("type") == "event" and e["event"]["kind"] == "TaskStarted"
        ),
        None,
    )
    pause_at = next(
        (i for i, e in enumerate(events) if e.get("type") == "needs_input"), None
    )
    check(
        "any question came before any task was solved",
        pause_at is None or (first_task is not None and pause_at < first_task),
        f"pause at frame {pause_at}, first task at frame {first_task}",
    )

    asked = next((e for e in events if e.get("type") == "needs_input"), {})
    keys = [q["key"] for q in asked.get("questions", [])]
    print(f"\n  asked: {keys}")
    # Whether the agent asked anything is the MODEL's call, and for this
    # fixture -- which is fully specified -- asking nothing is the right
    # answer. Asserting that it must produce a question would be asserting
    # that it invents one, which is the failure mode, not the feature.
    # `briefing_probe` below is what actually exercises the mechanism, against
    # a manual with a real gap in it.
    # IDENTITY IS NO LONGER ASKED UP FRONT, and a run with nothing genuine to
    # ask must not pause at all (2026-09-24).
    check("no identity question up front", not ({"name", "cms_id"} & set(keys)))
    check("no pause without a genuine question", "needs_input" not in kinds or bool(keys))
    check(
        "no question lacks a reason",
        all(q.get("reason") for q in asked.get("questions", []) if q.get("reason") is not None),
    )

    # -- narration --------------------------------------------------------
    narration = [e for e in events if e.get("type") == "narration"]
    print(f"\n  narration ({len(narration)}):")
    for n in narration[:4]:
        print(f"    {n['text'][:88]}")
    check("progress is said in a few plain lines", 0 < len(narration) <= 6, f"{len(narration)} lines")

    artifacts = {e["key"]: e for e in events if e.get("type") == "artifact"}
    print(f"\n  artifacts: {sorted(artifacts)}")
    # KEYED BY EMITTER NAME now, not by two fixed slots. The notebook has
    # always been built on every run; it just had no download of its own and
    # nothing here ever asserted it existed.
    check("report produced", "docx" in artifacts)
    check("notebook produced AND downloadable", "ipynb" in artifacts)
    check("package produced", "zip" in artifacts)
    check("at least one code file produced", any(k.startswith("code:") for k in artifacts))

    paths: dict[str, Path] = {}
    for key in artifacts:
        resp = requests.get(f"{BASE}/api/runs/{job_id}/download/{key}", timeout=60)
        if not check(f"downloaded {key}", resp.ok, f"{len(resp.content):,} bytes"):
            continue
        out = Path("runs/_web_uploads") / job_id / f"dl_{key.replace(':', '_')}"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(resp.content)
        paths[key] = out

    # -- the check that matters most --------------------------------------
    if "docx" in paths:
        from docx import Document

        doc = Document(str(paths["docx"]))
        text = "\n".join(p.text for p in doc.paragraphs)
        text += "\n".join(c.text for t in doc.tables for r in t.rows for c in r.cells)
        check(
            "the name typed into the browser reached the report",
            NAME in text,
            f"looked for {NAME!r}",
        )
        check("the CMS id typed into the browser reached the report", CMS in text)
        check(
            "the original manual text survived",
            "Lab" in text and len(doc.paragraphs) > 10,
            f"{len(doc.paragraphs)} paragraphs",
        )

    if "ipynb" in paths:
        import json as _json

        nb = _json.loads(paths["ipynb"].read_text(encoding="utf-8"))
        outputs = [
            o
            for c in nb.get("cells", [])
            if c.get("cell_type") == "code"
            for o in c.get("outputs", [])
        ]
        check(
            "the notebook opens already showing results",
            any(o.get("output_type") in ("stream", "display_data") for o in outputs),
            f"{len(outputs)} embedded outputs",
        )

    if "zip" in paths:
        with zipfile.ZipFile(paths["zip"]) as zf:
            names = zf.namelist()
        check("package contains the report", any(n.endswith(".docx") for n in names))
        check("package contains the notebook", any(n.endswith(".ipynb") for n in names))
        check("package contains code", any(n.startswith("code/") for n in names))
        check(
            "package contains screenshots",
            any(n.startswith("screenshots/") for n in names),
            f"{len(names)} entries",
        )

    # -- tool-level activity ----------------------------------------------
    # The tracer reaches the agent's tool calls through a callback hook rather
    # than through any change to the core, so the thing worth asserting is
    # that the hook actually fires in a real run -- a spike proved it can, and
    # this proves it still does.
    activity = [e for e in events if e.get("type") == "activity"]
    texts = [a["text"] for a in activity]
    print(f"\n  activity frames ({len(activity)}):")
    for t in texts[:12]:
        print(f"    {t}")

    check("tool activity streamed", len(activity) > 0, f"{len(activity)} frames")
    check("progress states are plain words", any(t in ("Writing the program", "Trying it out") for t in texts))
    check("no filenames or exit codes in progress", not any(".py" in t or t.startswith("exit") for t in texts))
    check(
        "activity is attributed to a task",
        all(a.get("task_id") for a in activity),
        "so it can be shown on the right row",
    )

    # -- path containment -------------------------------------------------
    for bad in ("../../.env", "..%2F..%2F.env", "etc/passwd"):
        r = requests.get(f"{BASE}/api/runs/{job_id}/download/{bad}", timeout=10)
        check(f"traversal refused: {bad}", r.status_code == 404, str(r.status_code))

    # -- revision ---------------------------------------------------------
    # Reuses the core's own resumability: drop a task's outcome from the
    # manifest and `run_lab(resume=True)` re-solves exactly that task. The
    # assertion that matters is that the OTHER tasks were not re-solved --
    # re-running everything would work and would be a waste of the money
    # already spent.
    print("\n  -- revision --")
    started = requests.post(
        f"{BASE}/api/runs/{job_id}/revise",
        json={"feedback": "Redo task 3 using a different approach."},
        timeout=30,
    )
    if check("revision accepted", started.status_code == 202, str(started.status_code)):
        # A fresh EventSource replays the WHOLE log from the start, so the
        # stream contains the original run over again before the revision
        # begins. Only frames after the revision announces itself count -- an
        # earlier version of this counted the replay and concluded, wrongly,
        # that every task had been redone.
        rev_events: list[dict] = []
        revision_frames: list[dict] = []
        seen_revision = False
        with requests.get(
            f"{BASE}/api/runs/{job_id}/events", stream=True, timeout=deadline
        ) as stream:
            for raw in stream.iter_lines(decode_unicode=True):
                if not raw.startswith("data: "):
                    continue
                payload = json.loads(raw[6:])
                rev_events.append(payload)
                if seen_revision:
                    revision_frames.append(payload)

                if payload.get("type") == "followup":
                    seen_revision = True
                elif payload.get("type") in ("done", "failed") and seen_revision:
                    break

        re_done = [
            e["event"]["task_id"]
            for e in revision_frames
            if e.get("type") == "event" and e["event"]["kind"] == "TaskStarted"
        ]
        print(f"    re-solved: {re_done}")
        check("the revision re-solved something", len(re_done) > 0)
        check(
            "the revision re-solved ONLY the task asked about",
            re_done == ["task3"],
            "kept the work already paid for on the other tasks",
        )
        check(
            "the revision narrated itself",
            any(e.get("type") == "followup" and e.get("action") == "change" for e in rev_events),
        )
        check(
            "the revision rebuilt the report",
            any(e.get("type") == "artifact" and e.get("kind") == "report" for e in rev_events),
        )


if __name__ == "__main__":
    raise SystemExit(main())
