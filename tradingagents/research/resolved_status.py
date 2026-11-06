"""One final status object for EV/EBITDA, shared by every consumer.

Enterprise-value arithmetic, the multiple assumption, the equity bridge, the
per-share basis and the comparison are different claims. A reconciled EV does not
make the equity bridge verified, and a verified comparison is impossible when the
share counts differ. The resolver is the last word before rendering.
"""

from __future__ import annotations

from dataclasses import dataclass, field

VERIFIED = "VERIFIED"
UNAVAILABLE = "UNAVAILABLE"
PROVISIONAL = "PROVISIONAL"
UNVERIFIED = "UNVERIFIED"
SHARE_COUNT_MISMATCH = "SHARE_COUNT_MISMATCH"
CROSS_CHECK_ONLY = "CROSS_CHECK_ONLY"
CONTROLLING = "CONTROLLING"
CONTROLLING_METHOD_CONFLICT = "CONTROLLING_VALUATION_METHOD_CONFLICT"


@dataclass
class ResolvedStatus:
    ev_calculation_status: str = UNAVAILABLE
    multiple_assumption_status: str = ""
    equity_bridge_status: str = UNAVAILABLE
    per_share_basis_status: str = UNAVAILABLE
    per_share_comparison_status: str = UNAVAILABLE
    decision_use: str = CROSS_CHECK_ONLY
    controlling_method: str = "pe"
    ev_decision_use: str = CROSS_CHECK_ONLY
    contradictions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ev_calculation_status": self.ev_calculation_status,
            "multiple_assumption_status": self.multiple_assumption_status,
            "equity_bridge_status": self.equity_bridge_status,
            "per_share_basis_status": self.per_share_basis_status,
            "per_share_comparison_status": self.per_share_comparison_status,
            "decision_use": self.decision_use,
            "controlling_method": self.controlling_method,
            "ev_decision_use": self.ev_decision_use,
            "contradictions": list(self.contradictions),
        }


def resolve(
    *,
    spec=None,
    statuses: dict | None = None,
    assumption=None,
    share_mismatch: bool = False,
    convertible_unresolved: bool = False,
    dominant_segment_detected: bool = False,
    segment_status: str = "",
    deterministic_checks_executed: bool = True,
    trigger_taxonomy_version: int = 2,
    legacy_rendered: bool = False,
    sensitivity_conflict: bool = False,
    controlling_method: str = "pe",
    controlling_conflict: str = "",
) -> ResolvedStatus:
    methods = (statuses or {}).get("valuation_method_statuses") or {}
    ev_calc = str(methods.get("ev_ebitda") or UNAVAILABLE)
    controlling = str(controlling_method or "pe").strip().lower()
    ev_controlling = controlling == "ev_ebitda"
    resolved = ResolvedStatus(
        ev_calculation_status=ev_calc,
        multiple_assumption_status=getattr(assumption, "status", "") if assumption else "",
        equity_bridge_status=(
            PROVISIONAL if convertible_unresolved
            else VERIFIED if ev_calc == VERIFIED else UNAVAILABLE
        ),
        per_share_basis_status=(
            SHARE_COUNT_MISMATCH if share_mismatch else
            ("VERIFIED" if ev_calc == VERIFIED else UNAVAILABLE)
        ),
        per_share_comparison_status=(
            UNVERIFIED if share_mismatch else
            str((statuses or {}).get("ev_per_share_status") or UNAVAILABLE)
        ),
        decision_use=CONTROLLING if ev_controlling else CROSS_CHECK_ONLY,
        controlling_method=controlling,
        ev_decision_use=CONTROLLING if ev_controlling else CROSS_CHECK_ONLY,
    )
    if share_mismatch:
        component_comparison = str((statuses or {}).get("ev_per_share_status") or "")
        if component_comparison == VERIFIED:
            # The component claimed a verified per-share comparison, but the two
            # methods count different shares. The resolved comparison is
            # UNVERIFIED; this contradiction is a blocker only if EV controls.
            resolved.contradictions.append(
                "EV_COMPARISON_VERIFIED_WITH_SHARE_MISMATCH"
            )
    if controlling_conflict:
        resolved.contradictions.append(CONTROLLING_METHOD_CONFLICT)
    if trigger_taxonomy_version >= 2 and legacy_rendered:
        resolved.contradictions.append("LEGACY_TRIGGER_RENDERED")
    if dominant_segment_detected and segment_status == "NOT_REQUIRED":
        resolved.contradictions.append("DOMINANT_SEGMENT_STATUS_NOT_REQUIRED")
    if not deterministic_checks_executed:
        resolved.contradictions.append("DETERMINISTIC_CHECKS_NOT_EXECUTED")
    if sensitivity_conflict:
        resolved.contradictions.append("SENSITIVITY_VERIFIED_AND_FAILED")
    return resolved
