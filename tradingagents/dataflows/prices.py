"""A numeric last close for valuation, without parsing a report by eye.

The model's valuation needs one market input — the share price on the run date.
It is read through the configured vendor chain like any other price, so the
valuation moves with the run rather than with a number someone typed once.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta

import pandas as pd

from tradingagents.dataflows.bars import load_bars
from tradingagents.dataflows.errors import NoMarketDataError
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


def _empty_closes() -> pd.Series:
    """No prices: an empty *dated* series, so callers can slice it like any other."""
    return pd.Series(dtype="float64", index=pd.DatetimeIndex([]))


def closes(symbol: str, start_date: str, end_date: str) -> pd.Series:
    """Daily closes over ``[start_date, end_date]`` from the configured price chain.

    Scoring a past decision follows the same chain as the analysis itself —
    Bloomberg first, Yahoo only where it is a configured fallback — so the memory
    log never reads from a source the run would refuse to use.

    No rows for the symbol returns an empty series: the caller leaves the
    decision pending, as it always has. A vendor outage or a configuration
    problem raises, so it is reported instead of retried silently forever.
    """
    try:
        frame = load_bars(symbol, end_date, fill_gaps=False)
    except NoMarketDataError:
        return _empty_closes()
    if frame is None or frame.empty:
        return _empty_closes()

    dates = pd.to_datetime(frame["Date"])
    window = frame[(dates >= pd.to_datetime(start_date)) & (dates <= pd.to_datetime(end_date))]
    if window.empty:
        return _empty_closes()
    return pd.Series(
        pd.to_numeric(window["Close"], errors="coerce").to_numpy(),
        index=pd.DatetimeIndex(pd.to_datetime(window["Date"])),
        dtype="float64",
    )
