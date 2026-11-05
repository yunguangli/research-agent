"""The model price and the market close are compared, not assumed equal."""

from __future__ import annotations

from tradingagents.graph.trading_graph import apply_price_conflict, price_conflict_reason
from tradingagents.reporting import write_report_tree


def test_a_two_and_a_half_percent_gap_is_a_conflict():
    reason = price_conflict_reason(6985.0, 6812.0)

    assert "model price" in reason and "market close" in reason
    assert "+2.5%" in reason


def test_a_gap_within_one_percent_is_not_a_conflict():
    assert price_conflict_reason(6985.0, 6950.0) == ""
    assert price_conflict_reason(6985.0, 6812.0) != ""


def test_missing_prices_are_not_a_conflict():
    assert price_conflict_reason(None, 6812.0) == ""
    assert price_conflict_reason(6985.0, None) == ""
    assert price_conflict_reason(0, 6812.0) == ""


def test_a_conflict_degrades_integrity_and_makes_the_decision_provisional():
    state = {
        "price_conflict": price_conflict_reason(6985.0, 6812.0),
        "decision_integrity": "validated",
        "decision_status": "FINAL",
    }

    apply_price_conflict(state)

    assert state["decision_integrity"].startswith("DEGRADED")
    assert "market close" in state["decision_integrity"]
    assert state["decision_status"] == "PROVISIONAL"


def test_an_existing_reason_list_gains_the_conflict_once():
    state = {
        "price_conflict": price_conflict_reason(6985.0, 6812.0),
        "decision_integrity": "DEGRADED: structured output fell back",
        "decision_status": "PROVISIONAL",
    }

    apply_price_conflict(state)
    apply_price_conflict(state)

    assert state["decision_integrity"].count("market close") == 1


def test_the_conflict_is_disclosed_once_in_the_report(tmp_path):
    state = {
        "trade_date": "2026-11-05",
        "price_conflict": price_conflict_reason(6985.0, 6812.0),
        "final_trade_decision": "**Rating**: Hold\n\n**Executive Summary**: No change.",
    }
    write_report_tree(state, "TEST", tmp_path, html=False)
    main = (tmp_path / "complete_report.md").read_text(encoding="utf-8")

    assert main.count("Price conflict:") == 1
