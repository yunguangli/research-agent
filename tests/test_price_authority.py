"""A controlling price has an authority, and a repair may not silently replace it.

The 6857.T run is the reference case: the analysis-date close was 29,485 and a
four-day-old note implied 32,980. Both reconcile with their own market
capitalisation, so the kernel could not see the substitution. These tests pin the
rank, the rejection, the conflict and the publication gate.
"""

from __future__ import annotations

import json

import pytest

from tradingagents.research import dependencies, price_authority, repair, superseded
from tradingagents.research.schemas import (
    Bridge,
    Financials,
    Line,
    ModelSpec,
    Period,
    Scenario,
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
        ],
        actuals={"FY25": Financials(revenue=100.0, operating_profit=10.0, net_profit=6.0)},
        guidance={},
        forecast={"FY26": Financials(net_profit=8.0)},
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


def _price_challenge(
    value: float,
    *,
    source: str = "",
    as_of: str = "",
    ref: str = "",
    stated: float | None = 20.0,
) -> repair.ModelChallenge:
    return repair.ModelChallenge(
        challenge_type="share_price",
        severity="material",
        affected_fields=["share price"],
        proposed_correction="correct the share price",
        proposed_values={"valuation.price": value},
        stated_model_value=stated,
        source_class=source,
        as_of=as_of,
        source_ref=ref,
    )


# --- rank and conflict ---------------------------------------------------------


def test_the_source_rank_puts_the_user_first_and_a_derivation_last():
    assert price_authority.rank_of(price_authority.USER_EXPLICIT) > price_authority.rank_of(
        price_authority.VERIFIED_CLOSE
    )
    assert price_authority.rank_of(price_authority.VERIFIED_CLOSE) > price_authority.rank_of(
        price_authority.REPORT_PRICE
    )
    assert price_authority.rank_of(price_authority.DERIVED) == 0
    assert price_authority.rank_of("") == -1


def test_a_lower_authority_price_is_rejected_and_never_applied():
    current = price_authority.PriceResolution(
        value=29_485.0,
        source=price_authority.VERIFIED_CLOSE,
        as_of="2026-11-05",
        ref="analysis-date close",
        ticker="6857.T",
    )
    challenge = _price_challenge(32_980.0, source="REPORT_PRICE", as_of="2026-11-04")

    verified, unverified, _structural = repair.verify_challenges(
        spec(), [challenge], price_resolution=current
    )

    assert verified == []
    assert unverified == [challenge]
    assert challenge.rejection_reason.startswith("LOWER_AUTHORITY_SOURCE")
    with pytest.raises(repair.RepairError, match="LOWER_AUTHORITY_SOURCE"):
        repair.repair_model(spec(), [challenge], price_resolution=current)


def test_a_missing_source_class_is_treated_as_a_report_price_and_loses():
    current = price_authority.PriceResolution(
        value=29_485.0, source=price_authority.VERIFIED_CLOSE, as_of="2026-11-05"
    )

    challenge = _price_challenge(32_980.0, stated=20.0)
    _verified, unverified, _structural = repair.verify_challenges(
        spec(), [challenge], price_resolution=current
    )

    assert unverified == [challenge]
    assert "LOWER_AUTHORITY_SOURCE" in challenge.rejection_reason


def test_two_values_at_the_same_authority_are_a_conflict_not_a_pick():
    current = price_authority.PriceResolution(
        value=29_485.0, source=price_authority.VERIFIED_CLOSE, as_of="2026-11-05"
    )
    challenge = _price_challenge(
        32_980.0, source="VERIFIED_CLOSE", as_of="2026-11-05", ref="second vendor"
    )

    _verified, unverified, _structural = repair.verify_challenges(
        spec(), [challenge], price_resolution=current
    )

    assert unverified == [challenge]
    assert challenge.rejection_reason.startswith("AUTHORITY_CONFLICT")


def test_a_report_price_may_replace_a_spec_price_without_a_market_close():
    current = price_authority.PriceResolution(
        value=20.0, source=price_authority.SPEC_PRICE
    )
    challenge = _price_challenge(21.0, source="REPORT_PRICE", as_of="2026-11-04")

    verified, unverified, _structural = repair.verify_challenges(
        spec(), [challenge], price_resolution=current
    )

    assert verified == [challenge]
    assert unverified == []


def test_an_analyst_cannot_claim_the_users_override():
    current = price_authority.PriceResolution(
        value=20.0, source=price_authority.SPEC_PRICE
    )
    challenge = _price_challenge(21.0, source="USER_EXPLICIT", as_of="2026-11-05")

    verified, _unverified, _structural = repair.verify_challenges(
        spec(), [challenge], price_resolution=current
    )

    assert verified == [challenge]
    # The claimed class was downgraded to a report price, not accepted at the top.
    proposed = repair._price_resolution_from_challenge(challenge, 21.0, "2026-11-05")
    assert proposed.source == price_authority.REPORT_PRICE


def test_the_changed_path_is_the_price_not_only_the_market_cap_it_implies():
    _repaired, _result, changes = repair.repair_model(
        spec(),
        [_price_challenge(21.0, source="REPORT_PRICE", as_of="2026-11-04")],
        price_resolution=price_authority.PriceResolution(
            value=20.0, source=price_authority.SPEC_PRICE
        ),
    )

    by_field = {change["field"]: change for change in changes}
    assert by_field["valuation.price"]["old"] == 20.0
    assert by_field["valuation.price"]["new"] == 21.0
    assert 20.0 in repair.superseded_values(changes)


# --- resolution ----------------------------------------------------------------


def test_the_analysis_date_close_outranks_the_spec_price(monkeypatch):
    import tradingagents.dataflows.prices as prices
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    monkeypatch.setattr(prices, "last_close", lambda *args, **kwargs: 25.0)
    resolution = TradingAgentsGraph._resolve_price(spec(), "TEST", "2026-11-05", None)

    assert resolution.value == 25.0
    assert resolution.source == price_authority.VERIFIED_CLOSE
    assert resolution.as_of == "2026-11-05"


def test_an_explicit_price_is_recorded_as_a_user_override(monkeypatch):
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    monkeypatch.setenv("TRADINGAGENTS_MODEL_PRICE", "42")
    resolution = TradingAgentsGraph._resolve_price(spec(), "TEST", "2026-11-05", None)

    assert resolution.value == 42.0
    assert resolution.source == price_authority.USER_EXPLICIT
    assert resolution.explicit is True


def test_a_stored_resolution_is_reused_without_re_picking(monkeypatch):
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    stored = price_authority.PriceResolution(
        value=29_485.0, source=price_authority.VERIFIED_CLOSE, as_of="2026-11-05"
    )
    resolution = TradingAgentsGraph._resolve_price(
        spec(), "TEST", "2026-11-05", None, resolution=stored
    )

    assert resolution.value == stored.value
    assert resolution.source == stored.source
    assert resolution.as_of == stored.as_of
    assert resolution.ticker == "TEST"
    assert resolution.currency == "USD"


def test_a_stored_override_from_another_date_is_not_current(monkeypatch):
    import tradingagents.dataflows.prices as prices
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    monkeypatch.setattr(prices, "last_close", lambda *args, **kwargs: None)
    stale = spec()
    stale.valuation.price_source = price_authority.USER_EXPLICIT
    stale.valuation.price_as_of = "2026-11-04"

    resolution = TradingAgentsGraph._resolve_price(stale, "TEST", "2026-11-05", None)

    assert resolution.value == 20.0
    assert resolution.source == price_authority.SPEC_PRICE
    assert "may be stale" in resolution.note


# --- dependencies --------------------------------------------------------------


def test_an_unverified_price_source_does_not_verify_the_market_cap():
    statuses = dependencies.assess_outputs(
        model_status="VERIFIED",
        basis={"market_cap_basis": "diluted", "forecast_eps_basis": "basic"},
        price=20.0,
        has_depreciation=True,
        has_net_debt=True,
        pe_multiple=11.0,
        ev_ebitda_multiple=10.0,
        share_price_verified=False,
    )

    assert statuses["share_price"] == dependencies.UNVERIFIED
    assert statuses["market_cap"] == dependencies.SUPPRESSED


# --- superseded prose ----------------------------------------------------------


def test_a_superseded_figure_named_as_such_is_not_a_current_use():
    values = {8.0}
    assert superseded.cites_as_current("Net profit is 8.0.", values) is True
    assert superseded.cites_as_current(
        "Net profit was 8.0; that figure is superseded by 9.0.", values
    ) is False


def test_a_sentence_that_only_names_the_old_value_is_not_a_violation():
    values = {29_485.0}
    assert superseded.cites_as_current("The model's 29,485 is stale.", values) is False
    assert superseded.cites_as_current("The shares trade at 29,485.", values) is True


# --- publication ---------------------------------------------------------------


def test_a_same_authority_conflict_blocks_the_report():
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    status, reasons = graph._publication_status({
        "price_authority_conflict": (
            "AUTHORITY_CONFLICT: two VERIFIED_CLOSE prices disagree (29,485 vs 32,980)"
        )
    })

    assert status == "BLOCKED"
    assert "VERIFIED_CLOSE" in reasons[0]


def test_a_decision_that_names_the_old_price_as_superseded_is_not_blocked():
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    state = {
        "model_repair_log": json.dumps({
            "changes": [{"field": "valuation.price", "old": 29_485.0, "new": 32_980.0}]
        }),
        "final_trade_decision": (
            "**Rating**: Hold\n\nI use 32,980; 29,485 is the superseded figure."
        ),
    }

    status, reasons = graph._publication_status(state)

    assert status == "OK"
    assert reasons == []


def test_a_decision_that_describes_both_prices_is_not_blocked():
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    state = {
        "model_repair_log": json.dumps({
            "changes": [{"field": "valuation.price", "old": 29_485.0, "new": 32_980.0}]
        }),
        "final_trade_decision": (
            "**Rating**: Hold\n\nTwo share prices conflict: 29,485 versus 32,980; "
            "I use 32,980."
        ),
    }

    status, _reasons = graph._publication_status(state)

    assert status == "OK"


def test_a_decision_that_uses_a_superseded_price_blocks_publication():
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    state = {
        "model_repair_log": json.dumps({
            "changes": [{"field": "valuation.price", "old": 29_485.0, "new": 32_980.0}]
        }),
        "final_trade_decision": "**Rating**: Hold\n\nThe shares trade at 29,485.",
    }

    status, reasons = graph._publication_status(state)

    assert status == "BLOCKED"
    assert "superseded" in reasons[0]


def test_a_decision_written_against_another_model_version_blocks_publication():
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    status, reasons = graph._publication_status({
        "active_model_version": "v2",
        "decision_model_version": "v1",
    })

    assert status == "BLOCKED"
    assert "v1" in reasons[0] and "v2" in reasons[0]


def test_prepare_final_state_withholds_the_rating_when_a_conflict_remains():
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {}
    state = {
        "final_trade_decision": "**Rating**: Hold\n\n**Executive Summary**: No change.",
        "price_authority_conflict": "two VERIFIED_CLOSE prices disagree",
    }

    out = graph.prepare_final_state(state, "TEST")

    assert out["publication_status"] == "BLOCKED"
    assert out["final_rating"] == "Not Rated"
    assert out["decision_status"] == "PROVISIONAL"
    assert "**Publication blocked**" in out["final_trade_decision"]


def test_the_blocked_report_carries_no_rating_or_fair_value(tmp_path):
    from tradingagents.reporting import write_report_tree

    state = {
        "trade_date": "2026-11-05",
        "publication_status": "BLOCKED",
        "publication_reasons": ["two VERIFIED_CLOSE prices disagree"],
        "final_trade_decision": "**Rating**: Hold\n\n**Fair Value**: 6,033-8,446",
    }
    path = write_report_tree(state, "TEST", tmp_path, html=False)
    main = path.read_text(encoding="utf-8")

    assert "Publication blocked" in main
    assert "**Rating**: Hold" not in main
    assert "6,033" not in main


def test_a_stale_fundamental_summary_is_replaced_by_the_repair_note(tmp_path):
    from tradingagents.reporting import write_report_tree

    state = {
        "trade_date": "2026-11-05",
        "publication_status": "OK",
        "fundamentals_report": (
            "Valuation: the shares trade on 22.5x at 29,485 with +11% base upside."
        ),
        "model_repair_log": json.dumps({
            "changes": [{"field": "valuation.price", "old": 29_485.0, "new": 32_980.0}]
        }),
        "model_repair_addendum": "**Model repair (v2)** - the price is 32,980.",
    }
    path = write_report_tree(state, "TEST", tmp_path, html=False)
    main = path.read_text(encoding="utf-8")

    assert "Model repair" in main
    assert "22.5x at 29,485" not in main
