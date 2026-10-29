"""The LLM authors the model; Python refuses one that does not tie."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage

from tradingagents.dataflows.vendors.research_corpus import CorpusStore
from tradingagents.research import builder
from tradingagents.research.schemas import ModelError

from .test_research_kernel import spec


class ScriptedLLM:
    def __init__(self, *responses: str):
        self.responses = list(responses)
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return AIMessage(content=self.responses.pop(0))


def _store(tmp_path):
    (tmp_path / "call 20260807.md").write_text("Guidance raised. " * 50, encoding="utf-8")
    return CorpusStore(tmp_path, "TEST")


def _valid() -> str:
    return json.dumps(spec().to_dict())


def _broken() -> str:
    data = spec().to_dict()
    data["bridges"][0]["declared_total"] = 999.0
    return json.dumps(data)


def _build(llm, tmp_path):
    return builder.build_spec(
        llm,
        ticker="TEST",
        company="Test Corp",
        as_of="2026-10-28",
        store=_store(tmp_path),
        facts_text="FY25 revenue 120, operating profit 15",
        price=20.0,
    )


def test_a_valid_model_is_accepted_on_the_first_attempt(tmp_path):
    llm = ScriptedLLM(_valid())
    model, attempts = _build(llm, tmp_path)
    assert attempts == []
    assert llm.calls == 1
    assert model.ticker == "TEST"
    assert model.valuation.price == 20.0


def test_one_failed_check_is_handed_back_and_corrected(tmp_path):
    llm = ScriptedLLM(_broken(), _valid())
    model, attempts = _build(llm, tmp_path)
    assert len(attempts) == 1
    assert "does not sum" in attempts[0]
    assert llm.calls == 2
    assert model.bridges[0].declared_total == 130.0


def test_a_model_that_never_ties_is_rejected(tmp_path):
    llm = ScriptedLLM(_broken(), _broken())
    dump = tmp_path / "failed.json"
    with pytest.raises(ModelError, match="could not build a valid forecast"):
        builder.build_spec(
            llm, ticker="TEST", company="Test Corp", as_of="2026-10-28",
            store=_store(tmp_path), facts_text="facts", price=20.0, dump_path=dump,
        )
    saved = json.loads(dump.read_text(encoding="utf-8"))
    assert "does not sum" in saved["error"]
    assert saved["raw"].startswith("{")


def test_non_json_is_reported_as_such(tmp_path):
    llm = ScriptedLLM("Here is my model: it will grow.", _valid())
    _model, attempts = _build(llm, tmp_path)
    assert "did not return a JSON object" in attempts[0]


def test_code_fences_are_stripped():
    assert builder._extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_a_citation_outside_the_corpus_is_rejected(tmp_path):
    data = json.loads(_valid())
    data["bridges"][0]["lines"][0]["cite"] = [{"document_id": "doc-invented"}]
    llm = ScriptedLLM(json.dumps(data), _valid())
    _model, attempts = _build(llm, tmp_path)
    assert "Unknown document id" in attempts[0]


def _missing_period() -> str:
    data = spec().to_dict()
    data["periods"] = [p for p in data["periods"] if p["label"] != "FY25"]
    return json.dumps(data)


def _delta_total() -> str:
    data = spec().to_dict()
    data["bridges"][0]["declared_total"] = 10.0   # the deltas, not the resulting level
    return json.dumps(data)


def test_a_period_used_but_not_declared_is_declared_for_the_model(tmp_path):
    llm = ScriptedLLM(_missing_period())
    model, attempts = _build(llm, tmp_path)
    assert attempts == []
    assert "FY25" in {period.label for period in model.periods}
    assert any("Declared the FY25 period" in warning for warning in model.warnings)


def test_a_declared_total_equal_to_the_deltas_is_repaired(tmp_path):
    llm = ScriptedLLM(_delta_total())
    model, attempts = _build(llm, tmp_path)
    assert attempts == []
    assert model.bridges[0].declared_total is None
    assert any("equals the sum of the deltas" in warning for warning in model.warnings)


def test_periods_are_ordered_actual_then_guidance_then_forecast(tmp_path):
    llm = ScriptedLLM(_missing_period())
    model, _attempts = _build(llm, tmp_path)
    basis = [period.basis for period in model.periods if period.kind == "annual"]
    assert basis == sorted(basis, key=["actual", "guidance", "forecast"].index)


def test_the_prompt_explains_declared_total(tmp_path):
    store = _store(tmp_path)
    index, excerpts = builder._excerpt_text(store, store.list_documents("2026-10-28"), set())
    prompt = builder.build_prompt(
        ticker="TEST", company="Test Corp", as_of="2026-10-28",
        facts_text="facts", index_text=index, excerpts=excerpts,
        price=20.0, unit="USD_m", currency="USD",
    )
    assert "resulting level after the deltas" in prompt
    assert "must also appear in" in prompt


def test_the_filings_numbers_override_the_models_restatement(tmp_path):
    data = spec().to_dict()
    data["guidance"]["FY25"]["revenue"] = 999.0     # the model misquotes the filing
    llm = ScriptedLLM(json.dumps(data))

    def overlay(model_spec):
        model_spec.guidance["FY25"].revenue = 120.0  # what the filing says

    model, attempts = builder.build_spec(
        llm, ticker="TEST", company="Test Corp", as_of="2026-10-28",
        store=_store(tmp_path), facts_text="facts", price=20.0, overlay=overlay,
    )
    assert attempts == []
    assert model.guidance["FY25"].revenue == 120.0


def test_without_the_overlay_a_misquoted_base_fails(tmp_path):
    data = spec().to_dict()
    data["guidance"]["FY25"]["revenue"] = 999.0
    llm = ScriptedLLM(json.dumps(data), json.dumps(data))
    with pytest.raises(ModelError, match="could not build a valid forecast"):
        _build(llm, tmp_path)


def test_a_documented_line_without_a_citation_is_rejected(tmp_path):
    data = json.loads(_valid())
    data["bridges"][0]["lines"][0] = {
        "label": "From the transcript", "value": 10.0, "provenance": "internal_note",
    }
    llm = ScriptedLLM(json.dumps(data), _valid())
    _model, attempts = _build(llm, tmp_path)
    assert "cite no document" in attempts[0]


def test_an_assumption_needs_no_citation(tmp_path):
    data = json.loads(_valid())
    data["bridges"][0]["lines"][0]["provenance"] = "assumption"
    model, attempts = _build(ScriptedLLM(json.dumps(data)), tmp_path)
    assert attempts == []
    assert model.bridges[0].lines[0].provenance == "assumption"


def test_the_prompt_requires_attributable_net_profit(tmp_path):
    store = _store(tmp_path)
    index, excerpts = builder._excerpt_text(store, store.list_documents("2026-10-28"), set())
    prompt = builder.build_prompt(
        ticker="TEST", company="Test Corp", as_of="2026-10-28",
        facts_text="facts", index_text=index, excerpts=excerpts,
        price=20.0, unit="USD_m", currency="USD",
    )
    assert "attributable to owners of the parent" in prompt


def test_a_single_scenario_is_rejected(tmp_path):
    data = spec().to_dict()
    data["scenarios"] = data["scenarios"][:1]
    llm = ScriptedLLM(json.dumps(data), _valid())
    _model, attempts = _build(llm, tmp_path)
    assert "at least Bear, Base and Bull" in attempts[0]


def test_the_prompt_requires_three_scenarios(tmp_path):
    store = _store(tmp_path)
    index, excerpts = builder._excerpt_text(store, store.list_documents("2026-10-28"), set())
    prompt = builder.build_prompt(
        ticker="TEST", company="Test Corp", as_of="2026-10-28",
        facts_text="facts", index_text=index, excerpts=excerpts,
        price=20.0, unit="USD_m", currency="USD",
    )
    assert "Bear, Base and Bull" in prompt
    assert "億円" in prompt


def test_a_supplied_consensus_overrides_the_models_own(tmp_path):
    from tradingagents.research.schemas import Consensus

    data = spec().to_dict()
    data["consensus"] = {"source": "model's own", "periods": {"FY26": {"eps": 1.0}}}
    supplied = Consensus("Bloomberg", eps_basis="company_style",
                         periods={"FY26": {"eps": 99.9}})
    model, _attempts = builder.build_spec(
        ScriptedLLM(json.dumps(data)),
        ticker="TEST", company="Test Corp", as_of="2026-10-28",
        store=_store(tmp_path), facts_text="facts", price=20.0, consensus=supplied,
    )
    assert model.consensus.source == "Bloomberg"
    assert model.consensus.periods["FY26"]["eps"] == 99.9


def test_key_figure_passages_are_in_the_prompt(tmp_path):
    root = tmp_path / "c"
    root.mkdir()
    (root / "results 20260514.md").write_text(
        "Consolidated results\nNet profit for the year was 157.2 billion yen.\n",
        encoding="utf-8",
    )
    prompt = builder.build_prompt(
        ticker="TEST", company="Test Corp", as_of="2026-10-28",
        facts_text="facts", index_text="idx", excerpts="ex", price=20.0,
        unit="USD_m", currency="USD", figures="Net profit 157.2",
    )
    assert "KEY FIGURE PASSAGES" in prompt
    assert "157.2" in prompt


def test_the_prompt_carries_the_facts_and_forbids_other_sources(tmp_path):
    store = _store(tmp_path)
    index, excerpts = builder._excerpt_text(store, store.list_documents("2026-10-28"), set())
    prompt = builder.build_prompt(
        ticker="TEST", company="Test Corp", as_of="2026-10-28",
        facts_text="FY25 revenue 120", index_text=index, excerpts=excerpts,
        price=20.0, unit="USD_m", currency="USD",
    )
    assert "FY25 revenue 120" in prompt
    assert "cite only these" in prompt.lower()
    assert "20 USD" in prompt
    assert "Return JSON only" in prompt
