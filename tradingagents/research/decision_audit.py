"""Whether a directional call is supported without a verified valuation.

The PM may act without a verified price target, but only on traceable evidence.
This module is the audit: it counts independent evidence clusters (not labels),
requires a cash-flow/balance-sheet/event or severe-operating leg, and normalises
an unsupported Sell or Reduce rather than publishing it as a fundamental call.

Nothing here reads prose. The PM returns the audit structure; the code counts it.
"""

from __future__ import annotations

from typing import Any

EVIDENCE_CATEGORIES = {
    "operating",
    "earnings_revision",
    "consensus_gap",
    "cash_flow",
    "balance_sheet",
    "capital_allocation",
    "regulatory_legal_accounting",
    "catalyst",
    "technical",
}

# The categories that can carry a sell/reduce without a valuation.
RISK_CATEGORIES = {"cash_flow", "balance_sheet", "regulatory_legal_accounting"}

DEFAULT_THRESHOLDS = {"operating_decline_pct": 20.0, "margin_compression_bp": 300.0}


def thresholds_for(sector: str, config: dict | None = None) -> dict[str, float]:
    """Sector-specific severity thresholds, with a generic default."""
    if config is None:
        from tradingagents.default_config import DEFAULT_CONFIG

        config = DEFAULT_CONFIG
    table = (config or {}).get("severity_thresholds") or {}
    key = str(sector or "").strip().lower()
    values = table.get(key) or table.get("default") or DEFAULT_THRESHOLDS
    merged = dict(DEFAULT_THRESHOLDS)
    merged.update({k: float(v) for k, v in values.items() if isinstance(v, (int, float))})
    return merged


def _verified_supporting(audit: dict) -> list[dict]:
    items = []
    for item in audit.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        if item.get("verified") is True and item.get("supports_action") is True:
            if str(item.get("category") or "") not in EVIDENCE_CATEGORIES:
                continue
            items.append(item)
    return items


def independent_sources(audit: dict) -> tuple[int, int]:
    """(independent evidence clusters, clusters with a primary disclosure).

    Categories sharing one `underlying_driver_id` are one cluster: a margin miss,
    its revision and its consensus gap are one fact. Primary sources are grouped
    by `disclosure_ref` (a filing section or event), not by document id alone.
    """
    items = _verified_supporting(audit)
    drivers: dict[str, set[str]] = {}
    for item in items:
        driver = str(item.get("underlying_driver_id") or item.get("category") or "")
        if not driver:
            continue
        refs = item.get("primary_source_refs") or []
        if isinstance(refs, str):
            refs = [refs]
        if refs:
            disclosure = str(item.get("disclosure_ref") or refs[0])
            drivers.setdefault(driver, set()).add(disclosure)
    clusters = {str(item.get("underlying_driver_id") or item.get("category") or "")
                for item in items}
    return len({c for c in clusters if c}), len(drivers)


def assess(
    audit: dict | None,
    *,
    action: str,
    valuation_verified: bool,
    sector: str = "",
    config: dict | None = None,
) -> dict[str, Any]:
    """The audit result: whether the action may stand, and why not."""
    data = audit or {}
    action = str(action or "").upper()
    failures: list[str] = []

    if data.get("uses_suppressed_figures"):
        failures.append("the reasoning uses suppressed figures")

    items = _verified_supporting(data)
    categories = {str(item.get("category") or "") for item in items}
    clusters, clustered_sources = independent_sources(data)
    basis = str(data.get("decision_basis") or "fundamental").lower()

    if basis not in {"fundamental", "event", "technical", "mixed"}:
        failures.append("the decision basis is not one of fundamental/event/technical/mixed")
    if basis == "mixed":
        controlling = str(data.get("controlling_category") or "")
        if not controlling:
            failures.append("a mixed basis must name the controlling category")
        elif controlling not in categories:
            failures.append("the controlling category has no verified evidence")
        if not (categories & {"operating", "earnings_revision", "consensus_gap",
                             "cash_flow", "balance_sheet", "capital_allocation",
                             "regulatory_legal_accounting", "catalyst"}):
            failures.append("a mixed basis needs at least one fundamental or event category")
        if "technical" not in categories:
            failures.append("a mixed basis needs verified technical evidence")
    if basis == "technical" and action not in {"HOLD", "NOT_RATED"}:
        failures.append("a technical-only basis cannot publish a fundamental Buy or Sell")

    # "Severe operating deterioration" is a claim with a number behind it. When
    # the audit states the observed move, the sector threshold decides; without
    # it, the flag needs the evidence the caller cites.
    thresholds = thresholds_for(sector, config)
    severe = bool(data.get("severe_operating_deterioration"))
    observed_decline = data.get("observed_operating_decline_pct")
    observed_bp = data.get("observed_margin_compression_bp")
    if severe and (observed_decline is not None or observed_bp is not None):
        severe = bool(
            (observed_decline is not None
             and float(observed_decline) >= thresholds["operating_decline_pct"])
            or (observed_bp is not None
                and float(observed_bp) >= thresholds["margin_compression_bp"])
        )

    if not str(data.get("horizon") or "").strip():
        failures.append("the horizon is not stated")
    if not (data.get("confirmation_conditions") or []):
        failures.append("no confirmation condition is stated")
    if not (data.get("invalidation_conditions") or []):
        failures.append("no invalidation condition is stated")

    has_risk_category = bool(categories & RISK_CATEGORIES) or severe

    needs_non_valuation = not valuation_verified and action in {"SELL", "REDUCE"}
    if needs_non_valuation:
        if not items:
            failures.append("no verified evidence supports the action")
        if clusters < 3:
            failures.append(f"{clusters} independent evidence cluster(s); three are required")
        if not has_risk_category:
            failures.append(
                "no cash-flow, balance-sheet, event or severe-operating evidence"
            )
        if clustered_sources < 2:
            failures.append(
                "fewer than two independent primary disclosures support the action"
            )

    passed = not failures
    normalized = action
    status = "FINAL" if valuation_verified else "PROVISIONAL"
    if needs_non_valuation and not passed:
        de_risking = clusters >= 2 and has_risk_category
        normalized = "REDUCE" if de_risking else "HOLD"
    if not action:
        normalized = "HOLD"
    return {
        "validation_passed": passed,
        "failure_reasons": failures,
        "independent_category_count": clusters,
        "independent_primary_source_count": clustered_sources,
        "has_risk_category": has_risk_category,
        "needs_non_valuation": needs_non_valuation,
        "normalized_action": normalized,
        "decision_status": status if passed or valuation_verified else "PROVISIONAL",
    }
