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
  "forecast":    {"FY3/28": {"revenue": 0, "operating_profit": 0, "net_profit": 0, "ordinary_profit": 0}},
  "quarterly":   {"Q1 FY3/27": {"revenue": 0, "operating_profit": 0, "net_profit": 0}},
  "quarterly_plan": {"fy_label": "FY3/27", "h1_label": "H1 FY3/27", "q1_label": "Q1 FY3/27",
                     "h2_split": {"revenue": [0.5, 0.5], "operating_profit": [0.5, 0.5], "net_profit": [0.5, 0.5]}},
  "bridges": [
    {"metric": "revenue", "base_period": "FY3/27", "target_period": "FY3/28", "base_value": 0,
     "declared_total": 0,
     "lines": [{"label": "...", "value": 0, "provenance": "derived", "formula": "...", "driver": "advanced substrate pricing", "alternative": 0,
                "cite": [{"document_id": "doc-...", "location": "page 4", "quote": "..."}]}]}
  ],
  "segments":   {"FY3/27": [{"name": "...", "revenue": 0, "operating_profit": 0,
                             "commissioning": "FY3/28"}]},
  "cashflow":   {"FY3/27": {"net_profit": 0, "depreciation": 0, "working_capital_change": 0,
                            "capex": 0, "payout_ratio": 0.4}},
  "share_bases": [{"name": "period_end_net", "shares_m": 0},
                  {"name": "diluted_average", "shares_m": 0,
                   "basic_weighted_average": 0, "diluted_weighted_average": 0,
                   "potential_cb_shares": 0}],
  "net_debt":   {"gross_debt": 0, "cash_and_equivalents": 0,
                 "lease_liabilities": 0, "lease_treatment": "included",
                 "minority_interest": 0, "minority_treatment": "excluded",
                 "as_of": "YYYY-MM-DD", "scope": "consolidated",
                 "source_unit": "JPY_bn",
                 "component_sources": {"gross_debt": "doc-... p.4",
                                       "cash_and_equivalents": "doc-... p.4"},
                 "source_refs": ["doc-... page 12"]},
  "capital_structure": {"issued_shares": 0, "treasury_shares": 0,
                        "current_net_shares": 0,
                        "basic_weighted_average": {"FY3/28": 0},
                        "diluted_weighted_average": {},
                        "convertibles": [{"name": "...", "shares_m": 0,
                                          "conversion_date": "YYYY-MM-DD"}],
                        "verification_status": "VERIFIED"},
  "valuation":  {"price": 0, "fair_value_multiple_low": 0, "fair_value_multiple_high": 0,
                 "fair_value_basis": "full_share", "fair_value_period": "FY3/28",
                 "sensitivity_multiples": [0],
                 "market_cap_basis": "period_end_net",
                 "forecast_eps_basis": "diluted_average",
                 "dilution_sensitivity_basis": "diluted_average",
                 "equity_value_share_basis": "period_end_net",
                 "ev_ebitda_multiple": 0,
                 "ev_ebitda_multiple_rationale": "peer median and own five-year average",
                 "ev_ebitda_multiple_refs": ["doc-..."],
                 "method_note": "..."},
  "scenarios":  [{"name": "Base", "assumptions": "...", "revenue": 0, "operating_profit": 0,
                   "eps": 0, "multiple": 0, "period": "FY3/28"}],
  "consensus":  {"source": "Bloomberg consensus", "eps_basis": "company_style",
                 "periods": {"FY3/28": {"revenue": 0, "operating_profit": 0,
                                        "net_profit": 0, "eps": 0}}},
  "warnings": ["..."]
}"""

RULES = """Rules:
- Use the disclosed ACTUALS below exactly; never change a reported number.
  Management's guidance inside those facts is a reference, not an answer — the
  next rules say how you must use it.
- Build the current fiscal year's quarters and the next fiscal year's quarters.
  Q2 of the current year is fully determined by H1 guidance minus Q1 actual;
  Q3/Q4 split H2 guidance, and you must state that split in quarterly_plan.
- Give annual periods for the current fiscal year and the two following years.
- Put every guided year the documents disclose in "guidance", including a
  next-year figure stated beside this year's. The audit checks a marked company
  figure against this block, so a guidance number left out of it is reported as a
  misquote even though the document stated it.
- Management guidance is a REFERENCE, not your forecast. Build your own view for
  every guided period: put your numbers in "forecast" under the same period
  label (they replace the guidance row in the model), set that period's basis to
  "forecast", and leave management's numbers untouched in "guidance" — the
  report prints both side by side and the debate reads them. Say in warnings, in
  one sentence, where you differ from guidance and why. A forecast that equals
  guidance on every line is adoption, not an independent build: derive it
  bottom-up (your own quarters and bridges) and, if it lands on guidance,
  say in warnings why it coincides. Quarters keep following management's timing;
  do not bend the annual row to make them agree.
- Every guidance AND forecast year needs revenue, operating profit and net
  profit together. If the documents give no visibility for a later year,
  forecast the topline from stated assumptions (or bridge it from the previous
  year) — never leave revenue null while writing net profit: a net profit
  without its topline is rejected by the validator.
- Provide at least three scenarios: Bear, Base and Bull. Each carries its own EPS,
  multiple and target. A single scenario is not a scenario analysis. Scenario
  multiples must sit inside the declared fair-value multiples (the Base target
  belongs within the fair-value range): one model cannot publish two valuations
  that cannot both be right — if they disagree, reconcile them before returning.
- Build each scenario from operational drivers, not from an arbitrary percentage.
  State the demand or volume, the price or ASP, utilisation or mix where relevant,
  the margin, revenue, operating profit, EPS, the free-cash-flow effect when
  material, the multiple and the fair value. Say which evidence would confirm the
  case and which would invalidate it. Scenarios need not be symmetrical around the
  base: a credible downside may be much further away than the upside.
- Value ONE year. `fair_value_period` names the year whose EPS the fair-value
  multiples multiply, and every scenario must price that same year (say so in its
  `period`). A Base target built on next year's EPS while the band multiplies this
  year's is two valuations, not one, and the validator rejects it.
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
- Give each load-bearing bridge line a "driver" (the assumption it represents, e.g.
  advanced substrate pricing, Cell 8 loading, premium mix) and an "alternative" value
  the same unit could plausibly take. The sensitivity engine shocks one line at a
  time; a line without both is reported as a research gap, not estimated.
- Name the three share bases explicitly: `market_cap_basis` (current net shares),
  `forecast_eps_basis` (forecast diluted weighted average), `dilution_sensitivity_basis`
  (full conversion). `eps_basis` is the one forecast EPS and P/E use. A single
  "full_share" base is ambiguous and suppresses per-share outputs. Name
  `equity_value_share_basis` too: the count the EV-derived per-share value divides by
  (usually the current net count).
- Net debt is DERIVED, never asserted: fill "net_debt" with `gross_debt`,
  `cash_and_equivalents`, `lease_liabilities` with `lease_treatment` ("included" or
  "excluded"), `minority_interest` with `minority_treatment`, and `source_refs`.
  Never write a single net figure, and never put 0 for an input the documents do not
  give — omit the field instead. Net debt = gross debt - cash (+ leases or
  minorities only when you said "included"). Also state the balance-sheet `as_of`
  date, the `scope` ("consolidated" or "parent"), the `source_unit` (JPY_bn, JPY_m,
  USD_m, ...) and `component_sources` naming the page for each component. A
  component whose date, unit or scope cannot be reconciled makes EV/EBITDA
  unavailable; the report names which of the three failed.
- Fill "capital_structure": issued, treasury and current net shares, the period
  basic and diluted weighted-average counts, and every convertible with its
  conversion date and share count. Forecast EPS uses the period weighted average;
  full conversion is only the dilution sensitivity. Do not set a "full_conversion"
  share base equal to the diluted average: if conversion timing is unresolved,
  name the forecast basis `conservative_full_conversion` instead of
  `diluted_average`.
- Give "cashflow" (net profit, depreciation, working-capital change, capex) for
  EVERY forecast year you value, not only the first: EV/EBITDA needs the valued
  year's depreciation, and a missing year makes the method unavailable.
- EV/EBITDA is a second, independent method. Its multiple is YOUR assumption:
  set `ev_ebitda_multiple` with `ev_ebitda_multiple_rationale` (peer multiples, the
  company's own history, the rate environment) and `ev_ebitda_multiple_refs`. It is
  an analyst assumption, never a company-reported figure, and the report labels it
  that way. Leave the multiple null when the documents give no basis for one.
  If gross debt, cash, EBITDA (operating profit + depreciation) or the multiple is
  missing, EV/EBITDA becomes UNAVAILABLE on its own — do not guess an input, and do
  not let its absence remove the P/E.
- On a segment, set "commissioning" to the period label it starts operating. A segment
  that books revenue before that period is a timing error and is rejected.
- A bridge moves from one period to a DIFFERENT one. "base_period" is the earlier
  period and "base_value" is that period's reported figure; the lines are the
  changes that reach the target period. Never bridge a period to itself and never
  put the target's own value in base_value: that doubles the base.
- Resolve relative time: when a document says "this year", "next year", "from
  next quarter" or "starting 2H", map it against the DOCUMENT'S OWN date to an
  explicit FY label, and say the mapping in warnings. A project's commencement
  is not its revenue contribution: an investment decision or a ramp start is
  not full-run-rate revenue — state the ramp you assume and when contribution
  begins.
- Bridge each metric from its own drivers. Do not infer revenue from an  operating-profit estimate (a broker's OP number implies a volume you cannot
  see), and do not infer net profit from a revenue consensus. An external
  estimate enters the metric it states, only.
- Split H2 between Q3 and Q4 by the seasonality the documents or the reported
  quarters actually show; use 50/50 only when nothing does, and say in warnings
  that the split is an assumption.
- Scenario multiples must come from market reasoning (peer multiples, the
  company's own history, the rate environment), then the target follows as
  EPS x multiple. Never choose a multiple because it lands the target inside
  the fair-value range — that fits the multiple to the output, and the kernel
  will compare the result to the band anyway.
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


def _reject_bare_net_debt(data: dict) -> None:
    """A single net-debt number cannot be checked and cannot be adjusted.

    It also hides an absent input: a model that writes ``net_debt: 0`` has not
    shown that the company has no debt. The components are required.
    """
    value = data.get("net_debt")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        raise ModelError(
            "net_debt must be stated as components, not one number: give "
            "gross_debt and cash_and_equivalents (plus lease_liabilities with "
            "lease_treatment, minority_interest with minority_treatment, and "
            "source_refs). Omit a component you cannot evidence; never write 0."
        )


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


def _figure_snippets(store, records, as_of: str | None, limit: int = 8) -> str:
    """Passages that state headline figures, whatever the document's language.

    The excerpts above are the first pages of each document; a results table can sit
    deeper in a long transcript. These snippets are where the numbers usually are.
    """
    terms = (
        "net profit", "net income", "operating profit", "revenue",
        # Period words: a quarterly results table often says "first quarter" and
        # "results" without any headline term above, and Q1/Q2 actuals hidden
        # behind them were reported as undisclosed.
        "first quarter", "quarterly", "results",
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
                 unit: str, currency: str = "", consensus=None, figures: str = "",
                 balance_sheet_text: str = "") -> str:
    # The corpus build cannot know the issuer's currency, so it must not claim
    # one (a Taiwan listing priced "668 JPY" made the model flag a mismatch
    # that should never have existed); a caller that knows passes it.
    if not price:
        price_line = "No market price is available. Set valuation.price to 0 and add a warning saying so."
    elif currency:
        price_line = f"The share price for valuation is {price:,.0f} {currency}; use exactly this."
    else:
        price_line = (
            f"The share price for valuation is {price:,.0f} in the issuer's own currency — set "
            '"currency" and "unit" to that currency (e.g. JPY/JPY_bn, TWD/TWD_bn, USD/USD_m) and '
            "use exactly this price."
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

BALANCE SHEET FACTS (deterministically extracted from tables; use for net_debt):
{balance_sheet_text or "none found"}

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


def _balance_sheet_text(store, records) -> str:
    """The extracted rows, as a bounded prompt block."""
    from . import balance_sheet

    rows = []
    for record in records:
        try:
            text = store.text_of(record.document_id).text
        except Exception:  # noqa: BLE001 - one unreadable document must not stop the build
            continue
        rows.extend(balance_sheet.extract(text, record.document_id))
        if len(rows) >= 40:
            break
    return balance_sheet.render(rows)


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


def _year_token(label: str | None) -> str | None:
    """The fiscal-year part of a label ("Q1 FY3/27" -> "FY3/27"), if it has one."""
    if not label:
        return None
    match = re.search(r"FY\s*\d{1,2}/\d{2}|FY\d{2,4}", str(label), re.IGNORECASE)
    return re.sub(r"\s+", "", match.group(0)).upper() if match else None


def _repair_quarterly_plan(spec: ModelSpec) -> None:
    """Point the quarterly plan at labels that exist, or drop the plan.

    A plan is a convenience: it derives Q2-Q4 from Q1 actual plus H1 and FY
    guidance. If its labels do not match the guidance the model actually
    supplied, dropping the plan keeps the rest of the model (and the explicit
    quarterly figures) usable.

    A missing H1 guidance row is different from a wrong label: the company gave
    no half-year forecast, so the model could not supply one. The H1 total is
    then rebuilt from the two reported quarters of that year — the Q2 line
    becomes definitional (H1 - Q1 = Q2), but H2 = FY guidance - H1 still
    cross-checks the reported Q3/Q4, which is the part that matters. Only when
    the labels disagree about the year, or Q1/Q2 are missing, is the plan
    dropped outright.
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

    # No H1 guidance row: rebuild H1 from the reported halves of the year the
    # plan's own Q1 names. The labels must agree on that year first — a label
    # pointing somewhere else is a wrong label, not a missing row.
    if fy and q1:
        year = _year_token(q1)
        if year and _year_token(plan.get("h1_label")) == year:
            q2 = _resolve_label(spec.quarterly, f"Q2 {year}", None)
            first = spec.quarterly.get(q1)
            second = spec.quarterly.get(q2) if q2 else None
            h1_values: dict[str, float] = {}
            if first is not None and second is not None:
                for metric in ("revenue", "operating_profit", "net_profit"):
                    half_year, half_quarter = first.get(metric), second.get(metric)
                    if half_year is not None and half_quarter is not None:
                        h1_values[metric] = half_year + half_quarter
            if h1_values:
                plan["fy_label"], plan["q1_label"] = fy, q1
                plan["h1_values"] = h1_values
                spec.quarterly_plan = plan
                spec.warnings.append(
                    f"Quarterly plan has no H1 guidance row: H1 rebuilt from reported "
                    f"{q1} + {q2}, so the Q2 line is definitional; H2 = {fy} guidance - H1 "
                    "still checks the reported Q3/Q4."
                )
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
    currency: str = "",   # unknown by default: an asserted-but-wrong currency is worse than none
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
        balance_sheet_text=_balance_sheet_text(store, records),
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
            _reject_bare_net_debt(data)
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
