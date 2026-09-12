"""Cost arithmetic. The cache subset rule is the thing that must not drift."""

from __future__ import annotations

from labsagent.usage import RunUsage, Usage


class FakeMsg:
    def __init__(self, i, o, cached=0):
        self.usage_metadata = {
            "input_tokens": i,
            "output_tokens": o,
            "input_token_details": {"cache_read": cached},
        }


def test_cached_tokens_are_a_subset_of_input_not_an_extra():
    u = Usage().add_message(FakeMsg(1000, 100, cached=900))

    assert u.input_tokens == 1000
    assert u.billable_input == 100, "cached must be subtracted, not added"
    assert u.cache_hit_rate == 0.9


def test_cost_uses_split_rates():
    u = Usage().add_message(FakeMsg(1_000_000, 1_000_000, cached=0))

    assert abs(u.cost_usd - (0.14 + 0.28)) < 1e-9


def test_caching_is_dramatically_cheaper():
    cold = Usage().add_message(FakeMsg(100_000, 100, cached=0))
    warm = Usage().add_message(FakeMsg(100_000, 100, cached=95_000))

    assert warm.cost_usd < cold.cost_usd / 5
    assert warm.cost_if_uncached_usd == cold.cost_usd


def test_naive_sum_would_overstate_cost():
    """Guards the specific mistake: charging all input at the miss rate."""
    u = Usage().add_message(FakeMsg(14_739, 334, cached=14_080))
    naive = 14_739 / 1e6 * 0.14 + 334 / 1e6 * 0.28

    assert naive > u.cost_usd * 5


def test_pro_model_costs_more():
    flash = Usage(model="deepseek-flash").add_message(FakeMsg(10_000, 1_000))
    pro = Usage(model="deepseek-v4-pro").add_message(FakeMsg(10_000, 1_000))

    assert pro.cost_usd > flash.cost_usd * 2


def test_messages_without_usage_metadata_are_ignored():
    u = Usage().add_messages([object(), FakeMsg(10, 5)])

    assert u.calls == 1


def test_phases_roll_up_into_a_total():
    run = RunUsage()
    run.phase("ingest").add_message(FakeMsg(3_000, 1_200))
    run.phase("solve").add_message(FakeMsg(14_000, 300, cached=13_000))

    assert run.total.calls == 2
    assert run.total.input_tokens == 17_000
    assert set(run.as_dict()["phases"]) == {"ingest", "solve"}
    assert run.as_dict()["total"]["cost_usd"] > 0
