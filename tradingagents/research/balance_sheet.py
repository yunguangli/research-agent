"""Table-aware balance-sheet extraction for the EV inputs.

The builder reads prose and excerpts; a PDF balance sheet is a table, and the
rows that matter (cash, bonds, borrowings, leases) sit under a unit header and
column headers. Line regex alone matched "long-term loans receivable" and
missed the debt entirely. This module maps labelled rows to components, keeps
the column values with their period labels, and preserves unit, page and
document, so the builder receives facts rather than a keyword hit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CASH = "cash_and_equivalents"
SHORT_BORROWINGS = "short_term_borrowings"
CURRENT_DEBT = "current_portion_of_debt"
BONDS = "bonds"
CONVERTIBLE = "convertible_bonds"
LONG_LOANS = "long_term_loans"
LEASES = "lease_liabilities"
MINORITY = "minority_interest"
TOTAL_DEBT = "interest_bearing_debt"

DEBT_COMPONENTS = (
    SHORT_BORROWINGS, CURRENT_DEBT, BONDS, CONVERTIBLE, LONG_LOANS, TOTAL_DEBT,
)

# Label -> component. Order matters: the first match wins, so a compound label
# is tested before its shorter part.
_ALIASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"convertible bonds?|転換社債", re.I), CONVERTIBLE),
    (re.compile(r"current portion of bonds|1年内償還予定の社債", re.I), CURRENT_DEBT),
    (re.compile(r"current portion of (?:long[- ]term )?(?:loans?|borrowings?)|1年内返済予定の長期借入金", re.I), CURRENT_DEBT),
    (re.compile(r"short[- ]term (?:borrowings?|loans?)|短期借入金", re.I), SHORT_BORROWINGS),
    (re.compile(r"bonds payable|社債", re.I), BONDS),
    (re.compile(r"long[- ]term (?:loans? payable|borrowings?)|長期借入金", re.I), LONG_LOANS),
    (re.compile(r"lease (?:obligations?|liabilities?)|リース債務", re.I), LEASES),
    (re.compile(r"non[- ]controlling interests?|minority interests?|非支配株主持分", re.I), MINORITY),
    (re.compile(r"interest[- ]bearing debt|有利子負債", re.I), TOTAL_DEBT),
    (re.compile(r"cash and (?:cash )?equivalents|cash and deposits|現金及び預金|現金預金", re.I), CASH),
]

# A receivable, a repayment or a narrative conversion note is not the balance.
_EXCLUDE = re.compile(
    r"receivable|未収|貸付|repayment|proceeds|redemption|converted|"
    r"face value|cash dividends|dividends paid|conversion price|stock split|"
    r"due 20\d\d|支払|配当",
    re.I,
)

_UNIT = [
    (re.compile(r"million[s]? of yen|百万円", re.I), "JPY_m"),
    (re.compile(r"thousand[s]? of yen|千円", re.I), "JPY_m"),
    (re.compile(r"billion[s]? of yen|十億円", re.I), "JPY_bn"),
    (re.compile(r"million[s]? of u\.?s\.? dollars|百万米ドル", re.I), "USD_m"),
    (re.compile(r"thousand[s]? of u\.?s\.? dollars|千米ドル", re.I), "USD_m"),
]
_CURRENT = re.compile(r"\bcurrent\b|当期|当期末|当中間", re.I)
_PRIOR = re.compile(r"\bprior\b|previous|前期|前中間", re.I)
_CHANGE = re.compile(r"\bchange\b|増減|比較", re.I)
_PAGE = re.compile(r"(?:page|ページ)\s*(\d+)", re.I)
_NUMBER = re.compile(r"-?\(?[\d,]+(?:\.\d+)?\)?")


@dataclass
class BalanceSheetRow:
    document_id: str
    label: str
    component: str
    values: dict[str, float] = field(default_factory=dict)
    unit: str = ""
    page: str = ""
    line: str = ""

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "label": self.label,
            "component": self.component,
            "values": self.values,
            "unit": self.unit,
            "page": self.page,
            "line": self.line,
        }


def _number(token: str) -> float | None:
    cleaned = token.replace(",", "").strip()
    negative = cleaned.startswith("(") or cleaned.startswith("△")
    cleaned = cleaned.strip("()△")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def _columns(header: str) -> list[str]:
    """Column names in the order they appear, or the conventional three."""
    found: list[tuple[int, str]] = []
    for pattern, name in ((_CURRENT, "current"), (_PRIOR, "prior"), (_CHANGE, "change")):
        match = pattern.search(header)
        if match:
            found.append((match.start(), name))
    ordered = [name for _index, name in sorted(found)]
    return ordered or ["current", "prior", "change"]


def extract(text: str, document_id: str) -> list[BalanceSheetRow]:
    """Every mapped balance-sheet row in one document, with its columns."""
    rows: list[BalanceSheetRow] = []
    unit = ""
    columns = ["current", "prior", "change"]
    page = ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        page_match = _PAGE.search(line)
        if page_match:
            page = f"page {page_match.group(1)}"
        for pattern, name in _UNIT:
            if pattern.search(line):
                unit = name
                break
        if _CURRENT.search(line) and _PRIOR.search(line):
            columns = _columns(line)
            continue
        if _EXCLUDE.search(line):
            continue
        component = None
        remainder = line
        for pattern, name in _ALIASES:
            match = pattern.search(line)
            if match:
                component = name
                remainder = line[match.end():]
                break
        if component is None:
            continue
        values = [_number(token) for token in _NUMBER.findall(remainder)]
        values = [value for value in values if value is not None]
        if not values:
            continue
        mapped = {
            columns[index] if index < len(columns) else f"column_{index + 1}": value
            for index, value in enumerate(values)
        }
        rows.append(
            BalanceSheetRow(
                document_id=document_id,
                label=line[:120],
                component=component,
                values=mapped,
                unit=unit,
                page=page,
                line=line[:240],
            )
        )
    return rows


def components(rows: list[BalanceSheetRow]) -> dict[str, float]:
    """Current-period totals by component (leases and minorities separately)."""
    totals: dict[str, float] = {}
    for row in rows:
        value = row.values.get("current")
        if value is None:
            continue
        key = row.component
        totals[key] = totals.get(key, 0.0) + value
    return totals


def derive(rows: list[BalanceSheetRow]) -> dict:
    """Gross debt, cash and net debt from the extracted rows, with a note.

    An itemised component list is summed, and a stated ``interest-bearing debt``
    total is used only when no component is itemised, so the two never double
    count. A disagreement between the stated total and the components is
    reported rather than resolved silently.
    """
    totals = components(rows)
    itemised = {
        name: value
        for name, value in totals.items()
        if name in DEBT_COMPONENTS and name != TOTAL_DEBT
    }
    stated_total = totals.get(TOTAL_DEBT)
    notes: list[str] = []
    if itemised:
        gross: float | None = sum(itemised.values())
        notes.append("gross debt is the sum of itemised components")
        if stated_total is not None and abs(stated_total - gross) > max(0.5, abs(gross) * 0.01):
            notes.append(
                f"the stated interest-bearing total {stated_total:,.0f} disagrees "
                f"with the itemised components ({gross:,.0f})"
            )
    elif stated_total is not None:
        gross = stated_total
        notes.append("gross debt is the stated total; no component was itemised")
    else:
        gross = None
        notes.append("no debt component was found")

    cash = totals.get(CASH)
    net = gross - cash if gross is not None and cash is not None else None
    if gross is not None and cash is None:
        notes.append("cash was not found, so net debt cannot be derived")
    return {
        "gross_debt": gross,
        "cash": cash,
        "net_debt": net,
        "lease_liabilities": totals.get(LEASES),
        "minority_interest": totals.get(MINORITY),
        "itemised": itemised,
        "note": "; ".join(notes),
    }


def render(rows: list[BalanceSheetRow], limit: int = 24) -> str:
    """A prompt block: one line per row with its source, unit and columns."""
    if not rows:
        return ""
    lines = [
        "Deterministically extracted balance-sheet rows. Use them for net_debt "
        "components; state the unit, the balance-sheet date and the scope, and cite "
        "the document and page. A receivable is not debt.",
    ]
    derived = derive(rows)
    if derived["gross_debt"] is not None or derived["net_debt"] is not None:
        parts = []
        if derived["gross_debt"] is not None:
            parts.append(f"gross debt {derived['gross_debt']:,.0f}")
        if derived["cash"] is not None:
            parts.append(f"cash {derived['cash']:,.0f}")
        if derived["net_debt"] is not None:
            parts.append(f"net debt {derived['net_debt']:,.0f}")
        lines.append(f"- derived: {', '.join(parts)} ({derived['note']})")
    for row in rows[:limit]:
        columns = ", ".join(f"{name} {value:,.0f}" for name, value in row.values.items())
        lines.append(
            f"- {row.component}: {columns} | unit {row.unit or 'not stated'} | "
            f"{row.document_id} {row.page or ''} | {row.label}"
        )
    return "\n".join(lines)
