"""Three trigger types, and the rules that keep them honest.

A forecast revision, a rating review and a thesis invalidation are different
events. Repeating company guidance is not a thesis break; a single quarterly
threshold is not either, unless it is material, unexplained and connected to the
structural claim. A fall in customer advances is a warning only when the cause is
cancellation, deferral, renegotiation or lost funding while committed capex
continues.

The validator is deterministic. It moves or drops events and records why; it
never changes the rating, the correction ledger or the scenarios.
"""

from __future__ import annotations

import re

FORECAST_REVISION = "FORECAST_REVISION_TRIGGER"
RATING_REVIEW = "RATING_REVIEW_TRIGGER"
THESIS_INVALIDATION = "THESIS_INVALIDATION_TRIGGER"

_BAD_ADVANCE_CAUSES = {"cancellation", "deferral", "renegotiation", "loss_of_funding"}
_NON_NEGATIVE_ADVANCE_CAUSES = {"applied_to_revenue", "construction_milestone"}
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_GUIDED_METRICS = {"revenue", "operating_profit", "ordinary_profit", "net_profit", "eps"}


def _get(trigger, key: str):
    if isinstance(trigger, dict):
        return trigger.get(key)
    return getattr(trigger, key, None)


def _text(trigger) -> str:
    evidence = _get(trigger, "evidence") or []
    if isinstance(evidence, list):
        evidence_text = " ".join(
            " ".join(
                [str(_get(item, "claim") or "")]
                + [str(ref) for ref in (_get(item, "source_ids") or [])]
            )
            for item in evidence
        )
    else:
        evidence_text = str(evidence)
    return " ".join(
        str(_get(trigger, key) or "")
        for key in ("description", "metric", "period")
    ) + " " + evidence_text.lower()


def _is_advance(trigger) -> bool:
    return "advance" in _text(trigger)


def _guidance_repeat(trigger, spec) -> bool:
    """Whether the event merely restates a metric the company already guided."""
    metric = str(_get(trigger, "metric") or "").strip().lower()
    period = str(_get(trigger, "period") or "").strip()
    if metric not in _GUIDED_METRICS or not period:
        return False
    guided = (getattr(spec, "guidance", None) or {}).get(period)
    if guided is None:
        return False
    if "guidance" in _text(trigger):
        return True
    stated = getattr(guided, metric, None)
    if stated is None:
        return False
    for token in _NUMBER.findall(str(_get(trigger, "description") or "")):
        try:
            number = float(token.replace(",", ""))
        except ValueError:
            continue
        if abs(number - float(stated)) <= max(0.01, abs(float(stated)) * 0.01):
            return True
    return False


def _material(trigger) -> bool:
    return str(_get(trigger, "materiality") or "").lower() in {"high", "material"}


def _reclassify(trigger, kind: str):
    if isinstance(trigger, dict):
        trigger["kind"] = kind
        trigger["trigger_type"] = kind
    else:
        trigger.kind = kind
        trigger.trigger_type = kind
    return trigger


def normalise(forecast, review, thesis, spec) -> tuple[list, list, list, list[str]]:
    """Return the three normalised lists and the findings that explain changes."""
    forecast = list(forecast or [])
    review = list(review or [])
    thesis = list(thesis or [])
    findings: list[str] = []
    kept_thesis: list = []

    for trigger in thesis:
        if _is_advance(trigger):
            cause = str(_get(trigger, "cause") or "").lower()
            capex = _get(trigger, "committed_capex_continues")
            if cause in _NON_NEGATIVE_ADVANCE_CAUSES:
                findings.append(
                    "Customer advances were not treated as negative: the documented "
                    f"cause is {cause}, not a cancellation, deferral, renegotiation "
                    "or loss of funding."
                )
                continue
            if cause not in _BAD_ADVANCE_CAUSES or capex is not True:
                findings.append(
                    "Customer advances were not treated as negative: the decline has "
                    "no documented cancellation/deferral/renegotiation/funding cause "
                    "with committed capex continuing."
                )
                continue
            review.append(
                _reclassify(
                    trigger,
                    RATING_REVIEW,
                )
            )
            findings.append(
                "Customer advances moved to rating review: the cause is documented, "
                "but the decline is a warning rather than a thesis break."
            )
            continue
        if _guidance_repeat(trigger, spec):
            forecast.append(_reclassify(trigger, FORECAST_REVISION))
            findings.append(
                "A thesis-invalidation trigger was moved to forecast revision: it "
                "repeats company guidance already in the model."
            )
            continue
        if (
            not _material(trigger)
            or str(_get(trigger, "temporary_explanation") or "").strip()
            or not str(_get(trigger, "thesis_link") or "").strip()
        ):
            review.append(_reclassify(trigger, RATING_REVIEW))
            findings.append(
                "A thesis-invalidation trigger was moved to rating review: it is not "
                "material, has a documented temporary explanation, or is not linked "
                "to the structural thesis."
            )
            continue
        kept_thesis.append(_reclassify(trigger, THESIS_INVALIDATION))
    return forecast, review, kept_thesis, findings
