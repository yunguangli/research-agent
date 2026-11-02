"""One adversarial reviewer replaces the two debating personas.

The old bull/bear pair read the same reports and re-argued the same facts from
fixed sides; this agent challenges the fundamental and technical analyses in a
single pass, producing both the strongest positive and the strongest negative
challenge, and appends both debate cases (aggressive and bearish) so the case
table, the managers and the synthesizer keep their quantified inputs.
"""

from __future__ import annotations

from tradingagents.agents.context import (
    COMPARATIVE_CLAIM_RULE,
    evidence_pack,
    fill_placeholders,
    get_language_instruction,
    position_fields,
    report_or_absent,
)
from tradingagents.research.cases import (
    CAP_FORECAST_PCT,
    CAP_MULTIPLE_PCT,
    parse_cases,
    strip_case_json,
    with_case,
)

TEMPLATE = r"""You are the Adversarial Investment Reviewer for {{instrument_ticker}}.

Your purpose is to challenge the fundamental and technical analyses without
manufacturing disagreement.

INPUTS

Source-extraction evidence pack:
{{evidence_pack_or_null}}

Independent fundamental analysis:
{{fundamental_analysis_or_null}}

Model audit:
{{model_audit_or_null}}

Technical analysis:
{{technical_analysis_or_null}}

Decision horizon: {{decision_horizon_or_null}}
Current position context: {{current_position_or_null}}
Mandate constraints: {{mandate_constraints_or_null}}

Prior rounds of this review: {{prior_review_or_null}}

TASK

Identify the strongest evidence for and against the proposed investment
conclusion.

Do not repeat the entire reports.

Do not treat multiple agents citing the same underlying source as independent
confirmation.

For the positive case, identify:

- The most important underappreciated operating driver;
- The strongest potential earnings-revision mechanism;
- The most credible rerating mechanism;
- The evidence required within the decision horizon.

For the negative case, identify:

- The most important overestimated assumption;
- The clearest earnings-downgrade mechanism;
- The most material valuation or cash-flow risk;
- The evidence that would confirm deterioration.

CHALLENGE TESTS

Ask:

- Does the base case merely match guidance or consensus?
- Is any capacity or product contribution recognized too early?
- Is pricing or mix double counted?
- Are residual bridge items being described as drivers?
- Does the thesis rely on evidence that cannot emerge within the decision horizon?
- Does the bull case require both higher earnings and multiple expansion?
- Does the bear case confuse technical weakness with fundamental deterioration?
- Is the chart being used to create false precision?
- Are customer advances, orders or backlog being overinterpreted?
- Are share count, taxes, capital expenditure or working capital understated?
- What is the single assumption most likely to change the decision?

OUTPUT

Use:

1. Strongest positive challenge
2. Strongest negative challenge
3. Common assumptions both sides may be missing
4. Evidence that would resolve the disagreement
5. Adversarial conclusion

Limit the output to the few disagreements that could materially change the rating,
target range or timing decision.

Do not issue portfolio sizing, stop-loss or hedging instructions.

Then append TWO JSON objects, each on its own line with nothing after them, so
the debate's case table can quantify both challenges against the verified model.
Caps: revenue_pct and operating_profit_pct stay within +/-{{cap_pct}}% of the
base case, the multiple stays within +/-{{cap_multiple}}% of the base multiple,
probability is between 0 and 1; a case beyond a cap is rejected:
{"stance": "aggressive", "probability": 0.34, "revenue_pct": 2.0, "operating_profit_pct": 3.0, "multiple": 30.0, "momentum": "...", "sentiment": "...", "evidence": ["document_id"]}
{"stance": "bearish", "probability": 0.34, "revenue_pct": -2.0, "operating_profit_pct": -3.0, "multiple": 22.0, "momentum": "...", "sentiment": "...", "evidence": ["document_id"]}"""


def _fill(state: dict, ticker: str) -> str:
    package = str(state.get("research_package") or "").strip()
    debate = state.get("investment_debate_state") or {}
    position, _average_cost = position_fields(str(state.get("portfolio_context") or ""))
    prior = strip_case_json(
        str(debate.get("challenge_history") or debate.get("history") or "")
    ).strip()
    values = {
        "instrument_ticker": ticker or "null",
        "evidence_pack_or_null": evidence_pack(ticker, str(state.get("trade_date") or "")),
        "fundamental_analysis_or_null": report_or_absent(
            state.get("fundamentals_report"), "fundamentals"
        ),
        "model_audit_or_null": package or "null — no verified model was built for this run.",
        "technical_analysis_or_null": report_or_absent(state.get("market_report"), "market"),
        "decision_horizon_or_null": str(state.get("decision_horizon") or "").strip() or "null",
        "current_position_or_null": position,
        "mandate_constraints_or_null": "null",
        "prior_review_or_null": prior or "none — this is the first pass.",
        "cap_pct": f"{CAP_FORECAST_PCT:g}",
        "cap_multiple": f"{CAP_MULTIPLE_PCT:g}",
    }
    return fill_placeholders(TEMPLATE, values)


def create_adversarial_reviewer(llm):
    def reviewer_node(state) -> dict:
        debate = state["investment_debate_state"]
        ticker = str(state.get("company_of_interest") or "").strip()

        prompt = (
            _fill(state, ticker)
            + COMPARATIVE_CLAIM_RULE
            + get_language_instruction()
        )

        response = llm.invoke(prompt)
        argument = str(response.content)

        challenge = str(debate.get("challenge_history") or "")
        new_debate_state = {
            "history": str(debate.get("history") or "") + "\n" + argument,
            "challenge_history": (challenge + "\n" + argument) if challenge else argument,
            # Kept for old checkpoints and the state log; the reviewer never writes them.
            "bull_history": debate.get("bull_history", ""),
            "bear_history": debate.get("bear_history", ""),
            "current_response": argument,
            "count": debate.get("count", 0) + 1,
        }

        result: dict = {"investment_debate_state": new_debate_state}
        cases = parse_cases(argument)
        if cases:
            existing = state.get("debate_cases", "")
            for case in cases:
                existing = with_case(existing, case)
            result["debate_cases"] = existing
        return result

    return reviewer_node
