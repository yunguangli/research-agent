"""Control-layer regressions across sectors: Advantest, Ibiden and a bank.

The control layer must detect a defective controlling input without assuming a
valuation method, a sector metric, segment guidance, consensus, technical levels
or convertible debt exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tradingagents.agents.managers.portfolio_manager import (
    _enforce_rejected_outputs,
    _technical_trigger_gate,
)
from tradingagents.agents.schemas import NewInvestorAction
from tradingagents.research import evidence, kernel, relevance, valuation, verification
from tradingagents.research.schemas import ModelSpec

FIXTURES = Path(__file__).parent / "fixtures"


def _spec(ticker: str) -> ModelSpec:
    path = FIXTURES / ticker / "spec.json"
    return ModelSpec.from_dict(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.unit
@pytest.mark.parametrize("ticker", ["6857.T", "4062.T", "bank"])
def test_cross_sector_specs_load_and_classify(ticker):
    spec = _spec(ticker)
    rows = evidence.classify_inputs(spec)
    assert rows
    assert {row.source_class for row in rows} <= set(evidence.SOURCE_CLASSES)
    assert evidence.benchmark_statement(spec)


@pytest.mark.unit
@pytest.mark.parametrize("ticker", ["6857.T", "4062.T"])
def test_valid_models_still_tie_and_value(ticker):
    model = kernel.run(_spec(ticker), strict=True)
    value = valuation.value(model)
    assert value.fair_value_mid > 0
    assert value.fair_value_low <= value.fair_value_mid <= value.fair_value_high


@pytest.mark.unit
def test_the_advantest_false_positives_stay_clean():
    spec = _spec("6857.T")
    sentences = [
        "**First**, the stated SoC levels (~1,560 vs ~940) do not match company segment "
        "data: company guidance is SoC testers of \u00a51,375.0bn for FY3/27E against "
        "\u00a5767.4bn in FY3/26.",
        "**SoC tester revenue:** \u00a5612.9bn in H1 implies \u00a5762.1bn in H2 to reach "
        "guidance of \u00a51,375.0bn.",
        "The model (agent forecasts, not management guidance) has FY3/27 revenue of "
        "1,900.0 and operating profit of 994.7.",
    ]
    for sentence in sentences:
        assert evidence.claim_findings(sentence, spec) == []


@pytest.mark.unit
def test_the_quarterly_allocation_is_named_as_internally_modelled():
    assert "Q3/Q4 allocation is internally modelled" in evidence.benchmark_statement(
        _spec("6857.T")
    )


@pytest.mark.unit
def test_rejected_scenarios_are_removed_from_verified_text():
    decision = SimpleNamespace(
        rejected_scenarios=["Bear scenario 18869 (-36%): implies 14.4x on the base EPS"],
        executive_summary="Base offers +6.6%; the bear endpoint is -36%.",
        investment_thesis="The bear case prints 18,869.",
        upgrade_evidence=["close above 33,900", "bear at 18,869"],
        downgrade_evidence=["band floor"],
        thesis_invalidation=["bear 18,869"],
        fair_value_low=23576.0,
        fair_value_base=31435.0,
        fair_value_high=39293.0,
    )
    package = (
        "Valuation: price 29,485.\n"
        "Scenarios: Bear 18,869 (-36%); Base 31,435 (+7%); Bull 44,929 (+52%)\n"
    )
    state = {
        "research_package": package,
        "research_package_artifact": package,
        "research_price": 29485.0,
    }

    note, filtered, _artifact = _enforce_rejected_outputs(decision, state)

    assert "Bear 18,869" not in filtered and "Base 31,435" in filtered
    assert "-36%" not in decision.executive_summary
    assert "Verified band 23,576–39,293" in decision.executive_summary
    assert "18,869" not in decision.investment_thesis
    assert decision.upgrade_evidence[0] == "close above 33,900"
    assert any("Verified band high 39,293" in item for item in decision.upgrade_evidence)
    assert any("Verified band floor 23,576" in item for item in decision.downgrade_evidence)
    assert decision.thesis_invalidation == []
    assert "Verified band: 23,576–39,293" in note


@pytest.mark.unit
def test_a_trigger_above_fair_value_does_not_start_new_capital():
    decision = SimpleNamespace(
        entry_condition="Buy on a close above 33,900.",
        fair_value_base=31435.0,
        new_investor_action=NewInvestorAction.INITIATE,
    )
    _technical_trigger_gate(decision)
    assert "does not authorise new capital" in decision.entry_condition
    assert decision.new_investor_action == NewInvestorAction.WAIT


@pytest.mark.unit
def test_an_internal_forecast_labelled_as_guidance_is_relabelled():
    spec = _spec("6857.T")
    text = "Company guidance is 758.6 for FY3/27."
    rewritten, findings = evidence.enforce_forecast_labels(text, spec)
    assert findings
    assert "company guidance" not in rewritten
    assert "internal forecast" in rewritten


@pytest.mark.unit
def test_a_defective_controlling_input_is_detected():
    unpropagated = verification.model_check(
        model_status="VERIFIED",
        corrections=[{
            "challenge_type": "share_count", "status": "ACCEPTED", "propagated": False,
            "affected_fields": ["valuation.shares_m"],
        }],
    )
    assert unpropagated.outcome == verification.FAIL

    blocking = verification.decision_check(
        action="Buy",
        audit={"validation_passed": True},
        unresolved=[{"challenge_type": "share_count", "severity": "blocking"}],
    )
    assert blocking.outcome == verification.FAIL


@pytest.mark.unit
def test_a_rejected_correction_does_not_qualify_the_decision():
    model = verification.model_check(
        model_status="VERIFIED",
        corrections=[{"challenge_type": "revenue", "status": "REJECTED", "propagated": False}],
    )
    decision = verification.decision_check(
        action="Hold",
        audit={"validation_passed": True},
        corrections=[{"challenge_type": "revenue", "status": "REJECTED", "propagated": False}],
    )
    assert model.outcome == verification.PASS
    assert decision.outcome == verification.PASS


@pytest.mark.unit
def test_relevance_is_relative_to_the_decision_not_a_fixed_percentage():
    decisive = relevance.classify(
        action="Buy", base_fair=100.0, shocked_fair=98.0, price=99.0, band_low=80.0,
        band_high=120.0,
    )
    secondary = relevance.classify(
        action="Buy", base_fair=100.0, shocked_fair=99.4, price=99.0, band_low=80.0,
        band_high=120.0,
    )
    minor = relevance.classify(
        action="Buy", base_fair=100.0, shocked_fair=99.9, price=99.0, band_low=80.0,
        band_high=120.0,
    )
    assert (decisive, secondary, minor) == (
        relevance.DECISIVE, relevance.MATERIAL_SECONDARY, relevance.MINOR
    )


@pytest.mark.unit
def test_checks_are_not_applicable_when_the_method_or_decision_does_not_exist():
    # The bank fixture has no segment guidance, no consensus and no technical levels.
    result = verification.assess(
        source={"model_status": "NOT_APPLICABLE"},
        model={"model_status": "NOT_APPLICABLE"},
        decision={"action": ""},
    )
    assert result["overall"] == verification.NOT_APPLICABLE
