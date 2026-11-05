"""Stable technical evidence: ids name facts, values are checked."""

from __future__ import annotations

import json

from tradingagents.research import technical


def _item(**overrides):
    base = {
        "evidence_id": "tech_close_below_50sma",
        "metric": "close_vs_50sma",
        "observed_value": 7988.0,
        "comparison_value": 10126.0,
        "direction": "below",
        "timeframe": "3-6 months",
        "source_refs": ["market_data:2026-11-04"],
        "supports_action": True,
    }
    base.update(overrides)
    return technical.TechnicalEvidence.from_dict(base)


def _state_with(comparison: float) -> dict:
    item = {
        "evidence_id": "tech_close_below_200sma",
        "metric": "close_vs_200sma",
        "observed_value": comparison - 100.0,
        "comparison_value": comparison,
        "direction": "below",
        "timeframe": "3-6 months",
        "source_refs": ["market_data:2026-11-04"],
        "supports_action": True,
    }
    return {"technical_evidence": json.dumps([technical.validate(
        technical.TechnicalEvidence.from_dict(item)
    ).to_dict()])}


def test_the_pm_level_is_checked_against_the_verified_evidence():
    from tradingagents.agents.managers.portfolio_manager import _technical_mismatches

    state = _state_with(7794.0)

    assert _technical_mismatches("The 200-day SMA at 7,794 held.", state) == []
    # The 2026-11-04 run printed 7,884 for the same level.
    assert _technical_mismatches("The 200-day SMA at 7,884 held.", state)


def test_an_unverified_item_does_not_supply_a_level():
    from tradingagents.agents.managers.portfolio_manager import _technical_mismatches

    state = {"technical_evidence": json.dumps([{
        "evidence_id": "tech_close_below_200sma", "observed_value": 8037.0,
        "comparison_value": 7794.0, "direction": "below",
        "verification_status": "REJECTED",
    }])}

    assert _technical_mismatches("The 200-day SMA at 7,884 held.", state) == []


def test_a_consistent_reading_verifies():
    result = technical.validate(_item())

    assert result.verification_status == technical.VERIFIED


def test_a_id_whose_direction_contradicts_its_numbers_is_rejected():
    result = technical.validate(_item(direction="above"))

    assert result.verification_status.startswith(technical.UNVERIFIED)
    assert "asserts below" in result.verification_status


def test_numbers_that_do_not_support_the_claim_are_rejected():
    result = technical.validate(
        _item(observed_value=11000.0)          # above the 50 SMA
    )

    assert result.verification_status.startswith(technical.UNVERIFIED)
    assert "does not support" in result.verification_status


def test_an_unknown_id_is_rejected():
    result = technical.validate(_item(evidence_id="tech_vibes_good"))

    assert result.verification_status.startswith(technical.UNVERIFIED)
    assert "unknown evidence id" in result.verification_status


def test_a_missing_source_or_timeframe_is_rejected():
    assert technical.validate(_item(source_refs=[])).verification_status.startswith(
        technical.UNVERIFIED
    )
    assert technical.validate(_item(timeframe="")).verification_status.startswith(
        technical.UNVERIFIED
    )


def test_the_block_is_removed_from_the_reader_facing_report():
    report = (
        "Trend is down.\n\n"
        "TECHNICAL_EVIDENCE: "
        + json.dumps([_item().to_dict()])
        + "\n"
    )

    text, items = technical.parse(report)

    assert "TECHNICAL_EVIDENCE" not in text
    assert text.strip() == "Trend is down."
    assert len(items) == 1
    assert items[0].verification_status == technical.VERIFIED


def test_only_verified_items_render_for_the_manager():
    report = (
        "Trend is down.\nTECHNICAL_EVIDENCE: "
        + json.dumps([
            _item().to_dict(),
            _item(evidence_id="tech_vibes_good").to_dict(),
        ])
        + "\n"
    )

    _text, items = technical.parse(report)
    block = technical.render(items)

    assert "tech_close_below_50sma" in block
    assert "tech_vibes_good" not in block
