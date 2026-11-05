"""Evidence attributes on the existing tags, not a parallel taxonomy.

A tag may carry metadata after a pipe: ``[fundamental|source_type=model_assumption]``.
The checks are deterministic: a model assumption cannot be presented as
reported, a read-through must name its comparable and limitation, an unsupported
claim cannot support an action, and "all three brokers" needs three sources.
"""

from __future__ import annotations

import re

SOURCE_TYPES = (
    "company_reported", "management_comment", "primary_calculation",
    "broker_interpretation", "model_assumption", "read_through", "unsupported",
)
_TAG = re.compile(
    r"\[(fundamental|valuation|technical|news_sentiment|portfolio_action)"
    r"((?:\|[^\]]+)?)\]",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_BROKERS = re.compile(r"\b(three|four|3|4)\s+(?:houses|brokers?)\b", re.IGNORECASE)
_CLAIMED = {"three": 3, "3": 3, "four": 4, "4": 4}


def parse_attributes(text: str) -> list[dict[str, str]]:
    """Every tagged claim with its attributes, in order."""
    entries: list[dict[str, str]] = []
    for match in _TAG.finditer(text or ""):
        attributes = {"tag": match.group(1).lower()}
        for part in match.group(2).lstrip("|").split("|"):
            if "=" in part:
                key, _, value = part.partition("=")
                attributes[key.strip().lower()] = value.strip()
        entries.append(attributes)
    return entries


def _sentence_for(text: str, position: int) -> str:
    start = 0
    for match in _SENTENCE.finditer(text or ""):
        if match.start() > position:
            return text[start:match.start()]
        start = match.end()
    return text[start:]


def broker_summary(text: str) -> dict[str, object]:
    """The broker count and list, generated only from structured source ids."""
    sources: list[str] = []
    for value in re.findall(r"source_id=([^|\]\s]+)", text or ""):
        if value.strip() and value.strip() not in sources:
            sources.append(value.strip())
    return {"count": len(sources), "brokers": sources}


def attribute_findings(text: str) -> list[str]:
    """Named problems with the attributes, or [] when the claims are honest."""
    findings: list[str] = []
    for match in _TAG.finditer(text or ""):
        attributes = {
            part.partition("=")[0].strip().lower(): part.partition("=")[2].strip()
            for part in match.group(2).lstrip("|").split("|") if "=" in part
        }
        source_type = attributes.get("source_type", "").lower()
        sentence = _sentence_for(text, match.start())
        if source_type == "model_assumption" and re.search(r"\breported\b", sentence, re.I):
            findings.append("a model assumption is presented as reported")
        if source_type == "read_through" and not (
            attributes.get("comparable") and attributes.get("limitation")
        ):
            findings.append("a read-through does not name its comparable case and limitation")
        if source_type == "unsupported" and attributes.get("supports_action", "").lower() == "true":
            findings.append("an unsupported claim is marked as supporting the action")
    brokers = _BROKERS.search(text or "")
    if brokers:
        claimed = _CLAIMED[brokers.group(1).lower()]
        # Count every distinct source id in the text: a single tag may carry
        # several, and the parser keeps one value per key.
        sources = {
            value.strip()
            for value in re.findall(r"source_id=([^|\]\s]+)", text or "")
            if value.strip()
        }
        if len(sources) < claimed:
            findings.append(
                f"'{brokers.group(0)}' is claimed but only {len(sources)} distinct "
                "source ids are cited"
            )
    return findings
