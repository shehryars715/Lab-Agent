"""Nobody opens a run that is not theirs -- checked on EVERY route that takes one.

The coverage test walks `app.routes` instead of listing routes by hand, so a
route added next month without an ownership check fails here, not in a
student's browser. Supabase mode, with its database faked as a dict.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from web.server import app as app_module
from web.server import auth, history, profiles, supa
from web.server.jobs import JobRegistry

ALICE = "aaaaaaaa-0000-0000-0000-000000000001"
BOB = "bbbbbbbb-0000-0000-0000-000000000002"
RUN = "20261021-090000_lab03_alicejob"


class FakeDB:
    """The handful of database functions the server calls, in memory."""

    def __init__(self):
        self.runs: dict[str, dict] = {}
        self.profiles: dict[str, dict] = {}

    def __call__(self, name, **a):
        if name == "record_run":
            self.runs[a["p_job_id"]] = {"user": a["p_user_id"], "run_dir": None}
        elif name == "attach_run_dir":
            self.runs[a["p_job_id"]]["run_dir"] = a["p_run_dir"]
        elif name == "owns_run":
            return any(r["user"] == a["p_user_id"] and r["run_dir"] == a["p_run_dir"] for r in self.runs.values())
        elif name == "my_runs":
            return [{"run_dir": r["run_dir"]} for r in self.runs.values() if r["user"] == a["p_user_id"] and r["run_dir"]]
        elif name == "get_profile":
            return self.profiles.get(a["p_user_id"])
        elif name == "save_profile":
            self.profiles[a["p_user_id"]] = {k[2:]: v for k, v in a.items() if k != "p_user_id"}
        elif name == "ensure_user":
            return None
        else:
            raise AssertionError(f"unexpected rpc {name}")


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setattr(
        supa, "settings",
        supa.SupaSettings(SUPABASE_URL="https://p.supabase.co", SUPABASE_ANON_KEY="a", SUPABASE_SERVICE_KEY="s"),
    )
    db = FakeDB()
    monkeypatch.setattr(supa, "rpc", db)
    monkeypatch.setattr(history, "RUNS_ROOT", tmp_path)
    monkeypatch.setattr(app_module, "UPLOADS_ROOT", tmp_path / "_web_uploads")
    # The worker does nothing: these tests are about who may reach a run.
    # It leaves the job "queued", i.e. still active, like a lab in progress.
    monkeypatch.setattr(app_module, "run_job", lambda **kwargs: None)
    monkeypatch.setattr(app_module, "registry", JobRegistry())

    # A finished run of Alice's on disk, with one downloadable file.
    run = tmp_path / RUN
    (run / "report").mkdir(parents=True)
    (run / "report" / "Lab03_Report.docx").write_bytes(b"PK report")
    (run / "manifest.json").write_text(json.dumps({"spec": {"lab_number": "03", "title": "t", "tasks": []}, "outcomes": []}))
    db.runs["alicejob"] = {"user": ALICE, "run_dir": RUN}

    def client_for(user):
        client = TestClient(app_module.app)
        client.cookies.set(auth.COOKIE, auth.mint(user))
        return client

    alice, bob = client_for(ALICE), client_for(BOB)
    created = alice.post("/api/runs", data={"instructions": "Task 1: print hello"})
    assert created.status_code == 202, created.text
    job_id = created.json()["job_id"]
    return {"alice": alice, "bob": bob, "job_id": job_id, "db": db}


def _id_routes():
    for route in app_module.app.routes:
        path = getattr(route, "path", "")
        if "{job_id}" in path or "{run_id}" in path:
            for method in route.methods - {"HEAD"}:
                yield method, path


def _fill(path, job_id):
    return path.replace("{job_id}", job_id).replace("{run_id}", RUN).replace("{key}", "report")


def test_there_are_id_routes_to_check():
    # 6 live-run routes + 2 history routes today; fewer means one went missing.
    assert len(list(_id_routes())) >= 8


@pytest.mark.parametrize("method,path", list(_id_routes()))
def test_every_id_route_hides_another_users_run(world, method, path):
    url = _fill(path, world["job_id"])
    body = {"answers": {}, "feedback": "x", "name": "Bob"}
    res = world["bob"].request(method, url, json=body)
    assert res.status_code == 404, f"{method} {path} answered {res.status_code} to a stranger"


def test_the_owner_still_gets_in(world):
    alice, job_id = world["alice"], world["job_id"]
    assert alice.get(f"/api/runs/{job_id}").status_code == 200
    detail = alice.get(f"/api/history/{RUN}")
    assert detail.status_code == 200
    key = detail.json()["artifacts"][0]["key"]
    assert alice.get(f"/api/history/{RUN}/download/{key}").content == b"PK report"
    assert world["bob"].get(f"/api/history/{RUN}/download/{key}").status_code == 404
    assert [r["run_id"] for r in alice.get("/api/history").json()["runs"]] == [RUN]


def test_history_lists_only_your_own_runs(world):
    assert world["bob"].get("/api/history").json()["runs"] == []


def test_one_lab_at_a_time_per_account(world):
    again = world["alice"].post("/api/runs", data={"instructions": "Task 1: print hi"})
    assert again.status_code == 409
    # Someone else is not blocked by Alice's lab.
    assert world["bob"].post("/api/runs", data={"instructions": "Task 1: print hi"}).status_code == 202


def test_profiles_are_per_account(world):
    world["alice"].put("/api/profile", json={"name": "Alice A", "cms_id": "123"})
    assert world["alice"].get("/api/profile").json()["name"] == "Alice A"
    assert world["bob"].get("/api/profile").json()["name"] == ""


def test_internal_folders_are_never_runs(world, tmp_path):
    (tmp_path / "_profiles").mkdir()
    (tmp_path / "_profiles" / f"{ALICE}.json").write_text("{}")
    assert history.run_dir("_profiles") is None
    assert world["alice"].get("/api/history/_profiles").status_code == 404


def test_profile_fields_are_trimmed_and_bounded():
    cleaned = profiles.clean({"name": "  A " + "x" * 500, "cms_id": "", "evil": "nope"})
    assert set(cleaned) == {"name"} and len(cleaned["name"]) == profiles.MAX_LEN


def test_retention_removes_only_what_is_older_than_the_window(tmp_path):
    from datetime import datetime, timezone

    from web.server import retention

    now = datetime(2026, 11, 30, tzinfo=timezone.utc)
    for name in ("20261001-000000_lab01_old", "20261125-000000_lab02_new", "_profiles", "not-a-run"):
        (tmp_path / name).mkdir()
    removed = retention.sweep(tmp_path, 30, now=now)
    assert removed == ["20261001-000000_lab01_old"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["20261125-000000_lab02_new", "_profiles", "not-a-run"]
    assert retention.sweep(tmp_path, 0, now=now) == []  # off means off
