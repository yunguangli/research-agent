"""Structured technical evidence with stable IDs.

The Market Analyst writes prose, and the Portfolio Manager used to re-state the
technical view as free text in its audit. That made "verified technical
evidence" a claim about prose. This module gives the technical leg the same
shape as every other evidence item: a stable id, two comparable values, a
direction, a timeframe and source refs. The id names the fact, never the value,
so `tech_close_below_50sma` can be true this week and false next week without
changing the audit's vocabulary.

Validation is arithmetic and local: an item verifies only when its direction
follows from the two numbers it prints. It is not a substitute for the
indicator data - it refuses an internally inconsistent claim, which is the
failure mode that reached the decision audit before.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

# id -> (metric, direction the id asserts)
CATALOGUE: dict[str, tuple[str, str]] = {
    "tech_close_below_10ema": ("close_vs_10ema", "below"),
    "tech_close_above_10ema": ("close_vs_10ema", "above"),
    "tech_close_below_50sma": ("close_vs_50sma", "below"),
    "tech_close_above_50sma": ("close_vs_50sma", "above"),
    "tech_close_below_200sma": ("close_vs_200sma", "below"),
    "tech_close_above_200sma": ("close_vs_200sma", "above"),
    "tech_macd_negative": ("macd_histogram", "negative"),
    "tech_macd_positive": ("macd_histogram", "positive"),
    "tech_atr_elevated": ("atr_pct", "above"),
}

VERIFIED = "VERIFIED"
UNVERIFIED = "UNVERIFIED"

# The analyst appends one JSON array under this marker; the reader never sees it.
MARKER = "TECHNICAL_EVIDENCE:"
_BLOCK = re.compile(
    r"TECHNICAL_EVIDENCE:\s*(\[[\s\S]*?\])\s*(?:\n|$)", re.IGNORECASE
)


@dataclass
class TechnicalEvidence:
    evidence_id: str
    metric: str
    observed_value: float | str | None
    comparison_value: float | str | None
    direction: str
    timeframe: str
    source_refs: list[str] = field(default_factory=list)
    verification_status: str = UNVERIFIED
    supports_action: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> TechnicalEvidence:
        return cls(
            evidence_id=str(data.get("evidence_id") or ""),
            metric=str(data.get("metric") or ""),
            observed_value=data.get("observed_value"),
            comparison_value=data.get("comparison_value"),
            direction=str(data.get("direction") or ""),
            timeframe=str(data.get("timeframe") or ""),
            source_refs=list(data.get("source_refs") or []),
            verification_status=str(data.get("verification_status") or UNVERIFIED),
            supports_action=bool(data.get("supports_action", False)),
        )


def _number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("x", "").strip().rstrip("%")
    try:
        return float(text)
    except ValueError:
        return None


def _direction_holds(direction: str, observed: float, comparison: float) -> bool:
    if direction == "below":
        return observed < comparison
    if direction == "above":
        return observed > comparison
    if direction == "negative":
        return observed < comparison
    if direction == "positive":
        return observed > comparison
    return False


def validate(item: TechnicalEvidence) -> TechnicalEvidence:
    """Mark an item VERIFIED only when its own numbers support its direction."""
    entry = CATALOGUE.get(item.evidence_id)
    if entry is None:
        item.verification_status = f"{UNVERIFIED}: unknown evidence id"
        return item
    metric, direction = entry
    if item.metric != metric:
        item.verification_status = (
            f"{UNVERIFIED}: {item.evidence_id} measures {metric}, not {item.metric}"
        )
        return item
    if item.direction != direction:
        item.verification_status = (
            f"{UNVERIFIED}: {item.evidence_id} asserts {direction}, not {item.direction}"
        )
        return item
    observed = _number(item.observed_value)
    comparison = _number(item.comparison_value)
    if observed is None or comparison is None:
        item.verification_status = f"{UNVERIFIED}: the compared values are not numbers"
        return item
    if direction in {"negative", "positive"} and comparison != 0:
        comparison = 0.0
    if not _direction_holds(direction, observed, comparison):
        item.verification_status = (
            f"{UNVERIFIED}: {observed:g} does not support {direction} {comparison:g}"
        )
        return item
    if not str(item.timeframe or "").strip():
        item.verification_status = f"{UNVERIFIED}: no timeframe stated"
        return item
    if not item.source_refs:
        item.verification_status = f"{UNVERIFIED}: no source reference"
        return item
    item.verification_status = VERIFIED
    return item


def parse(text: str) -> tuple[str, list[TechnicalEvidence]]:
    """``(report without the block, validated evidence)``."""
    match = _BLOCK.search(text or "")
    if not match:
        return text or "", []
    try:
        raw = json.loads(match.group(1))
    except ValueError:
        raw = []
    items: list[TechnicalEvidence] = []
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict):
                items.append(validate(TechnicalEvidence.from_dict(entry)))
    return (text[: match.start()] + text[match.end():]).rstrip() + "\n", items


def render(items: list[TechnicalEvidence]) -> str:
    """The block the Portfolio Manager reads, verified items only."""
    verified = [item for item in items if item.verification_status == VERIFIED]
    if not verified:
        return "No verified technical evidence was produced."
    lines = ["| Evidence ID | Metric | Observed | Comparison | Direction | Timeframe |"]
    lines.append("|---|---|---|---|---|---|")
    for item in verified:
        lines.append(
            f"| {item.evidence_id} | {item.metric} | {item.observed_value} | "
            f"{item.comparison_value} | {item.direction} | {item.timeframe} |"
        )
    return "\n".join(lines)
