"""The unverified-model mode: no model figure reaches the decision."""

from __future__ import annotations

from unittest.mock import MagicMock

from tradingagents.agents.managers.portfolio_manager import (
    _normalize_actions,
    create_portfolio_manager,
)
from tradingagents.agents.rating import RATING_REVIEW
from tradingagents.agents.schemas import (
    HolderAction,
    NewInvestorAction,
    PortfolioDecision,
    PortfolioRating,
)
from tradingagents.reporting import _header
from tradingagents.research.packets import (
    UNAVAILABLE,
    UNVERIFIED_FUNDAMENTALS_NOTICE,
    filter_decision_inputs,
)
from tradingagents.research.shares import basis_report


def _state(**overrides) -> dict:
    state = {
        "company_of_interest": "TEST",
        "trade_date": "2026-01-01",
        "market_report": "m",
        "sentiment_report": "",
        "news_report": "",
        "fundamentals_report": "manual EPS 200, fair value 7,000",
        "investment_debate_state": {"challenge_history": "", "history": ""},
        "investment_plan": "plan",
        "trader_investment_plan": "trade",
        "risk_debate_state": {
            "history": "", "aggressive_history": "", "conservative_history": "",
            "neutral_history": "", "count": 1, "latest_speaker": "Judge",
            "current_aggressive_response": "", "current_conservative_response": "",
            "current_neutral_response": "",
        },
        "portfolio_context": "",
        "decision_horizon": "3-6 months",
        "model_status": "UNVERIFIED",
        "research_model_source": "unavailable",
        "research_package": "FUNDAMENTAL MODEL ...",
        "case_table": "| case |",
        "sensitivity_table": "| id |",
        "metrics_table": "| metrics |",
    }
    state.update(overrides)
    return state


def test_an_unverified_packet_removes_every_model_output():
    packet = filter_decision_inputs(_state())

    assert packet["verified"] is False
    assert packet["package"] == UNAVAILABLE
    assert packet["fundamentals"] == UNVERIFIED_FUNDAMENTALS_NOTICE
    assert packet["case_table"] == ""
    assert packet["sensitivity_table"] == ""
    assert packet["metrics_table"] == ""
    assert "not established" in packet["valuation_notice"]


def test_a_verified_packet_is_unchanged():
    packet = filter_decision_inputs(
        _state(model_status="VERIFIED", research_model_source="built")
    )

    assert packet["verified"] is True
    assert packet["package"] == "FUNDAMENTAL MODEL ..."
    assert packet["fundamentals"].startswith("manual EPS")


def test_an_unverified_model_produces_a_deterministic_not_rated_decision():
    llm = MagicMock()

    result = create_portfolio_manager(llm)(_state())

    assert result["final_rating"] == RATING_REVIEW
    decision = result["final_trade_decision"]
    assert "**Rating**: Not Rated" in decision
    assert "Do not initiate pending model verification" in decision
    assert "Review exposure pending model verification" in decision
    assert "**Controlling fair value**: not provided" in decision
    assert result["controlling_fair_value_low"] is None
    llm.invoke.assert_not_called()


def test_the_unverified_decision_never_quotes_a_model_rating():
    decision = create_portfolio_manager(MagicMock())(_state())["final_trade_decision"]

    assert "**Rating**: Sell" not in decision
    assert "**Rating**: Buy" not in decision
    assert "Sell/Reduce" not in decision


def test_an_event_basis_may_still_produce_a_decision_but_carries_no_valuation():
    decision = PortfolioDecision(
        rating=PortfolioRating.SELL,
        executive_summary="Delisting announced.",
        investment_thesis="Mechanical exit.",
        fair_value_low=7000.0,
        fair_value_high=8000.0,
        controlling_low_source_id="independent_fundamental_analysis_s9",
        controlling_high_source_id="independent_fundamental_analysis_s9",
    )
    structured = MagicMock()
    structured.invoke.return_value = decision
    llm = MagicMock()
    llm.with_structured_output.return_value = structured

    result = create_portfolio_manager(llm)(
        _state(non_model_decision_basis={
            "basis_type": "delisting",
            "deterministic_validation_passed": True,
            "depends_on_model_figures": False,
        })
    )

    text = result["final_trade_decision"]
    assert "**Fair Value**" not in text
    assert "Valuation sources" not in text
    assert result["controlling_fair_value_high"] is None


def test_a_technical_basis_is_not_a_valid_exception():
    llm = MagicMock()

    result = create_portfolio_manager(llm)(
        _state(non_model_decision_basis={
            "basis_type": "technical",
            "deterministic_validation_passed": True,
            "depends_on_model_figures": False,
        })
    )

    assert result["final_rating"] == RATING_REVIEW
    llm.invoke.assert_not_called()


def test_a_sell_cannot_publish_a_wait_action():
    decision = PortfolioDecision(
        rating=PortfolioRating.SELL,
        executive_summary="Exit.",
        investment_thesis="Broken.",
        new_investor_action=NewInvestorAction.WAIT,
        existing_holder_action=HolderAction.HOLD,
    )

    notes = _normalize_actions(decision)

    assert decision.new_investor_action is NewInvestorAction.AVOID
    assert decision.existing_holder_action is HolderAction.REDUCE
    assert notes


def test_share_bases_report_a_fallback_rather_than_pretending_it_is_declared():
    from tradingagents.research.schemas import (
        ModelSpec,
        ShareBase,
        ValuationSpec,
    )

    model = ModelSpec(
        ticker="T",
        company="T",
        currency="USD",
        unit="USD_m",
        fiscal_year_end_month=12,
        periods=[],
        actuals={},
        guidance={},
        forecast={},
        bridges=[],
        share_bases=[ShareBase("full_share", 10.0)],
        valuation=ValuationSpec(
            price=20.0, fair_value_multiple_low=10.0, fair_value_multiple_high=12.0
        ),
    )

    report = basis_report(model)

    assert report["market_cap_basis"] == "full_share (fallback — not verified)"
    assert report["dilution_sensitivity_basis"] == "not established"


def test_the_unverified_header_states_the_status_and_suppression():
    header = _header("TEST", _state(
        final_trade_decision="**Rating**: Not Rated",
        final_rating=RATING_REVIEW,
        overall_status="Not Rated",
        decision_status="Provisional",
        blocking_issue='[{"challenge_type": "Forecast share basis", '
                       '"affected_fields": ["Forecast EPS"], '
                       '"verification_result": "unresolved", '
                       '"repair_attempted": true, '
                       '"failure_reason": "diluted shares not established", '
                       '"publication_impact": "model EPS, valuation and rating suppressed"}]',
    ), None)

    assert "- Rating: Not Rated (provisional)" in header
    assert "- Overall status: Not Rated" in header
    assert "- Decision status: Provisional" in header
    assert "- Model output: suppressed" in header
    assert "- Blocking challenge: Forecast share basis" in header
    assert "- Affected fields: Forecast EPS" in header
    assert "- Repair failure reason: diluted shares not established" in header
