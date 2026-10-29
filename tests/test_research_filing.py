"""The filing parser is the anchor: it must read disclosed numbers exactly."""

from __future__ import annotations

import pytest

from tradingagents.research import extract as extract_module
from tradingagents.research.filing import parse_filing
from tradingagents.research.schemas import Consensus, Financials, ModelError, Period

FILING = """2027年３月期  第１四半期決算短信〔日本基準〕（連結）
2026年８月７日
（百万円未満四捨五入）
（１）連結経営成績（累計） （％表示は、対前年同四半期増減率）
売上高 営業利益 経常利益 親会社株主に帰属する四半期純利益
百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％
2027年３月期第１四半期 402,009 50.1 104,828 155.1 111,456 166.8 80,434 156.8
2026年３月期第１四半期 267,908 22.7 41,086 68.0 41,775 60.1 31,318 63.9
---
（％表示は、通期は対前期、四半期は対前年同四半期増減率）
売上高 営業利益 経常利益 親会社株主に帰属する当期純利益 １株当たり当期純利益
百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％ 円 銭
第２四半期（累計） 821,000 46.9 198,000 119.6 209,000 127.9 149,000 121.9 89.98
通期 1,755,000 48.4 432,000 128.9 453,000 127.1 326,000 107.4 196.88
①  期末発行済株式数（自己株式を含む） 2027年３月期１Ｑ 1,775,180,526株 2026年３月期 1,775,180,526株
③  期中平均株式数（四半期累計） 2027年３月期１Ｑ 1,655,834,826株 2026年３月期１Ｑ 1,655,468,470株
"""


def facts():
    return parse_filing(FILING, "doc-5803", "2026-08-07")


def test_period_and_units_are_detected():
    parsed = facts()
    assert parsed.fiscal_year_end == "FY3/27"
    assert parsed.quarter == 1
    assert parsed.unit == "JPY_bn"
    assert parsed.scale == 1000.0


def test_cumulative_actuals_are_exact():
    actual = facts().actuals["Q1 FY3/27"]
    assert actual.revenue == pytest.approx(402.009)
    assert actual.operating_profit == pytest.approx(104.828)
    assert actual.ordinary_profit == pytest.approx(111.456)
    assert actual.net_profit == pytest.approx(80.434)


def test_prior_year_quarter_is_kept_separately():
    prior = facts().prior["Q1 FY3/26"]
    assert prior.revenue == pytest.approx(267.908)
    assert prior.operating_profit == pytest.approx(41.086)


def test_guidance_includes_half_and_full_year():
    guidance = facts().guidance
    assert guidance["H1 FY3/27"].revenue == pytest.approx(821.0)
    assert guidance["H1 FY3/27"].net_profit == pytest.approx(149.0)
    assert guidance["FY3/27"].operating_profit == pytest.approx(432.0)
    assert guidance["FY3/27"].net_profit == pytest.approx(326.0)


def test_share_counts_supply_both_eps_bases():
    shares = facts().shares_m
    assert shares["full_share"] == pytest.approx(1775.180526)
    assert shares["company_style"] == pytest.approx(1655.834826)


def test_outstanding_shares_exclude_treasury():
    assert facts().treasury_m is None          # the fixture has no treasury line
    with_treasury = FILING + "\n②  期末自己株式数 2027年３月期１Ｑ 119,020,114株 2026年３月期 119,521,230株\n"
    parsed = parse_filing(with_treasury, "doc-5803")
    assert parsed.treasury_m == pytest.approx(119.020114)
    assert parsed.outstanding_m == pytest.approx(1775.180526 - 119.020114)


def test_every_fact_carries_a_citation():
    parsed = facts()
    for label in [*parsed.actuals, *parsed.guidance, *parsed.shares_m]:
        assert label in parsed.citations
        assert parsed.citations[label].document_id == "doc-5803"
        assert parsed.citations[label].quote


def test_a_reordered_metric_header_is_refused():
    broken = FILING.replace(
        "売上高 営業利益 経常利益 親会社株主に帰属する四半期純利益",
        "営業利益 売上高 経常利益 親会社株主に帰属する四半期純利益",
    )
    with pytest.raises(ModelError, match="Refusing to map"):
        parse_filing(broken, "doc-5803")


def test_a_non_filing_document_is_refused():
    with pytest.raises(ModelError, match="does not look like a quarterly"):
        parse_filing("A news article about the company.", "doc-x")


def test_merge_refreshes_disclosed_blocks_and_shares():
    parsed = extract_module.Extraction(facts(), "doc-5803")
    spec = extract_module.skeleton(parsed, "5803.T", "Fujikura", price=5000.0)
    merged = extract_module.merge(spec, parsed)
    assert merged.share_base("full_share").shares_m == pytest.approx(1775.1805, abs=1e-4)
    assert merged.guidance["FY3/27"].operating_profit == pytest.approx(432.0)


def test_a_reported_actual_replaces_a_derived_one():
    spec = extract_module.skeleton(
        extract_module.Extraction(facts(), "doc-5803"), "5803.T", "Fujikura", price=5000.0
    )
    # A model that derived the actual year as pretax less tax, before minorities.
    spec.actuals["FY3/26"] = Financials(
        revenue=1182.4, operating_profit=188.7, net_profit=162.0
    )
    spec.periods.insert(0, Period("FY3/26", "", "", "actual"))
    consensus = Consensus(
        "Bloomberg",
        eps_basis="company_style",
        periods={"FY3/26": {"revenue": 1182.0, "operating_profit": 188.707, "net_profit": 157.163}},
    )
    extract_module.merge_consensus_actuals(spec, consensus)
    assert spec.actuals["FY3/26"].net_profit == pytest.approx(157.163)
    assert any("corrected from 162.0" in warning for warning in spec.warnings)


def test_a_matching_actual_is_left_alone():
    spec = extract_module.skeleton(
        extract_module.Extraction(facts(), "doc-5803"), "5803.T", "Fujikura", price=5000.0
    )
    spec.actuals["FY3/26"] = Financials(revenue=1182.4, net_profit=157.2)
    consensus = Consensus("Bloomberg", periods={"FY3/26": {"revenue": 1182.0, "net_profit": 157.163}})
    before = len(spec.warnings)
    extract_module.merge_consensus_actuals(spec, consensus)
    assert spec.actuals["FY3/26"].net_profit == pytest.approx(157.2)
    assert len(spec.warnings) == before


def test_a_consensus_without_the_actual_year_changes_nothing():
    spec = extract_module.skeleton(
        extract_module.Extraction(facts(), "doc-5803"), "5803.T", "Fujikura", price=5000.0
    )
    spec.actuals["FY3/26"] = Financials(net_profit=162.0)
    consensus = Consensus("Bloomberg", periods={"FY3/27": {"net_profit": 331.42}})
    extract_module.merge_consensus_actuals(spec, consensus)
    assert spec.actuals["FY3/26"].net_profit == pytest.approx(162.0)


def test_skeleton_lists_what_is_still_missing():
    spec = extract_module.skeleton(
        extract_module.Extraction(facts(), "doc-5803"), "5803.T", "Fujikura", price=5000.0
    )
    assert spec.quarterly_plan is not None
    assert spec.quarterly_plan["h1_label"] == "H1 FY3/27"
    assert spec.bridges == [] and "Draft spec" in " ".join(spec.warnings)
    assert {p.label for p in spec.periods} == {"Q1 FY3/27", "H1 FY3/27", "FY3/27"}
