"""Read a Japanese quarterly earnings release (決算短信) into structured facts.

This is the anchor of the model: disclosed actuals and company guidance are
parsed deterministically, never inferred by a model. A number read here carries
its page and line, so a report can cite the filing for it.

The parser is intentionally strict. If the metric header is not the expected
revenue / operating profit / ordinary profit / net profit sequence, it raises
rather than mapping values to the wrong labels.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from tradingagents.dataflows.vendors.research_corpus.extract import extract

from .schemas import Citation, Financials, ModelError

_FULLWIDTH = str.maketrans({
    "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
    "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
    "，": ",", "．": ".", "％": "%", "－": "-", "−": "-", "–": "-", "　": " ",
})
_QUARTER = str.maketrans("１２３４", "1234")

_METRIC_ORDER = ("revenue", "operating_profit", "ordinary_profit", "net_profit")
_METRIC_TOKENS = (
    ("売上高", "revenue"),
    ("営業利益", "operating_profit"),
    ("経常利益", "ordinary_profit"),
    ("純利益", "net_profit"),
)

_PAGE = re.compile(r"^--- page (\d+) ---$")


@dataclass
class FilingFacts:
    """What a filing states, with a citation for every block."""

    publication_date: str
    fiscal_year_end: str          # e.g. FY3/27
    quarter: int
    unit: str                     # JPY_bn
    scale: float                  # divide parsed numbers by this
    actuals: dict[str, Financials] = field(default_factory=dict)
    prior: dict[str, Financials] = field(default_factory=dict)
    guidance: dict[str, Financials] = field(default_factory=dict)
    shares_m: dict[str, float] = field(default_factory=dict)
    outstanding_m: float | None = None
    treasury_m: float | None = None
    citations: dict[str, Citation] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _normalise(text: str) -> str:
    return text.translate(_FULLWIDTH)


def _page_at(lines: list[str], index: int) -> str:
    for back in range(index, -1, -1):
        match = _PAGE.match(lines[back].strip())
        if match:
            return f"page {match.group(1)}"
    return ""


def _numbers(line: str) -> list[float]:
    """Value/percent pairs, then any trailing number (e.g. EPS)."""
    values: list[float] = []
    for amount, _percent in re.findall(r"([\d,]+)\s+(-?[\d.]+)", line):
        values.append(float(amount.replace(",", "")))
    trailing = re.search(r"(-?[\d.]+)\s*$", line)
    if trailing:
        last = float(trailing.group(1))
        if not values or abs(last - values[-1]) > 1e-9:
            values.append(last)
    return values


def _metric_columns(header: str) -> list[str]:
    """Map the header's left-to-right order onto statement metrics."""
    order = []
    position = {}
    for token, metric in _METRIC_TOKENS:
        index = header.find(token)
        if index >= 0:
            position[metric] = index
    ordered = sorted(position.items(), key=lambda item: item[1])
    for metric, _index in ordered:
        order.append(metric)
    if order[:4] != list(_METRIC_ORDER):
        raise ModelError(
            "Unexpected filing header: expected 売上高 / 営業利益 / 経常利益 / 純利益 in that order, "
            f"found {order}. Refusing to map values to the wrong metrics."
        )
    return order


def _fiscal_year(text: str) -> tuple[str, int, int] | None:
    """``(label, fiscal_end_year, month)`` from a period phrase such as 2027年３月期."""
    match = re.search(r"(\d{4})年(\d{1,2})月期", text)
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    return f"FY{month}/{year % 100:02d}", year, month


def _quarter(text: str) -> int | None:
    match = re.search(r"第([1-4])四半期", text.translate(_QUARTER))
    return int(match.group(1)) if match else None


def parse_filing(text: str, document_id: str, publication_date: str = "") -> FilingFacts:
    lines = [_normalise(line) for line in text.splitlines()]

    fiscal = None
    quarter = None
    for line in lines[:40]:
        if "決算短信" in line or "四半期" in line:
            fiscal = fiscal or _fiscal_year(line)
            quarter = quarter or _quarter(line)
        if fiscal and quarter:
            break
    if not fiscal or not quarter:
        raise ModelError("This document does not look like a quarterly earnings release (決算短信)")

    label, year, month = fiscal
    scale = 1000.0 if "百万円" in text[:2000] else 1.0

    facts = FilingFacts(
        publication_date=publication_date,
        fiscal_year_end=label,
        quarter=quarter,
        unit="JPY_bn",
        scale=scale,
    )

    # --- cumulative actuals ------------------------------------------------
    block = next((i for i, line in enumerate(lines) if "連結経営成績" in line), None)
    if block is None:
        raise ModelError("Could not find the consolidated results block (連結経営成績)")
    header_index, columns = None, None
    for index in range(block, min(block + 8, len(lines))):
        if "売上高" in lines[index] and "営業利益" in lines[index]:
            header_index = index
            columns = _metric_columns(lines[index])
            break
    if header_index is None:
        raise ModelError("Could not find the metric header row in the results block")

    for index in range(header_index, min(header_index + 8, len(lines))):
        line = lines[index]
        if not line.strip() or line.startswith("（注）"):
            continue
        period = re.match(r"^(\d{4})年(\d{1,2})月期第([1-4])四半期", line)
        if not period:
            continue
        row_year, row_month = int(period.group(1)), int(period.group(2))
        row_quarter = int(period.group(3))
        values = _numbers(line)
        if len(values) < 4:
            continue
        fin = Financials(**{metric: values[i] / scale for i, metric in enumerate(columns)})
        row_label = f"Q{row_quarter} FY{row_month}/{row_year % 100:02d}"
        if row_year == year and row_month == month and row_quarter == quarter:
            facts.actuals[row_label] = fin
            facts.citations[row_label] = Citation(document_id, _page_at(lines, index), line.strip()[:200])
        else:
            facts.prior[row_label] = fin

    # --- company guidance --------------------------------------------------
    guidance_header = next(
        (i for i, line in enumerate(lines) if "売上高" in line and "当期純利益" in line), None
    )
    if guidance_header is None:
        facts.warnings.append("No full-year guidance block found in the filing")
    else:
        columns = _metric_columns(lines[guidance_header])
        for index in range(guidance_header, min(guidance_header + 8, len(lines))):
            line = lines[index].strip()
            if line.startswith("通期"):
                target = facts.fiscal_year_end
            elif line.startswith("第2四半期") or line.startswith("第２四半期"):
                target = f"H1 {facts.fiscal_year_end}"
            else:
                continue
            values = _numbers(line)
            if len(values) < 4:
                continue
            facts.guidance[target] = Financials(
                **{metric: values[i] / scale for i, metric in enumerate(columns)}
            )
            facts.citations[target] = Citation(document_id, _page_at(lines, index), line[:200])

    # --- share counts ------------------------------------------------------
    for index, line in enumerate(lines):
        if "期末発行済株式数" in line:
            match = re.search(r"([\d,]+)株", line)
            if match:
                facts.shares_m["full_share"] = float(match.group(1).replace(",", "")) / 1e6
                facts.citations["full_share"] = Citation(document_id, _page_at(lines, index), line[:200])
        elif "期末自己株式数" in line:
            match = re.search(r"([\d,]+)株", line)
            if match:
                facts.treasury_m = float(match.group(1).replace(",", "")) / 1e6
        elif "期中平均株式数" in line:
            match = re.search(r"([\d,]+)株", line)
            if match:
                facts.shares_m["company_style"] = float(match.group(1).replace(",", "")) / 1e6
                facts.citations["company_style"] = Citation(document_id, _page_at(lines, index), line[:200])
    if not facts.shares_m:
        facts.warnings.append("Share count not found; EPS cannot be computed from the filing alone")
    if facts.shares_m.get("full_share") and facts.treasury_m:
        # Market capitalisation is price x shares outstanding, i.e. issued less
        # treasury. The EPS count includes potential shares and overstates it.
        facts.outstanding_m = facts.shares_m["full_share"] - facts.treasury_m

    return facts


def parse_file(path: str | Path, document_id: str, publication_date: str = "") -> FilingFacts:
    extracted = extract(path)
    return parse_filing(extracted.text, document_id, publication_date)
