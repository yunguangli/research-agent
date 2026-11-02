"""The Bloomberg vendor is a seam: selectable, typed errors, no hidden dependency."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from tradingagents.dataflows.errors import VendorNotConfiguredError
from tradingagents.dataflows.vendors import bloomberg


def _bars_csv(days: int = 60, end: date = date(2026, 10, 28)) -> str:
    """A synthetic daily series old enough for RSI and ending on ``end``."""
    lines = ["Date,Open,High,Low,Close,Volume"]
    for offset in range(days, 0, -1):
        day = end - timedelta(days=offset - 1)
        close = 100.0 + (days - offset) * 0.5
        lines.append(f"{day.isoformat()},{close},{close + 1},{close - 1},{close},1000000")
    return "\n".join(lines) + "\n"


def test_no_backend_is_a_typed_error_naming_the_variable(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_BLOOMBERG_BACKEND", raising=False)
    with pytest.raises(VendorNotConfiguredError, match="TRADINGAGENTS_BLOOMBERG_BACKEND"):
        bloomberg.get_stock_data("5803 JP", "2026-01-01", "2026-10-28")


def test_an_unknown_backend_lists_the_known_ones(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_BLOOMBERG_BACKEND", "bpipe")
    with pytest.raises(VendorNotConfiguredError, match="not implemented"):
        bloomberg.get_stock_data("5803 JP", "2026-01-01", "2026-10-28")


def test_indicators_are_computed_from_the_backend_bars(monkeypatch):
    """Indicators are a shared computation over the backend's own bars: the
    vendor decides where prices come from, never how RSI is calculated."""

    class BarsBackend:
        name = "bars"

        def bars(self, symbol, start_date, end_date):
            return "# Stock data for test\n\n" + _bars_csv()

    bloomberg.register_backend("bars")(BarsBackend)
    monkeypatch.setenv("TRADINGAGENTS_BLOOMBERG_BACKEND", "bars")

    out = bloomberg.get_stock_indicators("5803 T", "rsi", "2026-10-28", 5)
    assert "## rsi values from" in out
    assert "2026-10-28:" in out
    assert "RSI: Measures momentum" in out   # the shared docs


def test_an_unknown_indicator_lists_the_supported_ones(monkeypatch):
    class BarsBackend:
        name = "bars"

        def bars(self, symbol, start_date, end_date):
            return _bars_csv()

    bloomberg.register_backend("bars")(BarsBackend)
    monkeypatch.setenv("TRADINGAGENTS_BLOOMBERG_BACKEND", "bars")
    with pytest.raises(ValueError, match="not supported"):
        bloomberg.get_stock_indicators("5803 T", "voodoo", "2026-10-28", 5)


def test_fundamentals_route_through_the_same_backend(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_BLOOMBERG_BACKEND", raising=False)
    with pytest.raises(VendorNotConfiguredError, match="TRADINGAGENTS_BLOOMBERG_BACKEND"):
        bloomberg.get_income_statement("5803 JP", "quarterly", "2026-10-28")


def test_the_vendor_is_registered_for_prices_and_statements():
    from tradingagents.dataflows.router import VENDOR_METHODS

    for method in ("get_stock_data", "get_indicators", "get_fundamentals",
                   "get_balance_sheet", "get_cashflow", "get_income_statement"):
        assert "bloomberg" in VENDOR_METHODS[method]


def test_the_blpapi_backend_is_registered():
    """Selecting blpapi must find an implementation, not 'not implemented'."""
    from tradingagents.dataflows.vendors.bloomberg.backend import _BACKENDS

    assert "blpapi" in _BACKENDS


def test_a_registered_backend_is_used(monkeypatch):
    class FakeBackend:
        name = "fake"

        def bars(self, symbol, start_date, end_date):
            return "Date,Close\n2026-10-27,4100.0\n"

    bloomberg.register_backend("fake")(FakeBackend)
    monkeypatch.setenv("TRADINGAGENTS_BLOOMBERG_BACKEND", "fake")
    assert "4100.0" in bloomberg.get_stock_data("5803 JP", "2026-10-01", "2026-10-28")
