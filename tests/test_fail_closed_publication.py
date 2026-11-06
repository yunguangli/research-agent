"""A failed model must not leak a prior run's VERIFIED label or figures."""

from __future__ import annotations

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.reporting import _header, write_report_tree
from tradingagents.research import decision_checks, facts
from tradingagents.research.report import load_spec


def _spec():
    return load_spec("tests/fixtures/6857.T/spec.json")


def test_source_facts_survive_while_the_model_does_not():
    block = facts.render_source_facts(_spec())

    assert "SOURCE_FACTS" in block
    assert "FY3/27 company guidance" in block
    assert "valuation" not in block.lower()


def test_a_failed_gate_marks_every_dependent_output_not_tested():
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {}

    update = graph._repair_failed("provider rejected the structured review", "extraction_failed")

    for key in (
        "operating_model_status",
        "eps_model_status",
        "valuation_status",
        "calculation_status",
        "sensitivity_model_status",
        "segment_reconciliation_status",
    ):
        assert update[key] == "NOT_TESTED", key
    assert update["model_status"] == "UNVERIFIED"
    assert update["fundamental_packet"] == ""
    assert update["valuation_packet"] == ""
    assert update["valuation_method_statuses"] == "{}"


def test_the_safe_fallback_passes_safety_and_degrades_process():
    state = {
        "model_status": "UNVERIFIED",
        "research_model_source": "unavailable",
        "final_rating": "Not Rated",
        "decision_status": "NOT_RATED",
        "publication_status": "OK",
        "run_purpose": "production",
    }

    checks = decision_checks.assess(state)

    assert checks["decision_safety_check"] == "PASS"
    assert checks["decision_process_check"] == "DEGRADED"
    assert checks["production_decision_allowed"] is False


def test_a_rating_on_an_unverified_model_fails_safety():
    checks = decision_checks.assess({
        "model_status": "UNVERIFIED",
        "final_rating": "Hold",
        "controlling_fair_value_low": 10.0,
    })

    assert checks["decision_safety_check"] == "FAIL"
    assert checks["production_decision_allowed"] is False


def test_prepare_final_state_scrubs_the_rating_and_prints_the_three_checks():
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {}
    state = {
        "trade_date": "2026-11-06",
        "model_status": "UNVERIFIED",
        "research_model_source": "unavailable",
        "final_trade_decision": "**Rating**: Hold\n\n**Executive Summary**: stale.",
        "final_rating": "Hold",
        "controlling_fair_value_low": 10.0,
        "controlling_fair_value_high": 20.0,
        "operating_model_status": "NOT_TESTED",
        "fundamentals_report": "Reported revenue 1,128.6. The model says [[178.0]].",
        "research_package": "Valuation: price 20; fair value 10-12.",
        "source_facts": "SOURCE_FACTS (company-reported)\n- FY actual revenue 1,128.6",
    }

    out = graph.prepare_final_state(state, "TEST")
    header = _header("TEST", out, None)

    assert out["final_rating"] == "Not Rated"
    assert out["controlling_fair_value_low"] is None
    assert out["decision_safety_check"] == "PASS"
    assert out["decision_process_check"] == "DEGRADED"
    assert out["production_decision_allowed"] is False
    assert "DECISION_SAFETY_CHECK: PASS" in header
    assert "DECISION_PROCESS_CHECK: DEGRADED" in header
    assert "PRODUCTION_DECISION_ALLOWED: FALSE" in header


def test_the_main_report_withholds_model_figures_but_the_appendix_keeps_them(tmp_path):
    state = {
        "trade_date": "2026-11-06",
        "model_status": "UNVERIFIED",
        "research_model_source": "unavailable",
        "final_trade_decision": "**Rating**: Not Rated\n\n**Executive Summary**: model unavailable.",
        "final_rating": "Not Rated",
        "decision_status": "NOT_RATED",
        "operating_model_status": "NOT_TESTED",
        "fundamentals_report": "Reported revenue 1,128.6. The model says [[178.0]].",
        "research_package": "Valuation: price 20; fair value 10-12.",
        "source_facts": "SOURCE_FACTS (company-reported)\n- FY actual revenue 1,128.6",
        "structured_fallbacks": {"Model Repair Gate": "Error code: 400"},
        "investment_debate_state": {"first_review_raw": "RAW FIRST ATTEMPT"},
    }
    path = write_report_tree(state, "TEST", tmp_path, html=False)
    main = path.read_text(encoding="utf-8")
    appendix = (tmp_path / "appendix.md").read_text(encoding="utf-8")

    assert "178.0" not in main
    assert "Valuation: price 20" not in main
    assert "Fundamentals Analyst report is withheld" in main
    assert "SOURCE_FACTS" in main
    assert "178.0" in appendix
    assert "Error code: 400" in appendix
    assert "RAW FIRST ATTEMPT" in appendix
