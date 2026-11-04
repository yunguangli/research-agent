"""Trader: turns the Research Manager's investment plan into a concrete transaction proposal."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from tradingagents.agents.context import (
    get_instrument_context_from_state,
    get_language_instruction,
    get_portfolio_context_from_state,
    research_package_block,
)
from tradingagents.agents.schemas import TraderAction, TraderProposal, render_trader_proposal
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
                    "You are a trading agent analyzing market data to make investment decisions. "
                    "Based on your analysis, provide a specific recommendation to buy, sell, or hold. "
                    + grounding
                    # Entry/stop are numeric price fields. Asking for concrete
                    # levels invites a percentage ("15%"), which is not a price
                    # and fails the structured parse (#1288).
                    + "State entry price and stop-loss as absolute price levels in the "
                    "instrument's quote currency (for example 189.5), never a percentage "
                    "or a range; convert a percentage distance to the price level it "
                    "implies, or omit the field if you cannot state a number. "
                    + ("" if portfolio_context.strip() else
                       "No portfolio context was supplied: discuss trims, stops and position "
                       "changes qualitatively — never a fixed trim percentage or a stop as a "
                       "portfolio instruction. ")
                    + NO_EXTERNAL_TOOLS
                    + get_language_instruction()
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Here is the research team's investment plan for {company_name}. "
                    f"{instrument_context}\n\n"
                    f"{report_section}"
                    f"{model_block}\n"
                    f"{portfolio_context}\n\n"
                    f"Proposed Investment Plan:\n{investment_plan}\n\n"
                    "Make an informed, strategic trading decision.\n\n"
                    "## Output\n\n"
                    "Write these sections, in this order, starting with the action "
                    "on its own line:\n\n"
                    "- **Action**: exactly one of Buy / Hold / Sell. Do not map a research "
                    "rating mechanically: Underweight means reduce an existing long or avoid "
                    "a new one, not a short; Sell requires an existing holding to exit or an "
                    "explicit short mandate. Overweight is an add, not a mandate to chase. "
                    "Conflict alone is not a Hold.\n"
                    "- **Intent**: for a Sell, say whether it reduces an existing long "
                    "(`reduce_long`) or opens a short (`initiate_short`, only with a short "
                    "mandate). With no position, a Sell is an avoidance: leave the intent "
                    "empty and do not describe a short entry.\n"
                    "- **Reasoning**: why, against the plan and the price structure\n"
                    "- **Entry Price**, **Stop Loss**, **Position Sizing**: when you can state them\n"
                    "A level above the entry is an invalidation level, not a stop loss: "
                    "name it as the price that would end the stance, not as protection. "
                    "If ATR is available, a stop closer than about one ATR is not protection; "
                    "state conditions instead. If your confirmation condition is not met, the "
                    "action is Wait, not a first tranche."
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
            if not has_book or unverified:
                # Sizing and execution levels need a portfolio and a valuation the
                # run can verify; with neither, the fields stay empty.
                proposal.position_sizing = None
                proposal.entry_price = None
                proposal.stop_loss = None
            downgraded = unverified and proposal.action == TraderAction.BUY
            if downgraded:
                proposal.action = TraderAction.HOLD
                proposal.intent = None
            trader_plan = render_trader_proposal(proposal)
            if downgraded:
                # Name the reason that actually fired: with a portfolio supplied
                # the old note still claimed none had been.
                reason = (
                    "the valuation is unverified and no portfolio context was supplied"
                    if not has_book
                    else "the valuation is unverified"
                )
                trader_plan += (
                    f"\n\n**Action note**: the original Buy was downgraded to Hold because {reason}."
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
