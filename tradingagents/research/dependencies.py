"""Declarative output dependencies, and the status each output earns.

An output is verified only when every input it needs is verified. Statuses are
computed here, once, by propagating from the inputs a run actually has; no agent
declares an output verified, and no component is invalidated by a failure in a
sibling it does not depend on (P/E vs EV/EBITDA is the canonical case).

Statuses:

- VERIFIED: every dependency passed;
- UNVERIFIED: an input exists but has not been validated;
- SUPPRESSED: computed, but publication and decision use are prohibited;
- UNAVAILABLE: a required input or calculation was not produced;
- NOT_APPLICABLE: the output does not apply to this company or method;
- INVALID: validation failed (distinct from "could not be validated").
"""

from __future__ import annotations

from typing import Literal

VerificationStatus = Literal[
    "VERIFIED", "UNVERIFIED", "SUPPRESSED", "UNAVAILABLE", "NOT_APPLICABLE", "INVALID",
    "NOT_TESTED",
]

VERIFIED: VerificationStatus = "VERIFIED"
UNVERIFIED: VerificationStatus = "UNVERIFIED"
SUPPRESSED: VerificationStatus = "SUPPRESSED"
UNAVAILABLE: VerificationStatus = "UNAVAILABLE"
NOT_APPLICABLE: VerificationStatus = "NOT_APPLICABLE"
NOT_TESTED: VerificationStatus = "NOT_TESTED"
INVALID: VerificationStatus = "INVALID"

# output -> the inputs it needs. Keep it small: this is the publication contract,
# not a model of the accounting.
DEPENDENCIES: dict[str, frozenset[str]] = {
    "operating_forecast": frozenset({"kernel_identity"}),
    "forecast_net_income": frozenset({"operating_forecast"}),
    "forecast_eps": frozenset({"forecast_net_income", "forecast_eps_share_basis"}),
    "market_cap": frozenset({"share_price", "market_cap_share_basis"}),
    "ebitda": frozenset({"operating_forecast", "depreciation"}),
    "net_debt": frozenset({"gross_debt", "cash", "lease_liabilities"}),
    "pe_valuation": frozenset({"forecast_eps", "pe_multiple"}),
    # A target enterprise value needs the multiple, EBITDA and net debt only:
    # a missing market-cap share basis must not suppress it.
    "ev_ebitda": frozenset({"ebitda", "net_debt", "ev_ebitda_multiple"}),
    # The per-share target is the only EV output that needs a share basis.
    "ev_per_share": frozenset({"ev_ebitda", "equity_value_share_basis"}),
    "per_share_target": frozenset({"forecast_eps"}),
}

# Which per-share outputs a challenge blocks, by the fields it names. Kept
# deliberately coarse: a challenge about shares or EPS cannot be allowed to leave
# EV/EBITDA suppressed.
_EPS_FIELDS = ("eps", "share", "per-share", "per share", "p/e", "dilut")
_VALUATION_FIELDS = ("valuation", "fair value", "target", "multiple")
_ENTERPRISE_FIELDS = ("ebitda", "net debt", "enterprise value", "ev/")

def _normalise(status: str) -> VerificationStatus:
    value = str(status or "").upper()
    if value in {VERIFIED, UNVERIFIED, SUPPRESSED, UNAVAILABLE, NOT_APPLICABLE, NOT_TESTED}:
        return value  # type: ignore[return-value]
    return UNAVAILABLE


def _topological() -> list[str]:
    order: list[str] = []
    seen: set[str] = set()
    visiting: set[str] = set()

    def visit(node: str) -> None:
        if node in seen:
            return
        if node in visiting:
            raise ValueError(f"dependency cycle at {node!r}")
        visiting.add(node)
        for dep in DEPENDENCIES.get(node, frozenset()):
            if dep in DEPENDENCIES:
                visit(dep)
        visiting.discard(node)
        seen.add(node)
        order.append(node)

    for node in DEPENDENCIES:
        visit(node)
    return order


def blocked_outputs(challenge_fields: list[str] | None) -> set[str]:
    """The outputs an unresolved per-share challenge suppresses."""
    fields = " ".join(str(f).lower() for f in (challenge_fields or []))
    blocked: set[str] = set()
    if any(token in fields for token in _EPS_FIELDS):
        blocked.update({"forecast_eps", "pe_valuation", "per_share_target"})
    if any(token in fields for token in _VALUATION_FIELDS):
        blocked.add("pe_valuation")
    if any(token in fields for token in _ENTERPRISE_FIELDS):
        blocked.add("ev_ebitda")
    return blocked


def propagate(
    inputs: dict[str, VerificationStatus],
    blocked: set[str] | None = None,
) -> dict[str, VerificationStatus]:
    """Fill every output from its inputs; ``blocked`` nodes are suppressed."""
    statuses: dict[str, VerificationStatus] = {
        key: _normalise(value) for key, value in inputs.items()
    }
    for node in blocked or set():
        if node in DEPENDENCIES:
            statuses[node] = SUPPRESSED
    for node in _topological():
        if node in statuses:
            continue
        deps = DEPENDENCIES[node]
        if any(dep not in statuses for dep in deps):
            statuses[node] = UNAVAILABLE
            continue
        if any(statuses[dep] in (UNAVAILABLE, NOT_APPLICABLE) for dep in deps):
            statuses[node] = UNAVAILABLE
        elif any(statuses[dep] != VERIFIED for dep in deps):
            statuses[node] = SUPPRESSED
        else:
            statuses[node] = VERIFIED
    return statuses


def input_statuses(
    *,
    model_status: str,
    basis: dict[str, str],
    price: float | None,
    has_depreciation: bool,
    has_net_debt: bool,
    pe_multiple: float | None,
    ev_ebitda_multiple: float | None,
    capital_verified: bool = True,
    ev_inputs_ready: bool | None = None,
    market_cap_verified: bool | None = None,
    eps_basis_verified: bool | None = None,
    equity_basis_verified: bool | None = None,
    share_price_verified: bool | None = None,
) -> dict[str, VerificationStatus]:
    """The leaf inputs this run has, as statuses the engine can consume.

    ``share_price_verified`` is the source-authority verdict: a price stored in
    an old spec, or one with no recorded source, cannot verify the market
    capitalisation. ``None`` keeps the pre-authority behaviour for callers that
    predate the resolution.
    """
    from .status import basis_verified

    verified_model = _normalise(model_status) == VERIFIED
    cap_ok = capital_verified if market_cap_verified is None else market_cap_verified
    eps_ok = capital_verified if eps_basis_verified is None else eps_basis_verified
    equity_ok = (
        capital_verified if equity_basis_verified is None else equity_basis_verified
    )
    statuses: dict[str, VerificationStatus] = {
        "kernel_identity": VERIFIED if verified_model else _normalise(model_status),
        "operating_forecast": VERIFIED if verified_model else _normalise(model_status),
        "forecast_eps_share_basis": (
            VERIFIED
            if eps_ok and basis_verified(basis.get("forecast_eps_basis", ""))
            else SUPPRESSED
        ),
        "market_cap_share_basis": (
            VERIFIED
            if cap_ok and basis_verified(basis.get("market_cap_basis", ""))
            else SUPPRESSED
        ),
        "equity_value_share_basis": (
            VERIFIED
            if equity_ok
            and basis_verified(
                basis.get("equity_value_share_basis", "")
                or basis.get("market_cap_basis", "")
            )
            else SUPPRESSED
        ),
        "share_price": (
            VERIFIED
            if price and share_price_verified is not False
            else (UNVERIFIED if price else UNAVAILABLE)
        ),
        "depreciation": VERIFIED if has_depreciation else UNAVAILABLE,
        "gross_debt": VERIFIED if has_net_debt else UNAVAILABLE,
        "cash": VERIFIED if has_net_debt else UNAVAILABLE,
        "lease_liabilities": VERIFIED if has_net_debt else UNAVAILABLE,
        "pe_multiple": VERIFIED if pe_multiple else UNAVAILABLE,
        "ev_ebitda_multiple": (
            VERIFIED
            if ev_ebitda_multiple and ev_inputs_ready is not False
            else UNAVAILABLE
        ),
    }
    return statuses


def assess_outputs(
    *,
    model_status: str,
    basis: dict[str, str],
    price: float | None,
    has_depreciation: bool,
    has_net_debt: bool,
    pe_multiple: float | None,
    ev_ebitda_multiple: float | None,
    challenge_fields: list[str] | None = None,
    capital_verified: bool = True,
    ev_inputs_ready: bool | None = None,
    market_cap_verified: bool | None = None,
    eps_basis_verified: bool | None = None,
    equity_basis_verified: bool | None = None,
    share_price_verified: bool | None = None,
) -> dict[str, VerificationStatus]:
    inputs = input_statuses(
        model_status=model_status,
        basis=basis,
        price=price,
        has_depreciation=has_depreciation,
        has_net_debt=has_net_debt,
        pe_multiple=pe_multiple,
        ev_ebitda_multiple=ev_ebitda_multiple,
        capital_verified=capital_verified,
        ev_inputs_ready=ev_inputs_ready,
        market_cap_verified=market_cap_verified,
        eps_basis_verified=eps_basis_verified,
        equity_basis_verified=equity_basis_verified,
        share_price_verified=share_price_verified,
    )
    return propagate(inputs, blocked_outputs(challenge_fields))
