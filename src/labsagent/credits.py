"""Credits: the unit a student sees and is charged in.

ONE CREDIT IS ONE PAKISTANI RUPEE (owner's decision, 2026-10-22). That peg is
deliberately never shown: the screen says "credits" and nothing else. This
module is the only place the peg lives.

USD STAYS THE MEASURED TRUTH. `usage.py` prices tokens in dollars because that
is what the provider bills; the eval reports and the safety caps in `budget.py`
guard real money, so they stay in dollars too. Credits are a billing and
display layer on top, converted at a fixed `PKR_PER_USD` (Settings, default
280) -- fixed rather than fetched, so a price never moves under a student
between one day and the next.

COUNTED IN PAISA, SHOWN IN WHOLE CREDITS. Almost everything costs less than one
credit: a follow-up question is about Rs 0.06, a small lab Rs 0.10-0.50, a
heavy data lab Rs 2-5. Charging whole credits would make every small lab cost
1 (a 2-15x markup) and every lab look the same price. So:
  - the ledger counts PAISA (1/100 credit) as integers -- `paisa()`;
  - a settled charge is rounded UP to the next paisa, once per charge, so it is
    never under-reported and rounding cannot compound across calls;
  - the screen rounds to whole credits (the UI's `formatCredits`), with
    "<1 credit" for a charge that rounds to nothing.
`exact()` is for live running totals; `charge()` is the receipt.

THE RATE IS RECORDED WITH THE CHARGE. The manifest stores the credits it was
charged and the `usd_per_credit` in effect, so changing the rate later never
rewrites what an old run cost -- `shown()` converts an old receipt instead.
"""

from __future__ import annotations

import math

from labsagent.config import load_settings

#: Rupees per US dollar. The one knob; recorded with every charge.
PKR_PER_USD = float(load_settings().pkr_per_usd)
#: One credit = Rs 1.
USD_PER_CREDIT = 1.0 / PKR_PER_USD
PAISA_PER_CREDIT = 100
USD_PER_PAISA = USD_PER_CREDIT / PAISA_PER_CREDIT


def exact(usd: float) -> float:
    """Fractional credits for a dollar amount -- for live running totals."""
    return max(0.0, usd) / USD_PER_CREDIT


def paisa(usd: float) -> int:
    """Whole paisa for one settled charge, rounded up. What the ledger stores.

    Rounded to six places before the ceiling, because binary floats turn an
    exact amount into 20.000000000000004 -- a naive ceil would charge one
    paisa more than the work cost.
    """
    if usd <= 0:
        return 0
    return math.ceil(round(usd / USD_PER_PAISA, 6))


def charge(usd: float) -> float:
    """The settled charge in credits: whole paisa, as a credit amount (Rs)."""
    return paisa(usd) / PAISA_PER_CREDIT


def to_paisa(credits: float) -> int:
    """A credit amount (a setting, a balance) as whole paisa."""
    return int(round(credits * PAISA_PER_CREDIT))


def from_paisa(amount: int) -> float:
    return amount / PAISA_PER_CREDIT


def shown(credits_charged: float, usd_per_credit: float | None) -> float:
    """An old receipt in today's credits. Receipts from before the rupee peg
    (1 credit = $0.0001) are converted through the dollars they stood for."""
    if not usd_per_credit or math.isclose(usd_per_credit, USD_PER_CREDIT):
        return credits_charged
    return round(credits_charged * usd_per_credit / USD_PER_CREDIT, 2)
