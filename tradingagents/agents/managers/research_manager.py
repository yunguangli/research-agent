"""Research Manager: turns the adversarial review into a structured investment plan."""

from __future__ import annotations

import logging
import re

from tradingagents.agents.context import (
    EVIDENCE_DISCIPLINE,
    analyst_judgments_block,
    case_availability,
    case_block,
    get_instrument_context_from_state,
    get_language_instruction,
    model_facts_block,
    research_package_block,
    source_facts_block,
)
from tradingagents.agents.schemas import ResearchPlan, ResearchPlanMinimal, render_research_plan
from tradingagents.agents.sizing import gate_sizing, has_exact_size, scrub_sizing
from tradingagents.agents.structured import (
    NO_EXTERNAL_TOOLS,
    STRUCTURED_RETRY,
    bind_structured,
    invoke_structured,
)
from tradingagents.dataflows.config import get_config

logger = logging.getLogger(__name__)


def create_research_manager(llm):
    structured_llm = bind_structured(llm, ResearchPlan, "Research Manager")
    minimal_llm = bind_structured(llm, ResearchPlanMinimal, "Research Manager (minimal)")

    def research_manager_node(state) -> dict:
        instrument_context = get_instrument_context_from_state(state)
        model_block = research_package_block(state)
        case_line = case_block(state)
        history = state["investment_debate_state"].get("history", "")

        investment_debate_state = state["investment_debate_state"]

        benchmark = str(get_config().get("benchmark_ticker") or "").strip()
        if benchmark:
            scale = (
                "- **Buy**: Strong conviction in the bull thesis; recommend taking or growing the position\n"
                "- **Overweight**: Constructive view; recommend gradually increasing exposure\n"
                "- **Hold**: Balanced view; recommend maintaining the current position\n"
                "- **Underweight**: Cautious view; recommend trimming exposure\n"
                "- **Sell**: Strong conviction in the bear thesis; recommend exiting or avoiding the position"
            )
        else:
            scale = (
                "No benchmark or reference portfolio was supplied, so Overweight and Underweight "
                "are not defined. Use **Buy / Hold / Sell**, and state the absolute view "
                "(Positive / Neutral / Negative) on its own line."
            )

        prompt = f"""As the Research Manager and debate facilitator, your role is to critically evaluate this round of debate and deliver a clear, actionable investment plan for the trader.

{instrument_context}{source_facts_block(state)}{analyst_judgments_block(state)}{model_facts_block(state)}
{model_block}{case_line}

Classify every event that would change the plan into exactly one list:
forecast_revision_triggers (a forecast assumption), rating_review_triggers (expected
return may change) or thesis_invalidation_triggers (the structural thesis is
contradicted). Repeating company guidance is a forecast revision, not a thesis
invalidation. A thesis invalidation needs materiality, no documented temporary
explanation and an explicit thesis_link. For customer advances, state the cause and
whether committed capex continues; a decline with no cause is not a negative trigger.

---

**Rating Scale** (use exactly one):
{scale}

The debate always contains conflicting arguments; deciding which side is stronger is the job, so conflict alone is not a reason to Hold. Commit to the side with the stronger case, sized by how decisively it wins. Choose Hold only when the evidence is still balanced after that weighing, or too thin to support a call; do not manufacture a direction to appear decisive. Weigh the bull and bear cases on their merits, independent of which side spoke first or last.

Agreement between the debaters is not evidence: both read the same reports, so two agents saying the same thing is one observation counted twice. Cite the underlying data or documents — never the headcount — and treat an absence of data as a gap, not as a point for either side.

A forecast that equals guidance or consensus is no forecast differentiation, not evidence for a negative rating. Decide the rating from valuation, expectations, catalysts and downside protection. Technical evidence affects timing, not fundamental value. Do not map a rating to a transaction action here; the Trader does that. State the forecast-equals-consensus observation at most once; it is neutral, not a reason to reduce.

Evaluate a financial claim over the period it is about. Do not rebut a full-year or forward cash-flow claim with a partial-period result unless you reconcile the remaining period explicitly — H1 operating cash flow against full-year capex guidance, for example. Growth is current / prior - 1; incremental margin is (change in operating profit) / (change in revenue). Never present one as the other. If the verified model is unavailable, lower your confidence and do not upgrade the recommendation on unverified valuation: model unavailability never relaxes valuation discipline.

---

**Debate History:**
{history}

## Output

Write these sections, in this order, starting with the recommendation on its own line:

- **Recommendation**: exactly one of Buy / Overweight / Hold / Underweight / Sell
- **Rationale**: which arguments decided it
- **Strategic Actions**: concrete steps for the trader, sized against a standard allocation

{NO_EXTERNAL_TOOLS}""" + EVIDENCE_DISCIPLINE + case_availability(state) + get_language_instruction()

        status: dict = {}
        plan = invoke_structured(structured_llm, prompt, "Research Manager", status=status)
        if plan is None:
            # Retry the full schema once: a thinking model's prose answer is a
            # sampling miss, not a capability gap. Only then use the minimal one.
            retry_status: dict = {}
            plan = invoke_structured(
                structured_llm, prompt + STRUCTURED_RETRY, "Research Manager", status=retry_status
            )
            if plan is not None:
                status = retry_status
        if plan is None:
            # Keep the decision typed: retry on the minimal schema before any
            # prose reconstruction.
            minimal_status: dict = {}
            minimal = invoke_structured(
                minimal_llm, prompt, "Research Manager", status=minimal_status
            )
            if minimal is not None:
                plan = ResearchPlan(
                    recommendation=minimal.recommendation,
                    rationale=minimal.rationale,
                    strategic_actions="",
                )
                status = {"reason": "full schema failed; minimal schema used"}
            else:
                status = minimal_status or status
        trigger_note = ""
        if plan is not None:
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
                    plan.forecast_revision_triggers,
                    plan.rating_review_triggers,
                    plan.thesis_invalidation_triggers,
                    findings,
                ) = triggers_module.normalise(
                    plan.forecast_revision_triggers,
                    plan.rating_review_triggers,
                    plan.thesis_invalidation_triggers,
                    spec,
                )
                if findings:
                    trigger_note = (
                        "\n\n**Trigger classification**: " + "; ".join(findings)
                    )
            except Exception as exc:  # noqa: BLE001 - never lose the plan over a check
                logger.info("Research Manager trigger classification skipped: %s", exc)
        investment_plan = (
            render_research_plan(plan) + trigger_note
            if plan is not None else llm.invoke(prompt).content
        )

        if not str(state.get("portfolio_context") or "").strip():
            investment_plan, _gated = gate_sizing(
                investment_plan, lambda prompt: llm.invoke(prompt).content, "Research Manager"
            )
            if not _gated and has_exact_size(investment_plan):
                investment_plan = scrub_sizing(investment_plan)

        # The same benchmark rule as the Portfolio Manager: a relative rating is
        # not published without a reference portfolio. The absolute direction stays.
        if not benchmark:
            match = re.search(r"(?m)^\*\*Recommendation\*\*: (\w+)", investment_plan)
            if match and match.group(1) in {"Overweight", "Underweight"}:
                absolute = "Positive" if match.group(1) == "Overweight" else "Negative"
                investment_plan = re.sub(
                    r"(?m)^\*\*Recommendation\*\*: .*$", "**Recommendation**: Hold",
                    investment_plan, count=1,
                )
                investment_plan += (
                    f"\n\n**Absolute view**: {absolute}. {match.group(1)} is a relative rating; "
                    "no benchmark or mandate was supplied, so it is not published as one."
                )

        new_investment_debate_state = {
            "history": investment_debate_state.get("history", ""),
            "challenge_history": investment_debate_state.get("challenge_history", ""),
            "last_review": investment_debate_state.get("last_review", ""),
            "first_review": investment_debate_state.get("first_review", ""),
            "first_review_raw": investment_debate_state.get("first_review_raw", ""),
            "unresolved_issue": investment_debate_state.get("unresolved_issue", ""),
            "review_missing": investment_debate_state.get("review_missing", ""),
            "no_material_update": investment_debate_state.get("no_material_update", False),
            "bear_history": investment_debate_state.get("bear_history", ""),
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": investment_plan,
            "count": investment_debate_state["count"],
        }

        result = {
            "investment_debate_state": new_investment_debate_state,
            "investment_plan": investment_plan,
        }
        if status.get("reason"):
            result["structured_fallbacks"] = {"Research Manager": status["reason"]}
        return result

    return research_manager_node
