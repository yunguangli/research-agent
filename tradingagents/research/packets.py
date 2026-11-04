"""One place that decides which model outputs a run may use.

The gate already withholds the rating when the model is unverified, but the
downstream prompts were still built from the model package, the case table and
the ranked sensitivities. A warning inside a prompt is not a control: this module
removes the figures from the packet, so a debate cannot quote what it was never
given.

The Fundamentals report is withheld too. It was written against the model, so it
can contain model EPS, multiples and a manual fair value; no regex can separate
those from the rest reliably, and the report stays available in the appendix for
auditability.
"""

from __future__ import annotations

from typing import Any

UNAVAILABLE = "not available — deterministic model unverified"

UNVERIFIED_FUNDAMENTALS_NOTICE = (
    "The Fundamentals Analyst report is withheld from this packet because it was "
    "written against a deterministic model that failed verification. Its figures "
    "are not available for the decision; the full report is in the appendix for "
    "audit. Use only reported results, company guidance, verified consensus, "
    "verified technical data and qualitative evidence."
)


def model_verified(state: dict[str, Any]) -> bool:
    """Whether model figures may be used.

    Only an explicit UNVERIFIED blocks: a run with no deterministic model
    (NOT_APPLICABLE) is the classic pipeline, which never had model figures to
    withhold, and an old checkpoint without the key must keep working.
    """
    return str(state.get("model_status") or "").upper() != "UNVERIFIED"


def verified_packet(state: dict[str, Any]) -> dict[str, Any]:
    """The packet a verified run already builds (nothing removed)."""
    return {
        "verified": True,
        "fundamentals": str(state.get("fundamentals_report") or ""),
        "package": str(state.get("research_package") or ""),
        "case_table": str(state.get("case_table") or ""),
        "sensitivity_table": str(state.get("sensitivity_table") or ""),
        "metrics_table": str(state.get("metrics_table") or ""),
        "valuation_notice": "",
    }


def unverified_packet(state: dict[str, Any]) -> dict[str, Any]:
    """Everything sourced from the model is removed, not annotated."""
    return {
        "verified": False,
        "fundamentals": UNVERIFIED_FUNDAMENTALS_NOTICE,
        "package": UNAVAILABLE,
        "case_table": "",
        "sensitivity_table": "",
        "metrics_table": "",
        "valuation_notice": (
            "Valuation conclusion: not established — the deterministic model is "
            "unverified. Manual analyst valuation is non-controlling and cannot "
            "supply a price target, a fair-value range or a model-based rating."
        ),
    }


def filter_decision_inputs(state: dict[str, Any]) -> dict[str, Any]:
    """The packet every downstream stage must use."""
    return verified_packet(state) if model_verified(state) else unverified_packet(state)


def packet_for_prompt(state: dict[str, Any]) -> str:
    """The package block a prompt may carry (empty when unverified)."""
    packet = filter_decision_inputs(state)
    return packet["package"] if packet["verified"] else UNAVAILABLE


def unverified_reason(state: dict[str, Any]) -> str:
    return str(state.get("spec_compatibility_reason") or "the model did not verify")
