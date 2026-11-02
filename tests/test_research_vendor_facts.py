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


def test_the_statement_unit_follows_the_market_not_the_ticker():
    # Asian markets report in billions (億/억); everything else in millions.
    assert vendor_facts.statement_unit("5803.T") == "LOCAL_bn"
    assert vendor_facts.statement_unit("3711.TW") == "LOCAL_bn"
    assert vendor_facts.statement_unit("005930.KS") == "LOCAL_bn"
    assert vendor_facts.statement_unit("AAPL") == "LOCAL_m"
    assert vendor_facts.statement_unit("AAPL.US") == "LOCAL_m"
    assert vendor_facts.statement_unit("") == "LOCAL_m"


def test_the_vendor_text_is_labelled_as_fallback_evidence():
    """Corpus first: the prompt must say a figure stated in a document wins."""
    years = {"2024-03-31": ReportedYear("2024-03-31", revenue=100.0)}
    annual = vendor_facts.describe(years, "2026-10-30")
    quarters = vendor_facts.describe_quarters(years, "2026-10-30")
    for text in (annual, quarters):
        assert "fallback evidence" in text
        assert "the document wins" in text
    assert "year ended 2024-03-31" in annual
    assert "quarter ended 2024-03-31" in quarters
    assert vendor_facts.describe({}, "2026-10-30") == ""
    assert vendor_facts.describe_quarters({}, "2026-10-30") == ""


# The six real quarterly rows Bloomberg serves for 4062 JP Equity: millions of
# yen dated on fiscal period ends — the Q1/Q2 figures the model had estimated.
QUARTERS = [
    {"Date": "2025-06-30", "IS_ADJ_SALES_REVENUE_TURNOVER": 97464.0, "IS_OPER_INC": 17636.0, "NET_INCOME": 12728.0},
    {"Date": "2025-09-30", "IS_ADJ_SALES_REVENUE_TURNOVER": 98021.0, "IS_OPER_INC": 14937.0, "NET_INCOME": 9341.0},
    {"Date": "2025-12-31", "IS_ADJ_SALES_REVENUE_TURNOVER": 103136.0, "IS_OPER_INC": 11954.0, "NET_INCOME": 8931.0},
    {"Date": "2026-03-31", "IS_ADJ_SALES_REVENUE_TURNOVER": 117580.0, "IS_OPER_INC": 17500.0, "NET_INCOME": 32713.0},
    {"Date": "2026-06-30", "IS_ADJ_SALES_REVENUE_TURNOVER": 123219.0, "IS_OPER_INC": 26880.0, "NET_INCOME": 17918.0},
    {"Date": "2026-09-30", "IS_ADJ_SALES_REVENUE_TURNOVER": 139327.0, "IS_OPER_INC": 33236.0, "NET_INCOME": 20656.0},
]


def _csv(quarterly: bool, as_of: str) -> str:
    from tradingagents.dataflows.vendors.bloomberg.blpapi_backend import _income_statement_csv

    return _income_statement_csv(QUARTERS, quarterly=quarterly, as_of=as_of)


def test_the_backend_quarterly_statement_withholds_the_unfiled_quarter():
    quarters = vendor_facts.parse_annual_statement(_csv(True, "2026-10-30"), "JPY_bn")
    # 2026-09-30 + 90 days is after the run: that quarter could not have been filed.
    assert "2026-09-30" not in quarters
    # Q1 FY3/27, an actual where the model had an estimate.
    assert quarters["2026-06-30"].revenue == pytest.approx(123.219)
    assert quarters["2026-06-30"].operating_profit == pytest.approx(26.880)
    assert quarters["2026-06-30"].net_profit == pytest.approx(17.918)


def test_the_backend_annual_statement_sums_the_four_quarters():
    years = vendor_facts.parse_annual_statement(_csv(False, "2026-10-30"), "JPY_bn")
    fy26 = years["2026-03-31"]
    # 97,464 + 98,021 + 103,136 + 117,580 = 416,201 million = the model's 416.1bn.
    assert fy26.revenue == pytest.approx(416.201)
    assert fy26.operating_profit == pytest.approx(62.027)
    assert fy26.net_profit == pytest.approx(63.713)
    # An incomplete four-quarter window is never published as a part year.
    assert "2025-06-30" not in years


def test_everything_is_withheld_for_a_same_day_run():
    with pytest.raises(vendor_facts.VendorFactsError, match="no annual statement"):
        vendor_facts.parse_annual_statement(_csv(True, "2025-06-30"), "JPY_bn")
