"""Output-level verification statuses for one run.

A model can be arithmetically verified for revenue and operating profit while its
per-share outputs are not: if the share bases are inferred (``full_share``) or a
material challenge affects EPS, then P/E and per-share fair value are not
verified even though the operating model is. Collapsing all of that into one
"VERIFIED" is what let the 16:59 report publish a controlling band built on a
fallback share count.

Statuses:

- ``operating_model_status``: the revenue / operating-profit model;
- ``eps_status``: the per-share arithmetic and the share bases behind it;
- ``valuation_status``: the fair-value band built on that EPS;
- ``sensitivity_status``: the ranked driver sensitivities.
"""

from __future__ import annotations

from typing import Any

VERIFIED = "VERIFIED"
SUPPRESSED = "SUPPRESSED"
UNAVAILABLE = "UNAVAILABLE"
UNVERIFIED = "UNVERIFIED"
NOT_APPLICABLE = "NOT_APPLICABLE"
# Validation failed (as distinct from "could not be validated"): a controlling
# component in this state is what forces REVIEW.
INVALID = "INVALID"

# Worst-first. A controlling component's worst status is the overall one.
_SEVERITY = {
    NOT_APPLICABLE: 0,
    VERIFIED: 1,
    UNAVAILABLE: 2,
    SUPPRESSED: 3,
    UNVERIFIED: 4,
    INVALID: 5,
}


def worst_status(statuses) -> str:
    """The most severe status in a collection, ignoring empty values."""
    present = [str(value).upper() for value in statuses if str(value or "").strip()]
    if not present:
        return NOT_APPLICABLE
    return max(present, key=lambda value: _SEVERITY.get(value, 1))

def basis_verified(text: str) -> bool:
    """Whether a printed basis was actually declared and verified."""
    value = str(text or "").strip().lower()
    if not value or value == "not established":
        return False
    return "fallback" not in value and "not verified" not in value


def assess(
    *,
    model_status: str,
    basis: dict[str, str],
    has_band: bool,
    challenge_fields: list[str] | None = None,
    sensitivity_count: int = 0,
    propagation_ok: bool = True,
    controlling_propagation_ok: bool = True,
    price: float | None = None,
    has_depreciation: bool = False,
    has_net_debt: bool = False,
    ev_ebitda_multiple: float | None = None,
    capital_verified: bool = True,
    eps_basis_quality: str = "",
    ev_inputs_ready: bool | None = None,
    market_cap_verified: bool | None = None,
    eps_basis_verified: bool | None = None,
    equity_basis_verified: bool | None = None,
) -> dict[str, Any]:
    """The component statuses for this run, plus what each suppression means.

    The statuses come from :mod:`dependencies`, so a defect in one component
    suppresses only the outputs that depend on it. ``eps_status`` and
    ``sensitivity_status`` are kept as aliases for callers written before the
    component split.
    """
    from . import dependencies

    statuses = dependencies.assess_outputs(
        model_status=model_status,
        basis=basis,
        price=price,
        has_depreciation=has_depreciation,
        has_net_debt=has_net_debt,
        pe_multiple=(
            None if not has_band
            else 1.0   # the band exists; its multiples are the input, not a number here
        ),
        ev_ebitda_multiple=ev_ebitda_multiple,
        challenge_fields=challenge_fields,
        capital_verified=capital_verified,
        ev_inputs_ready=ev_inputs_ready,
        market_cap_verified=market_cap_verified,
        eps_basis_verified=eps_basis_verified,
        equity_basis_verified=equity_basis_verified,
    )
    operating = statuses["operating_forecast"]
    eps = statuses["forecast_eps"]
    methods = {
        "pe": statuses["pe_valuation"],
        "ev_ebitda": statuses["ev_ebitda"],
    }
    verified_methods = [name for name, value in methods.items() if value == dependencies.VERIFIED]
    if verified_methods:
        valuation = dependencies.VERIFIED
    elif any(value == dependencies.SUPPRESSED for value in methods.values()):
        valuation = dependencies.SUPPRESSED
    else:
        valuation = dependencies.UNAVAILABLE

    sensitivities = dependencies.UNAVAILABLE
    controlling_sensitivities = dependencies.UNAVAILABLE
    if sensitivity_count > 0 and eps == dependencies.VERIFIED and valuation == dependencies.VERIFIED:
        sensitivities = (
            dependencies.VERIFIED if propagation_ok else dependencies.UNVERIFIED
        )
        controlling_sensitivities = (
            dependencies.VERIFIED
            if controlling_propagation_ok else dependencies.UNVERIFIED
        )

    reasons: dict[str, str] = {}
    if operating != dependencies.VERIFIED:
        reasons["eps"] = "the operating model is not verified"
        reasons["valuation"] = "no verified operating model"
    elif eps != dependencies.VERIFIED:
        reasons["eps"] = (
            "the forecast EPS and P/E share bases are not verified"
            if not (basis_verified(basis.get("market_cap_basis", ""))
                    and basis_verified(basis.get("forecast_eps_basis", "")))
            else "an unresolved challenge blocks forecast EPS"
        )
        reasons["valuation"] = "the fair value depends on an unverified per-share basis"
    elif valuation != dependencies.VERIFIED:
        reasons["valuation"] = "no valuation method has all its inputs verified"
    if sensitivities != dependencies.VERIFIED:
        reasons["sensitivity"] = (
            "no driver has a verified assumption and alternative"
            if valuation == dependencies.VERIFIED
            else "sensitivities need a verified valuation method"
        )

    return {
        "operating_model_status": operating,
        "eps_model_status": eps,
        "eps_status": eps,                       # alias
        "eps_basis_quality": eps_basis_quality,
        "valuation_method_statuses": methods,
        "ev_per_share_status": statuses["ev_per_share"],
        "valuation_status": valuation,
        "sensitivity_model_status": sensitivities,
        "sensitivity_status": sensitivities,     # alias
        "controlling_sensitivity_status": controlling_sensitivities,
        "valuation_controlling": "pe" in verified_methods,
        "verified_methods": verified_methods,
        "reasons": reasons,
    }
