"""A debate case is a bounded set of deltas on the model, not a rival forecast."""

from __future__ import annotations

import json

import pytest

from tradingagents.research import cases, kernel
from tradingagents.research.schemas import ModelError
from tradingagents.research.valuation import value

from .test_research_kernel import spec


def _base():
    model = kernel.run(spec())
    return model, value(model)


def test_a_case_recomputes_the_base_with_its_deltas():
    model, valuation = _base()
    case = cases.Case(stance="aggressive", revenue_pct=5.0, operating_profit_pct=5.0, multiple=13.0)
    result = cases.apply_case(model, valuation, case)
    row = model.row(valuation.period)
    assert result.revenue == pytest.approx(row.revenue * 1.05)
    assert result.operating_profit == pytest.approx(row.operating_profit * 1.05)
    assert result.target == pytest.approx(result.eps * result.multiple)


def test_a_delta_beyond_the_cap_is_clamped_and_reported():
    model, valuation = _base()
    case = cases.Case(stance="aggressive", revenue_pct=40.0)
    result = cases.apply_case(model, valuation, case)
    assert result.applied["revenue_pct"] == cases.CAP_FORECAST_PCT
    assert any("capped" in note for note in result.notes)


def test_a_bear_arguing_growth_is_turned_round():
    model, valuation = _base()
    row = model.row(valuation.period)
    case = cases.Case(stance="bearish", revenue_pct=6.5, operating_profit_pct=6.5)
    result = cases.apply_case(model, valuation, case)
    # Flipped to -6.5% and then capped at -5%.
    assert result.applied["revenue_pct"] == pytest.approx(-cases.CAP_FORECAST_PCT)
    assert result.revenue < row.revenue
    assert any("applied as" in note and "bear case argues below" in note for note in result.notes)
    assert result.target < cases.base_multiple(valuation) * row.eps[valuation.basis]


def test_a_bull_arguing_a_decline_is_turned_round():
    model, valuation = _base()
    row = model.row(valuation.period)
    case = cases.Case(stance="aggressive", revenue_pct=-3.0, operating_profit_pct=-3.0)
    result = cases.apply_case(model, valuation, case)
    assert result.applied["revenue_pct"] == pytest.approx(3.0)
    assert result.revenue > row.revenue
    assert any("bull case argues above" in note for note in result.notes)


def test_a_neutral_case_may_argue_either_way():
    model, valuation = _base()
    case = cases.Case(stance="neutral", revenue_pct=2.0)
    result = cases.apply_case(model, valuation, case)
    assert result.applied["revenue_pct"] == pytest.approx(2.0)
    assert result.notes == []


def test_proposing_the_base_multiple_is_not_an_argument():
    model, valuation = _base()
    case = cases.Case(stance="aggressive", multiple=cases.base_multiple(valuation))
    result = cases.apply_case(model, valuation, case)
    assert result.notes == []


def test_a_multiple_beyond_the_cap_is_clamped():
    model, valuation = _base()
    base = cases.base_multiple(valuation)
    case = cases.Case(stance="aggressive", multiple=base * 3, evidence=["doc-peers"])
    result = cases.apply_case(model, valuation, case)
    assert result.multiple == pytest.approx(base * (1 + cases.CAP_MULTIPLE_PCT / 100), rel=1e-6)
    assert any("multiple limited" in note for note in result.notes)


def test_an_unknown_stance_is_rejected():
    with pytest.raises(ModelError, match="Unknown stance"):
        cases.Case.from_dict({"stance": "euphoric"})


def test_the_case_json_is_parsed_from_the_end_of_an_argument():
    text = 'Bull Analyst: long argument...\n{"stance": "aggressive", "revenue_pct": 2.0}'
    case = cases.parse_case(text)
    assert case is not None and case.stance == "aggressive"
    assert cases.parse_case("no json here") is None


def test_probabilities_are_normalized():
    results = [
        cases.CaseResult("aggressive", 2.0, 1, 1, 1, 1, 1, 1, {}),
        cases.CaseResult("bearish", 2.0, 1, 1, 1, 1, 1, 1, {}),
    ]
    cases.normalize_probabilities(results)
    assert sum(result.probability for result in results) == pytest.approx(1.0)


def test_the_proposal_table_shows_deltas_and_caps():
    table = cases.render_proposals([{"stance": "bearish", "revenue_pct": -4.0, "multiple": 20.0}])
    assert "bearish" in table and "-4.0%" in table and "20.0x" in table


def test_the_results_table_includes_the_base_row():
    model, valuation = _base()
    table = cases.render_results(
        model, valuation, [{"stance": "aggressive", "revenue_pct": 3.0}]
    )
    assert "base (fair-value mid)" in table
    assert "aggressive" in table
    assert table.count("\n") >= 3


def test_the_base_case_keeps_a_prior_weight():
    model, valuation = _base()
    table = cases.render_results(
        model, valuation,
        [{"stance": "aggressive", "probability": 0.5}, {"stance": "bearish", "probability": 0.5}],
    )
    assert "| base (fair-value mid) | 50%" in table
    assert "| aggressive | 25%" in table
    assert "| bearish | 25%" in table


def test_the_base_row_says_which_base_it_is():
    """The package's Base scenario target and the case table's base are two
    different model lines; the table must not call both simply 'base'."""
    model, valuation = _base()
    table = cases.render_results(model, valuation, [{"stance": "aggressive", "revenue_pct": 3.0}])
    assert "fair-value midpoint" in table
    assert "Base *scenario*" in table


def test_a_bear_cannot_argue_a_higher_multiple_than_the_base():
    model, valuation = _base()
    base = cases.base_multiple(valuation)
    result = cases.apply_case(model, valuation, cases.Case(stance="bearish", multiple=base * 1.1, evidence=["doc-peers"]))
    assert result.multiple == pytest.approx(base, rel=1e-6)
    assert any("cannot argue a higher multiple" in note for note in result.notes)


def test_a_bull_cannot_argue_a_lower_multiple_than_the_base():
    model, valuation = _base()
    base = cases.base_multiple(valuation)
    result = cases.apply_case(model, valuation, cases.Case(stance="aggressive", multiple=base * 0.8, evidence=["doc-peers"]))
    assert result.multiple == pytest.approx(base, rel=1e-6)
    assert any("cannot argue a lower multiple" in note for note in result.notes)


def test_case_figures_are_known_to_the_audit():
    from tradingagents.research import audit

    model, valuation = _base()
    case = {"stance": "bearish", "probability": 0.5, "revenue_pct": -2.0,
            "operating_profit_pct": -3.0, "multiple": 10.0}
    known = audit.known_values(model, valuation, [case])
    result = cases.apply_case(model, valuation, cases.Case.from_dict(case))
    assert round(result.eps, 2) in known
    assert round(result.target, 2) in known


def test_a_multiple_without_evidence_falls_back_to_the_base():
    model, valuation = _base()
    base = cases.base_multiple(valuation)
    result = cases.apply_case(model, valuation, cases.Case(stance="aggressive", multiple=base * 1.1))
    assert result.multiple == pytest.approx(base, rel=1e-6)
    assert any("no evidence" in note for note in result.notes)


def test_a_multiple_with_evidence_is_kept():
    model, valuation = _base()
    base = cases.base_multiple(valuation)
    case = cases.Case(stance="aggressive", multiple=base * 1.1, evidence=["doc-peers"])
    result = cases.apply_case(model, valuation, case)
    assert result.multiple == pytest.approx(base * 1.1, rel=1e-6)


def test_case_json_is_stripped_from_the_transcript_shown_to_the_opponent():
    text = (
        "Bull Analyst: argued at length.\n"
        '{"stance": "aggressive", "multiple": 22.0}\n'
        "Bear Analyst: replied."
    )
    cleaned = cases.strip_case_json(text)
    assert '"stance"' not in cleaned
    assert "argued at length" in cleaned and "replied" in cleaned


def test_a_case_wrapped_in_a_code_fence_is_still_read():
    text = 'The bear case:\n```json\n{"stance": "bearish", "revenue_pct": -2.0}\n```'
    case = cases.parse_case(text)
    assert case is not None and case.stance == "bearish"
    assert case.revenue_pct == pytest.approx(-2.0)


def test_prose_after_the_case_does_not_hide_it():
    text = '{"stance": "aggressive", "revenue_pct": 2.0}\n\nThat is the case.'
    assert cases.parse_case(text).stance == "aggressive"


def test_an_unknown_stance_is_ignored_rather_than_raising():
    assert cases.parse_case('{"stance": "bear", "revenue_pct": -2.0}') is None


def test_the_case_list_keeps_one_case_per_stance():
    from tradingagents.agents.researchers.bull_researcher import _with_case

    first = cases.Case(stance="aggressive", probability=0.4)
    second = cases.Case(stance="aggressive", probability=0.6)
    merged = _with_case(_with_case("", first), second)
    parsed = json.loads(merged)
    assert len(parsed) == 1
    assert parsed[0]["probability"] == 0.6

