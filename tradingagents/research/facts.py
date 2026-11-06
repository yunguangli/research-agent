"""Verified source facts, independent of the model's derived forecast.

When the deterministic model fails, the reported results, company guidance,
balance-sheet inputs and disclosed segment/project facts do not become false. They
must remain available to every downstream agent; only the model-derived forecast,
valuation and sensitivities are withheld.

This module renders the spec's source blocks and never reads ``forecast``,
``bridges``, ``scenarios`` or the valuation. A source fact is a number a company
reported or guided, not a number an agent computed.
"""

from __future__ import annotations

from . import render

_HEADER = "SOURCE_FACTS (company-reported / company-guided; no model forecast)"


def _financials(label: str, row, unit: str) -> list[str]:
    parts = []
    for name in ("revenue", "operating_profit", "ordinary_profit", "net_profit", "eps"):
        value = getattr(row, name, None)
        if value is not None:
            parts.append(f"{name} {render.fmt(value)}")
    return [f"- {label}: " + ", ".join(parts)] if parts else []


def render_source_facts(spec) -> str:
    """A compact block of reported and guided facts, with no model output."""
    if spec is None:
        return ""
    lines = [_HEADER]
    actual_lines: list[str] = []
    for label, row in (getattr(spec, "actuals", None) or {}).items():
        actual_lines.extend(_financials(f"{label} actual", row, spec.unit))
    guidance_lines: list[str] = []
    for label, row in (getattr(spec, "guidance", None) or {}).items():
        guidance_lines.extend(_financials(f"{label} company guidance", row, spec.unit))
    if actual_lines:
        lines.append("Reported results:")
        lines.extend(actual_lines)
    if guidance_lines:
        lines.append("Company guidance:")
        lines.extend(guidance_lines)
    debt = getattr(spec, "net_debt", None)
    if debt is not None and getattr(debt, "components_verified", lambda: False)():
        lines.append(
            "Balance sheet (company-reported): "
            f"gross debt {render.fmt(debt.gross_debt)}; "
            f"cash {render.fmt(debt.cash_and_equivalents)}; "
            f"lease liabilities {render.fmt(debt.lease_liabilities)}; "
            f"net debt {render.fmt(debt.value)}."
        )
    segments = getattr(spec, "segments", None) or {}
    if segments:
        lines.append("Disclosed segments (reported periods):")
        for label, items in segments.items():
            if str(label) not in {period.label for period in spec.periods
                                  if period.basis != "forecast"}:
                continue
            shown = ", ".join(
                f"{item.name} revenue {render.fmt(item.revenue)}, "
                f"operating profit {render.fmt(item.operating_profit)}"
                for item in items
            )
            if shown:
                lines.append(f"- {label}: {shown}")
    projects = getattr(spec, "projects", None) or []
    if projects:
        lines.append("Disclosed projects:")
        for project in projects:
            name = (getattr(project, "project_name", "")
                    or getattr(project, "project_id", "") or "project")
            dates = ", ".join(
                f"{key} {value}" for key, value in (
                    ("construction", getattr(project, "construction_start", "")),
                    ("commissioning", getattr(project, "commissioning_start", "")),
                    ("production", getattr(project, "production_start", "")),
                ) if value
            )
            capex = getattr(project, "capex", None)
            detail = f"{name}: " + (dates or "timing not stated")
            if capex:
                detail += f"; capex {render.fmt(capex)}"
            lines.append(f"- {detail}")
    if len(lines) == 1:
        lines.append("- No structured reported/guidance block was available.")
    lines.append(
        "Transcript and filing commentary is preserved as ANALYST_JUDGMENTS; it is "
        "not model-derived unless it cites the model."
    )
    return "\n".join(lines)
