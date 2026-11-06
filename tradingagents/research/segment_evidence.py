"""Current-run segment evidence, and the reconciliation requirement it drives.

The cached model is not the evidence. Segment disclosure comes from the current
Fundamentals Analyst's machine-readable block; the cached spec only supplies the
forecast-year segment rows that must be checked against it. A cached
``NOT_REQUIRED`` is provisional until this audit runs.

The block is stripped from the narrative before publication. A Markdown table is
parsed only as a labelled legacy fallback for diagnostics; it can never support a
production claim.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .segments import TOP_DOWN_SENTENCE

MARKER = "SEGMENT_EVIDENCE:"
STRUCTURED = "STRUCTURED"
LEGACY_MARKDOWN_FALLBACK = "LEGACY_MARKDOWN_FALLBACK"
MISSING = "MISSING"

NO_SEGMENT_DISCLOSURE = "NO_SEGMENT_DISCLOSURE"
SEGMENT_DATA_DISCLOSED = "SEGMENT_DATA_DISCLOSED"
SEGMENT_DATA_INCOMPLETE = "SEGMENT_DATA_INCOMPLETE"

NOT_REQUIRED = "NOT_REQUIRED"
REQUIRED_AND_PRESENT = "REQUIRED_AND_PRESENT"
REQUIRED_BUT_INSUFFICIENT_DATA = "REQUIRED_BUT_INSUFFICIENT_DATA"
REQUIRED_BUT_MISSING = "REQUIRED_BUT_MISSING"

# The published status. The two REQUIRED_BUT_* values are internal distinctions
# (evidence incomplete vs forecast rows missing); both mean the segment build is
# incomplete and the forecast is top-down.
INCOMPLETE = "INCOMPLETE"

BOTTOM_UP = "BOTTOM_UP"
TOP_DOWN = "TOP_DOWN"
NOT_APPLICABLE = "NOT_APPLICABLE"

SCHEMA_MISSING = "SEGMENT_EVIDENCE_SCHEMA_MISSING"
DEFAULT_THRESHOLD = 0.60

# An empty array is only a valid answer when the analyst names one of these
# reasons. Otherwise it is ambiguous and treated as missing data, never as
# "no disclosure".
VALID_EMPTY_REASONS = (NO_SEGMENT_DISCLOSURE, SEGMENT_DATA_INCOMPLETE)


@dataclass
class SegmentFact:
    segment_name: str
    period: str
    revenue: float
    operating_profit: float
    currency: str = ""
    source_ids: list[str] = field(default_factory=list)
    evidence_status: str = "REPORTED"


@dataclass
class SegmentEvidence:
    facts: list[SegmentFact] = field(default_factory=list)
    disclosure_status: str = ""
    source: str = MISSING
    clean_text: str = ""
    findings: list[str] = field(default_factory=list)
    schema_missing: bool = False
    raw: dict | None = None

    def to_dict(self) -> dict:
        return {
            "facts": [
                {
                    "segment_name": fact.segment_name,
                    "period": fact.period,
                    "revenue": fact.revenue,
                    "operating_profit": fact.operating_profit,
                    "currency": fact.currency,
                    "source_ids": list(fact.source_ids),
                    "evidence_status": fact.evidence_status,
                }
                for fact in self.facts
            ],
            "disclosure_status": self.disclosure_status,
            "source": self.source,
            "findings": list(self.findings),
            "schema_missing": self.schema_missing,
            "raw": self.raw,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> SegmentEvidence:
        data = data if isinstance(data, dict) else {}
        facts = []
        for row in data.get("facts") or []:
            if not isinstance(row, dict):
                continue
            try:
                facts.append(
                    SegmentFact(
                        segment_name=str(row.get("segment_name") or ""),
                        period=str(row.get("period") or ""),
                        revenue=float(row.get("revenue") or 0.0),
                        operating_profit=float(row.get("operating_profit") or 0.0),
                        currency=str(row.get("currency") or ""),
                        source_ids=[str(ref) for ref in (row.get("source_ids") or [])],
                        evidence_status=str(row.get("evidence_status") or "REPORTED"),
                    )
                )
            except (TypeError, ValueError):
                continue
        return cls(
            facts=facts,
            disclosure_status=str(data.get("disclosure_status") or ""),
            source=str(data.get("source") or MISSING),
            findings=[str(item) for item in (data.get("findings") or [])],
            schema_missing=bool(data.get("schema_missing", False)),
            raw=data.get("raw") if isinstance(data.get("raw"), dict) else None,
        )


def _number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _parse_block(text: str, allow_bare: bool = False) -> SegmentEvidence:
    result = SegmentEvidence(clean_text=text or "")
    source_text = text or ""
    marker = source_text.find(MARKER)
    if marker >= 0:
        payload = source_text[marker + len(MARKER):]
        result.clean_text = source_text[:marker].rstrip()
    elif allow_bare:
        # A correction answer may omit the marker and return only the object.
        payload = source_text
        result.clean_text = ""
    else:
        return result
    start = payload.find("{")
    if start < 0:
        result.schema_missing = True
        result.findings.append(f"{SCHEMA_MISSING}: no JSON object followed the marker")
        return result
    try:
        data, _end = json.JSONDecoder().raw_decode(payload[start:])
    except ValueError as exc:
        result.schema_missing = True
        result.findings.append(f"{SCHEMA_MISSING}: the block is not valid JSON ({exc})")
        return result
    result.raw = data if isinstance(data, dict) else None
    if not isinstance(data, dict):
        result.schema_missing = True
        result.findings.append(f"{SCHEMA_MISSING}: the block is not an object")
        return result
    result.disclosure_status = str(data.get("segment_disclosure_status") or "").strip()
    rows = data.get("segment_evidence")
    if not isinstance(rows, list):
        result.schema_missing = True
        result.findings.append(f"{SCHEMA_MISSING}: segment_evidence is not a list")
        return result
    result.source = STRUCTURED
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            result.findings.append(f"SEGMENT_EVIDENCE_ROW_{index}: not an object")
            continue
        name = str(row.get("segment_name") or "").strip()
        period = str(row.get("period") or "").strip()
        revenue = _number(row.get("revenue"))
        profit = _number(row.get("operating_profit"))
        refs = [str(ref) for ref in (row.get("source_ids") or []) if str(ref).strip()]
        if not name or not period or revenue is None or profit is None:
            result.findings.append(
                f"SEGMENT_EVIDENCE_ROW_{index}: missing segment_name/period/revenue/operating_profit"
            )
            continue
        if not refs:
            result.findings.append(f"SEGMENT_EVIDENCE_ROW_{index}: no source_ids")
            continue
        result.facts.append(
            SegmentFact(
                segment_name=name,
                period=period,
                revenue=revenue,
                operating_profit=profit,
                currency=str(row.get("currency") or "").strip(),
                source_ids=refs,
                evidence_status=str(row.get("evidence_status") or "REPORTED").upper(),
            )
        )
    if not result.facts and not result.disclosure_status:
        result.findings.append(f"{SCHEMA_MISSING}: no usable segment rows")
    return result


def _legacy_table(text: str) -> SegmentEvidence:
    """A cautious Markdown-table fallback, for diagnostics only."""
    result = SegmentEvidence(source=LEGACY_MARKDOWN_FALLBACK, clean_text=text or "")
    if not text:
        return result
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    for index, line in enumerate(lines):
        header = [cell.strip().lower() for cell in line.strip("|").split("|")]
        if not ("segment" in " ".join(header) or "business" in " ".join(header)):
            continue
        if not any("revenue" in cell for cell in header):
            continue
        if not any("profit" in cell or "income" in cell for cell in header):
            continue
        for row in lines[index + 2:]:
            cells = [cell.strip() for cell in row.strip("|").split("|")]
            if len(cells) < 3 or set(cells[0]) <= {"-", ":", " "}:
                break
            revenue, profit = _number(cells[1]), _number(cells[2])
            if revenue is None or profit is None:
                continue
            result.facts.append(
                SegmentFact(
                    segment_name=cells[0],
                    period="legacy-table",
                    revenue=revenue,
                    operating_profit=profit,
                )
            )
        if result.facts:
            return result
    return result


def extract(text: str, allow_bare: bool = False) -> SegmentEvidence:
    """Parse the current Fundamentals report's segment block, or a legacy table."""
    parsed = _parse_block(text or "", allow_bare=allow_bare)
    if parsed.source == STRUCTURED:
        return parsed
    # A missing structured block is a schema failure for production, but a
    # recognizable legacy table still helps diagnostics.
    legacy = _legacy_table(text or "")
    if legacy.facts:
        legacy.schema_missing = True
        legacy.findings.extend(parsed.findings)
        legacy.findings.append(
            f"{SCHEMA_MISSING}: production requires SEGMENT_EVIDENCE; a legacy "
            "Markdown table was used for diagnostics only"
        )
        return legacy
    parsed.schema_missing = True
    if not parsed.findings:
        parsed.findings.append(f"{SCHEMA_MISSING}: the SEGMENT_EVIDENCE block is absent")
    return parsed


def needs_retry(evidence: SegmentEvidence) -> bool:
    """Whether the block is absent, malformed, or ambiguous enough to retry once.

    Valid: structured rows with no row-level defect, or an empty array carrying an
    explicit reason code. Everything else gets one focused correction attempt; a
    still-invalid answer becomes ``SEGMENT_EVIDENCE_SCHEMA_MISSING``.
    """
    if evidence.source != STRUCTURED:
        return True
    if evidence.facts and not evidence.findings:
        return False
    return not (
        not evidence.facts and evidence.disclosure_status in VALID_EMPTY_REASONS
    )


def mark_retry_exhausted(evidence: SegmentEvidence, retry_findings: list[str] | None = None) -> SegmentEvidence:
    """Record a failed correction without inventing evidence."""
    evidence.schema_missing = True
    evidence.findings = list(evidence.findings) + list(retry_findings or [])
    evidence.findings.append(
        f"{SCHEMA_MISSING}: the block was absent or invalid after one focused retry"
    )
    return evidence


def _dominant(facts: list[SegmentFact]) -> tuple[str, float, str] | None:
    if not facts:
        return None
    groups: dict[str, list[SegmentFact]] = {}
    for fact in facts:
        groups.setdefault(fact.period, []).append(fact)
    # The latest disclosed period is the one with the most segment rows; ties go
    # to the last block the analyst printed.
    period = max(groups, key=lambda label: (len(groups[label]), list(groups).index(label)))
    rows = [row for row in groups[period] if row.operating_profit > 0]
    total = sum(row.operating_profit for row in rows)
    if total <= 0 or not rows:
        return None
    row = max(rows, key=lambda item: item.operating_profit)
    return row.segment_name, row.operating_profit / total, period


def annual_forecast_periods(spec) -> list[str]:
    return [
        period.label
        for period in spec.periods
        if period.basis == "forecast" and getattr(period, "kind", "annual") != "quarter"
    ]


@dataclass
class SegmentAudit:
    status: str
    detail: str
    segment: str = ""
    share: float | None = None
    period: str = ""
    source: str = MISSING
    missing_periods: list[str] = field(default_factory=list)
    schema_missing: bool = False
    findings: list[str] = field(default_factory=list)
    limitation: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "detail": self.detail,
            "segment": self.segment,
            "share": self.share,
            "period": self.period,
            "source": self.source,
            "missing_periods": list(self.missing_periods),
            "schema_missing": self.schema_missing,
            "findings": list(self.findings),
            "limitation": self.limitation,
        }


def audit(spec, evidence: SegmentEvidence, threshold: float = DEFAULT_THRESHOLD) -> SegmentAudit:
    """Recompute the segment requirement against current evidence."""
    if evidence.source == MISSING and not evidence.facts:
        if evidence.disclosure_status == NO_SEGMENT_DISCLOSURE:
            return SegmentAudit(NOT_REQUIRED, "the analyst reports no segment disclosure",
                                source=MISSING)
        return SegmentAudit(
            REQUIRED_BUT_INSUFFICIENT_DATA,
            "current segment evidence is unavailable; a cached requirement cannot be final",
            source=MISSING,
            schema_missing=True,
            findings=list(evidence.findings),
        )
    if not evidence.facts:
        if evidence.disclosure_status == NO_SEGMENT_DISCLOSURE:
            return SegmentAudit(NOT_REQUIRED, "the analyst reports no segment disclosure",
                                source=evidence.source)
        return SegmentAudit(
            REQUIRED_BUT_INSUFFICIENT_DATA,
            "the structured block has no usable segment rows",
            source=evidence.source,
            schema_missing=evidence.schema_missing,
            findings=list(evidence.findings),
        )
    if evidence.disclosure_status == SEGMENT_DATA_INCOMPLETE:
        return SegmentAudit(
            REQUIRED_BUT_INSUFFICIENT_DATA,
            "the analyst reports incomplete segment data",
            source=evidence.source,
            schema_missing=evidence.schema_missing,
            findings=list(evidence.findings),
        )
    if evidence.source == LEGACY_MARKDOWN_FALLBACK:
        # Diagnostics and migration only: a Markdown table may never support a
        # production claim, even when it shows no dominant segment.
        return SegmentAudit(
            REQUIRED_BUT_INSUFFICIENT_DATA,
            "the segment evidence comes from a legacy Markdown table and cannot "
            "support publication",
            source=evidence.source,
            schema_missing=True,
            findings=list(evidence.findings),
        )
    found = _dominant(evidence.facts)
    if found is None:
        return SegmentAudit(NOT_REQUIRED, "no positive segment operating profit",
                            source=evidence.source)
    expected_currency = str(getattr(spec, "currency", "") or "").upper()
    mismatched = {
        fact.currency.upper()
        for fact in evidence.facts
        if fact.currency and expected_currency and fact.currency.upper() != expected_currency
    }
    if mismatched:
        return SegmentAudit(
            REQUIRED_BUT_INSUFFICIENT_DATA,
            "segment evidence currency " + ", ".join(sorted(mismatched))
            + f" does not match the model currency {expected_currency}",
            source=evidence.source,
            schema_missing=evidence.schema_missing,
            findings=list(evidence.findings)
            + [f"SEGMENT_EVIDENCE_CURRENCY_MISMATCH: {expected_currency} vs "
               + ", ".join(sorted(mismatched))],
        )
    name, share, period = found
    if share < threshold:
        return SegmentAudit(
            NOT_REQUIRED,
            f"{name} is {share:.1%} of disclosed segment profit, below the "
            f"{threshold:.0%} threshold",
            name, share, period, evidence.source,
        )
    forecast_years = annual_forecast_periods(spec)
    if not forecast_years:
        return SegmentAudit(
            NOT_REQUIRED, "the horizon has no annual forecast years", name, share, period,
            evidence.source,
        )
    if not getattr(spec, "segments", None):
        return SegmentAudit(
            REQUIRED_BUT_MISSING,
            f"{name} is {share:.1%} of disclosed segment profit, but the model has no "
            "segment forecast",
            name, share, period, evidence.source,
            list(forecast_years), evidence.schema_missing, list(evidence.findings),
        )
    missing: list[str] = []
    insufficient: list[str] = []
    for label in forecast_years:
        row = next(
            (item for item in spec.segments.get(label, []) if item.name == name),
            None,
        )
        if row is None:
            missing.append(label)
        elif abs(float(row.revenue or 0.0)) < 1e-9:
            insufficient.append(label)
    if missing:
        return SegmentAudit(
            REQUIRED_BUT_MISSING,
            f"{name} is {share:.1%} of disclosed segment profit in {period}, but the "
            f"model has no segment forecast for {', '.join(missing)}",
            name, share, period, evidence.source, missing, evidence.schema_missing,
            list(evidence.findings),
        )
    if insufficient:
        return SegmentAudit(
            REQUIRED_BUT_INSUFFICIENT_DATA,
            f"{name} is {share:.1%} of disclosed segment profit in {period}, but its "
            f"forecast revenue is missing for {', '.join(insufficient)}",
            name, share, period, evidence.source, insufficient, evidence.schema_missing,
            list(evidence.findings),
        )
    return SegmentAudit(
        REQUIRED_AND_PRESENT,
        f"{name} is {share:.1%} of disclosed segment profit in {period}; "
        "segment forecasts are present",
        name, share, period, evidence.source, [], evidence.schema_missing,
        list(evidence.findings),
    )


def published_status(audit_result: SegmentAudit) -> str:
    """The one status a reader sees."""
    if audit_result.status in {REQUIRED_BUT_MISSING, REQUIRED_BUT_INSUFFICIENT_DATA}:
        return INCOMPLETE
    return audit_result.status


def is_controlling(spec) -> bool:
    """Whether the segment reconciliation controls the valuation or the decision."""
    return bool(getattr(spec, "segment_reconciliation_controlling", False))


@dataclass
class SegmentClassification:
    build_type: str
    bottom_up_claim_allowed: bool
    operating_model_confidence: str

    def to_dict(self) -> dict:
        return {
            "build_type": self.build_type,
            "bottom_up_claim_allowed": self.bottom_up_claim_allowed,
            "operating_model_confidence": self.operating_model_confidence,
        }


def classify(audit_result: SegmentAudit) -> SegmentClassification:
    """How the consolidated forecast was built, and whether that claim is allowed.

    An incomplete segment build is a top-down forecast: it may publish with a
    qualification, but it may not call itself bottom-up or segment-reconciled.
    """
    if audit_result.status == REQUIRED_AND_PRESENT:
        return SegmentClassification(BOTTOM_UP, True, "VERIFIED")
    if audit_result.status == NOT_REQUIRED:
        return SegmentClassification(NOT_APPLICABLE, True, "VERIFIED")
    return SegmentClassification(TOP_DOWN, False, "DEGRADED")


def limitation(audit_result: SegmentAudit) -> str:
    """The visible main-report limitation for an incomplete segment build."""
    published = published_status(audit_result)
    if published in {REQUIRED_AND_PRESENT, NOT_REQUIRED}:
        return ""
    base = audit_result.detail.rstrip(".")
    text = f"SEGMENT_RECONCILIATION_STATUS = {published}. {base}. {TOP_DOWN_SENTENCE}"
    if audit_result.schema_missing:
        text = f"{SCHEMA_MISSING}. {text}"
    return text
