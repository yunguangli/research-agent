"""Record where the model, management and consensus disagree, and by how much.

This is a register, not an arbiter: the precedence rules already live in the
builder (a filing beats a vendor statement, a disclosed actual beats a forecast)
and are applied before the spec exists. What a debate needs is the *list* of
disagreements that survived — which figure each source gives, which one the
model publishes, and whether the gap is material.

A 0.5% tolerance keeps rounding noise out; a gap above it is recorded. The
register is intentionally small: metric, period, candidates, controlling value.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .kernel import ModelResult

TOLERANCE = 0.005   # 0.5%: below this, two sources are stating the same number

_METRICS = ("revenue", "operating_profit", "net_profit")


@dataclass
class Conflict:
    metric: str
    period: str
    candidates: list[dict] = field(default_factory=list)
    controlling_value: float | None = None
    controlling_source: str = ""
    reason: str = ""
    material: bool = False

    def to_dict(self) -> dict:
        return {
            "metric": self.metric,
            "period": self.period,
            "candidates": self.candidates,
            "controlling_value": self.controlling_value,
            "controlling_source": self.controlling_source,
            "reason": self.reason,
            "material": self.material,
        }


def register(model: ModelResult, tolerance: float = TOLERANCE) -> list[Conflict]:
    """Every metric-period where two sources differ by more than ``tolerance``."""
    spec = model.spec
    conflicts: list[Conflict] = []
    for row in model.rows:
        guided = spec.guidance.get(row.label)
        consensus = (spec.consensus.periods.get(row.label) if spec.consensus else None) or {}
        for metric in _METRICS:
            candidates: list[dict] = []
            model_value = row.__dict__.get(metric)
            for source, block in (("company guidance", guided), ("consensus", consensus)):
                if block is None:
                    continue
                value = block.get(metric) if isinstance(block, dict) else getattr(block, metric, None)
                if value is not None:
                    candidates.append({"value": float(value), "source": source})
            if model_value is not None:
                candidates.append({"value": float(model_value), "source": f"model ({row.basis})"})
            if len(candidates) < 2:
                continue
            values = [c["value"] for c in candidates]
            spread = max(values) - min(values)
            if spread <= tolerance * max(abs(v) for v in values):
                continue
            controlling = candidates[-1] if model_value is not None else candidates[0]
            conflicts.append(
                Conflict(
                    metric=metric,
                    period=row.label,
                    candidates=candidates,
                    controlling_value=controlling["value"],
                    controlling_source=controlling["source"],
                    reason=(
                        "the model publishes its own forecast; guidance and consensus are "
                        "kept as reference"
                        if row.basis != "actual"
                        else "the disclosed actual controls"
                    ),
                    material=True,
                )
            )
    return conflicts


def render(conflicts: list[Conflict], limit: int = 12) -> str:
    """A compact table for the research package, or "" when nothing is material."""
    if not conflicts:
        return ""
    lines = [
        "Source variances (record only: the model's figure is the one published):",
        "| Period | Metric | Guidance | Consensus | Model |",
        "|---|---|---|---|---|",
    ]
    for conflict in conflicts[:limit]:
        by_source = {c["source"]: c["value"] for c in conflict.candidates}
        model = next((v for s, v in by_source.items() if s.startswith("model")), None)
        lines.append(
            f"| {conflict.period} | {conflict.metric.replace('_', ' ')} | "
            f"{by_source.get('company guidance', '—')} | "
            f"{by_source.get('consensus', '—')} | {model if model is not None else '—'} |"
        )
    if len(conflicts) > limit:
        lines.append(f"({len(conflicts) - limit} more in the appendix)")
    return "\n".join(lines)
