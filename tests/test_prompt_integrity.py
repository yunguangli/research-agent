"""What the agents are actually told.

Three problems the audit found: one analyst's brief reached the model as a Python
tuple, every analyst was asked for a trade call that nothing reads, and a report
that was never produced was presented as an empty labelled section, which invites
the next agent to fill it in from nothing.
"""

from __future__ import annotations

import importlib

import pytest

ANALYSTS = ["market_analyst", "sentiment_analyst", "news_analyst", "fundamentals_analyst"]


@pytest.mark.unit
@pytest.mark.parametrize("name", ANALYSTS)
def test_an_analyst_brief_is_text_not_a_python_object(name):
    """A trailing comma made one brief a tuple, so the model was handed its repr
    (quotes, parens and all) instead of the instruction."""
    import ast
    import inspect

    mod = importlib.import_module(f"tradingagents.agents.analysts.{name}")
    tree = ast.parse(inspect.getsource(mod))
    briefs = [node.value for node in ast.walk(tree)
              if isinstance(node, ast.Assign)
              and getattr(node.targets[0], "id", "") == "system_message"]
    assert briefs, f"{name} has no system_message"
    for brief in briefs:
        assert not isinstance(brief, ast.Tuple), "the brief is a tuple, not text"


@pytest.mark.unit
@pytest.mark.parametrize("name", ANALYSTS)
def test_an_analyst_is_not_asked_for_a_trade_call_nothing_reads(name):
    """The stop signal is never consumed, and asking for it makes an analyst
    open with a direction that then travels as evidence."""
    import inspect

    mod = importlib.import_module(f"tradingagents.agents.analysts.{name}")
    assert "FINAL TRANSACTION PROPOSAL" not in inspect.getsource(mod)


@pytest.mark.unit
@pytest.mark.parametrize("module, factory", [
    ("tradingagents.agents.researchers.adversarial_reviewer", "create_adversarial_reviewer"),
    ("tradingagents.agents.risk_mgmt.aggressive_debator", "create_aggressive_debator"),
    ("tradingagents.agents.risk_mgmt.conservative_debator", "create_conservative_debator"),
    ("tradingagents.agents.risk_mgmt.neutral_debator", "create_neutral_debator"),
])
def test_a_report_that_was_never_produced_says_so(module, factory):
    """`--analysts market` leaves three reports empty; presenting them as blank
    sections invites the model to invent the contents."""
    from langchain_core.messages import AIMessage

    mod = importlib.import_module(module)
    seen = []

    class _LLM:
        def invoke(self, prompt, *a, **k):
            seen.append(prompt if isinstance(prompt, str) else str(prompt))
            return AIMessage("argument")

        def with_structured_output(self, *a, **k):
            raise NotImplementedError

    state = {
        "company_of_interest": "NVDA", "trade_date": "2026-08-14", "asset_type": "stock",
        "instrument_context": "", "portfolio_context": "", "past_context": "",
        "market_report": "RSI 61, price 178.", "sentiment_report": "", "news_report": "",
        "fundamentals_report": "", "investment_plan": "P", "trader_investment_plan": "T",
        "investment_debate_state": {"bull_history": "", "bear_history": "", "history": "",
                                    "current_response": "", "count": 0},
        "risk_debate_state": {"history": "", "latest_speaker": "", "count": 0,
                              "aggressive_history": "", "conservative_history": "", "neutral_history": "",
                              "current_aggressive_response": "", "current_conservative_response": "",
                              "current_neutral_response": ""},
    }
    getattr(mod, factory)(_LLM())(state)

    prompt = " ".join(seen)
    assert "not part of this run" in prompt or "not available" in prompt, prompt[:400]
    assert "RSI 61" in prompt  # the report that does exist is still passed through


@pytest.mark.unit
def test_the_market_analyst_separates_observation_from_hypothesis():
    """The volume-interpretation lesson: OHLCV shows what, not who or why — and
    technical evidence must never move the fundamental target."""
    import inspect

    from tradingagents.agents.analysts import market_analyst

    source = inspect.getsource(market_analyst)
    assert "Do not infer participant identity" in source
    assert "label it as a hypothesis" in source
    # and it does not set the target: timing, sizing and risk only
    assert "must not determine fundamental fair value" in source


@pytest.mark.unit
def test_the_prose_rules_reach_every_writer_of_comparisons():
    """38.6bn does not exceed 63.7bn — the rule that says so, and where it is
    (and is not) attached: the shared comparative rule to the debaters, Section
    22's own wording to the fundamentals analyst (its template carries the rule
    inline), the sizing rule to everyone who could invent a trim, and the
    derived-figure example to the mark rule itself."""
    import inspect

    from tradingagents.agents import context
    from tradingagents.agents.analysts import fundamentals_analyst
    from tradingagents.agents.managers import portfolio_manager, research_manager
    from tradingagents.agents.risk_mgmt import (
        aggressive_debator,
        conservative_debator,
        neutral_debator,
    )

    assert "61% of 63.7" in context.COMPARATIVE_CLAIM_RULE
    assert "growth rate" in context.COMPARATIVE_CLAIM_RULE
    assert "agreement is never cited as support" in context.COMPARATIVE_CLAIM_RULE
    assert "215.4 × 50 = 10,770" in context.FIGURE_MARKER_RULE
    assert "report the discrepancy" in context.FIGURE_MARKER_RULE
    assert "fixed trim percentage" in context.SIZING_RULE
    from tradingagents.agents.researchers import adversarial_reviewer
    for module in (adversarial_reviewer,):
        assert "COMPARATIVE_CLAIM_RULE" in inspect.getsource(module)
    source = inspect.getsource(fundamentals_analyst)
    # Section 22 of the fundamentals template carries the same discipline
    assert "Every comparison must be mathematically true" in source
    assert "Do not use agreement between agents as evidence" in source
    assert "Write each figure once" in source
    for module in (aggressive_debator, conservative_debator, neutral_debator):
        assert "SIZING_RULE" in inspect.getsource(module)
    assert "Agreement between the debaters is not evidence" in inspect.getsource(research_manager)
    assert "Do not treat agent agreement as evidence" in inspect.getsource(portfolio_manager)


@pytest.mark.unit
def test_the_decision_horizon_flows_from_state_into_the_market_prompt():
    """The optional Step 9 answer rides the run state into the CONTEXT block;
    a state without one keeps the template's own overlay fallback."""
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    from tradingagents.agents.analysts import market_analyst
    from tradingagents.graph.propagation import Propagator

    captured: dict = {}

    class Recorder(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "recorder"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            captured["text"] = "\n".join(str(getattr(m, "content", "")) for m in messages)
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    state = Propagator().create_initial_state(
        "4062.T", "2026-11-02", decision_horizon="12+ months"
    )
    market_analyst.create_market_analyst(Recorder())(state)
    assert "Decision horizon: 12+ months" in captured["text"]

    state = Propagator().create_initial_state("4062.T", "2026-11-02")
    market_analyst.create_market_analyst(Recorder())(state)
    assert "Decision horizon: not supplied" in captured["text"]
    assert "daily-chart" in captured["text"] and "timing overlay" in captured["text"]


@pytest.mark.unit
def test_the_portfolio_managers_target_comes_from_the_model_not_the_chart():
    """Technical analysis informs timing — it must not move fundamental value,
    and the target may not be reverse-engineered to fit a conclusion."""
    import inspect

    from tradingagents.agents.managers import portfolio_manager

    source = inspect.getsource(portfolio_manager)
    assert "Do not change fundamental value to conform to the chart" in source
    assert "Technical analysis affects timing, not fundamental value" in source
    assert "target is not reverse-engineered" in source
    # the rating framework the runtime supplies is the one the parser reads
    assert "**Rating**:" in source
