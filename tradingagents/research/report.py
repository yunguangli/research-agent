"""Assemble the report: computed tables, deterministic summary, explicit gaps.

Narrative sections are marked as pending rather than filled with model-written
prose here. A later stage writes that prose over these tables, and the renderer
refuses any paragraph that introduces a number the tables do not contain.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from . import kernel, render, valuation as valuation_module
from .schemas import ModelSpec

NARRATIVE_MARKER = "<!-- NARRATIVE PENDING: {section} -->"


def _header(model: kernel.ModelResult, valuation: valuation_module.Valuation) -> str:
    spec = model.spec
    stance = _stance(valuation)
    return "\n".join([
        f"# {spec.company or spec.ticker} — Fundamental Research",
        "",
        f"- Ticker: {spec.ticker}",
        f"- Share price: {render.fmt(spec.valuation.price, 0)} {spec.currency}",
        f"- Market capitalisation: {render.fmt(model.market_cap)} {spec.unit}",
        f"- Rating: {stance}",
        f"- Fair-value range: {render.fmt(valuation.fair_value_low, 0)}-"
        f"{render.fmt(valuation.fair_value_high, 0)} {spec.currency}",
        f"- Model version: {spec.model_version} | generated {datetime.now():%Y-%m-%d %H:%M}",
        "",
        "Prices and per-share figures are in the quote currency; statement figures are in "
        f"{spec.unit}. Every table below is computed from the model spec; provenance is "
        "reported per bridge line.",
        "",
    ])


def _stance(valuation: valuation_module.Valuation) -> str:
    if valuation.price < valuation.fair_value_low:
        return "Undervalued versus the model's fair-value range"
    if valuation.price > valuation.fair_value_high:
        return "Overvalued versus the model's fair-value range"
    return "Fairly valued within the model's fair-value range"


def _executive_summary(model: kernel.ModelResult, valuation: valuation_module.Valuation) -> str:
    spec = model.spec
    lines = ["## 1. Executive summary", ""]
    guidance_labels = [p.label for p in spec.periods if p.basis == "guidance"]
    forecast_labels = [p.label for p in spec.periods if p.basis == "forecast"]
    if guidance_labels:
        row = model.row(guidance_labels[0])
        lines.append(
            f"Guidance for {row.label}: revenue {render.fmt(row.revenue)} {spec.unit}, "
            f"operating profit {render.fmt(row.operating_profit)} {spec.unit} "
            f"({render.fmt_pct(row.operating_margin)} margin)."
        )
    for label in forecast_labels:
        row = model.row(label)
        lines.append(
            f"{label} base case: revenue {render.fmt(row.revenue)} {spec.unit}, "
            f"operating profit {render.fmt(row.operating_profit)} {spec.unit} "
            f"({render.fmt_pct(row.operating_margin)} margin), "
            f"EPS {render.fmt(row.eps.get(valuation.basis))} {spec.currency}."
        )
    lines.append(
        f"At {render.fmt(valuation.price, 0)} {spec.currency}, the stock trades on "
        f"{render.fmt(valuation.pe, 1)}x {valuation.period} EPS, against a fair-value range of "
        f"{render.fmt(valuation.fair_value_low, 0)}-{render.fmt(valuation.fair_value_high, 0)}."
    )
    lines.extend(["", "<!-- NARRATIVE PENDING: executive summary argument -->", ""])
    return "\n".join(lines)


def _bridge_section(model: kernel.ModelResult) -> str:
    if not model.spec.bridges:
        return ""
    parts = ["## Forecast bridges", ""]
    for bridge in model.spec.bridges:
        parts.append(f"### {bridge.metric.replace('_', ' ').title()}: "
                     f"{bridge.base_period} → {bridge.target_period}")
        parts.append("")
        parts.append(render.to_markdown(render.bridge_table(bridge, model.spec.unit)))
        parts.append("")
        parts.append(
            f"Underlying margin implied by the bridge: "
            f"{_bridge_margin(bridge, model):.1f}%."
            if _bridge_margin(bridge, model) is not None else ""
        )
        parts.append("")
    return "\n".join(parts)


def _bridge_margin(bridge, model: kernel.ModelResult) -> float | None:
    if bridge.metric != "operating_profit":
        return None
    revenue = model.spec.bridge("revenue", bridge.target_period)
    if revenue is None or not revenue.base_value:
        return None
    return bridge.base_value / revenue.base_value * 100


def _consensus_notes(model: kernel.ModelResult) -> list[str]:
    """What a reader must know before comparing the table row by row."""
    spec = model.spec
    consensus = spec.consensus
    if consensus is None:
        return []
    notes: list[str] = []
    known = {period.label for period in spec.periods}

    # An actual year the consensus does not cover is not compared with reported
    # figures at all, so the model's own number stands unverified. That is worth
    # saying: it is how a derived net profit survived three rebuilds unnoticed.
    missing = [
        period.label
        for period in spec.periods
        if period.kind == "annual" and period.basis == "actual"
        and period.label not in consensus.periods
    ]
    if missing:
        notes.append(
            f"Note: the external consensus has no {' or '.join(missing)} row, so that year was "
            "not checked against it. Where the model has a reported figure for the year (a "
            "filing, or the vendor's annual statement) that figure is used; a derived one is "
            "not independently verified."
        )

    # EPS on a different share count reads as a forecast difference when it is a
    # denominator difference: compare the count each consensus EPS implies.
    basis = consensus.eps_basis or spec.eps_basis
    shares = next((base.shares_m for base in spec.share_bases if base.name == basis), None)
    if shares:
        for label, values in consensus.periods.items():
            net_profit, eps = values.get("net_profit"), values.get("eps")
            if label not in known or not net_profit or not eps:
                continue
            implied = kernel.shares_implied(net_profit, eps, spec.unit)
            if abs(implied - shares) / shares > 0.02:
                notes.append(
                    f"Note: consensus EPS for {label} implies {implied:,.0f}m shares against the "
                    f"model's {shares:,.0f}m {basis} basis, so that row's EPS variance is partly a "
                    "share-count difference, not a forecast difference."
                )
    return notes


def compact_summary(model: kernel.ModelResult, valuation: valuation_module.Valuation) -> str:
    """A prompt-sized rendering of the model: the tables an analyst must not recompute."""
    spec = model.spec
    lines = [
        f"FUNDAMENTAL MODEL — {spec.company or spec.ticker} ({spec.ticker}) — "
        f"model {spec.model_version}",
        f"Units: statement figures in {spec.unit}; per-share figures in {spec.currency}.",
        "",
        "Annual figures:",
    ]
    for period in spec.periods:
        if period.kind != "annual":
            continue
        row = model.row(period.label)
        eps_text = ", ".join(
            f"EPS {base.name} {row.eps[base.name]:,.1f}" for base in spec.share_bases
            if base.name in row.eps
        )
        lines.append(
            f"- {row.label} ({row.basis}): revenue {render.fmt(row.revenue)}, "
            f"operating profit {render.fmt(row.operating_profit)}, "
            f"margin {render.fmt_pct(row.operating_margin)}, net profit {render.fmt(row.net_profit)}"
            + (f", {eps_text}" if eps_text else "")
        )

    if spec.quarterly:
        lines.append("")
        lines.append("Quarterly build (current fiscal year):")
        for label, fin in spec.quarterly.items():
            lines.append(
                f"- {label}: revenue {render.fmt(fin.revenue)}, "
                f"operating profit {render.fmt(fin.operating_profit)}, "
                f"net profit {render.fmt(fin.net_profit)}"
            )

    if spec.consensus:
        headers, rows = render.consensus_table(model)
        if rows:
            lines.append("")
            lines.append(f"External consensus ({spec.consensus.source}), model versus consensus:")
            lines.append("  " + " | ".join(headers))
            for row in rows:
                lines.append("  " + " | ".join(row))
            lines.extend(_consensus_notes(model))
            lines.append(
                "Where the model differs from consensus, say in one sentence why. "
                "Do not average the two."
            )

    for bridge in spec.bridges:
        lines.append("")
        lines.append(
            f"{bridge.metric.replace('_', ' ').title()} bridge "
            f"{bridge.base_period} → {bridge.target_period}: base {bridge.base_value:g}"
        )
        for line in bridge.lines:
            detail = f" [{line.provenance}]"
            if line.formula:
                detail += f" ({line.formula})"
            lines.append(f"  {line.value:+g} {line.label}{detail}")
        lines.append(f"  = {bridge.total:g}")

    lines.append("")
    lines.append(
        f"Valuation: price {render.fmt(valuation.price, 0)} {spec.currency}, "
        f"{valuation.period} EPS {render.fmt(valuation.eps)} on {valuation.basis}, "
        f"P/E {render.fmt(valuation.pe, 1)}x, fair value "
        f"{render.fmt(valuation.fair_value_low, 0)}-{render.fmt(valuation.fair_value_high, 0)} "
        f"{spec.currency}."
    )
    if valuation.scenarios:
        lines.append(
            "Scenarios: "
            + "; ".join(
                f"{s['name']} {render.fmt(s['target'], 0)} ({s['change'] * 100:+.0f}%)"
                for s in valuation.scenarios
            )
        )
    if model.warnings:
        lines.append("")
        lines.append("Model warnings:")
        lines.extend(f"- {warning}" for warning in model.warnings)
    lines.append("")
    lines.append(
        "Do not recalculate these figures. Where the model depends on an assumption, "
        "state the assumption and cite the document or meeting note behind it."
    )
    return "\n".join(lines)


def build_markdown(model: kernel.ModelResult, valuation: valuation_module.Valuation) -> str:
    spec = model.spec
    parts = [_header(model, valuation), _executive_summary(model, valuation)]

    quarterly = render.quarterly_table(model)
    if quarterly[0]:
        parts += ["## Quarterly build", "", render.to_markdown(quarterly), ""]

    parts += ["## Annual forecast", "", render.to_markdown(render.annual_table(model)), ""]
    parts += [_bridge_section(model)]

    segments = render.segment_table(model)
    if segments[0]:
        parts += ["## Segment forecast", "", render.to_markdown(segments), ""]

    cashflow = render.cashflow_table(model)
    if cashflow[0]:
        parts += ["## Cash flow and capex", "", render.to_markdown(cashflow), ""]

    parts += [
        "## Valuation",
        "",
        render.to_markdown(render.valuation_table(model, valuation)),
        "",
        render.to_markdown(render.sensitivity_table(valuation, spec.currency)),
        "",
        f"Fair value uses a {spec.valuation.fair_value_multiple_low:g}-"
        f"{spec.valuation.fair_value_multiple_high:g}x range on {valuation.period} EPS "
        f"({valuation.basis}). {spec.valuation.method_note}",
        "",
    ]

    scenarios = render.scenario_table(valuation, spec.currency)
    if scenarios[0]:
        parts += ["## Scenario analysis", "", render.to_markdown(scenarios), ""]

    consensus = render.consensus_table(model)
    if consensus[0]:
        parts += [
            "## Model versus consensus",
            "",
            f"Source: {spec.consensus.source}" if spec.consensus else "",
            "",
            render.to_markdown(consensus),
            "",
            *_consensus_notes(model),
            "",
        ]

    parts += [
        "## Data quality",
        "",
        f"- Checks passed: {sum(1 for c in model.checks if c.passed)}/{len(model.checks)}",
    ]
    for warning in model.warnings:
        parts.append(f"- Warning: {warning}")
    if not model.warnings:
        parts.append("- No model warnings.")
    parts.append("")
    parts.append(NARRATIVE_MARKER.format(section="catalysts, risks, management questions"))
    parts.append("")
    return "\n".join(part for part in parts if part is not None)


def write_package(
    model: kernel.ModelResult,
    valuation: valuation_module.Valuation,
    out_dir: str | Path,
    spec_path: str | Path | None = None,
) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = render.write_tables(model, valuation, out_dir / "tables")
    report = build_markdown(model, valuation)
    (out_dir / "report.md").write_text(report, encoding="utf-8")

    spec_hash = ""
    if spec_path is not None and Path(spec_path).is_file():
        spec_hash = "sha256:" + hashlib.sha256(Path(spec_path).read_bytes()).hexdigest()

    package = {
        "ticker": model.spec.ticker,
        "company": model.spec.company,
        "model_version": model.spec.model_version,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "spec_hash": spec_hash,
        "periods": [render.row_summary(row) for row in model.rows],
        "valuation": valuation.to_dict(),
        "checks": [
            {"name": c.name, "expected": c.expected, "actual": c.actual, "passed": c.passed}
            for c in model.checks
        ],
        "warnings": model.warnings,
        "tables": {name: str(path.relative_to(out_dir)) for name, path in tables.items()},
    }
    (out_dir / "package.json").write_text(
        json.dumps(package, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return package


def load_spec(path: str | Path) -> ModelSpec:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ModelSpec.from_dict(data)


def build(spec_path: str | Path, out_dir: str | Path) -> dict:
    spec = load_spec(spec_path)
    model = kernel.run(spec)
    valuation = valuation_module.value(model)
    return write_package(model, valuation, out_dir, spec_path)
