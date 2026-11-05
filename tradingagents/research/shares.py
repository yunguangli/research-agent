"""Which share count each published figure uses, and whether they agree.

The failure this prevents: a model keeps a period-end count of 566.3m from an
older filing while the company reports 570.7m basic shares plus convertible
dilution. EPS then uses the stale denominator, market cap uses another, and the
P/E a reader checks cannot be reproduced from the two numbers printed beside it.

The rules here are deliberately explicit: a valuation period picks a *named*
base for the market cap and for the forecast EPS, and a material divergence
between them is surfaced rather than silently averaged.
"""

from __future__ import annotations

from .capital import valuation_period
from .schemas import ModelError, ModelSpec

# Beyond this relative gap, two bases are counting different things and the
# report must say which one each figure uses.
DIVERGENCE_WARN = 0.05


def share_base(spec: ModelSpec, name: str):
    """A named base, or ModelError listing the names that exist."""
    for base in spec.share_bases:
        if base.name == name:
            return base
    known = [base.name for base in spec.share_bases]
    raise ModelError(f"Unknown share base {name!r}; known: {known}")


def market_cap_basis(spec: ModelSpec) -> str:
    """The named base the market cap uses, defaulting to the EPS basis."""
    return (
        (spec.valuation.market_cap_basis or "").strip()
        or (spec.valuation.fair_value_basis or "").strip()
        or spec.eps_basis
    )


def forecast_eps_basis(spec: ModelSpec) -> str:
    """The named base a forecast EPS uses, defaulting to the spec's EPS basis."""
    return (spec.valuation.forecast_eps_basis or "").strip() or spec.eps_basis


def dilution_sensitivity_basis(spec: ModelSpec) -> str:
    """The named base for a full-conversion dilution case, or "" when undeclared.

    It must be declared explicitly: silently reusing the forecast EPS basis would
    show a dilution sensitivity that assumes no dilution.
    """
    return (spec.valuation.dilution_sensitivity_basis or "").strip()


def basis_names(spec: ModelSpec) -> dict[str, str]:
    """The published share bases, as the header prints them."""
    return {
        "market_cap_basis": market_cap_basis(spec),
        "forecast_eps_basis": forecast_eps_basis(spec),
        "dilution_sensitivity_basis": dilution_sensitivity_basis(spec),
        "equity_value_share_basis": (
            (spec.valuation.equity_value_share_basis or "").strip() or market_cap_basis(spec)
        ),
    }


def equity_value_basis(spec: ModelSpec) -> str:
    """The named base the EV per-share value divides by."""
    return (
        (spec.valuation.equity_value_share_basis or "").strip() or market_cap_basis(spec)
    )


def diluted_kind(spec: ModelSpec) -> str:
    """How the forecast EPS denominator was built.

    ``weighted_average`` when the ledger or the base states a diluted weighted
    average; ``conservative_full_conversion`` when the count is simply basic plus
    every convertible (which assumes conversion for the whole period). The
    distinction matters: a full-conversion count is a sensitivity, not the
    reported diluted EPS, and a P/E built on it is provisional.
    """
    name = forecast_eps_basis(spec)
    try:
        base = share_base(spec, name)
    except ModelError:
        return "unresolved"
    structure = spec.capital_structure
    if structure is not None:
        # The ledger resolves the period count from conversion dates and weights.
        # A period with no stated diluted average and an undated convertible
        # falls back to full conversion, which must carry that label.
        period_label = valuation_period(spec)
        if period_label and period_label in structure.diluted_weighted_average:
            return "weighted_average"
        if _timing_unresolved(structure):
            return "conservative_full_conversion"
        try:
            spec.period(period_label)
        except ModelError:
            return "unresolved"
        return "weighted_average"
    potential = base.potential_cb_shares or 0.0
    basic = base.basic_weighted_average
    if potential > 0 and basic is not None:
        expected = float(basic) + float(potential)
        count = base.diluted_weighted_average or base.shares_m
        if abs(float(count) - expected) <= max(0.05, expected * 0.0005):
            # Every convertible is counted for the whole period: that is full
            # conversion wearing the diluted-average label.
            return "conservative_full_conversion"
    if base.diluted_weighted_average is not None:
        return "weighted_average"
    return "declared"


def _timing_unresolved(structure) -> bool:
    """Whether a convertible with shares stated has no conversion date."""
    return any(
        item.shares_m is not None and not str(item.conversion_date or "").strip()
        for item in structure.convertibles
    )


def basis_report(spec: ModelSpec) -> dict[str, str]:
    """Each published basis and whether it was declared or inferred.

    ``full_share`` as the only basis is ambiguous: it does not say whether the
    count is period-end or weighted-average, so the header must not present it as
    a verified choice.
    """
    declared = {
        "market_cap_basis": (spec.valuation.market_cap_basis or "").strip(),
        "forecast_eps_basis": (spec.valuation.forecast_eps_basis or "").strip(),
        "dilution_sensitivity_basis": (spec.valuation.dilution_sensitivity_basis or "").strip(),
        "equity_value_share_basis": (spec.valuation.equity_value_share_basis or "").strip(),
    }
    resolved = basis_names(spec)
    report: dict[str, str] = {}
    for role, name in resolved.items():
        if declared[role]:
            report[role] = name
        elif name == "full_share":
            report[role] = f"{name} (fallback — not verified)"
        elif name:
            report[role] = f"{name} (fallback)"
        else:
            report[role] = "not established"
    if diluted_kind(spec) == "conservative_full_conversion":
        report["forecast_eps_basis"] = (
            f"{report['forecast_eps_basis']} (conservative full conversion)"
        )
    return report


def divergence(spec: ModelSpec) -> float | None:
    """Relative gap between the market-cap count and the EPS count, or None."""
    try:
        cap = share_base(spec, market_cap_basis(spec)).shares_m
        eps = share_base(spec, forecast_eps_basis(spec)).shares_m
    except ModelError:
        return None
    if not cap or not eps:
        return None
    return abs(cap - eps) / eps


def basis_warnings(spec: ModelSpec) -> list[str]:
    """Warnings describing a basis that is missing or materially different."""
    warnings: list[str] = []
    for role, name in (
        ("market cap", market_cap_basis(spec)),
        ("forecast EPS", forecast_eps_basis(spec)),
    ):
        try:
            base = share_base(spec, name)
        except ModelError as exc:
            warnings.append(f"The {role} names no usable share base ({exc}).")
            continue
        if base.shares_m <= 0:
            warnings.append(f"Share base {name!r} used for the {role} is not positive.")
        if role == "forecast EPS" and base.diluted_weighted_average is None and base.potential_cb_shares:
            warnings.append(
                f"Share base {name!r} carries {base.potential_cb_shares:g}m potential "
                "convertible shares but no diluted weighted average: state the diluted "
                "count, or say the EPS is basic."
            )
    gap = divergence(spec)
    if gap is not None and gap > DIVERGENCE_WARN:
        cap = share_base(spec, market_cap_basis(spec)).shares_m
        eps = share_base(spec, forecast_eps_basis(spec)).shares_m
        warnings.append(
            f"Market cap uses {cap:g}m shares and forecast EPS uses {eps:g}m "
            f"({gap:.1%} apart): the P/E printed beside them cannot be reproduced from "
            "one count. Name the basis each figure uses."
        )
    return warnings
