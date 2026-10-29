"""The Bloomberg vendor is a seam: selectable, typed errors, no hidden dependency."""

from __future__ import annotations

import pytest

from tradingagents.dataflows.errors import VendorNotConfiguredError
from tradingagents.dataflows.vendors import bloomberg


def test_no_backend_is_a_typed_error_naming_the_variable(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_BLOOMBERG_BACKEND", raising=False)
    with pytest.raises(VendorNotConfiguredError, match="TRADINGAGENTS_BLOOMBERG_BACKEND"):
        bloomberg.get_stock_data("5803 JP", "2026-01-01", "2026-10-28")


def test_an_unknown_backend_lists_the_known_ones(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_BLOOMBERG_BACKEND", "blpapi")
    with pytest.raises(VendorNotConfiguredError, match="not implemented"):
        bloomberg.get_stock_data("5803 JP", "2026-01-01", "2026-10-28")


def test_indicators_say_what_to_do_instead(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_BLOOMBERG_BACKEND", "blpapi")
    with pytest.raises(VendorNotConfiguredError, match="technical_indicators"):
        bloomberg.get_stock_indicators("5803 JP", "rsi", "2026-10-28")


def test_fundamentals_route_through_the_same_backend(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_BLOOMBERG_BACKEND", raising=False)
    with pytest.raises(VendorNotConfiguredError, match="TRADINGAGENTS_BLOOMBERG_BACKEND"):
        bloomberg.get_income_statement("5803 JP", "quarterly", "2026-10-28")


def test_the_vendor_is_registered_for_prices_and_statements():
    from tradingagents.dataflows.router import VENDOR_METHODS

    for method in ("get_stock_data", "get_indicators", "get_fundamentals",
                   "get_balance_sheet", "get_cashflow", "get_income_statement"):
        assert "bloomberg" in VENDOR_METHODS[method]


def test_a_registered_backend_is_used(monkeypatch):
    class FakeBackend:
        name = "fake"

        def bars(self, symbol, start_date, end_date):
            return "Date,Close\n2026-10-27,4100.0\n"

    bloomberg.register_backend("fake")(FakeBackend)
    monkeypatch.setenv("TRADINGAGENTS_BLOOMBERG_BACKEND", "fake")
    assert "4100.0" in bloomberg.get_stock_data("5803 JP", "2026-10-01", "2026-10-28")
