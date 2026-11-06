"""Output-level statuses, action consistency and publication hygiene."""

from __future__ import annotations

from unittest.mock import MagicMock

from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager
from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating
from tradingagents.reporting import _header, write_report_tree
from tradingagents.research.sensitivity import Sensitivity, has_effect
from tradingagents.research.status import (
    SUPPRESSED,
    UNAVAILABLE,
    VERIFIED,
    assess,
)

DECLARED = {
    "market_cap_basis": "period_end_net",
    "forecast_eps_basis": "diluted_average",
    "dilution_sensitivity_basis": "full_conversion",
}


def test_a_fallback_share_basis_suppresses_per_share_valuation():
    status = assess(
        model_status=VERIFIED,
        basis={
            "market_cap_basis": "full_share (fallback — not verified)",
            "forecast_eps_basis": "full_share (fallback — not verified)",
            "dilution_sensitivity_basis": "not established",
        },
        has_band=True,
        sensitivity_count=3,
    )

    assert status["operating_model_status"] == VERIFIED
    assert status["eps_status"] == SUPPRESSED
    assert status["valuation_status"] == SUPPRESSED
    assert status["sensitivity_status"] == UNAVAILABLE
    assert status["valuation_controlling"] is False


def test_the_operating_model_can_verify_while_the_valuation_does_not():
    status = assess(model_status=VERIFIED, basis=DECLARED, has_band=True, sensitivity_count=2)

    assert status["operating_model_status"] == VERIFIED
    assert status["eps_status"] == VERIFIED
    assert status["valuation_status"] == VERIFIED
    assert status["sensitivity_status"] == VERIFIED


def test_a_material_per_share_challenge_suppresses_eps_but_not_the_operating_model():
    status = assess(
        model_status=VERIFIED,
        basis=DECLARED,
        has_band=True,
        challenge_fields=["forecast diluted shares", "FY3/28 EPS"],
        sensitivity_count=2,
    )

    assert status["operating_model_status"] == VERIFIED
    assert status["eps_status"] == SUPPRESSED
    assert status["valuation_status"] == SUPPRESSED


def test_an_unverified_operating_model_suppresses_everything():
    status = assess(model_status="UNVERIFIED", basis=DECLARED, has_band=True)

    assert status["operating_model_status"] == "UNVERIFIED"
    assert status["eps_model_status"] == SUPPRESSED
    assert status["valuation_status"] == SUPPRESSED
    assert status["valuation_method_statuses"]["pe"] == SUPPRESSED


def test_an_invalid_eps_basis_leaves_ev_ebitda_verified():
    """Component independence: P/E depends on EPS, EV/EBITDA does not."""
    status = assess(
        model_status=VERIFIED,
        basis={
            "market_cap_basis": "period_end_net",
            "forecast_eps_basis": "full_share (fallback — not verified)",
            "dilution_sensitivity_basis": "full_conversion",
        },
        has_band=True,
        price=100.0,
        has_depreciation=True,
        has_net_debt=True,
        ev_ebitda_multiple=12.0,
    )

    assert status["operating_model_status"] == VERIFIED
    assert status["eps_model_status"] == SUPPRESSED
    assert status["valuation_method_statuses"]["pe"] == SUPPRESSED
    assert status["valuation_method_statuses"]["ev_ebitda"] == VERIFIED
    assert status["valuation_status"] == VERIFIED
    assert status["valuation_controlling"] is False   # the band is P/E-based


def test_a_shock_with_no_effect_is_not_a_sensitivity():
    dead = Sensitivity(
        sensitivity_id="x", driver="x", model_field="x", base_assumption=1.0,
        alternative_assumption=2.0,
    )

    assert has_effect(dead) is False


def test_a_shock_that_moves_eps_is_a_sensitivity():
    live = Sensitivity(
        sensitivity_id="x", driver="x", model_field="x", base_assumption=1.0,
        alternative_assumption=2.0, eps_impact=0.5,
    )

    assert has_effect(live) is True


def test_the_header_reports_each_output_status_separately():
    header = _header(
        "TEST",
        {
            "trade_date": "2026-11-03",
            "model_status": "VERIFIED",
            "operating_model_status": "VERIFIED",
            "eps_status": "SUPPRESSED",
            "valuation_status": "SUPPRESSED",
            "sensitivity_status": "UNAVAILABLE",
            "final_trade_decision": "**Rating**: Hold",
            "final_rating": "Hold",
        },
        None,
    )

    assert "- Operating model: VERIFIED" in header
    assert "- EPS model: SUPPRESSED" in header
    assert "- Valuation: CALCULATION_STATUS=SUPPRESSED" in header
    assert "- MODEL_NODE_SENSITIVITY_STATUS: UNAVAILABLE" in header


def test_pm_actions_stated_only_in_prose_become_advisory():
    decision = PortfolioDecision(
        rating=PortfolioRating.HOLD,
        executive_summary="New money should wait; existing holders keep the core.",
        investment_thesis="The chart is weak and ATR is high.",
    )
    structured = MagicMock()
    structured.invoke.return_value = decision      # the retry does not fix it
    llm = MagicMock()
    llm.with_structured_output.return_value = structured

    result = create_portfolio_manager(llm)({
        "company_of_interest": "TEST",
        "trade_date": "2026-01-01",
        "market_report": "m", "sentiment_report": "", "news_report": "",
        "fundamentals_report": "f",
        "investment_debate_state": {"challenge_history": "", "history": ""},
        "investment_plan": "plan", "trader_investment_plan": "trade",
        "risk_debate_state": {
            "history": "", "aggressive_history": "", "conservative_history": "",
            "neutral_history": "", "count": 1, "latest_speaker": "Judge",
            "current_aggressive_response": "", "current_conservative_response": "",
            "current_neutral_response": "",
        },
        "portfolio_context": "", "decision_horizon": "3-6 months",
        "model_status": "VERIFIED",
        "research_fair_value_verified": True,
    })

    assert result["action_status"] == "advisory"
    assert "advisory, not schema-validated" in result["final_trade_decision"]
    assert structured.invoke.call_count == 2


def test_the_pm_decision_is_invalidated_when_it_reconstructs_a_suppressed_pe():
    decision = PortfolioDecision(
        rating=PortfolioRating.HOLD,
        executive_summary="At 35.5x EPS 211.9 the stock is full.",
        investment_thesis="No margin of safety.",
    )
    structured = MagicMock()
    structured.invoke.return_value = decision
    llm = MagicMock()
    llm.with_structured_output.return_value = structured

    result = create_portfolio_manager(llm)({
        "company_of_interest": "TEST",
        "trade_date": "2026-01-01",
        "market_report": "m", "sentiment_report": "", "news_report": "",
        "fundamentals_report": "f",
        "investment_debate_state": {"challenge_history": "", "history": ""},
        "investment_plan": "plan", "trader_investment_plan": "trade",
        "risk_debate_state": {
            "history": "", "aggressive_history": "", "conservative_history": "",
            "neutral_history": "", "count": 1, "latest_speaker": "Judge",
            "current_aggressive_response": "", "current_conservative_response": "",
            "current_neutral_response": "",
        },
        "portfolio_context": "", "decision_horizon": "3-6 months",
        "model_status": "VERIFIED",
        "eps_status": "SUPPRESSED",
        "research_fair_value_verified": False,
        "research_fair_value_low": 5000.0,
        "research_fair_value_high": 9000.0,
    })

    text = result["final_trade_decision"]
    assert result["final_rating"] == "REVIEW"
    assert "Decision invalidated" in text
    assert result["controlling_fair_value_low"] is None
    assert result["decision_status"] == "PROVISIONAL"
    assert structured.invoke.call_count == 2


def test_a_clean_retry_after_a_reconstruction_violation_publishes():
    dirty = PortfolioDecision(
        rating=PortfolioRating.HOLD,
        executive_summary="At 35.5x EPS 211.9 the stock is full.",
        investment_thesis="No margin of safety.",
    )
    clean = PortfolioDecision(
        rating=PortfolioRating.HOLD,
        executive_summary="Earnings are strong; valuation is unavailable.",
        investment_thesis="Hold on the operating evidence.",
    )
    structured = MagicMock()
    structured.invoke.side_effect = [dirty, clean]
    llm = MagicMock()
    llm.with_structured_output.return_value = structured

    result = create_portfolio_manager(llm)({
        "company_of_interest": "TEST",
        "trade_date": "2026-01-01",
        "market_report": "m", "sentiment_report": "", "news_report": "",
        "fundamentals_report": "f",
        "investment_debate_state": {"challenge_history": "", "history": ""},
        "investment_plan": "plan", "trader_investment_plan": "trade",
        "risk_debate_state": {
            "history": "", "aggressive_history": "", "conservative_history": "",
            "neutral_history": "", "count": 1, "latest_speaker": "Judge",
            "current_aggressive_response": "", "current_conservative_response": "",
            "current_neutral_response": "",
        },
        "portfolio_context": "", "decision_horizon": "3-6 months",
        "model_status": "VERIFIED",
        "eps_status": "SUPPRESSED",
        "research_fair_value_verified": False,
        "research_fair_value_low": 5000.0,
        "research_fair_value_high": 9000.0,
    })

    text = result["final_trade_decision"]
    assert "unverified share basis" in text
    assert result["final_rating"] == "Hold"
    assert result["controlling_fair_value_low"] is None


def test_the_pe_case_table_is_removed_when_the_per_share_basis_is_unverified():
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {}
    state = {
        "trade_date": "2026-11-03",
        "model_status": "VERIFIED",
        "research_fair_value_verified": False,
        "debate_cases": '[{"stance": "bearish", "revenue_pct": -1.0}]',
        "investment_debate_state": {"first_review": "review text"},
    }

    out = graph.prepare_final_state(state, "TEST")

    assert "per-share basis is unverified" in out["case_table"]
    assert "| Case |" not in out["case_table"]


def test_news_diagnostics_leave_the_main_report(tmp_path):
    state = {
        "trade_date": "2026-11-03",
        "news_report": (
            "## News\nNo usable company news.\n\n"
            "## Data diagnostics\n| retry | vendor | error |\n|---|---|---|\n| 3 | yahoo | timeout |\n"
        ),
    }
    path = write_report_tree(state, "TEST", tmp_path, html=False)

    main = path.read_text(encoding="utf-8")
    appendix = (tmp_path / "appendix.md").read_text(encoding="utf-8")

    assert "| retry | vendor | error |" not in main
    # The full news report is appendix-only in the concise publication; the main
    # report names the gap in its header instead.
    assert "No usable company news." not in main
    assert "No usable company news." in appendix
    assert "| retry | vendor | error |" in appendix
    assert "News Diagnostics" in appendix
