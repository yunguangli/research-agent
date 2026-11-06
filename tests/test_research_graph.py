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


def test_prompt_sends_the_vendor_statement_tools_to_fallback():
    """Corpus first: the documents are the primary evidence, Bloomberg only
    fills the periods they never state."""
    Recorder.prompts = []
    create_fundamentals_analyst(Recorder(), tools=[*TOOLS, *CORPUS_TOOLS])(_state(PACKAGE))
    prompt = _system_prompt(Recorder.prompts[0])
    assert "FALLBACK" in prompt
    assert "a number stated in a document wins" in prompt


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


def test_every_template_placeholder_is_filled_from_the_run():
    """The 24-section template is rendered, never shipped with {{…}} intact,
    and the sections that carry the contract reach the model."""
    Recorder.prompts = []
    state = _state(PACKAGE)
    state["decision_horizon"] = "3-6 months"
    state["portfolio_context"] = (
        "Portfolio at the analysis date:\n"
        "- Current position in TEST: 500 units, average price 7,980.00\n"
        "- Cash available: 1,000.00 USD"
    )
    create_fundamentals_analyst(Recorder(), tools=[*TOOLS, *CORPUS_TOOLS])(state)
    prompt = _system_prompt(Recorder.prompts[0])
    assert "{{" not in prompt and "}}" not in prompt
    # runtime context filled from state
    assert "Analysis date: 2026-10-28" in prompt
    assert "Instrument: TEST" in prompt
    assert "Decision horizon: 3-6 months" in prompt
    assert "Current position: 500 units" in prompt
    assert "Average cost: 7,980.00" in prompt
    # the output contract arrived intact
    assert "1. RUNTIME CONTEXT" in prompt
    assert "## 1. Executive conclusion" in prompt
    assert "## 14. Summary table" in prompt
    assert "24. FINAL QUALITY CHECK" in prompt
    # the tools actually bound to this node are the ones listed
    assert "get_fundamentals(" in prompt and "list_research_documents(" in prompt


def test_the_investor_context_stays_null_without_a_portfolio_or_horizon():
    Recorder.prompts = []
    create_fundamentals_analyst(Recorder())(_state())
    prompt = _system_prompt(Recorder.prompts[0])
    assert "Decision horizon: null" in prompt
    assert "Current position: null" in prompt
    # no corpus, no model: their blocks are null, not half-present
    assert "Available research corpus:\nnull" in prompt
    assert "Available normalized financial dataset or verified model:\nnull" in prompt


def test_another_companys_spec_is_neither_model_nor_consensus(monkeypatch, tmp_path):
    """A leftover TRADINGAGENTS_RESEARCH_MODEL from an earlier test (Fujikura
    during an ASE run) must supply neither the model nor its consensus."""
    from tradingagents.graph import trading_graph as tg

    from .test_research_kernel import spec as kernel_spec

    path = tmp_path / "other.json"
    data = kernel_spec().to_dict()
    data["ticker"] = "5803.T"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("TRADINGAGENTS_RESEARCH_MODEL", str(path))

    assert tg._configured_spec("3711.TW") is None       # another company
    assert tg._configured_spec("5803.T") is not None    # its own company

    monkeypatch.setenv("TRADINGAGENTS_RESEARCH_MODEL", str(tmp_path / "missing.json"))
    assert tg._configured_spec("5803.T") is None        # unreadable pointer ignored


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
        "bridges": [
            {
                "metric": "revenue", "base_period": "FY25", "target_period": "FY26",
                "base_value": 120.0, "declared_total": 130.0,
                "lines": [{"label": "New business", "value": 10.0}],
            },
            # A forecast year carries its topline: revenue and operating profit
            # are bridged, net profit alone would be an orphan (kernel rule).
            {
                "metric": "operating_profit", "base_period": "FY25", "target_period": "FY26",
                "base_value": 15.0, "declared_total": 16.0,
                "lines": [{"label": "New business", "value": 1.0}],
            },
        ],
        "share_bases": [
            {"name": "basic", "shares_m": 10.0},
            {"name": "diluted", "shares_m": 10.5,
             "basic_weighted_average": 10.0, "diluted_weighted_average": 10.5},
        ],
        "valuation": {
            "price": 20.0, "fair_value_multiple_low": 10.0, "fair_value_multiple_high": 12.0,
            "market_cap_basis": "basic", "forecast_eps_basis": "diluted",
            "dilution_sensitivity_basis": "diluted",
        },
        "eps_basis": "basic",
        "_op_override": revenue_op,
    }


def _graph():
    """A graph instance without the LLM clients: these methods only read the env.

    Comparison purpose: a spec without a manifest is reusable for a diagnostic
    read; a production run would rebuild it first.
    """
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {"run_purpose": "comparison"}
    return graph


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
    main = (tmp_path / "complete_report.md").read_text(encoding="utf-8")
    assert "## Forecast and Valuation" in main
    assert "- FY26 (forecast): revenue 130.0" in main
    assert "Valuation: price 20 USD" in main

    plain = tmp_path / "plain"
    write_report_tree({"fundamentals_report": "f"}, "TEST", plain, html=False)
    plain_main = (plain / "complete_report.md").read_text(encoding="utf-8")
    assert PACKAGE.strip()[:20] not in plain_main
    assert "No usable model in this run." in plain_main


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


def test_save_reports_says_why_the_audit_did_not_run_without_a_model(tmp_path):
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {}
    graph.selected_analysts = ("fundamentals",)
    out = tmp_path / "out"
    graph.save_reports({"fundamentals_report": "FY26 revenue 999.0"}, "TEST", save_path=out, html=False)
    audit = (out / "1c_audit" / "numbers.md").read_text(encoding="utf-8")
    assert "no research corpus is configured" in audit
    assert "999" not in audit


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


def test_the_audit_section_says_it_did_not_run_when_no_model_is_usable(
    monkeypatch, tmp_path
):
    """A missing audit file reads like a clean audit; the section must say why."""
    from tradingagents.graph.trading_graph import _ModelUnavailable

    monkeypatch.setenv("TRADINGAGENTS_RESEARCH_DIR", str(tmp_path))
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {"data_cache_dir": str(tmp_path)}
    graph.quick_thinking_llm = None

    def no_model(*args, **kwargs):
        raise _ModelUnavailable(
            "MODEL UNAVAILABLE: the spec at 4062.T.json failed its checks "
            "(scenario Base is on another year)"
        )

    monkeypatch.setattr(graph, "_research_model", no_model)
    text = graph.report_audit({"trade_date": "2026-11-02"}, "4062.T")

    assert "did not run" in text
    assert "no usable research model" in text
    assert "failed its checks" in text
    assert "\n" not in text          # one line: it is a report section, not a stack trace


def test_prepare_final_state_marks_the_unavailable_model_in_the_header(
    monkeypatch, tmp_path
):
    """The audit section and the header must not disagree about the model."""
    from tradingagents.graph.trading_graph import _ModelUnavailable

    monkeypatch.setenv("TRADINGAGENTS_RESEARCH_DIR", str(tmp_path))
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {"data_cache_dir": str(tmp_path)}

    def no_model(*args, **kwargs):
        raise _ModelUnavailable("MODEL UNAVAILABLE: the spec failed its checks")

    monkeypatch.setattr(graph, "_research_model", no_model)
    state = {"trade_date": "2026-11-02", "research_model_path": ""}

    graph.prepare_final_state(state, "4062.T")

    assert state["research_model_source"] == "unavailable"
    assert state["spec_compatibility"] == "unavailable"
    assert "1c_audit" in state["spec_compatibility_reason"]


def test_the_case_table_can_be_rebuilt_from_the_review_transcript(monkeypatch, tmp_path):
    """A state without debate_cases still publishes the cases its review appended."""
    from tradingagents.research import kernel
    from tradingagents.research.schemas import (
        Financials,
        ModelSpec,
        Period,
        Scenario,
        ShareBase,
        ValuationSpec,
    )
    from tradingagents.research.valuation import value

    spec = ModelSpec(
        ticker="TEST", company="Test", currency="USD", unit="USD_m",
        fiscal_year_end_month=12,
        periods=[Period("FY24", "2024-01-01", "2024-12-31", "actual"),
                 Period("FY25", "2025-01-01", "2025-12-31", "forecast")],
        actuals={"FY24": Financials(revenue=100.0, operating_profit=10.0, net_profit=6.0)},
        guidance={},
        forecast={"FY25": Financials(revenue=120.0, operating_profit=15.0, net_profit=9.0)},
        bridges=[],
        share_bases=[ShareBase("basic", 10.0)],
        scenarios=[Scenario("Base", "steady", eps=0.9, multiple=10.0, period="FY25"),
                   Scenario("Bear", "weak", eps=0.8, multiple=10.0, period="FY25")],
        valuation=ValuationSpec(price=9.0, fair_value_multiple_low=10.0,
                                fair_value_multiple_high=12.0, fair_value_period="FY25"),
    )
    model = kernel.run(spec)
    valuation = value(model)

    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {"data_cache_dir": str(tmp_path)}
    monkeypatch.setattr(graph, "_research_model", lambda *a, **k: (model, valuation, "spec.json"))
    state = {
        "investment_debate_state": {
            "challenge_history": (
                'review\n{"stance": "aggressive", "probability": 0.3, "revenue_pct": 1.0, '
                '"operating_profit_pct": 1.0, "multiple": 11.0}'
            )
        }
    }

    graph.prepare_final_state(state, "TEST")

    assert "aggressive" in state["case_table"]
    assert state["debate_cases"]


def test_prepare_final_state_builds_the_compact_debate_summary():
    """Round 1 for the reader, later rounds only as material deltas."""
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {}
    state = {
        "investment_debate_state": {
            "first_review": "ROUND ONE",
            "last_review": "DELTA",
            "count": 2,
            "unresolved_issue": "Q3 operating profit",
        }
    }

    graph.prepare_final_state(state, "TEST")

    summary = state["debate_summary"]
    assert summary.startswith("### Round 1")
    assert "ROUND ONE" in summary
    assert "### Subsequent Material Update" in summary
    assert "DELTA" in summary
    assert "### Remaining Unresolved Issue" in summary
    assert "Q3 operating profit" in summary
    assert state["research_rounds_used"] == 2


def test_a_no_material_update_delta_is_not_published_as_a_subsequent_update():
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {}
    state = {
        "investment_debate_state": {
            "first_review": "ROUND ONE",
            "last_review": "NO MATERIAL UPDATE",
            "count": 2,
            "unresolved_issue": "Q3",
        }
    }

    graph.prepare_final_state(state, "TEST")

    assert "Subsequent Material Update" not in state["debate_summary"]


def test_a_missing_case_table_is_reported_not_silently_omitted():
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {}
    state = {
        "investment_debate_state": {"first_review": "ROUND ONE"},
        "research_model_source": "built",
    }

    graph.prepare_final_state(state, "TEST")

    assert "No case table" in state["case_table"]


def test_the_audit_does_not_run_when_no_model_was_used(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGAGENTS_RESEARCH_DIR", str(tmp_path))
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {"data_cache_dir": str(tmp_path)}
    called = []
    monkeypatch.setattr(graph, "_research_model", lambda *a, **k: called.append(1))
    state = {"research_model_source": "unavailable", "trade_date": "2026-11-03"}

    graph.prepare_final_state(state, "TEST")

    assert called == []                       # no old spec is opened for the audit
    assert "did not run" in state["report_audit"]
    assert "no research model was used" in state["report_audit"]


def test_conflicting_intermediate_decisions_are_named():
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {}
    state = {
        "final_rating": "Hold",
        "final_trade_decision": "**Rating**: Hold\n\n| New investor | Wait |",
        "investment_plan": "**Recommendation**: Buy\n\nBull case.",
        "trader_investment_plan": "**Action**: Buy\n\nBuy now.",
        "portfolio_context": "",
    }

    graph.prepare_final_state(state, "TEST")

    assert any("Research Manager" in item for item in state["decision_conflicts"])
    assert any("Trader proposed Buy" in item for item in state["decision_conflicts"])
    assert "**Superseded intermediate decisions**" in state["final_trade_decision"]
    assert state["stage_status"] == {
        "research_manager": "completed_appendix_only",
        "trader": "completed_appendix_only",
        "portfolio_manager": "completed_main_report",
    }
    assert state["controlling_decision"] == "portfolio_manager"


def test_the_stale_model_is_rendered_for_comparison_only(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGAGENTS_RESEARCH_DIR", str(tmp_path))
    (tmp_path / "model.json").write_text(json.dumps(_spec(tmp_path)), encoding="utf-8")
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {"data_cache_dir": str(tmp_path / "cache")}

    path, package = graph._stale_model_reference("TEST", "2026-11-03")

    assert path.endswith("model.json")
    assert "STALE MODEL" in package
    assert "NOT USED" in package


def test_a_used_model_keeps_its_provenance_when_the_audit_cannot_run(monkeypatch, tmp_path):
    """The model was loaded and its figures used; a later audit failure is not an outage."""
    monkeypatch.setenv("TRADINGAGENTS_RESEARCH_DIR", str(tmp_path))
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {"data_cache_dir": str(tmp_path)}
    monkeypatch.setattr(
        graph,
        "report_audit",
        lambda *a, **k: "The number audit did not run: no usable research model was available.",
    )
    state = {
        "trade_date": "2026-11-03",
        "research_model_source": "reused",
        "spec_compatibility": "compatible",
        "research_model_path": "C:/x/4062.T.json",
    }

    graph.prepare_final_state(state, "4062.T")

    assert state["research_model_source"] == "reused"
    assert state["spec_compatibility"] == "compatible"
    assert state["report_audit"].startswith("The number audit did not run")


def test_a_fallback_decision_is_marked_degraded_and_its_sizing_qualified():
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {}
    state = {
        "final_trade_decision": "**Rating**: Underweight\n\nTrim to 2.5% of the sleeve.",
        "trader_investment_plan": "Reduce to 50% in two tranches.",
        "structured_fallbacks": {"Trader": "ValueError: no parsed result"},
        "portfolio_context": "",
    }

    graph._apply_decision_integrity(state)

    assert state["decision_integrity"].startswith("DEGRADED")
    assert "Trader" in state["decision_integrity"]
    assert "**Sizing note**" in state["final_trade_decision"]
    assert "**Decision integrity**" in state["final_trade_decision"]

    before = state["final_trade_decision"]
    graph._apply_decision_integrity(state)
    assert state["final_trade_decision"] == before      # idempotent


def test_a_clean_decision_is_marked_validated():
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {}
    state = {
        "final_trade_decision": "**Rating**: Hold\n\nNo position change.",
        "trader_investment_plan": "Hold.",
        "portfolio_context": "Current position in NVDA: 100 units",
    }

    graph._apply_decision_integrity(state)

    assert state["decision_integrity"] == "validated"
    assert "**Decision integrity**" not in state["final_trade_decision"]




