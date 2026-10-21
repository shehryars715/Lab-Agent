"""Credits from the server's side: reserve, cap, settle -- with the database faked.

The SQL itself (locking, the race, the ledger) was checked against a real
Postgres; these pin the server's half: which runs are free, that a crash is
still settled, that the ceiling follows the hold, and the sentences a student
sees when they are out of credits.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from labsagent.config import Settings
from web.server import app as app_module
from web.server import auth, metering, supa
from web.server.jobs import Job, JobRegistry

USER = "cccccccc-0000-0000-0000-000000000003"


class Ledger:
    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply or {"hold_id": "h1", "credits": 150, "balance": 150}

    def __call__(self, name, **args):
        self.calls.append((name, args))
        if name == "reserve":
            return self.reply
        if name == "my_balance":
            return {"balance": 281}
        return None

    def settled(self):
        return [a for n, a in self.calls if n == "settle"]


class _Phase:
    def __init__(self):
        self.cost_usd = 0.0


class FakeUsage:
    """RunUsage's shape for metering: per-phase dollars, and a total."""

    def __init__(self, model=""):
        self.phases = {}

    def phase(self, name):
        return self.phases.setdefault(name, _Phase())

    @property
    def total(self):
        total = _Phase()
        total.cost_usd = sum(p.cost_usd for p in self.phases.values())
        return total


@pytest.fixture
def ledger(monkeypatch):
    monkeypatch.setattr(
        supa, "settings",
        supa.SupaSettings(SUPABASE_URL="https://p.supabase.co", SUPABASE_ANON_KEY="a", SUPABASE_SERVICE_KEY="s"),
    )
    fake = Ledger()
    monkeypatch.setattr(supa, "rpc", fake)
    monkeypatch.setattr(metering.time, "sleep", lambda s: None)
    monkeypatch.setattr(metering, "RunUsage", FakeUsage)
    return fake


def _usage(usd: float) -> FakeUsage:
    usage = FakeUsage()
    usage.phase("solve").cost_usd = usd
    return usage


def test_a_hold_sets_the_runs_cost_ceiling(ledger):
    hold = metering.reserve(USER, "j1", "run", Settings())
    assert hold == metering.Hold(id="h1", credits=150)
    capped = metering.capped(Settings(), hold)
    assert capped.max_cost_per_run_usd == pytest.approx(0.015)
    assert capped.max_cost_per_task_usd == pytest.approx(0.015)
    # A small hold makes a smaller ceiling than the per-task default.
    small = metering.capped(Settings(), metering.Hold(id="h", credits=12))
    assert small.max_cost_per_task_usd == pytest.approx(0.0012)


@pytest.mark.parametrize(
    "reply,status,words",
    [
        ({"error": "no_credits", "balance": 3}, 402, "this month’s 300 credits"),
        ({"error": "daily_cap"}, 503, "today’s limit"),
        ({}, 503, "Try again"),
    ],
)
def test_a_refused_reservation_is_a_sentence_not_a_crash(ledger, reply, status, words):
    ledger.reply = reply
    with pytest.raises(HTTPException) as caught:
        metering.reserve(USER, "j1", "run", Settings())
    assert caught.value.status_code == status and words in caught.value.detail


def test_a_lab_stopped_before_solving_is_free(ledger):
    job = Job("j1")

    def run_job(usage, **kwargs):
        usage.phase("ingest").cost_usd = 0.0004  # the read was paid for, by us

    metering.run_metered(run_job, metering.Hold("h1", 150), free_unless_solved=True, job=job)
    assert ledger.settled() == [{"p_hold_id": "h1", "p_charged": 0, "p_actual_usd": pytest.approx(0.0004)}]


def test_a_solved_lab_pays_its_real_cost_rounded_up(ledger):
    job = Job("j1")

    def run_job(usage, **kwargs):
        job.phase("solving", "Solving")
        usage.phase("solve").cost_usd = 0.00181

    metering.run_metered(run_job, metering.Hold("h1", 150), free_unless_solved=True, job=job)
    assert ledger.settled()[0]["p_charged"] == 19


def test_a_crash_after_solving_is_still_settled(ledger):
    job = Job("j1")

    def run_job(usage, **kwargs):
        job.phase("solving", "Solving")
        usage.phase("solve").cost_usd = 0.003
        raise RuntimeError("worker died")

    with pytest.raises(RuntimeError):
        metering.run_metered(run_job, metering.Hold("h1", 150), free_unless_solved=True, job=job)
    assert ledger.settled()[0]["p_charged"] == 30


def test_an_overrun_is_clamped_to_the_hold_but_its_real_cost_recorded(ledger):
    charged = metering.settle(metering.Hold("h1", 20), _usage(0.05))
    assert charged == 20
    assert ledger.settled()[0]["p_actual_usd"] == pytest.approx(0.05)


def test_a_follow_up_question_is_never_free(ledger):
    job = Job("j1")

    def revise_job(usage, **kwargs):
        usage.phase("revise").cost_usd = 0.0002  # answered, no solve

    metering.run_metered(revise_job, metering.Hold("h1", 150), free_unless_solved=False, job=job)
    assert ledger.settled()[0]["p_charged"] == 2


def test_unmetered_servers_hold_nothing_and_change_nothing(monkeypatch):
    monkeypatch.setattr(supa, "settings", supa.SupaSettings(SUPABASE_URL="", SUPABASE_ANON_KEY="", SUPABASE_SERVICE_KEY=""))
    monkeypatch.setattr(supa, "rpc", lambda *a, **k: pytest.fail("no database without Supabase"))
    hold = metering.reserve("local", "j1", "run", Settings())
    assert hold is metering.UNMETERED
    assert metering.capped(Settings(), hold) == Settings()
    assert metering.settle(hold, _usage(1.0)) == 0
    assert metering.balance("local", Settings()) is None


def test_refills_on_is_the_first_of_next_month():
    from datetime import datetime, timezone

    assert metering.refills_on(datetime(2026, 10, 21, tzinfo=timezone.utc)) == "1 Nov"
    assert metering.refills_on(datetime(2026, 12, 31, tzinfo=timezone.utc)) == "1 Jan"


def test_out_of_credits_answers_402_and_leaves_no_running_lab(ledger, monkeypatch):
    ledger.reply = {"error": "no_credits", "balance": 0}
    monkeypatch.setattr(app_module, "registry", JobRegistry())
    monkeypatch.setattr(app_module, "run_job", lambda **k: pytest.fail("must not start"))
    client = TestClient(app_module.app)
    client.cookies.set(auth.COOKIE, auth.mint(USER))
    res = client.post("/api/runs", data={"instructions": "Task 1: print hello"})
    assert res.status_code == 402 and "refill" in res.json()["detail"]
    assert app_module.registry.active_for(USER) == []  # the next try is not blocked
    assert ledger.settled() == []  # nothing was held, so nothing to settle


def test_me_reports_the_balance(ledger):
    client = TestClient(app_module.app)
    client.cookies.set(auth.COOKIE, auth.mint(USER))
    credits = client.get("/api/me").json()["credits"]
    assert credits["balance"] == 281 and credits["allowance"] == 300 and credits["refills_on"]
