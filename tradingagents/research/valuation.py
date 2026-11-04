"""Valuation is a multiple and a range, not a point estimate.

The reference report values the company on forward P/E with an explicit multiple
range, a sensitivity grid, and four scenarios. Every number here is derived from
the kernel's EPS and the spec's price, so a change to a forecast line moves the
valuation automatically instead of leaving a stale table behind.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import kernel
from .kernel import ModelResult
from .schemas import ModelError


@dataclass
class SensitivityRow:
    multiple: float
    implied_price: float
    change: float          # fraction, e.g. -0.127

    def to_dict(self) -> dict:
        return {"multiple": self.multiple, "implied_price": self.implied_price, "change": self.change}


@dataclass
class Valuation:
    basis: str
    period: str
    eps: float
    price: float
    pe: float
    fair_value_low: float
    fair_value_mid: float
    fair_value_high: float
    sensitivity: list[SensitivityRow]
    scenarios: list[dict]
    method_note: str
    eps_available: bool = True
    # The second, independent method: enterprise value from EBITDA.
    ev_ebitda_multiple: float | None = None
    ebitda: float | None = None
    enterprise_value: float | None = None
    ev_equity_value: float | None = None
    ev_per_share: float | None = None
    ev_period: str = ""

    @property
    def upside_to_mid(self) -> float:
        return self.fair_value_mid / self.price - 1 if self.price else 0.0

    def to_dict(self) -> dict:
        return {
            "basis": self.basis,
            "period": self.period,
            "eps": self.eps,
            "price": self.price,
            "pe": self.pe,
            "fair_value_low": self.fair_value_low,
            "fair_value_mid": self.fair_value_mid,
            "fair_value_high": self.fair_value_high,
            "sensitivity": [row.to_dict() for row in self.sensitivity],
            "scenarios": self.scenarios,
            "method_note": self.method_note,
            "eps_available": self.eps_available,
            "ev_ebitda_multiple": self.ev_ebitda_multiple,
            "ebitda": self.ebitda,
            "enterprise_value": self.enterprise_value,
            "ev_equity_value": self.ev_equity_value,
            "ev_per_share": self.ev_per_share,
            "ev_period": self.ev_period,
        }


def value(model: ModelResult) -> Valuation:
    spec = model.spec
    spec_valuation = spec.valuation
    basis = spec_valuation.fair_value_basis or spec.eps_basis
    row = _forecast_row(model)
    eps = row.eps.get(basis)
    eps_available = eps is not None
    if eps is None:
        eps = 0.0

    low = spec_valuation.fair_value_low
    high = spec_valuation.fair_value_high
    if eps_available:
        if low is None:
            low = eps * spec_valuation.fair_value_multiple_low
        if high is None:
            high = eps * spec_valuation.fair_value_multiple_high
        if low > high:
            raise ModelError(f"Fair value range is inverted: low {low:g} > high {high:g}")
    else:
        low = high = 0.0

    price = spec_valuation.price
    multiples = spec_valuation.sensitivity_multiples or [
        round(spec_valuation.fair_value_multiple_low - 3 + i, 1) for i in range(6)
    ]
    sensitivity = [
        SensitivityRow(multiple=multiple, implied_price=eps * multiple,
                       change=eps * multiple / price - 1 if price else 0.0)
        for multiple in multiples
    ] if eps_available else []

    scenarios = [
        {
            "name": scenario.name,
            "assumptions": scenario.assumptions,
            "revenue": scenario.revenue,
            "operating_profit": scenario.operating_profit,
            "eps": scenario.eps,
            "multiple": scenario.multiple,
            "target": scenario.resolved_target,
            "change": scenario.resolved_target / price - 1 if price else 0.0,
        }
        for scenario in spec.scenarios
    ]

    ev_multiple, ebitda, ev, equity, ev_per_share = _ev_ebitda(model, row, price)

    return Valuation(
        basis=basis,
        period=row.label,
        eps=eps,
        price=price,
        pe=price / eps if eps else 0.0,
        fair_value_low=low,
        fair_value_mid=(low + high) / 2,
        fair_value_high=high,
        sensitivity=sensitivity,
        scenarios=scenarios,
        method_note=spec_valuation.method_note,
        eps_available=eps_available,
        ev_ebitda_multiple=ev_multiple,
        ebitda=ebitda,
        enterprise_value=ev,
        ev_equity_value=equity,
        ev_per_share=ev_per_share,
        ev_period=row.label,
    )


def _ebitda(model: ModelResult, label: str) -> float | None:
    """Operating profit plus depreciation, or None when either is not stated."""
    try:
        row = model.row(label)
    except ModelError:
        return None
    flow = model.spec.cashflow.get(label)
    if row.operating_profit is None or flow is None:
        return None
    return row.operating_profit + flow.depreciation


def _ev_ebitda(
    model: ModelResult, row, price: float
) -> tuple[float | None, float | None, float | None, float | None, float | None]:
    """(multiple, EBITDA, enterprise value, equity value, per-share value).

    EV = multiple x EBITDA; equity = EV - net debt, once. The per-share figure
    divides by the declared equity-value share basis, so a missing share basis
    leaves it None while the enterprise value stands.

    Net debt must be derived from stated components. An absent gross debt, cash
    figure, EBITDA or multiple makes the method UNAVAILABLE — it is never
    defaulted to zero, because a zero would silently price the company as
    debt-free.
    """
    spec = model.spec
    multiple = spec.valuation.ev_ebitda_multiple
    ebitda = _ebitda(model, row.label)
    net_debt = spec.net_debt.value if spec.net_debt is not None else None
    if (
        multiple is None
        or ebitda is None
        or net_debt is None
        or spec.net_debt is None
        or not spec.net_debt.components_verified()
    ):
        return multiple, ebitda, None, None, None
    enterprise = multiple * ebitda
    equity = enterprise - net_debt
    from . import capital
    from .shares import market_cap_basis

    denominator = (spec.valuation.equity_value_share_basis or "").strip() or market_cap_basis(spec)
    try:
        shares = spec.share_base(denominator).shares_m
    except ModelError:
        shares = None
    if shares is not None and denominator == market_cap_basis(spec):
        shares = capital.market_cap_shares(spec, shares)
    per_share_value = None
    if shares and price:
        per_share_value = kernel.per_share(equity, shares, spec.unit)
    return multiple, ebitda, enterprise, equity, per_share_value


def _forecast_row(model: ModelResult):
    declared = (model.spec.valuation.fair_value_period or "").strip()
    if declared:
        return model.row(declared)
    for period in model.spec.periods:
        if period.basis == "forecast":
            return model.row(period.label)
    raise ModelError("The model has no forecast period to value")
