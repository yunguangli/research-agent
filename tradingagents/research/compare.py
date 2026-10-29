"""Compare a generated model against a reference report, when one exists.

A firm rarely has a reference report for a company it has not covered before, so
nothing here is required for a run. When one does exist — a report a human wrote,
or an earlier version of ours — this module answers the only question that
matters for calibration: do the numbers agree, and where do they not?

The reference is never used as evidence. Its tables are read for comparison only,
so a report cannot be fed into its own re-run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .kernel import ModelResult
from .schemas import ModelError
from .valuation import Valuation

# Absolute tolerances, in the unit each metric is reported in.
TOLERANCES = {
    "revenue": 0.15,
    "operating_profit": 0.15,
    "ordinary_profit": 0.15,
    "net_profit": 0.15,
    "operating_margin": 0.15,   # percentage points
    "eps": 0.15,                # per share
    "target": 2.0,              # scenario target price
}

_METRIC_LABELS = {
    "revenue": "revenue",
    "sales": "revenue",
    "operating profit": "operating_profit",
    "operating income": "operating_profit",
    "op": "operating_profit",
    "op margin": "operating_margin",
    "operating margin": "operating_margin",
    "ordinary profit": "ordinary_profit",
    "net profit": "net_profit",
    "net income": "net_profit",
    "eps": "eps",
}

_PERIOD = re.compile(r"(FY\s?\d+(?:/\d+)?|\d{4}Q\d|Q\d\s?FY\d+(?:/\d+)?)", re.IGNORECASE)


@dataclass
class ReferenceTable:
    headers: list[str]
    rows: list[list[str]]


@dataclass
class Comparison:
    label: str
    reference: float
    model: float
    delta: float
    tolerance: float

    @property
    def ok(self) -> bool:
        return abs(self.delta) <= self.tolerance

    def describe(self) -> str:
        status = "ok" if self.ok else "DIFF"
        return (
            f"[{status:4}] {self.label:34} reference {self.reference:>10,.1f}  "
            f"model {self.model:>10,.1f}  delta {self.delta:+8.2f}"
        )


def load_tables(path: str | Path) -> list[ReferenceTable]:
    """Read tables from a DOCX or a Markdown/text file."""
    path = Path(path)
    if path.suffix.lower() == ".docx":
        return _docx_tables(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    return _markdown_tables(text)


def _docx_tables(path: Path) -> list[ReferenceTable]:
    try:
        import docx
    except ImportError as exc:  # pragma: no cover - depends on the install extra
        raise ModelError('Reading a reference DOCX needs pypdf/docx: pip install "tradingagents[docs]"') from exc

    document = docx.Document(str(path))
    tables = []
    for table in document.tables:
        rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        if len(rows) >= 2:
            tables.append(ReferenceTable(headers=rows[0], rows=rows[1:]))
    return tables


def _markdown_tables(text: str) -> list[ReferenceTable]:
    tables: list[ReferenceTable] = []
    block: list[str] = []
    for line in [*text.splitlines(), ""]:
        stripped = line.strip()
        if stripped.startswith("|"):
            block.append(stripped)
            continue
        if len(block) >= 2:
            cells = [[c.strip() for c in row.strip("|").split("|")] for row in block]
            if all(set(cell) <= {"-", ":"} for cell in cells[1]):
                cells.pop(1)
            tables.append(ReferenceTable(headers=cells[0], rows=cells[1:]))
        block = []
    return tables


def _number(text: str) -> float | None:
    cleaned = text.replace(",", "").replace("¥", "").replace("￥", "").replace("%", "").strip()
    cleaned = cleaned.replace("−", "-").replace("–", "-")
    match = re.search(r"[+-]?\d+(?:\.\d+)?", cleaned)
    return float(match.group()) if match else None


def _period(text: str) -> str | None:
    match = _PERIOD.search(text.replace(" ", ""))
    if not match:
        return None
    value = match.group(1).upper().replace(" ", "")
    return value.replace("FY", "FY")


_IGNORE_LABELS = {
    "operating cash flow",
    "free cash flow",
    "post-dividend free cash flow",
    "capex",
    "depreciation and amortisation",
    "depreciation",
    "dividend",
    "working-capital change",
    "market capitalisation",
    "share price",
}


def _metric(text: str) -> str | None:
    lowered = text.lower().strip()
    if lowered in _IGNORE_LABELS:
        return None
    for needle in sorted(_METRIC_LABELS, key=len, reverse=True):
        # "OP" must not match "Operating cash flow", so the two-letter label is exact.
        if (len(needle) <= 2 and lowered == needle) or (len(needle) > 2 and lowered.startswith(needle)):
            return _METRIC_LABELS[needle]
    return None


def _eps_basis(label: str, default: str) -> str:
    lowered = label.lower()
    if "company" in lowered:
        return "company_style"
    if "full" in lowered:
        return "full_share"
    return default


def _model_value(model: ModelResult, period: str, metric: str, eps_basis: str) -> float | None:
    try:
        row = model.row(period)
    except ModelError:
        return None
    if metric == "eps":
        return row.eps.get(eps_basis)
    return getattr(row, metric, None)


def compare(model: ModelResult, valuation: Valuation, tables: list[ReferenceTable]) -> list[Comparison]:
    """Match reference table cells to model values, by period and metric."""
    spec = model.spec
    basis = spec.eps_basis
    results: list[Comparison] = []

    for table in tables:
        # Annual tables: a period in the header, a metric in the row label.
        # Quarterly tables reuse the same period tokens (Q1 FY3/27), so they are
        # excluded rather than compared against the annual figure.
        header_periods = [_period(cell) for cell in table.headers]
        quarterly = any(re.search(r"\bQ\d\b", cell, re.IGNORECASE) for cell in table.headers)
        annual = any(header_periods) and not quarterly
        for row in table.rows:
            if not row:
                continue
            label = row[0].strip()
            metric = _metric(label)
            row_basis = _eps_basis(label, basis)
            if metric and annual:
                for index, _header in enumerate(table.headers):
                    period = header_periods[index]
                    if not period or index >= len(row):
                        continue
                    reference = _number(row[index])
                    model_value = _model_value(model, period, metric, row_basis)
                    if reference is None or model_value is None:
                        continue
                    results.append(Comparison(
                        label=f"{period} {metric}" + (f" ({row_basis})" if metric == "eps" else ""),
                        reference=reference,
                        model=model_value,
                        delta=model_value - reference,
                        tolerance=TOLERANCES.get(metric, 0.2),
                    ))

        # Scenario tables: one row per scenario, with a target column.
        if any("target" in cell.lower() for cell in table.headers):
            target_index = next(i for i, cell in enumerate(table.headers) if "target" in cell.lower())
            by_name = {scenario["name"].lower(): scenario for scenario in valuation.scenarios}
            for row in table.rows:
                if not row:
                    continue
                scenario = by_name.get(row[0].strip().lower())
                if scenario is None or target_index >= len(row):
                    continue
                reference = _number(row[target_index])
                if reference is None:
                    continue
                results.append(Comparison(
                    label=f"scenario {row[0].strip()} target",
                    reference=reference,
                    model=scenario["target"],
                    delta=scenario["target"] - reference,
                    tolerance=TOLERANCES["target"],
                ))
    return results


def fair_value_from_text(path: str | Path) -> tuple[float, float] | None:
    """The fair-value range stated in a reference report's prose, if present."""
    path = Path(path)
    if path.suffix.lower() == ".docx":
        try:
            import docx
        except ImportError:  # pragma: no cover
            return None
        document = docx.Document(str(path))
        text = "\n".join(p.text for p in document.paragraphs)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(
        r"fair[- ]value range\s*:?\s*[¥￥]?\s*([\d,]+)\s*[-–]\s*[¥￥]?([\d,]+)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return float(match.group(1).replace(",", "")), float(match.group(2).replace(",", ""))
