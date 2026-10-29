"""Turn a debater's pitch into a bounded case on the same model.

A pitch is not a second forecast. It is a set of named deltas on the base case:
revenue growth, operating margin, the multiple the market should pay, and how the
technical and sentiment evidence changes the odds. Python applies them, so every
case is arithmetic on the same disclosures and can be compared line by line.

Caps exist because an uncapped pitch is a different company. A case that needs
more than the cap is rejected and the cap is reported, rather than silently
accepted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .kernel import ModelResult
from .schemas import ModelError
from .valuation import Valuation

CAP_FORECAST_PCT = 5.0     # revenue or operating profit, relative to the base
CAP_MULTIPLE_PCT = 20.0    # the P/E a case is allowed to argue for
STANCES = ("aggressive", "neutral", "bearish")


@dataclass
class Case:
    stance: str
    probability: float = 0.34
    revenue_pct: float = 0.0
    operating_profit_pct: float = 0.0
    multiple: float | None = None
    momentum: str = ""
    sentiment: str = ""
    evidence: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> Case:
        stance = str(data.get("stance", "")).strip().lower()
        if stance not in STANCES:
            raise ModelError(f"Unknown stance {stance!r}; expected one of {STANCES}")
        return cls(
            stance=stance,
            probability=_clamp(float(data.get("probability", 0.34) or 0.0), 0.0, 1.0),
            revenue_pct=float(data.get("revenue_pct", 0.0) or 0.0),
            operating_profit_pct=float(data.get("operating_profit_pct", 0.0) or 0.0),
            multiple=_opt_float(data.get("multiple")),
            momentum=str(data.get("momentum", "") or ""),
            sentiment=str(data.get("sentiment", "") or ""),
            evidence=[str(item) for item in (data.get("evidence") or [])][:6],
        )

    def to_dict(self) -> dict:
        return {
            "stance": self.stance,
            "probability": self.probability,
            "revenue_pct": self.revenue_pct,
            "operating_profit_pct": self.operating_profit_pct,
            "multiple": self.multiple,
            "momentum": self.momentum,
            "sentiment": self.sentiment,
            "evidence": self.evidence,
        }


@dataclass
class CaseResult:
    stance: str
    probability: float
    revenue: float
    operating_profit: float
    net_profit: float
    eps: float
    multiple: float
    target: float
    applied: dict[str, float]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "stance": self.stance,
            "probability": self.probability,
            "revenue": self.revenue,
            "operating_profit": self.operating_profit,
            "net_profit": self.net_profit,
            "eps": self.eps,
            "multiple": self.multiple,
            "target": self.target,
            "applied": self.applied,
            "notes": self.notes,
        }


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _opt_float(value) -> float | None:
    if value in (None, "", "n/a"):
        return None
    return float(value)


def base_multiple(valuation: Valuation) -> float:
    """The multiple the base case already implies, at the middle of its range."""
    return valuation.fair_value_mid / valuation.eps if valuation.eps else 0.0


def _stance_sign(stance: str, value: float, label: str, notes: list[str]) -> float:
    """Point a case's delta at its own stance: a bear argues down, a bull up.

    A delta is measured against the base case, so a bear that moves revenue up is
    arguing the bull's case. Flipping the sign keeps the argument and says what
    happened; rejecting it would drop the case, and clamping to zero would leave a
    bearish row identical to the base.
    """
    if stance == "bearish" and value > 0:
        notes.append(
            f"bearish {label} delta {value:+.1f}% applied as {-value:+.1f}%: "
            "a bear case argues below the base"
        )
        return -value
    if stance == "aggressive" and value < 0:
        notes.append(
            f"bullish {label} delta {value:+.1f}% applied as {-value:+.1f}%: "
            "a bull case argues above the base"
        )
        return -value
    return value


def apply_case(model: ModelResult, valuation: Valuation, case: Case) -> CaseResult:
    """Recompute the base model under a case's capped deltas."""
    notes: list[str] = []
    row = model.row(valuation.period)
    if row.revenue is None or row.operating_profit is None or row.net_profit is None:
        raise ModelError(f"The base model has no complete {valuation.period} row to adjust")

    proposed_revenue = case.revenue_pct
    proposed_op = case.operating_profit_pct
    revenue_pct = _stance_sign(case.stance, proposed_revenue, "revenue", notes)
    op_pct = _stance_sign(case.stance, proposed_op, "operating-profit", notes)
    capped_revenue = _clamp(revenue_pct, -CAP_FORECAST_PCT, CAP_FORECAST_PCT)
    capped_op = _clamp(op_pct, -CAP_FORECAST_PCT, CAP_FORECAST_PCT)
    if capped_revenue != revenue_pct:
        notes.append(
            f"revenue delta capped at {CAP_FORECAST_PCT:+.0f}% (proposed {proposed_revenue:+.1f}%)"
        )
    if capped_op != op_pct:
        notes.append(
            f"operating-profit delta capped at {CAP_FORECAST_PCT:+.0f}% "
            f"(proposed {proposed_op:+.1f}%)"
        )
    revenue_pct, op_pct = capped_revenue, capped_op

    revenue = row.revenue * (1 + revenue_pct / 100)
    operating_profit = row.operating_profit * (1 + op_pct / 100)

    # Net profit moves with operating profit; tax and interest held at the base
    # ratio, because a case argues about operations, not about the tax line.
    net_profit = row.net_profit * (operating_profit / row.operating_profit)

    base = base_multiple(valuation)
    multiple = case.multiple if case.multiple else base
    # A case that proposes the base multiple is not arguing about it; the tiny
    # difference is rounding in the base's own arithmetic, not a position.
    argues_multiple = case.multiple is not None and abs(case.multiple - base) > base * 0.005
    if argues_multiple and not case.evidence:
        notes.append("multiple argument has no evidence; kept at the base")
        multiple = base
    # A bear arguing a higher multiple than the base (or a bull arguing lower) is
    # contradictory; clamp to the base and say so rather than publish it.
    if argues_multiple and case.stance == "aggressive" and multiple < base:
        notes.append(f"multiple raised to the base {base:.1f}x: a bull case cannot argue a lower multiple")
        multiple = base
    elif argues_multiple and case.stance == "bearish" and multiple > base:
        notes.append(f"multiple cut to the base {base:.1f}x: a bear case cannot argue a higher multiple")
        multiple = base
    low, high = base * (1 - CAP_MULTIPLE_PCT / 100), base * (1 + CAP_MULTIPLE_PCT / 100)
    clamped_multiple = _clamp(multiple, low, high)
    if argues_multiple and abs(clamped_multiple - case.multiple) > 1e-9:
        notes.append(
            f"multiple limited to {clamped_multiple:.1f}x (proposed {case.multiple:.1f}x, "
            f"base {base:.1f}x ±{CAP_MULTIPLE_PCT:.0f}%)"
        )

    shares = model.spec.share_base(valuation.basis).shares_m
    eps = net_profit * 1000.0 / shares if model.spec.unit.endswith("bn") else net_profit / shares
    return CaseResult(
        stance=case.stance,
        probability=case.probability,
        revenue=revenue,
        operating_profit=operating_profit,
        net_profit=net_profit,
        eps=eps,
        multiple=clamped_multiple,
        target=eps * clamped_multiple,
        applied={"revenue_pct": revenue_pct, "operating_profit_pct": op_pct},
        notes=notes,
    )


def parse_case(text: str) -> Case | None:
    """The last JSON object a debater appended, if it is a valid case.

    A model often wraps the object in a code fence or adds a closing sentence, so
    anything after the object is ignored rather than treated as a parse error.
    That difference is not cosmetic: a failed parse silently drops the case from
    the debate table, leaving the managers with one side of the argument.
    """
    decoder = json.JSONDecoder()
    start = text.rfind("{")
    while start >= 0:
        try:
            data, _end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            start = text.rfind("{", 0, start)
            continue
        if isinstance(data, dict) and "stance" in data:
            try:
                return Case.from_dict(data)
            except ModelError:
                pass
        start = text.rfind("{", 0, start)
    return None


def normalize_probabilities(results: list[CaseResult]) -> list[CaseResult]:
    """Scale the cases' probabilities so they sum to one."""
    total = sum(result.probability for result in results)
    if total <= 0:
        for result in results:
            result.probability = 1 / len(results)
        return results
    for result in results:
        result.probability = result.probability / total
    return results


def strip_case_json(text: str) -> str:
    """Remove case JSON lines from a transcript before showing it to the opponent.

    The stored debate keeps the JSON; the prompt does not. Otherwise the bear reads
    the bull's multiple and copies it, and every case ends up at the same number.
    """
    return "\n".join(
        line
        for line in text.splitlines()
        if not (line.strip().startswith("{") and '"stance"' in line)
    )


def render_proposals(cases: list[dict]) -> str:
    """The cases as the debaters proposed them: deltas, not recomputed numbers."""
    if not cases:
        return ""
    lines = [
        "| Case | Probability | Revenue Δ | Operating profit Δ | Multiple | Momentum | Sentiment |",
        "|---|---|---|---|---|---|---|",
    ]
    for raw in cases:
        case = Case.from_dict(raw)
        multiple = f"{case.multiple:.1f}x" if case.multiple else "base"
        lines.append(
            f"| {case.stance} | {case.probability:.0%} | {case.revenue_pct:+.1f}% | "
            f"{case.operating_profit_pct:+.1f}% | {multiple} | "
            f"{case.momentum[:60]} | {case.sentiment[:60]} |"
        )
    return "\n".join(lines)


def render_results(model: ModelResult, valuation: Valuation, cases: list[dict]) -> str:
    """The cases recomputed on the base model, with the base row for comparison."""
    row = model.row(valuation.period)
    results = [apply_case(model, valuation, Case.from_dict(c)) for c in cases]

    # The base case is the model itself and starts with the weight of one case.
    # A 50/50 bull-versus-bear split therefore means base 50%, 25% each, not a
    # base that nobody believes.
    weights = [1.0] + [max(result.probability, 0.0) for result in results]
    total = sum(weights) or 1.0
    base_probability = weights[0] / total
    for result, weight in zip(results, weights[1:], strict=True):
        result.probability = weight / total

    currency = model.spec.currency

    lines = [
        "| Case | Probability | Revenue | Operating profit | EPS | Multiple | Target | Notes |",
        "|---|---|---|---|---|---|---|---|",
        f"| base (fair-value mid) | {base_probability:.0%} | {row.revenue:,.1f} | {row.operating_profit:,.1f} | "
        f"{row.eps.get(valuation.basis, 0):,.1f} | {base_multiple(valuation):,.1f}x | "
        f"{base_multiple(valuation) * row.eps.get(valuation.basis, 0):,.0f} {currency} | — |",
    ]
    for result in results:
        notes = "; ".join(result.notes)[:60] or "—"
        lines.append(
            f"| {result.stance} | {result.probability:.0%} | {result.revenue:,.1f} | "
            f"{result.operating_profit:,.1f} | {result.eps:,.1f} | {result.multiple:,.1f}x | "
            f"{result.target:,.0f} {currency} | {notes} |"
        )
    lines.append("")
    lines.append(
        "Base here is the model's fair-value midpoint (midpoint EPS x the multiple that "
        "reproduces it); each case is that row moved by its capped deltas. The research "
        "package's Base *scenario* target is a separate model line, so the two can differ; "
        "compare cases against this row, and scenarios against that one."
    )
    return "\n".join(lines)
