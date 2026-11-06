"""Which model figures a repair replaced, and whether prose still uses one as current.

A repair rewrites v1 into v2. The old values stay in the repair log so the report
can say what moved, but no investor-facing section may present one of them as the
current number. A sentence that *names* the old value as superseded is a correct
disclosure, not a violation; the distinction is the reason this module exists
instead of a bare number scan.

The parsing lives here so the report builder and the graph can share it without a
circular import (``reporting`` may not import ``trading_graph``).
"""

from __future__ import annotations

import json
import re

# A sentence that frames the figure as old is disclosing the repair, not using it.
_SUPERSEDED_MARKER = re.compile(
    r"\bsuperseded\b|\bstale\b|\breplaced\b|\bno longer\b|\brejected\b|\bv1\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def values_from_log(state: dict) -> set[float]:
    """The v1 figures a model repair replaced, parsed from the run's repair log."""
    try:
        log = json.loads(str(state.get("model_repair_log") or "{}"))
    except (TypeError, ValueError):
        return set()
    if not isinstance(log, dict):
        return set()
    values: set[float] = set()
    for change in log.get("changes") or []:
        if not isinstance(change, dict):
            continue
        old, new = change.get("old"), change.get("new")
        if (
            isinstance(old, (int, float))
            and not isinstance(old, bool)
            and old
            and (not isinstance(new, (int, float)) or abs(float(new) - float(old)) > 1e-9)
        ):
            values.add(float(old))
    return values


# A figure is controlling when the decision is built from it. A repaired segment
# detail does not invalidate a debate sentence; a price, EPS or fair value does.
_CONTROLLING_TOKENS = ("price", "eps", "fair value", "valuation", "market cap")


def controlling_values_from_log(state: dict) -> set[float]:
    """The superseded figures that control a rating, price target or fair value."""
    try:
        log = json.loads(str(state.get("model_repair_log") or "{}"))
    except (TypeError, ValueError):
        return set()
    if not isinstance(log, dict):
        return set()
    values: set[float] = set()
    for change in log.get("changes") or []:
        if not isinstance(change, dict):
            continue
        field = str(change.get("field") or "").lower()
        if not any(token in field for token in _CONTROLLING_TOKENS):
            continue
        old, new = change.get("old"), change.get("new")
        if (
            isinstance(old, (int, float))
            and not isinstance(old, bool)
            and old
            and (not isinstance(new, (int, float)) or abs(float(new) - float(old)) > 1e-9)
        ):
            values.add(float(old))
    return values


def controlling_pairs_from_log(state: dict) -> list[tuple[float, float]]:
    """Controlling (old, new) pairs, for deciding whether prose uses the old one."""
    try:
        log = json.loads(str(state.get("model_repair_log") or "{}"))
    except (TypeError, ValueError):
        return []
    if not isinstance(log, dict):
        return []
    pairs: list[tuple[float, float]] = []
    for change in log.get("changes") or []:
        if not isinstance(change, dict):
            continue
        field = str(change.get("field") or "").lower()
        if not any(token in field for token in _CONTROLLING_TOKENS):
            continue
        old, new = change.get("old"), change.get("new")
        if (
            isinstance(old, (int, float))
            and not isinstance(old, bool)
            and old
            and isinstance(new, (int, float))
            and not isinstance(new, bool)
            and abs(float(new) - float(old)) > 1e-9
        ):
            pairs.append((float(old), float(new)))
    return pairs


def uses_superseded(text: str, pairs: list[tuple[float, float]]) -> bool:
    """Whether a sentence uses an old figure without also naming its replacement.

    A sentence that prints both values is describing the repair or the conflict
    ("29,485 versus 32,980; I use 32,980"), which is a disclosure. A sentence that
    prints only the old value is using it as current.
    """
    if not text or not pairs:
        return False
    for sentence in _SENTENCE.split(text):
        if not sentence.strip() or _SUPERSEDED_MARKER.search(sentence):
            continue
        numbers = []
        for token in _NUMBER.findall(sentence):
            try:
                numbers.append(float(token.replace(",", "")))
            except ValueError:
                continue
        for old, new in pairs:
            if not any(_matches(number, {old}) for number in numbers):
                continue
            if not any(_matches(number, {new}) for number in numbers):
                return True
    return False


def _matches(number: float, values: set[float]) -> bool:
    return any(
        abs(number - value) <= max(0.005, abs(value) * 0.002) for value in values
    )


def cited_values(text: str, values: set[float]) -> set[float]:
    """The superseded values the text prints, whether as current or as old."""
    found: set[float] = set()
    if not text or not values:
        return found
    for token in _NUMBER.findall(text or ""):
        try:
            number = float(token.replace(",", ""))
        except ValueError:
            continue
        if _matches(number, values):
            found.update(v for v in values if _matches(number, {v}))
    return found


def cites_as_current(text: str, values: set[float]) -> bool:
    """Whether any sentence presents a superseded figure as the current one."""
    if not text or not values:
        return False
    for sentence in _SENTENCE.split(text):
        if not sentence.strip():
            continue
        if _SUPERSEDED_MARKER.search(sentence):
            continue
        if cited_values(sentence, values):
            return True
    return False
