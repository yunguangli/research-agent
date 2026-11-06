"""Phase 2/3 contracts: status enum, convertible modes, parsers."""

from __future__ import annotations

import pytest

from tradingagents.agents.researchers import delta
from tradingagents.agents.resolution import (
    contradiction_detected,
    entry_price,
    parse_pm_resolution,
)
from tradingagents.research import capital, evidence, kernel, status, valuation
from tradingagents.research.schemas import Convertible

from .test_capital_and_ev import spec


def _two_basis_spec(**overrides):
    model = spec()
    model.share_bases = [
        type(model.share_bases[0])("period_end_net", 566.3),
        type(model.share_bases[0])("diluted_average", 592.5, diluted_weighted_average=592.5),
    ]
    model.valuation.equity_value_share_basis = "period_end_net"
    model.valuation.forecast_eps_basis = "diluted_average"
    for key, value in overrides.items():
        setattr(model.valuation, key, value)
    return model


def test_a_convertible_written_before_the_fields_loads_as_unknown():
    item = Convertible.from_dict({"name": "CB", "shares_m": 30.5})

    assert item.valuation_treatment == "unknown"
    assert item.face_value is None


def test_valuation_modes_read_the_treatment():
    model = spec()
    model.capital_structure.convertibles[0].valuation_treatment = "debt"
    assert capital.valuation_mode(model) == "debt"

    model.capital_structure.convertibles[0].valuation_treatment = "assumed_converted"
    assert capital.valuation_mode(model) == "assumed_converted"

    model.capital_structure.convertibles[0].valuation_treatment = "unknown"
    assert capital.valuation_mode(model) == "unknown"


def test_cross_method_basis_reports_the_share_gap():
    model = _two_basis_spec()
    model.capital_structure.convertibles = [Convertible(name="CB", shares_m=30.5)]

    ev_count, pe_count, gap = capital.cross_method_basis(model)

    assert ev_count == pytest.approx(566.3)
    assert pe_count == pytest.approx(592.5)
    assert gap == pytest.approx((592.5 - 566.3) / 592.5)


def test_a_share_gap_marks_the_cross_method_comparison_unverified(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_FEATURE_CAPITAL_MODES_V1", "1")
    model = _two_basis_spec()
    model.capital_structure.convertibles = [Convertible(name="CB", shares_m=30.5)]

    value = valuation.value(kernel.run(model))

    assert value.cross_method_comparable is False
    assert value.cross_method_share_gap > 0.01


def test_a_debt_classified_convertible_is_added_to_net_debt(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_FEATURE_CAPITAL_MODES_V1", "1")
    model = _two_basis_spec()
    model.valuation.ev_ebitda_multiple = 10.0
    model.capital_structure.convertibles = [
        Convertible(name="CB", shares_m=30.5, face_value=56.0, valuation_treatment="debt")
    ]

    base_model = spec()
    base_model.valuation.ev_ebitda_multiple = 10.0
    base_model.capital_structure.convertibles = []
    without = valuation.value(kernel.run(base_model))
    with_debt = valuation.value(kernel.run(model))

    assert with_debt.enterprise_value == pytest.approx(without.enterprise_value)
    assert with_debt.ev_equity_value < without.ev_equity_value


def test_a_failed_propagation_makes_the_sensitivity_component_unverified():
    result = status.assess(
        model_status="VERIFIED",
        basis={
            "market_cap_basis": "period_end_net",
            "forecast_eps_basis": "diluted_average",
            "dilution_sensitivity_basis": "diluted_average",
        },
        has_band=True,
        price=100.0,
        sensitivity_count=3,
        propagation_ok=False,
    )

    assert result["sensitivity_model_status"] == "UNVERIFIED"


def test_the_shared_component_status_reaches_the_report_header(tmp_path):
    import json

    from tradingagents.reporting import write_report_tree

    state = {
        "trade_date": "2026-11-05",
        "component_status": json.dumps({
            "ev_ebitda_method": {"status": "VERIFIED"},
            "ev_per_share_comparison": {
                "status": "UNVERIFIED",
                "reason": "EV per-share uses 566.3m; P/E uses 592.5m",
            },
            "controlling_valuation": {"use": "pe only", "ev_controlling": False},
            "sensitivity": {"status": "UNVERIFIED", "failed_rows": 7},
            "controlling_sensitivity": {"status": "VERIFIED", "verified": 3, "expected": 3},
            "decision_use": {"failed_rows_excluded": True},
            "decision": {"provisional_reason": "diagnostic run purpose"},
        }),
        "final_trade_decision": "**Rating**: Hold\n\n**Executive Summary**: No change.",
    }
    write_report_tree(state, "TEST", tmp_path, html=False)
    main = (tmp_path / "complete_report.md").read_text(encoding="utf-8")
    appendix = (tmp_path / "appendix.md").read_text(encoding="utf-8")

    assert "EV/EBITDA enterprise-value method: VERIFIED" in main
    assert "EV/EBITDA per-share comparison: UNVERIFIED" in main
    assert "Controlling valuation use: pe only (EV/EBITDA is a cross-check)" in main
    assert (
        "Reason: 7 published row(s) failed downstream propagation "
        "(non-controlling rows; controlling candidates verified)" in main
    )
    assert "Controlling sensitivities: VERIFIED, 3 of 3 controlling candidates verified" in main
    assert "Decision-use exclusion: failed rows excluded from decision evidence" in main
    assert "Provisional reason: diagnostic run purpose" in main
    assert "## Component Status" in appendix
    assert "| sensitivity | UNVERIFIED | failed_rows=7 |" in appendix
    assert "| controlling_sensitivity | VERIFIED | 3 of 3 verified |" in appendix


def test_debt_treatment_with_a_diluted_denominator_is_a_conflict():
    model = _two_basis_spec()
    model.capital_structure.convertibles[0].valuation_treatment = "debt"
    model.valuation.equity_value_share_basis = "diluted_average"

    assert capital.treatment_conflicts(model) == [
        "debt treatment uses the diluted denominator"
    ]


def test_assumed_conversion_with_a_basic_denominator_is_a_conflict():
    model = _two_basis_spec()
    model.capital_structure.convertibles[0].valuation_treatment = "assumed_converted"

    assert capital.treatment_conflicts(model) == [
        "assumed conversion does not use the diluted denominator"
    ]


def test_a_consistent_debt_mode_has_no_conflict():
    model = _two_basis_spec()
    model.capital_structure.convertibles[0].valuation_treatment = "debt"

    assert capital.treatment_conflicts(model) == []


def test_unknown_treatment_is_not_a_treatment_conflict():
    model = _two_basis_spec()
    model.capital_structure.convertibles[0].valuation_treatment = "unknown"

    assert capital.treatment_conflicts(model) == []


def test_broker_summary_is_generated_from_structured_ids():
    text = (
        "Coverage [fundamental|source_id=GS|source_id=Citi|source_id=SMBC|source_id=JPM]."
    )

    summary = evidence.broker_summary(text)

    assert summary["count"] == 4
    assert summary["brokers"] == ["GS", "Citi", "SMBC", "JPM"]


def test_the_header_renders_the_broker_count_only_when_the_flag_is_on(tmp_path, monkeypatch):
    from tradingagents.reporting import write_report_tree

    state = {
        "trade_date": "2026-11-05",
        "fundamentals_report": (
            "Coverage [fundamental|source_id=GS|source_id=Citi|source_id=SMBC|source_id=JPM]."
        ),
        "final_trade_decision": "**Rating**: Hold\n\n**Executive Summary**: No change.",
    }
    write_report_tree(state, "TEST", tmp_path / "off", html=False)
    monkeypatch.setenv("TRADINGAGENTS_FEATURE_EVIDENCE_ATTRIBUTES_V1", "1")
    write_report_tree(state, "TEST", tmp_path / "on", html=False)

    off = (tmp_path / "off" / "complete_report.md").read_text(encoding="utf-8")
    on = (tmp_path / "on" / "complete_report.md").read_text(encoding="utf-8")

    assert "Broker sources:" not in off
    assert "Broker sources: 4 (GS, Citi, SMBC, JPM)" in on


def test_an_unavailable_ev_method_marks_the_comparison_unavailable(tmp_path):
    import json

    from tradingagents.reporting import write_report_tree

    state = {
        "trade_date": "2026-11-05",
        "component_status": json.dumps({
            "ev_ebitda_method": {"status": "UNAVAILABLE"},
            "ev_per_share_comparison": {"status": "UNAVAILABLE",
                                        "reason": "EV/EBITDA method unavailable"},
            "controlling_valuation": {"use": "pe only", "ev_controlling": False},
        }),
        "final_trade_decision": "**Rating**: Hold\n\n**Executive Summary**: No change.",
    }
    write_report_tree(state, "TEST", tmp_path, html=False)
    main = (tmp_path / "complete_report.md").read_text(encoding="utf-8")

    assert "EV/EBITDA enterprise-value method: UNAVAILABLE" in main
    assert "EV/EBITDA per-share comparison: UNAVAILABLE" in main
    assert "EV/EBITDA: PROVISIONAL" not in main


def test_the_technical_summary_keeps_the_limitation_under_budget_pressure(tmp_path):
    from tradingagents.reporting import write_report_tree

    long_sentence = " ".join(["word"] * 55) + "."
    market = "\n\n".join([
        f"## 1. Technical conclusion\nTrend says {long_sentence} 200 SMA holds.",
        "## 2. Evidence\nRSI is 30 and MACD histogram is -236. ATR is 10% of price, a gap risk.",
        "## 3. Decision levels\n| Zone | Basis |\n|---|---|\n| 7,700 | 200 SMA |",
        "## 4. Conditional outlook\nA close above 8,850 establishes repair; below 7,700 invalidates.",
        "## 5. Investment implication\nTiming is unfavorable for adding, so wait. "
        "Data limitations: the split adjustment basis is unresolved.",
    ])
    state = {
        "trade_date": "2026-11-05",
        "market_report": market,
        "final_trade_decision": "**Rating**: Hold\n\n**Executive Summary**: No change.",
    }
    write_report_tree(state, "TEST", tmp_path, html=False)
    main = (tmp_path / "complete_report.md").read_text(encoding="utf-8")
    tech = main.split("## Technical Summary", 1)[1].split("\n## ", 1)[0]

    assert "**Material data limitation**" in tech
    assert len(tech.split()) <= 320


def test_four_houses_need_four_source_ids():
    text = (
        "Four houses cover it [fundamental|source_id=gs|source_id=citi|source_id=smbc]."
    )

    findings = evidence.attribute_findings(text)

    assert any("four houses" in item.lower() for item in findings)


def test_consecutive_duplicate_paragraphs_are_collapsed():
    from tradingagents.agents.context import dedupe_consecutive_paragraphs

    text = "First argument.\n\nSame point.\n\nSame point.\n\nSecond argument."
    cleaned, dropped = dedupe_consecutive_paragraphs(text)

    assert cleaned == "First argument.\n\nSame point.\n\nSecond argument."
    assert dropped == 1


def test_the_status_enum_orders_invalid_worst():
    assert status.worst_status(["VERIFIED", "UNVERIFIED"]) == "UNVERIFIED"
    assert status.worst_status(["VERIFIED", "INVALID"]) == "INVALID"
    assert status.worst_status(["SUPPRESSED", "UNAVAILABLE"]) == "SUPPRESSED"
    assert status.worst_status([]) == status.NOT_APPLICABLE


def test_the_delta_parser_requires_all_six_fields():
    text = (
        "POSITION CHANGE:\nHold, not Reduce.\n\n"
        "STRONGEST UNRESOLVED OBJECTION:\nCell 8 timing.\n\n"
        "DIRECT ANSWER:\nThe timing gap is real.\n\n"
        "CONCESSION:\nnone\n\n"
        "NEW EVIDENCE OR CALCULATION:\nNone beyond the print.\n\n"
        "DECISION IMPACT:\nHold.\n"
    )
    parsed = delta.parse_delta(text)

    assert parsed["position_change"].startswith("Hold")
    assert parsed["concession"] == "none"
    assert delta.missing_fields(parsed) == []
    assert delta.within_limit(text)
    assert delta.delta_contract_ok(text)


def test_the_delta_parser_names_a_missing_field():
    parsed = delta.parse_delta("POSITION CHANGE:\nHold.\n")

    assert "decision_impact" in delta.missing_fields(parsed)
    assert not delta.delta_contract_ok("POSITION CHANGE:\nHold.\n")


def test_no_material_update_ends_the_delta_without_fields():
    assert delta.delta_contract_ok("NO MATERIAL UPDATE")


def test_evidence_attributes_are_checked_on_the_existing_tags():
    text = (
        "Cell 8 adds 40 of operating profit [fundamental|source_type=model_assumption], "
        "reported by the company.\n\n"
        "The yield is 70% [technical|source_type=read_through].\n\n"
        "The claim supports a buy [valuation|source_type=unsupported|supports_action=true].\n\n"
        "All three brokers agree [fundamental|source_id=doc-1].\n"
    )
    findings = evidence.attribute_findings(text)

    assert any("model assumption" in item for item in findings)
    assert any("read-through" in item for item in findings)
    assert any("unsupported" in item for item in findings)
    assert any("three brokers" in item for item in findings)


def test_an_honest_evidence_set_has_no_findings():
    text = (
        "Cell 8 adds 40 [fundamental|source_type=model_assumption].\n"
        "The yield is 70% [technical|source_type=read_through|comparable=peer line|"
        "limitation=not company-disclosed].\n"
        "Two brokers cite 190 [fundamental|source_id=doc-1|source_id=doc-2].\n"
    )

    assert evidence.attribute_findings(text) == []


def test_the_pm_resolution_parser_reads_the_fields():
    text = (
        "**Controlling price**: 6,812\n\n"
        "**Controlling valuation**: P/E band\n\n"
        "**Entry condition**: entry at 7,811\n\n"
        "**Resolution**: the break confirms demand.\n"
    )
    parsed = parse_pm_resolution(text)

    assert parsed["controlling_price"] == "6,812"
    assert parsed["entry_condition"].startswith("entry at")
    assert entry_price(parsed["entry_condition"]) == pytest.approx(7811.0)
    assert contradiction_detected(7811.0, 7116.0) is True
    assert contradiction_detected(6900.0, 7116.0) is False
