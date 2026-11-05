"""Phase 0/1: dependency statuses, the non-valuation audit, advisory Trader."""

from __future__ import annotations

from unittest.mock import MagicMock

from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager
from tradingagents.agents.rating import RATING_REVIEW
from tradingagents.agents.schemas import (
    HolderAction,
    NewInvestorAction,
    PortfolioDecision,
    PortfolioRating,
)
from tradingagents.agents.trader.trader import create_trader
from tradingagents.research import dependencies
from tradingagents.research.decision_audit import assess as audit_action

DECLARED = {
    "market_cap_basis": "period_end_net",
    "forecast_eps_basis": "diluted_average",
    "dilution_sensitivity_basis": "full_conversion",
}


def _statuses(**overrides):
    kwargs = {
        "model_status": "VERIFIED",
        "basis": DECLARED,
        "price": 100.0,
        "has_depreciation": True,
        "has_net_debt": True,
        "pe_multiple": 20.0,
        "ev_ebitda_multiple": 12.0,
        "challenge_fields": [],
    }
    kwargs.update(overrides)
    return dependencies.assess_outputs(**kwargs)


# --- dependency propagation ---------------------------------------------------


def test_all_inputs_present_verifies_both_valuation_methods():
    statuses = _statuses()

    assert statuses["pe_valuation"] == "VERIFIED"
    assert statuses["ev_ebitda"] == "VERIFIED"


def test_a_missing_net_debt_degrades_only_ev_ebitda():
    statuses = _statuses(has_net_debt=False)

    assert statuses["pe_valuation"] == "VERIFIED"
    assert statuses["ev_ebitda"] == "UNAVAILABLE"


def test_an_unverified_market_cap_basis_degrades_only_the_per_share_ev_target():
    statuses = _statuses(
        basis={**DECLARED, "market_cap_basis": "full_share (fallback — not verified)"}
    )

    assert statuses["pe_valuation"] == "VERIFIED"
    assert statuses["ev_ebitda"] == "VERIFIED"          # enterprise value stands
    assert statuses["ev_per_share"] == "SUPPRESSED"    # the per-share target does not


def test_an_unverified_equity_value_basis_suppresses_only_the_per_share_ev_target():
    statuses = _statuses(
        basis={**DECLARED, "equity_value_share_basis": "full_share (fallback — not verified)"}
    )

    assert statuses["ev_ebitda"] == "VERIFIED"
    assert statuses["ev_per_share"] == "SUPPRESSED"


def test_an_invalid_eps_basis_degrades_pe_but_not_ev_ebitda():
    statuses = _statuses(
        basis={**DECLARED, "forecast_eps_basis": "full_share (fallback — not verified)"}
    )

    assert statuses["forecast_eps"] == "SUPPRESSED"
    assert statuses["pe_valuation"] == "SUPPRESSED"
    assert statuses["ev_ebitda"] == "VERIFIED"


def test_a_per_share_challenge_distinguishes_methods():
    statuses = _statuses(challenge_fields=["FY3/28 EPS"])

    assert statuses["pe_valuation"] == "SUPPRESSED"
    assert statuses["ev_ebitda"] == "VERIFIED"
    assert statuses["ev_per_share"] == "VERIFIED"


# --- non-valuation decision audit --------------------------------------------


def _evidence(category, driver, refs=("doc-a",), disclosure=None, **overrides):
    item = {
        "category": category,
        "underlying_driver_id": driver,
        "primary_source_refs": list(refs),
        "disclosure_ref": disclosure or (refs[0] if refs else ""),
        "verified": True,
        "supports_action": True,
    }
    item.update(overrides)
    return item


def _audit(evidence, **overrides):
    data = {
        "decision_basis": "fundamental",
        "horizon": "3-6 months",
        "evidence": evidence,
        "confirmation_conditions": ["reported evidence confirms"],
        "invalidation_conditions": ["reported evidence reverses"],
        "uses_suppressed_figures": False,
    }
    data.update(overrides)
    return data


def test_three_independent_clusters_with_a_risk_leg_pass():
    result = audit_action(
        _audit([
            _evidence("operating", "margin-miss", ("doc-a",)),
            _evidence("cash_flow", "fcf-negative", ("doc-b",)),
            _evidence("balance_sheet", "covenant", ("doc-c",)),
        ]),
        action="SELL",
        valuation_verified=False,
    )

    assert result["validation_passed"] is True
    assert result["independent_category_count"] == 3
    assert result["independent_primary_source_count"] == 3


def test_categories_that_restate_one_fact_are_one_cluster():
    result = audit_action(
        _audit([
            _evidence("operating", "q2-miss", ("doc-a",)),
            _evidence("earnings_revision", "q2-miss", ("doc-a",)),
            _evidence("consensus_gap", "q2-miss", ("doc-a",)),
        ]),
        action="SELL",
        valuation_verified=False,
    )

    assert result["validation_passed"] is False
    assert result["independent_category_count"] == 1
    assert result["normalized_action"] == "HOLD"


def test_a_second_cluster_with_a_risk_leg_normalizes_to_reduce():
    result = audit_action(
        _audit([
            _evidence("operating", "q2-miss", ("doc-a",)),
            _evidence("cash_flow", "fcf-negative", ("doc-b",)),
        ]),
        action="SELL",
        valuation_verified=False,
    )

    assert result["validation_passed"] is False
    assert result["normalized_action"] == "REDUCE"


def test_primary_sources_must_span_two_clusters():
    result = audit_action(
        _audit([
            _evidence("operating", "margin", ("doc-a",)),
            _evidence("cash_flow", "fcf", ()),        # no primary disclosure
            _evidence("balance_sheet", "covenant", ()),
        ]),
        action="SELL",
        valuation_verified=False,
    )

    assert result["independent_category_count"] == 3
    assert result["independent_primary_source_count"] == 1
    assert result["validation_passed"] is False
    assert any("two independent primary" in reason for reason in result["failure_reasons"])


def test_technical_only_cannot_publish_a_fundamental_sell():
    result = audit_action(
        _audit(
            [_evidence("technical", "breakdown", ("doc-a",))],
            decision_basis="technical",
        ),
        action="SELL",
        valuation_verified=True,
    )

    assert result["validation_passed"] is False
    assert any("technical-only" in reason for reason in result["failure_reasons"])


def test_a_mixed_basis_must_name_its_controlling_category():
    result = audit_action(
        _audit(
            [
                _evidence("operating", "margin", ("doc-a",)),
                _evidence("technical", "breakdown", ("doc-b",)),
            ],
            decision_basis="mixed",
            controlling_category="",
        ),
        action="HOLD",
        valuation_verified=True,
    )

    assert result["validation_passed"] is False
    assert any("controlling category" in reason for reason in result["failure_reasons"])


def test_a_mixed_basis_without_verified_technicals_falls_back_to_fundamental():
    result = audit_action(
        _audit(
            [
                _evidence("operating", "margin", ("doc-a",)),
            ],
            decision_basis="mixed",
            controlling_category="operating",
        ),
        action="HOLD",
        valuation_verified=True,
    )

    assert result["validation_passed"] is True
    assert result["decision_basis"] == "fundamental"
    assert result["technical_overlay"] == "advisory"
    assert result["mixed_fallback"] is True


def test_the_fallback_cannot_launder_a_missing_fundamental_leg():
    result = audit_action(
        _audit(
            [_evidence("technical", "breakdown", ("doc-b",))],
            decision_basis="mixed",
            controlling_category="technical",
        ),
        action="HOLD",
        valuation_verified=True,
    )

    assert result["validation_passed"] is False
    assert result["decision_basis"] == "mixed"


def test_using_suppressed_figures_fails_the_audit():
    result = audit_action(
        _audit([
            _evidence("operating", "margin", ("doc-a",)),
            _evidence("cash_flow", "fcf", ("doc-b",)),
            _evidence("balance_sheet", "covenant", ("doc-c",)),
        ], uses_suppressed_figures=True),
        action="SELL",
        valuation_verified=False,
    )

    assert result["validation_passed"] is False


def test_severity_thresholds_follow_the_sector():
    # 12% operating decline: not severe for an industrial (20%), severe for a REIT (10%).
    data = _audit(
        [
            _evidence("operating", "op", ("doc-a",)),
            _evidence("catalyst", "cat", ("doc-b",)),
            _evidence("consensus_gap", "cons", ("doc-c",)),
        ],
        severe_operating_deterioration=True,
        observed_operating_decline_pct=12.0,
    )

    assert audit_action(
        data, action="SELL", valuation_verified=False
    )["validation_passed"] is False
    assert audit_action(
        data, action="SELL", valuation_verified=False, sector="reit"
    )["validation_passed"] is True


# --- PM enforcement -----------------------------------------------------------


def _pm_state(**overrides):
    state = {
        "company_of_interest": "TEST", "trade_date": "2026-01-01",
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
        "model_status": "VERIFIED", "research_fair_value_verified": True,
    }
    state.update(overrides)
    return state


def test_a_technical_decision_is_published_as_not_rated():
    decision = PortfolioDecision(
        rating=PortfolioRating.SELL,
        executive_summary="Breakdown below support; avoid new money.",
        investment_thesis="Price and volume only.",
        decision_basis="technical",
        decision_audit={
            "decision_basis": "technical",
            "horizon": "3-6 months",
            "evidence": [_evidence("technical", "breakdown", ("doc-a",))],
            "confirmation_conditions": ["close below support"],
            "invalidation_conditions": ["close back above"],
            "uses_suppressed_figures": False,
        },
    )
    structured = MagicMock()
    structured.invoke.return_value = decision
    llm = MagicMock()
    llm.with_structured_output.return_value = structured

    result = create_portfolio_manager(llm)(_pm_state())

    assert result["final_rating"] == RATING_REVIEW
    assert "**Rating**: Not Rated" in result["final_trade_decision"]
    assert "Fundamental rating**: Not Rated" in result["final_trade_decision"]


def test_an_unsupported_sell_is_normalized_to_hold():
    decision = PortfolioDecision(
        rating=PortfolioRating.SELL,
        executive_summary="Sell on the chart.",
        investment_thesis="Weak tape.",
        existing_holder_action=HolderAction.REDUCE,
        new_investor_action=NewInvestorAction.AVOID,
        decision_basis="fundamental",
        decision_audit={
            "decision_basis": "fundamental",
            "horizon": "3-6 months",
            "evidence": [_evidence("operating", "one-fact", ("doc-a",))],
            "confirmation_conditions": ["x"],
            "invalidation_conditions": ["y"],
            "uses_suppressed_figures": False,
        },
    )
    structured = MagicMock()
    structured.invoke.return_value = decision
    llm = MagicMock()
    llm.with_structured_output.return_value = structured

    result = create_portfolio_manager(llm)(_pm_state(research_fair_value_verified=False))

    assert result["final_rating"] == "Hold"
    assert result["decision_status"] == "PROVISIONAL"
    assert "Decision audit**: failed" in result["final_trade_decision"]


# --- advisory Trader ----------------------------------------------------------


def test_two_schema_misses_make_the_trader_advisory_and_scrub_levels():
    structured = MagicMock()
    structured.invoke.return_value = None
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    llm.invoke.return_value = MagicMock(
        content=(
            "**Action**: Buy\n**Entry Price**: 100\n**Stop Loss**: 90\n"
            "**Position Sizing**: 5% of the portfolio\n"
        )
    )

    result = create_trader(llm)({
        "company_of_interest": "TEST", "trade_date": "2026-01-01",
        "market_report": "", "investment_plan": "plan",
        "portfolio_context": "Portfolio: 100 units.", "research_model_source": "built",
        "research_package": "",
    })

    assert result["trader_status"] == "advisory"
    plan = result["trader_investment_plan"]
    assert "Entry Price**: not provided" in plan
    assert "5%" not in plan
    assert structured.invoke.call_count == 2
