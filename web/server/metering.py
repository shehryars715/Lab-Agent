"""Paying for a run out of an account's credits: reserve, then settle.

THE PROBLEM WITH "CHECK, THEN CHARGE". Start a run if the balance is positive,
charge the real cost at the end: two tabs with 160 credits left both see 160,
both start, and the balance ends at -140. Balances go negative, and parallel
runs race past the check.

RESERVE, THEN SETTLE. Before a run starts, the database HOLDS up to 150
credits (never more than the balance) in one locked step -- `reserve()` in
002_credits.sql locks the account's row, so the second tab waits, then sees
10. The run's cost ceiling is set to exactly what was held, so it cannot
spend past it. When it ends, `settle()` charges the real cost and releases
the rest. Net effect: the real cost, and a balance that can never go negative.

WHAT IS FREE. A lab stopped before solving (not a lab, out of scope, missing
data or earlier work) is charged nothing -- the owner's decision. Follow-ups
are always charged their real cost: even a question is a model call, and a
free question is an unlimited free model.

THE GLOBAL CAP. The same `reserve()` refuses when today's real spend across
every account, plus every hold still open, plus this one, would pass
`daily_cap_usd`. Counting open holds is the point: without them, twenty runs
starting at once would all see "under $2".

A RESTART. Jobs live in memory, so a restart ends live runs. At startup the
server releases every hold older than itself (`release_open_holds`): nobody
pays for a run that died. Their worst-case cost still counts toward today's
cap, because we no longer know what they spent.

Off unless Supabase is configured: single-user servers keep today's caps.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException

from labsagent import credits
from labsagent.config import Settings, load_settings
from labsagent.usage import RunUsage

from . import auth, supa

PROCESS_STARTED = datetime.now(timezone.utc)


@dataclass(frozen=True)
class Hold:
    id: str | None  # None: not metered (single-user server)
    credits: int


UNMETERED = Hold(id=None, credits=0)


def metered() -> bool:
    return auth.mode() == "supabase"


def refills_on(now: datetime | None = None) -> str:
    """'1 Nov' -- the first day of next month, UTC."""
    now = now or datetime.now(timezone.utc)
    first = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1)
    return f"{first.day} {first.strftime('%b')}"


def reserve(user_id: str, job_id: str, kind: str, settings: Settings) -> Hold:
    """Hold this run's credits, or refuse with a sentence the student can act on."""
    if not metered():
        return UNMETERED
    try:
        result = supa.rpc(
            "reserve",
            p_user_id=user_id,
            p_job_id=job_id,
            p_kind=kind,
            p_max=settings.max_hold_credits,
            p_min=settings.min_start_credits,
            p_monthly=settings.monthly_credits,
            p_usd_per_credit=credits.USD_PER_CREDIT,
            p_daily_cap_usd=settings.daily_cap_usd,
        ) or {}
    except supa.SupaError as exc:
        raise HTTPException(503, "Can’t check your credits right now. Try again in a minute.") from exc
    error = result.get("error")
    if error == "no_credits":
        raise HTTPException(
            402,
            f"You’ve used this month’s {settings.monthly_credits} credits. "
            f"They refill on {refills_on()}.",
        )
    if error == "daily_cap":
        # Midnight UTC is 05:00 in Pakistan, where the students are.
        raise HTTPException(
            503, "Labs-Agent has reached today’s limit. New labs start again at 05:00."
        )
    if error or not result.get("hold_id"):
        raise HTTPException(503, "Can’t reserve credits right now. Try again in a minute.")
    return Hold(id=str(result["hold_id"]), credits=int(result["credits"]))


def capped(settings: Settings, hold: Hold) -> Settings:
    """This run's settings, with its cost ceiling set to what its hold can pay."""
    if hold.id is None:
        return settings
    cap = hold.credits * credits.USD_PER_CREDIT
    return settings.model_copy(
        update={
            "max_cost_per_run_usd": min(settings.max_cost_per_run_usd, cap),
            "max_cost_per_task_usd": min(settings.max_cost_per_task_usd, cap),
        }
    )


def settle(hold: Hold, usage: RunUsage, *, free: bool = False) -> int:
    """Charge the real cost (0 when `free`), release the rest. Never raises."""
    if hold.id is None:
        return 0
    actual_usd = usage.total.cost_usd
    charged = 0 if free else min(credits.charge(actual_usd), hold.credits)
    for attempt in range(3):
        try:
            supa.rpc("settle", p_hold_id=hold.id, p_charged=charged, p_actual_usd=actual_usd)
            return charged
        except supa.SupaError as exc:
            if attempt == 2:
                # Left open; the next startup's sweep releases it in full.
                print(f"metering: could not settle hold {hold.id}: {exc}", flush=True)
            time.sleep(1 + attempt)
    return charged


def run_metered(target, hold: Hold, *, free_unless_solved: bool, **kwargs) -> None:
    """Run a job function with a usage it can fill, then settle -- always."""
    job = kwargs.get("job")
    usage = RunUsage(model=(kwargs.get("settings") or load_settings()).model_name)
    try:
        target(usage=usage, **kwargs)
    finally:
        free = free_unless_solved and not getattr(job, "solve_started", False)
        settle(hold, usage, free=free)


def balance(user_id: str, settings: Settings) -> dict | None:
    """{balance, allowance, refills_on} for the header, or None when unmetered."""
    if not metered():
        return None
    try:
        result = supa.rpc("my_balance", p_user_id=user_id, p_monthly=settings.monthly_credits) or {}
    except supa.SupaError:
        return None
    return {
        "balance": int(result.get("balance", 0)),
        "allowance": settings.monthly_credits,
        "refills_on": refills_on(),
    }


def release_stale_holds() -> None:
    """At startup: every open hold belongs to a run this process never saw."""
    if not metered():
        return
    try:
        supa.rpc("release_open_holds", p_before=PROCESS_STARTED.isoformat())
    except supa.SupaError as exc:
        print(f"metering: could not release stale holds: {exc}", flush=True)
