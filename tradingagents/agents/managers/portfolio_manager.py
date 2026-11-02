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

from tradingagents.agents.context import (
    FIGURE_MARKER_RULE,
    case_block,
    evidence_pack,
    fill_placeholders,
    get_language_instruction,
    position_fields,
    report_or_absent,
)
from tradingagents.agents.rating import parse_rating
from tradingagents.agents.schemas import PortfolioDecision, render_pm_decision
from tradingagents.agents.structured import NO_EXTERNAL_TOOLS, bind_structured, invoke_structured
from tradingagents.research.cases import strip_case_json

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

Technical analysis:
{{technical_analysis_or_null}}

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

TARGET AND VALUATION

Use a target range or rounded target consistent with forecast uncertainty.

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


def _fill(state: dict, ticker: str, analysis_date: str) -> str:
    package = str(state.get("research_package") or "").strip()
    debate = state.get("investment_debate_state") or {}
    risk = state.get("risk_debate_state") or {}
    portfolio_text = str(state.get("portfolio_context") or "")
    position, average_cost = position_fields(portfolio_text)
    # Old checkpoints predate the reviewer: the stored debate history is the
    # review in that state, so the synthesis still has an adversarial input.
    review = strip_case_json(
        str(debate.get("challenge_history") or debate.get("history") or "")
    ).strip()
    risk_team = strip_case_json(str(risk.get("history") or "")).strip()
    cases = case_block(state).strip()
    lessons = str(state.get("past_context") or "").strip()
    values = {
        "instrument_ticker": ticker or "null",
        "analysis_date": analysis_date or "null",
        "evidence_pack_or_null": evidence_pack(ticker, analysis_date),
        "fundamental_analysis_or_null": report_or_absent(
            state.get("fundamentals_report"), "fundamentals"
        ),
        # The package plus the marker rule: without it the synthesizer's quotes
        # of model figures arrive unmarked and the number audit cannot see them.
        "model_audit_or_null": (
            package + "\n" + FIGURE_MARKER_RULE if package
            else "null — no verified model was built for this run."
        ),
        "technical_analysis_or_null": report_or_absent(state.get("market_report"), "market"),
        "adversarial_review_or_null": review or "null — no adversarial review in this run.",
        "decision_horizon_or_null": str(state.get("decision_horizon") or "").strip() or "null",
        "current_position_or_null": position,
        "average_cost_or_null": average_cost,
        "portfolio_weight_or_null": "null",   # no runtime source supplies a weight
        "mandate_constraints_or_null": "null",
        "validated_trading_rules_or_null": "null",
        "instrument_context_or_null": str(state.get("instrument_context") or "").strip() or "null",
        "portfolio_block_or_null": portfolio_text.strip() or "null",
        "research_plan_or_null": str(state.get("investment_plan") or "").strip() or "null",
        "trader_proposal_or_null": str(state.get("trader_investment_plan") or "").strip() or "null",
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
        "rating_framework_or_null": (
            "Buy / Overweight / Hold / Underweight / Sell. Open the report with the "
            "rating on its own line, exactly: **Rating**: <one of the five> — the "
            "runtime reads this line to record the decision."
        ),
    }
    return fill_placeholders(TEMPLATE, values)


def create_portfolio_manager(llm):
    structured_llm = bind_structured(llm, PortfolioDecision, "Portfolio Manager")

    def portfolio_manager_node(state) -> dict:
        ticker = str(state.get("company_of_interest") or "").strip()
        analysis_date = str(state.get("trade_date") or "")

        prompt = (
            _fill(state, ticker, analysis_date)
            + NO_EXTERNAL_TOOLS
            + get_language_instruction()
        )

        risk_debate_state = state["risk_debate_state"]

        # The typed rating is the decision; the rendered text only carries it.
        # Read back from text, a rating the thesis quotes could replace it.
        decision = invoke_structured(structured_llm, prompt, "Portfolio Manager")
        if decision is not None:
            # The runtime knows the horizon the run was given; a model that
            # left the optional field empty should not print "not provided"
            # beside an executive summary that states it.
            horizon = str(state.get("decision_horizon") or "").strip()
            if horizon and not decision.time_horizon:
                decision.time_horizon = horizon
            final_trade_decision = render_pm_decision(decision)
            final_rating = decision.rating.value
        else:
            final_trade_decision = llm.invoke(prompt).content
            final_rating = parse_rating(final_trade_decision)

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

        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": final_trade_decision,
            "final_rating": final_rating,
        }

    return portfolio_manager_node
