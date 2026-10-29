"""The research corpus is wired into the Fundamentals Analyst, and only there.

Vanilla runs must stay identical: no corpus tools, no extra state, no extra
report section, and an unchanged checkpoint signature.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from tradingagents.agents.analysts.fundamentals_analyst import (
    CORPUS_TOOL_NAMES,
    TOOLS,
    create_fundamentals_analyst,
)
from tradingagents.agents.tools import CORPUS_TOOLS
from tradingagents.graph import setup as graph_setup_module
from tradingagents.graph.conditional_logic import ConditionalLogic
from tradingagents.graph.setup import GraphSetup
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.reporting import write_report_tree

PACKAGE = (
    "FUNDAMENTAL MODEL â€” Test Corp (TEST) â€” model 1.0.0\n"
    "- FY26 (forecast): revenue 130.0, operating profit 16.0, margin 12.3%, net profit 10.0\n"
    "Valuation: price 20 USD, FY26 EPS 1.0, P/E 20.0x, fair value 10-12 USD."
)


class Recorder(BaseChatModel):
    """Records the prompt it was given, then answers with a report."""

    prompts: list = []

    @property
    def _llm_type(self) -> str:
        return "recorder"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.__class__.prompts.append(messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="fundamental report"))])


def _state(package: str = "") -> dict:
    return {
        "trade_date": "2026-10-28",
        "company_of_interest": "TEST",
        "instrument_context": "",
        "research_package": package,
        "messages": [],
    }


def _system_prompt(messages) -> str:
    return "\n".join(str(getattr(message, "content", "")) for message in messages)


def test_corpus_tools_are_not_offered_by_default():
    assert {tool.name for tool in CORPUS_TOOLS} == set(CORPUS_TOOL_NAMES)
    assert not ({tool.name for tool in TOOLS} & CORPUS_TOOL_NAMES)


def test_prompt_without_a_corpus_mentions_no_research_tools():
    Recorder.prompts = []
    create_fundamentals_analyst(Recorder())(_state())
    prompt = _system_prompt(Recorder.prompts[0])
    assert "list_research_documents" not in prompt
    assert "VERIFIED MODEL" not in prompt


def test_prompt_with_a_corpus_teaches_citation_and_internal_notes():
    Recorder.prompts = []
    create_fundamentals_analyst(Recorder(), tools=[*TOOLS, *CORPUS_TOOLS])(_state())
    prompt = _system_prompt(Recorder.prompts[0])
    assert "list_research_documents" in prompt
    assert "internal_note" in prompt
    assert "withheld" in prompt
    assert "document_id" in prompt


def test_prompt_says_the_model_beats_a_conflicting_vendor_snapshot():
    Recorder.prompts = []
    create_fundamentals_analyst(Recorder(), tools=[*TOOLS, *CORPUS_TOOLS])(_state(PACKAGE))
    prompt = _system_prompt(Recorder.prompts[0])
    assert "the verified model wins" in prompt
    assert "conflicting snapshot" in prompt


def test_prompt_warns_about_the_filings_prior_period_column():
    Recorder.prompts = []
    create_fundamentals_analyst(Recorder(), tools=[*TOOLS, *CORPUS_TOOLS])(_state(PACKAGE))
    prompt = _system_prompt(Recorder.prompts[0])
    assert "prior period" in prompt
    assert "Do not re-read the filing's tables" in prompt


def test_citations_are_told_to_avoid_the_figure_marker():
    """[[6]] reads as the figure 6, so a citation written that way is audited —
    and reported — as a wrong number. The prompt has to say so."""
    Recorder.prompts = []
    create_fundamentals_analyst(Recorder(), tools=[*TOOLS, *CORPUS_TOOLS])(_state(PACKAGE))
    prompt = _system_prompt(Recorder.prompts[0])
    assert "single square brackets" in prompt
    assert "[[6]]" in prompt
    assert "Documents cited" in prompt


def test_the_unit_rule_converts_hundred_million_yen():
    Recorder.prompts = []
    create_fundamentals_analyst(Recorder(), tools=[*TOOLS, *CORPUS_TOOLS])(_state(PACKAGE))
    prompt = _system_prompt(Recorder.prompts[0])
    assert "100 million yen" in prompt
    assert "reconcile each year-on-year change" in prompt


def test_the_verified_model_is_injected_and_must_not_be_recalculated():
    Recorder.prompts = []
    create_fundamentals_analyst(Recorder(), tools=[*TOOLS, *CORPUS_TOOLS])(_state(PACKAGE))
    prompt = _system_prompt(Recorder.prompts[0])
    assert "VERIFIED MODEL" in prompt
    assert "revenue 130.0" in prompt
    assert "do not recompute" in prompt


def test_graph_setup_adds_corpus_tools_to_fundamentals_only(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGAGENTS_RESEARCH_DIR", str(tmp_path))
    captured: dict[str, list[str]] = {}

    def fake_analyst_graph(spec, agent, max_tool_rounds, tools=None):
        captured[spec.key] = [tool.name for tool in (tools or spec.tools)]
        return lambda state: {}

    monkeypatch.setattr(graph_setup_module, "_analyst_graph", fake_analyst_graph)
    GraphSetup(object(), object(), ConditionalLogic(), max_tool_rounds=5).setup_graph(
        ("market", "fundamentals")
    )

    assert set(CORPUS_TOOL_NAMES) <= set(captured["fundamentals"])
    assert not (set(CORPUS_TOOL_NAMES) & set(captured["market"]))


def test_graph_setup_is_unchanged_without_a_corpus(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_RESEARCH_DIR", raising=False)
    captured: dict[str, list[str]] = {}

    def fake_analyst_graph(spec, agent, max_tool_rounds, tools=None):
        captured[spec.key] = [tool.name for tool in (tools or spec.tools)]
        return lambda state: {}

    monkeypatch.setattr(graph_setup_module, "_analyst_graph", fake_analyst_graph)
    GraphSetup(object(), object(), ConditionalLogic(), max_tool_rounds=5).setup_graph(
        ("fundamentals",)
    )
    assert not (set(CORPUS_TOOL_NAMES) & set(captured["fundamentals"]))


def _spec(path, revenue_op=410.0):
    return {
        "ticker": "TEST",
        "company": "Test Corp",
        "currency": "USD",
        "unit": "USD_m",
        "fiscal_year_end_month": 12,
        "periods": [
            {"label": "FY25", "start": "2025-01-01", "end": "2025-12-31", "basis": "guidance"},
            {"label": "FY26", "start": "2026-01-01", "end": "2026-12-31", "basis": "forecast"},
        ],
        "guidance": {"FY25": {"revenue": 120.0, "operating_profit": 15.0, "net_profit": 9.0}},
        "forecast": {"FY26": {"net_profit": 10.0}},
        "bridges": [{
            "metric": "revenue", "base_period": "FY25", "target_period": "FY26",
            "base_value": 120.0, "declared_total": 130.0,
            "lines": [{"label": "New business", "value": 10.0}],
        }],
        "share_bases": [{"name": "basic", "shares_m": 10.0}],
        "valuation": {
            "price": 20.0, "fair_value_multiple_low": 10.0, "fair_value_multiple_high": 12.0,
        },
        "eps_basis": "basic",
        "_op_override": revenue_op,
    }


def _graph():
    """A graph instance without the LLM clients: these methods only read the env."""
    return TradingAgentsGraph.__new__(TradingAgentsGraph)


def test_research_package_is_empty_without_a_corpus(monkeypatch, tmp_path):
    monkeypatch.delenv("TRADINGAGENTS_RESEARCH_DIR", raising=False)
    assert _graph().research_package("TEST") == ""


def test_research_package_renders_the_model(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGAGENTS_RESEARCH_DIR", str(tmp_path))
    (tmp_path / "model.json").write_text(json.dumps(_spec(tmp_path)), encoding="utf-8")

    text = _graph().research_package("TEST")
    assert "FUNDAMENTAL MODEL" in text
    assert "revenue 130.0" in text
    assert "Fair" not in text  # it is a model summary, not the report


def test_a_broken_model_becomes_a_labelled_gap(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGAGENTS_RESEARCH_DIR", str(tmp_path))
    broken = _spec(tmp_path)
    broken["bridges"][0]["declared_total"] = 999.0
    (tmp_path / "model.json").write_text(json.dumps(broken), encoding="utf-8")

    text = _graph().research_package("TEST")
    assert text.startswith("MODEL UNAVAILABLE")
    assert "do not estimate" in text


def test_missing_model_spec_is_reported_not_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGAGENTS_RESEARCH_DIR", str(tmp_path))
    text = _graph().research_package("TEST")
    assert text.startswith("MODEL UNAVAILABLE")


def test_the_report_gains_a_research_section_only_when_the_model_exists(tmp_path):
    write_report_tree({"research_package": PACKAGE, "fundamentals_report": "f"}, "TEST", tmp_path, html=False)
    assert "Fundamental Research Package" in (tmp_path / "complete_report.md").read_text(encoding="utf-8")

    plain = tmp_path / "plain"
    write_report_tree({"fundamentals_report": "f"}, "TEST", plain, html=False)
    assert "Fundamental Research Package" not in (plain / "complete_report.md").read_text(encoding="utf-8")


def test_checkpoint_signature_changes_only_when_a_corpus_is_configured(monkeypatch, tmp_path):
    config = {"max_debate_rounds": 1, "max_risk_discuss_rounds": 1}
    graph = SimpleNamespace(config=config, selected_analysts=("fundamentals",))

    monkeypatch.delenv("TRADINGAGENTS_RESEARCH_DIR", raising=False)
    vanilla = TradingAgentsGraph._run_signature(graph, "stock")

    monkeypatch.setenv("TRADINGAGENTS_RESEARCH_DIR", str(tmp_path))
    with_corpus = TradingAgentsGraph._run_signature(graph, "stock")

    assert vanilla != with_corpus
    assert str(tmp_path) in with_corpus


def test_save_reports_adds_a_number_audit_when_a_model_is_used(monkeypatch, tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "model.json").write_text(json.dumps(_spec(tmp_path)), encoding="utf-8")
    monkeypatch.setenv("TRADINGAGENTS_RESEARCH_DIR", str(corpus))

    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {}
    graph.selected_analysts = ("fundamentals",)
    state = {
        "fundamentals_report": "Forward P/E [[13.51x]] looks cheap against the model.",
        "investment_debate_state": {},
    }
    out = tmp_path / "out"
    graph.save_reports(state, "TEST", save_path=out, html=False)

    audit_text = (out / "1c_audit" / "numbers.md").read_text(encoding="utf-8")
    assert "13.51" in audit_text
    assert "complete_report.md" in [p.name for p in out.iterdir()]


def test_save_reports_has_no_audit_without_a_model(tmp_path):
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {}
    graph.selected_analysts = ("fundamentals",)
    out = tmp_path / "out"
    graph.save_reports({"fundamentals_report": "FY26 revenue 999.0"}, "TEST", save_path=out, html=False)
    assert not (out / "1c_audit").exists()


def test_a_locked_file_returns_a_sentinel_instead_of_crashing_the_run(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGAGENTS_RESEARCH_DIR", str(tmp_path))
    import tradingagents.agents.tools as tool_module

    def locked(*args, **kwargs):
        raise PermissionError(13, "Permission denied", "Fujikura Latest Forecast .docx")

    monkeypatch.setattr(tool_module, "corpus_read_document", locked)
    result = tool_module.read_research_document.func(
        document_id="doc-5803.T-anything", ticker="5803.T", max_chars=100
    )
    assert result.startswith("DATA_UNAVAILABLE")
    assert "Permission denied" in result


def test_last_close_is_read_from_the_vendor_csv():
    from tradingagents.dataflows.prices import parse_last_close

    text = (
        "# Stock data for TEST\n# Total records: 2\n\n"
        ",Date,Open,High,Low,Close,Volume\n"
        "0,2026-10-26,10,11,9,10.5,100\n"
        "1,2026-10-27,10,11,9,10.9,200\n"
    )
    assert parse_last_close(text) == pytest.approx(10.9)
    assert parse_last_close("DATA_UNAVAILABLE: nothing") is None
    assert parse_last_close("") is None


def test_valuation_uses_the_run_dates_close(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGAGENTS_RESEARCH_DIR", str(tmp_path))
    (tmp_path / "model.json").write_text(json.dumps(_spec(tmp_path)), encoding="utf-8")
    import tradingagents.dataflows.prices as prices

    monkeypatch.setattr(prices, "last_close", lambda symbol, as_of, **kwargs: 15.0)
    text = _graph().research_package("TEST", "2026-10-28")

    assert "price 15" in text
    assert "P/E 15.0x" in text
    assert "updated to the 2026-10-28 close" in text


def test_a_missing_market_price_falls_back_and_says_it_may_be_stale(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGAGENTS_RESEARCH_DIR", str(tmp_path))
    (tmp_path / "model.json").write_text(json.dumps(_spec(tmp_path)), encoding="utf-8")
    import tradingagents.dataflows.prices as prices

    monkeypatch.setattr(prices, "last_close", lambda *args, **kwargs: None)
    text = _graph().research_package("TEST", "2026-10-28")
    assert "may be stale" in text


def test_an_explicit_price_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGAGENTS_RESEARCH_DIR", str(tmp_path))
    monkeypatch.setenv("TRADINGAGENTS_MODEL_PRICE", "42")
    (tmp_path / "model.json").write_text(json.dumps(_spec(tmp_path)), encoding="utf-8")
    import tradingagents.dataflows.prices as prices

    monkeypatch.setattr(prices, "last_close", lambda *args, **kwargs: 15.0)
    text = _graph().research_package("TEST", "2026-10-28")
    assert "price 42" in text


class StructuredLess(Recorder):
    def with_structured_output(self, schema, **kwargs):
        raise NotImplementedError


def test_the_research_manager_receives_the_verified_model():
    from tradingagents.agents.managers.research_manager import create_research_manager

    Recorder.prompts = []
    node = create_research_manager(StructuredLess())
    node({
        "company_of_interest": "TEST",
        "instrument_context": "",
        "research_package": PACKAGE,
        "investment_debate_state": {"history": "Bull: strong. Bear: expensive.", "count": 2},
    })
    prompt = _system_prompt(Recorder.prompts[0])
    assert "VERIFIED MODEL" in prompt
    assert "revenue 130.0" in prompt


def test_the_research_manager_prompt_is_unchanged_without_a_model():
    from tradingagents.agents.managers.research_manager import create_research_manager

    Recorder.prompts = []
    create_research_manager(StructuredLess())({
        "company_of_interest": "TEST",
        "instrument_context": "",
        "research_package": "",
        "investment_debate_state": {"history": "Bull: strong.", "count": 2},
    })
    assert "VERIFIED MODEL" not in _system_prompt(Recorder.prompts[0])


def test_the_paths_command_lists_every_location(capsys):
    from types import SimpleNamespace

    from tradingagents.research.__main__ import _paths

    assert _paths(SimpleNamespace(ticker="")) == 0
    printed = capsys.readouterr().out
    for label in ("Reports", "State log", "Memory log", "Model specs", "Vendor cache"):
        assert label in printed


@pytest.mark.parametrize("ticker", ["5803.T", "NVDA"])
def test_corpus_root_resolves_both_layouts(tmp_path, ticker):
    from tradingagents.dataflows.vendors.research_corpus import resolve_corpus_root

    assert resolve_corpus_root(tmp_path, ticker) == tmp_path  # single-company folder
    (tmp_path / ticker).mkdir()
    assert resolve_corpus_root(tmp_path, ticker) == tmp_path / ticker




