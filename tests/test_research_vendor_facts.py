"""Reported actuals come from the vendor's statements, once they could have been filed."""

from __future__ import annotations

import pytest

from tradingagents.research import kernel, vendor_facts
from tradingagents.research.schemas import Period
from tradingagents.research.vendor_facts import ReportedYear

from .test_research_kernel import spec

STATEMENT = """# Income Statement data for TEST (annual)
,2026-03-31,2025-03-31
Total Revenue,1182400000000.0,979400000000.0
Operating Income,188700000000.0,135500000000.0
Net Income Common Stockholders,157200000000.0,91100000000.0
Net Income,162000000000.0,95000000000.0
Diluted EPS,94.93,55.05
"""


def test_the_parsed_year_is_scaled_and_prefers_the_attributable_profit():
    years = vendor_facts.parse_annual_statement(STATEMENT, "JPY_bn")
    fy26 = years["2026-03-31"]
    assert fy26.revenue == pytest.approx(1182.4)
    assert fy26.operating_profit == pytest.approx(188.7)
    # "Net Income" (with minorities) is the fallback, never the answer.
    assert fy26.net_profit == pytest.approx(157.2)


def test_a_column_with_no_figures_is_dropped():
    years = vendor_facts.parse_annual_statement(
        "# Income Statement data for TEST (annual)\n,2026-03-31,2022-03-31\nTotal Revenue,100.0,\n",
        "JPY_bn",
    )
    assert list(years) == ["2026-03-31"]


def test_a_withheld_statement_is_an_error():
    with pytest.raises(vendor_facts.VendorFactsError, match="no annual statement"):
        vendor_facts.parse_annual_statement(
            "# Income Statement for X\n\nIncome Statement data is withheld for this date.", "JPY_bn"
        )


def test_a_year_is_public_only_after_the_filing_lag():
    assert not vendor_facts.is_public("2026-03-31", "2026-06-01")
    assert vendor_facts.is_public("2026-03-31", "2026-07-01")
    assert not vendor_facts.is_public("2026-03-31", "")


def test_describe_lists_only_the_public_years():
    years = {"2026-03-31": ReportedYear("2026-03-31", net_profit=157.2),
             "2024-03-31": ReportedYear("2024-03-31", net_profit=51.0)}
    text = vendor_facts.describe(years, "2025-06-30")
    assert "2024-03-31" in text and "2026-03-31" not in text


def test_a_derived_actual_is_replaced_by_the_reported_one():
    model_spec = spec()
    assert model_spec.actuals["FY24"].net_profit == pytest.approx(6.0)
    vendor_facts.merge_reported_actuals(
        model_spec, {"2024-12-31": ReportedYear("2024-12-31", net_profit=5.8)}, "2025-06-30"
    )
    assert model_spec.actuals["FY24"].net_profit == pytest.approx(5.8)
    assert any("taken from the reported annual statement" in w for w in model_spec.warnings)


def test_a_matching_actual_is_left_alone():
    model_spec = spec()
    before = len(model_spec.warnings)
    vendor_facts.merge_reported_actuals(
        model_spec, {"2024-12-31": ReportedYear("2024-12-31", net_profit=6.0)}, "2025-06-30"
    )
    assert model_spec.actuals["FY24"].net_profit == pytest.approx(6.0)
    assert len(model_spec.warnings) == before


def test_a_value_that_is_far_off_is_a_scale_error_not_a_correction():
    model_spec = spec()
    vendor_facts.merge_reported_actuals(
        model_spec, {"2024-12-31": ReportedYear("2024-12-31", net_profit=8.0)}, "2025-06-30"
    )
    assert model_spec.actuals["FY24"].net_profit == pytest.approx(6.0)
    assert any("more than a correction" in w for w in model_spec.warnings)


def test_a_year_that_was_not_public_yet_is_not_used():
    model_spec = spec()
    vendor_facts.merge_reported_actuals(
        model_spec, {"2024-12-31": ReportedYear("2024-12-31", net_profit=5.8)}, "2025-02-01"
    )
    assert model_spec.actuals["FY24"].net_profit == pytest.approx(6.0)
    assert not any("reported annual statement" in w for w in model_spec.warnings)


def test_an_actual_year_the_model_left_empty_is_filled_in():
    model_spec = spec()
    model_spec.periods.append(Period("FY23", "2023-01-01", "2023-12-31", "actual"))
    vendor_facts.merge_reported_actuals(
        model_spec,
        {"2023-12-31": ReportedYear("2023-12-31", revenue=90.0, net_profit=5.0)},
        "2025-06-30",
    )
    assert model_spec.actuals["FY23"].revenue == pytest.approx(90.0)
    assert kernel.run(model_spec).row("FY23").net_profit == pytest.approx(5.0)


def test_the_prompt_text_is_empty_when_no_year_is_public():
    years = {"2026-03-31": ReportedYear("2026-03-31", net_profit=157.2)}
    assert vendor_facts.describe(years, "2026-06-01") == ""


def test_the_unit_sets_the_scale():
    years = vendor_facts.parse_annual_statement(STATEMENT, "USD_m")
    assert years["2026-03-31"].revenue == pytest.approx(1_182_400.0)
