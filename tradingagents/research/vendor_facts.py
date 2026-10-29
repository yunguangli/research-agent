"""Reported annual figures from the market-data vendor, for the model's actual years.

A corpus rarely prints a full-year attributable profit: earnings calls talk about
revenue and operating profit, and the filing that carries the rest may not be in
the folder. Asked to fill that gap, a model derives it — pretax less tax — and
gets a number that is internally consistent and wrong, because it ignores
minorities. That derivation then changes on every rebuild.

The vendor already holds the reported figure for every closed fiscal year, so it
is used here as the source for actual periods. The model still builds the
forecast; only the past is taken from data.

Point in time: the vendor dates a statement by the period it covers and reports
no filing date, so a year is used only when its period ended at least
``FILING_LAG_DAYS`` before the run date. Anything closer could have been filed
after the run and is withheld.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, timedelta

# Long enough for a Japanese 決算短信 (45 days) or a US 10-K (60-90 days) to be
# filed after the fiscal year closes.
FILING_LAG_DAYS = 90

# Label in the vendor's statement -> our metric name. "Net Income Common
# Stockholders" is the attributable figure; "Net Income" includes minorities and
# is only a fallback.
_ROWS = {
    "revenue": ("Total Revenue",),
    "operating_profit": ("Operating Income",),
    "net_profit": ("Net Income Common Stockholders", "Net Income"),
}

# Applied only to a value that disagrees with the model's own: a 25% gap is a
# scale or currency mismatch (an ADR reporting in another currency), not a
# correction, and overriding it would poison every downstream ratio.
_MAX_CORRECTION = 0.25


class VendorFactsError(RuntimeError):
    """The vendor's statement could not be read; the build continues without it."""


@dataclass
class ReportedYear:
    """One fiscal year of reported figures, in the model's reporting unit."""

    period_end: str
    revenue: float | None = None
    operating_profit: float | None = None
    net_profit: float | None = None

    def get(self, metric: str) -> float | None:
        return getattr(self, metric, None)


def _scale(unit: str) -> float:
    suffix = unit.rsplit("_", 1)[-1].lower()
    if suffix == "bn":
        return 1e-9
    if suffix == "m":
        return 1e-6
    raise VendorFactsError(f"Unsupported model unit {unit!r}; use e.g. JPY_bn or USD_m")


def _number(text: str) -> float | None:
    try:
        return float(text.replace(",", "").strip())
    except (AttributeError, ValueError):
        return None


def parse_annual_statement(text: str, unit: str) -> dict[str, ReportedYear]:
    """``{period_end: figures}`` from the vendor's annual income statement CSV.

    The header carries one column per fiscal year, labelled by the period end,
    which is the same date a spec's periods use. Figures are printed in the
    reporting currency and scaled into the model's unit; EPS is not used, because
    the kernel derives it from net profit and the share counts.
    """
    if not text or "withheld" in text.lower():
        raise VendorFactsError("The vendor returned no annual statement.")
    lines = text.splitlines()
    body = "\n".join(line for line in lines if not line.startswith("#"))
    rows = list(csv.reader(io.StringIO(body)))
    if not rows or len(rows[0]) < 2:
        raise VendorFactsError("The vendor's income statement has no period columns.")

    header = rows[0]
    periods = {index: column.strip()[:10] for index, column in enumerate(header) if index}
    periods = {index: end for index, end in periods.items() if end[:2] == "20"}

    scale = _scale(unit)
    years = {end: ReportedYear(period_end=end) for end in periods.values()}
    for row in rows[1:]:
        if not row or not row[0]:
            continue
        label = row[0].strip()
        for metric, names in _ROWS.items():
            if label not in names:
                continue
            for index, end in periods.items():
                if index >= len(row):
                    continue
                # An earlier, preferred row (attributable profit) must not be
                # overwritten by its fallback.
                if getattr(years[end], metric) is not None:
                    continue
                value = _number(row[index])
                if value is not None:
                    setattr(years[end], metric, value * scale)
    return {
        end: year
        for end, year in years.items()
        if any(year.get(metric) is not None for metric in _ROWS)
    }


def is_public(period_end: str, as_of: str, lag_days: int = FILING_LAG_DAYS) -> bool:
    """Whether a fiscal year ended long enough before ``as_of`` to have been filed."""
    if not period_end or not as_of:
        return False
    try:
        ended = date.fromisoformat(period_end[:10])
        run = date.fromisoformat(as_of[:10])
    except ValueError:
        return False
    return ended + timedelta(days=lag_days) <= run


def describe(years: dict[str, ReportedYear], as_of: str) -> str:
    """Prompt text for the years that were public by ``as_of``, or ``""``."""
    public = [year for end, year in sorted(years.items()) if is_public(end, as_of)]
    if not public:
        return ""
    lines = [
        "REPORTED ANNUAL FIGURES (the vendor's audited statements; use these exactly for an "
        "actual year, never a derivation):"
    ]
    for year in public:
        parts = []
        for metric, label in (
            ("revenue", "revenue"),
            ("operating_profit", "operating profit"),
            ("net_profit", "net profit attributable"),
        ):
            value = year.get(metric)
            if value is not None:
                parts.append(f"{label} {value:,.1f}")
        lines.append(f"- year ended {year.period_end}: " + ", ".join(parts))
    return "\n".join(lines)


def fetch_annual(ticker: str, unit: str) -> dict[str, ReportedYear]:
    """The vendor's annual income statement, read once per build.

    Called without a run date: this is the vendor's present-day snapshot, and the
    public-period rule in :func:`is_public` decides which years may be used.
    """
    from tradingagents.dataflows.router import route_to_vendor

    try:
        text = route_to_vendor("get_income_statement", ticker, "annual", None)
    except Exception as exc:  # noqa: BLE001 - a missing statement must not stop a build
        raise VendorFactsError(f"No annual statement for {ticker}: {exc}") from exc
    return parse_annual_statement(str(text), unit)


def merge_reported_actuals(spec, years: dict[str, ReportedYear], as_of: str) -> list[str]:
    """Fill the model's actual years with reported figures; return what changed.

    Only periods the spec already treats as actual, and only those public by the
    run date. A value that disagrees with the model's own by more than 25% is
    treated as a unit or currency mismatch and left alone.
    """
    if not years or not as_of:
        return []
    from .schemas import Financials

    changed: list[str] = []
    for period in spec.periods:
        if period.kind != "annual" or period.basis != "actual":
            continue
        reported = years.get(period.end)
        if reported is None or not is_public(period.end, as_of):
            continue
        financials = spec.actuals.get(period.label)
        if financials is None:
            financials = Financials()
            spec.actuals[period.label] = financials
        for metric in ("revenue", "operating_profit", "net_profit"):
            value = reported.get(metric)
            if value is None:
                continue
            current = financials.get(metric)
            if current is not None and abs(current - value) / abs(current) <= 0.01:
                continue
            if current is not None and abs(value - current) / abs(current) > _MAX_CORRECTION:
                spec.warnings.append(
                    f"{period.label} {metric.replace('_', ' ')}: the vendor reports {value:,.1f} "
                    f"against the model's {current:,.1f}; that is more than a correction, so it was "
                    "not applied. Check the statement's currency and scale."
                )
                continue
            setattr(financials, metric, float(value))
            before = f"{current:,.1f}" if current is not None else "nothing"
            spec.warnings.append(
                f"{period.label} {metric.replace('_', ' ')} taken from the reported annual "
                f"statement ({before} -> {value:,.1f}); the corpus does not state this figure."
            )
            changed.append(f"{period.label} {metric}")
    return changed
