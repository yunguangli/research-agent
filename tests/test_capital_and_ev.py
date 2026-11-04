"""Phase 2/3: capital structure, per-period EPS, and EV/EBITDA."""

from __future__ import annotations

import dataclasses

import pytest

from tradingagents.research import capital, kernel, status, valuation
from tradingagents.research.report import compact_summary
from tradingagents.research.schemas import (
    Bridge,
    CapitalStructure,
    CashFlow,
    Convertible,
    Financials,
    InvestmentProject,
    Line,
    ModelSpec,
    NetDebt,
    Period,
    ShareBase,
    ValuationSpec,
)


def spec(**overrides) -> ModelSpec:
    base = ModelSpec(
        ticker="TEST",
        company="Test",
        currency="USD",
        unit="USD_m",
        fiscal_year_end_month=12,
        periods=[
            Period("FY25", "2025-01-01", "2025-12-31", "actual"),
            Period("FY26", "2026-01-01", "2026-12-31", "forecast"),
        ],
        actuals={"FY25": Financials(revenue=80.0, operating_profit=8.0, net_profit=10.0)},
        guidance={},
        forecast={"FY26": Financials(net_profit=12.0)},
        bridges=[
            Bridge("revenue", "FY25", "FY26", 80.0, [Line("growth", 20.0)],
                   declared_total=100.0),
            Bridge("operating_profit", "FY25", "FY26", 8.0, [Line("leverage", 12.0)],
                   declared_total=20.0),
        ],
        cashflow={
            "FY26": CashFlow(net_profit=12.0, depreciation=5.0,
                             working_capital_change=-1.0, capex=4.0),
        },
        share_bases=[
            ShareBase("period_end_net", 100.0),
            ShareBase("diluted_average", 100.0),
            ShareBase("full_conversion", 100.0),
        ],
        eps_basis="diluted_average",
        net_debt=NetDebt(
            gross_debt=250.0,
            cash_and_equivalents=50.0,
            source_refs=["doc-test p.12"],
        ),
        capital_structure=CapitalStructure(
            issued_shares=110.0,
            treasury_shares=10.0,
            basic_weighted_average={"FY25": 100.0, "FY26": 100.0},
            diluted_weighted_average={},
            post_period_issuances=[{"date": "2026-11-01", "shares_m": 5.0}],
            buybacks=[{"date": "2026-11-02", "shares_m": 2.0}],
            convertibles=[Convertible(name="CB", shares_m=8.0, conversion_date="2026-06-30")],
            verification_status="VERIFIED",
        ),
        valuation=ValuationSpec(
            price=100.0,
            fair_value_multiple_low=10.0,
            fair_value_multiple_high=12.0,
            fair_value_low=1.0,
            fair_value_high=1.2,
            fair_value_basis="diluted_average",
            fair_value_period="FY26",
            market_cap_basis="period_end_net",
            forecast_eps_basis="diluted_average",
            dilution_sensitivity_basis="full_conversion",
            ev_ebitda_multiple=10.0,
        ),
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


# --- capital structure --------------------------------------------------------


def test_net_shares_apply_issuance_and_buyback():
    shares, reason = capital.current_net_shares(spec().capital_structure)

    assert reason == ""
    assert shares == pytest.approx(103.0)      # 110 - 10 + 5 - 2


def test_a_convertible_counts_only_after_its_conversion_date():
    structure = spec().capital_structure

    before, _ = capital.forecast_shares(structure, "FY25", "2025-12-31")
    after, _ = capital.forecast_shares(structure, "FY26", "2026-12-31")

    assert before == pytest.approx(100.0)      # convertible converts after FY25
    assert after == pytest.approx(108.0)       # 100 basic + 8 convertible


def test_full_conversion_adds_every_convertible():
    shares, _ = capital.dilution_shares(spec().capital_structure)

    assert shares == pytest.approx(111.0)      # 103 net + 8


def test_a_split_after_the_period_restates_its_weighted_average():
    model = spec()
    model.capital_structure.split_history = [{"date": "2026-06-01", "factor": 2.0}]

    before, _ = capital.forecast_shares(model.capital_structure, "FY25", "2025-12-31")
    after, _ = capital.forecast_shares(model.capital_structure, "FY26", "2026-12-31")

    assert before == pytest.approx(200.0)     # pre-split average x 2
    assert after == pytest.approx(108.0)      # period after the split: unchanged


def test_an_unverified_capital_structure_suppresses_the_share_bases():
    model = spec()
    model.capital_structure.verification_status = "UNVERIFIED"

    assert capital.verified(model) is False
    result = status.assess(
        model_status="VERIFIED",
        basis={
            "market_cap_basis": "period_end_net",
            "forecast_eps_basis": "diluted_average",
            "dilution_sensitivity_basis": "full_conversion",
        },
        has_band=True,
        price=100.0,
        has_depreciation=True,
        has_net_debt=True,
        ev_ebitda_multiple=10.0,
        capital_verified=capital.verified(model),
    )
    assert result["eps_model_status"] == "SUPPRESSED"
    assert result["valuation_method_statuses"]["pe"] == "SUPPRESSED"
    assert result["valuation_method_statuses"]["ev_ebitda"] == "VERIFIED"
    assert result["ev_per_share_status"] == "SUPPRESSED"


# --- kernel integration -------------------------------------------------------


def test_forecast_eps_uses_the_period_weighted_average_but_history_does_not():
    model = kernel.run(spec())

    assert model.row("FY25").eps["diluted_average"] == pytest.approx(10.0 / 100.0)
    assert model.row("FY26").eps["diluted_average"] == pytest.approx(12.0 / 108.0)


def test_market_cap_uses_net_shares_from_the_capital_structure():
    model = kernel.run(spec())

    assert model.market_cap == pytest.approx(100.0 * 103.0)


# --- EV/EBITDA ----------------------------------------------------------------


def test_ev_ebitda_is_computed_once_without_double_counting_net_debt():
    value = valuation.value(kernel.run(spec()))

    assert value.ebitda == pytest.approx(25.0)          # OP 20 + D&A 5
    assert value.enterprise_value == pytest.approx(250.0)
    assert value.ev_equity_value == pytest.approx(50.0)  # 250 - 200
    assert value.ev_per_share == pytest.approx(50.0 / 103.0)


def test_a_missing_net_debt_leaves_ev_and_equity_unestablished():
    value = valuation.value(kernel.run(spec(net_debt=None)))

    assert value.ebitda == pytest.approx(25.0)
    assert value.enterprise_value is None
    assert value.ev_equity_value is None


def test_verified_debt_and_cash_components_produce_a_verified_ev_ebitda():
    model = spec()

    assert model.net_debt.components_verified() is True
    value = valuation.value(kernel.run(model))

    assert value.enterprise_value == pytest.approx(250.0)   # 10x EBITDA 25
    assert value.ev_equity_value == pytest.approx(50.0)     # 250 - (250 - 50)
    result = status.assess(
        model_status="VERIFIED",
        basis={
            "market_cap_basis": "period_end_net",
            "forecast_eps_basis": "diluted_average",
            "dilution_sensitivity_basis": "full_conversion",
            "equity_value_share_basis": "period_end_net",
        },
        has_band=True,
        price=100.0,
        has_depreciation=True,
        has_net_debt=model.net_debt.components_verified(),
        ev_ebitda_multiple=model.valuation.ev_ebitda_multiple,
        capital_verified=capital.validated(model)[0],
    )
    assert result["valuation_method_statuses"] == {
        "pe": "VERIFIED", "ev_ebitda": "VERIFIED",
    }


def test_missing_net_debt_makes_only_ev_ebitda_unavailable():
    model = spec(net_debt=None)
    value = valuation.value(kernel.run(model))

    assert value.ev_per_share is None
    assert value.pe == pytest.approx(100.0 / (12.0 / 108.0))

    result = status.assess(
        model_status="VERIFIED",
        basis={
            "market_cap_basis": "period_end_net",
            "forecast_eps_basis": "diluted_average",
            "dilution_sensitivity_basis": "full_conversion",
            "equity_value_share_basis": "period_end_net",
        },
        has_band=True,
        price=100.0,
        has_depreciation=True,
        has_net_debt=bool(
            model.net_debt is not None and model.net_debt.components_verified()
        ),
        ev_ebitda_multiple=model.valuation.ev_ebitda_multiple,
        capital_verified=capital.validated(model)[0],
    )
    assert result["valuation_method_statuses"]["pe"] == "VERIFIED"
    assert result["valuation_method_statuses"]["ev_ebitda"] == "UNAVAILABLE"
    assert result["eps_model_status"] == "VERIFIED"


def test_a_missing_cash_component_is_never_defaulted_to_zero():
    model = spec()
    model.net_debt = NetDebt(gross_debt=250.0)   # cash not stated
    value = valuation.value(kernel.run(model))

    assert model.net_debt.components_verified() is False
    assert value.enterprise_value is None
    assert value.ev_equity_value is None


def test_a_bare_net_debt_figure_does_not_feed_a_verified_ev_ebitda():
    model = dataclasses.replace(spec(), net_debt=200.0)   # legacy single figure

    assert isinstance(model.net_debt, NetDebt)
    assert model.net_debt.components_verified() is False
    value = valuation.value(kernel.run(model))

    assert value.enterprise_value is None
    assert value.pe > 0                         # P/E is untouched


def test_an_undated_convertible_suppresses_forecast_eps_instead_of_diluting():
    model = spec()
    model.capital_structure.convertibles = [
        Convertible(name="CB", shares_m=8.0, conversion_date="")
    ]
    model.capital_structure.diluted_weighted_average = {}

    usable, reason = capital.validated(model)
    assert usable is False
    assert "timing is unresolved" in reason

    run = kernel.run(model)
    assert "diluted_average" not in run.row("FY26").eps

    result = status.assess(
        model_status="VERIFIED",
        basis={
            "market_cap_basis": "period_end_net",
            "forecast_eps_basis": "diluted_average",
            "dilution_sensitivity_basis": "full_conversion",
        },
        has_band=True,
        price=100.0,
        has_depreciation=True,
        has_net_debt=True,
        ev_ebitda_multiple=10.0,
        capital_verified=usable,
    )
    assert result["eps_model_status"] == "SUPPRESSED"
    assert result["valuation_method_statuses"]["ev_ebitda"] == "VERIFIED"
    assert result["ev_per_share_status"] == "SUPPRESSED"


def test_a_stated_net_that_disagrees_with_issued_minus_treasury_is_rejected():
    model = spec()
    model.capital_structure.current_net_shares = 90.0   # issued 110 - treasury 10 = 100

    with pytest.raises(Exception) as excinfo:
        kernel.run(model)

    assert "disagree" in str(excinfo.value)


def test_a_treasury_cancellation_without_a_treasury_balance_is_rejected():
    model = spec()
    model.capital_structure.treasury_shares = None
    model.capital_structure.current_net_shares = None
    model.capital_structure.treasury_cancellations = [
        {"date": "2026-05-01", "shares_m": 3.0}
    ]

    with pytest.raises(Exception) as excinfo:
        kernel.run(model)

    assert "treasury" in str(excinfo.value)


def test_a_treasury_cancellation_leaves_net_shares_unchanged():
    before, _ = capital.current_net_shares(spec().capital_structure)

    model = spec()
    model.capital_structure.treasury_cancellations = [
        {"date": "2026-05-01", "shares_m": 3.0}
    ]
    after, _ = capital.current_net_shares(model.capital_structure)

    assert after == pytest.approx(before)


def test_the_ev_per_share_denominator_is_its_own_named_basis():
    model = spec()
    model.share_bases.append(ShareBase("equity_alt", 206.0))
    model.valuation.equity_value_share_basis = "equity_alt"

    value = valuation.value(kernel.run(model))

    assert value.ev_equity_value == pytest.approx(50.0)
    assert value.ev_per_share == pytest.approx(50.0 / 206.0)


def test_an_ebitda_valuation_uses_ebitda_not_operating_profit():
    value = valuation.value(kernel.run(spec()))

    assert value.ebitda == pytest.approx(25.0)          # OP 20 + D&A 5
    assert value.enterprise_value == pytest.approx(250.0)


def test_project_timing_must_be_ordered():
    model = spec()
    model.projects = [
        InvestmentProject(
            project_name="Plant",
            capex=10.0,
            construction_start="2026-01-01",
            commissioning_start="2025-01-01",
        )
    ]

    with pytest.raises(Exception) as excinfo:
        kernel.run(model)

    assert "precedes" in str(excinfo.value)


def test_capex_without_a_commissioning_date_is_a_warning():
    model = spec()
    model.projects = [InvestmentProject(project_name="Plant", capex=10.0)]

    result = kernel.run(model)

    assert any("commissioning date" in warning for warning in result.warnings)


def test_ev_ebitda_is_published_when_eps_is_suppressed():
    model = kernel.run(spec())
    value = valuation.value(model)

    text = compact_summary(
        model, value, include_per_share=False, include_ev_ebitda=True,
        include_ev_per_share=False,
    )

    assert "Per-share outputs suppressed" in text
    assert "EV/EBITDA" in text
    assert "enterprise value" in text
    assert "Valuation: price" not in text
    assert "EPS full_share" not in text
    assert "EPS diluted_average" not in text
    assert "per share 0" not in text
