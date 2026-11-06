"""The four research controls, independent of any company.

Calculation vs assumption, dominant-segment reconciliation, driver naming and the
three trigger types. Each test uses a synthetic company so the rule, not a ticker,
is what passes.
"""

from __future__ import annotations

from tradingagents.research import assumptions, segments, sensitivity, triggers
from tradingagents.research.schemas import (
    Bridge,
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
        guidance={"FY26": Financials(revenue=110.0, operating_profit=12.0, net_profit=8.0)},
        forecast={
            "FY26": Financials(revenue=110.0, operating_profit=12.0, net_profit=8.0),
            "FY27": Financials(revenue=120.0, operating_profit=14.0, net_profit=9.0),
        },
        bridges=[
            Bridge("revenue", "FY25", "FY26", 100.0, [Line("growth", 10.0)], declared_total=110.0),
            Bridge(
                "operating_profit", "FY25", "FY26", 10.0, [Line("growth", 2.0)],
                declared_total=12.0,
            ),
        ],
        share_bases=[
            ShareBase("basic", 10.0, basic_weighted_average=10.0),
            ShareBase("diluted", 10.6, basic_weighted_average=10.0,
                      diluted_weighted_average=10.6),
        ],
        scenarios=[Scenario("Base", "steady", eps=0.80, multiple=11.0, period="FY26")],
        eps_basis="basic",
        warnings=[
            "FY26: the forecast equals guidance because the model is built from the "
            "company's own disclosed half-year and guidance; this is stated so the "
            "agreement is not read as adoption."
        ],
        valuation=ValuationSpec(
            price=20.0,
            fair_value_multiple_low=10.0,
            fair_value_multiple_high=12.0,
            fair_value_low=8.0,
            fair_value_high=9.6,
            fair_value_basis="basic",
            fair_value_period="FY26",
            market_cap_basis="diluted",
            forecast_eps_basis="basic",
        ),
    )


# --- 1. calculation vs assumption ---------------------------------------------


def test_an_analyst_multiple_is_labelled_while_the_calculation_stays_verified():
    model = spec()
    model.valuation.assumptions = [
        assumptions.MethodAssumption(
            method="pe",
            earnings_source="company_guidance",
            multiple_source="analyst_assumption",
        )
    ]

    profile = assumptions.for_spec(model, "pe")

    assert profile.earnings_source == assumptions.COMPANY_GUIDANCE
    assert profile.multiple_source == assumptions.ANALYST_ASSUMPTION
    assert profile.status == assumptions.ANALYST_ASSUMPTION
    assert assumptions.wording("VERIFIED", profile) == (
        "Mechanically verified; valuation multiple is an analyst assumption."
    )


def test_an_external_multiple_needs_its_source_fields_or_it_is_unresolved():
    incomplete = assumptions.MethodAssumption(
        method="pe",
        earnings_source="company_reported",
        multiple_source="external_estimate",
        source_name="Broker note",
    )
    profile = assumptions.for_spec(
        _with_assumption(incomplete), "pe"
    )
    assert profile.multiple_source == assumptions.UNRESOLVED
    assert "unsupported" in assumptions.wording("VERIFIED", profile)

    complete = assumptions.MethodAssumption(
        method="pe",
        earnings_source="company_reported",
        multiple_source="external_estimate",
        source_name="Broker note",
        as_of="2026-11-04",
        metric="forward P/E",
        period="FY26",
        comparability="SAME_METRIC_SAME_PERIOD_SAME_BASIS",
        refs=["doc-1"],
    )
    profile = assumptions.for_spec(_with_assumption(complete), "pe")
    assert profile.multiple_source == assumptions.EXTERNAL_ESTIMATE
    assert "externally estimated" in assumptions.wording("VERIFIED", profile)


def _with_assumption(profile):
    model = spec()
    model.valuation.assumptions = [profile]
    return model


def test_the_header_prints_both_statuses_and_the_sentence():
    from tradingagents.reporting import _header

    header = _header("TEST", {
        "final_trade_decision": "**Rating**: Hold",
        "calculation_status": "VERIFIED",
        "assumption_status": "ANALYST_ASSUMPTION",
        "valuation_wording": (
            "Mechanically verified; valuation multiple is an analyst assumption."
        ),
    }, None)

    assert "CALCULATION_STATUS=VERIFIED" in header
    assert "ASSUMPTION_STATUS=ANALYST_ASSUMPTION" in header
    assert "valuation multiple is an analyst assumption" in header


def test_the_rendered_package_prints_the_exact_assumption_sentence():
    from tradingagents.research import kernel, report, valuation

    model = spec()
    model.valuation.assumptions = [
        assumptions.MethodAssumption(
            method="pe",
            earnings_source="company_guidance",
            multiple_source="analyst_assumption",
        )
    ]
    result = kernel.run(model)

    text = report.compact_summary(result, valuation.value(result))

    assert "CALCULATION_STATUS = VERIFIED; ASSUMPTION_STATUS = ANALYST_ASSUMPTION" in text
    assert (
        "Mechanically verified; valuation multiple is an analyst assumption." in text
    )


# --- 2. dominant segment reconciliation ---------------------------------------


def test_a_disclosed_segment_set_that_the_model_omits_is_insufficient_data():
    model = spec()
    model.segment_disclosure = True

    result = segments.status(model)

    assert result.status == segments.INSUFFICIENT_DATA
    assert "carries no segment" in result.detail


def test_a_dominant_segment_without_forecast_rows_is_insufficient_data():
    model = spec()
    model.segments = {
        "FY25": [Segment("A", 100.0, 50.0), Segment("B", 50.0, 10.0)],
    }

    result = segments.status(model)

    assert result.status == segments.INSUFFICIENT_DATA
    assert result.segment == "A"
    assert result.share is not None and result.share > 0.80
    assert "FY26" in result.detail and "FY27" in result.detail


def test_a_dominant_segment_forecast_for_every_year_verifies():
    model = spec()
    model.segments = {
        "FY25": [Segment("A", 100.0, 50.0), Segment("B", 50.0, 10.0)],
        "FY26": [Segment("A", 110.0, 55.0), Segment("B", 50.0, 10.0)],
        "FY27": [Segment("A", 120.0, 60.0), Segment("B", 50.0, 10.0)],
    }

    result = segments.status(model)

    assert result.status == segments.VERIFIED
    assert "Segment forecast (A)" in segments.table(model)


def test_a_diversified_company_does_not_need_segment_forecasts():
    model = spec()
    model.segments = {
        "FY25": [Segment("A", 100.0, 30.0), Segment("B", 100.0, 30.0)],
    }

    result = segments.status(model)

    assert result.status == segments.NOT_REQUIRED


def test_a_bottom_up_claim_is_removed_when_segments_do_not_reconcile(tmp_path):
    from tradingagents.reporting import write_report_tree

    state = {
        "trade_date": "2026-11-05",
        "segment_reconciliation_status": "INSUFFICIENT_DATA",
        "fundamentals_report": "This is a bottom-up consolidated build.",
        "final_trade_decision": "**Rating**: Hold\n\n**Executive Summary**: bottom-up.",
    }
    path = write_report_tree(state, "TEST", tmp_path, html=False)
    main = path.read_text(encoding="utf-8")

    assert "bottom-up" not in main.lower()
    assert "consolidated" in main.lower()


# --- 3. driver metadata and naming --------------------------------------------


def test_a_direct_operating_profit_shock_is_not_named_capacity_utilisation():
    model = spec()
    model.bridges[1].lines = [
        Line(
            "Cell 8 loading",
            2.0,
            driver="Cell 8 loading",
            driver_id="cell_8_loading",
            alternative=1.5,
            segment="Electronics",
            basis="cost",
        )
    ]

    items, _gaps = sensitivity.rank(model, top=3, eps_verified=True, pe_verified=True)

    target = next(item for item in items if item.driver_id.startswith("cell_8_loading"))
    assert target.shocked_input == "operating_profit"
    assert target.sensitivity_id.endswith("_op_contribution")
    assert "operating-profit contribution" in target.driver
    assert sensitivity.canonical_id("cell_8_loading", items) == target.sensitivity_id


def test_two_drivers_on_one_base_flag_a_potential_double_count():
    from tradingagents.research import issues

    model = spec()
    model.bridges[1].lines = [
        Line("A", 1.0, driver="A", driver_id="a", alternative=2.0,
             revenue_base_id="ai-substrate"),
        Line("B", 1.0, driver="B", driver_id="b", alternative=2.0,
             revenue_base_id="ai-substrate"),
    ]

    found = [issue for issue in issues.collect(model) if issue.kind == "driver_overlap"]

    assert found and "POTENTIAL_DOUBLE_COUNT" in found[0].detail


def test_a_direct_shock_named_capacity_utilisation_still_names_the_real_input():
    model = spec()
    model.bridges[1].lines = [
        Line(
            "Capacity utilisation",
            2.0,
            driver="Capacity utilisation",
            driver_id="capacity_utilization",
            alternative=1.5,
            segment="Electronics",
            basis="cost",
        )
    ]

    items, _gaps = sensitivity.rank(model, top=3, eps_verified=True, pe_verified=True)

    target = next(
        item for item in items if item.driver_id == "capacity_utilization"
    )
    assert target.sensitivity_id.endswith("_op_contribution")
    assert "operating-profit contribution" in target.driver


# --- 4. trigger types ----------------------------------------------------------


def _trigger(**attrs):
    values = {
        "kind": "THESIS_INVALIDATION_TRIGGER",
        "description": "event",
        "metric": "",
        "period": "",
        "materiality": "high",
        "temporary_explanation": "",
        "thesis_link": "structural claim",
    }
    values.update(attrs)
    return values


def test_repeating_guidance_is_a_forecast_revision_not_a_thesis_break():
    model = spec()
    trigger = _trigger(
        description="FY26 operating profit of 12.0 is company guidance",
        metric="operating_profit",
        period="FY26",
    )

    forecast, review, thesis, findings = triggers.normalise([], [], [trigger], model)

    assert forecast == [trigger]
    assert review == [] and thesis == []
    assert any("forecast revision" in finding for finding in findings)


def test_a_minor_quarterly_breach_is_a_rating_review_not_invalidation():
    model = spec()
    trigger = _trigger(
        description="Q1 operating margin 100bp below plan",
        metric="segment_margin",
        period="FY26",
        materiality="minor",
    )

    _forecast, review, thesis, findings = triggers.normalise([], [], [trigger], model)

    assert review == [trigger]
    assert thesis == []
    assert any("rating review" in finding for finding in findings)


def test_an_advances_decline_without_a_cause_is_not_negative():
    model = spec()
    trigger = _trigger(
        description="Customer advances fell 10%",
        metric="advances",
        committed_capex_continues=True,
    )

    _forecast, review, thesis, findings = triggers.normalise([], [], [trigger], model)

    assert review == [] and thesis == []
    assert any("no documented" in finding for finding in findings)


def test_advances_with_a_documented_cause_become_a_warning():
    model = spec()
    trigger = _trigger(
        description="Customer advances fell because a customer deferred delivery",
        metric="advances",
        cause="deferral",
        committed_capex_continues=True,
    )

    _forecast, review, thesis, findings = triggers.normalise([], [], [trigger], model)

    assert review == [trigger]
    assert thesis == []
    assert any("warning" in finding for finding in findings)
