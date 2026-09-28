"""Credits: the unit a student sees and is charged in.

USD STAYS THE MEASURED TRUTH. `usage.py` prices tokens in dollars because that
is what the provider bills; the eval reports and the safety caps in `budget.py`
guard real money, so they stay in dollars too. Credits are a display and
billing layer on top: this module is the only place that knows the rate, and
the web boundary (`wire_event`, the job summaries, history) is the only place
that converts.

WHY A HUNDREDTH OF A CENT. A basic follow-up costs about $0.0002 and a typical
three-task lab about $0.002. At 1 credit = $0.0001 those are 2 and 19 credits:
whole numbers with at most one credit of rounding on each charge. A tenth of a
cent would have charged the same follow-up 1 credit -- a 5x markup -- and
priced a $0.0014 lab like a $0.002 one.

WHY ROUND UP, ONCE. Fractional credits are kept exactly until a charge is
settled (a run, or one follow-up); then they are rounded up to a whole credit,
so a charge is never under-reported, and rounding happens once per charge so
it cannot compound across the calls inside it. `exact()` is for live running
totals; `charge()` is what gets recorded.

THE RATE IS RECORDED WITH THE CHARGE. The manifest stores the credits it was
charged and the `usd_per_credit` in effect, so re-pegging this constant later
never rewrites what an old run cost.

How many credits each student gets is a separate, open decision (PLAN.md,
"per-user credits").
"""

from __future__ import annotations

import math

#: One credit is a hundredth of a cent of model cost.
USD_PER_CREDIT = 0.0001


def exact(usd: float) -> float:
    """Fractional credits for a dollar amount -- for live running totals."""
    return max(0.0, usd) / USD_PER_CREDIT


def charge(usd: float) -> int:
    """Whole credits for one settled charge, rounded up.

    Rounded to six places before the ceiling, because binary floats make
    0.002 / 0.0001 come out as 20.000000000000004 -- a naive ceil would charge
    21 credits for exactly 20 credits' worth of work.
    """
    if usd <= 0:
        return 0
    return math.ceil(round(usd / USD_PER_CREDIT, 6))
