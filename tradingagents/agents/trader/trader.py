"""Trader: turns the Research Manager's investment plan into a concrete transaction proposal."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from tradingagents.agents.context import (
    analyst_judgments_block,
    get_instrument_context_from_state,
    get_language_instruction,
    get_portfolio_context_from_state,
    model_facts_block,
    research_package_block,
    source_facts_block,
)
from tradingagents.agents.schemas import (
    NewInvestorAction,
    TraderProposal,
    render_trader_proposal,
)
from tradingagents.agents.sizing import (
    downgrade_buy,
    gate_sizing,
    has_exact_size,
    scrub_execution_levels,
    scrub_sizing,
)
from tradingagents.agents.structured import (
    NO_EXTERNAL_TOOLS,
    bind_structured,
    invoke_structured,
)


def create_trader(llm):
    structured_llm = bind_structured(llm, TraderProposal, "Trader")

    def trader_node(state):
        company_name = state["company_of_interest"]
        instrument_context = get_instrument_context_from_state(state)
        investment_plan = state["investment_plan"]
        # The research plan digests the debate but loses exact price structure;
        # give the Trader the technical market report so entry/stop levels are
        # grounded in real ATR / support-resistance / current price (#1167). The
        # report is empty when the user did not select the market analyst, so
        # only offer it (and the grounding instruction) when it has content.
        market_report = (state["market_report"] or "").strip()
        portfolio_context = get_portfolio_context_from_state(state)
        # The plan digests the model into prose; the Trader needs the fair-value
        # range and the target to size and to mark what it quotes.
        model_block = research_package_block(state)

        if market_report:
            grounding = (
                "Ground concrete price levels (entry, stop-loss, position sizing) in the technical "
                "market report's price structure -- current price, support/resistance, ATR, and "
                "volatility -- and use the research plan for direction and strategy. "
            )
            report_section = f"Technical Market Report:\n{market_report}\n\n"
        else:
            grounding = ""
            report_section = ""

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a trading agent framing the technical stance on a name. "
                    "Give a stance, the condition that would justify acting, and the "
                    "condition that would end it. Do not state a position size, a stop "
                    "price or a fundamental rating: the Portfolio Manager owns sizing "
                    "and the rating. "
                    + grounding
                    + ("" if portfolio_context.strip() else
                       "No portfolio context was supplied: speak about the chart, not "
                       "about a book you cannot see. ")
                    + NO_EXTERNAL_TOOLS
                    + get_language_instruction()
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Here is the research team's investment plan for {company_name}. "
                    f"{instrument_context}\n\n"
                    f"{source_facts_block(state)}{analyst_judgments_block(state)}"
                    f"{model_facts_block(state)}\n\n"
                    f"{report_section}"
                    f"{model_block}\n"
                    f"{portfolio_context}\n\n"
                    f"Proposed Investment Plan:\n{investment_plan}\n\n"
                    "## Output\n\n"
                    "- **New investor action**: exactly one of Initiate / Wait / Avoid.\n"
                    "- **Holder action**: exactly one of Add / Hold / Review / Reduce / Exit. "
                    "This is the tactical stance for an existing holder, not a fundamental "
                    "rating. Do not map the research rating mechanically: Underweight means "
                    "reduce an existing long or avoid a new one, not a short; Exit requires a "
                    "holding to exit or an explicit short mandate. Conflict alone is not Hold. "
                    "Use Wait/Review when the evidence is insufficient.\n"
                    "- **Entry condition**: the observable condition that would justify "
                    "acting (a close above a level, with the confirmation you require).\n"
                    "- **Deterioration condition**: the observable condition that ends the "
                    "stance. This is an invalidation, not a protective stop.\n"
                    "- **Evidence**: the verified evidence IDs you rely on. Do not invent "
                    "an id.\n"
                    "- **Confidence**: High / Medium / Low.\n"
                    "If the valuation packet says a method is suppressed, do not state or "
                    "imply a P/E, market cap, target or fair value."
                ),
            },
        ]

        status: dict = {}
        has_book = bool(str(state.get("portfolio_context") or "").strip())
        unverified = str(state.get("research_model_source") or "") == "unavailable"
        trader_status = "structured"
        proposal = invoke_structured(structured_llm, messages, "Trader", status=status)
        if proposal is None:
            # Second attempt: a schema miss is usually a formatting miss, not a
            # missing judgement. Only a second failure makes the output advisory.
            proposal = invoke_structured(
                structured_llm,
                messages
                + [{
                    "role": "user",
                    "content": (
                        "Return only the required structured object. Do not add prose "
                        "outside the schema. Use an omitted field when the evidence is "
                        "insufficient, not a placeholder."
                    ),
                }],
                "Trader",
                status=status,
            )
        if proposal is not None:
            downgraded = (
                unverified and proposal.new_investor_action == NewInvestorAction.INITIATE
            )
            if downgraded:
                proposal.new_investor_action = NewInvestorAction.WAIT
            trader_plan = render_trader_proposal(proposal)
            if downgraded:
                trader_plan += (
                    "\n\n**Action note**: the original Initiate was downgraded to Wait "
                    "because the valuation is unverified."
                )
        else:
            trader_plan = llm.invoke(messages).content
            trader_status = "advisory"
            # A free-text Trader cannot control the levels, size or target. Scrub
            # them even when a book was supplied, because the schema was not met.
            trader_plan = scrub_execution_levels(trader_plan)
            if has_exact_size(trader_plan):
                trader_plan = scrub_sizing(trader_plan)
            if unverified:
                trader_plan, _downgraded = downgrade_buy(trader_plan)

        if not has_book:
            # Free text ignores the prompt's sizing rule; one rewrite pass, accepted
            # only if no exact size remains.
            trader_plan, _gated = gate_sizing(
                trader_plan, lambda prompt: llm.invoke(prompt).content, "Trader"
            )
            if not _gated and has_exact_size(trader_plan):
                trader_plan = scrub_sizing(trader_plan)

        result = {
            "messages": [AIMessage(content=trader_plan)],
            "trader_investment_plan": trader_plan,
            "trader_status": trader_status,
        }
        if status.get("reason"):
            result["structured_fallbacks"] = {"Trader": status["reason"]}
        return result

    return trader_node
