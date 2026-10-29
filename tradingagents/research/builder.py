"""Let the model build the forecast, then make it prove its own arithmetic.

The model reads the research directory and produces the forecast — periods,
bridges, segments, cash flow, valuation, scenarios — as one JSON object. Python
does not invent any of the numbers. It does three things the model cannot do for
itself: parse the filing into disclosed facts it must use, validate the resulting
model (every bridge sums, every margin recomputes, every EPS divides), and hand
back the exact failure so the model can correct it once.

A model that fails validation twice is rejected rather than rendered.
"""

from __future__ import annotations

import json
import re

from . import kernel
from .schemas import ModelError, ModelSpec, Period

MAX_EXCERPT_CHARS = 6000
MAX_TOTAL_CHARS = 45000
_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

SHAPE = """{
  "ticker": "5803.T", "company": "...", "currency": "JPY", "unit": "JPY_bn",
  "fiscal_year_end_month": 3, "eps_basis": "full_share",
  "periods": [
    {"label": "FY3/27", "start": "2026-04-01", "end": "2027-03-31", "basis": "guidance", "kind": "annual"},
    {"label": "FY3/28", "start": "2027-04-01", "end": "2028-03-31", "basis": "forecast", "kind": "annual"},
    {"label": "Q2 FY3/27", "start": "2026-07-01", "end": "2026-09-30", "basis": "forecast", "kind": "quarter"}
  ],
  "actuals":     {"FY3/26": {"revenue": 0, "operating_profit": 0, "ordinary_profit": 0, "net_profit": 0}},
  "guidance":    {"FY3/27": {"revenue": 0, "operating_profit": 0, "ordinary_profit": 0, "net_profit": 0}},
  "forecast":    {"FY3/28": {"net_profit": 0, "ordinary_profit": 0}},
  "quarterly":   {"Q1 FY3/27": {"revenue": 0, "operating_profit": 0, "net_profit": 0}},
  "quarterly_plan": {"fy_label": "FY3/27", "h1_label": "H1 FY3/27", "q1_label": "Q1 FY3/27",
                     "h2_split": {"revenue": [0.5, 0.5], "operating_profit": [0.5, 0.5], "net_profit": [0.5, 0.5]}},
  "bridges": [
    {"metric": "revenue", "base_period": "FY3/27", "target_period": "FY3/28", "base_value": 0,
     "declared_total": 0,
     "lines": [{"label": "...", "value": 0, "provenance": "derived", "formula": "...",
                "cite": [{"document_id": "doc-...", "location": "page 4", "quote": "..."}]}]}
  ],
  "segments":   {"FY3/27": [{"name": "...", "revenue": 0, "operating_profit": 0}]},
  "cashflow":   {"FY3/27": {"net_profit": 0, "depreciation": 0, "working_capital_change": 0,
                            "capex": 0, "payout_ratio": 0.4}},
  "share_bases": [{"name": "full_share", "shares_m": 0}],
  "scenarios":  [{"name": "Base", "assumptions": "...", "revenue": 0, "operating_profit": 0,
                  "eps": 0, "multiple": 0}],
  "valuation":  {"price": 0, "fair_value_multiple_low": 0, "fair_value_multiple_high": 0,
                 "fair_value_basis": "full_share", "sensitivity_multiples": [0],
                 "market_cap_shares_m": 0, "method_note": "..."},
  "consensus":  {"source": "Bloomberg consensus", "eps_basis": "company_style",
                 "periods": {"FY3/28": {"revenue": 0, "operating_profit": 0,
                                        "net_profit": 0, "eps": 0}}},
  "warnings": ["..."]
}"""

RULES = """Rules:
- Use the disclosed facts below exactly. Do not change a disclosed number.
- Build the current fiscal year's quarters and the next fiscal year's quarters.
  Q2 of the current year is fully determined by H1 guidance minus Q1 actual;
  Q3/Q4 split H2 guidance, and you must state that split in quarterly_plan.
- Give annual periods for the current fiscal year and the two following years.
- Provide at least three scenarios: Bear, Base and Bull. Each carries its own EPS,
  multiple and target. A single scenario is not a scenario analysis.
- Write cash-flow figures in the model's unit. Japanese filings quote 億円
  (100 million yen): 403億円 is JPY40.3bn, not JPY403bn. A capex figure near or
  above 20% of revenue is almost always a unit error.
- Use the issuer's own fiscal calendar. A March year-end company labels the year
  ending March 2027 as FY3/27 (or FY2027); a December year-end company uses FY12/27.
  Do not convert one company's periods to another company's fiscal year.
- market_cap_shares_m is shares outstanding (issued shares minus treasury shares),
  used for market capitalisation. It is not the EPS share count: EPS may use a
  larger full-share or average count, which would overstate the market cap.
- If the documents contain a consensus table or consensus estimates (a broker
  note, a Bloomberg export transcribed in a document), put those numbers in
  "consensus" with their source and the EPS share basis. Do not invent a
  consensus: omit the block when no document states one.
- For net profit use the amount attributable to owners of the parent, never the
  figure that includes non-controlling interests. EPS is computed from the
  attributable figure; if a document shows both, use the attributable one and
  say which you used.
- Every forecast line comes from a named bridge line. Each bridge line's
  "provenance" is exactly one of: disclosed, guidance, derived, assumption,
  internal_note. Derived lines must carry a "formula" and the inputs in words.
- Every non-obvious number carries a citation: document_id plus page or para.
  Use only document ids from the list below. A bridge line labelled "derived" or
  "internal_note" must cite the document it came from.
- Revenue and operating-profit bridges must sum to declared_total.
  "declared_total" is the resulting level after the deltas (1,755 - 480 + 225 +
  150 = 1,650), NOT the sum of the deltas. Omit it if unsure.
- Every period you put in actuals, guidance or forecast must also appear in
  "periods" with its basis ("actual", "guidance", "forecast").
- Segments, when given, must sum to group revenue and group operating profit.
- Cash flow: net_profit with depreciation and working capital gives operating
  cash flow; minus capex gives free cash flow.
- Do not leave a required key out. Use null only where a value is genuinely
  unknown, and say why in warnings.
- Return JSON only. No prose, no code fence."""


def _extract_json(text: str) -> dict:
    cleaned = _CODE_FENCE.sub("", text or "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ModelError("The model did not return a JSON object.")
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ModelError(f"The model's JSON is invalid: {exc}") from exc


def _content(response) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return str(content)


def _figure_snippets(store, records, as_of: str | None, limit: int = 6) -> str:
    """Passages that state headline figures, whatever the document's language.

    The excerpts above are the first pages of each document; a results table can sit
    deeper in a long transcript. These snippets are where the numbers usually are.
    """
    terms = (
        "net profit", "net income", "operating profit", "revenue",
        "当期純利益", "営業利益", "売上高", "親会社株主",
    )
    seen: set[str] = set()
    hits: list[str] = []
    for term in terms:
        try:
            found = store.search(term, as_of, limit=2)
        except Exception:  # noqa: BLE001 - a search failure must not stop the build
            continue
        for hit in found:
            if hit.snippet in seen:
                continue
            seen.add(hit.snippet)
            hits.append(hit.snippet)
    return "\n".join(hits[:limit])


def build_prompt(*, ticker: str, company: str, as_of: str, facts_text: str,
                 index_text: str, excerpts: str, price: float | None,
                 unit: str, currency: str, consensus=None, figures: str = "") -> str:
    price_line = (
        f"The share price for valuation is {price:,.0f} {currency}; use exactly this."
        if price else
        "No market price is available. Set valuation.price to 0 and add a warning saying so."
    )
    consensus_block = ""
    if consensus is not None and consensus.periods:
        rows = []
        for label, values in consensus.periods.items():
            parts = ", ".join(f"{k} {v:g}" for k, v in values.items())
            rows.append(f"- {label}: {parts}")
        basis = consensus.eps_basis or "the model's EPS basis"
        consensus_block = (
            f"\nEXTERNAL CONSENSUS ({consensus.source or 'consensus'}; EPS on {basis}). "
            "Use it as a benchmark, not as an answer: keep your own forecast, and where you "
            "differ by more than 5% on revenue, operating profit or EPS, say why in warnings.\n"
            + "\n".join(rows) + "\n"
        )
    return f"""You are the fundamental analyst for {company or ticker} ({ticker}), as of {as_of}.
Build the financial model from the research documents below.

DISCLOSED FACTS (from the filings; use exactly):
{facts_text or "No machine-parsed filing was available. Take disclosed numbers from the documents "
                "below, cite the document and page for each, and say in warnings if a period's "
                "actuals could not be found."}
{consensus_block}
DOCUMENTS (cite only these; they may be in English, Japanese, Simplified or Traditional
Chinese, or Korean - read each in its own language and quote figures as printed):
{index_text}

DOCUMENT EXCERPTS:
{excerpts}

KEY FIGURE PASSAGES (where the statements state headline numbers):
{figures or "none found"}

{price_line}

Return one JSON object with this shape (values are placeholders):
{SHAPE}

{RULES}"""


def _excerpt_text(store, records, skip: set[str]) -> tuple[str, str]:
    """Index block and bounded excerpts, so the prompt cannot grow without limit."""
    index_lines = []
    excerpts = []
    used = 0
    for record in records:
        index_lines.append(
            f"- {record.document_id} | {record.doc_type} | {record.published_at} | {record.title}"
        )
        if record.document_id in skip or used >= MAX_TOTAL_CHARS:
            continue
        try:
            text = store.text_of(record.document_id).text
        except Exception:  # noqa: BLE001 - one unreadable document must not stop the build
            excerpts.append(f"[{record.document_id}] unreadable; do not rely on it")
            continue
        budget = min(MAX_EXCERPT_CHARS, MAX_TOTAL_CHARS - used)
        chunk = text[:budget]
        used += len(chunk)
        excerpts.append(f"--- {record.document_id} ---\n{chunk}")
    return "\n".join(index_lines), "\n\n".join(excerpts)


def _declare_missing_periods(spec: ModelSpec) -> None:
    """Add a period row for any label the model used but did not declare."""
    declared = {period.label for period in spec.periods}
    for group, basis in (("actuals", "actual"), ("guidance", "guidance"), ("forecast", "forecast")):
        for label in getattr(spec, group):
            if label in declared:
                continue
            if label.startswith("H"):
                kind = "half"
            elif label[:1].upper() == "Q":
                kind = "quarter"
            else:
                kind = "annual"
            spec.periods.append(Period(label=label, start="", end="", basis=basis, kind=kind))
            declared.add(label)
            spec.warnings.append(f"Declared the {label} period from its use in {group}.")


def _repair_bridge_totals(spec: ModelSpec) -> None:
    """A ``declared_total`` equal to the sum of the deltas means the wrong field.

    The model sometimes writes the delta total where the resulting level belongs
    (195 instead of 1950). When the declared number matches the deltas and not
    the built total, treat it as absent and keep the built total, visibly.
    """
    for bridge in spec.bridges:
        if bridge.declared_total is None:
            continue
        if abs(bridge.declared_total - bridge.deltas) <= kernel.TOL and (
            abs(bridge.declared_total - bridge.total) > kernel.TOL
        ):
            spec.warnings.append(
                f"{bridge.metric} bridge: declared_total {bridge.declared_total:g} equals the sum of "
                f"the deltas, not the resulting level; used {bridge.total:g}."
            )
            bridge.declared_total = None


def _sort_periods(spec: ModelSpec) -> None:
    kind_order = {"annual": 0, "half": 1, "quarter": 2}
    basis_order = {"actual": 0, "guidance": 1, "forecast": 2}
    spec.periods.sort(key=lambda p: (kind_order.get(p.kind, 3), basis_order.get(p.basis, 3), p.label))


def _resolve_label(mapping: dict, wanted: str | None, prefix: str | None) -> str | None:
    """Find the key the model meant, tolerating case, spaces and 1H/H1."""
    if not mapping:
        return None
    if wanted and wanted in mapping:
        return wanted

    def norm(text: str) -> str:
        return str(text).upper().replace(" ", "").replace("_", "").replace("1H", "H1")

    if wanted:
        target = norm(wanted)
        for key in mapping:
            if norm(key) == target:
                return key
    if prefix:
        candidates = [key for key in mapping if norm(key).startswith(norm(prefix))]
        if len(candidates) == 1:
            return candidates[0]
        return None
    if len(mapping) == 1:
        return next(iter(mapping))
    return None


def _repair_quarterly_plan(spec: ModelSpec) -> None:
    """Point the quarterly plan at labels that exist, or drop the plan.

    A plan is a convenience: it derives Q2-Q4 from guidance. If its labels do not
    match the guidance the model actually supplied, dropping the plan keeps the
    rest of the model (and the explicit quarterly figures) usable.
    """
    plan = spec.quarterly_plan
    if not isinstance(plan, dict):
        return
    h1 = _resolve_label(spec.guidance, plan.get("h1_label"), "H1")
    fy = _resolve_label(spec.guidance, plan.get("fy_label"), "FY")
    q1 = _resolve_label(spec.quarterly, plan.get("q1_label"), "Q1")
    if h1 and fy and q1:
        plan["h1_label"], plan["fy_label"], plan["q1_label"] = h1, fy, q1
        spec.quarterly_plan = plan
        return
    spec.quarterly_plan = None
    spec.warnings.append(
        "Dropped the quarterly plan: its labels "
        f"({plan.get('q1_label')!r}, {plan.get('h1_label')!r}, {plan.get('fy_label')!r}) do not match "
        f"the guidance keys ({sorted(spec.guidance)}) and quarterly keys ({sorted(spec.quarterly)})."
    )


def _require_scenarios(spec: ModelSpec) -> None:
    """A single scenario is not a scenario analysis."""
    if len(spec.scenarios) < 2:
        raise ModelError(
            f"The model needs at least Bear, Base and Bull scenarios; it has {len(spec.scenarios)}. "
            "Add scenarios with their own EPS, multiple and target."
        )


def _normalise_spec(spec: ModelSpec) -> None:
    _declare_missing_periods(spec)
    _repair_quarterly_plan(spec)
    _repair_bridge_totals(spec)
    _sort_periods(spec)
    _require_scenarios(spec)


def _reject_unknown_citations(spec: ModelSpec, known: set[str]) -> None:
    """A citation to a document that is not in the corpus is a fabricated source."""
    cited = {
        citation.document_id
        for bridge in spec.bridges
        for line in bridge.lines
        for citation in line.cite
    } | {citation.document_id for base in spec.share_bases for citation in base.cite}
    unknown = sorted(cited - known)
    if unknown:
        raise ModelError(
            f"Unknown document id(s) in citations: {unknown}. Cite only documents from the list."
        )

    uncited = [
        line.label
        for bridge in spec.bridges
        for line in bridge.lines
        if line.provenance in {"derived", "internal_note", "disclosed"} and not line.cite
    ]
    if uncited:
        raise ModelError(
            "These bridge lines claim a documented source but cite no document: "
            + "; ".join(uncited[:6])
            + ". Add a document_id from the list, or label the line 'assumption'."
        )


def build_spec(
    llm,
    *,
    ticker: str,
    company: str,
    as_of: str,
    store,
    facts_text: str,
    price: float | None,
    unit: str = "JPY_bn",
    currency: str = "JPY",
    skip_documents: set[str] | None = None,
    max_attempts: int = 2,
    dump_path=None,
    overlay=None,
    consensus=None,
) -> tuple[ModelSpec, list[str]]:
    """Ask the model for a spec, validate it, and let it correct one failure."""
    records = store.list_documents(as_of or None)
    index_text, excerpts = _excerpt_text(store, records, skip_documents or set())
    figures = _figure_snippets(store, records, as_of or None)
    prompt = build_prompt(
        ticker=ticker, company=company, as_of=as_of, facts_text=facts_text,
        index_text=index_text, excerpts=excerpts, price=price, unit=unit, currency=currency,
        consensus=consensus, figures=figures,
    )

    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    messages = [SystemMessage(prompt), HumanMessage("Return the JSON object now.")]
    attempts: list[str] = []
    last_error = ""
    last_raw = ""
    known_documents = {record.document_id for record in records}
    for _ in range(max_attempts):
        raw = _content(llm.invoke(messages))
        last_raw = raw
        try:
            data = _extract_json(raw)
            spec = ModelSpec.from_dict(data)
            spec.ticker = spec.ticker or ticker
            spec.company = spec.company or company
            if price:
                spec.valuation.price = float(price)
            if consensus is not None:
                # A supplied consensus is data, not something to regenerate: it wins
                # over anything the model wrote from the documents.
                spec.consensus = consensus
            if overlay is not None:
                overlay(spec)
            _normalise_spec(spec)
            _reject_unknown_citations(spec, known_documents)
            kernel.run(spec)
            return spec, attempts
        except (ModelError, KeyError, TypeError, ValueError, AttributeError) as exc:
            last_error = str(exc)
            attempts.append(last_error)
            messages = [
                *messages,
                AIMessage(raw),
                HumanMessage(
                    "That model failed validation:\n"
                    f"{last_error}\n"
                    "Correct the JSON so every check passes. Return JSON only."
                ),
            ]
    if dump_path is not None:
        try:
            from pathlib import Path as _Path

            _Path(dump_path).write_text(
                json.dumps({"error": last_error, "attempts": attempts, "raw": last_raw},
                           indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass
    raise ModelError(
        "The model could not build a valid forecast after "
        f"{max_attempts} attempts. Last error: {last_error}"
    )
