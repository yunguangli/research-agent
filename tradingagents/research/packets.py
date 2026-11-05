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

import json
import re
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


def _methods(state: dict[str, Any]) -> dict[str, str]:
    try:
        data = json.loads(str(state.get("valuation_method_statuses") or "{}"))
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def pe_suppressed(state: dict[str, Any]) -> bool:
    """Whether the P/E output may not be used or reconstructed."""
    return _methods(state).get("pe") != "VERIFIED"


def fundamental_packet(state: dict[str, Any]) -> str:
    """The model's earnings facts, with no price, no shares and no per-share output."""
    text = str(state.get("fundamental_packet") or "").strip()
    if not text:
        # Old checkpoints have one package; it already omits per-share figures
        # when the basis failed, and the detector still guards the combination.
        text = str(state.get("research_package") or "").strip()
    if not text:
        return UNAVAILABLE
    if pe_suppressed(state):
        text += (
            "\n\nPacket rule: revenue, operating profit and net profit are usable as "
            "fundamental facts. Do not combine them with price, share count or any "
            "per-share figure to state a P/E, market cap, target or fair value."
        )
    return text


def valuation_packet(state: dict[str, Any]) -> str:
    """Only the valuation outputs that verified; empty when a method is suppressed."""
    text = str(state.get("valuation_packet") or "").strip()
    if text:
        return text
    low = state.get("research_fair_value_low")
    high = state.get("research_fair_value_high")
    if (
        not pe_suppressed(state)
        and low is not None
        and high is not None
        and bool(state.get("research_fair_value_verified", True))
    ):
        return f"P/E model band: {float(low):,.0f}-{float(high):,.0f}."
    return (
        "Valuation packet: P/E suppressed — no P/E, market cap, EPS-derived value, "
        "price target or fair-value range is available. State valuation as unavailable."
    )


def technical_packet(state: dict[str, Any]) -> str:
    """Price and verified technical evidence: timing only, never valuation."""
    return str(state.get("market_report") or "")


_PE_TERM = re.compile(r"\bP/E\b|price[- ]to[- ]earnings|price/earnings", re.IGNORECASE)
_RATIO = re.compile(r"\b\d+(?:\.\d+)?\s*(?:x|times)\b", re.IGNORECASE)
_TARGET = re.compile(r"price target|target price|fair value", re.IGNORECASE)
_EARNINGS_TERM = re.compile(r"\bEPS\b|earnings|net profit|market cap", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def reconstruction_findings(text: str, state: dict[str, Any]) -> list[str]:
    """Places the text reconstructs a suppressed P/E from remaining fields.

    A suppressed method is not merely hidden: price (technical packet) plus net
    profit (fundamental packet) reconstructs it, so the combination must be
    refused even though each input is visible.
    """
    if not pe_suppressed(state) or not str(text or "").strip():
        return []
    findings: list[str] = []
    if _PE_TERM.search(text):
        findings.append("states a P/E")
    for sentence in _SENTENCE.split(text):
        if "ev/ebitda" in sentence.lower():
            continue                     # the second method, independently verified
        if _RATIO.search(sentence) and _EARNINGS_TERM.search(sentence):
            findings.append(
                "derives a multiple from earnings: " + " ".join(sentence.split())[:160]
            )
        if _TARGET.search(sentence) and re.search(r"\d", sentence):
            findings.append(
                "states a target or fair value: " + " ".join(sentence.split())[:160]
            )
    return findings
