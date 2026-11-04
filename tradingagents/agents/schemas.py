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


class TraderAction(StrEnum):
    """3-tier transaction direction used by the Trader.

    The Trader's job is to translate the Research Manager's investment plan
    into a concrete transaction proposal: should the desk execute a Buy, a
    Sell, or sit on Hold this round.  Position sizing and the nuanced
    Overweight / Underweight calls happen later at the Portfolio Manager.
    """

    BUY = "Buy"
    HOLD = "Hold"
    SELL = "Sell"


class TraderIntent(StrEnum):
    """What a Sell means for the book.

    A Sell against an existing long is a reduction; a Sell with no position is
    either an avoidance or a new short, and a new short needs a mandate the
    system does not have. Naming the intent keeps the recommendation from
    silently becoming a short.
    """

    REDUCE_LONG = "reduce_long"
    INITIATE_SHORT = "initiate_short"
    HOLD = "hold"


# ---------------------------------------------------------------------------
# Research Manager
# ---------------------------------------------------------------------------


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


class ResearchPlanMinimal(BaseModel):
    """The fallback schema for the Research Manager: the call without the plan."""

    recommendation: PortfolioRating
    rationale: str


def render_research_plan(plan: ResearchPlan) -> str:
    """Render a ResearchPlan to markdown for storage and the trader's prompt context."""
    return "\n".join([
        f"**Recommendation**: {plan.recommendation.value}",
        "",
        f"**Rationale**: {plan.rationale}",
        "",
        f"**Strategic Actions**: {plan.strategic_actions}",
    ])


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------


class TraderProposal(BaseModel):
    """Structured transaction proposal produced by the Trader.

    The trader reads the Research Manager's investment plan and the analyst
    reports, then turns them into a concrete transaction: what action to
    take, the reasoning that justifies it, and the practical levels for
    entry, stop-loss, and sizing.
    """

    action: TraderAction = Field(
        description="The transaction direction. Exactly one of Buy / Hold / Sell.",
    )
    intent: TraderIntent | None = Field(
        default=None,
        description=(
            "What the action means for the book. With a Sell: 'reduce_long' when the "
            "book holds the name, 'initiate_short' only when a short mandate exists, "
            "otherwise leave empty (an avoidance). Omit for Buy and Hold."
        ),
    )
    reasoning: str = Field(
        description=(
            "The case for this action, anchored in the analysts' reports and "
            "the research plan. Two to four sentences."
        ),
    )
    entry_price: float | None = Field(
        default=None,
        description=(
            "Optional entry price target as an absolute number in the instrument's "
            "quote currency (e.g. 189.5), never a percentage or a range. Omit it "
            "if you cannot state a specific level."
        ),
    )
    stop_loss: float | None = Field(
        default=None,
        description=(
            "Optional stop-loss as an absolute price in the instrument's quote "
            "currency (e.g. 172.0), never a percentage. Convert a percentage "
            "distance to the price level it implies, or omit it."
        ),
    )
    position_sizing: str | None = Field(
        default=None,
        description="Optional sizing guidance, e.g. '5% of portfolio'.",
    )

    @field_validator("entry_price", "stop_loss", mode="before")
    @classmethod
    def _nullish_float_to_none(cls, v):
        return _coerce_optional_float(v)


def render_trader_proposal(proposal: TraderProposal) -> str:
    """Render a TraderProposal to markdown.

    The trailing ``FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**`` line is
    preserved for backward compatibility with the analyst stop-signal text
    and any external code that greps for it.

    A level above the entry is not a stop for a long position: it is the
    invalidation level that ends a bearish stance. Labelling it "Stop Loss"
    would describe a protection the trade does not have, so it is published as
    a reassessment level instead.
    """
    parts = [f"**Action**: {proposal.action.value}"]
    if proposal.intent is not None:
        parts.append(f"**Intent**: {proposal.intent.value.replace('_', ' ')}")
    parts.extend(["", f"**Reasoning**: {proposal.reasoning}"])

    stop_label, stop_note = "Stop Loss", ""
    if (
        proposal.stop_loss is not None
        and proposal.entry_price is not None
        and proposal.stop_loss > proposal.entry_price
    ):
        if proposal.action == TraderAction.BUY:
            stop_note = " (above the entry: not a protective stop for a long)"
        else:
            stop_label = "Reassessment Level"
            stop_note = " (above the entry: an invalidation level, not a protective stop)"

    # Named even when absent, so a reader can tell a level the trader chose not
    # to give from one the schema never asked for.
    for label, value, note in (
        ("Entry Price", proposal.entry_price, ""),
        (stop_label, proposal.stop_loss, stop_note),
        ("Position Sizing", proposal.position_sizing, ""),
    ):
        shown = value if value is not None and value != "" else "not provided"
        parts.extend(["", f"**{label}**: {shown}{note if shown != 'not provided' else ''}"])
    parts.extend([
        "",
        f"FINAL TRANSACTION PROPOSAL: **{proposal.action.value.upper()}**",
    ])
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
        ("Thesis invalidation", decision.thesis_invalidation),
        ("Evidence to upgrade", decision.upgrade_evidence),
        ("Evidence to downgrade", decision.downgrade_evidence),
    ):
        parts.extend(["", f"**{title}**: " + ("; ".join(items) if items else "not provided")])
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
