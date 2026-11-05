"""Regression tests for the review fixes (finding.md A1-A13).

One module so each fix stays traceable to the finding it closes.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cli.run import _resolve_run_purpose
from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager
from tradingagents.agents.schemas import (
    HolderAction,
    NewInvestorAction,
    PortfolioDecision,
    PortfolioDecisionMinimal,
    PortfolioRating,
    TraderProposal,
)
from tradingagents.agents.sizing import has_exact_size
from tradingagents.agents.trader.trader import create_trader
from tradingagents.backtest import BacktestResult, summarize
from tradingagents.dataflows.prices import parse_last_close
from tradingagents.dataflows.vendors.research_corpus.store import CorpusStore
from tradingagents.research.audit import (
    audit_text,
    percentage_findings,
    relational_findings,
    render,
    terminology_findings,
)
from tradingagents.research.metrics import PeriodMetrics
from tradingagents.research.report import fair_value_range_from_summary

# --- A1: the percentage audit must not read the claim's own percentage --------


def test_a_quoted_percentage_is_not_read_as_a_price_level():
    sentence = (
        "You say the 200 SMA is only 3.4% below spot and was pierced intraday to "
        "7,695, so calling the long-term trend intact is hope."
    )

    assert percentage_findings(sentence, "Aggressive Analyst", 8045.0) == []


def test_a_wrong_percentage_beside_a_printed_level_is_still_caught():
    sentence = "The 50 SMA 10,168.07 sits 20.9% above price on 2 November."

    findings = percentage_findings(sentence, "Aggressive Analyst", 8045.0)

    assert len(findings) == 1
    assert findings[0].subject == pytest.approx(10168.07)
    assert findings[0].expected == "+26.4%"


def test_two_slash_paired_metrics_are_not_compared_across_the_pair():
    # The 14:53 run's only audit finding: 190 (operating profit) was paired with
    # 650 (revenue) because both sit on one slash-paired line.
    sentence = (
        "FY3/28 model revenue/operating profit of 680.0/190.0 sits above "
        "management's 650.0/165.0 plan."
    )

    assert relational_findings(sentence, "Portfolio Manager", {}, "JPY_bn") == []


def test_a_real_contradiction_in_one_metric_is_still_caught():
    sentence = "FY3/28 operating profit of 165.0 sits above the 190.0 consensus."

    assert relational_findings(sentence, "Portfolio Manager", {}, "JPY_bn")


def test_a_conditional_trigger_is_not_a_comparative_claim():
    # The 15:19 run: "commit no new capital at 8,045, add on a close above 8,622"
    # was read as "8,045 above 8,622" because the operand swallowed its comma.
    sentence = (
        "hold existing exposure, commit no new capital at 8,045, add on a daily "
        "close above 8,622, and reduce on a close below 7,780."
    )

    assert relational_findings(sentence, "Aggressive Analyst", {}, "JPY_bn") == []


def test_a_growth_rate_before_a_labelled_incremental_margin_is_not_flagged():
    metrics = [PeriodMetrics("FY3/27", "FY3/26", 0.20, 0.131, 0.39)]
    sentence = (
        "Q2 sequential revenue +13.1% with a sequential incremental operating margin "
        "of ~39% (my calculation: +JPY6.3bn profit on +JPY16.1bn revenue)."
    )

    assert terminology_findings(sentence, metrics, "Fundamentals Analyst") == []


def test_a_growth_rate_called_an_incremental_margin_is_still_flagged():
    metrics = [PeriodMetrics("FY3/27", "FY3/26", 0.20, 0.97, 0.39)]
    sentence = "Operating profit grew 97% and the business delivered a 97% incremental margin."

    findings = terminology_findings(sentence, metrics, "Fundamentals Analyst")

    assert findings and findings[0].number == 97.0


def test_a_real_incremental_margin_near_a_growth_rate_is_not_flagged():
    # 16:59: 41% H1 incremental margin, 1.3 points from the FY3/28 op growth of
    # 39.7%; the old 1.5-point window reported it four times.
    metrics = [
        PeriodMetrics("FY3/27", "FY3/26", 0.37, 1.194, 0.481),
        PeriodMetrics("FY3/28", "FY3/27", 0.193, 0.397, 0.491),
    ]
    sentence = (
        "That implies a 40% contribution margin, which is below even the group's 41% "
        "incremental operating margin delivered in H1 FY3/27."
    )

    assert terminology_findings(sentence, metrics, "Conservative Analyst") == []


def test_an_incremental_margin_reproduced_by_its_own_deltas_is_not_flagged():
    # 11:35: "+10 revenue and +13 operating profit ... 130% incremental margin"
    # is 13/10, arithmetically correct despite being above 90%.
    metrics = [PeriodMetrics("FY3/27", "FY3/26", 0.2, 1.194, 0.481)]
    sentence = (
        "The model is +10 revenue and +13 operating profit versus guidance - an implied "
        "130% incremental margin on the delta."
    )

    assert terminology_findings(sentence, metrics, "Fundamentals Analyst") == []


def test_a_numbered_cell_label_is_not_a_comparative_operand():
    # 11:35: "supports OP above the 190 consensus" paired "Cell 9" with 190.
    sentence = (
        "Rebuild only once Cell 8 loading and the Cell 7/Cell 9 decision support OP "
        "above the 190 consensus."
    )

    assert relational_findings(sentence, "Research Manager", {}, "JPY_bn") == []


def test_a_verified_computed_figure_is_not_a_model_mismatch():
    findings = audit_text(
        "The implied [[computed: 130 = 13/10*100]] incremental margin is high.",
        "Fundamentals Analyst",
        known={1.0},
    )

    assert findings == []


def test_a_computed_figure_whose_formula_does_not_reproduce_it_is_flagged():
    findings = audit_text(
        "The implied [[computed: 130 = 13/11*100]] incremental margin is high.",
        "Fundamentals Analyst",
        known={1.0},
    )

    assert len(findings) == 1
    assert findings[0].kind == "computed"


def test_a_correct_ratio_with_a_multi_line_bridge_gets_an_attribution_warning():
    metrics = [PeriodMetrics("FY3/27", "FY3/26", 0.2, 1.194, 0.481)]
    sentence = (
        "The model is +10 revenue and +13 operating profit versus guidance - an implied "
        "130% incremental margin on the delta."
    )

    findings = terminology_findings(sentence, metrics, "Fundamentals Analyst", bridge_line_count=3)

    assert len(findings) == 1
    assert findings[0].kind == "attribution"
    assert "revenue difference alone" in render(findings)


def test_an_attribution_warning_is_not_raised_for_a_single_line_bridge():
    metrics = [PeriodMetrics("FY3/27", "FY3/26", 0.2, 1.194, 0.481)]
    sentence = (
        "The model is +10 revenue and +13 operating profit versus guidance - an implied "
        "130% incremental margin on the delta."
    )

    assert terminology_findings(sentence, metrics, "Fundamentals Analyst", bridge_line_count=1) == []


# --- A2: the band parser must read what the renderers actually print ----------


@pytest.mark.parametrize(
    "line",
    [
        "Valuation: price 8,045 JPY, FY3/28 EPS 229.6 on full_share, P/E 35.0x, "
        "fair value 6,428-9,642 JPY.",
        "- Fair-value range: 6,428-9,642 JPY",
    ],
)
def test_the_fair_value_band_is_read_from_either_renderer(line):
    assert fair_value_range_from_summary(line) == (6428.0, 9642.0)


def test_the_band_parser_reports_a_missing_band_rather_than_guessing():
    assert fair_value_range_from_summary("Valuation: price 8,045 JPY.") == (None, None)


def _pm_state(**overrides) -> dict:
    state = {
        "company_of_interest": "TEST",
        "trade_date": "2026-01-01",
        "market_report": "m",
        "sentiment_report": "",
        "news_report": "",
        "fundamentals_report": "f",
        "investment_debate_state": {"challenge_history": "", "history": ""},
        "investment_plan": "plan",
        "trader_investment_plan": "trade",
        "risk_debate_state": {
            "history": "",
            "aggressive_history": "",
            "conservative_history": "",
            "neutral_history": "",
            "count": 1,
            "latest_speaker": "Judge",
            "current_aggressive_response": "",
            "current_conservative_response": "",
            "current_neutral_response": "",
        },
        "portfolio_context": "",
        "decision_horizon": "3-6 months",
        "research_model_source": "built",
    }
    state.update(overrides)
    return state


def test_the_pm_backfills_the_range_from_the_models_own_summary():
    full = MagicMock()
    full.invoke.side_effect = ValueError("bad json")
    minimal = MagicMock()
    minimal.invoke.return_value = PortfolioDecisionMinimal(
        rating=PortfolioRating.HOLD, executive_summary="Hold.", investment_thesis=""
    )
    llm = MagicMock()
    llm.with_structured_output.side_effect = lambda schema, **kw: (
        full if schema is PortfolioDecision else minimal
    )
    llm.invoke.return_value = MagicMock(content="FREE TEXT")

    state = _pm_state(
        research_package=(
            "Valuation: price 8,045 JPY, FY3/28 EPS 229.6 on full_share, "
            "P/E 35.0x, fair value 6,428-9,642 JPY."
        )
    )

    decision = create_portfolio_manager(llm)(state)["final_trade_decision"]

    assert "**Fair Value**: 6,428–9,642" in decision
    assert "**Valuation status**: range" in decision
    assert "**Price Target**: not provided" not in decision


# --- A3: reading by id must honour the run date -------------------------------


def test_reading_a_withheld_document_by_id_is_refused():
    store = CorpusStore.__new__(CorpusStore)
    record = SimpleNamespace(
        document_id="doc-TEST-after-cutoff",
        published_at="2026-12-01",
        doc_quality="ok",
        excluded=False,
        exclusion_reason="",
        llm_use="full_text",
        license="public",
    )
    store.get = lambda document_id: record  # type: ignore[method-assign]

    with pytest.raises(Exception) as excinfo:
        store.read_document(record.document_id, as_of="2026-11-03")

    assert "after the analysis date" in str(excinfo.value)


# --- A4: the run purpose must survive the CLI default ------------------------


def test_run_purpose_falls_back_to_the_environment():
    assert _resolve_run_purpose({}, {"run_purpose": "comparison"}) == "comparison"
    assert _resolve_run_purpose({"run_purpose": "replay"}, {"run_purpose": "comparison"}) == "replay"
    assert _resolve_run_purpose({}, {}) == "production"


# --- A9: the price parser must not assume Yahoo's shape ----------------------


def test_last_close_reads_the_latest_dated_row_of_an_alpha_vantage_csv():
    text = (
        "timestamp,open,high,low,close,volume\n"
        "2026-11-02,8001,8353,7695,8045,29470000\n"
        "2026-10-30,9859,9860,8601,8700,31870000\n"
    )

    assert parse_last_close(text) == pytest.approx(8045.0)


def test_last_close_reads_yahoes_capitalised_header():
    text = "Date,Open,High,Low,Close,Volume\n2026-10-30,1,2,1,8700,1\n2026-11-02,1,2,1,8045,1\n"

    assert parse_last_close(text) == pytest.approx(8045.0)


# --- A8: pending is a count of pending entries, not a residual ---------------


def test_pending_counts_only_entries_still_waiting(tmp_path, monkeypatch):
    entries = [
        {"pending": False, "rating": "Buy", "alpha": "2.0%", "holding": "5d"},
        # Settled, but an older log's alpha is unreadable.
        {"pending": False, "rating": "Hold", "alpha": "", "holding": "5d"},
        {"pending": True, "rating": "Buy", "alpha": "", "holding": "5d"},
    ]

    class FakeLog:
        def __init__(self, config):
            pass

        def load_entries(self):
            return entries

    monkeypatch.setattr("tradingagents.backtest.TradingMemoryLog", FakeLog)
    source = BacktestResult(run_id="test", log_path=tmp_path / "log.md")

    summary = summarize(source)

    assert summary.pending == 1
    assert summary.resolved == 1


# --- A5: the full schema is retried before the minimal one -------------------


def test_the_pm_retries_the_full_schema_before_the_minimal_one():
    decision = PortfolioDecision(
        rating=PortfolioRating.HOLD,
        executive_summary="Hold.",
        investment_thesis="Balanced.",
    )
    full = MagicMock()
    full.invoke.side_effect = [ValueError("no parsed result"), decision]
    minimal = MagicMock()
    llm = MagicMock()
    llm.with_structured_output.side_effect = lambda schema, **kw: (
        full if schema is PortfolioDecision else minimal
    )

    result = create_portfolio_manager(llm)(_pm_state(research_model_source="built"))

    assert full.invoke.call_count == 2
    assert "structured_fallbacks" not in result
    assert "**Rating**: Hold" in result["final_trade_decision"]


# --- A12: the downgrade note must name the reason that fired -----------------


def _trader_state(portfolio: str) -> dict:
    return {
        "company_of_interest": "TEST",
        "trade_date": "2026-01-01",
        "market_report": "",
        "investment_plan": "plan",
        "portfolio_context": portfolio,
        "research_model_source": "unavailable",
        "research_package": "",
    }


def test_the_trader_does_not_claim_a_missing_portfolio_when_one_was_supplied():
    proposal = TraderProposal(new_investor_action=NewInvestorAction.INITIATE, holder_action=HolderAction.ADD, entry_condition="A close above resistance.", deterioration_condition="A close below support.")
    structured = MagicMock()
    structured.invoke.return_value = proposal
    llm = MagicMock()
    llm.with_structured_output.return_value = structured

    plan = create_trader(llm)(_trader_state("Portfolio: 100 units of TEST."))[
        "trader_investment_plan"
    ]

    assert "the valuation is unverified." in plan
    assert "no portfolio context" not in plan


# --- Sizing integrity: a number must actually be a position instruction ------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("No size is prescribed", False),
        ("Size conservatively", False),
        ("Use a smaller position", False),
        ("Hold current exposure", False),
        ("Do not add", False),
        ("Position sizing is constrained by volatility", False),
        ("Position size | ATR ~820 (~10.2% of price)", False),
        ("Use a 2% position", True),
        ("Reduce by 50bp", True),
        ("Sell one-third", True),
        ("Buy 100 shares", True),
        ("Invest ¥1 million", True),
    ],
)
def test_sizing_integrity_needs_a_numerical_instruction(text, expected):
    assert has_exact_size(text) is expected


def test_a_valuation_multiple_is_not_a_position_size():
    assert not has_exact_size("The stock trades at 39.8x FY3/28 diluted EPS.")
    assert has_exact_size("Scale to 1.5x")
    assert has_exact_size("Trim to 1.0x standard neutral allocation")


# --- The report header carries the deterministic model state -----------------


def test_the_header_prints_model_status_version_and_share_bases():
    from tradingagents.reporting import _header

    state = {
        "trade_date": "2026-11-03",
        "model_status": "VERIFIED",
        "active_model_version": "v2",
        "repairs_applied": 2,
        "model_repair_log": '{"changes": [{"field": "FY3/28 EPS (diluted)"}, '
                            '{"field": "FY3/28 fair value"}]}',
        "unresolved_blocking": 0,
        "unresolved_material": 1,
        "market_cap_basis": "period_end_net",
        "forecast_eps_basis": "diluted_average",
        "dilution_sensitivity_basis": "full_conversion",
        "controlling_fair_value_low": 6841.0,
        "controlling_fair_value_base": 8029.0,
        "controlling_fair_value_high": 8551.0,
        "decision_integrity": "validated",
    }

    header = _header("4062.T", state, None)

    assert "- Model status: VERIFIED" in header
    assert "- Active model version: v2" in header
    assert "- Repairs applied: 2 — FY3/28 EPS (diluted), FY3/28 fair value" in header
    assert "Controlling model: v2. Repairs applied: 2" in header
    assert "market cap period_end_net; forecast EPS diluted_average" in header
    assert "dilution sensitivity full_conversion" in header
    assert "- Controlling fair value: 6,841–8,551 (base 8,029)" in header
