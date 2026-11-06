"""The seven deterministic-control regressions required before another run."""

from __future__ import annotations

import json

from tradingagents.agents.schemas import (
    DecisionTrigger,
    HolderAction,
    NewInvestorAction,
    PortfolioDecision,
    PortfolioRating,
    ResearchPlan,
    TriggerEvidence,
    render_pm_decision,
    render_research_plan,
)
from tradingagents.reporting import _catalysts_components
from tradingagents.research import drivers, issues, resolved_status, segment_evidence
from tradingagents.research.schemas import Bridge, Line

from .test_research_controls import spec

# --- Test 1: reused legacy specification ---------------------------------------


def test_a_reused_spec_without_segment_metadata_is_revalidated():
    model = spec()
    model.segments = {}
    evidence = segment_evidence.SegmentEvidence(
        facts=[
            segment_evidence.SegmentFact("Electronics", "H1 FY3/2027", 172.5, 50.0),
            segment_evidence.SegmentFact("Ceramics", "H1 FY3/2027", 46.2, 5.4),
        ],
        disclosure_status=segment_evidence.SEGMENT_DATA_DISCLOSED,
        source=segment_evidence.STRUCTURED,
    )

    result = segment_evidence.audit(model, evidence)

    assert result.status != segment_evidence.NOT_REQUIRED
    assert result.status in {
        segment_evidence.REQUIRED_BUT_MISSING,
        segment_evidence.REQUIRED_BUT_INSUFFICIENT_DATA,
    }
    limitation = segment_evidence.limitation(result)
    assert "SEGMENT_RECONCILIATION_STATUS = INCOMPLETE" in limitation
    assert "classified as top-down" in limitation


def test_an_empty_block_is_ambiguous_without_a_reason_code():
    model = spec()
    empty = segment_evidence.SegmentEvidence(
        facts=[], disclosure_status="", source=segment_evidence.STRUCTURED
    )
    no_disclosure = segment_evidence.SegmentEvidence(
        facts=[],
        disclosure_status=segment_evidence.NO_SEGMENT_DISCLOSURE,
        source=segment_evidence.STRUCTURED,
    )

    assert segment_evidence.audit(model, empty).status == (
        segment_evidence.REQUIRED_BUT_INSUFFICIENT_DATA
    )
    assert segment_evidence.audit(model, no_disclosure).status == (
        segment_evidence.NOT_REQUIRED
    )


def test_a_missing_segment_block_is_a_schema_failure():
    evidence = segment_evidence.extract("Narrative only, no block.")

    assert evidence.schema_missing is True
    assert any(segment_evidence.SCHEMA_MISSING in finding for finding in evidence.findings)


def test_a_missing_segment_block_blocks_publication():
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    status, reasons, _qualifications = graph._publication_status({
        "model_status": "VERIFIED",
        "segment_evidence_schema_missing": True,
    })

    assert status == "BLOCKED"
    assert any("SEGMENT_EVIDENCE_SCHEMA_MISSING" in reason for reason in reasons)


# --- Test 2: zero-challenge run ------------------------------------------------


def test_deterministic_driver_issues_are_collected_without_an_llm():
    model = spec()
    model.bridges[1].lines = [
        Line("A driver with no contract", 2.0, driver="A driver with no contract")
    ]

    found = issues.collect(model)

    assert any(
        issue.kind == "driver_metadata" and "INCOMPLETE_DRIVER" in issue.detail
        for issue in found
    )
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {}
    updates, deterministic = graph._revalidate_current_evidence({}, model, "Report.")
    assert updates["deterministic_checks_executed"] is True
    assert any("INCOMPLETE_DRIVER" in issue.detail for issue in deterministic)


# --- Test 3: driver aliases ----------------------------------------------------


def test_one_economic_driver_has_one_identity_and_no_duplicate_result():
    model = spec()
    model.bridges[0].lines = [
        Line(
            "AI substrate volume", 10.0, driver="AI substrate volume",
            economic_driver_id="ai_substrate_op_contribution",
            model_stage="revenue", segment="Electronics", basis="revenue",
            propagation_ref="revenue-to-profit",
            alternative=8.0,
        )
    ]
    model.bridges[1].lines = [
        Line(
            "AI substrate OP contribution", 2.0,
            driver="AI substrate OP contribution",
            economic_driver_id="ai_substrate_op_contribution",
            model_stage="operating_profit", segment="Electronics", basis="cost",
            propagation_ref="revenue-to-profit",
            alternative=1.5,
        )
    ]

    rows = drivers.records(model)
    assert {row["economic_driver_id"] for row in rows} == {"ai_substrate_op_contribution"}
    assert drivers.overlaps(model) == []
    from tradingagents.research import sensitivity

    items, gaps = sensitivity.rank(model, top=5, eps_verified=True, pe_verified=True)
    same = [
        item for item in items
        if item.economic_driver_id == "ai_substrate_op_contribution"
    ]
    assert same
    verified = [item for item in same if item.verification_status == "VERIFIED"]
    if verified:
        assert all("ai_substrate_op_contribution" not in gap for gap in gaps)


# --- Test 4: punctuation-safe triggers -----------------------------------------


def _trigger(**overrides):
    data = {
        "kind": "THESIS_INVALIDATION_TRIGGER",
        "trigger_type": "THESIS_INVALIDATION_TRIGGER",
        "description": "Margin breaks the structural thesis",
        "evidence": [
            TriggerEvidence(
                claim="H1 margin was 29.0% (segment; company filing)",
                source_ids=["doc-1 p.12", "doc-2 p.3"],
            )
        ],
        "materiality": "high",
        "thesis_link": "pricing power",
    }
    data.update(overrides)
    return DecisionTrigger(**data)


def test_a_trigger_renders_one_bullet_with_nested_evidence():
    plan = ResearchPlan(
        recommendation=PortfolioRating.HOLD,
        rationale="r",
        strategic_actions="a",
        thesis_invalidation_triggers=[_trigger()],
    )

    text = render_research_plan(plan)

    assert text.count("- Margin breaks the structural thesis") == 1
    assert "  - H1 margin was 29.0%" in text
    assert "doc-1 p.12, doc-2 p.3" in text
    assert "H1 margin was 29.0% (segment; company filing);" not in text


def test_empty_trigger_sections_are_omitted_from_the_main_report():
    state = {
        "decision_triggers": json.dumps({
            "forecast_revision": [],
            "rating_review": [],
            "thesis_invalidation": [_trigger().model_dump()],
        }),
        "final_trade_decision": "",
    }
    pm = "**Rating**: Hold"
    components = _catalysts_components(pm, state)

    assert any("What would invalidate the view" in item for item in components)
    assert not any("What would revise the forecast" in item for item in components)
    assert "Legacy thesis invalidation" not in "\n".join(components)


# --- Test 5: EV basis conflict -------------------------------------------------


def test_ev_arithmetic_can_be_verified_while_the_per_share_basis_is_not():
    result = resolved_status.resolve(
        statuses={
            "valuation_method_statuses": {"ev_ebitda": "VERIFIED"},
            "ev_per_share_status": "VERIFIED",
        },
        share_mismatch=True,
        convertible_unresolved=True,
    )

    assert result.ev_calculation_status == "VERIFIED"
    assert result.equity_bridge_status == "PROVISIONAL"
    assert result.per_share_basis_status == "SHARE_COUNT_MISMATCH"
    assert result.per_share_comparison_status == "UNVERIFIED"
    assert result.decision_use == "CROSS_CHECK_ONLY"
    assert "EV_COMPARISON_VERIFIED_WITH_SHARE_MISMATCH" in result.contradictions


# --- Test 6: legacy suppression ------------------------------------------------


def test_legacy_thesis_invalidation_is_not_rendered():
    decision = PortfolioDecision.model_construct(
        rating=PortfolioRating.HOLD,
        executive_summary="summary",
        investment_thesis="thesis",
        thesis_invalidation=["an old free-text invalidation"],
        thesis_invalidation_triggers=[_trigger()],
        forecast_revision_triggers=[],
        rating_review_triggers=[],
        new_investor_action=NewInvestorAction.WAIT,
        existing_holder_action=HolderAction.HOLD,
        downgrade_applicability="NOT_ASSESSED",
    )

    text = render_pm_decision(decision)

    assert "Legacy thesis invalidation" not in text
    assert "an old free-text invalidation" not in text
    assert "Margin breaks the structural thesis" in text


# --- Test 7: bridge arithmetic -------------------------------------------------


def test_bridge_arithmetic_is_a_structured_object():
    bridge = Bridge(
        "operating_profit", "FY3/27", "FY3/28", 133.0,
        [
            Line("volume", 60.0), Line("price", -12.0), Line("ceramics", -5.0),
            Line("cost", -8.0), Line("leverage", 10.0),
        ],
        declared_total=178.0,
    )

    arithmetic = bridge.arithmetic

    assert arithmetic.calculated_total == 178.0
    assert arithmetic.declared_total == 178.0
    assert arithmetic.difference == 0.0
    assert arithmetic.reconciliation_status == "VERIFIED"
    assert arithmetic.to_dict()["components"] == [60.0, -12.0, -5.0, -8.0, 10.0]
