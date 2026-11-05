"""PM resolution: which price and valuation control, and why.

The parser exists before the prompt so a conflict is detectable from a stored
decision as well as from a live run.
"""

from __future__ import annotations

import re

FIELDS = (
    "controlling_price",
    "controlling_valuation",
    "entry_condition",
    "contradiction_detected",
    "resolution",
)

_PRICE = re.compile(r"(?i)\bentry[^.\n]{0,40}?([\d][\d,]*(?:\.\d+)?)")
_FIELD = re.compile(r"(?m)^\*\*([^*]+)\*\*:\s*(.+?)\s*$")


def parse_pm_resolution(text: str) -> dict[str, str]:
    """The bold-labelled decision fields, keyed by snake_case name."""
    parsed: dict[str, str] = {}
    for match in _FIELD.finditer(text or ""):
        key = re.sub(r"[^a-z0-9]+", "_", match.group(1).strip().lower()).strip("_")
        if key in FIELDS:
            parsed[key] = match.group(2).strip()
    return parsed


def entry_price(text: str) -> float | None:
    """The entry price stated in an entry-condition sentence, if any."""
    match = _PRICE.search(text or "")
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def contradiction_detected(entry: float | None, fair_value: float | None) -> bool:
    """An entry above the base fair value pays more than the value it buys."""
    if entry is None or fair_value is None or fair_value <= 0:
        return False
    return entry > fair_value * 1.001
