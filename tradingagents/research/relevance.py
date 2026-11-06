"""Decision relevance for a sensitivity, without a universal percentage threshold.

A shock matters only against the decision it would change. The classifier uses
the actual boundary (the current price for a directional call, the published
band for a neutral one) rather than a fixed percentage:

DECISIVE          the plausible shock crosses the boundary and can change the call.
MATERIAL_SECONDARY it changes the valuation or the margin of safety but does not
                  cross the boundary; the call survives only with that stated.
MINOR             it does not reach the decision.

No assumption is made about the valuation method, the sector, or which metrics
exist: the inputs are a fair value and, when available, a price or a band.
"""

from __future__ import annotations

DECISIVE = "DECISIVE"
MATERIAL_SECONDARY = "MATERIAL_SECONDARY"
MINOR = "MINOR"

LONG_ACTIONS = {"BUY", "OVERWEIGHT", "ADD", "INITIATE"}
SHORT_ACTIONS = {"SELL", "REDUCE", "EXIT", "AVOID"}


def classify(
    *,
    action: str,
    base_fair: float | None,
    shocked_fair: float | None,
    price: float | None = None,
    band_low: float | None = None,
    band_high: float | None = None,
) -> str:
    """The relevance class of one shocked fair value against the decision."""
    if base_fair is None or shocked_fair is None or base_fair <= 0:
        return MINOR
    normalized = str(action or "").strip().upper()

    if normalized in LONG_ACTIONS:
        if price and price > 0:
            if shocked_fair <= price:
                return DECISIVE
            margin = base_fair - price
            if margin > 0 and (base_fair - shocked_fair) >= margin * 0.5:
                return MATERIAL_SECONDARY
            return MINOR
        if band_low is not None and shocked_fair <= band_low:
            return MATERIAL_SECONDARY
        return MINOR

    if normalized in SHORT_ACTIONS:
        if price and price > 0:
            if shocked_fair >= price:
                return DECISIVE
            margin = price - base_fair
            if margin > 0 and (shocked_fair - base_fair) >= margin * 0.5:
                return MATERIAL_SECONDARY
            return MINOR
        if band_high is not None and shocked_fair >= band_high:
            return MATERIAL_SECONDARY
        return MINOR

    # A neutral call has no directional boundary. Crossing the price can change
    # the call; moving outside the published band changes the valuation case.
    if price and price > 0 and (base_fair - price) * (shocked_fair - price) < 0:
        return DECISIVE
    if band_low is not None and band_high is not None:
        if shocked_fair < band_low or shocked_fair > band_high:
            return MATERIAL_SECONDARY
        half_width = (band_high - band_low) / 2
        if half_width > 0 and abs(shocked_fair - base_fair) >= half_width * 0.5:
            return MATERIAL_SECONDARY
    return MINOR
