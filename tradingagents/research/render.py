"""Render computed model tables. Tables come from the kernel, never from prose.

The reference report's tables are the contract: column order, units, and the
inclusion of a YoY column are all reproduced here so a reader can compare a
generated report with the one a human wrote line by line.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .kernel import ModelResult, PeriodRow
from .schemas import Bridge, ModelError, ModelSpec
from .valuation import Valuation

Table = tuple[list[str], list[list[str]]]


def fmt(value: float | None, decimals: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.{decimals}f}"


def fmt_pct(value: float | None, decimals: int = 1, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.{decimals}f}%" if signed else f"{value:.{decimals}f}%"


def yoy(current: float | None, previous: float | None) -> str:
    if current is None or previous in (None, 0):
        return "n/a"
    return f"{(current / previous - 1) * 100:+.1f}%"


def _periods_by_basis(spec: ModelSpec, basis: str) -> list[str]:
    return [p.label for p in spec.periods if p.basis == basis]


def annual_table(model: ModelResult) -> Table:
    spec = model.spec
    labels = [p.label for p in spec.periods
              if p.basis in {"actual", "guidance", "forecast"} and p.kind == "annual"]
    headers = ["", labels[0]] if labels else [""]
    for label in labels[1:]:
        headers.extend([label, f"YoY {label}"])
    rows: list[list[str]] = []
    for metric, display in (
        ("revenue", "Revenue"),
        ("operating_profit", "Operating profit"),
        ("ordinary_profit", "Ordinary profit"),
        ("net_profit", "Net profit"),
    ):
        cells = [display]
        for index, label in enumerate(labels):
            value = getattr(model.row(label), metric)
            cells.append(fmt(value))
            if index > 0:
                cells.append(yoy(value, getattr(model.row(labels[index - 1]), metric)))
        rows.append(cells)

    cells = ["Operating margin"]
    for index, label in enumerate(labels):
        row = model.row(label)
        cells.append(fmt_pct(row.operating_margin))
        if index > 0:
            previous = model.row(labels[index - 1]).operating_margin
            delta = None if previous is None or row.operating_margin is None else row.operating_margin - previous
            cells.append(f"{delta:+.1f}pp" if delta is not None else "n/a")
    rows.append(cells)

    for base in spec.share_bases:
        cells = [f"EPS ({base.name.replace('_', ' ')})"]
        for index, label in enumerate(labels):
            value = model.row(label).eps.get(base.name)
            cells.append(fmt(value))
            if index > 0:
                cells.append(yoy(value, model.row(labels[index - 1]).eps.get(base.name)))
        rows.append(cells)
    return headers, rows


def quarterly_table(model: ModelResult) -> Table:
    spec = model.spec
    if not spec.quarterly:
        return [], []
    labels = list(spec.quarterly)
    fy_row = model.row(spec.quarterly_fy) if spec.quarterly_fy else None
    headers = ["", *labels, spec.quarterly_fy or "FY"]
    rows = []
    for metric, display in (
        ("revenue", "Revenue"),
        ("operating_profit", "Operating profit"),
        ("net_profit", "Net profit"),
    ):
        values = [spec.quarterly[label].get(metric) for label in labels]
        total = sum(value for value in values if value is not None)
        fy_value = getattr(fy_row, metric) if fy_row is not None else None
        rows.append([display, *(fmt(value) for value in values),
                     fmt(fy_value if fy_value is not None else total)])
    margins = []
    for label in labels:
        fin = spec.quarterly[label]
        margins.append(fmt_pct(fin.operating_profit / fin.revenue * 100)
                       if fin.revenue and fin.operating_profit is not None else "n/a")
    rows.append(["OP margin", *margins, fmt_pct(fy_row.operating_margin) if fy_row else "n/a"])
    return headers, rows


def segment_table(model: ModelResult) -> Table:
    periods = list(model.spec.segments)
    if not periods:
        return [], []
    headers = [""]
    for label in periods:
        headers.extend([f"{label} revenue", f"{label} operating profit"])
    names = [segment.name for segment in model.spec.segments[periods[0]]]
    rows = []
    for index, name in enumerate(names):
        cells = [name]
        for label in periods:
            segment = model.spec.segments[label][index]
            cells.extend([fmt(segment.revenue), fmt(segment.operating_profit)])
        rows.append(cells)
    totals = ["Total"]
    for label in periods:
        totals.extend([
            fmt(sum(s.revenue for s in model.spec.segments[label])),
            fmt(sum(s.operating_profit for s in model.spec.segments[label])),
        ])
    rows.append(totals)
    return headers, rows


def cashflow_table(model: ModelResult) -> Table:
    periods = list(model.spec.cashflow)
    if not periods:
        return [], []
    headers = ["", *periods]
    rows = []
    for key, display in (
        ("net_profit", "Net profit"),
        ("depreciation", "Depreciation and amortisation"),
        ("working_capital_change", "Working-capital change"),
    ):
        rows.append([display, *(fmt(getattr(model.spec.cashflow[p], key)) for p in periods)])
    rows.append(["Operating cash flow", *(fmt(model.spec.cashflow[p].operating_cash_flow) for p in periods)])
    rows.append(["Capex", *(fmt(-model.spec.cashflow[p].capex) for p in periods)])
    rows.append(["Free cash flow", *(fmt(model.spec.cashflow[p].free_cash_flow) for p in periods)])
    if any(model.spec.cashflow[p].payout_ratio is not None for p in periods):
        rows.append(["Dividend", *(fmt(model.spec.cashflow[p].dividend) for p in periods)])
        rows.append([
            "Post-dividend free cash flow",
            *(fmt(model.spec.cashflow[p].free_cash_flow + (model.spec.cashflow[p].dividend or 0))
              for p in periods),
        ])
    return headers, rows


def bridge_table(bridge: Bridge, unit: str) -> Table:
    headers = [f"{bridge.metric.replace('_', ' ').title()} bridge", unit]
    rows = [[f"{bridge.base_period} (base)", fmt(bridge.base_value)]]
    for line in bridge.lines:
        label = line.label
        if line.provenance == "derived" and line.formula:
            label = f"{label}  [{line.formula}]"
        elif line.provenance != "derived":
            label = f"{label}  [{line.provenance}]"
        rows.append([label, fmt(line.value)])
    rows.append([f"{bridge.target_period} (forecast)", fmt(bridge.total)])
    return headers, rows


def valuation_table(model: ModelResult, valuation: Valuation) -> Table:
    spec = model.spec
    headers = ["Metric", f"{valuation.period} EPS / P/E", "Value"]
    rows = []
    for base in spec.share_bases:
        row = model.row(valuation.period)
        eps_value = row.eps.get(base.name)
        rows.append([
            f"{base.name.replace('_', ' ')} EPS",
            f"{fmt(eps_value)} {spec.currency}",
            f"P/E {fmt(spec.valuation.price / eps_value, 1) if eps_value else 'n/a'}x",
        ])
    rows.append(["Market capitalisation", f"{fmt(model.market_cap)} {spec.unit}", ""])
    rows.append(["Share price", f"{fmt(spec.valuation.price, 0)} {spec.currency}", ""])
    rows.append(["Fair value range", f"{fmt(valuation.fair_value_low, 0)}-{fmt(valuation.fair_value_high, 0)} {spec.currency}",
                 f"mid {fmt(valuation.fair_value_mid, 0)}"])
    return headers, rows


def sensitivity_table(valuation: Valuation, currency: str) -> Table:
    headers = ["P/E", "Implied price", "Upside / downside"]
    rows = [
        [f"{row.multiple:g}x", f"{fmt(row.implied_price, 0)} {currency}", f"{row.change * 100:+.1f}%"]
        for row in valuation.sensitivity
    ]
    return headers, rows


def scenario_table(valuation: Valuation, currency: str) -> Table:
    headers = ["Scenario", "Assumptions", "Revenue", "Operating profit", "EPS", "P/E", "Target", "Upside / downside"]
    rows = [
        [
            scenario["name"],
            scenario["assumptions"],
            fmt(scenario["revenue"], 0),
            fmt(scenario["operating_profit"], 0),
            fmt(scenario["eps"]),
            f"{scenario['multiple']:g}x",
            f"{fmt(scenario['target'], 0)} {currency}",
            f"{scenario['change'] * 100:+.1f}%",
        ]
        for scenario in valuation.scenarios
    ]
    return headers, rows


def consensus_table(model: ModelResult) -> Table:
    """Model versus external consensus, period by period, with the variance."""
    consensus = model.spec.consensus
    if consensus is None or not consensus.periods:
        return [], []
    headers = ["Period", "Metric", "Consensus", "Model", "Variance"]
    rows: list[list[str]] = []
    for period, values in consensus.periods.items():
        try:
            row = model.row(period)
        except ModelError:
            continue
        for metric, display in (
            ("revenue", "Revenue"),
            ("operating_profit", "Operating profit"),
            ("net_profit", "Net profit"),
            ("eps", "EPS"),
        ):
            expected = values.get(metric)
            if expected is None:
                continue
            actual = (
                row.eps.get(consensus.eps_basis or model.spec.eps_basis)
                if metric == "eps"
                else getattr(row, metric)
            )
            if actual is None:
                continue
            variance = f"{(actual / expected - 1) * 100:+.1f}%" if expected else "n/a"
            rows.append([period, display, fmt(expected), fmt(actual), variance])
    return headers, rows


def to_markdown(table: Table) -> str:
    headers, rows = table
    if not headers:
        return ""
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        padded = row + [""] * (len(headers) - len(row))
        lines.append("| " + " | ".join(padded) + " |")
    return "\n".join(lines)


def write_csv(path: Path, table: Table) -> Path:
    headers, rows = table
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)
    return path


def write_tables(model: ModelResult, valuation: Valuation, out_dir: Path) -> dict[str, Path]:
    out_dir = Path(out_dir)
    written: dict[str, Path] = {}
    for name, table in (
        ("annual", annual_table(model)),
        ("quarterly", quarterly_table(model)),
        ("segments", segment_table(model)),
        ("cashflow", cashflow_table(model)),
        ("valuation", valuation_table(model, valuation)),
        ("sensitivity", sensitivity_table(valuation, model.spec.currency)),
        ("scenarios", scenario_table(valuation, model.spec.currency)),
        ("consensus", consensus_table(model)),
    ):
        if table[0]:
            written[name] = write_csv(out_dir / f"{name}.csv", table)
    for bridge in model.spec.bridges:
        name = f"bridge_{bridge.metric}_{bridge.target_period}".replace("/", "-").replace(" ", "_")
        written[name] = write_csv(out_dir / f"{name}.csv", bridge_table(bridge, model.spec.unit))
    return written


def row_summary(row: PeriodRow) -> dict:
    return {
        "label": row.label,
        "basis": row.basis,
        "revenue": row.revenue,
        "operating_profit": row.operating_profit,
        "net_profit": row.net_profit,
        "operating_margin": row.operating_margin,
        "eps": row.eps,
    }
