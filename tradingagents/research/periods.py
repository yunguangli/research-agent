"""Canonical fiscal periods: one date-derived label per fiscal year.

Company labels disagree across markets: a Japanese filing calls the year ending
March 2027 "FY2026" (the 2026年度 convention), a broker note may write "FY3/27", and
a December filer calls its year "FY2027". The model keeps one canonical label per
period, derived from the period's own end date and the company's fiscal-year-end
month, so every table, bridge and citation agrees on which year is meant.

This module only translates and checks; it never changes the spec.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from .schemas import ModelSpec, Period

_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


@dataclass(frozen=True)
class FiscalYear:
    """A fiscal year: the canonical label and the days it covers."""

    label: str
    end_month: int
    start: str
    end: str

    @property
    def status(self) -> str:  # kept for callers that print it beside the label
        return "year"

    def to_dict(self) -> dict:
        return {"label": self.label, "start": self.start, "end": self.end}


def _month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _label(end_month: int, end_year: int) -> str:
    """The canonical label: ``FY3/27`` for a March year-end, ``FY2026`` for December.

    A December year-end is the common case where "FY2026" already means the
    calendar year, so the label is the plain end year.
    """
    if end_month == 12:
        return f"FY{end_year}"
    return f"FY{end_month}/{end_year % 100:02d}"


def fiscal_year(end_month: int, end_year: int) -> FiscalYear:
    """The fiscal year ending in ``end_month`` of ``end_year``."""
    if not 1 <= end_month <= 12:
        raise ValueError(f"fiscal year end month must be 1-12, got {end_month}")
    end = _month_end(end_year, end_month)
    start = _month_end(end_year - 1, end_month) + timedelta(days=1)
    return FiscalYear(
        label=_label(end_month, end_year),
        end_month=end_month,
        start=start.isoformat(),
        end=end.isoformat(),
    )


def canonical_label(end: str, end_month: int) -> str | None:
    """The canonical label for a period that ends on ``end``, or None if unparsable."""
    match = _DATE_RE.match(str(end or "").strip())
    if not match:
        return None
    try:
        end_date = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
    if end_date.month != end_month:
        return None
    return _label(end_month, end_date.year)


def label_for_period(period: Period, end_month: int) -> str | None:
    """The canonical label a period's own dates imply."""
    if period.kind != "annual":
        return None
    return canonical_label(period.end, end_month)


def annual_periods(spec: ModelSpec) -> list[Period]:
    return [period for period in spec.periods if period.kind == "annual"]


def audit_periods(spec: ModelSpec) -> list[str]:
    """Warnings for labels that disagree with the dates they carry.

    A mismatch is a warning, not an error: the dates are what every computation
    uses, so a spec whose label is the company's own ("FY2027") is still usable —
    it just must not be quoted as the canonical year.
    """
    warnings: list[str] = []
    for period in annual_periods(spec):
        implied = label_for_period(period, spec.fiscal_year_end_month)
        if implied is None:
            continue
        if period.label != implied:
            warnings.append(
                f"{period.label} ends {period.end}, which is {implied}: quote the canonical "
                f"label {implied} in tables and citations (the company may call it "
                f"{period.label})."
            )
        start = _DATE_RE.match(str(period.start or "").strip())
        if start and str(period.start) >= str(period.end):
            warnings.append(
                f"{period.label} starts {period.start} and ends {period.end}: the start must "
                "precede the end."
            )
    return warnings
