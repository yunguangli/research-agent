"""Purpose-specific packets and the suppressed-method reconstruction guard."""

from __future__ import annotations

import json

from tradingagents.agents.managers.portfolio_manager import _invalidated_decision
from tradingagents.research import packets


def _state(*, pe: str = "SUPPRESSED", **overrides):
    state = {
        "valuation_method_statuses": json.dumps({"pe": pe, "ev_ebitda": "VERIFIED"}),
        "research_fair_value_verified": True,
        "research_fair_value_low": 6913.0,
        "research_fair_value_high": 8209.0,
        "fundamental_packet": "Net profit 128.0; operating profit 190.0.",
        "valuation_packet": "",
    }
    state.update(overrides)
    return state


def test_pe_suppression_reads_the_method_matrix():
    assert packets.pe_suppressed(_state(pe="SUPPRESSED")) is True
    assert packets.pe_suppressed(_state(pe="VERIFIED")) is False
    assert packets.pe_suppressed({}) is True


def test_the_fundamental_packet_keeps_earnings_and_forbids_combination():
    text = packets.fundamental_packet(_state())

    assert "Net profit 128.0" in text
    assert "Do not combine them with price" in text


def test_the_valuation_packet_is_empty_when_pe_is_suppressed():
    text = packets.valuation_packet(_state())

    assert "P/E suppressed" in text
    assert "no P/E" in text


def test_the_valuation_packet_carries_the_band_when_pe_verified():
    text = packets.valuation_packet(_state(pe="VERIFIED"))

    assert "6,913-8,209" in text


def test_a_multiple_derived_from_earnings_is_a_reconstruction():
    findings = packets.reconstruction_findings(
        "At 35.5x EPS 211.9 the stock is full.", _state()
    )

    assert findings and "derives a multiple" in findings[0]


def test_an_explicit_pe_is_a_reconstruction():
    findings = packets.reconstruction_findings("The P/E is 35x.", _state())

    assert any("states a P/E" in finding for finding in findings)


def test_a_price_target_is_a_reconstruction():
    findings = packets.reconstruction_findings(
        "My fair value is 7,400 per share.", _state()
    )

    assert any("target or fair value" in finding for finding in findings)


def test_an_ev_multiple_is_not_a_reconstruction():
    findings = packets.reconstruction_findings(
        "15.4x EV/EBITDA is undemanding.", _state()
    )

    assert findings == []


def test_a_verified_pe_is_not_policed():
    findings = packets.reconstruction_findings(
        "At 35.5x EPS 211.9 the stock is full.", _state(pe="VERIFIED")
    )

    assert findings == []


def test_an_invalidated_decision_publishes_no_rating():
    result = _invalidated_decision(_state(), ["states a P/E"])

    assert result["final_rating"] == "REVIEW"
    assert result["decision_status"] == "PROVISIONAL"
    assert "Decision invalidated" in result["final_trade_decision"]
    assert json.loads(result["decision_audit_result"])["validation_passed"] is False
    # The finding is named, not just described in prose.
    findings = json.loads(result["decision_findings"])
    assert findings[0]["code"] == "PM_RECONSTRUCTED_SUPPRESSED_PE"
