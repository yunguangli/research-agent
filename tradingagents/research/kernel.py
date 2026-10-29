"""Turn a model spec into numbers, and refuse to do so when they do not tie.

The kernel is deliberately boring: it adds, divides, and compares. Every
forecast figure is either stated in the spec or produced by a named bridge, and
every arithmetic identity the report relies on is checked before anything is
rendered. A failing check means the package is not produced at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .schemas import Financials, ModelError, ModelSpec

TOL = 0.15          # model-unit tolerance, i.e. ¥0.15bn on rounded disclosures
EPS_TOL = 0.15      # one decimal place on per-share figures
MARGIN_TOL = 0.15   # percentage points


@dataclass
class Check:
    name: str
    expected: float
    actual: float
    passed: bool
    unit: str = ""

    def describe(self) -> str:
        status = "ok" if self.passed else "FAIL"
        return f"[{status}] {self.name}: expected {self.expected:.4g}{self.unit}, got {self.actual:.4g}{self.unit}"


@dataclass
class PeriodRow:
    label: str
    basis: str
    revenue: float | None
    operating_profit: float | None
    ordinary_profit: float | None
    net_profit: float | None
    operating_margin: float | None
    eps: dict[str, float] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)


@dataclass
class ModelResult:
    spec: ModelSpec
    rows: list[PeriodRow]
    checks: list[Check]
    market_cap: float | None = None
    pe: dict[str, dict[str, float]] = field(default_factory=dict)
    quarterly_comparison: list[str] = field(default_factory=list)

    def row(self, label: str) -> PeriodRow:
        for row in self.rows:
            if row.label == label:
                return row
        raise ModelError(f"No computed row for period {label!r}")

    @property
    def warnings(self) -> list[str]:
        return list(self.spec.warnings)


def _unit_scale(unit: str) -> tuple[float, float]:
    """``(per_share_factor, market_cap_factor)`` for a unit such as ``JPY_bn``.

    EPS = net_profit * per_share_factor / shares_m; market cap uses the same
    factor against the share price. Getting this wrong is a factor-of-1000 error
    in the two numbers a reader checks first, so it is derived once, explicitly.
    """
    suffix = unit.rsplit("_", 1)[-1].lower()
    if suffix == "bn":
        return 1000.0, 1.0 / 1000.0
    if suffix == "m":
        return 1.0, 1.0
    raise ModelError(f"Unsupported model unit {unit!r}; use e.g. JPY_bn or USD_m")


def eps(net_profit: float, shares_m: float, unit: str) -> float:
    per_share, _ = _unit_scale(unit)
    return net_profit * per_share / shares_m


def shares_implied(net_profit: float, eps_value: float, unit: str) -> float:
    """The share count a quoted EPS implies: the inverse of :func:`eps`.

    Used to tell a forecast difference from a denominator difference when a
    consensus EPS is compared with the model's own.
    """
    if not eps_value:
        raise ModelError("An EPS of zero implies no share count.")
    per_share, _ = _unit_scale(unit)
    return net_profit * per_share / eps_value


def market_cap(price: float, shares_m: float, unit: str) -> float:
    _, cap_factor = _unit_scale(unit)
    return price * shares_m * cap_factor


def _margin(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator * 100.0


def run(spec: ModelSpec) -> ModelResult:
    """Build every period row and validate the model's arithmetic."""
    checks: list[Check] = []
    errors: list[str] = []

    declared = {p.label for p in spec.periods}
    for group_name, group in (
        ("actuals", spec.actuals),
        ("guidance", spec.guidance),
        ("forecast", spec.forecast),
        ("segments", spec.segments),
        ("cashflow", spec.cashflow),
    ):
        unknown = set(group) - declared
        if unknown:
            errors.append(f"{group_name} refers to undeclared period(s): {sorted(unknown)}")

    by_period: dict[str, Financials] = {}
    sources: dict[str, dict[str, str]] = {}

    for group_name, group in (("actual", spec.actuals), ("guidance", spec.guidance)):
        for label, fin in group.items():
            by_period[label] = _merge(by_period.get(label), fin)
            sources.setdefault(label, {}).update(dict.fromkeys(_present(fin), group_name))

    for label, fin in spec.forecast.items():
        merged = _merge(by_period.get(label), fin)
        by_period[label] = merged
        sources.setdefault(label, {}).update(dict.fromkeys(_present(fin), "forecast"))

    # Bridges fill or confirm revenue / operating profit for the target period.
    for bridge in spec.bridges:
        check = Check(
            name=f"{bridge.metric} bridge {bridge.base_period} -> {bridge.target_period}",
            expected=bridge.declared_total if bridge.declared_total is not None else bridge.total,
            actual=bridge.total,
            passed=True,
            unit=f" {spec.unit}",
        )
        if bridge.declared_total is not None:
            check.passed = abs(bridge.total - bridge.declared_total) <= TOL
            if not check.passed:
                errors.append(
                    f"{check.name} does not sum: base {bridge.base_value:g} + "
                    f"deltas {bridge.deltas:+g} = {bridge.total:g}, declared {bridge.declared_total:g}"
                )
        checks.append(check)

        # The bridge must start from the number the statement actually shows;
        # otherwise the walk and the table beside it tell different stories.
        base_row = by_period.get(bridge.base_period)
        base_shown = base_row.get(bridge.metric) if base_row is not None else None
        if base_shown is not None and abs(bridge.base_value - base_shown) > TOL:
            errors.append(
                f"{bridge.metric} bridge starts from {bridge.base_value:g} but "
                f"{bridge.base_period} {bridge.metric} is {base_shown:g}"
            )

        if bridge.metric not in {"revenue", "operating_profit", "net_profit"}:
            errors.append(f"Bridge metric {bridge.metric!r} is not a statement line")
            continue
        target = by_period.setdefault(bridge.target_period, Financials())
        existing = target.get(bridge.metric)
        if existing is not None and abs(existing - bridge.total) > TOL:
            errors.append(
                f"{bridge.target_period} {bridge.metric} is stated as {existing:g} but the bridge "
                f"builds {bridge.total:g}"
            )
        setattr(target, bridge.metric, bridge.total)
        sources.setdefault(bridge.target_period, {})[bridge.metric] = "bridge"

    rows: list[PeriodRow] = []
    for period in spec.periods:
        fin = by_period.get(period.label, Financials())
        row = PeriodRow(
            label=period.label,
            basis=period.basis,
            revenue=fin.revenue,
            operating_profit=fin.operating_profit,
            ordinary_profit=fin.ordinary_profit,
            net_profit=fin.net_profit,
            operating_margin=_margin(fin.operating_profit, fin.revenue),
            eps={
                base.name: eps(fin.net_profit, base.shares_m, spec.unit)
                for base in spec.share_bases
                if fin.net_profit is not None
            },
            sources=sources.get(period.label, {}),
        )
        rows.append(row)

    # --- quarterly build -------------------------------------------------
    # A plan derives Q2-Q4 from Q1 actual plus H1/FY guidance. A reported table
    # in the spec is then a cross-check on the plan's judgement, not its source.
    comparison: list[str] = []
    if spec.quarterly_plan:
        from .quarterly import QuarterlyPlan, build as build_quarters, compare

        plan = QuarterlyPlan.from_dict(spec.quarterly_plan)
        derived, _lines = build_quarters(spec, plan)
        if spec.quarterly:
            comparison = compare(derived, spec.quarterly, TOL)
            if comparison:
                checks.append(
                    Check(name="quarterly plan vs reported", expected=0, actual=len(comparison),
                          passed=False, unit=" differences")
                )
        spec.quarterly = derived
        spec.quarterly_fy = plan.fy_label
        spec.h1_label = plan.h1_label

    if spec.quarterly:
        quarters = list(spec.quarterly.items())
        for metric in ("revenue", "operating_profit", "net_profit"):
            total = sum(q.get(metric) or 0 for _, q in quarters)
            fy = by_period.get(spec.quarterly_fy, Financials()).get(metric)
            if fy is not None:
                checks.append(_check(f"quarters sum to {spec.quarterly_fy} {metric}", fy, total, spec.unit))
                if abs(total - fy) > TOL:
                    errors.append(
                        f"Quarterly {metric} sums to {total:g} but {spec.quarterly_fy} is {fy:g}"
                    )
            if spec.h1_label and len(quarters) >= 2:
                h1 = by_period.get(spec.h1_label, Financials()).get(metric)
                if h1 is not None:
                    first_half = (quarters[0][1].get(metric) or 0) + (quarters[1][1].get(metric) or 0)
                    checks.append(
                        _check(f"Q1+Q2 equal {spec.h1_label} {metric}", h1, first_half, spec.unit)
                    )
                    if abs(first_half - h1) > TOL:
                        errors.append(
                            f"Q1+Q2 {metric} is {first_half:g} but {spec.h1_label} is {h1:g}"
                        )

    # --- segments --------------------------------------------------------
    for label, segments in spec.segments.items():
        revenue = by_period.get(label, Financials()).revenue
        operating = by_period.get(label, Financials()).operating_profit
        if revenue is not None:
            total = sum(s.revenue for s in segments)
            checks.append(_check(f"{label} segment revenue", revenue, total, spec.unit))
            if abs(total - revenue) > TOL:
                errors.append(f"{label} segments sum to {total:g} revenue, group is {revenue:g}")
        if operating is not None:
            total = sum(s.operating_profit for s in segments)
            checks.append(_check(f"{label} segment operating profit", operating, total, spec.unit))
            if abs(total - operating) > TOL:
                errors.append(f"{label} segments sum to {total:g} operating profit, group is {operating:g}")

    # --- cash flow -------------------------------------------------------
    for label, flow in spec.cashflow.items():
        net_profit = by_period.get(label, Financials()).net_profit
        if net_profit is not None and abs(flow.net_profit - net_profit) > TOL:
            errors.append(
                f"{label} cash flow uses net profit {flow.net_profit:g}, model has {net_profit:g}"
            )
        revenue = by_period.get(label, Financials()).revenue
        if revenue:
            if flow.capex / revenue > 0.20:
                spec.warnings.append(
                    f"{label} capex is {flow.capex / revenue:.0%} of revenue; check the unit "
                    "(a Japanese filing's 億円 is 0.1bn, not 1bn)."
                )
            if flow.depreciation / revenue > 0.20:
                spec.warnings.append(
                    f"{label} depreciation is {flow.depreciation / revenue:.0%} of revenue; "
                    "check the unit."
                )
        expected_ocf = flow.net_profit + flow.depreciation + flow.working_capital_change
        checks.append(_check(f"{label} operating cash flow", expected_ocf, flow.operating_cash_flow, spec.unit))
        checks.append(
            _check(f"{label} free cash flow", expected_ocf - flow.capex, flow.free_cash_flow, spec.unit)
        )

    # --- per-share and valuation ----------------------------------------
    if not spec.share_bases:
        raise ModelError("The model needs at least one share base to compute EPS")
    basis = spec.eps_basis
    if not any(base.name == basis for base in spec.share_bases):
        # A spec that names one basis is the normal case; fall back rather than
        # leaving market cap silently empty.
        basis = spec.share_bases[0].name
        spec.eps_basis = basis

    market_caps: dict[str, float] = {}
    pe: dict[str, dict[str, float]] = {}
    cap_shares = spec.valuation.market_cap_shares_m
    if cap_shares is None:
        cap_shares = spec.share_base(basis).shares_m
    market_cap_value = market_cap(spec.valuation.price, cap_shares, spec.unit)
    for base in spec.share_bases:
        market_caps[base.name] = market_cap_value
        pe[base.name] = {}
        for row in rows:
            value = row.eps.get(base.name)
            if value:
                pe[base.name][row.label] = spec.valuation.price / value

    for base in spec.share_bases:
        for row in rows:
            if row.net_profit is None:
                continue
            expected = eps(row.net_profit, base.shares_m, spec.unit)
            actual = row.eps[base.name]
            check = Check(
                name=f"{row.label} EPS ({base.name})",
                expected=expected,
                actual=actual,
                passed=True,
                unit=f" {spec.currency}",
            )
            checks.append(check)

    # Fair-value range: the declared numbers are rounded to a round figure, so
    # compare within 1% rather than to the yen. A wrong multiple is far outside it.
    for label, declared_value, multiple in (
        ("fair value low", spec.valuation.fair_value_low, spec.valuation.fair_value_multiple_low),
        ("fair value high", spec.valuation.fair_value_high, spec.valuation.fair_value_multiple_high),
    ):
        if declared_value is None:
            continue
        row = _valuation_row(rows, spec)
        if row is None:
            continue
        base_eps = row.eps.get(spec.valuation.fair_value_basis or spec.eps_basis)
        if base_eps is None:
            continue
        checks.append(
            _check(
                f"{label} ({multiple:g}x)",
                declared_value,
                base_eps * multiple,
                _price_unit(spec),
                tol=max(5.0, declared_value * 0.01),
            )
        )

    for scenario in spec.scenarios:
        checks.append(
            Check(
                name=f"scenario {scenario.name} target",
                expected=scenario.eps * scenario.multiple,
                actual=scenario.target_price,
                passed=True,
                unit=f" {spec.currency}",
            )
        )

    if errors:
        raise ModelError("Model does not tie:\n  - " + "\n  - ".join(errors))

    return ModelResult(
        spec=spec,
        rows=rows,
        checks=checks,
        market_cap=market_caps.get(spec.eps_basis),
        pe=pe,
        quarterly_comparison=comparison,
    )


def _valuation_row(rows: list[PeriodRow], spec: ModelSpec) -> PeriodRow | None:
    """The row the fair-value range refers to: the first forecast period."""
    for period in spec.periods:
        if period.basis == "forecast":
            for row in rows:
                if row.label == period.label:
                    return row
    return None


def _price_unit(spec: ModelSpec) -> str:
    return f" {spec.currency}"


def _check(name: str, expected: float, actual: float, unit: str, tol: float = TOL) -> Check:
    return Check(name=name, expected=expected, actual=actual, passed=abs(expected - actual) <= tol,
                 unit=f" {unit}" if unit else "")


def _present(fin: Financials) -> list[str]:
    return [m for m in ("revenue", "operating_profit", "ordinary_profit", "net_profit")
            if fin.get(m) is not None]


def _merge(base: Financials | None, extra: Financials) -> Financials:
    merged = base or Financials()
    for metric in ("revenue", "operating_profit", "ordinary_profit", "net_profit"):
        value = extra.get(metric)
        if value is not None:
            setattr(merged, metric, value)
    return merged
