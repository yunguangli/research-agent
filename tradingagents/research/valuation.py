"""Valuation is a multiple and a range, not a point estimate.

The reference report values the company on forward P/E with an explicit multiple
range, a sensitivity grid, and four scenarios. Every number here is derived from
the kernel's EPS and the spec's price, so a change to a forecast line moves the
valuation automatically instead of leaving a stale table behind.
"""

from __future__ import annotations

from dataclasses import dataclass

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
        }


def value(model: ModelResult) -> Valuation:
    spec = model.spec
    spec_valuation = spec.valuation
    basis = spec_valuation.fair_value_basis or spec.eps_basis
    row = _forecast_row(model)
    eps = row.eps.get(basis)
    if eps is None:
        raise ModelError(
            f"No EPS for the valuation basis {basis!r}; add that share base or choose another basis"
        )

    low = spec_valuation.fair_value_low
    high = spec_valuation.fair_value_high
    if low is None:
        low = eps * spec_valuation.fair_value_multiple_low
    if high is None:
        high = eps * spec_valuation.fair_value_multiple_high
    if low > high:
        raise ModelError(f"Fair value range is inverted: low {low:g} > high {high:g}")

    price = spec_valuation.price
    multiples = spec_valuation.sensitivity_multiples or [
        round(spec_valuation.fair_value_multiple_low - 3 + i, 1) for i in range(6)
    ]
    sensitivity = [
        SensitivityRow(multiple=multiple, implied_price=eps * multiple,
                       change=eps * multiple / price - 1 if price else 0.0)
        for multiple in multiples
    ]

    scenarios = [
        {
            "name": scenario.name,
            "assumptions": scenario.assumptions,
            "revenue": scenario.revenue,
            "operating_profit": scenario.operating_profit,
            "eps": scenario.eps,
            "multiple": scenario.multiple,
            "target": scenario.target_price,
            "change": scenario.target_price / price - 1 if price else 0.0,
        }
        for scenario in spec.scenarios
    ]

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
    )


def _forecast_row(model: ModelResult):
    for period in model.spec.periods:
        if period.basis == "forecast":
            return model.row(period.label)
    raise ModelError("The model has no forecast period to value")
