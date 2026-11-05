"""EV input readiness: extraction failure, availability, or reconciliation."""

from __future__ import annotations

import dataclasses

from tradingagents.research import input_probe
from tradingagents.research.schemas import NetDebt

from .test_capital_and_ev import spec


def _debt(**overrides) -> NetDebt:
    base = NetDebt(
        gross_debt=250.0,
        cash_and_equivalents=50.0,
        lease_treatment="excluded",
        minority_treatment="excluded",
        as_of="2026-09-30",
        scope="consolidated",
        source_unit="JPY_bn",
        source_refs=["doc-bs p.4"],
        component_sources={"gross_debt": "doc-bs p.4", "cash_and_equivalents": "doc-bs p.4"},
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def _model(**overrides):
    model = spec()
    model.unit = "JPY_bn"
    model.net_debt = _debt()
    for key, value in overrides.items():
        setattr(model, key, value)
    return model


def _probe(cash=False, debt=False, leases=False, depreciation=False):
    def hit(name):
        return [input_probe.Hit("doc-bs", f"{name} 1,234")]

    return input_probe.Probe(
        documents=3,
        cash=hit("Cash and deposits") if cash else [],
        debt=hit("Interest-bearing debt") if debt else [],
        leases=hit("Lease liabilities") if leases else [],
        depreciation=hit("Depreciation") if depreciation else [],
    )


def _diagnose(model, probe=None, trade_date="2026-11-04"):
    return input_probe.diagnose(
        model,
        valuation_period="FY26",
        trade_date=trade_date,
        probe=probe,
    )


def test_fully_reconciled_inputs_verify():
    result = _diagnose(_model())

    assert result["status"] == "EXTRACTED"
    assert result["missing"] == []


def test_a_numbered_corpus_line_the_builder_missed_is_an_extraction_failure():
    model = _model(net_debt=NetDebt(lease_treatment="excluded",
                                    minority_treatment="excluded"))
    result = _diagnose(model, _probe(cash=True, debt=True))

    assert result["inputs"]["cash"]["status"] == input_probe.NOT_EXTRACTED
    assert result["inputs"]["cash"]["status"] == "PRESENT_NOT_EXTRACTED_BY_BUILDER"
    assert result["inputs"]["gross_debt"]["status"] == input_probe.NOT_EXTRACTED
    assert "corpus extraction failure" in input_probe.render(result)


def test_no_numbered_line_is_data_unavailability():
    model = _model(net_debt=None)
    result = _diagnose(model, _probe())

    assert result["inputs"]["cash"]["status"] == input_probe.ABSENT
    assert "data unavailable" in input_probe.render(result)


def test_an_unstated_unit_is_model_insufficiency():
    result = _diagnose(_model(net_debt=_debt(source_unit="")))

    assert result["inputs"]["cash"]["status"] == input_probe.NOT_RECONCILED
    assert "source unit is not stated" in input_probe.render(result)


def test_a_balance_sheet_after_the_trade_date_is_model_insufficiency():
    result = _diagnose(_model(net_debt=_debt(as_of="2027-01-31")))

    assert result["inputs"]["cash"]["status"] == input_probe.NOT_RECONCILED
    assert "after the trade date" in input_probe.render(result)


def test_parent_company_figures_are_model_insufficiency():
    result = _diagnose(_model(net_debt=_debt(scope="parent")))

    assert result["inputs"]["cash"]["status"] == input_probe.NOT_RECONCILED
    assert "parent-company" in input_probe.render(result)


def test_a_stated_net_that_disagrees_with_its_components_is_model_insufficiency():
    result = _diagnose(_model(net_debt=_debt(reported_net_debt=999.0)))

    assert result["inputs"]["cash"]["status"] == input_probe.NOT_RECONCILED
    assert "does not reconcile" in input_probe.render(result)


def test_lease_lines_without_a_treatment_are_model_insufficiency():
    result = _diagnose(_model(net_debt=_debt(lease_treatment="")), _probe(leases=True))

    assert result["inputs"]["cash"]["status"] == input_probe.NOT_RECONCILED
    assert "lease" in input_probe.render(result)


def test_missing_depreciation_is_named_as_the_missing_dependency():
    model = _model()
    model.cashflow = {}
    result = _diagnose(model)

    assert result["inputs"]["depreciation"]["status"] == input_probe.ABSENT
    assert "depreciation" in result["missing"]
    assert result["inputs"]["cash"]["status"] == "EXTRACTED"


def test_a_narrative_without_a_number_is_not_a_disclosure():
    found = input_probe.probe_lines(
        "doc-bs", "The company has no interest-bearing debt and ample cash."
    )

    assert found["debt"] == []
    assert found["cash"] == []


def test_a_numbered_line_is_recorded_with_its_evidence():
    found = input_probe.probe_lines(
        "doc-bs",
        "Cash and deposits 420,500\nInterest-bearing debt 162,100",
    )

    assert found["cash"] and "420,500" in found["cash"][0].line
    assert found["debt"] and "162,100" in found["debt"][0].line


def test_dataclass_replace_keeps_the_components():
    model = dataclasses.replace(_model(), unit="JPY_m")

    assert model.net_debt.components_verified()
    assert model.unit == "JPY_m"
