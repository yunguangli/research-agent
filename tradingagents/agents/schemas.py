"""Pydantic schemas used by agents that produce structured output.

The framework's primary artifact is still prose: each agent's natural-language
reasoning is what users read in the saved markdown reports and what the
downstream agents read as context.  Structured output is layered onto the
three decision-making agents (Research Manager, Trader, Portfolio Manager)
so that:

- Their outputs follow consistent section headers across runs and providers
- Each provider's native structured-output mode is used (json_schema for
  OpenAI/xAI, response_schema for Gemini, tool-use for Anthropic)
- Schema field descriptions become the model's output instructions, freeing
  the prompt body to focus on context and the rating-scale guidance
- A render helper turns the parsed Pydantic instance back into the same
  markdown shape the rest of the system already consumes, so display,
  memory log, and saved reports keep working unchanged
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# LLMs sometimes write a placeholder string ("None", "N/A", ...) into an optional
# numeric field instead of omitting it. Coerce those to None so the structured
# call validates instead of erroring (#1058). Pydantic still parses real numeric
# strings ("189.5") to float.
_NULLISH_FLOAT = {"", "none", "n/a", "na", "null", "nil", "-", "tbd", "unknown"}


def _coerce_optional_float(value):
    """Normalise an LLM-written optional numeric field before validation.

    Three shapes show up in practice: a placeholder string ("None", "N/A") in
    place of an omitted value (#1058); a percentage where a price was asked for
    ("15%", #1288); and a human-formatted price ("$1,234.50"). A percentage
    cannot be salvaged into an absolute level -- reading "15%" as 15 would put a
    stop at $15 on a $600 stock -- so it is dropped like a placeholder, leaving
    one bad field to null out instead of failing the whole proposal. A formatted
    price is reduced to its number.

    Anything that is not a single number is dropped the same way. A range
    ("150-160") or a hedge ("around 150") would otherwise reach pydantic, fail
    validation, and discard the whole decision, losing every field the model got
    right along with the price.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.lower() in _NULLISH_FLOAT or text.endswith("%"):
        return None
    cleaned = text.replace(",", "").lstrip("$€£¥").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Shared rating types
# ---------------------------------------------------------------------------


class PortfolioRating(StrEnum):
    """5-tier rating used by the Research Manager and Portfolio Manager."""

    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"


class FundamentalView(StrEnum):
    POSITIVE = "Positive"
    NEUTRAL = "Neutral"
    NEGATIVE = "Negative"


class ValuationView(StrEnum):
    ATTRACTIVE = "Attractive"
    FAIR = "Fair"
    FULL = "Full"


class TimingView(StrEnum):
    FAVORABLE = "Favorable"
    NEUTRAL = "Neutral"
    UNFAVORABLE = "Unfavorable"


class NewInvestorAction(StrEnum):
    INITIATE = "Initiate"
    WAIT = "Wait"
    AVOID = "Avoid"


class HolderAction(StrEnum):
    ADD = "Add"
    HOLD = "Hold"
    REVIEW = "Review"
    REDUCE = "Reduce"
    EXIT = "Exit"


class DecisionConfidence(StrEnum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class ValuationStatus(StrEnum):
    """How the decision expresses value: a point target, a range, or nothing."""

    POINT_TARGET = "point_target"
    RANGE = "range"
    UNAVAILABLE = "unavailable"


# ---------------------------------------------------------------------------
# Research Manager
# ---------------------------------------------------------------------------


class TriggerEvidence(BaseModel):
    """One claim inside a trigger, with the sources that support it."""

    claim: str = Field(description="The evidence claim, one complete sentence.")
    source_ids: list[str] = Field(
        default_factory=list, description="Document or data identifiers behind the claim."
    )


class DecisionTrigger(BaseModel):
    """One event that would change a forecast, the rating or the thesis.

    The separation is the point: repeating company guidance can revise a forecast
    without invalidating the business, and a single quarterly threshold is not a
    thesis break unless it is material, unexplained and linked to the thesis.
    """

    trigger_id: str = Field(default="", description="Stable identifier for this trigger.")
    trigger_type: Literal[
        "FORECAST_REVISION_TRIGGER",
        "RATING_REVIEW_TRIGGER",
        "THESIS_INVALIDATION_TRIGGER",
    ] = Field(default="FORECAST_REVISION_TRIGGER")
    # Legacy field; migrated into trigger_type when absent.
    kind: Literal[
        "FORECAST_REVISION_TRIGGER",
        "RATING_REVIEW_TRIGGER",
        "THESIS_INVALIDATION_TRIGGER",
        "",
    ] = Field(default="")
    description: str = Field(description="The event, one sentence.")
    evidence: list[TriggerEvidence] = Field(
        default_factory=list,
        description="Structured claims and source ids; never a punctuation-separated string.",
    )
    metric: str = Field(default="", description="revenue | operating_profit | net_profit | eps | segment_margin | advances | other")
    period: str = Field(default="", description="The fiscal period the metric belongs to.")
    threshold: float | None = Field(default=None, description="The numeric threshold, when stated.")
    direction: Literal["ABOVE", "BELOW", "CHANGE", "CAUSE_DEPENDENT", ""] = Field(default="")
    severity: Literal["warning", "material", "structural", "minor", ""] = Field(default="")
    materiality: Literal["high", "material", "minor", ""] = Field(default="")
    temporary_explanation: str = Field(
        default="",
        description="A credible temporary explanation already documented; empty when none exists.",
    )
    thesis_link: str = Field(
        default="",
        description="The structural thesis claim the event contradicts; required for thesis invalidation.",
    )
    cause: Literal[
        "",
        "cancellation",
        "deferral",
        "renegotiation",
        "loss_of_funding",
        "applied_to_revenue",
        "construction_milestone",
    ] = Field(default="", description="For an advances event: the documented cause.")
    committed_capex_continues: bool | None = Field(
        default=None,
        description="For an advances decline: whether committed capex still continues.",
    )

    @property
    def effective_type(self) -> str:
        return self.trigger_type or self.kind or "FORECAST_REVISION_TRIGGER"


class ResearchPlan(BaseModel):
    """Structured investment plan produced by the Research Manager.

    Hand-off to the Trader: the recommendation pins the directional view,
    the rationale captures which side of the bull/bear debate carried the
    argument, and the strategic actions translate that into concrete
    instructions the trader can execute against.
    """

    recommendation: PortfolioRating = Field(
        description=(
            "The investment recommendation. Exactly one of Buy / Overweight / "
            "Hold / Underweight / Sell. Conflicting arguments alone are not a "
            "reason to Hold: commit to the stronger side, sized by how "
            "decisively it wins. Choose Hold only when the evidence is still "
            "balanced after weighing, or too thin to support a call."
        ),
    )
    rationale: str = Field(
        description=(
            "Conversational summary of the key points from both sides of the "
            "debate, ending with which arguments led to the recommendation. "
            "Speak naturally, as if to a teammate."
        ),
    )
    strategic_actions: str = Field(
        description=(
            "Concrete steps for the trader to implement the recommendation, "
            "including sizing guidance relative to a standard allocation. The "
            "research team does not see the caller's holdings; the trader and "
            "portfolio manager apply the actual position."
        ),
    )
    forecast_revision_triggers: list[DecisionTrigger] = Field(default_factory=list)
    rating_review_triggers: list[DecisionTrigger] = Field(default_factory=list)
    thesis_invalidation_triggers: list[DecisionTrigger] = Field(default_factory=list)
    trigger_taxonomy_version: int = 2


class ResearchPlanMinimal(BaseModel):
    """The fallback schema for the Research Manager: the call without the plan."""

    recommendation: PortfolioRating
    rationale: str


def _render_triggers(items) -> str:
    lines: list[str] = []
    for item in items or []:
        description = getattr(item, "description", str(item))
        lines.append(f"- {description}")
        for evidence in getattr(item, "evidence", []) or []:
            claim = getattr(evidence, "claim", str(evidence))
            ids = ", ".join(getattr(evidence, "source_ids", []) or [])
            lines.append(f"  - {claim}" + (f" ({ids})" if ids else ""))
    return "\n".join(lines) if lines else "not provided"


def render_research_plan(plan: ResearchPlan) -> str:
    """Render a ResearchPlan to markdown for storage and the trader's prompt context."""
    return "\n".join([
        f"**Recommendation**: {plan.recommendation.value}",
        "",
        f"**Rationale**: {plan.rationale}",
        "",
        f"**Strategic Actions**: {plan.strategic_actions}",
        "",
        f"**Forecast revision triggers**: {_render_triggers(plan.forecast_revision_triggers)}",
        f"**Rating review triggers**: {_render_triggers(plan.rating_review_triggers)}",
        f"**Thesis invalidation**: {_render_triggers(plan.thesis_invalidation_triggers)}",
    ])


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------


class TraderProposal(BaseModel):
    """The Trader's stance and the conditions that would change it.

    Execution levels were removed on purpose: a model-authored stop price is not
    schema-validated risk control, and it invited the runtime to publish numbers
    nobody could check. The Trader now states a stance, the condition that
    triggers entry, the condition that invalidates it, the evidence it cites and
    its confidence. The Portfolio Manager owns sizing and execution.
    """

    new_investor_action: NewInvestorAction = Field(
        description="What a new investor should do: exactly one of Initiate / Wait / Avoid.",
    )
    holder_action: HolderAction = Field(
        description=(
            "What an existing holder should do: exactly one of Add / Hold / Review / "
            "Reduce / Exit. The Portfolio Manager owns the fundamental rating."
        ),
    )
    entry_condition: str = Field(
        description=(
            "The observable condition that would justify acting, e.g. 'a daily close "
            "above the 50-day average with volume expansion'. A level is a condition, "
            "not a promised fill."
        ),
    )
    deterioration_condition: str = Field(
        description=(
            "The observable condition that ends the stance, e.g. 'a close below the "
            "200-day average'. This is an invalidation, not a protective stop."
        ),
    )
    evidence_ids: list[str] = Field(
        default_factory=list,
        description=(
            "IDs of the evidence the stance relies on (verified technical evidence IDs "
            "and model fields). Do not invent an id."
        ),
    )
    confidence: DecisionConfidence = Field(
        default=DecisionConfidence.MEDIUM,
        description="High, Medium or Low, reflecting the strength of the cited evidence.",
    )


def _trader_signal(proposal: TraderProposal) -> str:
    """The legacy BUY/HOLD/SELL line, derived from the two action fields."""
    if proposal.holder_action == HolderAction.EXIT or (
        proposal.new_investor_action == NewInvestorAction.AVOID
        and proposal.holder_action == HolderAction.EXIT
    ):
        return "SELL"
    if proposal.holder_action == HolderAction.ADD or (
        proposal.new_investor_action == NewInvestorAction.INITIATE
        and proposal.holder_action != HolderAction.EXIT
    ):
        return "BUY"
    return "HOLD"


def render_trader_proposal(proposal: TraderProposal) -> str:
    """Render a TraderProposal to markdown."""
    evidence = ", ".join(proposal.evidence_ids) if proposal.evidence_ids else "not provided"
    parts = [
        f"**New Investor Action**: {proposal.new_investor_action.value}",
        "",
        f"**Holder Action**: {proposal.holder_action.value}",
        "",
        f"**Entry Condition**: {proposal.entry_condition}",
        "",
        f"**Deterioration Condition**: {proposal.deterioration_condition} "
        "(rating review trigger, not thesis invalidation)",
        "",
        f"**Evidence**: {evidence}",
        "",
        f"**Confidence**: {proposal.confidence.value}",
        "",
        f"FINAL TRANSACTION PROPOSAL: **{_trader_signal(proposal)}**",
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Portfolio Manager
# ---------------------------------------------------------------------------


class PortfolioDecision(BaseModel):
    """Structured output produced by the Portfolio Manager.

    The model fills every field as part of its primary LLM call; no separate
    extraction pass is required. Field descriptions double as the model's
    output instructions, so the prompt body only needs to convey context and
    the rating-scale guidance.
    """

    rating: PortfolioRating = Field(
        description=(
            "The final position rating. Exactly one of Buy / Overweight / Hold / "
            "Underweight / Sell, picked based on the analysts' debate. "
            "Conflicting arguments alone are not a reason to Hold: commit to the "
            "stronger side, sized by how decisively it wins. Choose Hold only "
            "when the evidence is still balanced after weighing, or too thin to "
            "support a call."
        ),
    )
    executive_summary: str = Field(
        description=(
            "A concise action plan covering entry strategy, position sizing, "
            "key risk levels, and time horizon. Two to four sentences."
        ),
    )
    investment_thesis: str = Field(
        description=(
            "Detailed reasoning anchored in specific evidence from the analysts' "
            "debate. If prior lessons are referenced in the prompt context, "
            "incorporate them; otherwise rely solely on the current analysis."
        ),
    )
    price_target: float | None = Field(
        default=None,
        description="Optional target price in the instrument's quote currency.",
    )
    # The verified model publishes a range, not always a point. A decision that
    # cannot pick a point still carries the value it used.
    fair_value_low: float | None = Field(
        default=None, description="Low end of the verified model's fair-value range."
    )
    fair_value_base: float | None = Field(
        default=None, description="Base (central) fair value, when the model states one."
    )
    fair_value_high: float | None = Field(
        default=None, description="High end of the verified model's fair-value range."
    )
    valuation_status: ValuationStatus | None = Field(
        default=None,
        description=(
            "point_target when price_target is set, range when a fair-value range is "
            "given, unavailable when the run had no verified valuation."
        ),
    )
    time_horizon: str | None = Field(
        default=None,
        description="Optional recommended holding period, e.g. '3-6 months'.",
    )
    # One rating cannot carry four different decisions. These typed fields keep
    # the fundamental view, valuation, timing and the two audiences separate.
    fundamental_view: FundamentalView | None = None
    valuation_view: ValuationView | None = None
    technical_timing: TimingView | None = None
    new_investor_action: NewInvestorAction | None = None
    existing_holder_action: HolderAction | None = None
    confidence: DecisionConfidence | None = None
    # The resolution the decision owes a reader, kept separate from the prose so
    # a missing judgement is visible as "not provided" rather than absent.
    earnings_revision_view: str | None = Field(
        default=None,
        description="Rising / Stable / Falling, or '' when no view is supported.",
    )
    catalyst_timing: str | None = Field(
        default=None,
        description="When the decisive catalyst is expected, within the decision horizon.",
    )
    position_size_constraint: str | None = Field(
        default=None,
        description="What caps the position (liquidity, ATR, mandate, concentration); never a size.",
    )
    thesis_invalidation: list[str] = Field(default_factory=list)
    forecast_revision_triggers: list[DecisionTrigger] = Field(default_factory=list)
    rating_review_triggers: list[DecisionTrigger] = Field(default_factory=list)
    thesis_invalidation_triggers: list[DecisionTrigger] = Field(default_factory=list)
    trigger_taxonomy_version: int = 2
    upgrade_evidence: list[str] = Field(default_factory=list)
    downgrade_evidence: list[str] = Field(default_factory=list)
    # Traceability: every published bound names the source it came from, and a
    # source that points at a rejected scenario is invalid.
    controlling_low_source_id: str | None = None
    controlling_base_source_id: str | None = None
    controlling_high_source_id: str | None = None
    controlling_model_version: str | None = None
    rejected_scenarios: list[str] = Field(default_factory=list)
    valuation_override: dict | None = None
    # What the action rests on: verified fundamentals, a discrete event, verified
    # technical evidence, or a mixture that names the controlling category.
    decision_basis: Literal["fundamental", "event", "technical", "mixed"] | None = None
    decision_audit: dict | None = None
    # Whether a further downgrade is still available. At the lowest rating
    # there is nothing below it, so no evidence-to-downgrade is required; a
    # Reduce must name the evidence that would trigger an Exit.
    downgrade_applicability: Literal[
        "APPLICABLE", "ALREADY_AT_LOWEST_RATING", "NOT_ASSESSED"
    ] = "NOT_ASSESSED"
    evidence_to_downgrade: list[str] | None = None
    # The resolution contract: which price and valuation control, what entry is
    # proposed, and how an entry above fair value is justified.
    controlling_price: float | None = None
    controlling_valuation: str = ""
    entry_condition: str = ""
    contradiction_detected: bool = False
    resolution: str = ""

    @field_validator("price_target", mode="before")
    @classmethod
    def _nullish_float_to_none(cls, v):
        return _coerce_optional_float(v)

    @field_validator("fair_value_low", "fair_value_base", "fair_value_high", mode="before")
    @classmethod
    def _nullish_range_to_none(cls, v):
        return _coerce_optional_float(v)


class PortfolioDecisionMinimal(BaseModel):
    """The fallback schema: the decision without the optional detail.

    Used when the full schema cannot be parsed. It is still a typed decision, so
    the run does not have to reconstruct a decision-critical object from prose.
    The fallback keeps the valuation output: a range may be given instead of a
    point target, so a decision built on a verified model does not lose it.
    """

    rating: PortfolioRating
    executive_summary: str
    investment_thesis: str = ""
    target_price: float | None = None
    fair_value_low: float | None = None
    fair_value_base: float | None = None
    fair_value_high: float | None = None
    valuation_status: ValuationStatus | None = None

    @field_validator(
        "target_price", "fair_value_low", "fair_value_base", "fair_value_high", mode="before"
    )
    @classmethod
    def _nullish_float_to_none(cls, v):
        return _coerce_optional_float(v)


def _fmt_value(value: float) -> str:
    """A price as a reader writes it: thousands separated, no trailing .0."""
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def render_pm_decision(decision: PortfolioDecision) -> str:
    """Render a PortfolioDecision back to the markdown shape the rest of the system expects.

    Memory log, CLI display, and saved report files all read this markdown,
    so the rendered output preserves the exact section headers (``**Rating**``,
    ``**Executive Summary**``, ``**Investment Thesis**``) that downstream
    parsers and the report writers already handle.
    """
    parts = [
        f"**Rating**: {decision.rating.value}",
        "",
        f"**Executive Summary**: {decision.executive_summary}",
        "",
        f"**Investment Thesis**: {decision.investment_thesis}",
    ]
    # Named even when absent: a missing line reads as a field nobody asked for,
    # so a reader cannot tell "no target" from "target not reported". A verified
    # model publishes a range, so a range is a complete answer, not a missing point.
    low, base, high = decision.fair_value_low, decision.fair_value_base, decision.fair_value_high
    if decision.price_target is not None:
        parts.extend(["", f"**Price Target**: {decision.price_target}"])
    elif low is not None and high is not None:
        centre = _fmt_value(base) if base is not None else "base not stated"
        parts.extend(
            ["", f"**Fair Value**: {_fmt_value(low)}–{_fmt_value(high)} (base {centre})"]
        )
    else:
        parts.extend(["", "**Price Target**: not provided"])
    status = decision.valuation_status
    if status is None:
        if decision.price_target is not None:
            status = ValuationStatus.POINT_TARGET
        elif low is not None and high is not None:
            status = ValuationStatus.RANGE
        else:
            status = ValuationStatus.UNAVAILABLE
    parts.extend(["", f"**Valuation status**: {status.value}"])
    parts.extend(["", f"**Time Horizon**: {decision.time_horizon or 'not provided'}"])
    if decision.controlling_price is not None:
        parts.extend(["", f"**Controlling price**: {decision.controlling_price:,.0f}"])
    if decision.controlling_valuation:
        parts.extend(["", f"**Controlling valuation**: {decision.controlling_valuation}"])
    if decision.entry_condition:
        parts.extend(["", f"**Entry condition**: {decision.entry_condition}"])
    if decision.contradiction_detected:
        parts.extend([
            "",
            "**Decision conflict**: "
            + (decision.resolution or "resolution not provided"),
        ])

    framework = [
        ("Fundamental view", decision.fundamental_view),
        ("Valuation", decision.valuation_view),
        ("Earnings revision", decision.earnings_revision_view),
        ("Technical timing", decision.technical_timing),
        ("New investor", decision.new_investor_action),
        ("Existing holder", decision.existing_holder_action),
    ]
    rows = [(label, value.value if hasattr(value, "value") else value) for label, value in framework]
    rows.append(("Position size", decision.position_size_constraint))
    if decision.confidence is not None:
        rows.append(("Confidence", decision.confidence.value))
    # Every row is printed: a decision that did not answer a question must show
    # the gap, not omit the question. The 13:51 fallback dropped the whole table.
    parts.extend(["", "**Decision framework**:", "", "| Item | Assessment |", "|---|---|"])
    parts.extend(f"| {label} | {value if value else 'not provided'} |" for label, value in rows)
    for title, items in (
        ("Forecast revision triggers", decision.forecast_revision_triggers),
        ("Rating review triggers", decision.rating_review_triggers),
        ("Thesis invalidation", decision.thesis_invalidation_triggers),
        ("Evidence to upgrade", decision.upgrade_evidence),
        ("Evidence to downgrade", decision.downgrade_evidence),
    ):
        if items and hasattr(items[0], "description"):
            parts.extend(["", f"**{title}**:", _render_triggers(items)])
        else:
            text = "; ".join(str(item) for item in items) if items else "not provided"
            parts.extend(["", f"**{title}**: {text}"])
    parts.extend(["", f"**Downgrade applicability**: {decision.downgrade_applicability}"])
    if decision.evidence_to_downgrade:
        parts.extend([
            "",
            "**Evidence that would downgrade further**: "
            + "; ".join(decision.evidence_to_downgrade),
        ])
    sources = {
        "low": decision.controlling_low_source_id,
        "base": decision.controlling_base_source_id,
        "high": decision.controlling_high_source_id,
    }
    if any(sources.values()):
        parts.extend(
            ["", "**Valuation sources**: " + "; ".join(
                f"{label} {source or 'not provided'}" for label, source in sources.items()
            )]
        )
    if decision.rejected_scenarios:
        parts.extend(["", "**Rejected scenarios**: " + ", ".join(decision.rejected_scenarios)])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Sentiment Analyst
# ---------------------------------------------------------------------------


class SentimentBand(StrEnum):
    """Discrete sentiment direction produced by the Sentiment Analyst.

    Six tiers keep the signal granular enough to be actionable while remaining
    small enough for every provider to map reliably from its JSON output.
    """

    BULLISH = "Bullish"
    MILDLY_BULLISH = "Mildly Bullish"
    NEUTRAL = "Neutral"
    MIXED = "Mixed"
    MILDLY_BEARISH = "Mildly Bearish"
    BEARISH = "Bearish"


class SentimentReport(BaseModel):
    """Structured sentiment report produced by the Sentiment Analyst.

    Replaces the previous free-form prose output so downstream consumers
    (dashboards, audit logs, PDF renderers, other agents) can read
    ``overall_band`` and ``overall_score`` without maintaining fragile regex
    fallbacks that drift with every model release. ``narrative`` preserves the
    rich source-by-source analysis; ``render_sentiment_report`` prepends a
    deterministic header so the saved report stays human-readable.
    """

    overall_band: SentimentBand = Field(
        description=(
            "Overall sentiment direction. Exactly one of: "
            "Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish. "
            "Use Mixed when sources point in clearly different directions. "
            "Use Neutral only when all sources are genuinely silent or non-committal."
        ),
    )
    overall_score: float = Field(
        ge=0.0,
        le=10.0,
        description=(
            "Numeric sentiment intensity on a 0–10 scale. "
            "0 = maximally bearish, 5 = neutral, 10 = maximally bullish. "
            "Guideline for consistency with overall_band: "
            "Bullish ~6.5–10, Mildly Bullish ~5.5–6.4, Neutral/Mixed ~4.5–5.5, "
            "Mildly Bearish ~3.5–4.4, Bearish ~0–3.4. "
            "Only the 0–10 bounds are enforced."
        ),
    )
    confidence: Literal["low", "medium", "high"] = Field(
        description=(
            "Confidence in the assessment based on data quality and sample size. "
            "Use 'low' when one or more sources returned a placeholder or fewer "
            "than 5 data points; 'medium' when data is present but sparse; "
            "'high' when all three sources returned substantive data."
        ),
    )
    narrative: str = Field(
        description=(
            "Full sentiment report covering, in order: "
            "(1) source-by-source breakdown with specific evidence (cite message "
            "counts, ratios, notable posts); "
            "(2) cross-source divergences and alignments; "
            "(3) dominant narrative themes; "
            "(4) catalysts and risks surfaced by the data; "
            "(5) a markdown table summarising key sentiment signals, their "
            "direction, source, and supporting evidence. "
            "Keep it informative and substantive: develop each section thoroughly "
            "with concrete evidence so every point adds new signal for the trader."
        ),
    )


def render_sentiment_report(report: SentimentReport) -> str:
    """Render a SentimentReport to the markdown shape the rest of the system expects.

    The structured header (band + score + confidence) is prepended to the
    narrative so the saved report is both human-readable and machine-parseable
    without regex.
    """
    return "\n".join([
        f"**Overall Sentiment:** **{report.overall_band.value}** "
        f"(Score: {report.overall_score:.1f}/10)",
        f"**Confidence:** {report.confidence.capitalize()}",
        "",
        report.narrative,
    ])
