"""A numeric last close for valuation, without parsing a report by eye.

The model's valuation needs one market input — the share price on the run date.
It is read through the configured vendor chain like any other price, so the
valuation moves with the run rather than with a number someone typed once.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta

from tradingagents.dataflows.router import route_to_vendor


def parse_last_close(text: str) -> float | None:
    """The last ``Close`` in the CSV a stock-data vendor returns."""
    if not text or text.startswith(("DATA_UNAVAILABLE", "NO_DATA_AVAILABLE")):
        return None
    rows = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    if len(rows) < 2:
        return None
    try:
        reader = csv.DictReader(rows)
        if not reader.fieldnames or "Close" not in reader.fieldnames:
            return None
        last = None
        for row in reader:
            last = row
        return float(last["Close"]) if last and last.get("Close") else None
    except (csv.Error, KeyError, TypeError, ValueError):
        return None


def last_close(symbol: str, as_of: str, lookback_days: int = 15) -> float | None:
    """Latest close on or before ``as_of``, or ``None`` if no vendor could serve it.

    A valuation that cannot get a price is a degraded valuation, not a failed
    run: the caller falls back to its own price and says so.
    """
    try:
        as_of_date = date.fromisoformat(str(as_of)[:10])
    except ValueError:
        return None
    start = (as_of_date - timedelta(days=lookback_days)).isoformat()
    try:
        text = route_to_vendor("get_stock_data", symbol, start, as_of_date.isoformat())
    except Exception:  # noqa: BLE001 - a price lookup must never end a run
        return None
    return parse_last_close(text)
