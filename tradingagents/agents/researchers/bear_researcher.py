
from tradingagents.agents.context import (
    get_instrument_context_from_state,
    get_language_instruction,
    opponent_argument_or_opening,
    report_or_absent,
)
from tradingagents.research.cases import (
    CAP_FORECAST_PCT,
    CAP_MULTIPLE_PCT,
    parse_case,
    strip_case_json,
)


def create_bear_researcher(llm):
    def bear_node(state) -> dict:
        investment_debate_state = state["investment_debate_state"]
        history = strip_case_json(investment_debate_state.get("history", ""))
        bear_history = investment_debate_state.get("bear_history", "")

        current_response = opponent_argument_or_opening(
            strip_case_json(investment_debate_state.get("current_response", "")), "bull analyst"
        )
        market_research_report = report_or_absent(state["market_report"], "market")
        sentiment_report = report_or_absent(state["sentiment_report"], "sentiment")
        news_report = report_or_absent(state["news_report"], "news")
        fundamentals_report = report_or_absent(state["fundamentals_report"], "fundamentals")
        instrument_context = get_instrument_context_from_state(state)
        asset_type = state.get("asset_type", "stock")
        target_label = "stock" if asset_type == "stock" else "asset"
        fundamentals_label = (
            "Company fundamentals report"
            if asset_type == "stock"
            else "Asset fundamentals report (may be unavailable for crypto)"
        )

        cap, multiple_cap = CAP_FORECAST_PCT, CAP_MULTIPLE_PCT

        prompt = f"""You are a Bear Analyst making the case against investing in the {target_label}. Your goal is to present a well-reasoned argument emphasizing risks, challenges, and negative indicators. Leverage the provided research and data to highlight potential downsides and counter bullish arguments effectively.

Key points to focus on:

- Risks and Challenges: Highlight factors like market saturation, financial instability, or macroeconomic threats that could hinder the stock's performance.
- Competitive Weaknesses: Emphasize vulnerabilities such as weaker market positioning, declining innovation, or threats from competitors.
- Negative Indicators: Use evidence from financial data, market trends, or recent adverse news to support your position.
- Bull Counterpoints: Critically analyze the bull argument with specific data and sound reasoning, exposing weaknesses or over-optimistic assumptions.
- Engagement: Present your argument in a conversational style, directly engaging with the bull analyst's points and debating effectively rather than simply listing facts.

Resources available:

{instrument_context}
Market research report: {market_research_report}
Social media sentiment report: {sentiment_report}
Latest world affairs news: {news_report}
{fundamentals_label}: {fundamentals_report}
Conversation history of the debate: {history}
Last bull argument: {current_response}
Use this information to deliver a compelling bear argument, refute the bull's claims, and engage in a dynamic debate that demonstrates the risks and weaknesses of investing in the {target_label}.

Then append ONE JSON object on its own line, with nothing after it, describing the case you are arguing for on the verified model:
{{"stance": "bearish", "probability": 0.34, "revenue_pct": -2.0, "operating_profit_pct": -3.0, "multiple": 22.0, "momentum": "...", "sentiment": "...", "evidence": ["document_id"]}}
Caps: revenue_pct and operating_profit_pct stay within ±{cap:.0f}% of the base case, the multiple stays within ±{multiple_cap:.0f}% of the base multiple, probability is between 0 and 1. A case beyond a cap is rejected, so stay inside it.

The multiple is a separate argument from the forecast. Argue a lower multiple only with evidence: peer multiples, the company's own history, or the rate environment. Put that evidence in "evidence" (document ids or the peer set). If you have no such evidence, set "multiple" to null and let the base multiple stand. A multiple asserted without evidence is discarded and the base is used. Do not reuse the multiple the other side argued: read their case as something to rebut, not as a default.
""" + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Bear Analyst: {response.content}"

        new_investment_debate_state = {
            "history": history + "\n" + argument,
            "bear_history": bear_history + "\n" + argument,
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
        }

        result = {"investment_debate_state": new_investment_debate_state}
        case = parse_case(str(response.content))
        if case is not None:
            from tradingagents.agents.researchers.bull_researcher import _with_case

            result["debate_cases"] = _with_case(state.get("debate_cases", ""), case)
        return result

    return bear_node
