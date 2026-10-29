"""The model kernel computes, and refuses to produce numbers that do not tie."""

from __future__ import annotations

import json

import pytest

from tradingagents.research.kernel import ModelError, eps, market_cap, run, shares_implied
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


def spec(**overrides) -> ModelSpec:
    base = ModelSpec(
        ticker="TEST",
        company="Test Corp",
        currency="USD",
        unit="USD_m",
        fiscal_year_end_month=12,
        periods=[
            Period("FY24", "2024-01-01", "2024-12-31", "actual"),
            Period("FY25", "2025-01-01", "2025-12-31", "guidance"),
            Period("H1 FY25", "2025-01-01", "2025-06-30", "guidance", kind="half"),
            Period("FY26", "2026-01-01", "2026-12-31", "forecast"),
        ],
        actuals={"FY24": Financials(revenue=100.0, operating_profit=10.0, net_profit=6.0)},
        guidance={
            "FY25": Financials(revenue=120.0, operating_profit=15.0, net_profit=9.0),
            "H1 FY25": Financials(revenue=55.0, operating_profit=7.0, net_profit=4.0),
        },
        forecast={"FY26": Financials(net_profit=10.0)},
        quarterly={"Q1 FY25": Financials(revenue=28.0, operating_profit=3.5, net_profit=2.0)},
        quarterly_plan={
            "fy_label": "FY25",
            "h1_label": "H1 FY25",
            "q1_label": "Q1 FY25",
            "h2_split": {"revenue": [0.5, 0.5], "operating_profit": [0.5, 0.5], "net_profit": [0.5, 0.5]},
        },
        bridges=[
            Bridge("revenue", "FY25", "FY26", 120.0, [Line("New business", 10.0)], declared_total=130.0),
            Bridge("operating_profit", "FY25", "FY26", 15.0, [Line("New business", 1.0)], declared_total=16.0),
        ],
        segments={
            "FY25": [
                Segment("A", 80.0, 10.0),
                Segment("B", 40.0, 5.0),
            ]
        },
        cashflow={
            "FY25": CashFlow(net_profit=9.0, depreciation=4.0, working_capital_change=-2.0, capex=3.0)
        },
        share_bases=[ShareBase("basic", 10.0)],
        scenarios=[
            Scenario("Base", "steady", eps=1.0, multiple=12.0),
            Scenario("Bear", "weak demand", eps=0.8, multiple=10.0),
        ],
        valuation=ValuationSpec(
            price=20.0, fair_value_multiple_low=10.0, fair_value_multiple_high=12.0,
            sensitivity_multiples=[8.0, 10.0, 12.0],
        ),
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_eps_and_market_cap_respect_the_unit():
    assert eps(net_profit=310.0, shares_m=1775.2, unit="JPY_bn") == pytest.approx(174.6, abs=0.05)
    assert market_cap(5000.0, 1775.2, "JPY_bn") == pytest.approx(8876.0, abs=0.1)
    assert eps(net_profit=10.0, shares_m=10.0, unit="USD_m") == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("net_profit", "units", "expected"),
    [(310.0, "JPY_bn", 1775.2), (10.0, "USD_m", 10.0)],
)
def test_the_share_count_a_quoted_eps_implies_inverts_the_unit(net_profit, units, expected):
    quoted = eps(net_profit=net_profit, shares_m=expected, unit=units)
    assert shares_implied(net_profit, quoted, units) == pytest.approx(expected)


def test_the_model_builds_rows_and_bridges():
    model = run(spec())
    assert model.row("FY26").revenue == pytest.approx(130.0)
    assert model.row("FY26").operating_profit == pytest.approx(16.0)
    assert model.row("FY25").operating_margin == pytest.approx(12.5)
    assert model.row("FY24").eps["basic"] == pytest.approx(0.6)
    assert model.market_cap == pytest.approx(200.0)


def test_quarterly_plan_derives_the_remaining_quarters():
    model = run(spec())
    model.spec.quarterly = {}          # derived quarters live on the spec after run()
    rebuilt = run(spec())
    assert rebuilt.spec.quarterly["Q2 FY25"].revenue == pytest.approx(27.0)
    assert rebuilt.spec.quarterly["Q3 FY25"].revenue == pytest.approx(32.5)
    assert rebuilt.spec.quarterly["Q4 FY25"].revenue == pytest.approx(32.5)
    assert model.quarterly_comparison == []


def test_quarterly_derivation_matches_a_reported_table_or_reports_the_gap():
    reported = {
        "Q1 FY25": Financials(revenue=28.0, operating_profit=3.5, net_profit=2.0),
        "Q2 FY25": Financials(revenue=27.0, operating_profit=3.5, net_profit=2.0),
        "Q3 FY25": Financials(revenue=30.0, operating_profit=3.5, net_profit=2.5),
        "Q4 FY25": Financials(revenue=35.0, operating_profit=4.5, net_profit=2.5),
    }
    model = run(spec(quarterly=reported))
    assert any("Q3 FY25 revenue" in message for message in model.quarterly_comparison)


def test_a_bridge_that_does_not_sum_fails_the_model():
    broken = spec()
    broken.bridges[0] = Bridge("revenue", "FY25", "FY26", 120.0, [Line("New business", 5.0)],
                               declared_total=130.0)
    with pytest.raises(ModelError, match="does not sum"):
        run(broken)


def test_a_revenue_bridge_that_contradicts_a_stated_value_fails():
    conflicting = spec()
    conflicting.forecast["FY26"] = Financials(revenue=140.0, net_profit=10.0)
    with pytest.raises(ModelError, match="stated as 140"):
        run(conflicting)


def test_segments_must_sum_to_the_group():
    broken = spec(segments={"FY25": [Segment("A", 80.0, 10.0), Segment("B", 30.0, 5.0)]})
    with pytest.raises(ModelError, match="segments sum to 110"):
        run(broken)


def test_cash_flow_net_profit_must_match_the_model():
    broken = spec()
    broken.cashflow["FY25"] = CashFlow(net_profit=8.0, depreciation=4.0,
                                       working_capital_change=-2.0, capex=3.0)
    with pytest.raises(ModelError, match="cash flow uses net profit"):
        run(broken)


def test_unknown_period_is_rejected():
    broken = spec()
    broken.actuals["FY23"] = Financials(revenue=90.0)
    with pytest.raises(ModelError, match="undeclared period"):
        run(broken)


def test_derived_lines_must_state_a_formula():
    with pytest.raises(ModelError, match="must state its formula"):
        Line("Project revenue", -20.0, provenance="derived")


def test_unknown_provenance_is_rejected():
    with pytest.raises(ModelError, match="Unknown provenance"):
        Line("Guess", 1.0, provenance="vibes")


@pytest.mark.parametrize(
    ("written", "expected"),
    [("actual", "disclosed"), ("reported", "disclosed"), ("estimate", "assumption"),
     ("forecast", "assumption"), ("guidance", "guidance")],
)
def test_common_provenance_vocabulary_is_normalised(written, expected):
    line = Line("Revenue", 100.0, provenance=written)
    assert line.provenance == expected


def test_the_error_names_the_allowed_values():
    with pytest.raises(ModelError, match="disclosed, guidance, derived"):
        Line("Guess", 1.0, provenance="vibes")


def test_market_cap_uses_outstanding_shares_when_given():
    model_spec = spec()
    model_spec.valuation.market_cap_shares_m = 8.0
    assert run(model_spec).market_cap == pytest.approx(160.0)


def test_market_cap_falls_back_to_the_eps_base():
    assert run(spec()).market_cap == pytest.approx(200.0)


def test_an_implausible_capex_ratio_raises_a_unit_warning():
    model_spec = spec()
    model_spec.cashflow["FY25"].capex = 60.0     # 50% of revenue: a 10x unit slip
    model = run(model_spec)
    assert any("check the unit" in warning for warning in model.warnings)


def test_null_sections_are_treated_as_empty():
    data = spec().to_dict()
    data["segments"] = None
    data["actuals"] = None
    data["periods"] = None
    restored = ModelSpec.from_dict(data)
    assert restored.segments == {}
    assert restored.actuals == {}
    assert restored.periods == []


def test_a_bridge_that_starts_from_the_wrong_base_fails():
    broken = spec()
    broken.bridges[0].base_value = 999.0
    with pytest.raises(ModelError, match="starts from 999"):
        run(broken)


def test_quarterly_plan_labels_are_resolved_when_written_differently():
    from tradingagents.research import builder

    model_spec = spec()
    model_spec.quarterly_plan["h1_label"] = "1H FY25"
    builder._normalise_spec(model_spec)
    assert model_spec.quarterly_plan["h1_label"] == "H1 FY25"
    run(model_spec)


def test_an_unresolvable_quarterly_plan_is_dropped_not_fatal():
    from tradingagents.research import builder

    model_spec = spec()
    model_spec.quarterly = {
        "Q1 FY25": Financials(revenue=28.0, operating_profit=3.5, net_profit=2.0),
        "Q2 FY25": Financials(revenue=27.0, operating_profit=3.5, net_profit=2.0),
        "Q3 FY25": Financials(revenue=32.5, operating_profit=4.0, net_profit=2.5),
        "Q4 FY25": Financials(revenue=32.5, operating_profit=4.0, net_profit=2.5),
    }
    model_spec.quarterly_plan = {
        "fy_label": "FY25", "h1_label": "H1 SOMEWHERE ELSE", "q1_label": "Q1 FY25",
        "h2_split": {},
    }
    model_spec.guidance.pop("H1 FY25")
    builder._normalise_spec(model_spec)
    assert model_spec.quarterly_plan is None
    assert any("Dropped the quarterly plan" in warning for warning in model_spec.warnings)
    run(model_spec)


def test_spec_round_trips_through_json():
    original = spec()
    restored = ModelSpec.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored.to_dict() == original.to_dict()
