"""Whether a valuation input is sourced, derived or merely assumed.

The model has one arithmetic verdict and several evidence classes. A P/E built
from a company-guided EPS and an analyst-chosen multiple is mechanically correct
and economically assumed; collapsing that into a single VERIFIED label is what
let the publication layer overstate it.

This module is the single vocabulary for the second dimension. It classifies an
input, labels it, and turns the pair (calculation, assumption) into one sentence
for the report. It never changes a number.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

COMPANY_REPORTED = "COMPANY_REPORTED"
COMPANY_GUIDANCE = "COMPANY_GUIDANCE"
EXTERNAL_ESTIMATE = "EXTERNAL_ESTIMATE"
DERIVED_FROM_SOURCED_INPUTS = "DERIVED_FROM_SOURCED_INPUTS"
ANALYST_ASSUMPTION = "ANALYST_ASSUMPTION"
UNRESOLVED = "UNRESOLVED"

CLASSES = (
    COMPANY_REPORTED,
    COMPANY_GUIDANCE,
    EXTERNAL_ESTIMATE,
    DERIVED_FROM_SOURCED_INPUTS,
    ANALYST_ASSUMPTION,
    UNRESOLVED,
)

# Strongest first. The published assumption status is the weakest controlling
# input: a company-guided EPS does not make an analyst multiple supported.
_RANK = {
    COMPANY_REPORTED: 5,
    COMPANY_GUIDANCE: 4,
    EXTERNAL_ESTIMATE: 3,
    DERIVED_FROM_SOURCED_INPUTS: 2,
    ANALYST_ASSUMPTION: 1,
    UNRESOLVED: 0,
}

_ALIASES = {
    "company_actual": COMPANY_REPORTED,
    "actual": COMPANY_REPORTED,
    "reported": COMPANY_REPORTED,
    "company_reported": COMPANY_REPORTED,
    "disclosed": COMPANY_REPORTED,
    "company": COMPANY_REPORTED,
    "guidance": COMPANY_GUIDANCE,
    "guided": COMPANY_GUIDANCE,
    "company_guidance": COMPANY_GUIDANCE,
    "consensus": EXTERNAL_ESTIMATE,
    "external": EXTERNAL_ESTIMATE,
    "external_estimate": EXTERNAL_ESTIMATE,
    "broker": EXTERNAL_ESTIMATE,
    "derived": DERIVED_FROM_SOURCED_INPUTS,
    "agent_derived": DERIVED_FROM_SOURCED_INPUTS,
    "derived_from_sourced_inputs": DERIVED_FROM_SOURCED_INPUTS,
    "assumption": ANALYST_ASSUMPTION,
    "analyst_assumption": ANALYST_ASSUMPTION,
    "agent_forecast": ANALYST_ASSUMPTION,
    "internal_note": ANALYST_ASSUMPTION,
    "estimate": ANALYST_ASSUMPTION,
    "unresolved": UNRESOLVED,
    "": UNRESOLVED,
}

EARNINGS_LABELS = {
    COMPANY_REPORTED: "Company-reported earnings",
    COMPANY_GUIDANCE: "Company-guided earnings",
    EXTERNAL_ESTIMATE: "Externally estimated earnings",
    DERIVED_FROM_SOURCED_INPUTS: "Derived from sourced inputs",
    ANALYST_ASSUMPTION: "Analyst-assumed earnings forecast",
    UNRESOLVED: "Unsupported earnings input",
}

MULTIPLE_LABELS = {
    COMPANY_REPORTED: "Company-reported valuation multiple",
    COMPANY_GUIDANCE: "Company-guided valuation multiple",
    EXTERNAL_ESTIMATE: "Externally estimated valuation multiple",
    DERIVED_FROM_SOURCED_INPUTS: "Derived valuation multiple",
    ANALYST_ASSUMPTION: "Analyst-assumed valuation multiple",
    UNRESOLVED: "Unsupported valuation assumption",
}

COMPARABILITY = ("SAME_METRIC_SAME_PERIOD_SAME_BASIS", "ADJUSTED", "NOT_COMPARABLE")


def classify(value) -> str:
    """A source class from prose, an enum or an empty field; never raises."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _ALIASES.get(text, UNRESOLVED)


def rank(source: str) -> int:
    return _RANK.get(str(source or "").upper(), 0)


def weakest(*sources: str) -> str:
    """The source class carrying the least evidence."""
    values = [str(source or "").upper() for source in sources if source]
    return min(values, key=rank) if values else UNRESOLVED


def label(source: str, *, multiple: bool = False) -> str:
    table = MULTIPLE_LABELS if multiple else EARNINGS_LABELS
    return table.get(str(source or "").upper(), table[UNRESOLVED])


def external_estimate_complete(profile: MethodAssumption) -> bool:
    """Whether an external estimate carries the evidence that makes it usable."""
    return bool(
        profile.source_name
        and profile.as_of
        and profile.metric
        and profile.period
        and profile.comparability in COMPARABILITY
        and profile.comparability != "NOT_COMPARABLE"
    )


@dataclass
class MethodAssumption:
    """One valuation method's earnings and multiple evidence."""

    method: str = ""
    earnings_source: str = ""
    multiple_source: str = ""
    source_name: str = ""
    as_of: str = ""
    metric: str = ""
    period: str = ""
    comparability: str = ""
    refs: list[str] = field(default_factory=list)
    note: str = ""

    def normalised(self) -> MethodAssumption:
        earnings = classify(self.earnings_source)
        multiple = classify(self.multiple_source)
        if multiple == EXTERNAL_ESTIMATE and not external_estimate_complete(self):
            multiple = UNRESOLVED
        return MethodAssumption(
            method=self.method,
            earnings_source=earnings,
            multiple_source=multiple,
            source_name=self.source_name,
            as_of=self.as_of,
            metric=self.metric,
            period=self.period,
            comparability=self.comparability,
            refs=list(self.refs),
            note=self.note,
        )

    @property
    def status(self) -> str:
        """The published assumption status: the weaker of the two inputs."""
        return weakest(self.earnings_source, self.multiple_source)

    @property
    def earnings_label(self) -> str:
        return label(self.earnings_source)

    @property
    def multiple_label(self) -> str:
        return label(self.multiple_source, multiple=True)

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "earnings_source": self.earnings_source,
            "multiple_source": self.multiple_source,
            "earnings_label": self.earnings_label,
            "multiple_label": self.multiple_label,
            "status": self.status,
            "source_name": self.source_name,
            "as_of": self.as_of,
            "metric": self.metric,
            "period": self.period,
            "comparability": self.comparability,
            "refs": list(self.refs),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> MethodAssumption:
        data = data if isinstance(data, dict) else {}
        return cls(
            method=str(data.get("method") or ""),
            earnings_source=str(data.get("earnings_source") or ""),
            multiple_source=str(data.get("multiple_source") or ""),
            source_name=str(data.get("source_name") or ""),
            as_of=str(data.get("as_of") or ""),
            metric=str(data.get("metric") or ""),
            period=str(data.get("period") or ""),
            comparability=str(data.get("comparability") or ""),
            refs=[str(item) for item in (data.get("refs") or [])],
            note=str(data.get("note") or ""),
        )


def wording(
    calculation_status: str,
    assumption: MethodAssumption | None,
) -> str:
    """The one published sentence for a valuation range or target."""
    if str(calculation_status or "").upper() != "VERIFIED":
        return "Not mechanically verified."
    if assumption is None:
        return "Mechanically verified; valuation assumptions are not stated."
    earnings = assumption.earnings_source
    multiple = assumption.multiple_source
    both_assumed = earnings == ANALYST_ASSUMPTION and multiple == ANALYST_ASSUMPTION
    if both_assumed:
        return (
            "Mechanically verified; earnings and valuation multiple are analyst "
            "assumptions."
        )
    if multiple in {ANALYST_ASSUMPTION, UNRESOLVED}:
        if multiple == UNRESOLVED:
            return "Mechanically verified; valuation multiple is unsupported."
        return "Mechanically verified; valuation multiple is an analyst assumption."
    if earnings in {ANALYST_ASSUMPTION, UNRESOLVED}:
        if earnings == UNRESOLVED:
            return "Mechanically verified; earnings input is unsupported."
        return "Mechanically verified; earnings forecast is an analyst assumption."
    if multiple == EXTERNAL_ESTIMATE:
        return "Mechanically verified; valuation multiple is externally estimated."
    if multiple == DERIVED_FROM_SOURCED_INPUTS:
        return "Mechanically verified; valuation multiple is derived from sourced inputs."
    return "Verified valuation input."


def for_spec(spec, method: str = "pe") -> MethodAssumption:
    """The profile a spec declares for ``method``, with sane defaults.

    A spec without an explicit record is not silently supported: the earnings
    input defaults to the forecast block (an analyst forecast) and the multiple
    to an analyst assumption, which is what the model actually did.
    """
    declared = getattr(getattr(spec, "valuation", None), "assumptions", None) or []
    record = next(
        (item for item in declared if str(item.method or "").lower() == method.lower()),
        None,
    )
    if record is None:
        record = MethodAssumption(method=method)
    if not record.earnings_source:
        record = replace(record, earnings_source=ANALYST_ASSUMPTION)
    if not record.multiple_source:
        provenance = ""
        if method.lower() == "ev_ebitda":
            provenance = str(
                getattr(spec.valuation, "ev_ebitda_multiple_provenance", "") or ""
            )
        record = replace(record, multiple_source=provenance or ANALYST_ASSUMPTION)
    normalised = record.normalised()
    normalised.method = method
    return normalised


def controlling(spec, method_statuses: dict | None) -> MethodAssumption | None:
    """The assumption profile behind the controlling valuation method."""
    statuses = method_statuses or {}
    if str(statuses.get("pe") or "").upper() == "VERIFIED":
        return for_spec(spec, "pe")
    if str(statuses.get("ev_ebitda") or "").upper() == "VERIFIED":
        return for_spec(spec, "ev_ebitda")
    return None
