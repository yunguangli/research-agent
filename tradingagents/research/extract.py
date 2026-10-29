"""Build a model spec from the corpus: disclosed facts first, gaps made explicit.

The filing supplies what is actually true — Q1 actuals, H1 and full-year
guidance, and the share counts. Everything after that is judgement, so this
module writes a draft with those values filled and a list of what a human or a
later extraction step must supply (bridges, scenarios, valuation multiples).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tradingagents.dataflows.vendors.research_corpus import CorpusStore

from .filing import FilingFacts, parse_file
from .schemas import ModelError, ModelSpec, Period, ShareBase


@dataclass
class Extraction:
    facts: FilingFacts
    document_id: str
    gaps: list[str] = field(default_factory=list)


def extract_filing(root: str, ticker: str, as_of: str = "") -> Extraction:
    """Parse the most recent filing visible on ``as_of``."""
    store = CorpusStore(root, ticker)
    filings = store.list_documents(as_of or None, ["filing"])
    if not filings:
        raise ModelError(
            f"No filing is visible for {ticker} on {as_of or 'any date'}. "
            "Add the earnings release to the corpus or check the publication date."
        )
    record = filings[-1]
    facts = parse_file(store.root / record.storage_path, record.document_id, record.published_at or "")
    gaps = []
    if not facts.actuals:
        gaps.append("no cumulative actuals found in the filing")
    if "H1" not in " ".join(facts.guidance):
        gaps.append("no H1 guidance found")
    if facts.fiscal_year_end not in facts.guidance:
        gaps.append("no full-year guidance found")
    return Extraction(facts=facts, document_id=record.document_id, gaps=gaps)


def skeleton(extraction: Extraction, ticker: str, company: str, price: float) -> ModelSpec:
    """A draft spec for a company with no prior model."""
    facts = extraction.facts
    fy = facts.fiscal_year_end
    quarter_label = next(iter(facts.actuals), f"Q{facts.quarter} {fy}")

    periods = [
        Period(quarter_label, "", "", "actual", kind="quarter"),
        Period(f"H1 {fy}", "", "", "guidance", kind="half"),
        Period(fy, "", "", "guidance", kind="annual"),
    ]
    if f"H1 {fy}" not in facts.guidance:
        periods = [p for p in periods if p.label != f"H1 {fy}"]

    return ModelSpec(
        ticker=ticker,
        company=company,
        currency="JPY" if facts.unit.startswith("JPY") else "",
        unit=facts.unit,
        fiscal_year_end_month=int(fy.split("/")[0][2:]),
        periods=periods,
        actuals={quarter_label: facts.actuals[quarter_label]} if quarter_label in facts.actuals else {},
        guidance=dict(facts.guidance),
        forecast={},
        quarterly={quarter_label: facts.actuals[quarter_label]} if quarter_label in facts.actuals else {},
        quarterly_plan={
            "fy_label": fy,
            "h1_label": f"H1 {fy}",
            "q1_label": quarter_label,
            "h2_split": {},
            "note": "default 50/50 H2 split; replace with a seasonal assumption",
        }
        if quarter_label in facts.actuals and f"H1 {fy}" in facts.guidance
        else None,
        bridges=[],
        share_bases=[
            ShareBase(name, shares, cite=[facts.citations[name]] if name in facts.citations else [])
            for name, shares in facts.shares_m.items()
        ],
        valuation=_empty_valuation(price),
        warnings=list(facts.warnings)
        + [
            "Draft spec: forecast bridges, scenarios, and valuation multiples still need assumptions.",
            "Prior full-year actuals are not in a quarterly filing; add them from the annual filing or deck.",
        ],
    )


def merge(existing: ModelSpec, extraction: Extraction) -> ModelSpec:
    """Overwrite the disclosed blocks of an existing spec with filing facts."""
    facts = extraction.facts
    fy = facts.fiscal_year_end
    quarter_label = next(iter(facts.actuals), f"Q{facts.quarter} {fy}")

    labels = {period.label for period in existing.periods}
    if quarter_label not in labels:
        existing.periods.insert(0, Period(quarter_label, "", "", "actual", kind="quarter"))
    for label in facts.guidance:
        if label not in labels:
            existing.periods.append(
                Period(label, "", "", "guidance", kind="half" if label.startswith("H1") else "annual")
            )

    existing.actuals[quarter_label] = facts.actuals[quarter_label]
    existing.quarterly[quarter_label] = facts.actuals[quarter_label]
    for label, fin in facts.guidance.items():
        existing.guidance[label] = fin

    names = {base.name for base in existing.share_bases}
    for name, shares in facts.shares_m.items():
        if name in names:
            base = existing.share_base(name)
            base.shares_m = round(shares, 4)
            if name in facts.citations:
                base.cite = [facts.citations[name]]
        else:
            existing.share_bases.append(ShareBase(name, round(shares, 4),
                                                  cite=[facts.citations[name]] if name in facts.citations else []))
    if facts.outstanding_m:
        existing.valuation.market_cap_shares_m = round(facts.outstanding_m, 4)

    existing.warnings.append(
        f"Disclosed figures refreshed from {extraction.document_id} ({facts.publication_date})."
    )
    return existing


CONSENSUS_ACTUAL_TOLERANCE = 0.01


def merge_consensus_actuals(existing: ModelSpec, consensus) -> ModelSpec:
    """Use the reported figure for an actual year instead of the model's derivation.

    A consensus row for a past period is the reported result. When the model's
    own actual differs by more than 1%, the reported number wins and the warning
    says both, so a net profit derived as pretax less tax (before minority
    interests) cannot stand in for the attributable figure.
    """
    if consensus is None or not consensus.periods:
        return existing
    for label, values in consensus.periods.items():
        financials = existing.actuals.get(label)
        if financials is None:
            continue
        for metric in ("revenue", "operating_profit", "net_profit"):
            reported = values.get(metric)
            current = financials.get(metric)
            if reported is None or current is None or not reported:
                continue
            if abs(current - reported) / abs(reported) <= CONSENSUS_ACTUAL_TOLERANCE:
                continue
            setattr(financials, metric, float(reported))
            existing.warnings.append(
                f"{label} {metric.replace('_', ' ')} corrected from {current:,.1f} to the "
                f"reported {reported:,.1f} given by the external consensus "
                f"({consensus.source or 'unspecified'}); an actual year is a disclosed "
                "figure, not a forecast."
            )
    return existing


def _empty_valuation(price: float):
    from .schemas import ValuationSpec

    return ValuationSpec(
        price=price,
        fair_value_multiple_low=0.0,
        fair_value_multiple_high=0.0,
        method_note="Valuation multiples not yet set.",
    )


def describe(extraction: Extraction) -> str:
    facts = extraction.facts
    lines = [
        f"filing: {extraction.document_id} ({facts.publication_date})",
        f"period: {facts.fiscal_year_end} Q{facts.quarter} | units: {facts.unit}",
    ]
    for label, fin in {**facts.actuals, **facts.guidance}.items():
        lines.append(
            f"  {label}: revenue {fin.revenue} | OP {fin.operating_profit} | "
            f"ordinary {fin.ordinary_profit} | net {fin.net_profit}"
        )
    for name, shares in facts.shares_m.items():
        lines.append(f"  shares ({name}): {shares:,.1f}m")
    if extraction.gaps:
        lines.append("gaps: " + "; ".join(extraction.gaps))
    return "\n".join(lines)
