"""Prices and indicators from Bloomberg.

The backend returns bars as CSV in the same shape the Yahoo vendor produces,
so the router, the market analyst's tools and the indicator layer treat them
identically. Indicators are computed locally from those bars by the shared
helper in ``dataflows.indicators`` — Bloomberg supplies prices, not RSI.

Symbol mapping lives in ``symbols.py`` (``5803.T`` → ``5803 JP Equity``).
"""

from __future__ import annotations

import pandas as pd

from tradingagents.dataflows.bars import parse_bars_csv, prepare_bars
from tradingagents.dataflows.indicators import render_window, value_on_date

from .backend import get_backend


def get_stock_data(symbol: str, start_date: str, end_date: str) -> str:
    """Daily OHLCV as CSV text, from the configured Bloomberg backend."""
    return get_backend().bars(symbol, start_date, end_date)


def bars_frame(symbol: str, as_of_date: str, fill_gaps: bool = True) -> pd.DataFrame:
    """Bloomberg bars as a DataFrame, held to the shared run rules.

    Five years back from the run date is what the loader asks of any vendor;
    the cutoff, gap and staleness rules are the ones in ``dataflows.bars``.
    """
    start = (pd.to_datetime(as_of_date) - pd.DateOffset(years=5)).strftime("%Y-%m-%d")
    text = get_stock_data(symbol, start, as_of_date)
    return prepare_bars(parse_bars_csv(text), symbol, as_of_date, fill_gaps)


def get_stock_indicators(
    symbol: str,
    indicator: str,
    curr_date: str,
    look_back_days: int = 30,
) -> str:
    """The indicator window over Bloomberg's bars; the computation is shared."""

    def on_date(symbols: str, name: str, date: str) -> str:
        return str(value_on_date(bars_frame, symbols, name, date))

    return render_window(bars_frame, on_date, symbol, indicator, curr_date, look_back_days)
