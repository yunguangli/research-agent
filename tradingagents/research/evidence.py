"""Evidence attributes and source classes.

A tag may carry metadata after a pipe: ``[fundamental|source_type=model_assumption]``.
The checks are deterministic: a model assumption cannot be presented as
reported, a read-through must name its comparable and limitation, an unsupported
claim cannot support an action, and "all three brokers" needs three sources.

The same module classifies the model's material inputs into five source classes
and polices how a forecast may be described. The classes are computed from the
spec's own sections, so a forecast cannot be relabelled by prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

COMPANY_ACTUAL = "COMPANY_ACTUAL"
COMPANY_GUIDANCE = "COMPANY_GUIDANCE"
AGENT_DERIVED = "AGENT_DERIVED"
AGENT_FORECAST = "AGENT_FORECAST"
EXTERNAL_ESTIMATE = "EXTERNAL_ESTIMATE"

SOURCE_CLASSES = (
    COMPANY_ACTUAL, COMPANY_GUIDANCE, AGENT_DERIVED, AGENT_FORECAST, EXTERNAL_ESTIMATE,
)

CLASS_LABELS = {
    COMPANY_ACTUAL: "company actual",
    COMPANY_GUIDANCE: "company-issued guidance",
    AGENT_DERIVED: "agent-derived (computed from the model)",
    AGENT_FORECAST: "agent forecast (internally modelled)",
    EXTERNAL_ESTIMATE: "external estimate",
}

_METRICS = ("revenue", "operating_profit", "ordinary_profit", "net_profit", "eps")
_GUIDANCE = re.compile(
    r"\b(?:company|management)(?:'s)?\s+(?:guidance|commitments?)\b|\bguidance\b|"
    r"\bverified consensus\b|\bcompany commitments?\b|\bcommitments?\b",
    re.IGNORECASE,
)
_SOURCE_WORD = re.compile(r"guidance|commitments?|verified consensus", re.IGNORECASE)
_HURDLE = re.compile(
    r"\b(?:beat|miss|upgrade|downgrade|catalyst|hurdle|clear|clears|cleared)\b", re.IGNORECASE
)
_MECHANICAL = re.compile(
    r"\bmechanically\b|\bderived directly\b|\bcalculated mechanically\b", re.IGNORECASE
)
_NEGATED = re.compile(
    r"\bno (?:company |management )?(?:guidance|commitments?)\b|\bwithout guidance\b|"
    r"issued no guidance|not (?:an? )?(?:company |management )?(?:guidance|commitments?)",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
# A figure is attributed to guidance only inside this narrow window; a number
# elsewhere in the sentence ("implies 762.1 to reach guidance of 1,375") is not
# a mislabelled guidance figure.
_RADIUS = 12

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


@dataclass(frozen=True)
class ClassifiedValue:
    """One material model input with the source class it belongs to."""

    period: str
    metric: str
    value: float
    source_class: str
    source: str


def classify_inputs(spec) -> list[ClassifiedValue]:
    """Every material input in the spec, labelled with its source class.

    The class comes from the spec's section, never from prose: actuals are
    company actuals, the guidance block is company guidance, the forecast block
    is an agent forecast, consensus is an external estimate, and scenario values
    are agent-derived.
    """
    rows: list[ClassifiedValue] = []
    for source_class, block in (
        (COMPANY_ACTUAL, spec.actuals),
        (COMPANY_GUIDANCE, spec.guidance),
        (AGENT_FORECAST, spec.forecast),
    ):
        for label, financials in (block or {}).items():
            for metric in _METRICS:
                value = financials.get(metric) if hasattr(financials, "get") else None
                if value is not None:
                    rows.append(ClassifiedValue(label, metric, float(value), source_class,
                                                source_class.lower()))
    consensus = getattr(spec, "consensus", None)
    for label, period in ((consensus.periods if consensus else {}) or {}).items():
        for metric, value in (period or {}).items():
            if value is not None:
                rows.append(ClassifiedValue(label, metric, float(value), EXTERNAL_ESTIMATE,
                                            consensus.source or "consensus"))
    for scenario in getattr(spec, "scenarios", []) or []:
        for metric in ("eps", "multiple"):
            value = getattr(scenario, metric, None)
            if value is not None:
                rows.append(ClassifiedValue(
                    getattr(scenario, "period", ""), metric, float(value), AGENT_DERIVED,
                    f"scenario {getattr(scenario, 'name', '')}".strip(),
                ))
    return rows


def benchmark_statement(spec) -> str:
    """The visible statement of whether the benchmark is company-issued or modelled."""
    if spec.guidance and spec.forecast:
        text = (
            "Benchmark: company-issued guidance for the guided period; the model's forecast "
            "is an agent forecast (AGENT_FORECAST), not company guidance."
        )
    elif spec.guidance:
        text = "Benchmark: company-issued guidance (COMPANY_GUIDANCE)."
    elif spec.forecast:
        text = (
            "Benchmark: internally modelled forecast (AGENT_FORECAST); the company issued no "
            "guidance in this model, so the forecast is not company guidance."
        )
    else:
        text = "Benchmark: not established; no company guidance or model forecast is present."
    consensus = getattr(spec, "consensus", None)
    if consensus and consensus.periods:
        text += (
            " External consensus is an external estimate (EXTERNAL_ESTIMATE), not company "
            "guidance."
        )
    if getattr(spec, "quarterly_plan", None):
        text += (
            " The Q3/Q4 allocation is internally modelled (AGENT_FORECAST), not company "
            "guidance."
        )
    return text


def _numbers(text: str) -> list[float]:
    values: list[float] = []
    for token in _NUMBER.findall(text or ""):
        try:
            values.append(float(token.replace(",", "")))
        except ValueError:
            continue
    return values


def _matches(value: float, candidates: set[float]) -> bool:
    return any(abs(value - candidate) <= max(0.01, abs(candidate) * 0.005)
               for candidate in candidates)


def _guidance_window(sentence: str) -> str:
    """The text around each mention of guidance, where a figure can be attributed."""
    windows: list[str] = []
    for match in _SOURCE_WORD.finditer(sentence):
        windows.append(
            sentence[max(0, match.start() - _RADIUS): match.end() + _RADIUS]
        )
    return " ".join(windows)


def _with_sentence(message: str, sentence: str) -> str:
    text = " ".join(sentence.split())
    return f"{message}: \"{text[:180]}\""


def claim_findings(text: str, spec) -> list[str]:
    """Prose that mislabels an internal forecast, or turns it into a company hurdle.

    High precision by design: a finding needs the claim word and a figure in the
    forecast block *attributed to guidance* within a short window. A sentence
    that quotes guidance — or that explicitly says the forecast is not guidance —
    is left alone.
    """
    rows = classify_inputs(spec)
    guidance_values = {row.value for row in rows if row.source_class == COMPANY_GUIDANCE}
    forecast_values = {row.value for row in rows if row.source_class == AGENT_FORECAST}
    if not guidance_values and not forecast_values:
        return []
    findings: list[str] = []
    for sentence in _SENTENCE.split(text or ""):
        if not sentence.strip():
            continue
        window = _guidance_window(sentence)
        window_numbers = _numbers(window) if window else []
        guidance_hit = any(_matches(value, guidance_values) for value in window_numbers)
        forecast_hit = any(_matches(value, forecast_values) for value in window_numbers)
        sentence_forecast = any(
            _matches(value, forecast_values) for value in _numbers(sentence)
        )
        claims_guidance = _GUIDANCE.search(sentence) and not _NEGATED.search(sentence)
        if claims_guidance:
            if not guidance_values:
                findings.append(_with_sentence(
                    "the sentence refers to company guidance, but the model contains none",
                    sentence,
                ))
            elif forecast_hit and not guidance_hit:
                findings.append(_with_sentence(
                    "an internally modelled forecast is described as company guidance",
                    sentence,
                ))
        if claims_guidance and _HURDLE.search(sentence):
            if not guidance_values:
                findings.append(_with_sentence(
                    "a beat/miss/upgrade/downgrade hurdle is called company guidance, but "
                    "the company issued none",
                    sentence,
                ))
            elif forecast_hit and not guidance_hit:
                findings.append(_with_sentence(
                    "an internal forecast is used as the company-guided hurdle",
                    sentence,
                ))
        if _MECHANICAL.search(sentence) and sentence_forecast:
            findings.append(_with_sentence(
                "a discretionary forecast is presented as mechanically derived",
                sentence,
            ))
    # One message per kind is enough for the audit table; the sentence keeps it locatable.
    return list(dict.fromkeys(findings))


def _sentence_split(text: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+", text or "")


def enforce_forecast_labels(text: str, spec) -> tuple[str, list[str]]:
    """Relabel an internal forecast that is presented as company guidance.

    Only a sentence that produces a claim finding is rewritten, so a genuine
    guidance quote and a negated disclosure keep their wording.
    """
    findings = claim_findings(text, spec)
    if not findings:
        return text, []
    replacements = (
        (re.compile(r"\b(?:company|management)(?:'s)?\s+(?:guidance|commitments?)\b", re.I),
         "an internal forecast"),
        (re.compile(r"\bverified consensus\b", re.I), "external estimate"),
    )

    def _fix(sentence: str) -> str:
        if not claim_findings(sentence, spec):
            return sentence
        for pattern, replacement in replacements:
            sentence = pattern.sub(replacement, sentence)
        return sentence

    parts = re.split(r"([^.!?\n]+[.!?]?|\n+)", text or "")
    return "".join(_fix(part) if part.strip() else part for part in parts), findings


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
