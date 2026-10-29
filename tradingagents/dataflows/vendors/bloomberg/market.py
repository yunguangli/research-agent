"""Prices and indicators from Bloomberg.

The backend returns bars; the formatting mirrors the Yahoo vendor so the market
analyst's tools, the verified snapshot and the cache keep working unchanged.

Indicators are not implemented here yet. They must be computed locally from the
bars (Bloomberg supplies prices, not RSI), and that computation currently lives
inside the Yahoo vendor. Until it is extracted into a shared helper, route
``technical_indicators`` to another vendor::

    TRADINGAGENTS_DATA_VENDORS={"core_stock_apis":"bloomberg",
                                "technical_indicators":"alpha_vantage"}
"""

from __future__ import annotations

from tradingagents.dataflows.errors import VendorNotConfiguredError

from .backend import get_backend


def get_stock_data(symbol: str, start_date: str, end_date: str) -> str:
    """Daily OHLCV as CSV text, from the configured Bloomberg backend."""
    return get_backend().bars(symbol, start_date, end_date)


def get_stock_indicators(symbol: str, indicator: str, curr_date: str, look_back_days: int = 30) -> str:
    """Not implemented: indicators need the shared computation helper first."""
    raise VendorNotConfiguredError(
        "The Bloomberg vendor supplies prices but not indicators yet. Until the "
        "indicator computation is shared between vendors, route technical_indicators "
        "to alpha_vantage or yfinance and keep bloomberg for core_stock_apis."
    )
