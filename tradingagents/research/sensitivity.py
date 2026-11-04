"""Rank the model's own assumptions by their impact on fair value.

A sensitivity is a bridge line with a stated alternative: the model already says
"price declines cost 10", so the useful question is "what if it is 20?". Only
lines that carry both a ``driver`` and an ``alternative`` can be shocked; a line
without them is a research gap, not a proxy to invent.

The engine mutates one line at a time on a copy of the spec, re-runs the kernel,
and values the result. Nothing is estimated outside the model.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from . import kernel, valuation as valuation_module
from .schemas import ModelError, ModelSpec


@dataclass
class Sensitivity:
    sensitivity_id: str
    driver: str
    model_field: str
    base_assumption: float
    alternative_assumption: float
    revenue_impact: float | None = None
    operating_profit_impact: float | None = None
    pretax_profit_impact: float | None = None
    net_income_impact: float | None = None
    cash_flow_impact: float | None = None
    eps_impact: float | None = None
    fair_value_impact: float | None = None
    propagation_path: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sensitivity_id": self.sensitivity_id,
            "driver": self.driver,
            "model_field": self.model_field,
            "base_assumption": self.base_assumption,
            "alternative_assumption": self.alternative_assumption,
            "revenue_impact": self.revenue_impact,
            "operating_profit_impact": self.operating_profit_impact,
            "pretax_profit_impact": self.pretax_profit_impact,
            "net_income_impact": self.net_income_impact,
            "cash_flow_impact": self.cash_flow_impact,
            "eps_impact": self.eps_impact,
            "fair_value_impact": self.fair_value_impact,
            "propagation_path": self.propagation_path,
            "evidence_refs": self.evidence_refs,
        }


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug or "assumption"


def _midpoint(value: valuation_module.Valuation) -> float:
    if value.fair_value_low is not None and value.fair_value_high is not None:
        return (value.fair_value_low + value.fair_value_high) / 2
    return 0.0


def has_effect(item: Sensitivity) -> bool:
    """Whether the shock moved anything a reader could act on."""
    return any(
        abs(value or 0) > 1e-9
        for value in (
            item.revenue_impact,
            item.operating_profit_impact,
            item.pretax_profit_impact,
            item.net_income_impact,
            item.cash_flow_impact,
            item.eps_impact,
            item.fair_value_impact,
        )
    )


def _propagation_path(metric: str) -> list[str]:
    """The chain a shock travels, so the reader can audit the arithmetic."""
    if metric == "operating_profit":
        return ["operating_profit", "pretax_profit", "tax", "net_income", "eps", "fair_value"]
    if metric == "net_profit":
        return ["net_income", "eps", "fair_value"]
    if metric == "revenue":
        return ["revenue", "operating_profit", "net_income", "eps", "fair_value"]
    return [metric, "fair_value"]


def rank(spec: ModelSpec, top: int = 5) -> tuple[list[Sensitivity], list[str]]:
    """``(ranked sensitivities, research gaps)`` for the spec's own assumptions."""
    try:
        base_model = kernel.run(spec)
        base_valuation = valuation_module.value(base_model)
    except ModelError:
        return [], ["the model does not tie"]
    base_row = next(
        (row for row in base_model.rows if row.label == base_valuation.period), None
    )
    basis = spec.valuation.fair_value_basis or spec.eps_basis
    base_eps = base_row.eps.get(basis) if base_row else None
    multiple_mid = (
        spec.valuation.fair_value_multiple_low + spec.valuation.fair_value_multiple_high
    ) / 2
    # Fair value is EPS x the model's own mid multiple: a declared band is a
    # static number and would show every shock as zero impact.
    base_fair = (
        base_eps * multiple_mid
        if base_eps is not None and multiple_mid
        else _midpoint(base_valuation)
    )

    results: list[Sensitivity] = []
    gaps: list[str] = []
    seen: set[str] = set()
    for bridge in spec.bridges:
        for line in bridge.lines:
            if not line.driver or line.alternative is None:
                if line.driver or abs(line.value) > 0:
                    gaps.append(line.label)
                continue
            if abs(line.alternative - line.value) < 1e-9:
                continue
            shock = copy.deepcopy(spec)
            # A group-level shock tests the assumption, not the segment
            # decomposition: keeping the old segment split would reject every
            # operating-profit move as a segment-sum error.
            shock.segments = {}
            # The declared band and scenarios are outputs of the base case; a
            # shock re-derives the band from EPS x the model's own multiples.
            shock.valuation.fair_value_low = None
            shock.valuation.fair_value_high = None
            shock.scenarios = []
            target = next(
                (b for b in shock.bridges
                 if b.metric == bridge.metric
                 and b.base_period == bridge.base_period
                 and b.target_period == bridge.target_period),
                None,
            )
            if target is None:
                continue
            changed = next((c for c in target.lines if c.label == line.label), None)
            if changed is None:
                continue
            changed.value = line.alternative
            delta = line.alternative - line.value
            # A declared bridge total must move with its line, or the kernel
            # rejects the shock as an arithmetic error rather than a scenario.
            if target.declared_total is not None:
                target.declared_total += delta
            # The target period's stated figure is the same fact; move it too.
            if bridge.metric in {"revenue", "operating_profit", "net_profit"}:
                financials = shock.forecast.get(bridge.target_period)
                current = financials.get(bridge.metric) if financials is not None else None
                if current is not None:
                    setattr(financials, bridge.metric, current + delta)
            # A pricing or loading shock hits operating profit first; without a
            # net-profit bridge of its own the model has no path to EPS, so the
            # shock would publish as all zeros. Carry it through the model's own
            # base conversion (net / operating profit) to net profit.
            if bridge.metric == "operating_profit" and not any(
                candidate.metric == "net_profit" for candidate in shock.bridges
            ):
                target_row = next(
                    (row for row in base_model.rows if row.label == bridge.target_period), None
                )
                financials = shock.forecast.get(bridge.target_period)
                if (
                    target_row is not None
                    and target_row.operating_profit
                    and financials is not None
                    and financials.net_profit is not None
                ):
                    tax = spec.effective_tax_rate
                    if tax is None and target_row.ordinary_profit and target_row.net_profit:
                        tax = 1 - target_row.net_profit / target_row.ordinary_profit
                    if tax is not None:
                        net_delta = delta * (1 - float(tax))
                    else:
                        net_delta = delta * (
                            target_row.net_profit / target_row.operating_profit
                        )
                    financials.net_profit += net_delta
            try:
                model = kernel.run(shock)
                value = valuation_module.value(model)
            except ModelError:
                continue
            row = next((r for r in model.rows if r.label == base_valuation.period), None)
            if row is None:
                continue
            sensitivity_id = _slug(line.driver)
            if sensitivity_id in seen:
                sensitivity_id = f"{sensitivity_id}_{_slug(line.label)}"
            seen.add(sensitivity_id)
            base_eps = base_row.eps.get(basis) if base_row else None
            new_eps = row.eps.get(basis)
            fair_value = (
                new_eps * multiple_mid
                if new_eps is not None and multiple_mid
                else _midpoint(value)
            )
            results.append(
                Sensitivity(
                    sensitivity_id=sensitivity_id,
                    driver=line.driver,
                    model_field=f"{bridge.metric} bridge {bridge.target_period}: {line.label}",
                    base_assumption=line.value,
                    alternative_assumption=line.alternative,
                    revenue_impact=_delta(base_row.revenue if base_row else None, row.revenue),
                    operating_profit_impact=_delta(
                        base_row.operating_profit if base_row else None, row.operating_profit
                    ),
                    eps_impact=_delta(base_eps, new_eps),
                    fair_value_impact=_truncate(fair_value - base_fair),
                    pretax_profit_impact=_delta(
                        base_row.ordinary_profit if base_row else None, row.ordinary_profit
                    ),
                    net_income_impact=_delta(
                        base_row.net_profit if base_row else None, row.net_profit
                    ),
                    cash_flow_impact=None,
                    propagation_path=_propagation_path(bridge.metric),
                    evidence_refs=[c.document_id for c in line.cite],
                )
            )
    # A changed assumption whose outputs are all zero is not a sensitivity: it is
    # a broken propagation, and publishing it would let a driver look irrelevant.
    results = [item for item in results if has_effect(item)]
    results.sort(key=lambda item: abs(item.fair_value_impact or 0), reverse=True)
    return results[:top], gaps


def render(items: list[Sensitivity]) -> str:
    """A compact ranked table for the package and the reviewer."""
    if not items:
        return ""
    lines = [
        "Ranked sensitivities (verified model, largest fair-value impact first):",
        "| ID | Driver | Base | Alternative | Δ operating profit | Δ EPS | Δ fair value |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in items:
        lines.append(
            f"| {item.sensitivity_id} | {item.driver} | {_fmt(item.base_assumption)} | "
            f"{_fmt(item.alternative_assumption)} | {_fmt(item.operating_profit_impact)} | "
            f"{_fmt(item.eps_impact)} | {_fmt(item.fair_value_impact)} |"
        )
    lines.append(
        "These are the model's own assumptions shocked one at a time. A driver with no "
        "explicit assumption in the model has no sensitivity, and is reported as a "
        "research gap rather than estimated."
    )
    return "\n".join(lines)


def _delta(before: float | None, after: float | None) -> float | None:
    if before is None or after is None:
        return None
    return _truncate(after - before)


def _truncate(value: float) -> float:
    return round(value, 2)


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:+,.2f}"
