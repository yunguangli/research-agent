"""Typed records for a deterministic fundamental model.

The model is a set of periods, statement lines, and named bridges. Every value
declares how it is known — disclosed, guidance, derived, assumption, or internal
note — and every derived value carries the arithmetic that produced it. The LLM
may propose an assumption; only this module turns assumptions into numbers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

PROVENANCE = ("disclosed", "guidance", "derived", "assumption", "internal_note")
BASIS = ("actual", "guidance", "forecast")

# What a model actually writes when asked for provenance. "actual" and
# "estimate" are the two that appear most often; mapping them is kinder than
# failing a whole forecast over a vocabulary choice.
PROVENANCE_ALIASES = {
    "actual": "disclosed",
    "reported": "disclosed",
    "company": "disclosed",
    "filing": "disclosed",
    "guidance": "guidance",
    "guided": "guidance",
    "forecast": "assumption",
    "estimate": "assumption",
    "estimated": "assumption",
    "consensus": "assumption",
    "derived": "derived",
    "assumption": "assumption",
    "internal": "internal_note",
    "note": "internal_note",
    "internal_note": "internal_note",
}


class ModelError(ValueError):
    """The model is incomplete or internally inconsistent; do not render it."""


@dataclass
class Citation:
    document_id: str
    location: str = ""
    quote: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | str) -> Citation:
        if isinstance(data, str):
            return cls(document_id=data)
        return cls(**{k: v for k, v in data.items() if k in {"document_id", "location", "quote"}})


@dataclass
class Line:
    """One forecast line: a value plus the reason it can be trusted."""

    label: str
    value: float
    provenance: str = "assumption"
    formula: str = ""
    cite: list[Citation] = field(default_factory=list)

    def __post_init__(self) -> None:
        normalised = PROVENANCE_ALIASES.get(str(self.provenance).strip().lower(), self.provenance)
        self.provenance = normalised
        if self.provenance not in PROVENANCE:
            raise ModelError(
                f"Unknown provenance {self.provenance!r} for {self.label!r}; "
                f"use one of {', '.join(PROVENANCE)}"
            )
        if self.provenance == "derived" and not self.formula:
            raise ModelError(f"Derived line {self.label!r} must state its formula")

    def to_dict(self) -> dict:
        data = asdict(self)
        data["cite"] = [c.to_dict() for c in self.cite]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Line:
        return cls(
            label=data["label"],
            value=float(data["value"]),
            provenance=data.get("provenance", "assumption"),
            formula=data.get("formula", ""),
            cite=[Citation.from_dict(c) for c in data.get("cite", [])],
        )


@dataclass
class Bridge:
    """A named walk from one period to the next, e.g. FY3/27 → FY3/28 revenue.

    Bridges are the model's primary object because a forecast for a company with
    a large expiring contract is a set of named deltas, not a growth rate.
    """

    metric: str                       # revenue | operating_profit | net_profit
    base_period: str
    target_period: str
    base_value: float
    lines: list[Line]
    declared_total: float | None = None
    cite: list[Citation] = field(default_factory=list)

    @property
    def total(self) -> float:
        return self.base_value + sum(line.value for line in self.lines)

    @property
    def deltas(self) -> float:
        return sum(line.value for line in self.lines)

    def to_dict(self) -> dict:
        return {
            "metric": self.metric,
            "base_period": self.base_period,
            "target_period": self.target_period,
            "base_value": self.base_value,
            "declared_total": self.declared_total,
            "lines": [line.to_dict() for line in self.lines],
            "cite": [c.to_dict() for c in self.cite],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Bridge:
        return cls(
            metric=data["metric"],
            base_period=data["base_period"],
            target_period=data["target_period"],
            base_value=float(data["base_value"]),
            declared_total=None
            if data.get("declared_total") is None
            else float(data["declared_total"]),
            lines=[Line.from_dict(line) for line in data.get("lines", [])],
            cite=[Citation.from_dict(c) for c in data.get("cite", [])],
        )


@dataclass
class Period:
    label: str
    start: str
    end: str
    basis: str
    kind: str = "annual"          # annual | half | quarter
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Period:
        return cls(**{k: v for k, v in data.items()
                      if k in {"label", "start", "end", "basis", "kind", "note"}})


@dataclass
class Financials:
    """Statement lines for one period, in the model's reporting unit."""

    revenue: float | None = None
    operating_profit: float | None = None
    ordinary_profit: float | None = None
    net_profit: float | None = None

    def get(self, metric: str) -> float | None:
        return getattr(self, metric, None)

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> Financials:
        return cls(
            revenue=_opt(data.get("revenue")),
            operating_profit=_opt(data.get("operating_profit")),
            ordinary_profit=_opt(data.get("ordinary_profit")),
            net_profit=_opt(data.get("net_profit")),
        )


@dataclass
class Segment:
    name: str
    revenue: float
    operating_profit: float
    is_adjustment: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Segment:
        return cls(
            name=data["name"],
            revenue=float(data["revenue"]),
            operating_profit=float(data["operating_profit"]),
            is_adjustment=bool(data.get("is_adjustment", False)),
        )


@dataclass
class CashFlow:
    net_profit: float
    depreciation: float
    working_capital_change: float
    capex: float
    payout_ratio: float | None = None

    @property
    def operating_cash_flow(self) -> float:
        return self.net_profit + self.depreciation + self.working_capital_change

    @property
    def free_cash_flow(self) -> float:
        return self.operating_cash_flow - self.capex

    @property
    def dividend(self) -> float | None:
        return None if self.payout_ratio is None else -self.net_profit * self.payout_ratio

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> CashFlow:
        return cls(
            net_profit=float(data["net_profit"]),
            depreciation=float(data["depreciation"]),
            working_capital_change=float(data["working_capital_change"]),
            capex=float(data["capex"]),
            payout_ratio=_opt(data.get("payout_ratio")),
        )


@dataclass
class ShareBase:
    name: str
    shares_m: float
    note: str = ""
    cite: list[Citation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"name": self.name, "shares_m": self.shares_m, "note": self.note,
                "cite": [c.to_dict() for c in self.cite]}

    @classmethod
    def from_dict(cls, data: dict) -> ShareBase:
        return cls(
            name=data["name"],
            shares_m=float(data["shares_m"]),
            note=data.get("note", ""),
            cite=[Citation.from_dict(c) for c in data.get("cite", [])],
        )


@dataclass
class Scenario:
    name: str
    assumptions: str
    eps: float
    multiple: float
    revenue: float | None = None
    operating_profit: float | None = None

    @property
    def target_price(self) -> float:
        return self.eps * self.multiple

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Scenario:
        return cls(
            name=data["name"],
            assumptions=data.get("assumptions", ""),
            eps=float(data["eps"]),
            multiple=float(data["multiple"]),
            revenue=_opt(data.get("revenue")),
            operating_profit=_opt(data.get("operating_profit")),
        )


@dataclass
class ValuationSpec:
    price: float
    fair_value_multiple_low: float
    fair_value_multiple_high: float
    fair_value_low: float | None = None
    fair_value_high: float | None = None
    fair_value_basis: str = ""        # which share base the range refers to
    market_cap_shares_m: float | None = None   # shares outstanding, not the EPS base
    sensitivity_multiples: list[float] = field(default_factory=list)
    method_note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ValuationSpec:
        return cls(
            price=float(data["price"]),
            fair_value_multiple_low=float(data["fair_value_multiple_low"]),
            fair_value_multiple_high=float(data["fair_value_multiple_high"]),
            fair_value_low=_opt(data.get("fair_value_low")),
            fair_value_high=_opt(data.get("fair_value_high")),
            fair_value_basis=data.get("fair_value_basis", ""),
            market_cap_shares_m=_opt(data.get("market_cap_shares_m")),
            sensitivity_multiples=[float(m) for m in data.get("sensitivity_multiples", [])],
            method_note=data.get("method_note", ""),
        )


@dataclass
class Consensus:
    """An external forecast to compare against, e.g. a Bloomberg consensus."""

    source: str
    eps_basis: str = ""
    periods: dict[str, dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"source": self.source, "eps_basis": self.eps_basis, "periods": self.periods}

    @classmethod
    def from_dict(cls, data: dict) -> Consensus:
        return cls(
            source=data.get("source", ""),
            eps_basis=data.get("eps_basis", ""),
            periods={
                str(label): {k: float(v) for k, v in row.items() if v is not None}
                for label, row in (data.get("periods") or {}).items()
                if isinstance(row, dict)
            },
        )


@dataclass
class ModelSpec:
    """Everything the kernel needs. Missing inputs stay missing; they are never
    filled with a silent default, because the report must show the gap."""

    ticker: str
    company: str
    currency: str
    unit: str                        # JPY_bn, USD_m, ...
    fiscal_year_end_month: int
    periods: list[Period]
    actuals: dict[str, Financials]
    guidance: dict[str, Financials]
    forecast: dict[str, Financials]
    bridges: list[Bridge]
    share_bases: list[ShareBase]
    valuation: ValuationSpec
    quarterly: dict[str, Financials] = field(default_factory=dict)
    quarterly_plan: dict | None = None
    quarterly_fy: str = ""
    h1_label: str = ""
    segments: dict[str, list[Segment]] = field(default_factory=dict)
    cashflow: dict[str, CashFlow] = field(default_factory=dict)
    scenarios: list[Scenario] = field(default_factory=list)
    eps_basis: str = "full_share"
    model_version: str = "1.0.0"
    consensus: Consensus | None = None
    warnings: list[str] = field(default_factory=list)

    def period(self, label: str) -> Period:
        for period in self.periods:
            if period.label == label:
                return period
        raise ModelError(f"Unknown period {label!r}")

    def share_base(self, name: str) -> ShareBase:
        for base in self.share_bases:
            if base.name == name:
                return base
        raise ModelError(f"Unknown share base {name!r}; known: {[b.name for b in self.share_bases]}")

    def bridge(self, metric: str, target_period: str) -> Bridge | None:
        for bridge in self.bridges:
            if bridge.metric == metric and bridge.target_period == target_period:
                return bridge
        return None

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "company": self.company,
            "currency": self.currency,
            "unit": self.unit,
            "fiscal_year_end_month": self.fiscal_year_end_month,
            "periods": [p.to_dict() for p in self.periods],
            "actuals": {k: v.to_dict() for k, v in self.actuals.items()},
            "guidance": {k: v.to_dict() for k, v in self.guidance.items()},
            "forecast": {k: v.to_dict() for k, v in self.forecast.items()},
            "quarterly": {k: v.to_dict() for k, v in self.quarterly.items()},
            "quarterly_plan": self.quarterly_plan,
            "quarterly_fy": self.quarterly_fy,
            "h1_label": self.h1_label,
            "segments": {k: [s.to_dict() for s in v] for k, v in self.segments.items()},
            "cashflow": {k: v.to_dict() for k, v in self.cashflow.items()},
            "bridges": [b.to_dict() for b in self.bridges],
            "share_bases": [s.to_dict() for s in self.share_bases],
            "scenarios": [s.to_dict() for s in self.scenarios],
            "valuation": self.valuation.to_dict(),
            "eps_basis": self.eps_basis,
            "model_version": self.model_version,
            "consensus": self.consensus.to_dict() if self.consensus else None,
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ModelSpec:
        # A model-authored JSON may set a section to null instead of omitting it.
        # Treat null as empty rather than crashing on ``None.items()``.
        return cls(
            ticker=data["ticker"],
            company=data.get("company", ""),
            currency=data.get("currency", ""),
            unit=data.get("unit", ""),
            fiscal_year_end_month=int(data.get("fiscal_year_end_month", 12)),
            periods=[Period.from_dict(p) for p in _sequence(data.get("periods"))],
            actuals=_financials(data.get("actuals")),
            guidance=_financials(data.get("guidance")),
            forecast=_financials(data.get("forecast")),
            quarterly=_financials(data.get("quarterly")),
            quarterly_plan=data.get("quarterly_plan") or None,
            quarterly_fy=data.get("quarterly_fy", ""),
            h1_label=data.get("h1_label", ""),
            segments={
                k: [Segment.from_dict(s) for s in _sequence(v)]
                for k, v in _mapping(data.get("segments")).items()
            },
            cashflow={
                k: CashFlow.from_dict(v) for k, v in _mapping(data.get("cashflow")).items()
                if isinstance(v, dict)
            },
            bridges=[Bridge.from_dict(b) for b in _sequence(data.get("bridges"))],
            share_bases=[ShareBase.from_dict(s) for s in _sequence(data.get("share_bases"))],
            scenarios=[Scenario.from_dict(s) for s in _sequence(data.get("scenarios"))],
            valuation=ValuationSpec.from_dict(_mapping(data.get("valuation"))),
            eps_basis=data.get("eps_basis", "full_share"),
            model_version=data.get("model_version", "1.0.0"),
            consensus=Consensus.from_dict(data["consensus"]) if data.get("consensus") else None,
            warnings=list(data.get("warnings") or []),
        )


def _opt(value):
    return None if value is None or value == "" else float(value)


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _sequence(value) -> list:
    return value if isinstance(value, list) else []


def _financials(value) -> dict[str, Financials]:
    return {
        label: Financials.from_dict(row)
        for label, row in _mapping(value).items()
        if isinstance(row, dict)
    }
