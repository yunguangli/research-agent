"""Which share price may control the valuation, and when two values conflict.

A price is not just a number: it has an authority. A price the caller supplied for
this run, or the verified close of the analysis date, outranks a price quoted in an
older research note, which in turn outranks a value stored in the model spec. A
price reverse-derived from a market capitalisation is the weakest candidate of all:
it must never displace a verified close, and it is normally a consistency check
rather than an input.

The point of the ranking is that the kernel cannot catch a wrong price. In the
6857.T run the market close was 29,485 and a four-day-old note implied 32,980; both
reconcile with their own market capitalisation, so every arithmetic identity passed
while the repair silently replaced the session close with the older value. Only the
source class distinguishes them.

The module is deliberately data-only. It does not fetch a price, read a state or
write a report; callers resolve a price once and carry a ``PriceResolution``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

USER_EXPLICIT = "USER_EXPLICIT"
VERIFIED_CLOSE = "VERIFIED_CLOSE"
REPORT_PRICE = "REPORT_PRICE"
SPEC_PRICE = "SPEC_PRICE"
DERIVED = "DERIVED_FROM_MARKET_CAP"
UNKNOWN = ""

SOURCES = (USER_EXPLICIT, VERIFIED_CLOSE, REPORT_PRICE, SPEC_PRICE, DERIVED)

_RANK = {
    USER_EXPLICIT: 4,
    VERIFIED_CLOSE: 3,
    REPORT_PRICE: 2,
    SPEC_PRICE: 1,
    DERIVED: 0,
    UNKNOWN: -1,
}

# The tolerance used everywhere a price is compared: one percent, matching the
# existing price-conflict snapshot. It absorbs display rounding without letting a
# real move through.
TOLERANCE = 0.01


@dataclass(frozen=True)
class PriceResolution:
    """One controlling price with the class of evidence behind it."""

    value: float | None = None
    source: str = UNKNOWN
    as_of: str = ""
    ref: str = ""
    ticker: str = ""
    currency: str = ""
    note: str = ""

    @property
    def rank(self) -> int:
        return rank_of(self.source)

    @property
    def verified(self) -> bool:
        """Whether the price is usable as a controlling input."""
        return self.rank >= _RANK[VERIFIED_CLOSE]

    @property
    def explicit(self) -> bool:
        return self.source == USER_EXPLICIT

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "source": self.source,
            "as_of": self.as_of,
            "ref": self.ref,
            "ticker": self.ticker,
            "currency": self.currency,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> PriceResolution:
        data = data if isinstance(data, dict) else {}
        try:
            value = float(data.get("value")) if data.get("value") is not None else None
        except (TypeError, ValueError):
            value = None
        return cls(
            value=value,
            source=str(data.get("source") or UNKNOWN),
            as_of=str(data.get("as_of") or ""),
            ref=str(data.get("ref") or ""),
            ticker=str(data.get("ticker") or ""),
            currency=str(data.get("currency") or ""),
            note=str(data.get("note") or ""),
        )

    @classmethod
    def from_state(cls, state: dict) -> PriceResolution:
        """The resolution a run recorded, for a later step that must not re-pick it."""
        try:
            value = float(state.get("research_price") or 0) or None
        except (TypeError, ValueError):
            value = None
        return cls(
            value=value,
            source=str(state.get("price_source") or UNKNOWN),
            as_of=str(state.get("price_as_of") or ""),
            ref=str(state.get("price_ref") or ""),
            ticker=str(state.get("company_of_interest") or ""),
            currency=str(state.get("currency") or ""),
            note=str(state.get("price_note") or ""),
        )

    @classmethod
    def from_spec(cls, spec) -> PriceResolution:
        """The resolution stored on a model spec, if the spec carries one."""
        valuation = getattr(spec, "valuation", None)
        if valuation is None:
            return cls()
        try:
            value = float(getattr(valuation, "price", None) or 0) or None
        except (TypeError, ValueError):
            value = None
        return cls(
            value=value,
            source=str(getattr(valuation, "price_source", "") or UNKNOWN),
            as_of=str(getattr(valuation, "price_as_of", "") or ""),
            ref=str(getattr(valuation, "price_ref", "") or ""),
            ticker=str(getattr(spec, "ticker", "") or ""),
            currency=str(getattr(spec, "currency", "") or ""),
        )

    def with_context(self, *, ticker: str = "", currency: str = "") -> PriceResolution:
        return replace(
            self,
            ticker=ticker or self.ticker,
            currency=currency or self.currency,
        )


def rank_of(source: str) -> int:
    return _RANK.get(str(source or UNKNOWN), _RANK[UNKNOWN])


def same_security(a: PriceResolution, b: PriceResolution) -> bool:
    """Whether two resolutions claim the same instrument and currency.

    An unstated side is not a mismatch: older checkpoints do not carry either, and
    refusing to compare would hide a real conflict.
    """
    if a.ticker and b.ticker and a.ticker != b.ticker:
        return False
    return not (a.currency and b.currency and a.currency != b.currency)


def differs(a: float | None, b: float | None, tolerance: float = TOLERANCE) -> bool:
    if a is None or b is None:
        return False
    if not a or not b:
        return False
    return abs(float(a) - float(b)) / max(abs(float(a)), abs(float(b))) > tolerance


def authority_rejection(
    current: PriceResolution | None,
    proposed: PriceResolution | None,
) -> str:
    """Why ``proposed`` may not replace ``current``; empty when it may.

    Two outcomes are distinguished: a lower source class (rejected outright) and
    two values at the same class (a conflict that blocks publication, because no
    deterministic rule can choose between them).
    """
    if current is None or proposed is None:
        return ""
    if not current.value or not proposed.value:
        return ""
    if not same_security(current, proposed):
        return "SECURITY_MISMATCH: the proposed price names a different instrument"
    if proposed.rank < current.rank:
        return (
            f"LOWER_AUTHORITY_SOURCE: a {proposed.source or 'UNCLASSIFIED'} price "
            f"of {proposed.value:,.0f} cannot replace the {current.source} price of "
            f"{current.value:,.0f}"
        )
    if proposed.rank > current.rank:
        return ""
    if differs(current.value, proposed.value):
        return (
            f"AUTHORITY_CONFLICT: two {current.source} prices disagree "
            f"({current.value:,.0f} from {current.ref or 'unstated source'} vs "
            f"{proposed.value:,.0f} from {proposed.ref or 'unstated source'})"
        )
    return ""


def conflict_reason(
    a: PriceResolution, b: PriceResolution, tolerance: float = TOLERANCE
) -> str:
    """A same-authority disagreement, or empty when the two can be reconciled."""
    if not same_security(a, b):
        return (
            "the controlling prices name different instruments "
            f"({a.ticker or 'unstated'} vs {b.ticker or 'unstated'})"
        )
    if a.rank != b.rank:
        return ""
    if not differs(a.value, b.value, tolerance):
        return ""
    return (
        f"two {a.source or 'unclassified'} prices disagree: "
        f"{float(a.value or 0):,.0f} ({a.ref or 'unstated source'}) vs "
        f"{float(b.value or 0):,.0f} ({b.ref or 'unstated source'})"
    )


def describe(resolution: PriceResolution) -> str:
    """One line for a report header, e.g. ``29,485 (VERIFIED_CLOSE, as of ...)``."""
    if not resolution.value:
        return "no controlling share price"
    parts = [f"{float(resolution.value):,.0f}"]
    if resolution.source:
        parts.append(resolution.source)
    if resolution.as_of:
        parts.append(f"as of {resolution.as_of}")
    if resolution.ref:
        parts.append(resolution.ref)
    return " (".join([parts[0], ", ".join(parts[1:])]) + (")" if len(parts) > 1 else "")
