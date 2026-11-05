"""A shock must travel the whole chain, not stop at operating profit."""

from __future__ import annotations

import pytest

from tradingagents.research import sensitivity
from tradingagents.research.schemas import Bridge, Line

from .test_research_kernel import spec


@pytest.fixture(autouse=True)
def _enable_flag(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_FEATURE_SENSITIVITY_INVARIANTS_V1", "1")


def _spec(bridges=None, **overrides):
    bridges = bridges or [
        Bridge(
            "revenue", "FY25", "FY26", 120.0,
            [Line("Growth", 10.0, driver="growth", alternative=4.0)],
            declared_total=130.0,
        ),
        Bridge(
            "operating_profit", "FY25", "FY26", 15.0,
            [Line("Leverage", 1.0, driver="leverage", alternative=0.0)],
            declared_total=16.0,
        ),
        Bridge(
            "net_profit", "FY25", "FY26", 9.0,
            [Line("Tax and minority", 1.0)],
            declared_total=10.0,
        ),
    ]
    return spec(bridges=bridges, **overrides)


def test_a_revenue_shock_reaches_net_income_eps_and_fair_value():
    items, gaps = sensitivity.rank(_spec(), top=5)
    item = next(entry for entry in items if entry.driver == "growth")

    assert item.operating_profit_impact == pytest.approx(-0.6)
    assert item.net_income_impact not in (None, 0.0)
    assert item.eps_impact not in (None, 0.0)
    assert item.fair_value_impact not in (None, 0.0)
    assert not any(
        gap.startswith("SENSITIVITY_PROPAGATION_FAILED") for gap in gaps
    ), gaps


def test_an_operating_profit_shock_reaches_eps_and_fair_value():
    items, _gaps = sensitivity.rank(_spec(), top=5)
    item = next(entry for entry in items if entry.driver == "leverage")

    assert item.operating_profit_impact == pytest.approx(-1.0)
    assert item.net_income_impact == pytest.approx(-0.75)
    assert item.eps_impact not in (None, 0.0)
    assert item.fair_value_impact not in (None, 0.0)


def test_a_chain_that_stops_at_operating_profit_is_a_named_failure():
    # The operating-profit bridge adds nothing, so a revenue shock cannot reach
    # profit. Without a declared zero pass-through this is a broken sensitivity.
    model_spec = _spec(bridges=[
        Bridge(
            "revenue", "FY25", "FY26", 120.0,
            [Line("Growth", 10.0, driver="growth", alternative=4.0)],
            declared_total=130.0,
        ),
        Bridge("operating_profit", "FY25", "FY26", 15.0, [], declared_total=15.0),
        Bridge(
            "net_profit", "FY25", "FY26", 9.0,
            [Line("Tax and minority", 1.0)],
            declared_total=10.0,
        ),
    ])

    items, gaps = sensitivity.rank(model_spec, top=5)
    item = next(entry for entry in items if entry.driver == "growth")

    # The row stays visible but is not a result: its zero is not published.
    assert item.verification_status == "UNVERIFIED"
    assert any(
        gap.startswith("SENSITIVITY_PROPAGATION_FAILED") and "growth" in gap
        for gap in gaps
    )
    table = sensitivity.render([item])
    assert "| unavailable | unavailable | unavailable |" in table


def test_with_the_flag_off_the_historical_zero_impact_is_reproduced(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_FEATURE_SENSITIVITY_INVARIANTS_V1", raising=False)

    items, _gaps = sensitivity.rank(_spec(), top=5)
    item = next(entry for entry in items if entry.driver == "growth")

    assert item.operating_profit_impact == pytest.approx(-0.6)
    assert item.eps_impact in (None, 0.0)
    assert item.fair_value_impact in (None, 0.0)


def test_a_declared_zero_pass_through_is_accepted():
    model_spec = _spec(bridges=[
        Bridge(
            "revenue", "FY25", "FY26", 120.0,
            [Line("Growth", 10.0, driver="growth", alternative=4.0,
                  formula="zero pass-through: volume only")],
            declared_total=130.0,
        ),
        Bridge("operating_profit", "FY25", "FY26", 15.0, [], declared_total=15.0),
        Bridge(
            "net_profit", "FY25", "FY26", 9.0,
            [Line("Tax and minority", 1.0)],
            declared_total=10.0,
        ),
    ])

    _items, gaps = sensitivity.rank(model_spec, top=5)

    assert not any(
        "no operating-profit effect" in gap for gap in gaps
    ), gaps
