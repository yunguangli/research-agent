"""Valuation is a multiple and a range, not a point estimate.

The reference report values the company on forward P/E with an explicit multiple
range, a sensitivity grid, and four scenarios. Every number here is derived from
the kernel's EPS and the spec's price, so a change to a forecast line moves the
valuation automatically instead of leaving a stale table behind.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import kernel
from .kernel import ModelResult
from .schemas import ModelError


def _feature(name: str) -> bool:
    value = os.environ.get(f"TRADINGAGENTS_FEATURE_{name}", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


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
    # False when the per-share figure does not reconcile to the equity value on
    # the verified share basis; the enterprise-value result is still valid.
    ev_per_share_reconciled: bool = True
    # The P/E and EV per-share methods can use different counts. When they do,
    # the cross-method comparison is unverified even though each is valid alone.
    cross_method_share_gap: float | None = None
    cross_method_comparable: bool = True

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

    ev_multiple, ebitda, ev, equity, ev_per_share, ev_reconciled = _ev_ebitda(model, row, price)
    from . import capital

    share_gap = None
    comparable = True
    if _feature("CAPITAL_MODES_V1"):
        _ev_count, _pe_count, share_gap = capital.cross_method_basis(model.spec)
        # Different counts are not a defect in either method, but they make the
        # cross-method comparison unverified, whether or not a convertible is
        # the cause.
        comparable = not (
            (share_gap is not None and share_gap > 0.01)
            or capital.treatment_conflicts(model.spec)
        )

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
        ev_per_share_reconciled=ev_reconciled,
        cross_method_share_gap=share_gap,
        cross_method_comparable=comparable,
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


def _per_share_reconciles(
    per_share_value: float | None, shares_m: float | None, equity_value: float, unit: str
) -> bool:
    """Whether ``per_share x shares`` returns the equity value it was divided from.

    ``per_share`` converts between the model's unit and a currency amount, so the
    round trip goes through ``kernel.market_cap`` rather than a bare multiply.
    """
    if per_share_value is None or not shares_m:
        return False
    restored = kernel.market_cap(per_share_value, shares_m, unit)
    return abs(restored - equity_value) <= max(0.5, abs(equity_value) * 1e-6)


def _ev_ebitda(
    model: ModelResult, row, price: float
) -> tuple[float | None, float | None, float | None, float | None, float | None, bool]:
    """(multiple, EBITDA, EV, equity value, per-share value, per-share reconciled).

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
        return multiple, ebitda, None, None, None, True
    from . import capital
    from .shares import equity_value_basis, forecast_eps_basis, market_cap_basis

    mode = capital.valuation_mode(spec) if _feature("CAPITAL_MODES_V1") else "unknown"
    if mode == "debt" and spec.capital_structure is not None:
        face = sum(
            float(item.face_value) for item in spec.capital_structure.convertibles
            if item.face_value is not None
        )
        net_debt = net_debt + face
    enterprise = multiple * ebitda
    equity = enterprise - net_debt
    denominator = equity_value_basis(spec)
    if mode == "assumed_converted":
        # The converted shares carry the dilution, so debt excludes the face.
        denominator = forecast_eps_basis(spec)
    try:
        shares = spec.share_base(denominator).shares_m
    except ModelError:
        shares = None
    if shares is not None and denominator == market_cap_basis(spec):
        shares = capital.market_cap_shares(spec, shares)
    per_share_value = None
    reconciled = True
    if shares and price:
        per_share_value = kernel.per_share(equity, shares, spec.unit)
        # The per-share output is the only EV figure that depends on a share
        # count, so a figure that does not return its own equity value is
        # suppressed rather than published. EV/EBITDA itself stays verified.
        if not _per_share_reconciles(per_share_value, shares, equity, spec.unit):
            per_share_value = None
            reconciled = False
    return multiple, ebitda, enterprise, equity, per_share_value, reconciled


def _forecast_row(model: ModelResult):
    declared = (model.spec.valuation.fair_value_period or "").strip()
    if declared:
        return model.row(declared)
    for period in model.spec.periods:
        if period.basis == "forecast":
            return model.row(period.label)
    raise ModelError("The model has no forecast period to value")
