"""Three independent verification checks, each with its own published outcome.

The checks share a report but not a calculation: ``source_check``,
``model_check`` and ``decision_check`` each take their own inputs and return
their own verdict. None reads another's result, so a clean-looking decision
cannot launder a failed source check.

SOURCE_CHECK   do material inputs and classifications agree with primary evidence?
MODEL_CHECK    do the calculations reconcile, and did accepted corrections propagate?
DECISION_CHECK does the call survive the range of known corrections and unresolved
               defects, not only the ranked sensitivities?

Outcomes:
PASS                      the conclusion survives the plausible range.
PASS_WITH_QUALIFICATION   it survives only under stated assumptions, or part of the
                          rationale/valuation/trigger framework still needs repair.
FAIL                      it depends materially on a defective, misclassified or
                          unpropagated input.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import relevance

PASS = "PASS"
QUALIFIED = "PASS_WITH_QUALIFICATION"
FAIL = "FAIL"
NOT_APPLICABLE = "N/A"

# Worst first: the published overall outcome is the worst applicable check.
_RANK = {NOT_APPLICABLE: -1, PASS: 0, QUALIFIED: 1, FAIL: 2}

LONG_ACTIONS = {"BUY", "OVERWEIGHT", "ADD", "INITIATE"}
SHORT_ACTIONS = {"SELL", "REDUCE", "EXIT", "AVOID"}

# A correction only qualifies the decision when it touches something the decision
# is built from; a segment detail that never reaches the valuation does not.
_DECISION_FIELDS = (
    "valuation", "eps", "forecast", "rating", "fair_value",
    "net_profit", "operating_profit", "revenue",
)


def _affects_decision(record: dict) -> bool:
    text = " ".join(
        [str(value) for value in record.get("downstream_outputs") or []]
        + [str(value) for value in record.get("affected_fields") or []]
    ).lower()
    return any(field in text for field in _DECISION_FIELDS)


@dataclass
class CheckResult:
    name: str
    outcome: str = PASS
    reasons: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.outcome == PASS

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "outcome": self.outcome,
            "reasons": list(self.reasons),
            "details": dict(self.details),
        }


def _worse(current: str, candidate: str) -> str:
    return candidate if _RANK[candidate] > _RANK[current] else current


def _status(value) -> str:
    return str(value or "").strip().upper()


def source_check(
    *,
    model_status: str = "",
    conflicts: list | None = None,
    unresolved: list[dict] | None = None,
    provenance: str = "",
) -> CheckResult:
    """Material inputs and classifications against primary evidence.

    A source is primary only when the model names it. A conflict that no source
    resolves, or an unresolved challenge that says the evidence disagrees, is a
    source failure; a resolved conflict is a qualification.
    """
    result = CheckResult("SOURCE_CHECK")
    if (
        _status(model_status) not in {"VERIFIED", "UNVERIFIED"}
        and not conflicts
        and not unresolved
    ):
        result.outcome = NOT_APPLICABLE
        result.reasons.append("no sourced model input in this run")
        result.details = {"model_status": _status(model_status)}
        return result
    if _status(model_status) == "UNVERIFIED":
        result.outcome = FAIL
        result.reasons.append("the controlling model is unavailable, so no input can be sourced")

    if _status(provenance) == "INCOMPATIBLE":
        result.outcome = _worse(result.outcome, FAIL)
        result.reasons.append("the model's provenance is incompatible with this run")

    for conflict in conflicts or []:
        text = str(conflict or "").strip()
        if text:
            result.outcome = _worse(result.outcome, QUALIFIED)
            result.reasons.append(f"a source conflict is on the record: {text}")

    for item in unresolved or []:
        severity = _status(item.get("severity"))
        label = str(item.get("challenge_type") or item.get("label") or "an input")
        if severity == "BLOCKING":
            result.outcome = _worse(result.outcome, FAIL)
            result.reasons.append(f"an unresolved blocking source defect: {label}")
        elif severity in {"MATERIAL", "MINOR", "SCENARIO_ONLY"}:
            result.outcome = _worse(result.outcome, QUALIFIED)
            result.reasons.append(f"an unresolved source qualification: {label}")

    result.details = {
        "model_status": _status(model_status),
        "provenance": _status(provenance),
        "conflicts": len(conflicts or []),
        "unresolved": len(unresolved or []),
    }
    return result


def model_check(
    *,
    model_status: str = "",
    output_statuses: dict | None = None,
    corrections: list[dict] | None = None,
    superseded: int = 0,
    decision_model_version: str = "",
    active_model_version: str = "",
) -> CheckResult:
    """Calculations reconcile and every accepted correction is propagated.

    This check may read the correction ledger only through its propagation flag;
    it never reads the decision audit.
    """
    result = CheckResult("MODEL_CHECK")
    if (
        _status(model_status) not in {"VERIFIED", "UNVERIFIED"}
        and not corrections
        and not output_statuses
        and not superseded
    ):
        result.outcome = NOT_APPLICABLE
        result.reasons.append("no model calculation to reconcile in this run")
        result.details = {"model_status": _status(model_status)}
        return result
    if _status(model_status) == "UNVERIFIED":
        result.outcome = FAIL
        result.reasons.append("the model did not verify")

    for record in corrections or []:
        status = _status(record.get("status"))
        label = str(record.get("challenge_type") or "a correction")
        if status == "ACCEPTED" and not record.get("propagated", False):
            result.outcome = _worse(result.outcome, FAIL)
            result.reasons.append(f"an accepted correction did not reach the model: {label}")
        elif status in {"PROPOSED", "UNRESOLVED"}:
            result.outcome = _worse(result.outcome, QUALIFIED)
            result.reasons.append(f"a correction is not in the controlling model: {label}")

    statuses = output_statuses or {}
    suppressed = sorted(
        name for name, value in statuses.items() if _status(value) in {"SUPPRESSED", "UNVERIFIED"}
    )
    unavailable = sorted(name for name, value in statuses.items() if _status(value) == "UNAVAILABLE")
    if suppressed:
        result.outcome = _worse(result.outcome, QUALIFIED)
        result.reasons.append("dependent outputs suppressed: " + ", ".join(suppressed))
    if unavailable:
        result.outcome = _worse(result.outcome, QUALIFIED)
        result.reasons.append("outputs not available: " + ", ".join(unavailable))
    if superseded:
        result.outcome = _worse(result.outcome, QUALIFIED)
        result.reasons.append(f"{superseded} figure(s) from an earlier model version are superseded")

    if (
        _status(decision_model_version)
        and _status(active_model_version)
        and _status(decision_model_version) != _status(active_model_version)
    ):
        result.outcome = FAIL
        result.reasons.append(
            f"the decision was written against {decision_model_version}, but the "
            f"controlling model is {active_model_version}"
        )

    result.details = {
        "model_status": _status(model_status),
        "corrections": len(corrections or []),
        "superseded": superseded,
    }
    return result


def decision_check(
    *,
    action: str,
    fair_value_low: float | None = None,
    fair_value_mid: float | None = None,
    fair_value_high: float | None = None,
    price: float | None = None,
    audit: dict | None = None,
    sensitivities: list[dict] | None = None,
    corrections: list[dict] | None = None,
    unresolved: list[dict] | None = None,
) -> CheckResult:
    """Does the call survive the known corrections and unresolved defects?

    Each accepted correction and unresolved defect is a stress case. A ranked
    sensitivity is judged by whether its plausible variation can change the
    decision (``research/relevance.py``), not by a universal percentage.
    """
    result = CheckResult("DECISION_CHECK")
    normalized = _status(action)
    if not normalized and not audit and not corrections and not unresolved:
        result.outcome = NOT_APPLICABLE
        result.reasons.append("no decision to test in this run")
        result.details = {"action": ""}
        return result

    if audit and audit.get("validation_passed") is False:
        result.outcome = FAIL
        reasons = audit.get("failure_reasons") or ["the decision audit did not pass"]
        result.reasons.append("the decision audit failed: " + "; ".join(map(str, reasons)))

    for item in unresolved or []:
        severity = _status(item.get("severity"))
        label = str(item.get("challenge_type") or item.get("label") or "a known defect")
        if severity == "BLOCKING":
            result.outcome = FAIL
            result.reasons.append(f"the call rests on an unresolved blocking defect: {label}")
        elif severity in {"MATERIAL", "SCENARIO_ONLY"}:
            result.outcome = _worse(result.outcome, QUALIFIED)
            result.reasons.append(f"the call survives only with an unresolved defect disclosed: {label}")

    for record in corrections or []:
        status = _status(record.get("status"))
        label = str(record.get("challenge_type") or "a correction")
        if status == "ACCEPTED" and not record.get("propagated", False):
            result.outcome = FAIL
            result.reasons.append(f"an accepted correction never reached the decision: {label}")
        elif status == "ACCEPTED":
            # The call now depends on the correction being right. That is a
            # qualification unless the correction never reaches the decision.
            if _affects_decision(record):
                result.outcome = _worse(result.outcome, QUALIFIED)
                result.reasons.append(f"the call depends on an accepted correction: {label}")
        elif status in {"PROPOSED", "UNRESOLVED"}:
            result.outcome = _worse(result.outcome, QUALIFIED)
            result.reasons.append(f"a known correction is not yet resolved: {label}")

    base = fair_value_mid
    if base is None and fair_value_low is not None and fair_value_high is not None:
        base = (fair_value_low + fair_value_high) / 2
    if (
        base is not None
        and fair_value_low is not None
        and price is not None
        and normalized in LONG_ACTIONS
        and fair_value_low <= price
    ):
        # The published band already contains the low end: a call that needs the
        # midpoint to stand is qualified even before any shock is applied.
        result.outcome = _worse(result.outcome, QUALIFIED)
        result.reasons.append("the low end of the published band is at or below the price")

    tested: list[dict] = []
    for item in sensitivities or []:
        impact = item.get("fair_value_impact")
        if impact is None or base is None or not base:
            continue
        shocked = base + float(impact)
        relevance_class = relevance.classify(
            action=normalized,
            base_fair=base,
            shocked_fair=shocked,
            price=price,
            band_low=fair_value_low,
            band_high=fair_value_high,
        )
        tested.append({
            "sensitivity_id": item.get("sensitivity_id") or item.get("driver") or "",
            "relevance": relevance_class,
            "fair_value_impact": float(impact),
        })
        label = item.get("sensitivity_id") or item.get("driver") or "a sensitivity"
        if relevance_class == relevance.DECISIVE:
            result.outcome = FAIL
            result.reasons.append(
                f"{label}: plausible variation changes the {normalized or 'rated'} decision"
            )
        elif relevance_class == relevance.MATERIAL_SECONDARY:
            result.outcome = _worse(result.outcome, QUALIFIED)
            result.reasons.append(
                f"{label}: the decision survives only with a material secondary sensitivity"
            )

    counts = dict.fromkeys(
        (relevance.DECISIVE, relevance.MATERIAL_SECONDARY, relevance.MINOR), 0
    )
    for item in tested:
        counts[item["relevance"]] = counts.get(item["relevance"], 0) + 1
    result.details = {
        "action": normalized,
        "sensitivities_tested": tested,
        "relevance_counts": counts,
        "fair_value_mid": base,
        "price": price,
    }
    return result


def assess(
    *,
    source: dict | None = None,
    model: dict | None = None,
    decision: dict | None = None,
) -> dict:
    """Run the three checks independently and publish the worst outcome.

    The argument groups are deliberately separate: each check gets only the
    inputs it is allowed to read, and no check receives another's result.
    """
    checks = [
        source_check(**(source or {})),
        model_check(**(model or {})),
        decision_check(**(decision or {})),
    ]
    applicable = [check.outcome for check in checks if check.outcome != NOT_APPLICABLE]
    overall = (
        max(applicable, key=lambda outcome: _RANK[outcome])
        if applicable else NOT_APPLICABLE
    )
    return {
        "checks": [check.to_dict() for check in checks],
        "overall": overall,
    }
