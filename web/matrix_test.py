"""Matrix sweep: every input container x every deliverable, against a live server.

    uv run python -m uvicorn web.server.app:app --port 8123
    LABSAGENT_BASE=http://127.0.0.1:8123 uv run python web/matrix_test.py

WHY THIS EXISTS ALONGSIDE `smoke_test.py`. The smoke test proves one run works
end to end, in depth -- the pause, the tracer, path traversal, revision. This
proves the SHAPE of the thing: that any readable document can go in, that any
requested combination of files comes out, and that asking for one task gives
you one task. Those are different questions, and the unit suite can answer
neither.

It earned its place immediately. On its first full run it found three bugs that
261 unit tests could not, all of them the same mistake -- a value travelling on
two channels, or the wrong one:

  * anchors were minted for every input format, so a .txt upload arrived
    carrying line indices that look exactly like Word paragraph indices, and
    the DOCX emitter tried to annotate plain text as a document
  * the raw request still reached the solver as well as the emitters, so
    "give me the report, the notebook, a markdown copy and a zip" made the
    agent write a 5,996-byte program that generated those files itself, in
    answer to "read two integers and print their sum"
  * solver steering was concatenated onto `Task.statement`, and every exporter
    PRINTS `statement`, so a submitted .py opened with "As you work: before
    your first tool call, say in ONE short sentence..."

None of those are visible to a unit test, because each needs a real document of
a particular type to travel the whole way through a real pipeline.

MODULE LEVEL IS DELIBERATELY IMPORT-SAFE. pytest's default `python_files`
includes `*_test.py`, so this name matches and pytest will import the module if
it ever walks this directory. Reading `sys.argv` or creating a directory out
here would turn that import into a collection error, which does not skip the
file -- it interrupts the entire suite. `pyproject.toml` also pins
`testpaths = ["tests"]`, so both guards would have to fail at once.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

NAME, CMS = "Test Student", "22F-9999"
IDENTITY = {"name": NAME, "cms_id": CMS, "section": "A", "program": "BSCS"}

#: Per-scenario ceiling. A three-task solve is ~25s; this is generous enough to
#: absorb a retry without letting a wedged run hold the sweep open all day.
DEADLINE = 420


class Scenario:
    """One row of the matrix, and what it is allowed to come back as.

    Every `expect_*` is optional. Leaving one unset means "anything is fine" --
    used where the interesting property is something else, so that a change in
    the agent's own taste does not fail a test about scoping.
    """

    def __init__(self, sid, title, file, ask, expect_artifacts=None, expect_tasks=None,
                 expect_lab=True, expect_pulled=None, expect_status=202,
                 pause_artifacts=None, note=""):
        self.sid, self.title, self.file, self.ask = sid, title, file, ask
        self.expect_artifacts = expect_artifacts
        self.expect_tasks = expect_tasks
        self.expect_lab = expect_lab
        self.expect_pulled = expect_pulled
        self.expect_status = expect_status
        self.pause_artifacts = pause_artifacts
        self.note = note
        self.result: dict = {}


SCENARIOS = [
    Scenario("01", "docx lab, no request -> the old default", "lab03_manual.docx", "",
             expect_artifacts={"docx", "ipynb", "zip"}, expect_tasks=3,
             note="must still behave as it did before formats were requestable"),
    Scenario("02", "docx lab, scope to one task", "lab03_manual.docx",
             "Only task 1 please.", expect_tasks=1,
             note="formats unasserted: with no preference stated, the agent picks"),
    Scenario("03", "docx lab -> a bare .py", "lab03_manual.docx",
             "Only task 2. Give me a plain .py file, nothing else.",
             expect_artifacts={"py"}, expect_tasks=1,
             note="the 'rigid doc+zip' complaint, directly"),
    Scenario("04", "docx lab -> markdown", "lab03_manual.docx",
             "Just task 3, as a markdown write-up.",
             expect_artifacts={"md"}, expect_tasks=1),
    Scenario("05", "docx lab -> two formats, no archive", "lab03_manual.docx",
             "Only task 1. I want the notebook and the python script, and no zip.",
             expect_artifacts={"ipynb", "py"}, expect_tasks=1),
    Scenario("06", "notebook lab in, dependency pulled in", "ds311.ipynb",
             "only task 2", expect_artifacts={"ipynb"}, expect_tasks=2,
             expect_pulled=["task1"],
             note="task 2 needs task 1; the lab says 'submit only the .ipynb'"),
    Scenario("07", "markdown lab in", "lab05.md",
             "Only task 1. A notebook is fine.", expect_tasks=1),
    Scenario("08", "plain .txt lab in -> Word report with NO anchors", "lab05.txt",
             "Only task 2. I need a Word report.", expect_artifacts={"docx"},
             expect_tasks=1, note="the fresh-document DOCX path; found bug 1"),
    Scenario("09", "PDF lab in", "lab05.pdf",
             "Only the first task, as a notebook.", expect_tasks=1,
             note="PDF extraction kerns 'Task' into 'T ask' -- deliberately"),
    Scenario("10", "no file at all -- the lab is pasted", None,
             "Here is my lab, solve task 1 only and give me a .py:\n\n"
             "Task 1: Read two integers and print their product.\n"
             "Task 2: Read a list of five numbers and print the largest.",
             expect_artifacts={"py"}, expect_tasks=1),
    Scenario("11", "a CV is not a lab", "cv.docx", "",
             expect_lab=False, note="used to hallucinate tasks and then solve them"),
    Scenario("12", "an invoice is not a lab", "invoice.md", "", expect_lab=False),
    Scenario("13", "every format at once", "lab03_manual.docx",
             "Only task 1, but give me all of it: the word report, the notebook, "
             "the python file, a markdown copy and a zip.",
             expect_artifacts={"docx", "ipynb", "py", "md", "zip"}, expect_tasks=1,
             note="found bug 2 -- the agent wrote a file-generator instead"),
    # The pause no longer carries a formats field (2026-09-24): formats come
    # from the request, and can be changed afterwards by a free rebuild.
    Scenario("14", "formats named in the request", "lab03_manual.docx",
             "Only task 2, as markdown and a python file.",
             expect_artifacts={"md", "py"}, expect_tasks=1,
             note="the agent proposes, the student redirects"),
    Scenario("15", "an unreadable file type is refused politely", "photo.xyz", "",
             expect_status=415),
    Scenario("16", "nothing at all is refused politely", None, "",
             expect_status=422),
    Scenario("17", "a real code instruction still reaches the solver",
             "lab03_manual.docx",
             "Only task 1, as a .py. Use a while loop and do not use f-strings.",
             expect_artifacts={"py"}, expect_tasks=1,
             note="the other half of bug 2: notes must survive the split"),
]


def post(base: str, fixtures: Path, manual: Path, sc: Scenario):
    data = {"instructions": sc.ask, **IDENTITY}
    if sc.file is None:
        return requests.post(f"{base}/api/runs", data=data, timeout=60)
    path = manual if sc.file == manual.name else fixtures / sc.file
    with path.open("rb") as fh:
        return requests.post(
            f"{base}/api/runs",
            files={"manual": (path.name, fh, "application/octet-stream")},
            data=data,
            timeout=60,
        )


def answer_for(sc: Scenario, questions: list[dict]) -> dict:
    """Reply the way a browser would: identity from storage, formats confirmed.

    Echoing back the `artifacts` field's pre-filled value is what "confirm the
    agent's proposal" means; `pause_artifacts` is what "redirect it" means.
    """
    out = {}
    for q in questions:
        key = q["key"]
        if key in IDENTITY:
            out[key] = IDENTITY[key]
        elif key == "artifacts":
            out[key] = sc.pause_artifacts or q.get("value", "")
        else:
            out[key] = "Use a sensible default and keep it short."
    return out


def drive(base: str, fixtures: Path, manual: Path, out_dir: Path, sc: Scenario) -> dict:
    started = time.time()
    created = post(base, fixtures, manual, sc)
    if created.status_code != 202:
        ctype = created.headers.get("content-type", "")
        body = created.json().get("detail") if ctype.startswith("application/json") \
            else created.text
        return {"status": created.status_code, "message": str(body)[:200],
                "seconds": round(time.time() - started, 1)}

    job_id = created.json()["job_id"]
    res = {"status": 202, "job": job_id, "artifacts": {}, "tasks": None, "pulled": [],
           "cost": None, "error": None, "emit_failed": [], "proposed": None, "asked": []}

    with requests.get(f"{base}/api/runs/{job_id}/events", stream=True,
                      timeout=DEADLINE) as stream:
        for raw in stream.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data: "):
                continue
            p = json.loads(raw[6:])
            kind = p.get("type")

            if kind == "spec":
                res["tasks"] = p.get("task_count")
                res["pulled"] = p.get("pulled_in") or []
            elif kind == "questions_ready":
                res["proposed"] = p.get("proposed_artifacts")
            elif kind == "needs_input":
                res["asked"] = [q["key"] for q in p["questions"]]
                body = answer_for(sc, p["questions"])
                # Answered from another thread: the stream we are reading is
                # what tells us the answer landed, so blocking here to POST
                # would be waiting on ourselves.
                threading.Thread(
                    target=lambda: requests.post(f"{base}/api/runs/{job_id}/answers",
                                                 json={"answers": body}, timeout=30),
                    daemon=True,
                ).start()
            elif kind == "artifact":
                res["artifacts"][p["key"]] = {"kind": p.get("kind"),
                                              "filename": p.get("filename"),
                                              "bytes": p.get("bytes")}
            elif kind == "emit_failed":
                res["emit_failed"].append(f'{p.get("format")}: {p.get("reason")}')
            elif kind == "done":
                # `finish` splats the summary into the frame rather than
                # nesting it, so these are top-level keys.
                res["cost"] = p.get("cost_usd")
                res["passed"] = p.get("passed")
                break
            elif kind == "failed":
                res["error"] = p.get("error")
                break

    res["seconds"] = round(time.time() - started, 1)

    # Fetch everything that was registered. "Registered" and "downloadable" are
    # different claims, and only the second one is any use to a student.
    for key, meta in res["artifacts"].items():
        r = requests.get(f"{base}/api/runs/{job_id}/download/{key}", timeout=120)
        meta["downloaded"] = r.ok and len(r.content) > 0
        if r.ok:
            (out_dir / f"{sc.sid}_{key.replace(':', '_')}_{meta['filename']}"
             ).write_bytes(r.content)
    return res


def judge(sc: Scenario) -> list[str]:
    """Failure strings for one scenario. Empty means it passed."""
    r, bad = sc.result, []

    if sc.expect_status not in (200, 202):
        if r.get("status") != sc.expect_status:
            bad.append(f"expected HTTP {sc.expect_status}, got {r.get('status')}")
        return bad
    if r.get("status") != 202:
        bad.append(f"upload rejected: {r.get('status')} {r.get('message', '')}")
        return bad

    if not sc.expect_lab:
        if r.get("error") is None:
            bad.append("expected a 'not a lab' answer, but the run proceeded")
        elif "lab" not in (r["error"] or "").lower():
            bad.append(f"unhelpful message: {r['error'][:80]}")
        if r.get("artifacts"):
            bad.append("produced artifacts for a non-lab")
        return bad

    if r.get("error"):
        bad.append(f"run failed: {r['error'][:120]}")
        return bad
    if sc.expect_tasks is not None and r.get("tasks") != sc.expect_tasks:
        bad.append(f"expected {sc.expect_tasks} task(s), got {r.get('tasks')}")
    if sc.expect_pulled is not None and sorted(r.get("pulled") or []) != sorted(sc.expect_pulled):
        bad.append(f"expected pulled-in {sc.expect_pulled}, got {r.get('pulled')}")
    if sc.expect_artifacts is not None:
        got = {k for k in r.get("artifacts", {}) if not k.startswith("code:")}
        if got != sc.expect_artifacts:
            bad.append(f"expected artifacts {sorted(sc.expect_artifacts)}, got {sorted(got)}")
    if r.get("emit_failed"):
        bad.append(f"emitters failed: {r['emit_failed']}")
    for key, meta in r.get("artifacts", {}).items():
        if not meta.get("downloaded"):
            bad.append(f"{key} registered but not downloadable")
    return bad


def leak_check(out_dir: Path) -> list[str]:
    """No deliverable may contain the solver's steering.

    This is bug 3's regression, and it has to look at the FILES: the leak was
    in `Task.statement`, which every exporter prints, so nothing short of
    reading what came out would have caught it.
    """
    needles = ("Additional instructions from the student", "This is a revision.", "Never write report files")
    leaked = []
    for path in out_dir.iterdir():
        if path.suffix not in (".py", ".md", ".ipynb"):
            continue
        body = path.read_text(encoding="utf-8", errors="replace").lower()
        if any(n in body for n in needles):
            leaked.append(path.name)
    return leaked


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from web.fixtures.make_inputs import build, lab_manual

    base = os.environ.get("LABSAGENT_BASE", "http://127.0.0.1:8000")
    fixtures = build(Path(os.environ.get("LABSAGENT_FIXTURES", "web/fixtures/inputs")))
    manual = lab_manual()
    out_dir = fixtures.parent / "matrix_out"
    out_dir.mkdir(parents=True, exist_ok=True)

    only = sys.argv[1].split(",") if len(sys.argv) > 1 else None

    try:
        health = requests.get(f"{base}/api/health", timeout=10).json()
    except requests.RequestException as exc:
        print(f"no server at {base}: {exc}")
        print("start one with: uv run python -m uvicorn web.server.app:app --port 8123")
        return 2
    if not health.get("api_key_configured"):
        print("DEEPSEEK_API_KEY is not set on the server")
        return 2
    print(f"server: {base}  model={health['model']}")

    results = []
    for sc in SCENARIOS:
        if only and sc.sid not in only:
            continue
        print(f"\n=== [{sc.sid}] {sc.title}")
        print(f"    input: {sc.file or '(none, pasted)'}   ask: {sc.ask[:68]!r}")
        try:
            sc.result = drive(base, fixtures, manual, out_dir, sc)
        except Exception as exc:  # noqa: BLE001 -- one bad scenario must not end the sweep
            sc.result = {"status": "EXC", "message": f"{type(exc).__name__}: {exc}"}
        bad = judge(sc)
        results.append((sc, bad))
        arts = sorted(k for k in sc.result.get("artifacts", {}) if not k.startswith("code:"))
        print(f"    -> tasks={sc.result.get('tasks')} artifacts={arts} "
              f"pulled={sc.result.get('pulled')} "
              f"cost=${sc.result.get('cost') or 0:.5f} {sc.result.get('seconds')}s")
        if sc.result.get("error"):
            print(f"    -> message: {sc.result['error'][:140]}")
        print("    " + ("PASS" if not bad else "FAIL: " + "; ".join(bad)))

    leaked = leak_check(out_dir)
    print(f"\n{'=' * 70}")
    print("prompt leak in deliverables: " + (", ".join(leaked) if leaked else "none"))

    (out_dir / "results.json").write_text(
        json.dumps([{"id": s.sid, "title": s.title, "file": s.file, "ask": s.ask,
                     "failures": b, **s.result} for s, b in results],
                   indent=2, default=str), encoding="utf-8")

    npass = sum(1 for _, b in results if not b)
    cost = sum(s.result.get("cost") or 0 for s, _ in results)
    print(f"{npass}/{len(results)} scenarios passed   total cost ${cost:.4f}")
    for s, b in results:
        if b:
            print(f"  [{s.sid}] {s.title}: {'; '.join(b)}")
    return 0 if (npass == len(results) and not leaked) else 1


if __name__ == "__main__":
    raise SystemExit(main())
