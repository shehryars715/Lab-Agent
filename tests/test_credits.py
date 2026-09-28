"""Credits: the conversion, the settle-once rounding, and the wire boundary."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from labsagent import credits
from labsagent import events as ev
from labsagent.models import LabSpec, RunManifest, Task
from labsagent.runstore import manifest_from_dict, manifest_to_dict
from web.server.pipeline import _record_followup, wire_event


def test_a_credit_is_a_hundredth_of_a_cent():
    assert credits.USD_PER_CREDIT == 0.0001
    assert credits.exact(0.0019) == pytest.approx(19.0)


@pytest.mark.parametrize(
    ("usd", "charged"),
    [
        (0.0, 0),
        (-0.001, 0),  # never a negative charge
        (0.00001, 1),  # a sliver still costs a whole credit, never zero
        (0.0019033, 20),  # rounded UP, never under-reported
        (0.0019, 19),  # exact amounts stay exact
        # 0.002 / 0.0001 is 20.000000000000004 in binary floats; a naive ceil
        # would bill 21 credits for 20 credits' worth of work.
        (0.002, 20),
        (0.15, 1500),  # the run cap, in credits
    ],
)
def test_charge_rounds_up_once(usd, charged):
    assert credits.charge(usd) == charged


def _manifest(**extra) -> RunManifest:
    return RunManifest(
        run_id="20260926-000000_lab07",
        started_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
        spec=LabSpec(lab_number="07", title="t", course="c", tasks=[Task(id="task1", title="T", statement="s")]),
        cost_usd=0.0019,
        **extra,
    )


def test_manifest_keeps_the_charge_and_its_rate():
    m = _manifest(credits=19, usd_per_credit=0.0001)
    back = manifest_from_dict(manifest_to_dict(m))
    assert back.credits == 19
    assert back.usd_per_credit == 0.0001


def test_a_run_from_before_credits_still_shows_a_number():
    data = manifest_to_dict(_manifest())
    del data["credits"], data["usd_per_credit"]  # as every run on disk today
    back = manifest_from_dict(data)
    assert back.credits == 19
    assert back.usd_per_credit == credits.USD_PER_CREDIT


def test_the_wire_carries_credits_never_dollars():
    event = ev.TaskFinished(task_id="task1", status="passed", attempts=1, cost_usd=0.0004)
    payload = wire_event(event)
    assert "cost_usd" not in payload
    # exact while live: a running total must not round each task up
    assert payload["credits"] == pytest.approx(4.0)


class _Usage:
    """Just enough of RunUsage for `_record_followup`."""

    def __init__(self, usd: float):
        self.total = type("T", (), {"cost_usd": usd, "as_dict": lambda self: {}})()


class _Followup:
    kind, resolve_ids, rewrite_ids, artifacts, style = "resolve", [], [], [], "classic"


def test_each_followup_is_its_own_charge():
    # Two follow-ups of $0.00015 each: rounded per charge that is 2 + 2 = 4
    # credits. Rounding the running dollar total instead would say 3 -- a
    # student would be charged differently depending on how the sum is taken.
    m = _manifest(credits=19, usd_per_credit=0.0001)
    prior = m.cost_usd
    _record_followup(m, "use a while loop", _Followup(), _Usage(0.00015), prior)
    _record_followup(m, "and no f-strings", _Followup(), _Usage(0.00015), m.cost_usd)
    assert [f["credits"] for f in m.followups] == [2, 2]
    assert m.credits == 19 + 4
    assert m.cost_usd == pytest.approx(0.0019 + 0.0003)
