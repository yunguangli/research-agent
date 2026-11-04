"""Share counts: current net shares, per-period weighted averages, dilution.

The model's three published bases name *which* count is used; this module resolves
the number. A capital structure is optional: when absent the named ``ShareBase``
entries remain the source of truth, exactly as before. When present, current net
shares are stated (issued less treasury, adjusted by post-period issuance and
buybacks) and forecast EPS uses the period's weighted average, adding a
convertible only once it can convert.
"""

from __future__ import annotations

from .schemas import CapitalStructure, Convertible, ModelSpec

VERIFIED = "VERIFIED"


def verified(spec: ModelSpec) -> bool:
    """Whether the capital structure may supply published share counts."""
    structure = spec.capital_structure
    if structure is None:
        return True                       # the named bases are the stated source
    return str(structure.verification_status or "").upper() == VERIFIED


def _event_shares(event: dict) -> float | None:
    try:
        return float(event.get("shares_m"))
    except (TypeError, ValueError, AttributeError):
        return None


def current_net_shares(structure: CapitalStructure | None) -> tuple[float | None, str]:
    """(shares in millions, why not) for the market-cap basis."""
    if structure is None:
        return None, "no capital structure"
    base = structure.current_net_shares
    if base is None and structure.issued_shares is not None:
        base = structure.issued_shares - (structure.treasury_shares or 0.0)
    if base is None:
        return None, "issued and treasury shares are not stated"
    for event in structure.post_period_issuances:
        shares = _event_shares(event)
        if shares is not None:
            base += shares
    for event in structure.buybacks:
        shares = _event_shares(event)
        if shares is not None:
            base -= shares
    # A treasury cancellation removes issued and treasury shares in equal measure,
    # so net shares do not move; the event is kept for the audit trail.
    return base, ""


def _convertible_shares(structure: CapitalStructure, period_end: str) -> tuple[float, str]:
    """(shares from convertibles that can convert by period end, why not)."""
    total = 0.0
    for item in structure.convertibles:
        if item.shares_m is None:
            # A conversion price without a share count cannot be resolved here.
            if item.conversion_price is not None:
                return total, f"{item.name or 'convertible'} has no share count"
            continue
        if not item.conversion_date:
            # Eligibility is not verification: without a conversion date the
            # timing (and therefore the diluted count) is unresolved.
            return total, f"{item.name or 'convertible'} conversion timing is unresolved"
        if period_end and item.conversion_date > period_end:
            continue
        total += item.shares_m
    return total, ""


def _split_factor_after(structure: CapitalStructure, period_end: str) -> float:
    """Restate a pre-split weighted average onto today's share basis."""
    factor = 1.0
    if not period_end:
        return factor
    for event in structure.split_history:
        date = str(event.get("date") or "")
        try:
            ratio = float(event.get("factor"))
        except (TypeError, ValueError):
            continue
        if date and date > period_end and ratio > 0:
            factor *= ratio
    return factor


def forecast_shares(
    structure: CapitalStructure | None, period: str, period_end: str = ""
) -> tuple[float | None, str]:
    """(weighted-average diluted shares for ``period``, why not)."""
    if structure is None:
        return None, "no capital structure"
    factor = _split_factor_after(structure, period_end)
    diluted = structure.diluted_weighted_average.get(period)
    if diluted is not None:
        return float(diluted) * factor, ""
    basic = structure.basic_weighted_average.get(period)
    if basic is None:
        return None, f"no weighted-average share count for {period}"
    convertibles, reason = _convertible_shares(structure, period_end)
    if reason:
        return None, reason
    return (float(basic) + convertibles) * factor, ""


def dilution_shares(structure: CapitalStructure | None) -> tuple[float | None, str]:
    """(full-conversion shares for the dilution sensitivity, why not)."""
    if structure is None:
        return None, "no capital structure"
    net, reason = current_net_shares(structure)
    if net is None:
        return None, reason
    potential = sum(
        item.shares_m for item in structure.convertibles if item.shares_m is not None
    )
    return net + potential, ""


def market_cap_shares(spec: ModelSpec, fallback: float | None = None) -> float | None:
    """The count the market cap uses: the structure's net shares, else the named base."""
    value, _reason = current_net_shares(spec.capital_structure)
    if value is not None:
        return value
    return fallback


def convertible_list(structure: CapitalStructure | None) -> list[Convertible]:
    return list(structure.convertibles) if structure else []


def validated(spec: ModelSpec) -> tuple[bool, str]:
    """Whether every forecast period has a resolved, verified share count."""
    if not verified(spec):
        return False, "the capital structure is not verified"
    structure = spec.capital_structure
    if structure is None:
        return True, ""
    for period in spec.periods:
        if period.basis != "forecast":
            continue
        value, reason = forecast_shares(structure, period.label, period.end)
        if value is None:
            return False, reason
    return True, ""


def validate(structure: CapitalStructure | None) -> list[str]:
    """Invariants a stated capital structure must satisfy.

    A treasury cancellation removes issued and treasury shares in equal measure,
    so net shares cannot move; a stated current net that disagrees with
    ``issued - treasury`` means one of the three numbers is wrong.
    """
    if structure is None:
        return []
    issues: list[str] = []
    if (
        structure.issued_shares is not None
        and structure.treasury_shares is not None
        and structure.current_net_shares is not None
    ):
        expected = structure.issued_shares - structure.treasury_shares
        if abs(structure.current_net_shares - expected) > 0.5:
            issues.append(
                f"current net shares {structure.current_net_shares:g} disagree with "
                f"issued {structure.issued_shares:g} minus treasury "
                f"{structure.treasury_shares:g} ({expected:g})"
            )
    for event in structure.treasury_cancellations:
        shares = _event_shares(event)
        if not shares or shares <= 0:
            issues.append("a treasury cancellation has no positive share count")
            continue
        if structure.treasury_shares is None:
            issues.append(
                "a treasury cancellation is stated without a treasury balance; "
                "net shares cannot be verified"
            )
    return issues
