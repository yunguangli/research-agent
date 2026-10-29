"""Derive the remaining quarters of the current fiscal year.

The analyst knows Q1 actuals and the company's H1 and full-year guidance. Those
three facts fix Q2 exactly and fix the H2 total exactly; the split of H2 between
Q3 and Q4 is the one genuine judgement left. This module makes that judgement
explicit and auditable instead of hiding it inside a copied table:

    Q2 = H1 guidance - Q1 actual
    H2 = FY guidance - H1 guidance
    Q3 = H2 x q3_share
    Q4 = H2 - Q3          (so the year always ties to guidance)

The split is per metric, because a company can grow revenue in H2 while margin
mix shifts operating profit to a different quarter.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .schemas import Financials, ModelError, ModelSpec

METRICS = ("revenue", "operating_profit", "net_profit")


@dataclass
class QuarterlyPlan:
    """How to split H2. Shares are fractions of the H2 total, and must sum to 1."""

    fy_label: str
    h1_label: str
    q1_label: str
    h2_split: dict[str, tuple[float, float]] = field(default_factory=dict)
    note: str = ""

    def share(self, metric: str) -> tuple[float, float]:
        q3, _ = self.h2_split.get(metric, (0.5, 0.5))
        return q3, 1.0 - q3

    def to_dict(self) -> dict:
        return {
            "fy_label": self.fy_label,
            "h1_label": self.h1_label,
            "q1_label": self.q1_label,
            "h2_split": {k: list(v) for k, v in self.h2_split.items()},
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict) -> QuarterlyPlan:
        split = {}
        for metric, value in (data.get("h2_split") or {}).items():
            q3 = float(value[0] if isinstance(value, (list, tuple)) else value)
            if not 0 < q3 < 1:
                raise ModelError(f"h2_split for {metric} must be a fraction between 0 and 1, got {q3}")
            split[metric] = (q3, 1.0 - q3)
        return cls(
            fy_label=data["fy_label"],
            h1_label=data["h1_label"],
            q1_label=data["q1_label"],
            h2_split=split,
            note=data.get("note", ""),
        )


@dataclass
class QuarterLine:
    label: str
    metric: str
    value: float
    formula: str


def build(spec: ModelSpec, plan: QuarterlyPlan) -> tuple[dict[str, Financials], list[QuarterLine]]:
    """Compute Q2-Q4 from Q1 actual and H1/FY guidance."""
    q1 = spec.quarterly.get(plan.q1_label)
    if q1 is None:
        raise ModelError(f"Quarterly plan has no actuals for {plan.q1_label}")
    h1 = spec.guidance.get(plan.h1_label)
    fy = spec.guidance.get(plan.fy_label)
    if h1 is None or fy is None:
        raise ModelError(
            f"Quarterly plan needs both {plan.h1_label} and {plan.fy_label} guidance"
        )

    suffix = plan.fy_label.split(" ", 1)[-1] if " " in plan.fy_label else plan.fy_label
    labels = (plan.q1_label, f"Q2 {suffix}", f"Q3 {suffix}", f"Q4 {suffix}")
    quarters = {label: Financials() for label in labels}
    lines: list[QuarterLine] = []

    for metric in METRICS:
        first = q1.get(metric)
        half = h1.get(metric)
        year = fy.get(metric)
        if first is None or half is None or year is None:
            continue
        second = half - first
        h2 = year - half
        q3_share, _ = plan.share(metric)
        third = h2 * q3_share
        fourth = h2 - third

        setattr(quarters[labels[0]], metric, first)
        setattr(quarters[labels[1]], metric, second)
        setattr(quarters[labels[2]], metric, third)
        setattr(quarters[labels[3]], metric, fourth)

        lines.extend([
            QuarterLine(labels[0], metric, first, f"{plan.q1_label} actual"),
            QuarterLine(labels[1], metric, second,
                        f"{plan.h1_label} {half:g} - {plan.q1_label} {first:g}"),
            QuarterLine(labels[2], metric, third,
                        f"H2 {h2:g} x {q3_share:.3f}"),
            QuarterLine(labels[3], metric, fourth,
                        f"H2 {h2:g} - Q3 {third:g}"),
        ])
    return quarters, lines


def compare(
    derived: dict[str, Financials], reported: dict[str, Financials], tol: float
) -> list[str]:
    """Differences between a derived quarter and a reported one, as messages."""
    problems = []
    for label, reported_fin in reported.items():
        derived_fin = derived.get(label)
        if derived_fin is None:
            problems.append(f"Reported quarter {label} is not produced by the plan")
            continue
        for metric in METRICS:
            expected = reported_fin.get(metric)
            actual = derived_fin.get(metric)
            if expected is None or actual is None:
                continue
            if abs(expected - actual) > tol:
                problems.append(
                    f"{label} {metric}: plan derives {actual:g}, source reports {expected:g}"
                )
    return problems
