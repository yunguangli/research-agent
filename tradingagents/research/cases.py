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

import contextlib
import json
from dataclasses import dataclass, field

from .kernel import ModelResult
from .schemas import ModelError
from .valuation import Valuation

# A case may argue a wide move when the evidence supports it; the ceiling is a
# guard against arithmetic drift, not a target. The reviewer's prompt uses the
# same numbers, and a case beyond a cap is still rejected.
CAP_FORECAST_PCT = 25.0  # revenue or operating profit, relative to the base
CAP_MULTIPLE_PCT = 20.0  # the P/E a case is allowed to argue for
STANCES = ("aggressive", "neutral", "bearish")


@dataclass
class Case:
    stance: str
    probability: float = 0.34
    revenue_pct: float = 0.0
    operating_profit_pct: float = 0.0
    multiple: float | None = None
    # A multiple argument needs provenance: "pe_model_band_v1" when the case
    # keeps the model's own multiple, a versioned case source when it shocks it.
    multiple_source_id: str = ""
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
            multiple_source_id=str(data.get("multiple_source_id", "") or ""),
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
            "multiple_source_id": self.multiple_source_id,
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
    net_profit: float | None
    eps: float | None
    multiple: float
    target: float | None
    applied: dict[str, float]
    notes: list[str] = field(default_factory=list)
    # "verified" (stated tax), "indicative" (implied from the base year) or
    # "insufficient" (stopped at operating profit).
    basis: str = "verified"
    reconciled: bool = True
    multiple_source_id: str = ""
    multiple_changed: bool = False

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
            "basis": self.basis,
            "reconciled": self.reconciled,
            "multiple_source_id": self.multiple_source_id,
            "multiple_changed": self.multiple_changed,
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


def _tax_basis(model: ModelResult, period: str) -> tuple[float | None, str]:
    """(rate, basis) for the scenario's tax step.

    Hierarchy: a stated forecast rate (verified), else the rate implied by the
    base year's own ordinary-profit and net-profit rows (indicative), else no
    rate — the case then stops at operating profit instead of inventing one.
    """
    spec = model.spec
    if spec.effective_tax_rate is not None:
        return float(spec.effective_tax_rate), "verified"
    row = model.row(period)
    if row.ordinary_profit and row.net_profit is not None and row.ordinary_profit > 0:
        return 1 - row.net_profit / row.ordinary_profit, "indicative"
    return None, "insufficient"


def _eps_shares(model: ModelResult, period: str, basis: str) -> float | None:
    """The period's forecast diluted count, else the named basis.

    The scenarios use the same denominator as the published EPS, so a case's
    per-share value reconciles with the base row it argues against.
    """
    from . import capital

    structure = model.spec.capital_structure
    if structure is not None:
        try:
            target = model.spec.period(period)
        except ModelError:
            target = None
        value, _reason = capital.forecast_shares(
            structure,
            period,
            getattr(target, "end", "") if target else "",
            getattr(target, "start", "") if target else "",
        )
        if value:
            return value
    try:
        return model.spec.share_base(basis).shares_m
    except ModelError:
        return None


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

    # The scenario chain, one step at a time: operating-profit delta → pretax →
    # tax → net income → EPS → value. A ratio shortcut (total OP x net/OP)
    # silently prices tax, non-operating items and minority interest as if they
    # scaled with operations, which is exactly what a scenario must not do.
    from . import kernel

    op_delta = operating_profit - row.operating_profit
    tax, basis = _tax_basis(model, valuation.period)
    net_profit = None
    eps = None
    target = None
    shares = None
    reconciled = True
    if tax is None:
        basis = "insufficient"
        notes.append(
            "SENSITIVITY_PROPAGATION_FAILED: no tax basis is stated or implied, so net "
            "income, EPS and fair value are withheld; the case stops at operating profit"
        )
    else:
        net_profit = row.net_profit + op_delta * (1 - tax)
        shares = _eps_shares(model, valuation.period, valuation.basis)
        if shares:
            eps = kernel.per_share(net_profit, shares, model.spec.unit)
        else:
            reconciled = False
            notes.append("case rejected: no verified share count for the EPS step")
        if basis == "indicative":
            notes.append(
                "tax basis is implied from the base year: net income, EPS and fair "
                "value are INDICATIVE"
            )
        if model.spec.net_debt is not None and model.spec.net_debt.minority_interest:
            notes.append("minority interest is held at its base level, not re-estimated")
        # The chain must return the numbers it published, or the case is rejected.
        expected_net = row.net_profit + op_delta * (1 - tax)
        if abs(net_profit - expected_net) > max(0.5, abs(expected_net) * 1e-6):
            reconciled = False
        if eps is not None and shares:
            restored = kernel.market_cap(eps, shares, model.spec.unit)
            if abs(restored - net_profit) > max(0.5, abs(net_profit) * 1e-6):
                reconciled = False
        if not reconciled:
            notes.append(
                "case rejected: operating profit, net income, EPS and value do not reconcile"
            )
            net_profit = eps = None

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

    target = eps * clamped_multiple if eps is not None else None
    # A scenario multiple carries its provenance: the model's own band when it
    # is unchanged, a versioned case source when it is deliberately shocked.
    multiple_changed = bool(
        argues_multiple and abs(clamped_multiple - base) > max(0.05, base * 0.005)
    )
    multiple_source_id = case.multiple_source_id or (
        "analyst_case_multiple_v1" if multiple_changed else "pe_model_band_v1"
    )
    return CaseResult(
        stance=case.stance,
        probability=case.probability,
        revenue=revenue,
        operating_profit=operating_profit,
        net_profit=net_profit,
        eps=eps,
        multiple=clamped_multiple,
        target=target,
        applied={"revenue_pct": revenue_pct, "operating_profit_pct": op_pct},
        notes=notes,
        basis=basis,
        reconciled=reconciled,
        multiple_source_id=multiple_source_id,
        multiple_changed=multiple_changed,
    )


def parse_case(text: str) -> Case | None:
    """The last JSON object a debater appended, if it is a valid case.

    A model often wraps the object in a code fence or adds a closing sentence, so
    anything after the object is ignored rather than treated as a parse error.
    That difference is not cosmetic: a failed parse silently drops the case from
    the debate table, leaving the managers with one side of the argument.
    """
    scanned = _scan_cases(text)
    return scanned[-1] if scanned else None


def _scan_cases(text: str) -> list[Case]:
    """Every valid case object in the text, in the order they appear."""
    decoder = json.JSONDecoder()
    found: list[Case] = []
    pos = 0
    while True:
        start = text.find("{", pos)
        if start < 0:
            break
        try:
            data, _end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            pos = start + 1
            continue
        if isinstance(data, dict) and "stance" in data:
            with contextlib.suppress(ModelError):
                found.append(Case.from_dict(data))
        pos = start + 1
    return found


def parse_cases(text: str) -> list[Case]:
    """Every stance's case, in first-appearance order, the last object winning.

    The Adversarial Reviewer appends both stances in one answer (its positive
    and its negative challenge), where the old two-persona debate produced one
    each. Reading only the last object would drop half the case table.
    """
    by_stance: dict[str, Case] = {}
    for case in _scan_cases(text):
        by_stance[case.stance] = case   # first occurrence keeps the position,
    return list(by_stance.values())     # a later one replaces the value


def with_case(existing: str, case: Case) -> str:
    """Add or replace this stance's case in the run's case list."""
    try:
        cases = json.loads(existing) if existing else []
    except json.JSONDecodeError:
        cases = []
    cases = [c for c in cases if c.get("stance") != case.stance]
    cases.append(case.to_dict())
    return json.dumps(cases)


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
        "| Case | Confidence | Revenue Δ | Operating profit Δ | Multiple | Momentum | Sentiment |",
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
        "| Case | Weight (agent confidence) | Revenue | Operating profit | EPS | Multiple | Target | Notes |",
        "|---|---|---|---|---|---|---|---|",
        f"| base (fair-value mid) | {base_probability:.0%} | {row.revenue:,.1f} | {row.operating_profit:,.1f} | "
        f"{row.eps.get(valuation.basis, 0):,.1f} | {base_multiple(valuation):,.1f}x | "
        f"{base_multiple(valuation) * row.eps.get(valuation.basis, 0):,.0f} {currency} | — |",
    ]
    for result in results:
        notes = "; ".join(result.notes).replace("|", "\\|") or "—"
        if not result.reconciled or result.eps is None:
            eps_text = target_text = "REJECTED"
        else:
            eps_text = f"{result.eps:,.1f}"
            target_text = (
                f"{result.target:,.0f} {currency}" if result.target is not None else "n/a"
            )
        lines.append(
            f"| {result.stance} | {result.probability:.0%} | {result.revenue:,.1f} | "
            f"{result.operating_profit:,.1f} | {eps_text} | {result.multiple:,.1f}x | "
            f"{target_text} | {notes} |"
        )
    lines.append("")
    lines.append(
        "Base here is the model's fair-value midpoint (midpoint EPS x the multiple that "
        "reproduces it); each case is that row moved by its capped deltas. The research "
        "package's Base *scenario* target is a separate model line, so the two can differ; "
        "compare cases against this row, and scenarios against that one. Weights are agent "
        "confidence, not calibrated probabilities: no expected value is computed from them."
    )
    return "\n".join(lines)
