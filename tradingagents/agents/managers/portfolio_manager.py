"""Lead Investment Synthesizer: makes the final decision from the evidence.

Uses LangChain's ``with_structured_output`` so the LLM produces a typed
``PortfolioDecision`` directly, in a single call. Its rating is the run's
``final_rating``, and the decision is rendered to markdown as
``final_trade_decision`` for the memory log, CLI display and saved reports.
When a provider does not expose structured output, the agent falls back to
free-text generation and the rating is read from that text (the runtime
instruction requires the ``**Rating**:`` line the parser reads).

The prompt is the Lead Investment Synthesizer template: it weighs the inputs
and must not expose raw internal debate transcripts in the output — the
adversarial review and the risk-team views arrive as inputs, summarized at the
model's discretion.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time

from tradingagents.agents.context import (
    EVIDENCE_DISCIPLINE,
    FIGURE_MARKER_RULE,
    bridge_arithmetic_block,
    case_availability,
    case_block,
    corrections_block,
    evidence_pack,
    fill_placeholders,
    fundamentals_with_repair_notice,
    get_language_instruction,
    model_facts_block,
    position_fields,
    report_or_absent,
    resolved_status_block,
    source_facts_block,
)
from tradingagents.agents.rating import RATING_REVIEW, parse_rating
from tradingagents.agents.schemas import (
    DecisionConfidence,
    DecisionTrigger,
    HolderAction,
    NewInvestorAction,
    PortfolioDecision,
    PortfolioDecisionMinimal,
    PortfolioRating,
    TriggerEvidence,
    ValuationStatus,
    render_pm_decision,
)
from tradingagents.agents.sizing import (
    gate_sizing,
    has_exact_size,
    scrub_execution_levels,
    scrub_sizing,
)
from tradingagents.agents.structured import (
    NO_EXTERNAL_TOOLS,
    STRUCTURED_RETRY,
    bind_structured,
    invoke_structured,
)
from tradingagents.dataflows.config import get_config
from tradingagents.research.cases import strip_case_json

logger = logging.getLogger(__name__)

# The decision prompt is the slowest call in the run. These caps keep it bounded
# without dropping the evidence the decision cites: each block is truncated with
# a marker, and the full text remains in the report tree.
_PROMPT_CAPS = {
    "fundamentals": 12000,
    "review": 8000,
    "risk": 8000,
    "package": 6000,
    "plan": 4000,
    "trader": 3000,
    "portfolio": 2000,
    "lessons": 3000,
}


def _clip(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[…truncated for the decision prompt; full text in the report]"

TEMPLATE = r"""You are the Lead Investment Synthesizer in a global-equity research workflow.

Produce the final investment assessment for {{instrument_ticker}} as of
{{analysis_date}}.

Your job is to make a disciplined decision from the evidence, not to average the
outputs mechanically.

INPUTS

Source-extraction evidence pack:
{{evidence_pack_or_null}}

Independent fundamental analysis:
{{fundamental_analysis_or_null}}

Model audit:
{{model_audit_or_null}}

Fundamental packet (earnings facts; no price, no per-share output):
{{fundamental_packet_or_null}}

Valuation packet (verified valuation outputs only):
{{valuation_packet_or_null}}

Technical analysis:
{{technical_analysis_or_null}}

Verified technical evidence (stable IDs):
{{technical_evidence_or_null}}

Adversarial review:
{{adversarial_review_or_null}}

Decision horizon: {{decision_horizon_or_null}}
Current position context: {{current_position_or_null}}
Average cost: {{average_cost_or_null}}
Portfolio weight: {{portfolio_weight_or_null}}
Mandate and constraints: {{mandate_constraints_or_null}}
Validated trading rules: {{validated_trading_rules_or_null}}

Workflow artifacts (the run's own conclusions — inputs, not transcripts to repeat):
- Instrument identity: {{instrument_context_or_null}}
- Caller's book (rendered): {{portfolio_block_or_null}}
- Research Manager's investment plan: {{research_plan_or_null}}
- Trader's proposal: {{trader_proposal_or_null}}
- Risk-team assessments: {{risk_team_or_null}}
- Quantified cases from the debate: {{cases_or_null}}
{{past_lessons_line}}

EVIDENCE HIERARCHY

Prefer:

1. Primary company and regulatory evidence;
2. Reliable point-in-time market data;
3. Independently reasoned forecast assumptions;
4. Audited model outputs;
5. External consensus and broker evidence;
6. Private or internal notes, clearly labeled.

Do not treat agent agreement as evidence.

Do not average conflicting forecasts merely because both exist. Determine which
assumptions are better supported.

A forecast that equals guidance or consensus is no forecast differentiation, not evidence
for a negative rating. Decide the rating from valuation, expectations, catalysts and
downside protection. Technical evidence affects timing, not fundamental value.

State the action for an existing holder and the action for a new investor separately; do
not report one action for all readers, and do not infer a short from an Underweight. The
case table's confidence column is agent confidence, not a calibrated probability: do not
compute an expected value from it.

DECISION FRAMEWORK

Answer these questions:

1. What does the market appear to expect?
2. Where, if anywhere, does the analysis differ?
3. What can cause estimates or valuation to change within the decision horizon?
4. Is expected upside large enough relative to downside and uncertainty?
5. Does the technical structure improve or weaken the timing?
6. What evidence would change the stance?

Keep separate:

- Fundamental value;
- Earnings-revision potential;
- Technical timing;
- Portfolio suitability.

The inputs always contain conflicting positions; deciding which is better
supported is the job, so conflict alone is not a reason to Hold.

RATING

Use the rating framework supplied by the runtime system:
{{rating_framework_or_null}}

If no rating framework is supplied, use:

- Positive;
- Neutral;
- Negative.

Do not use false numerical precision.

Do not assign exact probabilities unless supported by a documented methodology.

State "the forecast equals guidance/consensus" at most once, as a neutral observation. It
is not evidence for a negative rating and must not be repeated as the reason for one.

Also fill the typed decision fields separately: fundamental view (Positive / Neutral /
Negative), valuation view (Attractive / Fair / Full), earnings-revision view (Rising /
Stable / Falling), technical timing (Favorable / Neutral / Unfavorable), the
new-investor action (Initiate / Wait / Avoid), the existing-holder action (Add / Hold /
Review / Reduce / Exit), the position-size constraint (what caps the position, never a
size), the fundamental thesis-invalidation conditions, and the evidence that would
upgrade or downgrade the stance. Confidence (High / Medium / Low). Also set
`downgrade_applicability`: at the lowest rating (Sell/Exit) it is
ALREADY_AT_LOWEST_RATING and no evidence-to-downgrade is required; a Reduce must name
the evidence that would trigger an Exit, with APPLICABLE. Do not compress all of them
into the rating.

Rating and holder action must agree: a partial reduction is Underweight with the holder
action Reduce; Sell is reserved for a full exit.

RESOLUTION

Do not vote or average the analysts' ratings. Resolve the disagreements and make one
portfolio decision from the verified model and the repaired valuation. If fundamentals,
valuation and technicals disagree, say which one controls the current action and why.
Explain the cost of waiting for confirmation, and do not require both fundamental and
technical confirmation automatically: a staged entry is valid when valuation is
attractive and the technical evidence is only weak. Normal price volatility is not
thesis invalidation.

DECISION BASIS AUDIT

Fill `decision_basis` (fundamental | event | technical | mixed) and `decision_audit`:

{
  "controlling_category": "...",
  "horizon": "...",
  "evidence": [{"category": "...", "underlying_driver_id": "...", "evidence_refs": [],
                "primary_source_refs": [], "disclosure_ref": "...",
                "verified": true, "supports_action": true}],
  "severe_operating_deterioration": false,
  "observed_operating_decline_pct": null,
  "observed_margin_compression_bp": null,
  "uses_suppressed_figures": false,
  "confirmation_conditions": [], "invalidation_conditions": []
}
Categories: operating, earnings_revision, consensus_gap, cash_flow, balance_sheet,
capital_allocation, regulatory_legal_accounting, catalyst, technical.
One `underlying_driver_id` per underlying fact: a margin miss, its revision and the
consensus gap it creates are ONE cluster. A Sell or Reduce without a verified valuation
needs three independent clusters, one cash-flow/balance-sheet/event or severe-operating
leg, and two independent primary disclosures. Technical evidence alone cannot support a
fundamental Buy or Sell; a mixed basis must name the controlling category.

For category "technical", cite only an evidence_id printed in VERIFIED TECHNICAL
EVIDENCE, with `underlying_driver_id` equal to that id, `verified: true`, and
`primary_source_refs` copied from the item. Do not invent a technical item. A mixed
basis is controlled by a fundamental or event category, never by "technical"; if the
technical leg is weak, use decision_basis "fundamental" and say the technical view is
an advisory overlay rather than failing the whole basis.

If the valuation packet says P/E is SUPPRESSED, the rating must not rely on a multiple:
do not state a P/E, market cap, EPS-derived value, price target or fair-value range, and
do not combine the price in the technical packet with net profit in the fundamental
packet. A reconstruction invalidates the decision.

POSITION CONTEXT

If current holdings and cost are supplied:

- Explain the implication for the existing position;
- Distinguish holding from initiating or adding;
- Do not let cost basis determine fundamental value.

If portfolio weight, mandate or risk tolerance is missing:

- Do not prescribe exact share quantities;
- Do not prescribe exact portfolio percentages;
- Do not prescribe derivatives;
- Do not invent mechanical stop-loss rules.

You may give qualitative timing guidance such as:

- Add only after specified evidence;
- Wait for confirmation;
- Hold while a thesis condition remains valid;
- Reduce if a fundamental assumption is invalidated.

Distinguish fundamental invalidation from ordinary price volatility.

Trigger classification. Put each event in exactly one list:
- forecast_revision_triggers: evidence challenges a forecast assumption or annual
  estimate;
- rating_review_triggers: a forecast or valuation change may alter expected return;
- thesis_invalidation_triggers: evidence contradicts the structural investment thesis.
Repeating company guidance belongs in forecast revision, not thesis invalidation.
A single quarterly threshold belongs in thesis invalidation only when it is material,
has no documented temporary explanation, and names the structural claim it contradicts
(thesis_link). For a customer-advances event, state the documented cause
(cancellation, deferral, renegotiation, loss_of_funding, applied_to_revenue or
construction_milestone) and whether committed capex continues; a decline with no cause
is not a negative trigger.

TARGET AND VALUATION

When the model states CALCULATION_STATUS=VERIFIED and
ASSUMPTION_STATUS=ANALYST_ASSUMPTION, say the valuation is mechanically verified
but the multiple is an analyst assumption. Never call that range or target simply
verified. The same rule applies to EV/EBITDA, P/B, DCF and scenario multiples.

{{valuation_sources_or_null}}

{{price_conflict_block}}
{{cross_method_block}}
{{component_status_block}}

Use a target range or rounded target consistent with forecast uncertainty.

When the verified model states a fair-value range, carry that range into the decision:
set fair_value_low, fair_value_base and fair_value_high, and set valuation_status to
"range". Do not write "not provided" for the price target while the model's range is in
front of you, and do not invent a point target the model does not support. Use
"point_target" only when you are choosing a point, and "unavailable" only when the run
had no verified valuation.

State:

- Forecast period;
- Earnings or cash-flow basis;
- Share basis;
- Valuation method;
- Principal assumptions;
- Material alternative case.

Do not change fundamental value to conform to the chart.

Do not create a target merely by averaging unrelated methods.

DECISION-HORIZON FOCUS

Give priority to catalysts and risks that can reasonably emerge within
{{decision_horizon_or_null}}.

Long-term optionality may support valuation, but must not substitute for evidence
relevant to the decision horizon.

{{resolution_block}}

OUTPUT

Open the report with the rating on its own line, exactly: **Rating**: <one of
the framework's values> — the runtime reads this line to record the decision.

Use this structure:

1. Executive conclusion
   - Rating;
   - Decision horizon;
   - Fundamental view;
   - Technical timing view;
   - Valuation range or target;
   - Main catalyst;
   - Main risk.

2. Investment thesis
   - No more than three core points.

3. What is already priced in

4. Forecast and variant perception
   - State clearly whether the forecast differs from guidance or consensus.

5. Valuation and cash flow

6. Technical timing
   - Present only the information relevant to the investment decision.

7. Catalysts within the decision horizon

8. Risks and invalidation conditions

9. Decision framework
   - Existing holders;
   - Potential new investors;
   - Conditions for becoming more positive;
   - Conditions for becoming more negative.

10. Summary table

Summary table:

| Item | Assessment |
|---|---|
| Rating | |
| Decision horizon | |
| Fundamental direction | |
| Forecast edge versus consensus | |
| Valuation | |
| Cash-flow quality | |
| Technical condition | |
| Main catalyst | |
| Main risk | |
| Positive trigger | |
| Invalidation condition | |

Do not include raw internal debate transcripts.

Do not repeat the same conclusion in multiple sections.

FINAL CHECK

Before responding, verify:

- Every material conclusion is linked to evidence;
- Guidance, consensus and independent forecast are distinguished;
- The model audit has been incorporated;
- The target is not reverse-engineered;
- Cash flow and capital intensity affect valuation where relevant;
- Technical analysis affects timing, not fundamental value;
- The conclusion matches the decision horizon;
- Existing-position advice does not assume missing portfolio information;
- No exact trade size, stop or hedge was invented;
- The report is concise enough to support an investment decision."""


def _feature(name: str) -> bool:
    return os.environ.get(f"TRADINGAGENTS_FEATURE_{name}", "").strip().lower() in {
        "1", "true", "yes", "on"
    }


_CONTROLLING_STATUS_KEYS = (
    "operating_model_status", "eps_model_status", "valuation_status",
)


def controlling_invalid(state: dict) -> bool:
    """Whether a controlling valuation component failed validation."""
    return any(
        str(state.get(key) or "").upper() == "INVALID"
        for key in _CONTROLLING_STATUS_KEYS
    )


def _component_status_note(state: dict) -> str:
    """The shared component status, rendered for the PM prompt."""
    try:
        component = json.loads(str(state.get("component_status") or "{}"))
    except ValueError:
        return ""
    if not isinstance(component, dict) or not component:
        return ""
    lines = ["COMPONENT STATUS"]
    method = (component.get("ev_ebitda_method") or {}).get("status")
    comparison = component.get("ev_per_share_comparison") or {}
    if method:
        lines.append(f"- EV/EBITDA enterprise-value method: {method}")
    if comparison.get("status"):
        lines.append(
            f"- EV/EBITDA per-share comparison: {comparison['status']}"
            + (f" ({comparison['reason']})" if comparison.get("reason") else "")
        )
    lines.append("- Controlling valuation use: pe only; EV/EBITDA is a cross-check")
    sensitivity = component.get("sensitivity") or {}
    controlling_sensitivity = component.get("controlling_sensitivity") or {}
    if sensitivity.get("failed_rows"):
        lines.append(
            f"- Sensitivity: {sensitivity.get('status')} — "
            f"{sensitivity.get('failed_rows')} failed rows excluded from decision evidence"
        )
    if controlling_sensitivity.get("status"):
        lines.append(
            f"- Controlling sensitivities: {controlling_sensitivity['status']}, "
            f"{controlling_sensitivity.get('verified', 0)} of "
            f"{controlling_sensitivity.get('expected', 0)} verified"
        )
    return "\n".join(lines)


def _cross_method_note(state: dict) -> str:
    """The share-basis mismatch between EV/EBITDA and P/E, when present."""
    try:
        diagnostics = json.loads(str(state.get("ev_input_diagnostics") or "{}"))
    except ValueError:
        return ""
    if not isinstance(diagnostics, dict):
        return ""
    return str(diagnostics.get("cross_method_basis") or "")


def _technical_block(state: dict) -> str:
    """The verified technical items, or an explicit absence.

    The PM may cite only what is printed here; an item the analyst did not
    retrieve is absent rather than restated from prose.
    """
    from tradingagents.research import technical

    raw = state.get("technical_evidence")
    if not raw:
        return "No verified technical evidence was produced in this run."
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        data = []
    items = [
        technical.TechnicalEvidence.from_dict(entry)
        for entry in data
        if isinstance(entry, dict)
    ]
    if not any(item.verification_status == technical.VERIFIED for item in items):
        return "No verified technical evidence was produced in this run."
    return technical.render(items)


_TECH_LEVEL_IDS = {
    "10": ("tech_close_below_10ema", "tech_close_above_10ema"),
    "50": ("tech_close_below_50sma", "tech_close_above_50sma"),
    "200": ("tech_close_below_200sma", "tech_close_above_200sma"),
}
def _level_pattern(term: str) -> re.Pattern[str]:
    return re.compile(
        r"\b" + re.escape(term)
        + r"\s*(?:-?\s*day)?\s*(?:sma|ema)\b[^\d]{0,24}([\d,]+(?:\.\d+)?)",
        re.IGNORECASE,
    )


def _verified_levels(state: dict) -> dict[str, float]:
    """The moving-average levels printed in the verified technical evidence."""
    from tradingagents.research import technical

    raw = state.get("technical_evidence")
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    levels: dict[str, float] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("verification_status") or "") != technical.VERIFIED:
            continue
        for term, ids in _TECH_LEVEL_IDS.items():
            if str(entry.get("evidence_id") or "") in ids:
                value = entry.get("comparison_value")
                if isinstance(value, (int, float)):
                    levels[term] = float(value)
    return levels


def _technical_mismatches(text: str, state: dict) -> list[str]:
    """PM levels that disagree with the verified evidence for the same indicator.

    Only a number attached to a named moving average is checked; a level the PM
    calculates (a gap midpoint, say) is not an indicator claim.
    """
    findings: list[str] = []
    for term, value in _verified_levels(state).items():
        for match in _level_pattern(term).finditer(text or ""):
            try:
                stated = float(match.group(1).replace(",", ""))
            except ValueError:
                continue
            if abs(stated - value) > max(1.0, abs(value) * 0.005):
                findings.append(
                    f"the {term}-period level {stated:,.0f} disagrees with the verified "
                    f"{value:,.0f}"
                )
    return findings


def _packet_block(state: dict, kind: str) -> str:
    """The purpose-specific model packet, never the undifferentiated package."""
    from tradingagents.research import packets

    if kind == "fundamental":
        return packets.fundamental_packet(state)
    return packets.valuation_packet(state)


def _verified_technical_ids(state: dict) -> set[str]:
    from tradingagents.research import technical

    raw = state.get("technical_evidence")
    if not raw:
        return set()
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return set()
    return {
        str(entry.get("evidence_id") or "")
        for entry in data
        if isinstance(entry, dict)
        and str(entry.get("verification_status") or "") == technical.VERIFIED
    }


def _fill(state: dict, ticker: str, analysis_date: str) -> str:
    from tradingagents.research import packets

    caps = _PROMPT_CAPS
    package = _clip(str(state.get("research_package") or "").strip(), caps["package"])
    debate = state.get("investment_debate_state") or {}
    risk = state.get("risk_debate_state") or {}
    portfolio_text = _clip(str(state.get("portfolio_context") or ""), caps["portfolio"])
    position, average_cost = position_fields(portfolio_text)
    # Old checkpoints predate the reviewer: the stored debate history is the
    # review in that state, so the synthesis still has an adversarial input.
    review = _clip(
        strip_case_json(
            str(debate.get("challenge_history") or debate.get("history") or "")
        ).strip(),
        caps["review"],
    )
    risk_team = _clip(strip_case_json(str(risk.get("history") or "")).strip(), caps["risk"])
    cases = case_block(state).strip()
    lessons = _clip(str(state.get("past_context") or "").strip(), caps["lessons"])
    values = {
        "instrument_ticker": ticker or "null",
        "analysis_date": analysis_date or "null",
        "evidence_pack_or_null": evidence_pack(ticker, analysis_date),
        "fundamental_analysis_or_null": _clip(
            fundamentals_with_repair_notice(state), caps["fundamentals"]
        ),
        # The package plus the marker rule: without it the synthesizer's quotes
        # of model figures arrive unmarked and the number audit cannot see them.
        "model_audit_or_null": (
            package + "\n" + FIGURE_MARKER_RULE if package
            else "null — no verified model was built for this run."
        ),
        "fundamental_packet_or_null": _packet_block(state, "fundamental"),
        "valuation_packet_or_null": _packet_block(state, "valuation"),
        # The technical packet carries price and verified evidence only; it may
        # not carry net income, EPS, shares or valuation output.
        "technical_analysis_or_null": (
            packets.technical_packet(state).strip()
            or report_or_absent(state.get("market_report"), "market")
        ),
        "technical_evidence_or_null": _technical_block(state),
        "adversarial_review_or_null": review or "null — no adversarial review in this run.",
        "decision_horizon_or_null": str(state.get("decision_horizon") or "").strip() or "null",
        "current_position_or_null": position,
        "average_cost_or_null": average_cost,
        "portfolio_weight_or_null": "null",   # no runtime source supplies a weight
        "mandate_constraints_or_null": "null",
        "validated_trading_rules_or_null": "null",
        "instrument_context_or_null": str(state.get("instrument_context") or "").strip() or "null",
        "portfolio_block_or_null": portfolio_text.strip() or "null",
        "research_plan_or_null": _clip(
            str(state.get("investment_plan") or "").strip(), caps["plan"]
        ) or "null",
        "trader_proposal_or_null": _clip(
            str(state.get("trader_investment_plan") or "").strip(), caps["trader"]
        ) or "null",
        "risk_team_or_null": risk_team or "null",
        "cases_or_null": cases or "null",
        # The lessons section exists only when there are lessons: an empty one
        # is omitted entirely rather than shown as a null line.
        "past_lessons_line": (
            f"- Lessons from prior decisions and outcomes:\n{lessons}" if lessons else ""
        ),
        # The runtime's framework: the 5-tier scale the structured decision, the
        # memory log and the settlement layer all read. The opening **Rating**:
        # line is what the free-text parser requires.
        "rating_framework_or_null": _rating_framework(),
        "valuation_sources_or_null": _valuation_sources(state),
        "price_conflict_block": (
            "PRICE CONFLICT\n"
            f"{state.get('price_conflict')}\n"
            "Name the controlling price once and explain why it controls. Do not "
            "present the other price as controlling."
            if str(state.get("price_conflict") or "").strip()
            else ""
        ),
        "component_status_block": _component_status_note(state),
        "resolution_block": (
            "Also fill the resolution fields: controlling_price (the price that controls), "
            "controlling_valuation (P/E band or EV cross-check), entry_condition (the observable "
            "trigger, including its price when stated), and resolution (why the action makes "
            "sense if the entry is above base fair value)."
            if _feature("PM_RESOLUTION_V1")
            else ""
        ),
        "cross_method_block": (
            "CROSS-METHOD BASIS\n"
            f"{_cross_method_note(state)}\n"
            "The EV/EBITDA per-share figure is a cross-check, not a controlling "
            "valuation. Do not use it to set the rating or fair value."
            if _cross_method_note(state)
            else ""
        ),
    }
    return fill_placeholders(TEMPLATE, values)


def _package_fair_value_range(state: dict) -> tuple[float | None, float | None]:
    """The verified model's fair-value range, from state or the printed package."""
    low = state.get("research_fair_value_low")
    high = state.get("research_fair_value_high")
    try:
        if low is not None and high is not None:
            return float(low), float(high)
    except (TypeError, ValueError):
        pass
    from tradingagents.research.report import fair_value_range_from_summary

    return fair_value_range_from_summary(str(state.get("research_package") or ""))


def _model_band_source_id(state: dict, method: str = "pe") -> str:
    """A source id that names both the valuation method and the active model version.

    ``model_band`` alone cannot be audited: six months later nobody can tell
    which method or which spec produced the number.
    """
    version = str(state.get("active_model_version") or "v1").strip() or "v1"
    return f"{method}_model_band_{version}"


def _valuation_sources(state: dict) -> str:
    """The only controlling sources a published bound may cite."""
    low = state.get("research_fair_value_low")
    high = state.get("research_fair_value_high")
    version = state.get("active_model_version") or "v1"
    if low is None or high is None:
        return (
            "No verified model band is available. Set valuation_status to unavailable and "
            "leave the fair-value fields empty; do not invent a controlling range."
        )
    if not bool(state.get("research_fair_value_verified", True)):
        return (
            f"The model band (low {float(low):,.0f}, high {float(high):,.0f}) is INDICATIVE and "
            "NON-CONTROLLING because the per-share basis is not verified. Set valuation_status "
            "to unavailable and leave the fair-value and source fields empty."
        )
    band_id = _model_band_source_id(state or {})
    return (
        f"Active model {version}. The only valid controlling sources are:\n"
        f"- {band_id}: low {float(low):,.0f}, high {float(high):,.0f}\n"
        "- override: set valuation_override with source_model_value, adjusted_value, "
        "adjustment_type, adjustment_amount, rationale and evidence_refs; the adjusted "
        "value must reproduce from those inputs.\n"
        "Set controlling_low_source_id, controlling_base_source_id and "
        "controlling_high_source_id to one of them. A generic id such as model_band is "
        "not a valid source. List every scenario you rejected in "
        "rejected_scenarios; a rejected scenario cannot supply a bound."
    )


_SCENARIO_LINE = re.compile(r"(?m)^(Scenarios[^:]*:)\s*(.+)$")
_SCENARIO_ENTRY = re.compile(r"([A-Za-z][A-Za-z ]*?)\s+([\d,]+)\s*\(([+-]?[\d.]+)%\)")
_PRICE_IN_TEXT = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d{4,}(?:\.\d+)?\b")


def _scenario_entries(line: str) -> list[str]:
    return [match.group(0) for match in _SCENARIO_ENTRY.finditer(line or "")]


def _filter_scenario_text(text: str, rejected_names: set[str]) -> str:
    """Remove rejected scenario entries from a published scenario line."""
    match = _SCENARIO_LINE.search(text or "")
    if not match:
        return text
    kept = []
    for raw in _scenario_entries(match.group(2)):
        entry = _SCENARIO_ENTRY.match(raw)
        if entry and entry.group(1).strip().lower() in rejected_names:
            continue
        kept.append(raw)
    replacement = (
        f"{match.group(1)} " + ("; ".join(kept) if kept
                               else "none verified; the controlling fair-value band applies.")
    )
    return text[: match.start()] + replacement + text[match.end():]


def _drop_sentences(text: str, tokens: set[str]) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text or "")
    return " ".join(s for s in sentences if s.strip() and not any(t in s for t in tokens)).strip()


def _enforce_rejected_outputs(decision, state) -> tuple[str, str, str]:
    """Remove rejected scenario targets from verified text and the summary.

    Returns (note, package, artifact). The appendix keeps the rejected values;
    the investor-facing sections keep only the verified band.
    """
    package = str(state.get("research_package") or "")
    artifact = str(state.get("research_package_artifact") or "")
    line = _SCENARIO_LINE.search(package)
    if line is None or not decision.rejected_scenarios:
        return "", package, artifact
    rejected_text = " ".join(str(item) for item in decision.rejected_scenarios).lower()
    rejected: list[tuple[str, str, str]] = []
    for raw in _scenario_entries(line.group(2)):
        entry = _SCENARIO_ENTRY.match(raw)
        if not entry:
            continue
        name, target, pct = entry.group(1).strip(), entry.group(2), entry.group(3)
        target_forms = {target, target.replace(",", "")}
        if (
            any(form in rejected_text for form in target_forms)
            or f"{pct}%" in rejected_text
            or re.search(
                rf"\b{re.escape(name.lower())}\b[^.;\n]{{0,24}}{re.escape(target)}",
                rejected_text,
            )
        ):
            rejected.append((name, target, pct))
    if not rejected:
        return "", package, artifact
    names = {name.lower() for name, _target, _pct in rejected}
    tokens: set[str] = set()
    for _name, target, pct in rejected:
        tokens.update({
            target, target.replace(",", ""), f"{pct}%", pct,
            f"{pct.lstrip('+-')}%",
        })

    package = _filter_scenario_text(package, names)
    artifact = _filter_scenario_text(artifact, names)
    for attribute in ("executive_summary", "investment_thesis"):
        value = getattr(decision, attribute, "") or ""
        setattr(decision, attribute, _drop_sentences(value, tokens))
    if not str(decision.executive_summary or "").strip():
        decision.executive_summary = (
            "See the verified fair-value band; rejected scenario outputs are in the appendix."
        )
    for attribute in ("upgrade_evidence", "downgrade_evidence", "thesis_invalidation"):
        values = getattr(decision, attribute, None)
        if isinstance(values, list):
            setattr(decision, attribute, [
                item for item in values if not any(token in str(item) for token in tokens)
            ])

    price = getattr(decision, "controlling_price", None) or state.get("research_price")
    low, base, high = (
        decision.fair_value_low, decision.fair_value_base, decision.fair_value_high
    )
    corrected = ""
    if low is not None and high is not None:
        parts = [f"Verified band {low:,.0f}\u2013{high:,.0f}"]
        if base is not None:
            parts.append(f"base {base:,.0f}")
        if price:
            parts.append(
                f"band floor {low / price - 1:+.1%}, band high {high / price - 1:+.1%} "
                f"vs {price:,.0f}"
            )
        corrected = "; ".join(parts) + "."
        summary = str(decision.executive_summary or "").strip()
        if "Verified band" not in summary:
            decision.executive_summary = (summary + " " + corrected).strip()
        if price and isinstance(getattr(decision, "upgrade_evidence", None), list):
            entry = f"Verified band high {high:,.0f} ({high / price - 1:+.1%} vs {price:,.0f})"
            if entry not in decision.upgrade_evidence:
                decision.upgrade_evidence.append(entry)
        if price and isinstance(getattr(decision, "downgrade_evidence", None), list):
            entry = f"Verified band floor {low:,.0f} ({low / price - 1:+.1%} vs {price:,.0f})"
            if entry not in decision.downgrade_evidence:
                decision.downgrade_evidence.append(entry)

    note = (
        "\n\n**Rejected outputs**: "
        + "; ".join(f"{name} {target} ({pct}%)" for name, target, pct in rejected)
        + " are not decision evidence; they remain in the appendix with their status."
    )
    if low is not None and high is not None:
        centre = f" (base {base:,.0f})" if base is not None else ""
        note += f" Verified band: {low:,.0f}\u2013{high:,.0f}{centre}."
    return note, package, artifact


def _technical_trigger_gate(decision) -> str:
    """A technical trigger above base fair value may not start new capital."""
    base = decision.fair_value_base
    text = str(decision.entry_condition or "")
    if not base or base <= 0 or not text:
        return ""
    prices = [float(value.replace(",", "")) for value in _PRICE_IN_TEXT.findall(text)]
    if not prices:
        return ""
    trigger = max(prices)
    if trigger <= 0:
        return ""
    remaining = base / trigger - 1
    if remaining > 0:
        return ""
    note = (
        f"At the {trigger:,.0f} technical trigger, remaining fundamental return is "
        f"{remaining:.1%} against base fair value {base:,.0f}: confirmation changes timing "
        "only and does not authorise new capital unless new evidence raises fair value."
    )
    decision.entry_condition = (text.rstrip() + " " + note).strip()
    if decision.new_investor_action == NewInvestorAction.INITIATE:
        decision.new_investor_action = NewInvestorAction.WAIT
    return note


def _matches(value: float | None, target: float | None) -> bool:
    if value is None or target is None:
        return False
    # 0.5% rounding tolerance, with a cent-level floor for small figures.
    return abs(value - target) <= max(0.01, abs(target) * 0.005)


def _range_error(decision, low, high, state: dict | None = None) -> str | None:
    """Why the decision's controlling range is not traceable, or None when it is."""
    if low is None or high is None:
        return None     # no verified band to trace to; the unavailable path clears it
    if decision.fair_value_low is None and decision.fair_value_high is None:
        return None
    if decision.fair_value_low is None or decision.fair_value_high is None:
        return "both ends of the controlling range are required"
    if decision.fair_value_low > decision.fair_value_high:
        return "the controlling low is above the controlling high"
    base = decision.fair_value_base
    if base is not None and not (decision.fair_value_low <= base <= decision.fair_value_high):
        return "the controlling base is outside the controlling range"

    ids = {
        "low": decision.controlling_low_source_id,
        "base": decision.controlling_base_source_id,
        "high": decision.controlling_high_source_id,
    }
    values = {
        "low": decision.fair_value_low,
        "base": decision.fair_value_base,
        "high": decision.fair_value_high,
    }
    if not any(ids.values()):
        # A range identical to the verified model band is traceable to it.
        if _matches(decision.fair_value_low, low) and _matches(decision.fair_value_high, high):
            band_id = _model_band_source_id(state or {})
            decision.controlling_low_source_id = band_id
            decision.controlling_high_source_id = band_id
            if base is not None:
                decision.controlling_base_source_id = band_id
            return None
        return (
            "the controlling range cites no source and does not match the verified model "
            "band"
        )
    rejected = {str(item) for item in decision.rejected_scenarios}
    band_id = _model_band_source_id(state or {})
    for label, source in ids.items():
        value = values[label]
        if value is None:
            continue
        if not source:
            return f"the {label} bound has no source id"
        if source in rejected:
            return f"the {label} bound cites the rejected scenario {source!r}"
        if source == band_id:
            target = low if label == "low" else high if label == "high" else None
            if target is not None and not _matches(value, target):
                return f"the {label} bound does not match the verified model band"
        elif source == "override":
            override = decision.valuation_override or {}
            try:
                recomputed = float(override["source_model_value"]) + float(
                    override["adjustment_amount"]
                )
            except (KeyError, TypeError, ValueError):
                return "the valuation override is missing source_model_value or adjustment_amount"
            if not _matches(value, recomputed):
                return f"the {label} override does not reproduce from its stated inputs"
        else:
            return f"the {label} source {source!r} is not a known valuation source"
    return None


def _rating_framework() -> str:
    """The scale depends on whether the caller supplied a reference portfolio.

    Overweight and Underweight are relative to a benchmark; with none, publishing
    them is a claim the run cannot support. Buy/Hold/Sell are absolute positions.
    """
    source_rule = (
        " State whether any beat/miss, upgrade/downgrade or catalyst benchmark is "
        "company-issued guidance or an internal forecast; the model's own forecast is "
        "never company guidance."
    )
    if str(get_config().get("benchmark_ticker") or "").strip():
        return (
            "Buy / Overweight / Hold / Underweight / Sell. Open the report with the "
            "rating on its own line, exactly: **Rating**: <one of the five> — the "
            "runtime reads this line to record the decision." + source_rule
        )
    return (
        "No benchmark or reference portfolio was supplied, so Overweight and Underweight "
        "are not defined. Use Buy / Hold / Sell, and state the absolute view (Positive / "
        "Neutral / Negative) separately. Open the report with the rating on its own line, "
        "exactly: **Rating**: <Buy|Hold|Sell> — the runtime reads this line." + source_rule
    )


EVENT_BASES = {
    "merger",
    "delisting",
    "bankruptcy",
    "insolvency",
    "accounting_breach",
    "trading_suspension",
}


def _decision_status(
    state: dict,
    *,
    structured: bool,
    valuation_verified: bool,
    audit_passed: bool,
) -> str:
    """FINAL only when every controlling condition holds; otherwise PROVISIONAL.

    A diagnostic run is not a production decision even when every check passes,
    and a free-text PM decision is not a decision the runtime may stamp FINAL.
    """
    if not structured:
        return ""
    purpose = str(
        state.get("run_purpose") or get_config().get("run_purpose") or "production"
    ).strip().lower()
    model_ok = str(state.get("model_status") or "VERIFIED").upper() == "VERIFIED"
    blocking = bool(state.get("unresolved_blocking") or state.get("blocking_issue"))
    if (
        purpose != "production"
        or not valuation_verified
        or not audit_passed
        or not model_ok
        or blocking
        or str(state.get("eps_basis_quality") or "") == "conservative_full_conversion"
    ):
        return "PROVISIONAL"
    return "FINAL"


def _valid_non_model_basis(state: dict) -> bool:
    """Whether a deterministic, event-driven basis permits a directional call.

    Restricted to corporate events: technical analysis, price action or an
    analyst's own valuation never override an unverified model.
    """
    basis = state.get("non_model_decision_basis") or {}
    return (
        isinstance(basis, dict)
        and str(basis.get("basis_type") or "").lower() in EVENT_BASES
        and basis.get("deterministic_validation_passed") is True
        and basis.get("depends_on_model_figures") is False
    )


def _invalidated_decision(state: dict, violations: list[str]) -> dict:
    """No controlling decision is published after a repeated reconstruction.

    A sentence scrub would leave the rest of a decision built on a suppressed
    method. The deterministic Not Rated decision replaces it instead.
    """
    text = _provisional_decision(state) + (
        "\n\n**Decision invalidated**: the Portfolio Manager reconstructed the "
        "suppressed P/E after one retry ("
        + "; ".join(violations)
        + "). No controlling rating is published for this run."
    )
    return {
        "final_trade_decision": text,
        "final_rating": RATING_REVIEW,
        "controlling_fair_value_low": None,
        "controlling_fair_value_base": None,
        "controlling_fair_value_high": None,
        "action_status": "invalidated",
        "decision_status": "PROVISIONAL",
        "decision_findings": json.dumps(
            [{"code": "PM_RECONSTRUCTED_SUPPRESSED_PE", "detail": "; ".join(violations)}]
        ),
        "decision_audit_result": json.dumps(
            {
                "validation_passed": False,
                "failure_reasons": [
                    "the decision reconstructed a suppressed P/E after one retry"
                ]
                + violations,
            }
        ),
    }


def _provisional_decision(state: dict) -> str:
    """The deterministic Not Rated decision an unverified model requires."""
    reason = str(state.get("spec_compatibility_reason") or "").strip()
    horizon = str(state.get("decision_horizon") or "").strip() or "not provided"
    why = f" ({reason})" if reason else ""
    return "\n".join([
        "**Rating**: Not Rated",
        "",
        "**Executive Summary**: Overall status: Not Rated. Decision status: Provisional. "
        f"The deterministic model did not verify{why}, so no model-based rating, price "
        "target or fair value is published. New investor: do not initiate pending model "
        "verification. Existing holder: review exposure pending model verification.",
        "",
        "**Investment Thesis**: The usable evidence is reported results, company guidance, "
        "verified consensus, verified technical data and qualitative risks. Unverified "
        "model forecasts and manual analyst valuation are non-controlling.",
        "",
        "**Price Target**: not provided",
        "",
        "**Valuation status**: unavailable",
        "",
        f"**Time Horizon**: {horizon}",
        "",
        "**Decision framework**:",
        "",
        "| Item | Assessment |",
        "|---|---|",
        "| Fundamental view | not provided |",
        "| Valuation | not provided |",
        "| Earnings revision | not provided |",
        "| Technical timing | not provided |",
        "| New investor | Do not initiate pending model verification |",
        "| Existing holder | Review exposure pending model verification |",
        "| Position size | not provided |",
        "| Confidence | Low |",
        "",
        "**Controlling fair value**: not provided",
    ])


ACTION_RETRY = (
    "\n\nACTION FIELDS MISSING: {fields}. The narrative already states these actions, but "
    "the schema fields are empty. Return the full decision again with new_investor_action, "
    "existing_holder_action and technical_timing filled from the narrative."
)


def _missing_actions(decision, text: str) -> list[str]:
    """Schema action fields the narrative states but the decision left empty."""
    lower = (text or "").lower()
    missing: list[str] = []
    if decision.new_investor_action is None and re.search(
        r"new investor|new money|do not initiate|initiate|avoid|wait", lower
    ):
        missing.append("new_investor_action")
    if decision.existing_holder_action is None and re.search(
        r"existing holder|existing position|holders? should|hold the core|trim|reduce", lower
    ):
        missing.append("existing_holder_action")
    if decision.technical_timing is None and re.search(
        r"technically|atr|50-day|200-day|10 ema|close below|close above|support", lower
    ):
        missing.append("technical_timing")
    return missing


def _action_from_decision(decision) -> str:
    """The transaction action the audit checks, from the typed holder/new fields."""
    if decision.existing_holder_action == HolderAction.EXIT:
        return "SELL"
    if (
        decision.existing_holder_action == HolderAction.REDUCE
        or decision.new_investor_action == NewInvestorAction.AVOID
    ):
        return "REDUCE"
    if (
        decision.new_investor_action == NewInvestorAction.INITIATE
        or decision.existing_holder_action == HolderAction.ADD
    ):
        return "BUY"
    return "HOLD"


def _normalize_actions(decision) -> list[str]:
    """Keep the published actions consistent with the rating; return what changed."""
    notes: list[str] = []
    rating = decision.rating.value
    if rating in {"Sell", "Underweight"}:
        if decision.new_investor_action not in (None, NewInvestorAction.AVOID):
            decision.new_investor_action = NewInvestorAction.AVOID
            notes.append("new-investor action set to Avoid for a Sell/Underweight")
        if decision.existing_holder_action not in (
            None, HolderAction.REDUCE, HolderAction.EXIT
        ):
            decision.existing_holder_action = HolderAction.REDUCE
            notes.append("existing-holder action set to Reduce for a Sell/Underweight")
    elif rating in {"Buy", "Overweight"}:
        if decision.new_investor_action not in (None, NewInvestorAction.INITIATE):
            decision.new_investor_action = NewInvestorAction.INITIATE
            notes.append("new-investor action set to Initiate for a Buy/Overweight")
    elif rating == "Hold" and decision.new_investor_action == NewInvestorAction.AVOID:
        decision.new_investor_action = NewInvestorAction.WAIT
        notes.append("new-investor action set to Wait for a Hold")
    return notes


def _align_rating_with_holder_action(decision) -> list[str]:
    """Keep the headline rating consistent with the holder action.

    A partial reduction is canonical Underweight (holder action Reduce); Sell is
    reserved for a full exit. The reverse also holds: an Exit cannot be published
    under a Buy/Overweight headline.
    """
    notes: list[str] = []
    if decision.existing_holder_action == HolderAction.EXIT:
        if decision.rating != PortfolioRating.SELL:
            decision.rating = PortfolioRating.SELL
            notes.append("rating set to Sell because the holder action is Exit")
        decision.downgrade_applicability = "ALREADY_AT_LOWEST_RATING"
        decision.evidence_to_downgrade = None
    elif decision.existing_holder_action == HolderAction.REDUCE:
        if decision.rating in {PortfolioRating.BUY, PortfolioRating.OVERWEIGHT}:
            decision.rating = PortfolioRating.UNDERWEIGHT
            notes.append("rating set to Underweight because the holder action is Reduce")
        if decision.downgrade_applicability != "APPLICABLE":
            decision.downgrade_applicability = "APPLICABLE"
        if not decision.evidence_to_downgrade:
            notes.append("Reduce without evidence that would trigger an Exit")
    return notes


def create_portfolio_manager(llm):
    structured_llm = bind_structured(llm, PortfolioDecision, "Portfolio Manager")
    minimal_llm = bind_structured(llm, PortfolioDecisionMinimal, "Portfolio Manager (minimal)")

    def portfolio_manager_node(state) -> dict:
        ticker = str(state.get("company_of_interest") or "").strip()
        analysis_date = str(state.get("trade_date") or "")
        valuation_verified = bool(state.get("research_fair_value_verified", True))
        audit_result: dict = {}
        decision_basis = ""
        technical_overlay = ""
        trigger_findings: list[str] = []

        if str(state.get("model_status") or "").upper() == "UNVERIFIED" and not _valid_non_model_basis(state):
            # No verified valuation and no audited non-model basis: the decision
            # is deterministic. An unverified model may not produce a rating.
            return {
                "final_trade_decision": _provisional_decision(state),
                "final_rating": RATING_REVIEW,
                "controlling_fair_value_low": None,
                "controlling_fair_value_base": None,
                "controlling_fair_value_high": None,
            }

        prompt = (
            _fill(state, ticker, analysis_date)
            + corrections_block(state)
            + bridge_arithmetic_block(state)
            + resolved_status_block(state)
            + source_facts_block(state)
            + model_facts_block(state)
            + NO_EXTERNAL_TOOLS
            + EVIDENCE_DISCIPLINE
            + case_availability(state)
            + get_language_instruction()
        )

        risk_debate_state = state["risk_debate_state"]

        # The typed rating is the decision; the rendered text only carries it.
        # Read back from text, a rating the thesis quotes could replace it.
        started = time.monotonic()
        logger.info("Portfolio Manager: structured decision started (%d chars)", len(prompt))
        status: dict = {}
        decision = invoke_structured(structured_llm, prompt, "Portfolio Manager", status=status)
        if decision is None:
            # One full-schema retry with the tool requirement restated, before
            # the decision is reduced to the minimal schema.
            logger.info("Portfolio Manager: full-schema retry")
            retry_status: dict = {}
            decision = invoke_structured(
                structured_llm, prompt + STRUCTURED_RETRY, "Portfolio Manager", status=retry_status
            )
            if decision is not None:
                status = retry_status
        if decision is None:
            # A decision-critical object is not rebuilt from prose: retry on the
            # minimal schema first, so the rating and summary stay typed.
            logger.info("Portfolio Manager: minimal-schema fallback")
            minimal_status: dict = {}
            minimal = invoke_structured(
                minimal_llm, prompt, "Portfolio Manager", status=minimal_status
            )
            if minimal is not None:
                decision = PortfolioDecision(
                    rating=minimal.rating,
                    executive_summary=minimal.executive_summary,
                    investment_thesis=minimal.investment_thesis or "",
                    price_target=minimal.target_price,
                    fair_value_low=minimal.fair_value_low,
                    fair_value_base=minimal.fair_value_base,
                    fair_value_high=minimal.fair_value_high,
                    valuation_status=minimal.valuation_status,
                )
                status = {"reason": "full schema failed; minimal schema used"}
            else:
                status = minimal_status or status

        if decision is not None:
            from tradingagents.research import packets

            if packets.pe_suppressed(state):
                violation = packets.reconstruction_findings(
                    f"{decision.executive_summary} {decision.investment_thesis}", state
                )
                if violation:
                    logger.info("Portfolio Manager: P/E reconstruction retry (%s)", violation)
                    retry_status = {}
                    retried = invoke_structured(
                        structured_llm,
                        prompt
                        + "\n\nRECONSTRUCTION VIOLATION: the P/E is suppressed in this "
                        "run, but your answer "
                        + "; ".join(violation)
                        + ". Do not state a P/E, market cap, EPS-derived value, price "
                        "target or fair-value range, and do not combine price with net "
                        "profit. Rewrite the decision; keep the evidence-based rating.",
                        "Portfolio Manager",
                        status=retry_status,
                    )
                    if retried is not None:
                        violation = packets.reconstruction_findings(
                            f"{retried.executive_summary} {retried.investment_thesis}",
                            state,
                        )
                        if not violation:
                            decision = retried
                        else:
                            return _invalidated_decision(state, violation)
                    else:
                        return _invalidated_decision(state, violation)

        if decision is not None:
            action_status = "controlling"
            # The runtime knows the horizon the run was given; a model that
            # left the optional field empty should not print "not provided"
            # beside an executive summary that states it.
            horizon = str(state.get("decision_horizon") or "").strip()
            if horizon and not decision.time_horizon:
                decision.time_horizon = horizon
            action_status = "controlling"
            action_note_from_retry = ""
            resolution_note = ""
            if _feature("PM_RESOLUTION_V1"):
                from tradingagents.agents.resolution import (
                    contradiction_detected,
                    entry_price,
                )

                entry = entry_price(decision.entry_condition or "")
                if contradiction_detected(entry, decision.fair_value_base):
                    decision.contradiction_detected = True
                    if not decision.resolution.strip():
                        resolution_note = (
                            "\n\n**Decision conflict**: the entry condition is above base "
                            "fair value and no resolution was supplied."
                        )
                        status["reason"] = (
                            (status.get("reason") or "") + "; entry above fair value"
                        ).strip("; ")
            technical_note = ""
            mismatches = _technical_mismatches(
                f"{decision.executive_summary} {decision.investment_thesis}", state
            )
            if mismatches:
                logger.info(
                    "Portfolio Manager: technical level retry (%s)", "; ".join(mismatches)
                )
                corrected = invoke_structured(
                    structured_llm,
                    prompt
                    + "\n\nTECHNICAL CHECK FAILED: "
                    + "; ".join(mismatches)
                    + ". Use only the levels printed in VERIFIED TECHNICAL EVIDENCE. A "
                    "level you calculate yourself must be marked [computed] with its "
                    "formula and the evidence ids it uses. Keep every other field.",
                    "Portfolio Manager",
                    status={},
                )
                if corrected is not None and not _technical_mismatches(
                    f"{corrected.executive_summary} {corrected.investment_thesis}", state
                ):
                    decision = corrected
                else:
                    # A claim the evidence contradicts does not control the
                    # decision; the technical leg becomes an advisory overlay.
                    technical_note = (
                        "\n\n**Technical note**: the technical levels stated above are not "
                        "confirmed by the verified evidence (" + "; ".join(mismatches)
                        + "); the technical overlay is advisory."
                    )
            missing_actions = _missing_actions(
                decision, f"{decision.executive_summary} {decision.investment_thesis}"
            )
            if missing_actions:
                logger.info(
                    "Portfolio Manager: action fields retry (%s)", ", ".join(missing_actions)
                )
                corrected = invoke_structured(
                    structured_llm,
                    prompt + ACTION_RETRY.format(fields=", ".join(missing_actions)),
                    "Portfolio Manager",
                    status={},
                )
                if corrected is not None and not _missing_actions(
                    corrected, f"{corrected.executive_summary} {corrected.investment_thesis}"
                ):
                    decision = corrected
                else:
                    action_status = "advisory"
                    action_note_from_retry = (
                        "\n\n**Action note**: the narrative's new-investor, existing-holder or "
                        "technical actions are advisory, not schema-validated."
                    )
            range_note = ""
            package_update = ""
            artifact_update = ""
            from tradingagents.research import decision_audit

            action = _action_from_decision(decision)
            audit_input = dict(decision.decision_audit or {})
            # A technical claim the analyst did not verify cannot enter the audit,
            # even when the PM marks it verified.
            verified_tech = _verified_technical_ids(state)
            evidence = []
            for item in audit_input.get("evidence") or []:
                if (
                    isinstance(item, dict)
                    and str(item.get("category") or "") == "technical"
                    and str(item.get("underlying_driver_id") or "") not in verified_tech
                ):
                    continue
                evidence.append(item)
            audit_input["evidence"] = evidence
            audit_input.setdefault("decision_basis", decision.decision_basis or "fundamental")
            audit_input.setdefault(
                "horizon", str(state.get("decision_horizon") or "").strip()
            )
            audit_result = decision_audit.assess(
                audit_input,
                action=action,
                valuation_verified=valuation_verified,
                sector=str(state.get("sector") or ""),
                config=get_config(),
            )
            audit_note = ""
            if not audit_result["validation_passed"]:
                normalized = audit_result["normalized_action"]
                if action in {"SELL", "REDUCE"} and normalized != action:
                    if normalized == "HOLD":
                        decision.rating = PortfolioRating.HOLD
                        decision.new_investor_action = NewInvestorAction.WAIT
                        decision.existing_holder_action = HolderAction.HOLD
                    else:
                        decision.new_investor_action = NewInvestorAction.AVOID
                        decision.existing_holder_action = HolderAction.REDUCE
                    decision.confidence = DecisionConfidence.LOW
                    decision.valuation_status = ValuationStatus.UNAVAILABLE
                    status["reason"] = (
                        (status.get("reason") or "") + "; non-valuation decision audit failed"
                    ).strip("; ")
                audit_note = (
                    "\n\n**Decision audit**: failed — "
                    + "; ".join(audit_result["failure_reasons"])
                    + ". The published action is capped accordingly."
                )
            elif audit_result["needs_non_valuation"]:
                audit_note = (
                    "\n\n**Decision audit**: passed — "
                    f"{audit_result['independent_category_count']} independent evidence "
                    f"clusters, {audit_result['independent_primary_source_count']} primary "
                    "disclosures."
                )
            decision_basis = str(
                audit_result.get("decision_basis")
                or audit_input.get("decision_basis")
                or "fundamental"
            )
            technical_overlay = str(audit_result.get("technical_overlay") or "")
            if technical_note:
                technical_overlay = "advisory"
            if technical_overlay:
                audit_note += (
                    "\n\n**Technical overlay**: advisory — the fundamental evidence "
                    "carries the rating; the technical leg did not verify and does not "
                    "control it."
                )
            if decision_basis == "technical":
                # A technical call is not a fundamental rating.
                decision.rating = PortfolioRating.HOLD
                decision.fundamental_view = None
                decision.valuation_view = None
                decision.confidence = DecisionConfidence.LOW
                audit_note += (
                    "\n\n**Fundamental rating**: Not Rated — this is a technical decision. "
                    "It may control timing and tactical exposure, not fundamental value."
                )
            if str(state.get("model_status") or "").upper() == "UNVERIFIED":
                # The exception path may still run the LLM, but it may not carry a
                # model valuation or a source id from analyst prose.
                decision.price_target = None
                decision.fair_value_low = None
                decision.fair_value_base = None
                decision.fair_value_high = None
                decision.valuation_status = ValuationStatus.UNAVAILABLE
                decision.controlling_low_source_id = None
                decision.controlling_base_source_id = None
                decision.controlling_high_source_id = None
            elif not valuation_verified:
                # The operating model verified but the per-share basis did not:
                # the band is indicative and may not control the decision.
                decision.price_target = None
                decision.fair_value_low = None
                decision.fair_value_base = None
                decision.fair_value_high = None
                decision.valuation_status = ValuationStatus.UNAVAILABLE
                decision.controlling_low_source_id = None
                decision.controlling_base_source_id = None
                decision.controlling_high_source_id = None
                decision.confidence = DecisionConfidence.LOW
                range_note = (
                    "\n\n**Controlling fair value**: not established — the per-share basis is "
                    "unverified, so the model band is indicative only."
                )
            if (
                valuation_verified
                and str(state.get("model_status") or "").upper() != "UNVERIFIED"
                and decision.price_target is None
                and decision.fair_value_low is None
            ):
                # The fallback lost the valuation the thesis used. The verified
                # model's range is in the package; carry it rather than "not provided".
                low, high = _package_fair_value_range(state)
                if low is not None and high is not None:
                    decision.fair_value_low = low
                    decision.fair_value_high = high
                    decision.valuation_status = ValuationStatus.RANGE
            if str(state.get("research_model_source") or "") == "unavailable":
                # A decision on an unverified valuation cannot carry high conviction,
                # and it has no verified value to state.
                decision.confidence = DecisionConfidence.LOW
                decision.price_target = None
                decision.fair_value_low = None
                decision.fair_value_base = None
                decision.fair_value_high = None
                decision.valuation_status = ValuationStatus.UNAVAILABLE
                decision.controlling_low_source_id = None
                decision.controlling_base_source_id = None
                decision.controlling_high_source_id = None
            range_error = _range_error(
                decision,
                state.get("research_fair_value_low"),
                state.get("research_fair_value_high"),
                state,
            )
            if range_error:
                # One corrective call, then publish no controlling value rather
                # than an untraceable one.
                logger.info("Portfolio Manager: range retry (%s)", range_error)
                retry_status: dict = {}
                corrected = invoke_structured(
                    structured_llm,
                    prompt
                    + "\n\nRANGE CHECK FAILED: "
                    + range_error
                    + ". Correct only the controlling fair value, its source ids and "
                    "rejected_scenarios; keep every other field.",
                    "Portfolio Manager",
                    status=retry_status,
                )
                if corrected is not None and not _range_error(
                    corrected,
                    state.get("research_fair_value_low"),
                    state.get("research_fair_value_high"),
                    state,
                ):
                    decision = corrected
                else:
                    decision.fair_value_low = None
                    decision.fair_value_base = None
                    decision.fair_value_high = None
                    decision.price_target = None
                    decision.valuation_status = ValuationStatus.UNAVAILABLE
                    decision.controlling_low_source_id = None
                    decision.controlling_base_source_id = None
                    decision.controlling_high_source_id = None
                    range_note = (
                        "\n\n**Controlling fair value**: not established — the proposed "
                        "range did not trace to the verified model or a reproducible override."
                    )
                    status["reason"] = (
                        (status.get("reason") or "")
                        + "; controlling range not traceable"
                    ).strip("; ")
            logger.info(
                "Portfolio Manager: decision ready in %.1fs (status=%s)",
                time.monotonic() - started,
                status.get("reason") or "schema",
            )
            # Legacy free-text invalidations migrate into the structured taxonomy;
            # they are never rendered as a separate legacy section.
            if decision.thesis_invalidation and not decision.thesis_invalidation_triggers:
                decision.thesis_invalidation_triggers = [
                    DecisionTrigger(
                        kind="THESIS_INVALIDATION_TRIGGER",
                        trigger_type="THESIS_INVALIDATION_TRIGGER",
                        description=str(item),
                        evidence=[TriggerEvidence(claim=str(item))],
                    )
                    for item in decision.thesis_invalidation
                    if str(item).strip()
                ]
            decision.thesis_invalidation = []
            action_notes = _normalize_actions(decision)
            action_notes += _align_rating_with_holder_action(decision)
            action_note = action_note_from_retry
            if action_notes:
                action_note += "\n\n**Action note**: " + "; ".join(action_notes) + "."
            rejected_note, package_update, artifact_update = _enforce_rejected_outputs(
                decision, state
            )
            _technical_trigger_gate(decision)
            trigger_findings: list[str] = []
            try:
                from tradingagents.research import (
                    report as research_report,
                    triggers as triggers_module,
                )

                spec = (
                    research_report.load_spec(state.get("research_model_path"))
                    if state.get("research_model_path") else None
                )
                (
                    decision.forecast_revision_triggers,
                    decision.rating_review_triggers,
                    decision.thesis_invalidation_triggers,
                    trigger_findings,
                ) = triggers_module.normalise(
                    decision.forecast_revision_triggers,
                    decision.rating_review_triggers,
                    decision.thesis_invalidation_triggers,
                    spec,
                )
            except Exception as exc:  # noqa: BLE001 - a trigger check must not lose the decision
                logger.info("Trigger classification skipped: %s", exc)
            if trigger_findings:
                action_note += (
                    "\n\n**Trigger classification**: " + "; ".join(trigger_findings)
                )
            final_trade_decision = (
                render_pm_decision(decision)
                + range_note
                + action_note
                + rejected_note
                + audit_note
                + technical_note
                + resolution_note
            )
            if (
                str(state.get("eps_status") or "VERIFIED") != "VERIFIED"
                and re.search(r"\bEPS\b|P/E|\d+(?:\.\d+)?x|fair value|per-share", final_trade_decision,
                              re.IGNORECASE)
            ):
                # The schema fields are suppressed, but the prose may still quote
                # the indicative per-share numbers; say they do not control.
                final_trade_decision += (
                    "\n\n**Per-share basis**: the EPS, P/E and fair-value figures above use an "
                    "unverified share basis. They are indicative and do not control the rating."
                )
            final_rating = decision.rating.value
            if decision_basis == "technical":
                final_rating = RATING_REVIEW
                final_trade_decision = final_trade_decision.replace(
                    f"**Rating**: {decision.rating.value}", "**Rating**: Not Rated", 1
                )
        else:
            final_trade_decision = llm.invoke(prompt).content
            final_rating = parse_rating(final_trade_decision)
            package_update = ""
            artifact_update = ""

        if not str(state.get("portfolio_context") or "").strip():
            final_trade_decision, _gated = gate_sizing(
                final_trade_decision, lambda p: llm.invoke(p).content, "Portfolio Manager"
            )
            if not _gated and has_exact_size(final_trade_decision):
                final_trade_decision = scrub_sizing(final_trade_decision)

        if not str(state.get("portfolio_context") or "").strip() or str(
            state.get("research_model_source") or ""
        ) == "unavailable":
            # No portfolio or no verified valuation: no exact execution levels.
            final_trade_decision = scrub_execution_levels(final_trade_decision)

        # A relative rating without a reference portfolio is not defined. The
        # prompt asks for Buy/Hold/Sell, but a model can still answer Underweight;
        # publish Hold and keep the direction as an explicit absolute view.
        benchmark = str(get_config().get("benchmark_ticker") or "").strip()
        if not benchmark and final_rating in {"Overweight", "Underweight"}:
            absolute = "Positive" if final_rating == "Overweight" else "Negative"
            final_trade_decision = re.sub(
                r"(?m)^\*\*Rating\*\*: .*$", "**Rating**: Hold", final_trade_decision, count=1
            )
            final_trade_decision += (
                f"\n\n**Absolute view**: {absolute}. The model's {final_rating} is a relative "
                "rating; no benchmark or mandate was supplied, so it is not published as one."
            )
            final_rating = "Hold"

        if _feature("COMPONENT_STATUS_V1") and controlling_invalid(state):
            # A failed controlling component cannot carry a directional call.
            final_rating = RATING_REVIEW
            final_trade_decision = (
                "**Rating**: Not Rated\n\n"
                "**Invalidation**: a controlling component failed validation, so no "
                "directional call is published for this run.\n\n"
                + final_trade_decision
            )

        new_risk_debate_state = {
            "history": risk_debate_state["history"],
            "aggressive_history": risk_debate_state["aggressive_history"],
            "conservative_history": risk_debate_state["conservative_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",
            "current_aggressive_response": risk_debate_state["current_aggressive_response"],
            "current_conservative_response": risk_debate_state["current_conservative_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],
        }

        result = {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": final_trade_decision,
            "final_rating": final_rating,
            "decision_model_version": str(state.get("active_model_version") or ""),
        }
        if package_update and package_update != str(state.get("research_package") or ""):
            result["research_package"] = package_update
            result["research_package_artifact"] = artifact_update
        if decision is not None:
            result["decision_triggers"] = json.dumps({
                "trigger_taxonomy_version": decision.trigger_taxonomy_version,
                "forecast_revision": [
                    trigger.model_dump() for trigger in decision.forecast_revision_triggers
                ],
                "rating_review": [
                    trigger.model_dump() for trigger in decision.rating_review_triggers
                ],
                "thesis_invalidation": [
                    trigger.model_dump() for trigger in decision.thesis_invalidation_triggers
                ],
            }, default=str)
            result["trigger_taxonomy_version"] = decision.trigger_taxonomy_version
        from tradingagents.research.report import decision_fair_value

        low, base, high = decision_fair_value(final_trade_decision)
        result["controlling_fair_value_low"] = low
        result["controlling_fair_value_base"] = base
        result["controlling_fair_value_high"] = high
        result["action_status"] = action_status if decision is not None else "advisory"
        result["decision_basis"] = decision_basis if decision is not None else ""
        result["technical_overlay"] = technical_overlay if decision is not None else ""
        result["decision_audit_result"] = (
            json.dumps(audit_result) if decision is not None else ""
        )
        result["decision_status"] = _decision_status(
            state,
            structured=decision is not None,
            valuation_verified=valuation_verified,
            audit_passed=bool(audit_result.get("validation_passed", True)),
        )
        if trigger_findings:
            result["decision_findings"] = json.dumps([
                {"code": "TRIGGER_RECLASSIFIED", "detail": detail}
                for detail in trigger_findings
            ])
        if status.get("reason"):
            result["structured_fallbacks"] = {"Portfolio Manager": status["reason"]}
        return result

    return portfolio_manager_node
