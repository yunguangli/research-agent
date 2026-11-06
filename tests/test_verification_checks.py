"""The three verification checks are independent and each has its own inputs."""

from __future__ import annotations

import json

import pytest

from tradingagents.agents.context import corrections_block
from tradingagents.research import relevance, verification


@pytest.mark.unit
def test_source_check_fails_on_an_unresolved_blocking_defect():
    result = verification.source_check(
        model_status="VERIFIED",
        unresolved=[{"challenge_type": "share_count", "severity": "blocking"}],
    )
    assert result.outcome == verification.FAIL
    assert "blocking source defect" in result.reasons[0]


@pytest.mark.unit
def test_source_check_qualifies_a_resolved_conflict_and_passes_clean():
    qualified = verification.source_check(
        model_status="VERIFIED", conflicts=["EPS: guidance 1.0 vs model 0.9"]
    )
    assert qualified.outcome == verification.QUALIFIED
    assert verification.source_check(model_status="VERIFIED").outcome == verification.PASS


@pytest.mark.unit
def test_source_check_does_not_read_the_decision_audit():
    """A failed decision audit cannot make SOURCE_CHECK fail: it has no such input."""
    result = verification.source_check(model_status="VERIFIED")
    assert result.outcome == verification.PASS


@pytest.mark.unit
def test_model_check_fails_when_an_accepted_correction_did_not_propagate():
    result = verification.model_check(
        model_status="VERIFIED",
        corrections=[{"challenge_type": "share_count", "status": "ACCEPTED", "propagated": False}],
    )
    assert result.outcome == verification.FAIL
    assert "did not reach the model" in result.reasons[0]


@pytest.mark.unit
def test_model_check_qualifies_suppressed_outputs_and_passes_clean():
    qualified = verification.model_check(
        model_status="VERIFIED", output_statuses={"eps_model": "SUPPRESSED"}
    )
    assert qualified.outcome == verification.QUALIFIED
    assert verification.model_check(model_status="VERIFIED").outcome == verification.PASS


@pytest.mark.unit
def test_model_check_fails_when_the_decision_used_another_model_version():
    result = verification.model_check(
        model_status="VERIFIED",
        decision_model_version="v1",
        active_model_version="v2",
    )
    assert result.outcome == verification.FAIL
    assert "against v1" in result.reasons[0]


@pytest.mark.unit
def test_decision_check_fails_when_the_audit_failed():
    result = verification.decision_check(
        action="Buy",
        audit={"validation_passed": False, "failure_reasons": ["no horizon"]},
    )
    assert result.outcome == verification.FAIL


@pytest.mark.unit
def test_a_shock_that_crosses_the_price_is_decisive():
    result = verification.decision_check(
        action="Buy",
        fair_value_mid=100.0,
        fair_value_low=80.0,
        price=99.0,
        sensitivities=[{"sensitivity_id": "eps_5", "fair_value_impact": -8.0}],
    )
    assert result.outcome == verification.FAIL
    assert "changes the BUY decision" in result.reasons[-1]


@pytest.mark.unit
def test_a_shock_that_consumes_the_margin_of_safety_is_secondary():
    result = verification.decision_check(
        action="Buy",
        fair_value_mid=100.0,
        fair_value_low=95.0,
        price=99.0,
        sensitivities=[{"sensitivity_id": "eps_1", "fair_value_impact": -0.8}],
    )
    assert result.outcome == verification.QUALIFIED
    assert "material secondary" in result.reasons[-1]
    assert result.details["relevance_counts"][relevance.MATERIAL_SECONDARY] == 1


@pytest.mark.unit
def test_a_shock_that_cannot_change_the_decision_is_minor():
    result = verification.decision_check(
        action="Buy",
        fair_value_mid=100.0,
        fair_value_low=99.5,
        price=99.0,
        sensitivities=[{"sensitivity_id": "eps_0", "fair_value_impact": -0.1}],
    )
    assert result.outcome == verification.PASS
    assert result.details["relevance_counts"][relevance.MINOR] == 1


@pytest.mark.unit
def test_decision_check_tests_unresolved_defects_not_only_sensitivities():
    result = verification.decision_check(
        action="Hold",
        fair_value_mid=100.0,
        price=100.0,
        unresolved=[{"challenge_type": "tax_rate", "severity": "material"}],
    )
    assert result.outcome == verification.QUALIFIED
    assert "unresolved defect" in result.reasons[0]


@pytest.mark.unit
def test_decision_check_tests_accepted_corrections():
    result = verification.decision_check(
        action="Buy",
        fair_value_mid=100.0,
        price=80.0,
        corrections=[{
            "challenge_type": "share_count", "status": "ACCEPTED", "propagated": True,
            "affected_fields": ["valuation.shares_m"],
            "downstream_outputs": ["forecast_eps", "pe_valuation"],
        }],
    )
    assert result.outcome == verification.QUALIFIED
    assert "depends on an accepted correction" in result.reasons[0]


@pytest.mark.unit
def test_assess_is_the_worst_of_three_independent_checks():
    result = verification.assess(
        source={"model_status": "VERIFIED"},
        model={"model_status": "VERIFIED", "output_statuses": {"eps_model": "SUPPRESSED"}},
        decision={"action": "Hold", "audit": {"validation_passed": False}},
    )
    outcomes = {check["name"]: check["outcome"] for check in result["checks"]}
    assert outcomes == {
        "SOURCE_CHECK": verification.PASS,
        "MODEL_CHECK": verification.QUALIFIED,
        "DECISION_CHECK": verification.FAIL,
    }
    assert result["overall"] == verification.FAIL


@pytest.mark.unit
def test_a_correction_accepted_before_the_decision_reaches_the_prompt():
    state = {
        "model_corrections": json.dumps([
            {
                "challenge_type": "share_count",
                "status": "ACCEPTED",
                "corrected_value": {"valuation.shares_m": 566.3},
                "downstream_outputs": ["forecast_eps", "pe_valuation"],
                "evidence_refs": ["doc:page 12"],
            },
            {"challenge_type": "tax", "status": "PROPOSED"},
        ])
    }
    block = corrections_block(state)
    assert "ACCEPTED MODEL CORRECTIONS" in block
    assert "share_count" in block and "566.3" in block
    assert "pe_valuation" in block
    assert "tax" not in block


@pytest.mark.unit
def test_the_correction_ledger_records_status_and_downstream_outputs():
    from types import SimpleNamespace

    from tradingagents.graph.trading_graph import TradingAgentsGraph

    accepted = SimpleNamespace(
        challenge_type="share_count", severity="material", proposed_values={"valuation.shares_m": 566.3},
        affected_fields=["valuation.shares_m"], blocked_outputs=["forecast_eps", "pe_valuation"],
        evidence_refs=["doc:page 12"], stated_model_value=566.3, requires_rebuild=False,
    )
    proposed = SimpleNamespace(
        challenge_type="tax_rate", severity="material", proposed_values={},
        affected_fields=["tax_rate"], blocked_outputs=[], evidence_refs=["doc:page 20"],
        stated_model_value=None, requires_rebuild=False,
    )
    structural = SimpleNamespace(
        challenge_type="segment_scope", severity="material", proposed_values={},
        affected_fields=["segments"], blocked_outputs=[], evidence_refs=[],
        stated_model_value=None, requires_rebuild=True,
    )
    rejected = SimpleNamespace(
        challenge_type="revenue", severity="material",
        proposed_values={"actuals.FY25.revenue": 999.0},
        affected_fields=["actuals.FY25.revenue"], blocked_outputs=[], evidence_refs=[],
        stated_model_value=1.0, requires_rebuild=False,
    )
    records = TradingAgentsGraph._correction_records(
        object(), None, [accepted, proposed, structural, rejected], [accepted],
        [proposed, rejected], [structural],
    )
    by_type = {record["challenge_type"]: record for record in records}
    assert by_type["share_count"]["status"] == "ACCEPTED"
    assert by_type["share_count"]["propagated"] is True
    assert by_type["share_count"]["accepted_by"] == "model_controller"
    assert by_type["share_count"]["downstream_outputs"] == ["forecast_eps", "pe_valuation"]
    assert by_type["share_count"]["original_value"] == {"valuation.shares_m": None}
    assert by_type["tax_rate"]["status"] == "UNRESOLVED"
    assert by_type["segment_scope"]["status"] == "UNRESOLVED"
    assert by_type["revenue"]["status"] == "REJECTED"
    assert by_type["revenue"]["propagated"] is False


@pytest.mark.unit
def test_a_run_with_no_model_or_decision_is_not_applicable():
    result = verification.assess(
        source={"model_status": "NOT_APPLICABLE"},
        model={"model_status": "NOT_APPLICABLE"},
        decision={"action": ""},
    )
    assert {check["outcome"] for check in result["checks"]} == {
        verification.NOT_APPLICABLE
    }
    assert result["overall"] == verification.NOT_APPLICABLE


@pytest.mark.unit
def test_a_non_applicable_check_does_not_fail_a_passing_run():
    result = verification.assess(
        source={"model_status": "NOT_APPLICABLE"},
        model={"model_status": "VERIFIED"},
        decision={"action": "Hold", "price": 100.0},
    )
    assert result["overall"] == verification.PASS
