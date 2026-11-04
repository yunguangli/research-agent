"""Deterministic model issues, collected before any LLM is asked.

The kernel refuses a model that does not tie, so at this point the identities
hold. What remains are the checks that are warnings by design — a label that
disagrees with its dates, two share bases that count different things, a segment
booking revenue before it is commissioned — plus the recorded source variances.
They are issues, not repairs: nothing here changes a number, and nothing here can
be downgraded by an LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from . import kernel, periods, shares
from .schemas import ModelError, ModelSpec

Severity = Literal["blocking", "material", "scenario_only"]


@dataclass
class Issue:
    kind: str
    severity: Severity
    detail: str
    fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "detail": self.detail,
            "fields": self.fields,
        }


def collect(spec: ModelSpec) -> list[Issue]:
    """Every deterministic issue the *current* spec carries."""
    issues: list[Issue] = []
    for message in periods.audit_periods(spec):
        issues.append(Issue("period_label", "material", message, ["period label"]))
    for message in shares.basis_warnings(spec):
        issues.append(Issue("share_basis", "material", message, ["share basis"]))
    for warning in spec.warnings:
        if "commissioned in" in warning:
            issues.append(Issue("commissioning", "material", warning, ["segment timing"]))
    try:
        model = kernel.run(spec, strict=False)
    except ModelError:
        return issues
    for conflict in getattr(model, "conflicts", []):
        issues.append(
            Issue(
                "source_variance",
                "scenario_only",
                f"{conflict.period} {conflict.metric}: "
                + ", ".join(
                    f"{candidate['source']} {candidate['value']:g}"
                    for candidate in conflict.candidates
                ),
                [f"{conflict.period} {conflict.metric}"],
            )
        )
    return issues


def counts(issues: list[Issue]) -> dict[str, int]:
    return {
        "blocking": sum(1 for issue in issues if issue.severity == "blocking"),
        "material": sum(1 for issue in issues if issue.severity == "material"),
        "scenario_only": sum(1 for issue in issues if issue.severity == "scenario_only"),
    }
