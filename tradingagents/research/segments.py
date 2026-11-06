"""Whether a dominant business segment is forecast at its own level.

When one disclosed segment carries most of group profit, a consolidated forecast
is not a bottom-up build unless that segment's revenue, profit and margin are
forecast explicitly and reconcile to the group. The rule is general: the 60%
share is a configurable system threshold, never a company name.

The module never fabricates a missing segment. It reports ``INSUFFICIENT_DATA``
so the publication layer can stop calling the forecast bottom-up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

VERIFIED = "VERIFIED"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
UNRESOLVED = "UNRESOLVED"
NOT_REQUIRED = "NOT_REQUIRED"

DEFAULT_PROFIT_SHARE_THRESHOLD = 0.60
TOLERANCE = 0.01
TOP_DOWN_SENTENCE = (
    "The consolidated forecast is not segment-reconciled and is classified as top-down."
)


@dataclass
class SegmentStatus:
    status: str
    detail: str = ""
    segment: str = ""
    share: float | None = None
    period: str = ""
    missing_periods: list[str] | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "detail": self.detail,
            "segment": self.segment,
            "share": self.share,
            "period": self.period,
            "missing_periods": list(self.missing_periods or []),
        }


def _share(segments) -> tuple[str, float, float] | None:
    """The largest positive segment by operating profit and its share."""
    candidates = [
        (segment.name, float(segment.operating_profit or 0.0))
        for segment in segments
        if not getattr(segment, "is_adjustment", False)
        and float(segment.operating_profit or 0.0) > 0
    ]
    total = sum(profit for _name, profit in candidates)
    if total <= 0 or not candidates:
        return None
    name, profit = max(candidates, key=lambda item: item[1])
    return name, profit / total, total


def dominant(spec) -> tuple[str, float, str] | None:
    """The dominant disclosed segment from the latest reported period.

    Actual periods outrank guidance, which outranks forecast. The denominator is
    the sum of positive segment profits, so a loss-making segment does not turn a
    large share into a small one.
    """
    order = {"actual": 0, "guidance": 1, "forecast": 2}
    periods = sorted(
        (period for period in spec.periods if spec.segments.get(period.label)),
        key=lambda period: (order.get(period.basis, 9), period.end),
    )
    for period in periods:
        result = _share(spec.segments[period.label])
        if result is None:
            continue
        name, share, _total = result
        return name, share, period.label
    return None


def annual_forecast_periods(spec) -> list[str]:
    return [
        period.label
        for period in spec.periods
        if period.basis == "forecast" and getattr(period, "kind", "annual") != "quarter"
    ]


def status(spec, threshold: float = DEFAULT_PROFIT_SHARE_THRESHOLD) -> SegmentStatus:
    """Require the dominant segment in every annual forecast year."""
    if not getattr(spec, "segments", None):
        if getattr(spec, "segment_disclosure", False):
            return SegmentStatus(
                INSUFFICIENT_DATA,
                "the corpus discloses segments, but the model carries no segment "
                "revenue or operating profit",
                missing_periods=annual_forecast_periods(spec),
            )
        return SegmentStatus(NOT_REQUIRED, "no disclosed segment profit in the model")
    found = dominant(spec)
    if found is None:
        return SegmentStatus(NOT_REQUIRED, "no positive disclosed segment profit")
    name, share, period = found
    if share < threshold:
        return SegmentStatus(
            NOT_REQUIRED,
            f"{name} is {share:.1%} of disclosed segment profit, below the "
            f"{threshold:.0%} threshold",
            name,
            share,
            period,
        )
    missing: list[str] = []
    for label in annual_forecast_periods(spec):
        segment = next(
            (item for item in spec.segments.get(label, []) if item.name == name),
            None,
        )
        if segment is None:
            missing.append(label)
        elif abs(float(segment.revenue or 0.0)) < 1e-9:
            # Zero revenue with a dominant profit share is not a segment forecast:
            # it is either a source gap or a fabricated zero.
            missing.append(label)
    if missing:
        return SegmentStatus(
            INSUFFICIENT_DATA,
            f"{name} carries {share:.1%} of disclosed segment profit in {period}, "
            f"but the model has no segment forecast for {', '.join(missing)}",
            name,
            share,
            period,
            missing,
        )
    return SegmentStatus(
        VERIFIED,
        f"{name} is {share:.1%} of disclosed segment profit in {period}; "
        "segment forecasts reconcile to the group",
        name,
        share,
        period,
    )


def neutralise_bottom_up(text: str) -> str:
    """A consolidated forecast may not call itself bottom-up or segment-reconciled."""
    text = re.sub(r"\bbottom[- ]up\b", "consolidated", text or "", flags=re.IGNORECASE)
    return re.sub(
        r"\bsegment[-\s]reconcil\w*\b", "consolidated", text, flags=re.IGNORECASE
    )


def table(spec, limit: int = 4) -> str:
    """A compact segment table for the dominant segment across periods."""
    found = dominant(spec)
    if found is None:
        return ""
    name, _share, _period = found
    rows = ["| Period | Revenue | Operating profit | Margin |", "|---|---|---|---|"]
    count = 0
    for period in spec.periods:
        segments = spec.segments.get(period.label) or []
        segment = next((item for item in segments if item.name == name), None)
        if segment is None:
            continue
        revenue, profit = float(segment.revenue or 0.0), float(segment.operating_profit or 0.0)
        margin = f"{profit / revenue:.1%}" if revenue else "n/a"
        rows.append(
            f"| {period.label} | {revenue:,.1f} | {profit:,.1f} | {margin} |"
        )
        count += 1
        if count >= limit:
            break
    if count == 0:
        return ""
    return f"Segment forecast ({name}):\n" + "\n".join(rows)
