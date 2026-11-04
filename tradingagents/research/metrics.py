"""Growth and incremental margin, computed once from the verified model.

The 13:09 report called an ~97% operating-profit growth rate an ~42% *incremental
operating margin*. The two are different quantities:

    op_growth            = current_op / prior_op - 1
    incremental_margin   = (current_op - prior_op) / (current_revenue - prior_revenue)

The kernel already recomputes every margin and growth figure it prints; this module
exists for the two derived series it does not store, so an analyst or manager does
not have to recompute them in prose (and cannot relabel one as the other by accident).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .kernel import ModelResult

# Quarter labels; their rows are not annual growth steps. Half-year rows are
# kept: a half-on-half comparison is a real growth rate for a half-year model.
_NON_ANNUAL = re.compile(r"^Q[1-4]\b", re.IGNORECASE)


@dataclass(frozen=True)
class PeriodMetrics:
    """One period's change against the period before it."""

    label: str
    prior_label: str
    revenue_growth: float | None
    op_growth: float | None
    incremental_margin: float | None


def period_metrics(model: ModelResult) -> list[PeriodMetrics]:
    """Growth and incremental margin for every consecutive pair of non-quarter rows.

    Quarterly rows are excluded: a quarter compared with the year before it is
    not a growth rate, and the spec may declare quarters in any order, so pairing
    them produced "Q1 FY3/27 (vs FY3/29)".
    """
    rows = [row for row in model.rows if not _NON_ANNUAL.match(row.label.strip())]
    metrics: list[PeriodMetrics] = []
    for prior, current in zip(rows, rows[1:], strict=False):
        revenue_growth = _growth(prior.revenue, current.revenue)
        op_growth = _growth(prior.operating_profit, current.operating_profit)
        incremental: float | None = None
        if (
            prior.operating_profit is not None
            and current.operating_profit is not None
            and prior.revenue is not None
            and current.revenue is not None
        ):
            delta_revenue = current.revenue - prior.revenue
            if abs(delta_revenue) > 1e-9:
                incremental = (
                    (current.operating_profit - prior.operating_profit) / delta_revenue
                )
        metrics.append(
            PeriodMetrics(
                label=current.label,
                prior_label=prior.label,
                revenue_growth=revenue_growth,
                op_growth=op_growth,
                incremental_margin=incremental,
            )
        )
    return metrics


def _growth(prior: float | None, current: float | None) -> float | None:
    if prior is None or current is None or abs(prior) < 1e-9:
        return None
    return current / prior - 1


def render_metrics(metrics: list[PeriodMetrics]) -> str:
    """A small table of the two series, for a package or an audit note."""
    if not metrics:
        return ""
    lines = [
        "| Period | Revenue growth | Operating-profit growth | Incremental operating margin |",
        "|---|---|---|---|",
    ]
    for row in metrics:
        lines.append(
            f"| {row.label} (vs {row.prior_label}) | {_pct(row.revenue_growth)} | "
            f"{_pct(row.op_growth)} | {_pct(row.incremental_margin)} |"
        )
    return "\n".join(lines)


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.1f}%"
