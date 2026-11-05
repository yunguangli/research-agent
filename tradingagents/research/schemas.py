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
    # A bridge line is an assumption, so the debate can quantify it: the driver
    # it represents and the defensible alternative the sensitivity engine shocks
    # it to. Empty driver = no explicit assumption (a research gap).
    driver: str = ""
    alternative: float | None = None

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
            driver=str(data.get("driver") or ""),
            alternative=_opt(data.get("alternative")),
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
    # A disclosed EPS on ``ModelSpec.eps_basis``. Optional: it exists so the
    # kernel can check the computed EPS against a stated one instead of
    # re-deriving the same formula. No disclosure means no check, not a pass.
    eps: float | None = None

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
            eps=_opt(data.get("eps")),
        )


@dataclass
class Segment:
    name: str
    revenue: float
    operating_profit: float
    is_adjustment: bool = False
    # The period label the segment starts operating (company disclosure). A
    # forecast that books revenue earlier is unsupported timing.
    commissioning: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Segment:
        return cls(
            name=data["name"],
            revenue=float(data["revenue"]),
            operating_profit=float(data["operating_profit"]),
            is_adjustment=bool(data.get("is_adjustment", False)),
            commissioning=str(data.get("commissioning") or ""),
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
    """One way of counting shares, with the denominator it is a count of.

    A forecast EPS needs a weighted-average diluted count; a market cap needs
    shares outstanding at the period end. Keeping them as separate named bases
    (rather than one "shares" field) is what stops a stale period-end count from
    controlling EPS after a conversion.
    """

    name: str
    shares_m: float
    note: str = ""
    cite: list[Citation] = field(default_factory=list)
    basic_weighted_average: float | None = None
    diluted_weighted_average: float | None = None
    post_period_conversion: float | None = None
    potential_cb_shares: float | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name, "shares_m": self.shares_m, "note": self.note,
            "cite": [c.to_dict() for c in self.cite],
            "basic_weighted_average": self.basic_weighted_average,
            "diluted_weighted_average": self.diluted_weighted_average,
            "post_period_conversion": self.post_period_conversion,
            "potential_cb_shares": self.potential_cb_shares,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ShareBase:
        return cls(
            name=data["name"],
            shares_m=float(data["shares_m"]),
            note=data.get("note", ""),
            cite=[Citation.from_dict(c) for c in data.get("cite", [])],
            basic_weighted_average=_opt(data.get("basic_weighted_average")),
            diluted_weighted_average=_opt(data.get("diluted_weighted_average")),
            post_period_conversion=_opt(data.get("post_period_conversion")),
            potential_cb_shares=_opt(data.get("potential_cb_shares")),
        )


@dataclass
class Scenario:
    name: str
    assumptions: str
    eps: float
    multiple: float
    revenue: float | None = None
    operating_profit: float | None = None
    # A declared target, when the documents (or the builder) state one. None
    # means the target is derived from eps x multiple; a declared value is
    # checked against that product rather than replacing it.
    target_price: float | None = None
    # The period whose EPS this scenario uses. Empty means "same as the
    # valuation"; the kernel then infers it from the EPS and rejects a
    # scenario built on another year than the band.
    period: str = ""
    # Where the case comes from: reported | guidance | consensus | derived |
    # assumption. Empty is allowed for specs written before the vocabulary.
    provenance: str = ""

    @property
    def resolved_target(self) -> float:
        """The target this scenario publishes: declared when given, else derived."""
        return self.target_price if self.target_price is not None else self.eps * self.multiple

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
            target_price=_opt(data.get("target_price")),
            period=str(data.get("period") or ""),
            provenance=str(data.get("provenance") or ""),
        )


@dataclass
class ValuationSpec:
    price: float
    fair_value_multiple_low: float
    fair_value_multiple_high: float
    fair_value_low: float | None = None
    fair_value_high: float | None = None
    fair_value_basis: str = ""        # which share base the range refers to
    fair_value_period: str = ""       # which year's EPS the range multiplies
    market_cap_shares_m: float | None = None   # shares outstanding, not the EPS base
    market_cap_basis: str = ""        # named ShareBase used for the market cap
    forecast_eps_basis: str = ""      # named ShareBase used for a forecast EPS
    dilution_sensitivity_basis: str = ""   # named ShareBase for the full-conversion case
    ev_ebitda_multiple: float | None = None # selected multiple for the second method
    equity_value_share_basis: str = ""    # named ShareBase for an EV-based per-share value
    # The multiple is an analyst assumption, never a company-reported figure.
    ev_ebitda_multiple_provenance: str = "analyst_assumption"
    ev_ebitda_multiple_rationale: str = ""
    ev_ebitda_multiple_refs: list[str] = field(default_factory=list)
    provenance: str = ""              # reported | guidance | consensus | derived | assumption
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
            fair_value_period=str(data.get("fair_value_period") or ""),
            market_cap_shares_m=_opt(data.get("market_cap_shares_m")),
            market_cap_basis=str(data.get("market_cap_basis") or ""),
            forecast_eps_basis=str(data.get("forecast_eps_basis") or ""),
            dilution_sensitivity_basis=str(data.get("dilution_sensitivity_basis") or ""),
            ev_ebitda_multiple=_opt(data.get("ev_ebitda_multiple")),
            equity_value_share_basis=str(data.get("equity_value_share_basis") or ""),
            ev_ebitda_multiple_provenance=str(
                data.get("ev_ebitda_multiple_provenance") or "analyst_assumption"
            ),
            ev_ebitda_multiple_rationale=str(
                data.get("ev_ebitda_multiple_rationale") or ""
            ),
            ev_ebitda_multiple_refs=list(data.get("ev_ebitda_multiple_refs") or []),
            provenance=str(data.get("provenance") or ""),
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
class Convertible:
    """One convertible instrument: the shares it would add, and when."""

    name: str = ""
    shares_m: float | None = None
    conversion_price: float | None = None
    conversion_date: str = ""       # empty means outstanding throughout
    note: str = ""
    # Debt/equity classification for valuation. ``unknown`` is the default so a
    # spec written before these fields loads unchanged; treatment is never
    # inferred when the required fields are absent.
    face_value: float | None = None
    accounting_classification: str = ""   # debt | equity | unknown
    valuation_treatment: str = "unknown"  # debt | assumed_converted | unknown

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Convertible:
        return cls(
            name=str(data.get("name") or ""),
            shares_m=_opt(data.get("shares_m")),
            conversion_price=_opt(data.get("conversion_price")),
            conversion_date=str(data.get("conversion_date") or ""),
            note=str(data.get("note") or ""),
            face_value=_opt(data.get("face_value")),
            accounting_classification=str(data.get("accounting_classification") or ""),
            valuation_treatment=str(data.get("valuation_treatment") or "unknown"),
        )


@dataclass
class CapitalStructure:
    """Share counts and the corporate actions that move them.

    Current net shares and the per-period weighted averages are stated by the
    company; the events are applied on top so a post-period issuance or a buyback
    is not silently ignored. Missing values stay ``None``; zero is a fact.
    """

    issued_shares: float | None = None
    treasury_shares: float | None = None
    current_net_shares: float | None = None
    basic_weighted_average: dict[str, float] = field(default_factory=dict)
    diluted_weighted_average: dict[str, float] = field(default_factory=dict)
    post_period_issuances: list[dict] = field(default_factory=list)
    buybacks: list[dict] = field(default_factory=list)
    treasury_cancellations: list[dict] = field(default_factory=list)
    convertibles: list[Convertible] = field(default_factory=list)
    split_history: list[dict] = field(default_factory=list)
    rights_issues: list[dict] = field(default_factory=list)
    share_classes: list[dict] = field(default_factory=list)
    adr_ratio: float | None = None
    source_refs: list[str] = field(default_factory=list)
    verification_status: str = ""

    def to_dict(self) -> dict:
        return {
            "issued_shares": self.issued_shares,
            "treasury_shares": self.treasury_shares,
            "current_net_shares": self.current_net_shares,
            "basic_weighted_average": self.basic_weighted_average,
            "diluted_weighted_average": self.diluted_weighted_average,
            "post_period_issuances": self.post_period_issuances,
            "buybacks": self.buybacks,
            "treasury_cancellations": self.treasury_cancellations,
            "convertibles": [item.to_dict() for item in self.convertibles],
            "split_history": self.split_history,
            "rights_issues": self.rights_issues,
            "share_classes": self.share_classes,
            "adr_ratio": self.adr_ratio,
            "source_refs": self.source_refs,
            "verification_status": self.verification_status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CapitalStructure:
        return cls(
            issued_shares=_opt(data.get("issued_shares")),
            treasury_shares=_opt(data.get("treasury_shares")),
            current_net_shares=_opt(data.get("current_net_shares")),
            basic_weighted_average={
                str(k): float(v) for k, v in _mapping(data.get("basic_weighted_average")).items()
            },
            diluted_weighted_average={
                str(k): float(v) for k, v in _mapping(data.get("diluted_weighted_average")).items()
            },
            post_period_issuances=list(_sequence(data.get("post_period_issuances"))),
            buybacks=list(_sequence(data.get("buybacks"))),
            treasury_cancellations=list(_sequence(data.get("treasury_cancellations"))),
            convertibles=[
                Convertible.from_dict(item) for item in _sequence(data.get("convertibles"))
                if isinstance(item, dict)
            ],
            split_history=list(_sequence(data.get("split_history"))),
            rights_issues=list(_sequence(data.get("rights_issues"))),
            share_classes=list(_sequence(data.get("share_classes"))),
            adr_ratio=_opt(data.get("adr_ratio")),
            source_refs=list(data.get("source_refs") or []),
            verification_status=str(data.get("verification_status") or ""),
        )


@dataclass
class NetDebt:
    """Debt and cash, stated as components so net debt is derived, not asserted.

    A bare net figure cannot be checked and cannot be adjusted for leases or
    minority interests. ``components_verified`` is true only when gross debt and
    cash are both stated; a legacy float loads as ``reported_net_debt`` and is
    not usable for a verified EV/EBITDA.
    """

    gross_debt: float | None = None
    cash_and_equivalents: float | None = None
    lease_liabilities: float | None = None
    lease_treatment: str = ""          # included | excluded
    minority_interest: float | None = None
    minority_treatment: str = ""       # included | excluded
    reported_net_debt: float | None = None
    # Provenance the reconciliation checks need: which balance-sheet date, which
    # scope (consolidated or parent), and the unit the source printed.
    as_of: str = ""
    scope: str = ""                    # consolidated | parent | unknown
    source_unit: str = ""
    source_refs: list[str] = field(default_factory=list)
    component_sources: dict[str, str] = field(default_factory=dict)
    verification_status: str = ""

    def components_verified(self) -> bool:
        return self.gross_debt is not None and self.cash_and_equivalents is not None

    @property
    def value(self) -> float | None:
        """Net debt, or None when it cannot be derived from components."""
        if not self.components_verified():
            return self.reported_net_debt
        total = self.gross_debt - self.cash_and_equivalents
        if self.lease_treatment.lower() == "included":
            total += self.lease_liabilities or 0.0
        if self.minority_treatment.lower() == "included":
            total += self.minority_interest or 0.0
        return total

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data) -> NetDebt | None:
        if data is None:
            return None
        if isinstance(data, (int, float)) and not isinstance(data, bool):
            # A legacy spec stated only the net figure. Keep it for display; it
            # is not verified and cannot feed a verified EV/EBITDA.
            return cls(reported_net_debt=float(data))
        if not isinstance(data, dict):
            return None
        return cls(
            gross_debt=_opt(data.get("gross_debt")),
            cash_and_equivalents=_opt(data.get("cash_and_equivalents")),
            lease_liabilities=_opt(data.get("lease_liabilities")),
            lease_treatment=str(data.get("lease_treatment") or ""),
            minority_interest=_opt(data.get("minority_interest")),
            minority_treatment=str(data.get("minority_treatment") or ""),
            reported_net_debt=_opt(data.get("reported_net_debt")),
            as_of=str(data.get("as_of") or ""),
            scope=str(data.get("scope") or ""),
            source_unit=str(data.get("source_unit") or ""),
            source_refs=list(data.get("source_refs") or []),
            component_sources=dict(data.get("component_sources") or {}),
            verification_status=str(data.get("verification_status") or ""),
        )


@dataclass
class InvestmentProject:
    """A capital project and the dates that gate its contribution."""

    project_id: str = ""
    project_name: str = ""
    project_type: str = ""
    capex: float | None = None
    currency: str = ""
    construction_start: str = ""
    commissioning_start: str = ""
    production_start: str = ""
    revenue_contribution_start: str = ""
    depreciation_start: str = ""
    ramp_profile: dict = field(default_factory=dict)
    expected_capacity: float | None = None
    expected_revenue_contribution: float | None = None
    source_refs: list[str] = field(default_factory=list)
    verification_status: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> InvestmentProject:
        return cls(
            project_id=str(data.get("project_id") or ""),
            project_name=str(data.get("project_name") or ""),
            project_type=str(data.get("project_type") or ""),
            capex=_opt(data.get("capex")),
            currency=str(data.get("currency") or ""),
            construction_start=str(data.get("construction_start") or ""),
            commissioning_start=str(data.get("commissioning_start") or ""),
            production_start=str(data.get("production_start") or ""),
            revenue_contribution_start=str(data.get("revenue_contribution_start") or ""),
            depreciation_start=str(data.get("depreciation_start") or ""),
            ramp_profile=dict(_mapping(data.get("ramp_profile"))),
            expected_capacity=_opt(data.get("expected_capacity")),
            expected_revenue_contribution=_opt(data.get("expected_revenue_contribution")),
            source_refs=list(data.get("source_refs") or []),
            verification_status=str(data.get("verification_status") or ""),
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
    # Optional balance-sheet and tax inputs. None means "not established"; never
    # zero, because a zero debt or a zero tax rate is a fact, not a default.
    net_debt: NetDebt | None = None
    effective_tax_rate: float | None = None
    # Capital projects with their timing (commissioning, production, revenue).
    # Company-specific names live in the data, never in a schema field.
    projects: list[InvestmentProject] = field(default_factory=list)
    # Sector drives the severity thresholds, not the analysis.
    sector: str = ""
    # Optional share-count ledger. When absent, the named ShareBase entries are
    # the source of truth.
    capital_structure: CapitalStructure | None = None

    def __post_init__(self) -> None:
        # A caller may still pass a bare net-debt figure. Keep it, but as an
        # unverified component-less record: it must not feed a verified EV/EBITDA.
        if isinstance(self.net_debt, (int, float)) and not isinstance(self.net_debt, bool):
            self.net_debt = NetDebt(reported_net_debt=float(self.net_debt))
        elif isinstance(self.net_debt, dict):
            self.net_debt = NetDebt.from_dict(self.net_debt)

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
            "net_debt": self.net_debt.to_dict() if self.net_debt else None,
            "effective_tax_rate": self.effective_tax_rate,
            "projects": [p.to_dict() if hasattr(p, "to_dict") else p for p in self.projects],
            "sector": self.sector,
            "capital_structure": self.capital_structure.to_dict() if self.capital_structure else None,
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
            net_debt=NetDebt.from_dict(data.get("net_debt")),
            effective_tax_rate=_opt(data.get("effective_tax_rate")),
            projects=[
                InvestmentProject.from_dict(p) if isinstance(p, dict) else p
                for p in _sequence(data.get("projects"))
            ],
            sector=str(data.get("sector") or ""),
            capital_structure=(
                CapitalStructure.from_dict(data["capital_structure"])
                if isinstance(data.get("capital_structure"), dict) else None
            ),
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
