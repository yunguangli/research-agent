"""Bloomberg prices end to end: symbol mapping, bar rules, chain fallback.

Offline: a registered fake backend stands in for the terminal. The one live
test is marked integration, so it runs only when explicitly asked.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from tradingagents.dataflows import bars as bars_module
from tradingagents.dataflows.errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
    VendorUnavailableError,
)
from tradingagents.dataflows.vendors import bloomberg
from tradingagents.dataflows.vendors.bloomberg.symbols import canonical_ticker, to_bloomberg


def _csv(days: int = 40, end: date = date(2026, 10, 28)) -> str:
    lines = ["Date,Open,High,Low,Close,Volume"]
    for offset in range(days, 0, -1):
        day = end - timedelta(days=offset - 1)
        close = 4000.0 + (days - offset) * 5.0
        lines.append(f"{day.isoformat()},{close},{close + 10},{close - 10},{close},1000000")
    return "\n".join(lines) + "\n"


@pytest.mark.unit
class TestSymbolMapping:
    def test_a_tokyo_ticker_gets_the_japan_key(self):
        assert to_bloomberg("5803.T") == "5803 JP Equity"

    def test_a_us_ticker_gets_the_us_key(self):
        assert to_bloomberg("nvda") == "NVDA US Equity"

    def test_london_and_hong_kong_suffixes(self):
        assert to_bloomberg("AZN.L") == "AZN LN Equity"
        assert to_bloomberg("0700.HK") == "0700 HK Equity"

    def test_an_already_bloomberg_key_passes_through(self):
        assert to_bloomberg("5803 JP Equity") == "5803 JP Equity"

    def test_a_crypto_symbol_is_refused_not_guessed(self):
        with pytest.raises(VendorNotConfiguredError, match="TRADINGAGENTS_BLOOMBERG_TICKERS"):
            to_bloomberg("BTC-USD")

    def test_a_yahoo_index_symbol_is_refused_so_the_chain_can_fall_back(self):
        """^N225 (the Tokyo benchmark) is Yahoo notation: Bloomberg's key is
        NKY Index, and guessing "US Equity" would price the wrong instrument."""
        with pytest.raises(VendorNotConfiguredError, match="No Bloomberg mapping"):
            to_bloomberg("^N225")

    def test_an_unknown_exchange_suffix_is_refused(self):
        with pytest.raises(VendorNotConfiguredError, match="No Bloomberg mapping"):
            to_bloomberg("ABC.XX")

    def test_an_env_override_maps_the_odd_symbol(self, monkeypatch):
        monkeypatch.setenv(
            "TRADINGAGENTS_BLOOMBERG_TICKERS", '{"BTC-USD": "XBTUSD Curncy"}'
        )
        assert to_bloomberg("BTC-USD") == "XBTUSD Curncy"


@pytest.mark.unit
class TestBarRules:
    def test_the_comment_header_is_not_part_of_the_frame(self):
        payload = "# Stock data for 5803 JP Equity\n# Total records: 2\n\n" + _csv(2)
        frame = bars_module.parse_bars_csv(payload)
        assert list(frame.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]
        assert len(frame) == 2

    def test_rows_after_the_run_date_are_cut(self, monkeypatch):
        frame = bars_module.parse_bars_csv(_csv(40))
        out = bars_module.prepare_bars(frame, "5803.T", "2026-10-20", fill_gaps=False)
        assert out["Date"].max() <= pd.Timestamp("2026-10-20")

    def test_a_frame_older_than_the_run_date_is_refused(self):
        frame = bars_module.parse_bars_csv(_csv(5, end=date(2026, 1, 5)))
        with pytest.raises(NoMarketDataError, match="stale"):
            bars_module.prepare_bars(frame, "5803.T", "2026-10-28")


@pytest.mark.unit
class TestChainFallback:
    def test_the_chain_serves_bars_from_the_backend(self, monkeypatch):
        class FakeBackend:
            name = "fake"

            def bars(self, symbol, start_date, end_date):
                return "# header\n\n" + _csv()

        bloomberg.register_backend("fake")(FakeBackend)
        monkeypatch.setenv("TRADINGAGENTS_BLOOMBERG_BACKEND", "fake")
        monkeypatch.setattr(bars_module, "_bar_chain", lambda: ["bloomberg"])

        out = bars_module.load_bars("5803.T", "2026-10-28")
        assert out["Close"].iloc[-1] == pytest.approx(4000.0 + 39 * 5.0)

    def test_bloomberg_down_falls_back_to_the_next_link(self, monkeypatch):
        class DownBackend:
            name = "down"

            def bars(self, symbol, start_date, end_date):
                raise VendorUnavailableError("terminal closed")

        bloomberg.register_backend("down")(DownBackend)
        monkeypatch.setenv("TRADINGAGENTS_BLOOMBERG_BACKEND", "down")
        monkeypatch.setattr(bars_module, "_bar_chain", lambda: ["bloomberg", "yfinance"])

        fallback = bars_module._FETCHERS["yfinance"]
        monkeypatch.setitem(
            bars_module._FETCHERS, "yfinance",
            lambda symbol, as_of, fill_gaps: bars_module.parse_bars_csv(_csv(30)),
        )
        try:
            out = bars_module.load_bars("5803.T", "2026-10-28")
            assert len(out) == 30
        finally:
            bars_module._FETCHERS["yfinance"] = fallback

    def test_bloomberg_alone_reports_the_outage(self, monkeypatch):
        class DownBackend:
            name = "down"

            def bars(self, symbol, start_date, end_date):
                raise VendorUnavailableError("terminal closed")

        bloomberg.register_backend("down")(DownBackend)
        monkeypatch.setenv("TRADINGAGENTS_BLOOMBERG_BACKEND", "down")
        monkeypatch.setattr(bars_module, "_bar_chain", lambda: ["bloomberg"])

        with pytest.raises(VendorUnavailableError, match="terminal closed"):
            bars_module.load_bars("5803.T", "2026-10-28")


@pytest.mark.unit
class TestIdentity:
    """A Bloomberg-configured run names the company from Bloomberg, and any
    failure falls back to the profile it already used."""

    def test_no_backend_configured_means_no_bloomberg_lookup(self, monkeypatch):
        from tradingagents.agents import context

        monkeypatch.delenv("TRADINGAGENTS_BLOOMBERG_BACKEND", raising=False)
        assert context._bloomberg_identity("NVDA") == {}

    def test_a_configured_backend_answers_identity_without_touching_yahoo(self, monkeypatch):
        from tradingagents.agents import context

        class IdentityBackend:
            name = "identity"

            def identity(self, symbol):
                return {"company_name": "FUJIKURA LTD", "sector": "Industrials",
                        "industry": "Electrical Equipment"}

        bloomberg.register_backend("identity")(IdentityBackend)
        monkeypatch.setenv("TRADINGAGENTS_BLOOMBERG_BACKEND", "identity")
        monkeypatch.setattr(context, "get_company_profile",
                            lambda t: (_ for _ in ()).throw(AssertionError("Yahoo was asked")))
        context._identity.cache_clear()
        try:
            found = context._identity("5803.T")
        finally:
            context._identity.cache_clear()
        assert found["company_name"] == "FUJIKURA LTD"
        assert found["sector"] == "Industrials"

    def test_a_backend_without_identity_falls_back_to_the_profile(self, monkeypatch):
        from tradingagents.agents import context

        class NoIdentityBackend:
            name = "noidentity"

            def bars(self, symbol, start_date, end_date):
                return ""

        bloomberg.register_backend("noidentity")(NoIdentityBackend)
        monkeypatch.setenv("TRADINGAGENTS_BLOOMBERG_BACKEND", "noidentity")
        monkeypatch.setattr(context, "get_company_profile", lambda t: {"longName": "Example Corp"})
        context._identity.cache_clear()
        try:
            assert context._identity("EX")["company_name"] == "Example Corp"
        finally:
            context._identity.cache_clear()

    def test_a_backend_that_fails_falls_back_to_the_profile(self, monkeypatch):
        from tradingagents.agents import context
        from tradingagents.dataflows.errors import VendorUnavailableError

        class BrokenBackend:
            name = "broken"

            def identity(self, symbol):
                raise VendorUnavailableError("terminal closed")

        bloomberg.register_backend("broken")(BrokenBackend)
        monkeypatch.setenv("TRADINGAGENTS_BLOOMBERG_BACKEND", "broken")
        monkeypatch.setattr(context, "get_company_profile", lambda t: {"longName": "Fallback Corp"})
        context._identity.cache_clear()
        try:
            assert context._identity("EX")["company_name"] == "Fallback Corp"
        finally:
            context._identity.cache_clear()


@pytest.mark.unit
class TestSettlementCloses:
    """Scoring a decision reads the same price chain as the analysis."""

    @staticmethod
    def _frame():
        return bars_module.parse_bars_csv(_csv(40))

    def test_closes_are_sliced_from_the_chain(self, monkeypatch):
        from tradingagents.dataflows import prices

        monkeypatch.setattr(prices, "load_bars", lambda symbol, as_of, fill_gaps=True: self._frame())
        out = prices.closes("5803.T", "2026-10-20", "2026-10-28")
        assert list(out.index.strftime("%Y-%m-%d"))[-1] == "2026-10-28"
        assert out.index[0] == pd.Timestamp("2026-10-20")

    def test_no_rows_for_a_symbol_is_empty_not_an_error(self, monkeypatch):
        from tradingagents.dataflows import prices
        from tradingagents.dataflows.errors import NoMarketDataError

        def no_data(*args, **kwargs):
            raise NoMarketDataError("XXXXXFAKE", "XXXXXFAKE", "no price rows")

        monkeypatch.setattr(prices, "load_bars", no_data)
        out = prices.closes("XXXXXFAKE", "2026-01-10", "2026-02-01")
        assert out.empty
        # an empty result must still be sliceable the way the caller slices it
        assert len(out[out.index > pd.Timestamp("2026-01-10")]) == 0

    def test_a_vendor_outage_is_raised_so_settlement_can_report_it(self, monkeypatch):
        from tradingagents.dataflows import prices
        from tradingagents.dataflows.errors import VendorUnavailableError

        def down(*args, **kwargs):
            raise VendorUnavailableError("terminal closed")

        monkeypatch.setattr(prices, "load_bars", down)
        with pytest.raises(VendorUnavailableError, match="terminal closed"):
            prices.closes("5803.T", "2026-01-10", "2026-02-01")


@pytest.mark.unit
class TestCanonicalTicker:
    """A Bloomberg-style ticker and this system's spelling are one instrument."""

    @pytest.mark.parametrize("given, expected", [
        ("3711.TT", "3711.TW"),      # Taiwan
        ("5803.JT", "5803.T"),       # Japan
        ("AZN.LN", "AZN.L"),         # London
        ("RIO.AU", "RIO.AX"),        # Australia
        ("SAP.GR", "SAP.DE"),        # Germany
        ("NVDA.US", "NVDA"),         # the US has no suffix here
        ("3711.TW", "3711.TW"),      # already canonical: untouched
        ("NVDA", "NVDA"),
        ("BTC-USD", "BTC-USD"),
        ("600519.SS", "600519.SS"),  # Shanghai: Bloomberg's Sweden code must not claim it
        ("ABC.XX", "ABC.XX"),        # unknown dialect: pass through, fail where a mapping is asked
    ])
    def test_the_dialect_maps_once(self, given, expected):
        assert canonical_ticker(given) == expected

    def test_bloomberg_input_reaches_the_bloomberg_key(self):
        assert to_bloomberg("3711.TT") == "3711 TT Equity"
        assert to_bloomberg("5803.JT") == "5803 JP Equity"

    def test_propagate_canonicalizes_before_the_run(self):
        """The wiring: everything the run keys on — state, memory, corpus,
        report folders — uses the canonical spelling."""
        from tradingagents.default_config import DEFAULT_CONFIG
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        graph = object.__new__(TradingAgentsGraph)
        graph.config = dict(DEFAULT_CONFIG)
        graph._checkpointer_ctx = None
        seen: dict = {}

        def fake_run(company_name, trade_date, **kwargs):
            seen["ticker"] = company_name
            return {}, "Hold"

        graph._run_graph = fake_run
        graph.propagate("3711.TT", "2026-10-30")
        assert seen["ticker"] == "3711.TW"

    def test_the_corpus_resolver_finds_the_folder_under_either_spelling(self, tmp_path):
        from tradingagents.dataflows.vendors.research_corpus.store import resolve_corpus_root

        (tmp_path / "3711.TW").mkdir()
        assert resolve_corpus_root(tmp_path, "3711.TT").name == "3711.TW"


@pytest.mark.integration
def test_a_running_terminal_serves_daily_bars(monkeypatch):
    """Requires the Bloomberg Terminal, logged in, and the blpapi SDK."""
    pytest.importorskip("blpapi")
    monkeypatch.setenv("TRADINGAGENTS_BLOOMBERG_BACKEND", "blpapi")
    text = bloomberg.get_stock_data("5803.T", "2026-10-01", "2026-10-28")
    assert "5803 JP Equity" in text
    assert "Date,Open,High,Low,Close,Volume" in text
    assert "2026-10-28" in text
