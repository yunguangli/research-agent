"""Cross-stock fixtures: the architecture, not one company, is under test.

The fixtures hold company facts (share counts, conversion terms, project timing,
consensus) and expected statuses. Production code contains none of them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradingagents.research import capital, kernel, sensitivity, status, valuation
from tradingagents.research.schemas import (
    Bridge,
    CapitalStructure,
    Convertible,
    Financials,
    Line,
    ModelSpec,
    Period,
    ShareBase,
    ValuationSpec,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _ibiden_spec(*, dated_convertible: bool = True) -> ModelSpec:
    facts = json.loads((FIXTURES / "4062.T" / "facts.json").read_text(encoding="utf-8"))
    structure = CapitalStructure.from_dict(facts["capital_structure"])
    if not dated_convertible:
        structure.convertibles = [
            Convertible(name="convertible bonds 2029", shares_m=19.8, conversion_date="")
        ]
    return ModelSpec(
        ticker=facts["ticker"],
        company="Ibiden",
        currency="JPY",
        unit="JPY_bn",
        fiscal_year_end_month=3,
        sector=facts["sector"],
        periods=[
            Period("FY3/26", "2025-04-01", "2026-03-31", "actual"),
            Period("FY3/27", "2026-04-01", "2027-03-31", "forecast"),
        ],
        actuals={"FY3/26": Financials(revenue=416.2, operating_profit=62.0, net_profit=63.7)},
        guidance={},
        forecast={"FY3/27": Financials(net_profit=91.0)},
        bridges=[
            Bridge("revenue", "FY3/26", "FY3/27", 416.2,
                   [Line("substrate growth", 153.8)], declared_total=570.0),
            Bridge("operating_profit", "FY3/26", "FY3/27", 62.0,
                   [Line("operating leverage", 74.0)], declared_total=136.0),
        ],
        share_bases=[
            ShareBase("period_end_net", 562.3),
            ShareBase("diluted_average", 566.0),
            ShareBase("full_conversion", 585.8),
        ],
        eps_basis="diluted_average",
        net_debt=facts["net_debt"],
        effective_tax_rate=facts["effective_tax_rate"],
        capital_structure=structure,
        valuation=ValuationSpec(
            price=8045.0,
            fair_value_multiple_low=30.0,
            fair_value_multiple_high=44.0,
            market_cap_basis="period_end_net",
            forecast_eps_basis="diluted_average",
            dilution_sensitivity_basis="full_conversion",
            equity_value_share_basis="period_end_net",
            ev_ebitda_multiple=15.0,
        ),
    )


def test_the_ibiden_fixture_verifies_every_basis_it_declares():
    model = _ibiden_spec()
    result = kernel.run(model)

    assert model.capital_structure is not None
    assert result.row("FY3/27").eps["diluted_average"] == pytest.approx(91.0 * 1000 / 566.0)

    statuses = status.assess(
        model_status="VERIFIED",
        basis={
            "market_cap_basis": "period_end_net",
            "forecast_eps_basis": "diluted_average",
            "dilution_sensitivity_basis": "full_conversion",
            "equity_value_share_basis": "period_end_net",
        },
        has_band=True,
        price=model.valuation.price,
        has_depreciation=False,
        has_net_debt=True,
        ev_ebitda_multiple=model.valuation.ev_ebitda_multiple,
        capital_verified=capital.validated(model)[0],
    )

    expected = json.loads(
        (FIXTURES / "4062.T" / "facts.json").read_text(encoding="utf-8")
    )["expected"]
    assert statuses["operating_model_status"] == expected["operating_model_status"]
    assert statuses["eps_model_status"] == expected["eps_model_status"]
    assert statuses["valuation_method_statuses"]["pe"] == expected["pe"]
    # No depreciation row in this compact fixture, so EBITDA cannot verify.
    assert statuses["valuation_method_statuses"]["ev_ebitda"] == "UNAVAILABLE"


def test_an_undated_convertible_in_the_ibiden_fixture_suppresses_eps_only():
    model = _ibiden_spec(dated_convertible=False)

    usable, reason = capital.validated(model)
    assert usable is False and "timing" in reason

    statuses = status.assess(
        model_status="VERIFIED",
        basis={
            "market_cap_basis": "period_end_net",
            "forecast_eps_basis": "diluted_average",
            "dilution_sensitivity_basis": "full_conversion",
        },
        has_band=True,
        price=model.valuation.price,
        has_depreciation=False,
        has_net_debt=True,
        ev_ebitda_multiple=model.valuation.ev_ebitda_multiple,
        capital_verified=False,
    )

    assert statuses["eps_model_status"] == "SUPPRESSED"
    assert statuses["valuation_method_statuses"]["pe"] == "SUPPRESSED"


def test_the_bank_fixture_runs_and_prices_both_methods_independently():
    spec = ModelSpec.from_dict(
        json.loads((FIXTURES / "bank" / "spec.json").read_text(encoding="utf-8"))
    )

    model = kernel.run(spec)
    value = valuation.value(model)

    assert model.row("FY26").eps["diluted_average"] == pytest.approx(3.0)
    assert value.pe == pytest.approx(50.0 / 3.0)
    assert value.ebitda == pytest.approx(50.0)          # OP 46 + D&A 4
    assert value.enterprise_value == pytest.approx(400.0)   # 8.0x
    assert value.ev_equity_value == pytest.approx(420.0)    # net cash -20
    assert value.ev_per_share == pytest.approx(42.0)        # 420 / 10 shares

    statuses = status.assess(
        model_status="VERIFIED",
        basis={
            "market_cap_basis": "period_end_net",
            "forecast_eps_basis": "diluted_average",
            "dilution_sensitivity_basis": "full_conversion",
            "equity_value_share_basis": "period_end_net",
        },
        has_band=True,
        price=spec.valuation.price,
        has_depreciation=True,
        has_net_debt=True,
        ev_ebitda_multiple=spec.valuation.ev_ebitda_multiple,
        capital_verified=capital.validated(spec)[0],
    )
    assert statuses["eps_model_status"] == "VERIFIED"
    assert statuses["valuation_method_statuses"] == {
        "pe": "VERIFIED", "ev_ebitda": "VERIFIED",
    }


def test_the_bank_fixture_produces_a_nim_sensitivity_with_a_path():
    spec = ModelSpec.from_dict(
        json.loads((FIXTURES / "bank" / "spec.json").read_text(encoding="utf-8"))
    )

    items, gaps = sensitivity.rank(spec)

    nim = next(item for item in items if item.driver == "net interest margin")
    assert nim.revenue_impact == pytest.approx(4.0)     # 14 vs 10
    assert nim.propagation_path[0] == "revenue"
    assert "eps" in nim.propagation_path
