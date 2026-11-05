"""Share counts: current net shares, per-period weighted averages, dilution.

The model's three published bases name *which* count is used; this module resolves
the number. A capital structure is optional: when absent the named ``ShareBase``
entries remain the source of truth, exactly as before. When present, current net
shares are stated (issued less treasury, adjusted by post-period issuance and
buybacks) and forecast EPS uses the period's weighted average, adding a
convertible only once it can convert.
"""

from __future__ import annotations

import re
from datetime import date

from .schemas import CapitalStructure, ModelError, ModelSpec

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


def unsettled_convertibles(spec: ModelSpec) -> bool:
    """Whether the model names convertible-type instruments it cannot classify.

    The spec has no convertible-debt field, so a convertible cannot be included
    in gross debt and net debt cannot be recomputed. A builder that leaves the
    list empty but warns that the dilutive instruments were not itemized is in
    the same position: detected, not repaired. EV/EBITDA stays PROVISIONAL until
    the schema change lands.
    """
    structure = spec.capital_structure
    if structure is not None and structure.convertibles:
        return True
    return any(
        re.search(r"convertible|dilutive instrument", str(warning), re.IGNORECASE)
        for warning in (spec.warnings or [])
    )


def valuation_mode(spec: ModelSpec) -> str:
    """``debt``, ``assumed_converted`` or ``unknown`` for the convertible book.

    Mixed treatments are resolved conservatively: any debt classification wins,
    because including the instrument in debt is the stricter choice.
    """
    treatments = {
        str(item.valuation_treatment or "unknown").strip().lower()
        for item in (
            spec.capital_structure.convertibles
            if spec.capital_structure is not None else []
        )
    }
    if "debt" in treatments:
        return "debt"
    if "assumed_converted" in treatments:
        return "assumed_converted"
    return "unknown"


def treatment_conflicts(spec: ModelSpec) -> list[str]:
    """A method whose share count contradicts its convertible treatment.

    ``debt`` keeps basic/period-end shares; ``assumed_converted`` uses the
    diluted count. Mixing the two silently is never allowed.
    """
    structure = spec.capital_structure
    if structure is None or not structure.convertibles:
        return []
    from .shares import equity_value_basis, forecast_eps_basis

    mode = valuation_mode(spec)
    ev_basis = equity_value_basis(spec)
    pe_basis = forecast_eps_basis(spec)
    if mode == "debt" and ev_basis == pe_basis:
        return ["debt treatment uses the diluted denominator"]
    if mode == "assumed_converted" and ev_basis != pe_basis:
        return ["assumed conversion does not use the diluted denominator"]
    return []


def cross_method_basis(spec: ModelSpec) -> tuple[float | None, float | None, float | None]:
    """(EV per-share count, P/E count, relative gap) for the two methods."""
    from .shares import equity_value_basis, forecast_eps_basis

    try:
        ev = spec.share_base(equity_value_basis(spec)).shares_m
        pe = spec.share_base(forecast_eps_basis(spec)).shares_m
    except ModelError:
        return None, None, None
    if not ev or not pe:
        return ev, pe, None
    return ev, pe, abs(float(ev) - float(pe)) / float(pe)


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


def _convertible_shares(
    structure: CapitalStructure, period_end: str, period_start: str = ""
) -> tuple[float, str]:
    """(period-weighted shares from convertibles, why not).

    A convertible that becomes dilutive halfway through the period contributes
    its shares for the remainder of the period, not for the whole year. Using
    the full count is a conservative full-conversion assumption, which belongs
    to the dilution sensitivity, not to the reported diluted weighted average.
    """
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
        total += item.shares_m * _period_weight(
            item.conversion_date, period_start, period_end
        )
    return total, ""


def _period_weight(conversion_date: str, period_start: str, period_end: str) -> float:
    """Fraction of the period during which the converted shares are outstanding."""
    if not period_start or not period_end or conversion_date <= period_start:
        return 1.0
    start = date.fromisoformat(period_start)
    end = date.fromisoformat(period_end)
    if end <= start:
        return 1.0
    if conversion_date >= period_end:
        return 0.0
    converted = date.fromisoformat(conversion_date)
    return max(0.0, (end - converted).days / (end - start).days)


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
    structure: CapitalStructure | None, period: str, period_end: str = "",
    period_start: str = "",
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
    convertibles, reason = _convertible_shares(structure, period_end, period_start)
    if reason:
        # Timing is unresolved, so the *reported* diluted average cannot be
        # stated. Fall back to full conversion and say so: the denominator is
        # labelled ``conservative_full_conversion`` and the P/E built on it is
        # provisional, never presented as the company's diluted EPS.
        potential = sum(
            float(item.shares_m) for item in structure.convertibles
            if item.shares_m is not None
        )
        return (float(basic) + potential) * factor, reason
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


def valuation_period(spec: ModelSpec) -> str:
    """The period the valuation actually prices: declared, else the first forecast."""
    declared = (spec.valuation.fair_value_period or "").strip()
    if declared:
        return declared
    for period in spec.periods:
        if period.basis == "forecast":
            return period.label
    return ""


def eps_basis_ready(spec: ModelSpec, period_label: str = "") -> tuple[bool, str]:
    """Whether the forecast EPS count for one period can be published.

    The check is per output and per period: a stated diluted average verifies
    even when issued/treasury were inferred, and a later year without a count
    does not suppress the year the valuation prices.
    """
    structure = spec.capital_structure
    if structure is None:
        return True, ""
    label = period_label or valuation_period(spec)
    try:
        period = spec.period(label)
    except ModelError:
        return True, ""
    value, reason = forecast_shares(structure, period.label, period.end, period.start)
    if value is None:
        return False, reason
    if not verified(spec) and period.label not in structure.diluted_weighted_average:
        return (
            False,
            "the capital structure is unverified and the period count is inferred",
        )
    # A non-empty reason with a value means the count is published but is the
    # conservative full-conversion fallback: say so rather than dropping it.
    return True, reason


def market_cap_ready(spec: ModelSpec) -> tuple[bool, str]:
    """Whether the market-cap share count can be published."""
    structure = spec.capital_structure
    if structure is None:
        return True, ""
    value, reason = current_net_shares(structure)
    if value is None:
        return False, reason
    if not verified(spec) and structure.current_net_shares is None:
        return False, "the capital structure is unverified and net shares are inferred"
    return True, ""


def equity_basis_ready(spec: ModelSpec) -> tuple[bool, str]:
    """Whether the EV per-share denominator can be published."""
    return market_cap_ready(spec)


def validated(spec: ModelSpec, period_label: str = "") -> tuple[bool, str]:
    """Whether the valuation period's forecast EPS count is publishable."""
    structure = spec.capital_structure
    if structure is None:
        return True, ""
    return eps_basis_ready(spec, period_label)


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
