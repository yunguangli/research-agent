"""The shared status object is rebuilt after repair, never patched field by field."""

from __future__ import annotations

import json

from tradingagents.agents.managers.portfolio_manager import _component_status_note
from tradingagents.graph.trading_graph import (
    TradingAgentsGraph,
    build_component_status,
    repaired_status_object,
)
from tradingagents.reporting import write_report_tree


def _statuses(*, operating="VERIFIED", eps="VERIFIED", pe="VERIFIED",
              ev="VERIFIED", sensitivity="VERIFIED", ev_per_share="UNVERIFIED"):
    return {
        "operating_model_status": operating,
        "eps_model_status": eps,
        "valuation_method_statuses": {"pe": pe, "ev_ebitda": ev},
        "ev_per_share_status": ev_per_share,
        "sensitivity_model_status": sensitivity,
    }


def test_an_unverified_model_becomes_verified_after_a_successful_repair():
    pre = build_component_status(
        _statuses(operating="UNVERIFIED", eps="UNVERIFIED", pe="UNVERIFIED"),
        propagation_ok=False,
    )
    post = build_component_status(_statuses(), propagation_ok=True)

    result = repaired_status_object(pre, post, "repair:1", "2026-11-05T12:00:00")

    assert result["operating_forecast"]["status"] == "VERIFIED"
    assert result["forecast_eps"]["status"] == "VERIFIED"
    assert result["pre_repair_status"]["operating_forecast"]["status"] == "UNVERIFIED"
    assert result["post_repair_status"]["operating_forecast"]["status"] == "VERIFIED"
    assert result["repair_id"] == "repair:1"
    assert result["validated_at"] == "2026-11-05T12:00:00"


def test_a_defective_repair_keeps_the_controlling_component_unverified():
    pre = build_component_status(_statuses(), propagation_ok=True)
    post = build_component_status(
        _statuses(operating="VERIFIED", eps="UNVERIFIED", pe="UNVERIFIED"),
        propagation_ok=False,
    )

    result = repaired_status_object(pre, post, "repair:2", "2026-11-05T12:01:00")

    assert result["operating_forecast"]["status"] == "VERIFIED"
    assert result["forecast_eps"]["status"] == "UNVERIFIED"
    assert result["pe_valuation"]["status"] == "UNVERIFIED"
    assert result["pre_repair_status"]["forecast_eps"]["status"] == "VERIFIED"


def test_a_mixed_repair_changes_only_the_component_that_changed():
    pre = build_component_status(_statuses(sensitivity="UNVERIFIED"), propagation_ok=False)
    post = build_component_status(_statuses(), propagation_ok=True)

    result = repaired_status_object(pre, post, "repair:3", "2026-11-05T12:02:00")

    assert result["operating_forecast"]["status"] == "VERIFIED"
    assert result["sensitivity"]["status"] == "VERIFIED"
    assert result["pre_repair_status"]["sensitivity"]["status"] == "UNVERIFIED"


def test_repeating_the_same_repair_is_idempotent():
    pre = build_component_status(_statuses(), propagation_ok=True)
    post = build_component_status(_statuses(), propagation_ok=True)

    first = repaired_status_object(pre, post, "repair:4", "2026-11-05T12:03:00")
    second = repaired_status_object(first, post, "repair:4", "2026-11-05T12:04:00")

    assert first is second
    assert second["validated_at"] == "2026-11-05T12:03:00"


def test_a_failed_repair_leaves_controlling_components_unverified():
    result = TradingAgentsGraph._repair_failed(
        None, "the repaired model does not tie", "repair_failed"
    )
    component = json.loads(result["component_status"])

    assert component["operating_forecast"]["status"] == "UNVERIFIED"
    assert component["forecast_eps"]["status"] == "UNVERIFIED"
    assert component["pe_valuation"]["status"] == "UNVERIFIED"
    assert component["controlling_valuation"]["use"] == "none"
    assert result["model_status"] == "UNVERIFIED"


def test_a_repair_rebuild_matches_the_initial_assessment():
    statuses = _statuses()
    initial = build_component_status(statuses, propagation_ok=True)
    post = build_component_status(statuses, propagation_ok=True)

    result = repaired_status_object({}, post, "repair:6", "2026-11-05T12:06:00")

    for key, value in post.items():
        assert result[key] == value
    assert initial == post


def test_header_appendix_and_pm_receive_the_same_post_repair_status(tmp_path):
    pre = build_component_status(
        _statuses(operating="UNVERIFIED", eps="UNVERIFIED", pe="UNVERIFIED"),
        propagation_ok=False,
    )
    post = build_component_status(_statuses(), propagation_ok=True)
    component = repaired_status_object(pre, post, "repair:5", "2026-11-05T12:05:00")

    state = {
        "trade_date": "2026-11-05",
        "component_status": json.dumps(component),
        "final_trade_decision": "**Rating**: Hold\n\n**Executive Summary**: No change.",
    }
    write_report_tree(state, "TEST", tmp_path, html=False)
    main = (tmp_path / "complete_report.md").read_text(encoding="utf-8")
    appendix = (tmp_path / "appendix.md").read_text(encoding="utf-8")
    pm_note = _component_status_note(state)

    # The three surfaces phrase it differently but must carry the same values.
    assert "EV/EBITDA per-share comparison: UNVERIFIED" in main
    assert "EV/EBITDA per-share comparison: UNVERIFIED" in pm_note
    assert "| ev_per_share_comparison | UNVERIFIED |" in appendix
    assert "EV/EBITDA enterprise-value method: VERIFIED" in main
    assert "EV/EBITDA enterprise-value method: VERIFIED" in pm_note
    assert "| ev_ebitda_method | VERIFIED |" in appendix
    assert "| decision_use | failed_rows_excluded |" in appendix
