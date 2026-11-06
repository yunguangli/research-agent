"""Three independent decision checks, safe even when the model fails.

DECISION_SAFETY_CHECK asks whether the run published something it must not: a
rating or fair value built on an unavailable or conflicted model. A safe Not Rated
fallback passes this check.

DECISION_PROCESS_CHECK asks whether the process was fully validated: a verified
model, a passed decision audit, structured output and a final decision. A safe
fallback is DEGRADED, not failed.

PRODUCTION_DECISION_ALLOWED is true only when both checks pass, the decision is
FINAL, publication is not blocked and the run purpose is production.
"""

from __future__ import annotations

PASS = "PASS"
DEGRADED = "DEGRADED"
FAIL = "FAIL"
NOT_APPLICABLE = "N/A"


def decision_safety(final_state: dict) -> tuple[str, list[str]]:
    reasons: list[str] = []
    model = str(final_state.get("model_status") or "").upper()
    source = str(final_state.get("research_model_source") or "")
    rating = str(final_state.get("final_rating") or "").strip()
    fair = any(
        final_state.get(key) is not None
        for key in (
            "controlling_fair_value_low",
            "controlling_fair_value_base",
            "controlling_fair_value_high",
        )
    )
    unavailable = model == "UNVERIFIED" or source == "unavailable"
    if unavailable:
        if rating and rating.lower() not in {"not rated", "review"}:
            reasons.append(f"a {rating} rating was published without a verified model")
        if fair:
            reasons.append("a controlling fair value was published without a verified model")
    if str(final_state.get("publication_status") or "").upper() == "BLOCKED":
        if rating and rating.lower() not in {"not rated", "review"}:
            reasons.append("a rating was published while publication is blocked")
        if fair:
            reasons.append("a fair value was published while publication is blocked")
    return (FAIL if reasons else PASS), reasons


def decision_process(final_state: dict) -> tuple[str, list[str]]:
    reasons: list[str] = []
    model = str(final_state.get("model_status") or "").upper()
    if model != "VERIFIED":
        reasons.append(f"model status is {model or 'not recorded'}")
    if str(final_state.get("decision_integrity") or "").startswith("DEGRADED"):
        reasons.append("decision integrity is degraded")
    if str(final_state.get("action_status") or "") in {"advisory", "invalidated"}:
        reasons.append("the decision action is advisory")
    if str(final_state.get("decision_status") or "").upper() != "FINAL":
        reasons.append(f"decision status is {final_state.get('decision_status') or 'not recorded'}")
    if str(final_state.get("publication_status") or "").upper() == "BLOCKED":
        reasons.append("publication is blocked")
    if reasons:
        return DEGRADED, reasons
    return PASS, []


def production_allowed(final_state: dict) -> bool:
    safety, _ = decision_safety(final_state)
    process, _ = decision_process(final_state)
    purpose = str(final_state.get("run_purpose") or "production").lower()
    return (
        safety == PASS
        and process == PASS
        and str(final_state.get("decision_status") or "").upper() == "FINAL"
        and str(final_state.get("publication_status") or "").upper() != "BLOCKED"
        and purpose == "production"
    )


def assess(final_state: dict) -> dict:
    safety, safety_reasons = decision_safety(final_state)
    process, process_reasons = decision_process(final_state)
    return {
        "decision_safety_check": safety,
        "decision_process_check": process,
        "production_decision_allowed": production_allowed(final_state),
        "safety_reasons": safety_reasons,
        "process_reasons": process_reasons,
    }
