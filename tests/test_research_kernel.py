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
        # Scenarios need a tax basis to reach net income; the fixture states one
        # rather than borrowing a ratio from the base year.
        effective_tax_rate=0.25,
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


def test_a_forecast_year_cannot_keep_net_profit_without_its_topline():
    """The orphan that showed up as n/a revenue on a real run: a model may not
    write a bottom line with no business behind it — forecast the topline."""
    model_spec = spec(bridges=[])          # nothing fills FY26's topline
    model_spec.forecast["FY26"] = Financials(net_profit=10.0)
    with pytest.raises(ModelError, match="topline"):
        run(model_spec)


def test_a_reported_year_may_still_lack_its_topline():
    """A disclosure gap in an actual is a fact, not a modelling choice."""
    model_spec = spec()
    model_spec.actuals["FY24"] = Financials(net_profit=6.0)
    result = run(model_spec)
    assert result.row("FY24").revenue is None


def test_a_base_scenario_outside_the_fair_value_band_is_rejected():
    """A real run published a ¥4,715-6,286 fair value and a ¥7,970 Base target:
    two valuations with no bridge. A warning cannot help — the model has already
    finished writing — so this is an error that enters the repair round."""
    model_spec = spec()
    model_spec.scenarios[0].multiple = 16.0        # fixture Base: EPS 1.0 ->
    with pytest.raises(ModelError, match="outside the fair-value range"):
        run(model_spec)                            # target 16, band 10-12


def test_a_period_cannot_be_declared_twice():
    """The rebuild duplicated FY3/27 (one 'guidance' entry, one 'forecast'
    entry), so the package printed the annual row twice."""
    model_spec = spec()
    model_spec.periods.append(model_spec.periods[1])
    with pytest.raises(ModelError, match="more than once"):
        run(model_spec)


def test_a_guided_period_forecast_over_guidance_must_be_relabelled():
    from tradingagents.research.schemas import Bridge, Financials, Line

    model_spec = spec(
        forecast={"FY26": Financials(net_profit=10.0), "FY25": Financials(revenue=130.0)},
        bridges=[
            Bridge("revenue", "FY25", "FY26", 130.0, [Line("New business", 10.0)],
                   declared_total=140.0),
            Bridge("operating_profit", "FY25", "FY26", 15.0, [Line("New business", 1.0)],
                   declared_total=16.0),
        ],
        segments={},
    )
    # FY25 still claims management's label while carrying the model's number.
    with pytest.raises(ModelError, match="basis 'guidance'"):
        run(model_spec)

    model_spec.periods[1].basis = "forecast"
    # relabelling moves the fair-value anchor to FY25 (EPS 0.9): keep the Base
    # scenario inside the band so only the relabelling is under test here
    model_spec.scenarios[0].multiple = 10.0
    model_spec.scenarios[0].eps = 0.9          # FY25 is the valuation anchor now
    model_spec.scenarios[0].period = "FY25"
    model = run(model_spec)
    assert model.row("FY25").revenue == 130.0
    assert model.spec.guidance["FY25"].revenue == 120.0    # management's number kept


def test_running_the_kernel_twice_does_not_stack_warnings():
    """Warnings are computed on every validation and some are already persisted
    in the spec file; re-running must not publish the same warning twice."""
    model_spec = spec()
    model_spec.cashflow["FY25"] = CashFlow(
        net_profit=9.0, depreciation=30.0, working_capital_change=-2.0, capex=30.0
    )
    run(model_spec)
    after_first = list(model_spec.warnings)
    assert sum("capex is" in w for w in after_first) == 1

    run(model_spec)
    assert model_spec.warnings == after_first
    assert sum("capex is" in w for w in model_spec.warnings) == 1


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


def test_a_plan_without_h1_guidance_is_rebuilt_from_the_reported_halves():
    """A company that published no H1 guidance row is not a mislabelled plan:
    H1 is rebuilt from reported Q1+Q2, and H2 = FY guidance - H1 still checks
    the back half (the 4062.T case: guidance carried FY only)."""
    from tradingagents.research import builder

    model_spec = spec()
    model_spec.quarterly = {
        "Q1 FY25": Financials(revenue=28.0, operating_profit=3.5, net_profit=2.0),
        "Q2 FY25": Financials(revenue=27.0, operating_profit=3.5, net_profit=2.0),
        "Q3 FY25": Financials(revenue=32.5, operating_profit=4.0, net_profit=2.5),
        "Q4 FY25": Financials(revenue=32.5, operating_profit=4.0, net_profit=2.5),
    }
    model_spec.quarterly_plan = {
        "fy_label": "FY25", "h1_label": "H1 FY25", "q1_label": "Q1 FY25", "h2_split": {},
    }
    model_spec.guidance.pop("H1 FY25")
    builder._normalise_spec(model_spec)

    assert model_spec.quarterly_plan is not None
    assert model_spec.quarterly_plan["h1_values"] == {
        "revenue": 55.0, "operating_profit": 7.0, "net_profit": 4.0,
    }
    assert any("H1 guidance row" in warning for warning in model_spec.warnings)
    model = run(model_spec)
    assert model.spec.quarterly["Q3 FY25"].revenue == pytest.approx(32.5)
    assert model.quarterly_comparison == []


def test_a_forecast_that_equals_guidance_must_explain_the_coincidence():
    """Guidance is a reference: a non-guidance row identical to it on every line
    is adoption, not a build — unless the model says why they coincide."""
    model_spec = spec()
    model_spec.forecast["FY25"] = Financials(revenue=120.0, operating_profit=15.0, net_profit=9.0)
    model_spec.periods[1].basis = "forecast"
    model_spec.scenarios[0].multiple = 10.0      # keep the Base target in its band
    model_spec.scenarios[0].eps = 0.9            # and on FY25, the valuation period
    model_spec.scenarios[0].period = "FY25"
    with pytest.raises(ModelError, match="adoption"):
        run(model_spec)

    model_spec.warnings.append(
        "FY25 forecast equals management guidance because the H1 actual plus the H2 "
        "guide tie to it exactly (management's own numbers, confirmed by the Q2 filing)."
    )
    run(model_spec)                              # the coincidence is on the record


def test_spec_round_trips_through_json():
    original = spec()
    restored = ModelSpec.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored.to_dict() == original.to_dict()


# --- disclosed figures are checked, not re-derived ---------------------------


def test_a_bridge_from_a_period_to_itself_is_rejected():
    model_spec = spec(
        bridges=[
            Bridge("revenue", "FY25", "FY25", 120.0,
                   [Line("Guidance plus a beat", 5.0)], declared_total=120.0),
        ],
        segments={},
    )
    with pytest.raises(ModelError, match="to itself"):
        run(model_spec)


def test_a_stated_eps_that_contradicts_net_profit_fails():
    """0.6 is net profit 6.0 over 10m shares; a stated 0.9 is one input wrong."""
    model_spec = spec(
        actuals={"FY24": Financials(
            revenue=100.0, operating_profit=10.0, net_profit=6.0, eps=0.9
        )}
    )
    with pytest.raises(ModelError, match="states EPS"):
        run(model_spec)


def test_a_stated_eps_within_the_declared_tolerance_is_accepted():
    model_spec = spec(
        actuals={"FY24": Financials(
            revenue=100.0, operating_profit=10.0, net_profit=6.0, eps=0.65
        )}
    )
    model = run(model_spec)
    check = next(c for c in model.checks if c.name == "FY24 stated EPS (basic)")
    assert check.passed


def test_a_scenario_target_that_is_not_eps_times_multiple_fails():
    model_spec = spec(
        scenarios=[
            Scenario("Base", "steady", eps=1.0, multiple=12.0, target_price=15.0),
            Scenario("Bear", "weak demand", eps=0.8, multiple=10.0),
        ]
    )
    with pytest.raises(ModelError, match="scenario Base states a target"):
        run(model_spec)


def test_a_scenario_without_a_target_derives_it_and_says_so():
    model = run(spec())
    assert model.spec.scenarios[0].target_price is None
    assert model.spec.scenarios[0].resolved_target == pytest.approx(12.0)
    assert any("derived" in check.name for check in model.checks)


# --- one valuation period ----------------------------------------------------


def test_a_scenario_built_on_another_years_eps_fails():
    """The 4062.T shape: the band multiplies FY26 EPS while Base prices FY25."""
    model_spec = spec(
        scenarios=[
            Scenario("Base", "steady", eps=0.9, multiple=10.0),    # FY25 net 9 / 10 shares
            Scenario("Bear", "weak demand", eps=0.8, multiple=10.0),
        ]
    )
    with pytest.raises(ModelError, match="FY25's EPS"):
        run(model_spec)


def test_a_scenario_declaring_another_period_fails():
    model_spec = spec()
    model_spec.scenarios[0].period = "FY25"
    with pytest.raises(ModelError, match="one model, one valuation period"):
        run(model_spec)


def test_a_scenario_declaring_the_valuation_period_passes():
    model_spec = spec()
    model_spec.scenarios[0].period = "FY26"
    run(model_spec)


def test_fair_value_period_moves_the_band_to_that_year():
    from tradingagents.research.valuation import value

    model_spec = spec(valuation=ValuationSpec(
        price=20.0, fair_value_multiple_low=10.0, fair_value_multiple_high=12.0,
        sensitivity_multiples=[8.0, 10.0, 12.0], fair_value_period="FY25",
    ))
    model_spec.scenarios = [
        Scenario("Base", "steady", eps=0.9, multiple=10.0, period="FY25"),
        Scenario("Bear", "weak demand", eps=0.8, multiple=10.0, period="FY25"),
    ]
    model = run(model_spec)
    valuation = value(model)
    assert valuation.period == "FY25"
    assert valuation.eps == pytest.approx(0.9)
    assert valuation.fair_value_low == pytest.approx(9.0)


def test_a_historical_replay_keeps_the_published_mix_as_a_warning():
    """A replay reproduces the archived report; it does not correct it."""
    model_spec = spec(scenarios=[
        Scenario("Base", "steady", eps=0.9, multiple=12.0),      # FY25 EPS, FY26 band
        Scenario("Bear", "weak demand", eps=0.8, multiple=10.0),
    ])
    model = run(model_spec, strict=False)
    assert any("historical replay" in warning for warning in model.spec.warnings)


def test_a_per_share_figure_must_multiply_back_to_its_total():
    from tradingagents.research.kernel import reconciles_per_share

    assert reconciles_per_share(10.0, 10.0, 100.0, "USD_m") is True
    # Rounding in the printed share count is allowed up to 1%.
    assert reconciles_per_share(10.05, 10.0, 100.0, "USD_m") is True
    assert reconciles_per_share(10.2, 10.0, 100.0, "USD_m") is False
    assert reconciles_per_share(None, 10.0, 100.0, "USD_m") is False
