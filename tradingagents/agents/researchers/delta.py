"""Round 2+ reviewer delta: six named fields, no repetition, 150 words.

The parser exists before the prompt so a malformed delta is a test failure,
not a silent prompt regression.
"""

from __future__ import annotations

import re

MAX_WORDS = 150

DELTA_FIELDS = (
    ("POSITION CHANGE", "position_change"),
    ("STRONGEST UNRESOLVED OBJECTION", "strongest_unresolved_objection"),
    ("DIRECT ANSWER", "direct_answer"),
    ("CONCESSION", "concession"),
    ("NEW EVIDENCE OR CALCULATION", "new_evidence_or_calculation"),
    ("DECISION IMPACT", "decision_impact"),
)

_HEADING = re.compile(r"(?m)^\s*([A-Z][A-Z /]{4,})\s*:\s*$")


def parse_delta(text: str) -> dict[str, str]:
    """The six fields by name; a missing field is an empty string."""
    matches = list(_HEADING.finditer(text or ""))
    sections: dict[str, str] = {}
    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        sections[match.group(1).strip().upper()] = text[match.end():end].strip()
    return {key: sections.get(label, "") for label, key in DELTA_FIELDS}


def missing_fields(parsed: dict[str, str]) -> list[str]:
    """Fields the delta did not answer; ``concession: none`` is an answer."""
    return [key for key, value in parsed.items() if not str(value).strip()]


def declares_no_material_update(text: str) -> bool:
    return "NO MATERIAL UPDATE" in str(text or "").upper()


def within_limit(text: str) -> bool:
    return len(str(text or "").split()) <= MAX_WORDS


def delta_contract_ok(text: str) -> bool:
    if declares_no_material_update(text):
        return True
    return not missing_fields(parse_delta(text)) and within_limit(text)
