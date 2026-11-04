"""Report parity: the shared writer produces the report tree for the CLI and the
programmatic API alike (#1037)."""

import re
from types import SimpleNamespace

import pytest

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.reporting import write_report_tree


def _state():
    return {
        "market_report": "MKT",
        "news_report": "NEWS",
        "investment_debate_state": {"challenge_history": "REVIEW"},
        "investment_plan": "RM PLAN",
        "trader_investment_plan": "TRADE",
        "risk_debate_state": {"neutral_history": "NEUTRAL"},
        "final_trade_decision": "PM DECISION",
    }


SETTINGS = {"version": "0.5.2", "llm_provider": "openai", "deep_think_llm": "gpt-6-sol",
            "quick_think_llm": "gpt-6-luna", "analysts": ["market", "news"], "max_debate_rounds": 1,
            "max_risk_discuss_rounds": 2, "data_vendors": {"core_stock_apis": "yfinance"}}


@pytest.mark.unit
def test_write_report_tree_creates_files(tmp_path):
    out = write_report_tree(_state(), "AAPL", tmp_path)
    assert out.name == "complete_report.md"
    assert (tmp_path / "1_analysts" / "market.md").read_text() == "MKT"
    assert (tmp_path / "1_analysts" / "news.md").read_text() == "NEWS"
    assert (tmp_path / "2_research" / "challenge.md").read_text() == "REVIEW"
    assert (tmp_path / "2_research" / "manager.md").read_text() == "RM PLAN"
    assert (tmp_path / "3_trading" / "trader.md").read_text() == "TRADE"
    assert (tmp_path / "5_portfolio" / "decision.md").read_text() == "PM DECISION"
    complete = out.read_text()
    assert "Trading Analysis Report: AAPL" in complete
    assert "MKT" in complete and "PM DECISION" in complete


@pytest.mark.unit
def test_the_published_package_prefers_the_artifact_rendering(tmp_path):
    """The prompt's imperative lines must not reach the reader (#finding B3)."""
    state = _state()
    state["research_package"] = "PROMPT TEXT\nDo not recalculate these figures."
    state["research_package_artifact"] = "MODEL TEXT"
    write_report_tree(state, "AAPL", tmp_path)
    published = (tmp_path / "1b_research" / "research_package.md").read_text()
    assert published == "MODEL TEXT"


@pytest.mark.unit
def test_a_state_saved_before_the_split_still_publishes_the_package(tmp_path):
    """A checkpoint from an older version has only the prompt rendering."""
    state = _state()
    state["research_package"] = "MODEL TEXT"
    write_report_tree(state, "AAPL", tmp_path)
    assert (tmp_path / "1b_research" / "research_package.md").read_text() == "MODEL TEXT"


@pytest.mark.unit
def test_the_header_discloses_a_diagnostic_run_and_the_spec_source(tmp_path):
    state = _state()
    state.update({
        "research_model_path": "C:/x/4062.T.json",
        "research_model_source": "reused",
        "research_model_created_at": "2026-11-02T11:45:46",
        "spec_compatibility": "unknown",
    })
    write_report_tree(
        state, "AAPL", tmp_path, settings={**SETTINGS, "run_purpose": "comparison"}
    )
    header = (tmp_path / "complete_report.md").read_text()
    assert "Authority: diagnostic" in header
    assert "reused from C:/x/4062.T.json" in header
    assert "compatibility unknown" in header


@pytest.mark.unit
def test_the_main_report_excludes_the_debate_and_audit(tmp_path):
    state = _state()
    state["investment_debate_state"]["challenge_history"] = "ADVERSARIAL-ONLY"
    state["risk_debate_state"]["neutral_history"] = "RISK-ONLY"
    state["report_audit"] = "AUDIT"
    state["case_table"] = "CASES"
    write_report_tree(state, "AAPL", tmp_path)
    main = (tmp_path / "complete_report.md").read_text()
    assert "ADVERSARIAL-ONLY" not in main
    assert "RISK-ONLY" not in main
    assert "AUDIT" not in main
    appendix = (tmp_path / "appendix.md").read_text()
    assert "ADVERSARIAL-ONLY" in appendix
    assert "RISK-ONLY" in appendix
    assert "AUDIT" in appendix
    assert "CASES" in appendix


@pytest.mark.unit
def test_the_header_shows_rounds_run_not_only_the_configured_maximum(tmp_path):
    state = _state()
    state["research_rounds_used"] = 2
    state["risk_rounds_used"] = 1
    write_report_tree(state, "AAPL", tmp_path, settings={**SETTINGS, "max_debate_rounds": 3,
                                                          "max_risk_discuss_rounds": 3})
    header = (tmp_path / "complete_report.md").read_text()
    assert "research debate rounds: 2 of 3" in header
    assert "risk debate rounds: 1 of 3" in header


@pytest.mark.unit
def test_a_degraded_decision_is_labelled_provisional_in_the_header(tmp_path):
    state = _state()
    state["final_trade_decision"] = "**Rating**: Underweight\n\nReduce."
    state["decision_integrity"] = "DEGRADED: structured output fell back to free text for Trader"
    write_report_tree(state, "AAPL", tmp_path)
    header = (tmp_path / "complete_report.md").read_text()
    assert "- Rating: Underweight (provisional)" in header


@pytest.mark.unit
def test_the_header_discloses_decision_integrity(tmp_path):
    state = _state()
    state["decision_integrity"] = "DEGRADED: structured output fell back to free text for Trader"
    write_report_tree(state, "AAPL", tmp_path)
    header = (tmp_path / "complete_report.md").read_text()
    assert "Decision integrity: DEGRADED" in header
    assert "Trader" in header


@pytest.mark.unit
def test_the_header_names_an_unavailable_research_model(tmp_path):
    """A rejected spec is the research model, not the LLM; the header must say so."""
    state = _state()
    state.update({
        "research_model_source": "unavailable",
        "spec_compatibility": "unavailable",
        "spec_compatibility_reason": (
            "MODEL UNAVAILABLE: the model spec at 4062.T.json failed its checks"
        ),
    })
    write_report_tree(state, "AAPL", tmp_path)
    header = (tmp_path / "complete_report.md").read_text()
    assert "Research model: UNAVAILABLE" in header
    assert "not the LLM" in header
    assert "failed its checks" in header


@pytest.mark.unit
def test_saving_twice_writes_the_same_sections(tmp_path):
    state = _state()
    write_report_tree(state, "AAPL", tmp_path)
    first = (tmp_path / "1_analysts" / "market.md").read_bytes()
    write_report_tree(state, "AAPL", tmp_path)
    assert (tmp_path / "1_analysts" / "market.md").read_bytes() == first


@pytest.mark.unit
def test_save_reports_explicit_path(tmp_path):
    graph = SimpleNamespace(run_settings=lambda: SETTINGS)
    out = TradingAgentsGraph.save_reports(graph, _state(), "AAPL", save_path=tmp_path)
    assert (tmp_path / "complete_report.md").exists()
    assert out == tmp_path / "complete_report.md"


@pytest.mark.unit
def test_save_reports_defaults_under_results_dir(tmp_path):
    graph = object.__new__(TradingAgentsGraph)
    graph.config = {"results_dir": str(tmp_path)}
    graph.run_settings = lambda: SETTINGS
    out = graph.save_reports(_state(), "AAPL")
    assert out.exists()
    assert out.parent.parent.name == "reports"  # results_dir/reports/AAPL_<stamp>/...
    assert out.parent.name.startswith("AAPL_")



@pytest.mark.unit
def test_the_report_names_the_analysis_date_and_what_produced_it(tmp_path):
    state = dict(_state(), trade_date="2026-09-23")

    header = write_report_tree(state, "NVDA", tmp_path, settings=SETTINGS).read_text().split("## ")[0]

    assert "Analysis date: 2026-09-23" in header
    assert "TradingAgents 0.5.2" in header
    assert "openai, deep gpt-6-sol, quick gpt-6-luna" in header
    assert "Analysts: market, news" in header
    assert "research debate rounds: 1" in header
    assert "risk debate rounds: 2" in header
    assert "core_stock_apis yfinance" in header


@pytest.mark.unit
def test_run_settings_record_the_run_without_endpoints_or_paths():
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = object.__new__(TradingAgentsGraph)
    graph.selected_analysts = ("market", "news")
    graph.config = {"llm_provider": "openai", "deep_think_llm": "gpt-6-sol", "quick_think_llm": "gpt-6-luna",
                    "max_debate_rounds": 1, "max_risk_discuss_rounds": 1, "output_language": "English",
                    "data_vendors": {"core_stock_apis": "yfinance"}, "tool_vendors": {},
                    "backend_url": "https://user:secret@relay.example/v1", "results_dir": "/home/me/results"}

    settings = graph.run_settings()

    assert settings["analysts"] == ["market", "news"]
    assert settings["deep_think_llm"] == "gpt-6-sol"
    assert "secret" not in str(settings) and "/home/me" not in str(settings)


@pytest.mark.unit
def test_a_partial_settings_dict_still_writes_the_report(tmp_path):
    out = write_report_tree(_state(), "AAPL", tmp_path, settings={"llm_provider": "openai"})
    assert "openai" in out.read_text()


@pytest.mark.unit
def test_run_settings_name_the_version_of_the_running_code():
    import tradingagents
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = object.__new__(TradingAgentsGraph)
    graph.selected_analysts, graph.config = ("market",), {}
    assert graph.run_settings()["version"] == tradingagents.__version__


@pytest.mark.unit
@pytest.mark.parametrize("decision, rating", [
    ("**Rating**: Overweight\n\nAdd on weakness.", "Overweight"),
    ("Add on weakness.", "REVIEW"),           # no readable rating is said, not left out
])
def test_the_report_header_names_the_rating(tmp_path, decision, rating):
    state = dict(_state(), final_trade_decision=decision)

    header = write_report_tree(state, "NVDA", tmp_path, settings=SETTINGS).read_text().split("## ")[0]

    assert f"- Rating: {rating}" in header


@pytest.mark.unit
def test_the_header_names_the_analysts_as_users_select_them(tmp_path):
    settings = {**SETTINGS, "analysts": ["market", "social", "news"]}

    write_report_tree(_state(), "NVDA", tmp_path, settings=settings, html=True)

    markdown = (tmp_path / "complete_report.md").read_text(encoding="utf-8")
    assert "market, sentiment, news" in markdown and "social" not in markdown
    page = (tmp_path / "complete_report.html").read_text(encoding="utf-8")
    fields = re.findall(r'<span class="field">([^<]*)</span>', page)
    assert {"market", "sentiment", "news"} <= set(fields) and "social" not in page
