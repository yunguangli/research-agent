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
import os
import re
from dataclasses import dataclass, field

from . import kernel, valuation as valuation_module
from .schemas import Line, ModelError, ModelSpec


class SensitivityError(ValueError):
    """A cited sensitivity id that does not resolve to one ranked item."""

    def __init__(self, reason: str, code: str = "SENSITIVITY_FIELD_NOT_FOUND", **context):
        super().__init__(reason)
        self.code = code
        self.context = context


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
    valuation_method: str = ""
    verification_status: str = "VERIFIED"
    # Stable keys behind the display id: the bridge the shock moves and the
    # assumption line inside it. Legacy driver slugs are aliases, never ids.
    bridge_id: str = ""
    model_field_id: str = ""
    aliases: list[str] = field(default_factory=list)
    # The magnitude top-N set, fixed before verification and before the display
    # reorder. A failed row keeps its candidacy for the audit table.
    controlling_candidate: bool = False

    @property
    def decision_eligible(self) -> bool:
        return self.verification_status == "VERIFIED"

    def to_dict(self) -> dict:
        return {
            "sensitivity_id": self.sensitivity_id,
            "driver": self.driver,
            "model_field": self.model_field,
            "bridge_id": self.bridge_id,
            "model_field_id": self.model_field_id,
            "aliases": list(self.aliases),
            "controlling_candidate": self.controlling_candidate,
            "decision_eligible": self.decision_eligible,
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
            "valuation_method": self.valuation_method,
            "verification_status": self.verification_status,
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


def _feature(name: str) -> bool:
    """Environment-only rollout flag; never a config key."""
    value = os.environ.get(f"TRADINGAGENTS_FEATURE_{name}", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _declares_zero_pass_through(line) -> bool:
    """Whether the bridge line explicitly states a zero pass-through."""
    return "zero pass-through" in str(getattr(line, "formula", "") or "").lower()


def _propagation_failure(item: Sensitivity, metric: str, line) -> str:
    """The first stage a shock failed to reach, or "" when the chain is whole.

    A change at one level that leaves the next level untouched is a broken
    propagation, not a sensitivity. ``None`` means the stage is suppressed by
    the verification status, which is a different, already-reported condition.
    """
    stages = {
        "revenue": (
            ("operating-profit", item.operating_profit_impact),
            ("net-income", item.net_income_impact),
            ("EPS", item.eps_impact),
            ("fair-value", item.fair_value_impact),
        ),
        "operating_profit": (
            ("net-income", item.net_income_impact),
            ("EPS", item.eps_impact),
            ("fair-value", item.fair_value_impact),
        ),
        "net_profit": (
            ("EPS", item.eps_impact),
            ("fair-value", item.fair_value_impact),
        ),
    }.get(metric, ())
    for stage, value in stages:
        if value is None:
            continue
        if abs(value) <= 1e-9 and not _declares_zero_pass_through(line):
            return stage
    return ""


def _propagation_path(metric: str) -> list[str]:
    """The chain a shock travels, so the reader can audit the arithmetic."""
    if metric == "operating_profit":
        return ["operating_profit", "pretax_profit", "tax", "net_income", "eps", "fair_value"]
    if metric == "net_profit":
        return ["net_income", "eps", "fair_value"]
    if metric == "revenue":
        return ["revenue", "operating_profit", "net_income", "eps", "fair_value"]
    return [metric, "fair_value"]


def _shift_net_profit(shock, base_model, spec, period: str, operating_delta: float,
                      mode: str = "carry") -> None:
    """Carry an operating-profit move to net profit through the model's own tax.

    A model with its own net-profit bridge still has to move when a driver moves:
    returning early left operating profit shocked and net income untouched, so
    the ranked table showed a non-zero OP effect with zero EPS and fair value.
    The bridge is rebased with one explicit pass-through line, keeping its other
    lines and provenance intact.
    """
    if not _feature("SENSITIVITY_INVARIANTS_V1") and any(
        bridge.metric == "net_profit" for bridge in shock.bridges
    ):
        return                              # flag off: the historical behaviour
    row = next((candidate for candidate in base_model.rows if candidate.label == period), None)
    financials = shock.forecast.get(period)
    if (
        row is None
        or not row.operating_profit
        or financials is None
        or financials.net_profit is None
    ):
        return
    tax = spec.effective_tax_rate
    if tax is None and row.ordinary_profit and row.net_profit:
        tax = 1 - row.net_profit / row.ordinary_profit
    if tax is not None:
        net_delta = operating_delta * (1 - float(tax))
    else:
        net_delta = operating_delta * (row.net_profit / row.operating_profit)
    financials.net_profit += net_delta
    # A model with its own net-profit bridge must move with the level it
    # explains, or the kernel rejects the shock as an internal contradiction.
    incoming = next(
        (
            bridge
            for bridge in shock.bridges
            if bridge.metric == "net_profit" and bridge.target_period == period
        ),
        None,
    )
    if incoming is not None:
        base_period = incoming.base_period
        prior_shock = shock.forecast.get(base_period)
        prior_base = next(
            (candidate for candidate in base_model.rows if candidate.label == base_period),
            None,
        )
        already_shifted = (
            prior_shock is not None
            and prior_shock.net_profit is not None
            and prior_base is not None
            and prior_base.net_profit is not None
            and abs(
                (prior_shock.net_profit - prior_base.net_profit) - net_delta
            ) <= 1e-6
        )
        if not already_shifted:
            incoming.lines.append(
                Line(
                    label=f"operating-profit sensitivity pass-through {period}",
                    value=net_delta,
                    provenance="derived",
                    formula="operating-profit shock x (1 - effective tax rate)",
                )
            )
        if incoming.declared_total is not None:
            incoming.declared_total += net_delta
    # The next period's bridge starts from this period's net profit, so it has
    # to move too — otherwise the kernel sees a stale starting level. In
    # ``carry`` mode this walk is transitive (the caller will not revisit the
    # later periods); in ``per_period`` mode the caller applies each later
    # period's own delta, so only the immediate next period is carried here.
    current = period
    while True:
        later = next(
            (
                bridge
                for bridge in shock.bridges
                if bridge.metric == "net_profit" and bridge.base_period == current
            ),
            None,
        )
        if later is None:
            break
        later.base_value += net_delta
        if later.declared_total is not None:
            later.declared_total += net_delta
        later_financials = shock.forecast.get(later.target_period)
        if later_financials is not None and later_financials.net_profit is not None:
            later_financials.net_profit += net_delta
        later_flow = shock.cashflow.get(later.target_period)
        if later_flow is not None and later_flow.net_profit is not None:
            later_flow.net_profit += net_delta
        if mode != "carry":
            break
        current = later.target_period
    # The cash-flow statement restates net profit; leaving it behind makes the
    # kernel reject the shock as an internal contradiction.
    flow = shock.cashflow.get(period)
    if flow is not None and flow.net_profit is not None:
        flow.net_profit += net_delta


def _operating_delta_from_revenue(base_model, period: str, revenue_delta: float) -> float:
    """A revenue shock reaches operating profit at the model's own incremental margin."""
    spec = base_model.spec
    revenue = spec.bridge("revenue", period)
    operating = spec.bridge("operating_profit", period)
    if revenue is None or operating is None:
        return 0.0
    if revenue.declared_total is None or operating.declared_total is None:
        return 0.0
    revenue_change = revenue.declared_total - revenue.base_value
    operating_change = operating.declared_total - operating.base_value
    if abs(revenue_change) < 1e-9:
        return 0.0
    return revenue_delta * operating_change / revenue_change


def _apply_shift(shock, base_model, spec, metric: str, period: str, delta: float,
                 follow: bool = True, net_mode: str = "carry") -> None:
    """Move one period's level and every later period built on it.

    A bridge is a decomposition of a level, not an isolated fact: if FY3/27
    operating profit falls by 6, the FY3/28 bridge starts from 6 less and its
    own deltas land 6 lower. Without this the kernel rejects every later shock
    as an arithmetic error and the driver silently disappears.
    """
    visited: set[str] = set()
    while period and period not in visited:
        first = not visited
        visited.add(period)
        financials = shock.forecast.get(period)
        if financials is not None and getattr(financials, metric) is not None:
            setattr(financials, metric, getattr(financials, metric) + delta)
        # The bridge that builds this period's level must move with it, or the
        # kernel sees a stated figure its own decomposition cannot reproduce.
        incoming = next(
            (
                bridge
                for bridge in shock.bridges
                if bridge.metric == metric and bridge.target_period == period
            ),
            None,
        )
        if incoming is not None and incoming.declared_total is not None:
            incoming.declared_total += delta
        if metric == "operating_profit":
            # The first period carries the net-profit level through every later
            # period; the later iterations of a level shift must not repeat it.
            if first:
                _shift_net_profit(shock, base_model, spec, period, delta, mode=net_mode)
        elif metric == "revenue" and (
            first or not _feature("SENSITIVITY_INVARIANTS_V1")
        ):
            # The shock is a one-year move: the later periods carry the same
            # level through the operating-profit walk, not a second pass-through.
            operating_delta = _operating_delta_from_revenue(base_model, period, delta)
            if operating_delta:
                # The operating-profit bridge must still tie: the revenue shock
                # lands as one explicit pass-through line rather than silently
                # changing an existing driver's value.
                operating_bridge = next(
                    (
                        bridge
                        for bridge in shock.bridges
                        if bridge.metric == "operating_profit"
                        and bridge.target_period == period
                    ),
                    None,
                )
                if operating_bridge is not None:
                    operating_bridge.lines.append(
                        Line(
                            label=f"revenue pass-through {period}",
                            value=operating_delta,
                            provenance="derived",
                            formula="revenue shock x the model's own incremental operating margin",
                            driver="revenue pass-through",
                            alternative=operating_delta,
                        )
                    )
                # The revenue loop already visits every later period, so the
                # operating-profit pass-through must not walk them again: that
                # applied the same shock twice and broke the net-profit bridge.
                _apply_shift(
                    shock, base_model, spec, "operating_profit", period, operating_delta,
                    follow=True, net_mode="carry",
                )
        later = next(
            (
                bridge
                for bridge in shock.bridges
                if bridge.metric == metric and bridge.base_period == period
            ),
            None,
        )
        if later is not None and (
            follow or _feature("SENSITIVITY_INVARIANTS_V1")
        ):
            # The next bridge starts from this period's level even when this
            # call does not walk into it; a stale base fails the kernel.
            later.base_value += delta
        if not follow or later is None:
            break
        period = later.target_period


def rank(
    spec: ModelSpec, top: int = 5, *, eps_verified: bool = True, pe_verified: bool = True
) -> tuple[list[Sensitivity], list[str]]:
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
                if line.driver:
                    gaps.append(
                        f"RESEARCH_GAP_NO_PROPAGATION: {line.label} has a driver but no "
                        "alternative assumption, so no path to EPS or fair value exists"
                    )
                elif abs(line.value) > 0:
                    gaps.append(f"RESEARCH_GAP_NO_PROPAGATION: {line.label} names no driver")
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
            # The shock changes a level, so every later period built on it moves
            # too: rebase each downstream bridge and carry the delta forward.
            _apply_shift(shock, base_model, spec, bridge.metric, bridge.target_period, delta)
            try:
                model = kernel.run(shock)
                value = valuation_module.value(model)
            except ModelError as exc:
                # A shock that cannot be recomputed is a named failure, never a
                # silent zero: the driver would otherwise look irrelevant.
                gaps.append(
                    f"SENSITIVITY_PROPAGATION_FAILED: {line.label}: the shock could "
                    f"not be recomputed ({exc})"
                )
                continue
            row = next((r for r in model.rows if r.label == base_valuation.period), None)
            if row is None:
                continue
            # A display string is not a key. The id names the bridge and the
            # assumption line; the driver slug is kept only as a legacy alias,
            # so a review written before this change still resolves.
            bridge_id = f"{_slug(bridge.metric)}_{_slug(bridge.target_period)}"
            model_field_id = _slug(line.label)
            sensitivity_id = f"{bridge_id}_{model_field_id}"
            if sensitivity_id in seen:
                sensitivity_id = f"{sensitivity_id}_{len(seen)}"
            seen.add(sensitivity_id)
            legacy = _slug(line.driver)
            aliases = [legacy]
            if legacy != model_field_id:
                aliases.append(f"{legacy}_{_slug(line.label)}")
            base_eps = base_row.eps.get(basis) if base_row else None
            new_eps = row.eps.get(basis)
            fair_value = (
                new_eps * multiple_mid
                if new_eps is not None and multiple_mid
                else _midpoint(value)
            )
            reported_eps = new_eps if eps_verified else None
            reported_fair = fair_value if (eps_verified and pe_verified) else None
            net_income_impact = _delta(
                base_row.net_profit if base_row else None, row.net_profit
            )
            item = Sensitivity(
                    sensitivity_id=sensitivity_id,
                    driver=line.driver,
                    model_field=f"{bridge.metric} bridge {bridge.target_period}: {line.label}",
                    base_assumption=line.value,
                    alternative_assumption=line.alternative,
                    revenue_impact=_delta(base_row.revenue if base_row else None, row.revenue),
                    operating_profit_impact=_delta(
                        base_row.operating_profit if base_row else None, row.operating_profit
                    ),
                    eps_impact=_delta(base_eps, reported_eps) if eps_verified else None,
                    fair_value_impact=(
                        _truncate(fair_value - base_fair) if reported_fair is not None else None
                    ),
                    pretax_profit_impact=_delta(
                        base_row.ordinary_profit if base_row else None, row.ordinary_profit
                    ),
                    net_income_impact=net_income_impact,
                    # D&A and working capital are unchanged by the shock, so the
                    # cash-flow move equals the net-income move.
                    cash_flow_impact=net_income_impact,
                    propagation_path=_propagation_path(bridge.metric),
                    evidence_refs=[c.document_id for c in line.cite],
                    valuation_method=(
                        "pe" if (eps_verified and pe_verified) else "unavailable"
                    ),
                    verification_status=(
                        "VERIFIED" if (eps_verified and pe_verified) else "SUPPRESSED"
                    ),
                    bridge_id=bridge_id,
                    model_field_id=model_field_id,
                    aliases=[alias for alias in aliases if alias and alias != sensitivity_id],
                )
            if _feature("SENSITIVITY_INVARIANTS_V1"):
                failure = _propagation_failure(item, bridge.metric, line)
                if failure:
                    # Keep the row so the driver stays visible, but its zero is
                    # not a result: mark it unverified and render it unavailable.
                    item.verification_status = "UNVERIFIED"
                    item.valuation_method = "unavailable"
                    gaps.append(
                        f"SENSITIVITY_PROPAGATION_FAILED: {line.driver or line.label}: the "
                        f"shock produced no {failure} effect"
                    )
            results.append(item)
    # A changed assumption whose outputs are all zero is not a sensitivity: it is
    # a broken propagation. Record it as a gap so the driver cannot look harmless.
    surviving: list[Sensitivity] = []
    for item in results:
        if item.verification_status == "UNVERIFIED" or has_effect(item):
            surviving.append(item)
        else:
            gaps.append(
                f"SENSITIVITY_PROPAGATION_FAILED: {item.driver}: the shock produced no "
                "revenue, profit, EPS or fair-value change"
            )
    # 1. Controlling candidates: magnitude top-N, fixed before verification.
    ordered = mark_controlling(surviving, top)
    # 2. Display order: decision-eligible first, then magnitude, stable ties.
    published = sorted(
        ordered,
        key=lambda item: (
            not item.decision_eligible,
            -abs(item.fair_value_impact or 0),
            item.sensitivity_id,
        ),
    )
    return published, gaps


def mark_controlling(items: list[Sensitivity], top: int) -> list[Sensitivity]:
    """Flag the magnitude top-N before any verification-aware reordering.

    A failed row keeps its candidacy: replacing it with the next verified row
    would hide that the intended controlling evidence is incomplete.
    """
    ordered = sorted(
        items,
        key=lambda item: (-abs(item.fair_value_impact or 0), item.sensitivity_id),
    )
    for position, item in enumerate(ordered):
        item.controlling_candidate = position < top
    return ordered


def alias_map(items: list[Sensitivity]) -> dict[str, str]:
    """Legacy id → canonical id, for reviews written before the key change.

    An alias that would map to more than one item is dropped rather than
    guessed: two different assumptions sharing a display slug is exactly the
    ambiguity that made the old ids unstable.
    """
    counts: dict[str, list[str]] = {}
    for item in items:
        for alias in item.aliases:
            counts.setdefault(alias, []).append(item.sensitivity_id)
    return {
        alias: ids[0] for alias, ids in counts.items()
        if len(set(ids)) == 1 and alias not in {item.sensitivity_id for item in items}
    }


def canonical_id(value: str, items: list[Sensitivity]) -> str:
    """The canonical id for a cited value; raises when it names nothing ranked.

    A legacy alias resolves to its canonical id — the caller records the
    canonical value, so an alias never becomes a second sensitivity.
    """
    value = str(value or "").strip()
    for item in items:
        if value == item.sensitivity_id:
            return value
    mapping = alias_map(items)
    if value in mapping:
        return mapping[value]
    raise SensitivityError(
        f"SENSITIVITY_FIELD_NOT_FOUND: {value!r} names no ranked sensitivity",
        value=value,
    )


def render(items: list[Sensitivity]) -> str:
    """A compact ranked table for the package and the reviewer."""
    if not items:
        return ""
    lines = [
        "Ranked sensitivities (verified model, largest fair-value impact first):",
        "| ID | Driver | Base | Alternative | Δ revenue | Δ operating profit | "
        "Δ net profit | Δ EPS | Δ fair value | Status |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in items:
        if not item.decision_eligible:
            lines.append(
                f"| {item.sensitivity_id} | {item.driver} | {_fmt(item.base_assumption)} | "
                f"{_fmt(item.alternative_assumption)} | unavailable | unavailable | "
                f"unavailable | unavailable | unavailable | {item.verification_status} |"
            )
            continue
        lines.append(
            f"| {item.sensitivity_id} | {item.driver} | {_fmt(item.base_assumption)} | "
            f"{_fmt(item.alternative_assumption)} | {_fmt(item.revenue_impact)} | "
            f"{_fmt(item.operating_profit_impact)} | {_fmt(item.net_income_impact)} | "
            f"{_fmt(item.eps_impact)} | {_fmt(item.fair_value_impact)} | VERIFIED |"
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
