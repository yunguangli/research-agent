"""The first speaker in each debate must not rebut a nonexistent argument (#1176).

The research team's single Adversarial Investment Reviewer opens with no prior
round and must say so rather than rebut a phantom; later rounds carry its own
previous review (with the case JSON stripped so it cannot copy its own
multiples). The three risk analysts each open against two opponent slots and
still substitute an explicit opening marker when an opponent has not spoken.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tradingagents.agents.context import opponent_argument_or_opening
from tradingagents.agents.researchers.adversarial_reviewer import create_adversarial_reviewer
from tradingagents.agents.risk_mgmt.aggressive_debator import create_aggressive_debator
from tradingagents.agents.risk_mgmt.conservative_debator import create_conservative_debator
from tradingagents.agents.risk_mgmt.neutral_debator import create_neutral_debator

_REPORTS = {
    "company_of_interest": "AAPL", "asset_type": "stock",
    "trade_date": "2026-08-14",
    "market_report": "m", "sentiment_report": "s",
    "news_report": "n", "fundamentals_report": "f",
}


def _capturing_llm(captured: dict, content: str = "argument"):
    llm = MagicMock()
    llm.invoke.side_effect = lambda prompt: (
        captured.__setitem__("prompt", prompt) or MagicMock(content=content)
    )
    return llm


def _investment_state(challenge="", portfolio=""):
    return {
        **_REPORTS,
        "portfolio_context": portfolio,
        "investment_debate_state": {
            "history": "", "bull_history": "", "bear_history": "",
            "challenge_history": challenge,
            "current_response": "", "count": 0,
        },
    }


def _risk_state(**responses):
    base = {
        "current_aggressive_response": "", "current_conservative_response": "",
        "current_neutral_response": "", "history": "", "aggressive_history": "",
        "conservative_history": "", "neutral_history": "", "count": 0,
    }
    base.update(responses)
    return {**_REPORTS, "trader_investment_plan": "plan", "risk_debate_state": base}


# --- shared helper ----------------------------------------------------------

@pytest.mark.unit
def test_helper_marks_empty_and_passes_through():
    assert "has not spoken yet" in opponent_argument_or_opening("", "bear analyst")
    assert opponent_argument_or_opening("  real point ", "bear") == "real point"


# --- the adversarial reviewer ----------------------------------------------

@pytest.mark.unit
def test_the_first_pass_says_it_is_the_first_pass():
    """No prior round exists; the prompt says so instead of inventing one."""
    captured = {}
    create_adversarial_reviewer(_capturing_llm(captured))(_investment_state())
    assert "none — this is the first pass." in captured["prompt"]


@pytest.mark.unit
def test_a_later_round_carries_the_previous_review_without_its_case_json():
    captured = {}
    prior = (
        "## 1. Strongest positive challenge\nprevious findings\n"
        '{"stance": "aggressive", "probability": 0.34, "revenue_pct": 2.0,'
        ' "operating_profit_pct": 3.0, "multiple": 17.5}'
    )
    state = _investment_state(challenge=prior)
    state["investment_debate_state"]["count"] = 1
    create_adversarial_reviewer(_capturing_llm(captured))(state)
    assert "previous findings" in captured["prompt"]
    # the stored round's case JSON is stripped: it must not copy its own multiple
    assert '"multiple": 17.5' not in captured["prompt"]


@pytest.mark.unit
def test_the_reviewer_reads_the_reports_that_exist_and_names_the_missing():
    captured = {}
    state = _investment_state()
    state["fundamentals_report"] = ""
    create_adversarial_reviewer(_capturing_llm(captured))(state)
    prompt = captured["prompt"]
    assert "Technical analysis:\nm" in prompt          # the report that exists is passed through
    assert "not available" in prompt                   # the missing one is named, not invented
    assert "no research corpus is configured" in prompt  # the evidence pack states its gap


@pytest.mark.unit
def test_the_reviewer_sees_the_position_but_never_the_raw_book():
    """Its template asks for the position; cash and other holdings stay out."""
    captured = {}
    portfolio = (
        "Portfolio at the analysis date:\n"
        "- Current position in AAPL: 500 units, average price 7,980.00\n"
        "- Cash available: 1,000.00 USD"
    )
    create_adversarial_reviewer(_capturing_llm(captured))(_investment_state(portfolio=portfolio))
    assert "Current position context: 500 units" in captured["prompt"]
    assert "Cash available" not in captured["prompt"]


@pytest.mark.unit
def test_the_reviewer_appends_both_case_stances():
    answer = (
        "challenge\n"
        '{"stance": "aggressive", "probability": 0.34, "revenue_pct": 2.0,'
        ' "operating_profit_pct": 3.0, "multiple": 30.0}\n'
        '{"stance": "bearish", "probability": 0.34, "revenue_pct": -2.0,'
        ' "operating_profit_pct": -3.0, "multiple": 22.0}'
    )
    state = _investment_state()
    result = create_adversarial_reviewer(_capturing_llm(captured={}, content=answer))(state)

    import json

    cases = json.loads(result["debate_cases"])
    assert {case["stance"] for case in cases} == {"aggressive", "bearish"}
    # the round lands in the shared histories, count moves, old keys untouched
    debate = result["investment_debate_state"]
    assert debate["count"] == 1 and "challenge" in debate["challenge_history"]
    assert debate["history"].lstrip().startswith("challenge")
    assert debate["bull_history"] == "" and debate["bear_history"] == ""


# --- risk debators ----------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize(
    "factory", [create_aggressive_debator, create_conservative_debator, create_neutral_debator]
)
def test_risk_opening_has_no_phantom_opponent(factory):
    captured = {}
    factory(_capturing_llm(captured))(_risk_state())
    # Both opponent slots were empty -> two opening markers, no fabricated args.
    assert captured["prompt"].count("has not spoken yet") == 2


@pytest.mark.unit
def test_risk_passes_real_opponent_arguments():
    captured = {}
    state = _risk_state(
        current_conservative_response="Conservative Analyst: trim risk",
        current_neutral_response="Neutral Analyst: hold steady",
    )
    create_aggressive_debator(_capturing_llm(captured))(state)
    assert "trim risk" in captured["prompt"]
    assert "hold steady" in captured["prompt"]
    assert "has not spoken yet" not in captured["prompt"]
