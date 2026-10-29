"""The model's figures are marked by the agent; the audit checks the arithmetic."""

from __future__ import annotations

import pytest

from tradingagents.research import audit, kernel
from tradingagents.research.valuation import value

from .test_research_kernel import spec


def known():
    model = kernel.run(spec())
    return model, value(model), audit.known_values(model, value(model))


def test_a_marked_figure_that_matches_produces_no_finding():
    _model, _valuation, values = known()
    text = "EPS is [[1.0]] and the P/E is [[20.0x]]."
    assert audit.audit_text(text, "F", values) == []


def test_a_marked_figure_that_disagrees_is_reported():
    _model, _valuation, values = known()
    findings = audit.audit_text("EPS is [[1.4]].", "F", values)
    assert [finding.number for finding in findings] == [1.4]


def test_an_unmarked_number_is_ignored():
    _model, _valuation, values = known()
    text = "Monitor the Q2 print, 25 bps at a time (doc-5803.T-x, p. 2), around 2019."
    assert audit.audit_text(text, "F", values) == []


def test_a_marked_number_in_trillions_matches_the_billions_model():
    _model, _valuation, values = known()
    assert audit.audit_text("Market cap [[0.2tn]].", "F", values) == []


def test_a_marked_full_unit_amount_matches_its_scaled_form():
    _model, _valuation, values = known()
    assert audit.audit_text("Market cap [[200,000,000]].", "F", values) == []


def test_a_marked_negative_figure_matches_its_negative_value():
    from tradingagents.research.schemas import Bridge, Line

    model_spec = spec(
        bridges=[Bridge("revenue", "FY25", "FY26", 120.0, [Line("FX headwind", -5.0)],
                        declared_total=115.0)]
    )
    model = kernel.run(model_spec)
    values = audit.known_values(model, value(model))
    assert audit.audit_text("| FX headwind | [[−5]] |", "F", values) == []


def test_a_marked_percentage_is_ignored():
    _model, _valuation, values = known()
    assert audit.audit_text("Margin rose [[10.3%]].", "F", values) == []


def test_a_marked_price_inside_the_fair_value_band_is_a_choice():
    _model, _valuation, values = known()
    assert audit.audit_text(
        "Target [[11.0]]", "PM", values, band=(10.0, 12.0)
    ) == []
    findings = audit.audit_text("Target [[30.0]]", "PM", values, band=(10.0, 12.0))
    assert [finding.number for finding in findings] == [30.0]


def test_the_marks_are_stripped_for_the_report():
    assert audit.strip_marks("EPS is [[229.5]] and cash [[JPY 96.2bn]].") == (
        "EPS is 229.5 and cash JPY 96.2bn."
    )


def test_marked_figures_are_listed_in_order():
    assert audit.marked_figures("[[1.0]] then [[2.0]] and [[1.0]]") == ["1.0", "2.0", "1.0"]


def test_a_state_audit_checks_the_four_written_sections():
    model, valuation, _values = known()
    state = {
        "fundamentals_report": "EPS [[1.4]]",
        "investment_plan": "Fair value [[11.0]]",
        "trader_investment_plan": "Entry [[20.0]]",
        "final_trade_decision": "Target [[30.0]]",
    }
    stats: dict = {}
    findings = audit.audit_state(state, model, valuation, stats=stats)
    assert {finding.section for finding in findings} == {"Fundamentals Analyst", "Portfolio Manager"}
    assert stats["sections"] == {
        "Fundamentals Analyst": 1,
        "Research Manager": 1,
        "Trader": 1,
        "Portfolio Manager": 1,
    }


def test_render_says_so_when_everything_matches():
    text = audit.render([], stats={"marked": 3, "sections": {"Fundamentals Analyst": 3}})
    assert "All 3 figure(s)" in text
    assert "Fundamentals Analyst 3" in text


def test_render_says_so_when_nothing_was_marked():
    text = audit.render([], stats={"marked": 0, "sections": {}})
    assert "nothing was checked" in text
    assert "double square brackets" in text


def test_render_lists_findings_as_a_table():
    text = audit.render([audit.Finding("Fundamentals", 999.0, "FY26 revenue 999.0")])
    assert "| Fundamentals | 999 |" in text
    assert "do not match the verified model" in text


def test_a_finding_context_cannot_break_the_table():
    text = audit.render([audit.Finding("S", 1.0, "a | b")])
    assert "a \\| b" in text


@pytest.mark.parametrize("value_in_model", [20.0, 20.04, 19.96])
def test_small_rounding_differences_are_tolerated(value_in_model):
    _model, _valuation, values = known()
    assert audit.audit_text(f"P/E [[{value_in_model}x]]", "F", values) == []


def test_citation_marks_are_counted_instead_of_reported():
    """[[6]] is a footnote, not a figure: a run whose citations outnumber its
    figures must not bury the real findings in noise. Only a single digit reads
    as a citation — [[90]] and [[12]] are model lines and stay checked."""
    _model, _valuation, values = known()
    stats: dict = {}
    text = "From the filing [[6]], the call [[2]] and the notes [[7]]. EPS is [[1.0]]."
    assert audit.audit_text(text, "F", values, stats=stats) == []
    assert stats["references"] == 3
    assert stats["marked"] == 4


def test_a_two_digit_mark_is_still_checked():
    _model, _valuation, values = known()
    stats: dict = {}
    findings = audit.audit_text("Drag of [[73]]bn.", "F", values, stats=stats)
    assert stats["references"] == 0
    assert [finding.number for finding in findings] == [73.0]


def test_the_audit_says_what_it_did_not_check():
    text = audit.render([], stats={"marked": 4, "references": 3, "sections": {"F": 4}})
    assert "All 1 figure(s)" in text
    assert "reference numbers" in text


def test_a_mark_holding_an_id_is_a_label_not_a_number():
    _model, _valuation, values = known()
    assert audit.audit_text("Source [[doc-5803.T-x]].", "F", values) == []


def test_fused_marks_are_unwrapped_for_the_report():
    assert audit.strip_marks("from the filing [[1][2]] done") == "from the filing 1, 2 done"


def test_the_context_is_the_line_the_figure_is_marked_in():
    _model, _valuation, values = known()
    findings = audit.audit_text("Round 1.4 of reviews\n\nEPS [[1.4]].", "F", values)
    assert len(findings) == 1
    assert findings[0].context.startswith("EPS")


def test_the_same_misquote_twice_on_one_line_is_one_finding():
    _model, _valuation, values = known()
    findings = audit.audit_text("EPS [[1.4]] and again [[1.4]].", "F", values)
    assert [finding.number for finding in findings] == [1.4]
