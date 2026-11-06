"""Blocked-publication clearing and the one-shot SEGMENT_EVIDENCE correction.

Two controls are pinned here. A blocked run must clear every decision output
*before* DECISION_SAFETY_CHECK runs, so the safe fallback passes because it
worked, not because a stale fair value happened to be ignored. And the segment
block, when absent or malformed, gets exactly one tool-free correction attempt;
the narrative is never regenerated, and a still-invalid block blocks publication.
"""

from __future__ import annotations

import json
import os

from langchain_core.messages import AIMessage

from tradingagents.agents.analysts.fundamentals_analyst import ensure_segment_evidence
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.reporting import _header, write_report_tree
from tradingagents.research import segment_evidence

from .test_research_controls import spec

FIXTURE = os.path.abspath("tests/fixtures/4062.T/spec.json")
CORPUS = os.path.dirname(FIXTURE)

VALID_BLOCK = (
    "SEGMENT_EVIDENCE:\n"
    '{"segment_evidence": [{"segment_name": "Electronics", "period": "H1 FY3/2027", '
    '"revenue": 172540, "operating_profit": 50028, "currency": "JPY", '
    '"source_ids": ["doc-1"], "evidence_status": "REPORTED"}], '
    '"segment_disclosure_status": "SEGMENT_DATA_DISCLOSED"}'
)


class _Answer:
    def __init__(self, text: str):
        self.text = text
        self.calls = 0

    def invoke(self, _messages):
        self.calls += 1
        return AIMessage(content=self.text)


def _blocked_state(**overrides):
    state = {
        "trade_date": "2026-11-06",
        "final_trade_decision": "**Rating**: Hold\n\n**Executive Summary**: stale.",
        "final_rating": "Hold",
        "decision_status": "FINAL",
        "decision_integrity": "validated",
        "controlling_fair_value_low": 10.0,
        "controlling_fair_value_base": 15.0,
        "controlling_fair_value_high": 20.0,
        "controlling_fair_value_method": "pe_model_band_v1",
        "price_authority_conflict": "two controlling prices disagree",
    }
    state.update(overrides)
    return state


def _graph():
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {}
    return graph


# --- Blocked publication -------------------------------------------------------


def test_a_blocked_report_clears_every_decision_output_before_safety():
    out = _graph().prepare_final_state(_blocked_state(), "TEST")

    assert out["publication_status"] == "BLOCKED"
    assert out["final_rating"] == "Not Rated"
    assert out["decision_status"] == "NOT_RATED"
    assert out["provisional_decision"] == "HOLD"
    assert out["published_decision"] == "NOT_RATED"
    assert out["controlling_fair_value_low"] is None
    assert out["controlling_fair_value_base"] is None
    assert out["controlling_fair_value_high"] is None
    assert out["controlling_fair_value_method"] is None
    assert out["publication_fair_value_status"] == "NOT_PUBLISHED"
    assert str(out["decision_integrity"]).startswith("DEGRADED")
    assert out["production_decision_allowed"] is False
    # The safety check passes because the fail-closed clearing worked.
    assert out["decision_safety_check"] == "PASS"
    assert out["decision_process_check"] == "DEGRADED"


def test_a_checkpoint_already_marked_blocked_stays_blocked_and_safe():
    state = _blocked_state()
    state.pop("price_authority_conflict")
    state["publication_status"] = "BLOCKED"

    out = _graph().prepare_final_state(state, "TEST")
    header = _header("TEST", out, None)

    assert out["publication_status"] == "BLOCKED"
    assert out["publication_reasons"] == [
        "publication was blocked at an earlier checkpoint"
    ]
    assert out["decision_safety_check"] == "PASS"
    assert out["decision_status"] == "NOT_RATED"
    assert out["controlling_fair_value_high"] is None
    assert "PUBLICATION_STATUS: BLOCKED" in header
    assert "FAIR_VALUE: NOT_PUBLISHED" in header
    assert "DECISION_SAFETY_CHECK: PASS" in header
    assert "DECISION_PROCESS_CHECK: DEGRADED" in header
    assert "PRODUCTION_DECISION_ALLOWED: FALSE" in header
    assert "Rating: Not Rated (publication blocked)" in header


# --- SEGMENT_EVIDENCE retry ----------------------------------------------------


def test_an_absent_block_is_retried_once_and_the_narrative_is_preserved():
    llm = _Answer(VALID_BLOCK)

    evidence, retry = ensure_segment_evidence(llm, "Narrative only.", enabled=True)

    assert llm.calls == 1
    assert retry["attempted"] is True
    assert retry["reason"] == "absent"
    assert retry["valid_after_retry"] is True
    assert evidence.source == segment_evidence.STRUCTURED
    assert len(evidence.facts) == 1
    assert evidence.schema_missing is False
    assert evidence.clean_text == "Narrative only."


def test_a_malformed_block_is_retried_once():
    llm = _Answer(VALID_BLOCK)
    report = "Narrative.\n\nSEGMENT_EVIDENCE:\n{not valid json"

    evidence, retry = ensure_segment_evidence(llm, report, enabled=True)

    assert llm.calls == 1
    assert retry["valid_after_retry"] is True
    assert evidence.schema_missing is False
    assert evidence.clean_text == "Narrative."


def test_a_bare_json_retry_answer_is_accepted():
    llm = _Answer(VALID_BLOCK.split("SEGMENT_EVIDENCE:\n", 1)[1])

    evidence, retry = ensure_segment_evidence(llm, "Narrative only.", enabled=True)

    assert retry["valid_after_retry"] is True
    assert len(evidence.facts) == 1


def test_a_still_invalid_retry_marks_the_block_schema_missing():
    llm = _Answer("Sorry, I cannot produce that block.")

    evidence, retry = ensure_segment_evidence(llm, "Narrative only.", enabled=True)

    assert llm.calls == 1
    assert retry["attempted"] is True
    assert retry["valid_after_retry"] is False
    assert evidence.schema_missing is True
    assert any(
        segment_evidence.SCHEMA_MISSING in finding for finding in evidence.findings
    )
    # The narrative is still the analyst's own text, not the retry answer.
    assert evidence.clean_text == "Narrative only."


def test_empty_no_disclosure_is_not_retried_and_is_not_required():
    report = (
        "Narrative.\nSEGMENT_EVIDENCE:\n"
        '{"segment_evidence": [], "segment_disclosure_status": "NO_SEGMENT_DISCLOSURE"}'
    )
    llm = _Answer("unused")

    evidence, retry = ensure_segment_evidence(llm, report, enabled=True)

    assert llm.calls == 0
    assert retry["attempted"] is False
    assert evidence.schema_missing is False
    assert segment_evidence.needs_retry(evidence) is False


def test_empty_incomplete_is_valid_but_insufficient_not_schema_missing():
    report = (
        "Narrative.\nSEGMENT_EVIDENCE:\n"
        '{"segment_evidence": [], "segment_disclosure_status": "SEGMENT_DATA_INCOMPLETE"}'
    )
    llm = _Answer("unused")

    evidence, retry = ensure_segment_evidence(llm, report, enabled=True)

    assert llm.calls == 0
    assert retry["attempted"] is False
    assert evidence.schema_missing is False

    from tradingagents.research.report import load_spec

    result = segment_evidence.audit(load_spec(FIXTURE), evidence)
    assert result.status == segment_evidence.REQUIRED_BUT_INSUFFICIENT_DATA
    assert result.schema_missing is False
    assert "classified as top-down" in segment_evidence.limitation(result)


def test_empty_without_a_reason_is_ambiguous_and_retried():
    report = (
        "Narrative.\nSEGMENT_EVIDENCE:\n"
        '{"segment_evidence": [], "segment_disclosure_status": ""}'
    )
    llm = _Answer("unused")

    evidence, retry = ensure_segment_evidence(llm, report, enabled=True)

    assert llm.calls == 1
    assert retry["attempted"] is True
    assert evidence.schema_missing is True


def test_a_legacy_markdown_table_cannot_support_publication():
    report = (
        "| Segment | Revenue | Operating profit |\n"
        "|---|---|---|\n"
        "| A | 100 | 30 |\n"
        "| B | 100 | 30 |\n"
    )

    evidence = segment_evidence.extract(report)

    assert evidence.source == segment_evidence.LEGACY_MARKDOWN_FALLBACK
    assert evidence.schema_missing is True
    from tradingagents.research.report import load_spec

    result = segment_evidence.audit(load_spec(FIXTURE), evidence)
    assert result.status == segment_evidence.REQUIRED_BUT_INSUFFICIENT_DATA
    assert result.schema_missing is True


# --- No Fundamentals Analyst ---------------------------------------------------


def test_no_fundamentals_analyst_blocks_publication_fail_closed(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_RESEARCH_DIR", CORPUS)
    graph = _graph()
    graph._research_model = lambda *a, **k: None
    state = {
        "company_of_interest": "4062.T",
        "trade_date": "2026-11-06",
        "research_model_path": FIXTURE,
        "active_model_version": "v1",
        "research_model_source": "reused",
        "report_audit": "not run",
        "fundamentals_report": "",
        "research_package": "The bottom-up build.",
        "final_trade_decision": "**Rating**: Hold",
        "final_rating": "Hold",
        "controlling_fair_value_low": 100.0,
        "controlling_fair_value_high": 200.0,
    }

    gate = graph.run_repair_gate(state)
    assert gate["segment_reconciliation_status"] == segment_evidence.INCOMPLETE
    assert gate["segment_build_type"] == segment_evidence.TOP_DOWN
    assert gate["bottom_up_claim_allowed"] is False
    assert gate["operating_model_confidence"] == "DEGRADED"
    assert gate["segment_evidence_schema_missing"] is True
    assert gate["segment_evidence_retry"]
    assert "no Fundamentals report" in gate["segment_evidence_retry"]

    merged = {**state, **gate}
    publication, reasons, _qualifications = graph._publication_status(merged)
    assert publication == "BLOCKED"
    assert "SEGMENT_EVIDENCE_SCHEMA_MISSING" in reasons

    final = graph.prepare_final_state(merged, "4062.T")
    assert final["publication_status"] == "BLOCKED"
    assert final["final_rating"] == "Not Rated"
    assert final["decision_status"] == "NOT_RATED"
    assert final["decision_safety_check"] == "PASS"
    assert final["production_decision_allowed"] is False


# --- Package and appendix surfaces ---------------------------------------------


def test_the_package_receives_the_segment_limitation_and_loses_bottom_up():
    graph = _graph()
    evidence = segment_evidence.SegmentEvidence(
        facts=[
            segment_evidence.SegmentFact(
                "Electronics", "H1 FY3/2027", 172540.0, 50028.0, "JPY", ["doc-1"]
            ),
            segment_evidence.SegmentFact(
                "Ceramics", "H1 FY3/2027", 46200.0, 5400.0, "JPY", ["doc-1"]
            ),
        ],
        disclosure_status=segment_evidence.SEGMENT_DATA_DISCLOSED,
        source=segment_evidence.STRUCTURED,
    )
    state = {
        "segment_evidence": json.dumps(evidence.to_dict()),
        "research_package": "The bottom-up, segment-reconciled build.",
        "research_package_artifact": "The bottom-up, segment-reconciled build.",
        "valuation_method_statuses": "{}",
    }
    from tradingagents.research.report import load_spec

    updates, deterministic = graph._revalidate_current_evidence(
        state, load_spec(FIXTURE), "Narrative."
    )

    assert updates["segment_reconciliation_status"] == segment_evidence.INCOMPLETE
    assert updates["segment_build_type"] == segment_evidence.TOP_DOWN
    assert updates["bottom_up_claim_allowed"] is False
    assert updates["operating_model_confidence"] == "DEGRADED"
    assert "SEGMENT_RECONCILIATION_STATUS = INCOMPLETE" in updates["research_package"]
    assert "The bottom-up" not in updates["research_package"]
    assert updates["research_package"].count("segment-reconciled") == 1
    assert "The consolidated, consolidated build" in updates["research_package"]
    assert "classified as top-down" in updates["segment_limitation"]
    assert "Segment forecast (Electronics)" in updates["research_package"]
    assert "| Period | Revenue | Operating profit | Margin |" in updates["research_package"]
    assert updates["segment_limitation"] in updates["research_package_artifact"]
    assert updates["deterministic_checks_executed"] is True
    assert deterministic, "the reused spec's driver issues must still be collected"


def test_the_fundamentals_subgraph_returns_its_segment_channels():
    from tradingagents.graph.analyst_execution import ANALYST_NODE_SPECS
    from tradingagents.graph.setup import _analyst_graph

    spec = ANALYST_NODE_SPECS["fundamentals"]

    def agent(_state):
        return {
            "fundamentals_report": "report",
            "segment_evidence": '{"facts": []}',
            "segment_evidence_source": "STRUCTURED",
            "segment_evidence_schema_missing": False,
            "segment_evidence_retry": '{"attempted": true}',
        }

    output = _analyst_graph(spec, agent, 1, tools=[]).invoke({"messages": []})

    # Without these channels the reviewer re-parses the narrative, where only
    # the legacy table remains, and a valid structured block is lost.
    assert output["segment_evidence"] == '{"facts": []}'
    assert output["segment_evidence_source"] == "STRUCTURED"
    assert output["segment_evidence_schema_missing"] is False
    assert output["segment_evidence_retry"] == '{"attempted": true}'


def test_revalidating_twice_does_not_stack_the_segment_limitation():
    graph = _graph()
    evidence = segment_evidence.SegmentEvidence(
        facts=[
            segment_evidence.SegmentFact(
                "Electronics", "H1 FY3/2027", 172540.0, 50028.0, "JPY", ["doc-1"]
            ),
            segment_evidence.SegmentFact(
                "Ceramics", "H1 FY3/2027", 46156.0, 5391.0, "JPY", ["doc-1"]
            ),
        ],
        disclosure_status=segment_evidence.SEGMENT_DATA_DISCLOSED,
        source=segment_evidence.STRUCTURED,
    )
    state = {
        "segment_evidence": json.dumps(evidence.to_dict()),
        "research_package": "The bottom-up, segment-reconciled build.",
        "research_package_artifact": "The bottom-up, segment-reconciled build.",
        "valuation_method_statuses": "{}",
    }
    from tradingagents.research.report import load_spec

    first, _ = graph._revalidate_current_evidence(
        state, load_spec(FIXTURE), "Narrative."
    )
    second, _ = graph._revalidate_current_evidence(
        {**state, **first}, load_spec(FIXTURE), "Narrative."
    )

    text = second["research_package"]
    assert text.count("SEGMENT_RECONCILIATION_STATUS = ") == 1
    assert "not a consolidated build" not in text
    assert "classified as top-down" in text
    assert text.count("classified as top-down") == 1


def test_incomplete_segments_build_is_qualified_not_blocked():
    from tradingagents.research.report import load_spec

    graph = _graph()
    evidence = segment_evidence.SegmentEvidence(
        facts=[
            segment_evidence.SegmentFact(
                "Electronics", "H1 FY3/2027", 172540.0, 50028.0, "JPY", ["doc-1"]
            ),
            segment_evidence.SegmentFact(
                "Ceramics", "H1 FY3/2027", 46156.0, 5391.0, "JPY", ["doc-1"]
            ),
        ],
        disclosure_status=segment_evidence.SEGMENT_DATA_DISCLOSED,
        source=segment_evidence.STRUCTURED,
    )
    state = {
        "segment_evidence": json.dumps(evidence.to_dict()),
        "research_package": "The bottom-up build.",
        "research_package_artifact": "The bottom-up build.",
        "valuation_method_statuses": "{}",
        "model_status": "VERIFIED",
        "selection": "reused",
    }
    spec = load_spec(FIXTURE)
    spec.segment_reconciliation_controlling = False

    updates, _ = graph._revalidate_current_evidence(state, spec, "Narrative.")
    merged = {**state, **updates}
    publication, reasons, _qualifications = graph._publication_status(merged)

    assert updates["segment_reconciliation_status"] == segment_evidence.INCOMPLETE
    assert updates["segment_build_type"] == segment_evidence.TOP_DOWN
    assert updates["bottom_up_claim_allowed"] is False
    assert updates["operating_model_confidence"] == "DEGRADED"
    assert updates["segment_reconciliation_controlling"] is False
    assert publication == "OK"
    assert "SEGMENT_RECONCILIATION_INCOMPLETE_CONTROLLING" not in reasons

    spec.segment_reconciliation_controlling = True
    updates, _ = graph._revalidate_current_evidence(state, spec, "Narrative.")
    merged = {**state, **updates}
    publication, reasons, _qualifications = graph._publication_status(merged)

    assert updates["segment_reconciliation_controlling"] is True
    assert publication == "BLOCKED"
    assert "SEGMENT_RECONCILIATION_INCOMPLETE_CONTROLLING" in reasons


def test_a_complete_segment_build_is_bottom_up_and_verified():
    from tradingagents.research import segment_evidence as se

    complete = se.SegmentAudit(
        se.REQUIRED_AND_PRESENT, "segment forecasts present"
    )
    classification = se.classify(complete)

    assert classification.build_type == se.BOTTOM_UP
    assert classification.bottom_up_claim_allowed is True
    assert classification.operating_model_confidence == "VERIFIED"
    assert se.published_status(complete) == se.REQUIRED_AND_PRESENT


def test_the_state_declares_the_deterministic_and_decision_channels():
    from tradingagents.agents.state import AgentState

    for key in (
        "deterministic_issues",
        "deterministic_checks_executed",
        "provisional_decision",
        "published_decision",
        "segment_build_type",
        "bottom_up_claim_allowed",
        "operating_model_confidence",
        "model_node_sensitivity_status",
        "economic_driver_mapping_status",
        "failed_driver_mappings",
    ):
        assert key in AgentState.__annotations__, key


def test_a_verified_driver_is_not_also_reported_as_a_failed_mapping():
    from types import SimpleNamespace

    from tradingagents.research import sensitivity

    model = spec()
    verified = SimpleNamespace(
        verification_status="VERIFIED",
        economic_driver_id="ai_substrate_volume",
        driver_id="ai_substrate_volume",
        driver="AI substrate volume",
    )
    duplicate = (
        "SENSITIVITY_PROPAGATION_FAILED: AI substrate volume: "
        "the shock produced no effect"
    )
    other = "SENSITIVITY_PROPAGATION_FAILED: Ceramics automotive: no effect"

    assert sensitivity.filter_gaps(model, [verified], [duplicate]) == []
    assert sensitivity.filter_gaps(model, [verified], [other]) == [other]

    qualified = SimpleNamespace(
        verification_status="VERIFIED",
        economic_driver_id="cell_8_loading_utilisation",
        driver_id="cell_8_loading_utilisation",
        driver="Cell 8 loading / utilisation (operating-profit contribution)",
    )
    qualified_gap = (
        "SENSITIVITY_PROPAGATION_FAILED: Cell 8 loading "
        "(operating-profit contribution): the shock produced no effect"
    )
    assert sensitivity.filter_gaps(model, [qualified], [qualified_gap]) == []

    summary = sensitivity.mapping_summary(model, [verified], [])
    assert summary["failed_driver_mappings"] == 0
    assert summary["economic_driver_mapping_status"] == "COMPLETE"


def test_the_header_separates_model_node_and_driver_mapping_scopes():
    from tradingagents.reporting import _header

    state = {
        "trade_date": "2026-11-06",
        "final_trade_decision": "**Rating**: Hold",
        "final_rating": "Hold",
        "model_node_sensitivity_status": "VERIFIED",
        "economic_driver_mapping_status": "PARTIAL",
        "failed_driver_mappings": 9,
    }

    header = _header("TEST", state, None)

    assert "- MODEL_NODE_SENSITIVITY_STATUS: VERIFIED" in header
    assert "- ECONOMIC_DRIVER_MAPPING_STATUS: PARTIAL" in header
    assert "- FAILED_DRIVER_MAPPINGS: 9" in header
    assert "- Sensitivity model:" not in header


def _ev_cross_check_state(**overrides):
    state = {
        "trade_date": "2026-11-06",
        "final_trade_decision": "**Rating**: Hold",
        "final_rating": "Hold",
        "decision_status": "FINAL",
        "decision_integrity": "validated",
        "controlling_fair_value_low": 6_033.0,
        "controlling_fair_value_base": 6_837.0,
        "controlling_fair_value_high": 8_446.0,
        "controlling_valuation_method": "pe",
        "resolved_status": json.dumps({
            "ev_calculation_status": "VERIFIED",
            "equity_bridge_status": "PROVISIONAL",
            "per_share_basis_status": "SHARE_COUNT_MISMATCH",
            "per_share_comparison_status": "UNVERIFIED",
            "decision_use": "CROSS_CHECK_ONLY",
            "controlling_method": "pe",
            "ev_decision_use": "CROSS_CHECK_ONLY",
            "contradictions": ["EV_COMPARISON_VERIFIED_WITH_SHARE_MISMATCH"],
        }),
    }
    state.update(overrides)
    return state


def test_a_non_controlling_ev_cross_check_qualifies_instead_of_blocking():
    out = _graph().prepare_final_state(_ev_cross_check_state(), "TEST")
    header = _header("TEST", out, None)

    assert out["publication_status"] == "OK_WITH_QUALIFICATION"
    assert out["publication_qualifications"] == [
        "EV_COMPARISON_VERIFIED_WITH_SHARE_MISMATCH"
    ]
    assert out["publication_reasons"] == []
    assert out["provisional_decision"] == "HOLD"
    assert out["published_decision"] == "HOLD"
    assert out["final_rating"] == "Hold"
    assert out["controlling_fair_value_base"] == 6_837.0
    assert out["decision_safety_check"] == "PASS"
    assert "PUBLICATION_STATUS: OK_WITH_QUALIFICATION" in header
    assert "PUBLICATION_QUALIFICATIONS: EV_COMPARISON_VERIFIED_WITH_SHARE_MISMATCH" in header
    assert "PER_SHARE_COMPARISON_STATUS: UNVERIFIED" in header
    assert "EV_DECISION_USE: CROSS_CHECK_ONLY" in header


def test_the_same_ev_defect_blocks_when_ev_is_controlling():
    out = _graph().prepare_final_state(
        _ev_cross_check_state(controlling_valuation_method="ev_ebitda"), "TEST"
    )

    assert out["publication_status"] == "BLOCKED"
    assert out["publication_reasons"] == [
        "EV_COMPARISON_VERIFIED_WITH_SHARE_MISMATCH"
    ]
    assert out["provisional_decision"] == "HOLD"
    assert out["published_decision"] == "NOT_RATED"
    assert out["final_rating"] == "Not Rated"
    assert out["controlling_fair_value_base"] is None


def test_a_controlling_method_configuration_conflict_blocks():
    status, reasons, _qualifications = _graph()._publication_status({
        "controlling_valuation_method": "pe",
        "controlling_valuation_conflict": (
            "ev_ebitda.controlling is true while pe controls"
        ),
    })

    assert status == "BLOCKED"
    assert any(
        "CONTROLLING_VALUATION_METHOD_CONFLICT" in reason for reason in reasons
    )


def test_the_resolved_ev_comparison_replaces_the_stale_component_status():
    state = _ev_cross_check_state(
        component_status=json.dumps({
            "ev_ebitda_method": {"status": "VERIFIED"},
            "ev_per_share_comparison": {"status": "VERIFIED"},
            "controlling_valuation": {"use": "pe only", "ev_controlling": False},
        })
    )

    out = _graph().prepare_final_state(state, "TEST")
    component = json.loads(out["component_status"])

    assert component["ev_ebitda_method"]["status"] == "VERIFIED"
    assert component["ev_per_share_comparison"]["status"] == "UNVERIFIED"
    assert component["controlling_valuation"] == {
        "use": "pe only", "ev_controlling": False,
    }


def test_provisional_decision_survives_a_second_prepare():
    graph = _graph()
    first = graph.prepare_final_state(_blocked_state(), "TEST")
    second = graph.prepare_final_state(first, "TEST")
    header = _header("TEST", second, None)

    assert first["provisional_decision"] == "HOLD"
    assert second["provisional_decision"] == "HOLD"
    assert second["published_decision"] == "NOT_RATED"
    assert second["final_rating"] == "Not Rated"
    assert "Rating: Not Rated (publication blocked)" in header


def test_published_decision_drives_the_investor_facing_rating():
    state = {
        "trade_date": "2026-11-06",
        "final_trade_decision": "**Rating**: Buy",
        "final_rating": "Buy",
        "provisional_decision": "BUY",
    }

    out = _graph().prepare_final_state(state, "TEST")
    header = _header("TEST", out, None)

    assert out["provisional_decision"] == "BUY"
    assert out["published_decision"] == "BUY"
    assert out["final_rating"] == "Buy"
    assert "Rating: Buy" in header


def test_deterministic_issues_are_itemized_in_the_appendix(tmp_path):
    issues = [
        {
            "kind": "driver_metadata",
            "severity": "material",
            "detail": "INCOMPLETE_DRIVER: revenue FY3/28: volume is missing basis",
            "fields": ["revenue FY3/28: volume"],
        }
    ]
    state = {
        "trade_date": "2026-11-06",
        "model_status": "UNVERIFIED",
        "research_model_source": "unavailable",
        "publication_status": "BLOCKED",
        "publication_fair_value_status": "NOT_PUBLISHED",
        "final_trade_decision": "**Rating**: Not Rated",
        "final_rating": "Not Rated",
        "decision_status": "NOT_RATED",
        "operating_model_status": "NOT_TESTED",
        "fundamentals_report": "Reported revenue 1,128.6.",
        "research_package": "Valuation: price 20.",
        "source_facts": "SOURCE_FACTS (company-reported)",
        "deterministic_issues": json.dumps(issues),
    }

    path = write_report_tree(state, "TEST", tmp_path, html=False)
    main = path.read_text(encoding="utf-8")
    appendix = (tmp_path / "appendix.md").read_text(encoding="utf-8")

    assert "Deterministic model issues" in appendix
    assert "INCOMPLETE_DRIVER: revenue FY3/28" in appendix
    assert "INTERNAL_DIAGNOSTIC_ONLY" in appendix
    assert "NOT_PUBLISHED" in appendix
    assert "NOT_DECISION_EVIDENCE" in appendix
    assert "V. Portfolio Manager Decision" in appendix
    assert appendix.count("INTERNAL_DIAGNOSTIC_ONLY") >= 2
    assert "INCOMPLETE_DRIVER" not in main
