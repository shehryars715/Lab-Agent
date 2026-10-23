"""Labs that survive a restart (PLAN.md N1): the chat on disk, whose a job is
after a restart, auto-resume once and then a button, and the checkpoint that
lets a solve carry on from the next task.

A "restart" here is a NEW JobRegistry over the same folder -- exactly what a
new process sees: the files, and nothing in memory.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from labsagent import credits
from labsagent.config import Settings
from labsagent.ingest.labspec import Reading
from labsagent.intent import Intent
from labsagent.models import LabSpec, RunManifest, Task, TaskOutcome
from labsagent.runstore import RunStore, manifest_from_dict, manifest_to_dict
from labsagent.usage import RunUsage, Usage
from web.server import durable, metering, pipeline, retention
from web.server.briefing import Plan
from web.server.jobs import EVENTS_FILE, STATE_FILE, Job, JobRegistry

ME = "local"  # auth.LOCAL_USER: the one user of an open server


def _registry(root, owner=ME, adopt=None):
    return JobRegistry(root=root, owner_of=lambda job_id: owner, on_restore=adopt)


def _events(job):
    return job.since(0, timeout_s=0)[0]


def _cut_short(root, kind="run", **detail):
    """A job whose process died part-way: the folder as that process left it."""
    job = _registry(root).create()
    job.begin(kind, **detail)
    job.publish({"type": "accepted"})
    job.phase("reading", "Reading what you sent")
    return job  # never finished


def _checkpointed(runs, *, done, total):
    """A run folder as `run_job` leaves it once solving has started."""
    store = RunStore.create("03", root=runs, suffix="abcdef000000")
    tasks = [Task(id=f"task{i}", title=f"T{i}", statement="Print it.") for i in range(1, total + 1)]
    manifest = RunManifest(
        run_id=store.run_id, started_at=datetime.now(timezone.utc), spec=LabSpec("03", "Loops", tasks=tasks)
    )
    manifest.outcomes = [TaskOutcome(task=t, status="passed") for t in tasks[:done]]
    manifest.context = {"manual": "", "resume": {"answered": False}}
    store.save(manifest)
    return store


@pytest.fixture
def launched(monkeypatch):
    """Resumes that would start a worker, recorded instead of started."""
    started = []
    monkeypatch.setattr(durable, "launch", lambda job, hold, **k: started.append((job.id, dict(job.state["work"]))))
    monkeypatch.setattr(metering, "reserve", lambda *a, **k: metering.UNMETERED)
    return started


# -- 1. the log is a file now ---------------------------------------------------------


def test_the_chat_survives_a_restart_and_keeps_counting(tmp_path):
    job = _registry(tmp_path).create()
    job.begin("run", instructions="Task 1")
    job.publish({"type": "accepted"})
    job.phase("reading", "Reading")
    job.finish(summary={"passed": 1, "total": 1})

    again = _registry(tmp_path).get(job.id)
    assert again is not job and again.owner == ME
    assert [e["type"] for e in _events(again)] == ["accepted", "phase", "done"]
    assert again.status == "done" and again.summary["passed"] == 1
    # The browser's cursor still means the same thing: the next event is 4.
    again.reopen()
    again.publish({"type": "narration", "text": "hi"})
    last = (tmp_path / job.id / EVENTS_FILE).read_text(encoding="utf-8").splitlines()[-1]
    assert again.last_seq == 4 and json.loads(last)["seq"] == 4


def test_a_torn_line_is_dropped_and_a_lost_one_keeps_its_number(tmp_path):
    folder = tmp_path / "abcdef012345"
    folder.mkdir()
    (folder / EVENTS_FILE).write_text(
        '{"type": "accepted", "seq": 1}\n{"type": "narration", "text": "x", "seq": 3}\n{"type": "do',
        encoding="utf-8",
    )
    job = _registry(tmp_path).get("abcdef012345")
    log = _events(job)
    assert [e["type"] for e in log[:3]] == ["accepted", "gap", "narration"]
    assert [e["seq"] for e in log] == list(range(1, len(log) + 1))
    # No record of work in flight, and no end: said, never left hanging.
    assert job.status == "failed" and log[-1]["type"] == "failed"


@pytest.mark.parametrize("bad", ["../outside", "..\\outside", "ABCDEF012345", "abcdef01234", "abcdef0123456"])
def test_an_id_that_is_not_a_job_never_becomes_a_path(tmp_path, bad):
    # A real log where a traversal would land, and one for each near-miss id:
    # without the check, every one of these would be read.
    for name in ("outside", "ABCDEF012345", "abcdef01234", "abcdef0123456"):
        (tmp_path / name).mkdir()
        (tmp_path / name / EVENTS_FILE).write_text('{"type": "done", "seq": 1}\n', encoding="utf-8")
    asked = []
    registry = JobRegistry(root=tmp_path / "jobs", owner_of=lambda j: asked.append(j) or ME)
    (tmp_path / "jobs").mkdir()
    for name in ("ABCDEF012345", "abcdef01234", "abcdef0123456"):
        (tmp_path / name).rename(tmp_path / "jobs" / name)
    assert registry.get(bad) is None
    assert asked == []  # not even the database is asked


def test_whose_it_is_is_asked_before_the_log_is_read(tmp_path, monkeypatch):
    job = _cut_short(tmp_path)
    monkeypatch.setattr(Job, "restore", classmethod(lambda *a: pytest.fail("a stranger's log was read")))
    stranger = _registry(tmp_path, owner=None)
    assert stranger.get(job.id) is None and stranger.active() == []


def test_the_download_whitelist_comes_back_but_only_inside_the_run_folder(tmp_path):
    uploads = tmp_path / "_web_uploads"
    run = tmp_path / "20261023-100000_lab03_abcdef000000"
    (run / "report").mkdir(parents=True)
    report = run / "report" / "Lab03.docx"
    report.write_bytes(b"PK")
    (tmp_path / "secret.txt").write_text("not yours", encoding="utf-8")

    job = _registry(uploads).create()
    job.begin("run")
    job.attach_run(run)
    job.register("docx", report, label="Word report", kind="report")
    job.finish(summary={})
    state_file = uploads / job.id / STATE_FILE
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["artifacts"] == {"docx": "report/Lab03.docx"} and state["work"] is None

    state["artifacts"]["evil"] = "../secret.txt"  # the disk is data, not authority
    state_file.write_text(json.dumps(state), encoding="utf-8")
    back = _registry(uploads).get(job.id)
    assert back.run_dir == run and back.artifacts == {"docx": report.resolve()}


# -- 2. auto-resume once, then a button --------------------------------------------------


def test_interrupted_work_starts_again_once_by_itself(tmp_path, launched):
    job = _cut_short(tmp_path, instructions="Task 1: print hi", manual="lab.md")
    back = _registry(tmp_path, adopt=durable.adopt).get(job.id)
    assert back.status == "running" and back.state["work"]["tries"] == 1
    # What doing it again needs rode along.
    assert back.state["work"]["instructions"] == "Task 1: print hi" and back.state["work"]["manual"] == "lab.md"
    said = [e for e in _events(back) if e["type"] == "interrupted"]
    assert len(said) == 1 and said[0]["resuming"] and said[0]["stage"] == "reading"
    assert "reading your lab again" in said[0]["text"] and "Nothing was charged" in said[0]["text"]
    assert [job_id for job_id, _ in launched] == [job.id]


def test_the_same_work_cut_short_twice_waits_for_the_button(tmp_path, launched):
    job = _cut_short(tmp_path)
    _registry(tmp_path, adopt=durable.adopt).get(job.id)  # resumed once, never finishes
    parked = _registry(tmp_path, adopt=durable.adopt).get(job.id)  # and cut short again
    assert parked.status == "interrupted" and parked.is_terminal()
    assert parked.state["work"] is None and parked.state["pending"]["tries"] == 1
    last = _events(parked)[-1]
    assert last["type"] == "interrupted" and not last["resuming"] and "Press Resume" in last["text"]
    assert len(launched) == 1
    # A third process finds it parked, not in flight, and leaves it be.
    assert _registry(tmp_path, adopt=durable.adopt).get(job.id).status == "interrupted"
    assert len(launched) == 1

    durable.resume(parked, parked.state["pending"], auto=False)  # the button
    assert parked.status == "running" and parked.state["work"]["tries"] == 2
    assert "pending" not in parked.state and _events(parked)[-1]["type"] == "resumed"
    assert len(launched) == 2
    with pytest.raises(HTTPException) as twice:  # a double click
        durable.resume(parked, {"kind": "run"}, auto=False)
    assert twice.value.status_code == 409 and len(launched) == 2


def test_no_credits_after_a_restart_parks_the_lab_with_the_reason(tmp_path, monkeypatch):
    def refuse(*a, **k):
        raise HTTPException(402, "You’ve used this month’s 50 credits. They refill on 1 Nov.")

    monkeypatch.setattr(metering, "reserve", refuse)
    monkeypatch.setattr(durable, "launch", lambda *a, **k: pytest.fail("must not start"))
    job = _cut_short(tmp_path)
    back = _registry(tmp_path, adopt=durable.adopt).get(job.id)
    assert back.status == "interrupted"
    assert "refill on 1 Nov" in _events(back)[-1]["text"]
    assert back.state["pending"]["tries"] == 0  # nothing was started, so no try was spent


def test_a_lab_cut_short_mid_solve_says_where_it_carries_on(tmp_path, launched):
    uploads = tmp_path / "_web_uploads"
    store = _checkpointed(tmp_path, done=2, total=5)
    job = _registry(uploads).create()
    job.begin("run")
    job.attach_run(store.dir)
    job.phase("solving", "Solving each task")
    back = _registry(uploads, adopt=durable.adopt).get(job.id)
    said = next(e for e in _events(back) if e["type"] == "interrupted")
    assert said["stage"] == "solving"
    assert "during task 3 of 5" in said["text"] and "The 2 finished ones are kept" in said["text"]
    assert "isn't charged" in said["text"]


def test_a_change_is_undone_before_it_is_made_again(tmp_path, monkeypatch):
    uploads = tmp_path / "_web_uploads"
    store = _checkpointed(tmp_path, done=2, total=2)
    job = _registry(uploads).create()
    job.attach_run(store.dir)
    started = []
    monkeypatch.setattr(durable.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: started.append(kw)))

    job.begin("followup", feedback="make task 2 use a while loop")
    durable.launch(job, metering.UNMETERED)  # the first time: a snapshot
    # The change got as far as dropping task 2's outcome; then the process died.
    half = store.load()
    half.outcomes = half.outcomes[:1]
    store.save(half)

    job.begin("followup", tries=1, feedback="make task 2 use a while loop")
    durable.launch(job, metering.UNMETERED)  # again: back to the snapshot first
    assert len(store.load().outcomes) == 2
    assert started[-1]["args"][2] == "make task 2 use a while loop"


# -- 3. the checkpoint, and a solve that carries on ------------------------------------------


def test_the_context_is_on_disk_before_the_first_task_is_solved(monkeypatch, tmp_path):
    spec = LabSpec("03", "Loops", tasks=[Task(id="task1", title="Sum", statement="Add two numbers.")])
    reading = Reading(kind="lab", confidence=0.9, what_this_is="a lab", usage=Usage(), spec=spec, intent=Intent())
    monkeypatch.setattr(pipeline, "extract_labspec", lambda *a, **k: reading)
    monkeypatch.setattr(pipeline, "build_model", lambda *a, **k: None)
    monkeypatch.setattr(
        pipeline, "read_briefing", lambda *a, **k: (Plan(questions=[], layout="classic", tagline=""), Usage())
    )
    monkeypatch.setattr(pipeline, "RUNS_ROOT", tmp_path / "runs")
    seen = {}

    def run_lab(spec, store, *a, resume=False, **k):
        seen["resume"], seen["on_disk"] = resume, store.load()
        raise RuntimeError("the process dies here")

    monkeypatch.setattr(pipeline, "run_lab", run_lab)
    manual = tmp_path / "lab.md"
    manual.write_text("# Lab 03\n\nTask 1: Add two numbers.", encoding="utf-8")
    job = Job("abcdef000001")
    pipeline.run_job(job, manual_path=manual, instructions="", profile_seed={}, settings=Settings(deepseek_api_key="x"))

    # A fresh solve is a resume with nothing settled yet: one path for both.
    assert seen["resume"] is True
    saved = seen["on_disk"]
    assert saved.outcomes == [] and saved.presentation
    assert saved.context["resume"]["answered"] is False and "taste" in saved.context["resume"]
    assert durable.checkpoint(job) is not None  # what a new process would find


def test_a_resumed_solve_ends_like_a_fresh_one_and_charges_only_itself(monkeypatch, tmp_path):
    store = _checkpointed(tmp_path, done=1, total=2)
    before = store.load()
    before.usage = {"phases": {"solve": {"calls": 3, "input_tokens": 9000, "output_tokens": 900, "cached_tokens": 0}}}
    store.save(before)

    def run_lab(spec, store, settings, shots, *, usage, resume, **k):
        assert resume
        manifest = store.load()
        left = [t for t in manifest.spec.tasks if t.id not in {o.task.id for o in manifest.outcomes}]
        assert [t.id for t in left] == ["task2"]  # task 1 is not solved again
        usage.phase("solve").merge(Usage(calls=2, input_tokens=4000, output_tokens=400))
        manifest.outcomes.append(TaskOutcome(task=left[0], status="passed"))
        store.save(manifest)
        return manifest

    monkeypatch.setattr(pipeline, "run_lab", run_lab)
    monkeypatch.setattr(pipeline, "build_model", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "build_explainer", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_emit", lambda *a, **k: [])
    job = Job("abcdef000002")
    job.attach_run(store.dir)
    usage = RunUsage()
    pipeline.resume_job(job, settings=Settings(deepseek_api_key="x"), profile_seed={}, usage=usage)

    assert job.status == "done", job.error
    assert (job.summary["passed"], job.summary["total"]) == (2, 2)
    after = store.load()
    assert "resume" not in after.context  # the checkpoint's notes go with it
    assert after.usage["phases"]["solve"]["calls"] == 5  # the record keeps both passes
    assert after.credits == credits.charge(usage.total.cost_usd)  # the bill, only this one


def test_a_spec_comes_back_from_the_manifest_unchanged():
    """A fresh solve now runs from the manifest's copy of the spec, not the
    one in memory -- so the copy must be the same spec."""
    task = Task(
        id="task1", title="Sum", statement="Add.", sample_inputs=["3 5"], sample_output="8",
        wants_explanation=True, written_questions=["Why?"], needs_code=True,
        instruction="Use a while loop.", effort="basic",
    )
    manifest = RunManifest(run_id="r", started_at=datetime.now(timezone.utc), spec=LabSpec("03", "L", tasks=[task]))
    assert manifest_from_dict(manifest_to_dict(manifest)).spec == manifest.spec


# -- 4. over HTTP, and the retention sweep -------------------------------------------------------


def test_after_a_restart_the_browser_replays_the_chat_and_can_resume(tmp_path, monkeypatch, launched):
    from fastapi.testclient import TestClient

    from web.server import app as app_module

    job = _cut_short(tmp_path)
    for _ in range(2):  # two restarts: resumed once, then parked
        monkeypatch.setattr(
            app_module, "registry",
            JobRegistry(root=tmp_path, owner_of=durable.owner_of, on_restore=durable.adopt),
        )
        client = TestClient(app_module.app)
        status = client.get(f"/api/runs/{job.id}").json()["status"]
    assert status == "interrupted"
    replay = client.get(f"/api/runs/{job.id}/events").text
    assert '"type": "accepted"' in replay and '"type": "interrupted"' in replay
    assert client.post(f"/api/runs/{job.id}/resume").status_code == 202
    assert client.post(f"/api/runs/{job.id}/resume").status_code == 409
    assert client.get(f"/api/runs/{'0' * 12}").status_code == 404


def test_a_labs_chat_is_deleted_with_its_files(tmp_path):
    now = datetime.now(timezone.utc)
    (tmp_path / "20250101-000000_lab01_abcdef012345").mkdir()
    gone = tmp_path / "_web_uploads" / "abcdef012345"
    kept = tmp_path / "_web_uploads" / "fedcba543210"
    for folder in (gone, kept):
        folder.mkdir(parents=True)
        (folder / EVENTS_FILE).write_text("{}\n", encoding="utf-8")
    assert retention.sweep(tmp_path, 30, now=now) == ["20250101-000000_lab01_abcdef012345"]
    assert not gone.exists() and kept.exists()
