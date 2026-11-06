"""Evidence classes: a forecast may not be relabelled as company guidance."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradingagents.research import evidence
from tradingagents.research.schemas import Financials, ModelSpec

FIXTURE = Path(__file__).parent / "fixtures" / "bank" / "spec.json"


def _spec() -> ModelSpec:
    return ModelSpec.from_dict(json.loads(FIXTURE.read_text(encoding="utf-8")))


@pytest.mark.unit
def test_the_spec_sections_carry_their_source_classes():
    spec = _spec()
    spec.guidance = {"FY26": Financials(revenue=99.0)}
    spec.consensus = None
    rows = evidence.classify_inputs(spec)
    classes = {row.source_class for row in rows}
    assert evidence.COMPANY_ACTUAL in classes
    assert evidence.AGENT_FORECAST in classes
    assert evidence.COMPANY_GUIDANCE in classes
    guidance = next(row for row in rows if row.source_class == evidence.COMPANY_GUIDANCE)
    assert guidance.period == "FY26" and guidance.value == 99.0


@pytest.mark.unit
def test_the_visible_benchmark_statement_names_the_source():
    spec = _spec()
    spec.guidance = {"FY26": Financials(revenue=99.0)}
    assert "company-issued guidance" in evidence.benchmark_statement(spec)
    spec.guidance = {}
    assert "internally modelled forecast" in evidence.benchmark_statement(spec)
    assert "not company guidance" in evidence.benchmark_statement(spec)


@pytest.mark.unit
def test_a_forecast_called_company_guidance_is_a_finding():
    spec = _spec()
    spec.guidance = {}
    forecast = next(
        row.value for row in evidence.classify_inputs(spec)
        if row.source_class == evidence.AGENT_FORECAST and row.metric == "net_profit"
    )
    text = f"Net profit of {forecast:g} is company guidance for FY26."
    findings = evidence.claim_findings(text, spec)
    assert any("describes as company guidance" in message or "contains none" in message
               for message in findings)


@pytest.mark.unit
def test_quoting_actual_company_guidance_is_not_a_finding():
    spec = _spec()
    spec.guidance = {"FY26": Financials(revenue=999.0)}
    text = "Company guidance is revenue 999.0 for FY26."
    assert evidence.claim_findings(text, spec) == []


@pytest.mark.unit
def test_an_internal_forecast_as_a_hurdle_is_a_finding():
    spec = _spec()
    spec.guidance = {}
    forecast = next(
        row.value for row in evidence.classify_inputs(spec)
        if row.source_class == evidence.AGENT_FORECAST and row.metric == "net_profit"
    )
    text = f"A beat versus company guidance of {forecast:g} is needed."
    findings = evidence.claim_findings(text, spec)
    assert any("hurdle" in message for message in findings)


@pytest.mark.unit
def test_a_forecast_number_outside_the_guidance_clause_is_not_a_mislabel():
    spec = _spec()
    spec.guidance = {"FY26": Financials(revenue=1375.0)}
    spec.forecast["FY26"].revenue = 1560.0
    text = (
        "**First**, the stated SoC levels (~1,560 vs ~940) do not match company segment "
        "data: company guidance is SoC testers of ¥1,375.0bn for FY3/27E against "
        "¥767.4bn in FY3/26 [doc p.15]."
    )
    assert evidence.claim_findings(text, spec) == []


@pytest.mark.unit
def test_an_explicit_not_management_guidance_disclosure_is_not_a_finding():
    spec = _spec()
    text = (
        "The model (agent forecasts, not management guidance) has FY3/27 revenue of "
        "1,900.0, operating profit of 994.7 and diluted EPS of 1,043.0."
    )
    spec.forecast["FY26"].revenue = 1900.0
    assert evidence.claim_findings(text, spec) == []


@pytest.mark.unit
def test_a_finding_quotes_the_sentence_so_it_can_be_located():
    spec = _spec()
    spec.guidance = {}
    text = "Net profit of 30 is company guidance for FY26."
    findings = evidence.claim_findings(text, spec)
    assert findings and "Net profit of 30" in findings[0]


@pytest.mark.unit
def test_a_forecast_called_a_company_commitment_is_a_finding():
    spec = _spec()
    spec.guidance = {}
    forecast = next(
        row.value for row in evidence.classify_inputs(spec)
        if row.source_class == evidence.AGENT_FORECAST and row.metric == "net_profit"
    )
    text = f"Management's net profit commitment of {forecast:g} is the hurdle."
    findings = evidence.claim_findings(text, spec)
    assert any("company guidance" in message or "commitment" in message for message in findings)


@pytest.mark.unit
def test_not_a_commitment_is_not_a_finding():
    spec = _spec()
    spec.guidance = {}
    text = "The forecast is not a company commitment; it is our own model."
    assert evidence.claim_findings(text, spec) == []


@pytest.mark.unit
def test_a_discretionary_forecast_cannot_be_called_mechanical():
    spec = _spec()
    forecast = next(
        row.value for row in evidence.classify_inputs(spec)
        if row.source_class == evidence.AGENT_FORECAST and row.metric == "net_profit"
    )
    text = f"Net profit of {forecast:g} is mechanically derived from capacity."
    findings = evidence.claim_findings(text, spec)
    assert any("mechanically derived" in message for message in findings)
