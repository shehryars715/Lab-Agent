"""Credits: the rupee peg, paisa rounding, old receipts, and the wire boundary.

1 credit = Rs 1 at a fixed PKR_PER_USD (default 280). Charges are counted to
the paisa and rounded up once per charge; the screen rounds to whole credits.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from labsagent import credits
from labsagent import events as ev
from labsagent.models import LabSpec, RunManifest, Task
from labsagent.runstore import manifest_from_dict, manifest_to_dict
from web.server.pipeline import _record_followup, wire_event

OLD_RATE = 0.0001  # 1 credit = $0.0001, before the rupee peg


def test_a_credit_is_one_rupee_at_the_fixed_rate():
    assert credits.PKR_PER_USD == 280
    assert credits.USD_PER_CREDIT == pytest.approx(1 / 280)
    assert credits.exact(1 / 280) == pytest.approx(1.0)
    assert credits.exact(0.0019) == pytest.approx(0.532)


@pytest.mark.parametrize(
    ("usd", "paisa"),
    [
        (0.0, 0),
        (-0.001, 0),  # never a negative charge
        (0.000001, 1),  # a sliver still costs a paisa, never zero
        (0.0019, 54),  # 53.2 paisa, rounded UP, never under-reported
        (0.0165, 462),  # the Lab 05 run that hit the old ceiling: Rs 4.62
        (0.05, 1400),  # exact amounts stay exact (no float-noise extra paisa)
    ],
)
def test_charges_are_whole_paisa_rounded_up_once(usd, paisa):
    assert credits.paisa(usd) == paisa
    assert credits.charge(usd) == pytest.approx(paisa / 100)


def test_settings_convert_to_paisa_and_back():
    assert credits.to_paisa(50) == 5000
    assert credits.to_paisa(0.3) == 30
    assert credits.from_paisa(4580) == 45.8


def test_an_old_receipt_is_shown_in_todays_credits():
    # 150 old credits were $0.015, which is Rs 4.20 today.
    assert credits.shown(150, OLD_RATE) == pytest.approx(4.2)
    assert credits.shown(3.5, credits.USD_PER_CREDIT) == 3.5
    assert credits.shown(3.5, None) == 3.5


def _manifest(**extra) -> RunManifest:
    return RunManifest(
        run_id="20260926-000000_lab07",
        started_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
        spec=LabSpec(lab_number="07", title="t", course="c", tasks=[Task(id="task1", title="T", statement="s")]),
        cost_usd=0.0019,
        **extra,
    )


def test_manifest_keeps_the_charge_and_its_rate():
    m = _manifest(credits=0.54, usd_per_credit=credits.USD_PER_CREDIT)
    back = manifest_from_dict(manifest_to_dict(m))
    assert back.credits == pytest.approx(0.54)
    assert back.usd_per_credit == pytest.approx(credits.USD_PER_CREDIT)


def test_a_manifest_charged_at_the_old_rate_loads_in_todays_credits():
    """So a follow-up's rupee charge can be added to it in the same unit."""
    back = manifest_from_dict(manifest_to_dict(_manifest(credits=19, usd_per_credit=OLD_RATE)))
    assert back.credits == pytest.approx(0.53)
    assert back.usd_per_credit == pytest.approx(credits.USD_PER_CREDIT)


def test_a_run_from_before_credits_still_shows_a_number():
    data = manifest_to_dict(_manifest())
    del data["credits"], data["usd_per_credit"]
    back = manifest_from_dict(data)
    assert back.credits == pytest.approx(0.54)


def test_the_wire_carries_credits_never_dollars():
    event = ev.TaskFinished(task_id="task1", status="passed", attempts=1, cost_usd=0.0004)
    payload = wire_event(event)
    assert "cost_usd" not in payload
    # exact while live: a running total must not round each task up
    assert payload["credits"] == pytest.approx(0.112)


class _Usage:
    """Just enough of RunUsage for `_record_followup`."""

    def __init__(self, usd: float):
        self.total = type("T", (), {"cost_usd": usd, "as_dict": lambda self: {}})()


class _Followup:
    kind, resolve_ids, rewrite_ids, edit_request, unsupported = "resolve", [], [], "", []


def test_each_followup_is_its_own_charge():
    # Two follow-ups of $0.00015 each are 4.2 paisa each: rounded per charge
    # that is 5 + 5 = 10 paisa. Rounding the running total instead would say
    # 9 -- a student would be charged differently depending on how the sum is
    # taken.
    m = _manifest(credits=0.54, usd_per_credit=credits.USD_PER_CREDIT)
    prior = m.cost_usd
    _record_followup(m, "use a while loop", _Followup(), _Usage(0.00015), prior)
    _record_followup(m, "and no f-strings", _Followup(), _Usage(0.00015), m.cost_usd)
    assert [f["credits"] for f in m.followups] == [pytest.approx(0.05), pytest.approx(0.05)]
    assert m.credits == pytest.approx(0.54 + 0.10)
    assert m.cost_usd == pytest.approx(0.0019 + 0.0003)
