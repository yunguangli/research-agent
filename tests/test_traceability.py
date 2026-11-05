"""Tests for the traceability layer: sensitivities, PM range, reviewer ID."""

from __future__ import annotations

import pytest

from tradingagents.agents.managers.portfolio_manager import _range_error
from tradingagents.agents.researchers.adversarial_reviewer import (
    decisive_sensitivity,
)
from tradingagents.research import kernel, repair, sensitivity
from tradingagents.research.schemas import (
    Bridge,
    CashFlow,
    Financials,
    Line,
    ModelSpec,
    Period,
    Scenario,
    Segment,
    ShareBase,
    ValuationSpec,
)


def spec() -> ModelSpec:
    return ModelSpec(
        ticker="TEST",
        company="Test Corp",
        currency="USD",
        unit="USD_m",
        fiscal_year_end_month=12,
        periods=[
            Period("FY25", "2025-01-01", "2025-12-31", "actual"),
            Period("FY26", "2026-01-01", "2026-12-31", "forecast"),
            Period("FY27", "2027-01-01", "2027-12-31", "forecast"),
        ],
        actuals={"FY25": Financials(revenue=100.0, operating_profit=10.0, net_profit=6.0)},
        guidance={},
        forecast={"FY26": Financials(net_profit=8.0)},
        bridges=[
            Bridge("revenue", "FY25", "FY26", 100.0, [Line("growth", 10.0)], declared_total=110.0),
            Bridge("operating_profit", "FY25", "FY26", 10.0,
                   [Line("margin", 2.0, driver="operating leverage", alternative=4.0)],
                   declared_total=12.0),
            Bridge("net_profit", "FY25", "FY26", 6.0,
                   [Line("tax", 2.0, driver="tax rate", alternative=3.0)],
                   declared_total=8.0),
        ],
        segments={
            "FY26": [
                Segment("Core", 100.0, 11.0),
                Segment("New plant", 10.0, 1.0, commissioning="FY27"),
            ]
        },
        share_bases=[ShareBase("basic", 10.0)],
        scenarios=[
            Scenario("Base", "steady", eps=0.80, multiple=11.0, period="FY26"),
            Scenario("Bear", "weak", eps=0.65, multiple=10.0, period="FY26"),
        ],
        warnings=["FY26: the forecast matches guidance because the same drivers are used."],
        eps_basis="basic",
        valuation=ValuationSpec(
            price=20.0,
            fair_value_multiple_low=10.0,
            fair_value_multiple_high=12.0,
            fair_value_low=8.0,
            fair_value_high=9.6,
            fair_value_basis="basic",
            fair_value_period="FY26",
            market_cap_basis="basic",
            forecast_eps_basis="basic",
            dilution_sensitivity_basis="basic",
        ),
    )


# --- sensitivities ------------------------------------------------------------


def test_a_bridge_line_with_a_driver_is_shocked_and_ranked():
    items, gaps = sensitivity.rank(spec())

    drivers = {item.driver: item for item in items}
    assert drivers["tax rate"].eps_impact == pytest.approx(0.1)
    assert drivers["tax rate"].fair_value_impact is not None
    assert drivers["tax rate"].fair_value_impact > 0
    assert drivers["operating leverage"].operating_profit_impact == pytest.approx(2.0)
    assert any("growth" in gap for gap in gaps)          # no driver and no alternative -> research gap


def test_an_operating_shock_uses_the_models_tax_rate_to_reach_eps():
    model = spec()
    model.bridges = [bridge for bridge in model.bridges if bridge.metric != "net_profit"]
    model.effective_tax_rate = 0.3

    items, _ = sensitivity.rank(model)
    op = next(item for item in items if item.driver == "operating leverage")

    assert op.net_income_impact == pytest.approx(1.4)      # 2.0 x (1 - 0.3)
    assert op.eps_impact == pytest.approx(0.14)            # 1.4 / 10 shares
    assert "tax" in op.propagation_path
    assert op.fair_value_impact is not None and op.fair_value_impact > 0


def test_a_line_without_an_alternative_is_not_estimated():
    model = spec()
    model.bridges[1].lines[0].alternative = None

    items, gaps = sensitivity.rank(model)

    assert [item.driver for item in items] == ["tax rate"]
    assert any("margin" in gap for gap in gaps)


def test_the_sensitivity_table_names_the_shock():
    items, _ = sensitivity.rank(spec())

    table = sensitivity.render(items)

    assert "net_profit_fy26_tax" in table          # the stable bridge + field id
    assert "| tax_rate |" not in table             # the display slug is not the id
    assert sensitivity.alias_map(items)["tax_rate"] == "net_profit_fy26_tax"
    assert "operating leverage" in table


# --- commissioning ------------------------------------------------------------


def test_a_segment_booking_revenue_before_commissioning_is_flagged():
    result = kernel.run(spec(), strict=False)

    assert any("commissioned in FY27" in warning for warning in result.warnings)


def test_a_segment_override_changes_the_segment_and_revalidates():
    repaired, _result, changes = repair.repair_model(
        spec(),
        [repair.ModelChallenge(
            challenge_type="segment",
            severity="material",
            proposed_values={"segments.FY26.Core.revenue": 99.0,
                             "segments.FY26.New plant.revenue": 11.0},
        )],
    )

    assert repaired.segments["FY26"][1].revenue == 11.0
    assert any("segment" in change["field"].lower() for change in changes) or changes


# --- PM controlling range -----------------------------------------------------


class _Decision:
    def __init__(self, low, base, high, **kwargs):
        self.fair_value_low = low
        self.fair_value_base = base
        self.fair_value_high = high
        self.controlling_low_source_id = kwargs.get("low_id")
        self.controlling_base_source_id = kwargs.get("base_id")
        self.controlling_high_source_id = kwargs.get("high_id")
        self.rejected_scenarios = kwargs.get("rejected", [])
        self.valuation_override = kwargs.get("override")


def test_a_range_that_matches_the_band_is_traced_to_it():
    decision = _Decision(8.0, 8.8, 9.6)

    assert _range_error(decision, 8.0, 9.6, {"active_model_version": "v2"}) is None
    assert decision.controlling_low_source_id == "pe_model_band_v2"


def test_a_range_that_does_not_match_and_cites_nothing_is_rejected():
    decision = _Decision(1.0, 2.0, 3.0)

    assert "does not match" in (_range_error(decision, 8.0, 9.6) or "")


def test_a_rejected_scenario_cannot_supply_a_bound():
    decision = _Decision(
        8.0, 8.8, 9.6, low_id="Bear", base_id="pe_model_band_v1", high_id="pe_model_band_v1",
        rejected=["Bear"],
    )

    assert "rejected scenario" in (_range_error(decision, 8.0, 9.6) or "")


def test_an_override_must_reproduce_from_its_inputs():
    good = _Decision(
        7.0, 8.8, 9.6, low_id="override", base_id="pe_model_band_v1", high_id="pe_model_band_v1",
        override={"source_model_value": 8.0, "adjustment_amount": -1.0},
    )
    bad = _Decision(
        7.0, 8.8, 9.6, low_id="override", base_id="pe_model_band_v1", high_id="pe_model_band_v1",
        override={"source_model_value": 8.0, "adjustment_amount": -2.0},
    )

    assert _range_error(good, 8.0, 9.6) is None
    assert "does not reproduce" in (_range_error(bad, 8.0, 9.6) or "")


def test_a_generic_model_band_id_is_not_a_valid_source():
    decision = _Decision(
        8.0, 8.8, 9.6, low_id="model_band", base_id="model_band", high_id="model_band"
    )

    error = _range_error(decision, 8.0, 9.6, {"active_model_version": "v1"})

    assert "not a known valuation source" in (error or "")


def test_the_decision_status_is_derived_not_forced():
    from tradingagents.agents.managers.portfolio_manager import _decision_status

    def status(**overrides):
        state = {"run_purpose": "production"}
        state.update(overrides.pop("state", {}))
        return _decision_status(
            state,
            structured=overrides.pop("structured", True),
            valuation_verified=overrides.pop("valuation_verified", True),
            audit_passed=overrides.pop("audit_passed", True),
        )

    assert status() == "FINAL"
    assert status(state={"run_purpose": "comparison"}) == "PROVISIONAL"
    assert status(state={"run_purpose": "research"}) == "PROVISIONAL"
    assert status(audit_passed=False) == "PROVISIONAL"
    assert status(valuation_verified=False) == "PROVISIONAL"
    assert status(structured=False) == ""
    assert status(state={"eps_basis_quality": "conservative_full_conversion"}) == "PROVISIONAL"


def test_a_base_outside_the_range_is_rejected():
    decision = _Decision(8.0, 12.0, 9.6)

    assert "base" in (_range_error(decision, 8.0, 9.6) or "")


# --- reviewer sensitivity id --------------------------------------------------


def test_a_valid_sensitivity_id_is_read_from_the_review():
    review = "## Decisive Disagreement\nPricing.\n\nDecisive sensitivity ID: substrate_pricing"

    assert decisive_sensitivity(review, {"substrate_pricing"}) == "substrate_pricing"


def test_an_unknown_sensitivity_id_is_refused():
    review = "Decisive sensitivity ID: not_a_real_id"

    assert decisive_sensitivity(review, {"substrate_pricing"}) == ""


def test_not_established_is_not_an_id():
    review = "Decisive sensitivity ID: not established"

    assert decisive_sensitivity(review, {"substrate_pricing"}) == ""


# --- repair conclusions -------------------------------------------------------


def test_conclusion_pairs_are_parsed_from_the_addendum():
    text = (
        "**Model repair (v2)** — the following conclusions changed:\n"
        "- Superseded: The shares are moderately attractive at 34.8x.\n"
        "  Repaired: On the repaired basis they are approximately full.\n"
    )

    assert repair.conclusion_pairs(text) == [
        ("The shares are moderately attractive at 34.8x.",
         "On the repaired basis they are approximately full.")
    ]

def _three_year_spec() -> ModelSpec:
    return ModelSpec(
        ticker="TEST", company="Test", currency="USD", unit="USD_m",
        fiscal_year_end_month=12,
        periods=[
            Period("FY25", "2025-01-01", "2025-12-31", "actual"),
            Period("FY26", "2026-01-01", "2026-12-31", "forecast"),
            Period("FY27", "2027-01-01", "2027-12-31", "forecast"),
        ],
        actuals={"FY25": Financials(revenue=100.0, operating_profit=10.0, net_profit=8.0)},
        guidance={},
        forecast={
            "FY26": Financials(revenue=120.0, operating_profit=14.0, net_profit=10.0),
            "FY27": Financials(revenue=140.0, operating_profit=18.0, net_profit=13.0),
        },
        bridges=[
            Bridge("operating_profit", "FY25", "FY26", 10.0,
                   [Line("volume", 4.0, driver="volume", alternative=2.0)], declared_total=14.0),
            Bridge("operating_profit", "FY26", "FY27", 14.0,
                   [Line("volume", 4.0, driver="volume", alternative=3.0)], declared_total=18.0),
        ],
        cashflow={"FY27": CashFlow(
            net_profit=13.0, depreciation=3.0, working_capital_change=0.0, capex=2.0)},
        share_bases=[ShareBase("full_share", 10.0)],
        eps_basis="full_share",
        valuation=ValuationSpec(
            price=20.0, fair_value_multiple_low=10.0, fair_value_multiple_high=12.0,
            fair_value_basis="full_share", fair_value_period="FY27",
        ),
    )


def test_a_shock_propagates_into_later_periods():
    items, _gaps = sensitivity.rank(_three_year_spec(), top=5)

    shocked = [item for item in items if item.driver == "volume"]
    assert shocked
    assert any(item.operating_profit_impact not in (None, 0) for item in shocked)
    assert any(item.net_income_impact not in (None, 0) for item in shocked)
    assert any(item.eps_impact not in (None, 0) for item in shocked)
    assert any(item.fair_value_impact not in (None, 0) for item in shocked)


def test_a_suppressed_eps_removes_the_per_share_sensitivity_output():
    items, _gaps = sensitivity.rank(
        _three_year_spec(), top=5, eps_verified=False, pe_verified=False
    )

    assert items
    assert all(item.eps_impact is None for item in items)
    assert all(item.fair_value_impact is None for item in items)
    assert all(item.verification_status == "SUPPRESSED" for item in items)
