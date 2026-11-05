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


def test_net_debt_components_printed_in_the_package_are_known_values():
    """The 4062.T package printed net debt -258.4 with gross debt 162.0 and cash 420.5.

    Every one of those figures is the model's, so a mark on them is a quote, not
    a misquote. The audit reported all of them as errors before this check.
    """
    from tradingagents.research.schemas import NetDebt

    model_spec = spec(net_debt=NetDebt(gross_debt=162.0, cash_and_equivalents=420.5))
    model = kernel.run(model_spec)
    valuation = value(model)
    values = audit.known_values(model, valuation)

    text = "Net debt [[−258.5]] with gross debt [[162.0]] and cash [[420.5]]."
    assert audit.audit_text(text, "F", values) == []


def test_the_ev_ebitda_block_printed_in_the_package_is_a_known_value():
    from tradingagents.research.schemas import CashFlow, NetDebt, ValuationSpec

    model_spec = spec(
        net_debt=NetDebt(gross_debt=100.0, cash_and_equivalents=40.0),
        cashflow={
            "FY25": CashFlow(net_profit=9.0, depreciation=4.0,
                             working_capital_change=-2.0, capex=3.0),
            "FY26": CashFlow(net_profit=10.0, depreciation=2.0,
                             working_capital_change=0.0, capex=1.0),
        },
        valuation=ValuationSpec(
            price=20.0, fair_value_multiple_low=10.0, fair_value_multiple_high=12.0,
            sensitivity_multiples=[8.0, 10.0, 12.0], ev_ebitda_multiple=15.5,
        ),
    )
    model = kernel.run(model_spec)
    valuation = value(model)
    assert valuation.ebitda is not None and valuation.enterprise_value is not None
    values = audit.known_values(model, valuation)

    text = (
        f"EBITDA [[{valuation.ebitda:,.1f}]] gives an enterprise value of "
        f"[[{valuation.enterprise_value:,.1f}]]."
    )
    assert audit.audit_text(text, "F", values) == []


def test_a_ranked_sensitivity_impact_is_known_in_either_sign():
    """The table prints -407.18; prose quotes "loses 407.18"."""
    _model, valuation, _values = known()
    model = kernel.run(spec())
    item = {"sensitivity_id": "cell_8_loading", "fair_value_impact": -407.18,
            "eps_impact": -11.31, "operating_profit_impact": -10.0}
    values = audit.known_values(model, valuation, sensitivity_items=[item])

    assert audit.audit_text("It loses [[407.18]] of fair value.", "F", values) == []
    assert audit.audit_text("Fair value falls [[−407.18]].", "F", values) == []
    assert audit.audit_text("EPS falls [[11.31]].", "F", values) == []


def test_a_marked_negative_figure_matches_its_negative_value():
    from tradingagents.research.schemas import Bridge, Line

    model_spec = spec(
        bridges=[
            # a forecast year keeps its topline: both revenue and operating
            # profit are bridged, or the kernel rejects the orphan net profit
            Bridge("revenue", "FY25", "FY26", 120.0, [Line("FX headwind", -5.0)],
                   declared_total=115.0),
            Bridge("operating_profit", "FY25", "FY26", 15.0, [Line("Scale", 1.0)],
                   declared_total=16.0),
        ]
    )
    model = kernel.run(model_spec)
    values = audit.known_values(model, value(model))
    assert audit.audit_text("| FX headwind | [[−5]] |", "F", values) == []


def test_a_percentage_above_the_price_is_recomputed():
    """The 4062.T market report published this sentence; it is wrong by 5.5pp."""
    text = ("The 50-day SMA 10,168.07 (falling from 10,409.45 on 20 Oct) sits "
            "20.9% above price.")
    findings = audit.percentage_findings(text, "Market Analyst", 8045.0)
    assert [finding.number for finding in findings] == [20.9]


def test_the_formulas_the_research_manager_writes_are_evaluated():
    """Unicode signs and unit suffixes are arithmetic, not failures."""
    text, verified, failures = audit.extract_computed(
        "[[computed: 170.0 = 136 + 20 + 10 + 8 − 4]] "
        "[[computed: 187.3 = 111.0bn/592.5m]] "
        "[[computed: 6,279 = 209.3 × 30]] "
        "[[computed: 7,953 = 209.3 × 38]]"
    )

    assert failures == []
    assert verified == {170.0, 187.3, 6279.0, 7953.0}
    assert "[[" not in text


def test_a_formula_that_is_wrong_still_fails_and_names_the_figure():
    _text, verified, failures = audit.extract_computed(
        "[[computed: 171.0 = 136 + 20 + 10 + 8 − 4]]"
    )

    assert 171.0 not in verified
    assert failures == [(171.0, "136 + 20 + 10 + 8 − 4")]


def test_a_formula_that_rounds_to_the_printed_figure_passes():
    """-11.783 printed as -11.8 is a correct quote, not a failed formula."""
    _text, verified, failures = audit.extract_computed(
        "Base [[computed: -11.8 = (7090/8037-1)*100]] and "
        "high [[computed: 1.7 = (8176/8037-1)*100]]."
    )

    assert failures == []
    assert -11.8 in verified and 1.7 in verified


def test_an_integer_figure_allows_half_a_unit_of_rounding():
    _text, verified, failures = audit.extract_computed("[[computed: 12 = 11.6 + 0.4]]")

    assert failures == []
    assert 12.0 in verified


def test_a_value_outside_the_printed_precision_still_fails():
    _text, _verified, failures = audit.extract_computed(
        "[[computed: -11.9 = (7090/8037-1)*100]]"
    )

    assert [value for value, _formula in failures] == [-11.9]


def test_a_correct_percentage_passes():
    text = "The 10-EMA 9,026.10 is 12.2% above price."
    assert audit.percentage_findings(text, "Market Analyst", 8045.0) == []


def test_a_percentage_against_a_level_is_not_a_price_claim():
    text = "Price sits 3.4% above the 200-day SMA."
    assert audit.percentage_findings(text, "Market Analyst", 8045.0) == []


def test_a_repair_that_fixes_the_percentage_is_accepted():
    model = kernel.run(spec())
    wrong = "The 50-day SMA 25.28 sits 20.9% above price."
    state = {"market_report": wrong}
    findings = audit.percentage_findings(wrong, "Market Analyst", model.spec.valuation.price)
    assert findings
    # No model call is needed: the percentage is arithmetic on two printed numbers.
    assert audit.repair_relations(state, findings, lambda prompt: wrong, model=model) == [
        "Market Analyst"
    ]
    assert state["market_report"] == "The 50-day SMA 25.28 sits 26.4% above price."


def test_a_percentage_on_the_wrong_side_of_the_price_is_flipped():
    text = "The 50-day SMA 25.28 sits 20.9% above price."
    fixed, count = audit.fix_percentage_claims(text, 20.0)
    assert count == 1
    assert "26.4% above" in fixed


def test_a_percentage_for_a_level_below_the_price_reads_below():
    text = "The 200-day SMA 15.00 sits 20.0% above price."
    fixed, count = audit.fix_percentage_claims(text, 20.0)
    assert count == 1
    assert "25.0% below" in fixed


def test_a_plus_signed_bridge_delta_is_checked():
    """`[[+16]]` is a model figure; the audit used to skip it as a label."""
    _model, _valuation, values = known()
    assert audit.audit_text("add +[[1.0]] to revenue", "F", values) == []
    findings = audit.audit_text("add +[[42.0]] to revenue", "F", values)
    assert [finding.number for finding in findings] == [42.0]


def test_a_period_label_across_a_comma_does_not_claim_the_figure():
    """The 4062.T PM sentence paired net profit ~118 with FY3/29's 150.7 via a capex clause."""
    model = kernel.run(spec())
    sentence = (
        "At 20 the stock is 35.9x FY26 EPS 1.0, with net profit ~0.8, capex above JPY5bn "
        "in FY24."
    )
    findings = audit.relational_findings(
        sentence, "S", audit._period_table(model), model.spec.unit
    )
    assert findings == []


def test_the_relation_table_shows_both_operands_and_the_truth():
    finding = audit.Finding(
        "Portfolio Manager", 118.0, "net profit ~118 versus FY3/29", kind="relation",
        subject=118.0, object=150.7, expected="118 is below 150.7",
    )
    text = audit.render([finding])
    assert "118 vs 150.7" in text
    assert "118 is below 150.7" in text


def test_the_percentage_table_shows_the_level_the_price_and_the_recomputed_value():
    finding = audit.Finding(
        "Market Analyst", 20.9, "the SMA sits 20.9% above price", kind="percentage",
        subject=10168.07, object=8045.0, expected="+26.4%",
    )
    text = audit.render([finding])
    assert "10,168 vs 8,045" in text
    assert "+26.4%" in text


def test_a_marked_percentage_is_checked_like_any_other_figure():
    """A margin the model produced passes; a different one is a finding.

    The old check skipped any mark on a line that already showed the same digits
    with a percent sign, which could hide a misquote. The mark is now the unit.
    """
    _model, _valuation, values = known()
    assert audit.audit_text("Margin rose to [[12.5%]].", "F", values) == []
    findings = audit.audit_text("Margin rose to [[10.3%]].", "F", values)
    assert [finding.number for finding in findings] == [10.3]


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


def test_guidance_values_are_known_even_when_the_forecast_overrides_them():
    """Management's guidance is printed in the package's comparison table, so
    marking it quotes the model's own artifact — the rows no longer carry it."""
    from tradingagents.research.schemas import Bridge, Financials, Line

    model_spec = spec(
        forecast={"FY26": Financials(net_profit=10.0), "FY25": Financials(revenue=130.0)},
        bridges=[
            Bridge("revenue", "FY25", "FY26", 130.0, [Line("New business", 10.0)],
                   declared_total=140.0),
            Bridge("operating_profit", "FY25", "FY26", 15.0, [Line("New business", 1.0)],
                   declared_total=16.0),
        ],
        segments={},
    )
    model_spec.periods[1].basis = "forecast"
    model_spec.scenarios[0].multiple = 10.0    # FY25 anchors the fair value (EPS 0.9):
    model_spec.scenarios[0].eps = 0.9          # keep the Base target inside its band
    model_spec.scenarios[0].period = "FY25"
    model = kernel.run(model_spec)
    values = audit.known_values(model, value(model), [])
    assert 120.0 in values                        # guidance, not in any row
    assert audit.audit_text("Management said [[120.0]] for FY25.", "F", values) == []


def test_numbers_inside_bridge_formulas_are_model_content():
    """H1 actuals and H2 guidance appear in the package as bridge formulas
    ('H1 262.5 less 195.5 = 67.0'); analysts mark them from there."""
    model_spec = spec()
    model_spec.bridges[0].lines[0].formula = "H1 262.5 less 195.5 = 67.0 delta"
    model = kernel.run(model_spec)
    values = audit.known_values(model, value(model), [])
    assert audit.audit_text("H1 was [[262.5]] vs [[195.5]].", "F", values) == []


def test_the_rendered_scenario_change_is_a_known_figure():
    """The package prints scenarios as '(-42%)'; a mark on that percent is a
    quote of the package, not a misquote."""
    model = kernel.run(spec())
    val = value(model)
    rendered = int(f"{val.scenarios[0]['change'] * 100:.0f}")
    values = audit.known_values(model, val, [])
    assert float(rendered) in values and float(abs(rendered)) in values
    assert audit.audit_text(f"The bear case sits at [[{abs(rendered)}]].", "F", values) == []


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
    assert "All 3 checked figure(s)" in text
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
    assert "All 1 checked figure(s)" in text
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


def test_a_sign_glued_outside_the_brackets_counts_as_the_sign():
    """Models write −[[14]]; the figure is −14, which the model has."""
    assert audit.marked_figures("profit −[[14]] tax") == ["−14"]
    assert audit.audit_text("profit −[[14]] tax", "F", {-14.0}) == []


def test_an_unmarked_sign_still_flags():
    """[[14]] on its own claims +14, and +14 against a model of −14 is a finding."""
    findings = audit.audit_text("profit [[14]] tax", "F", {-14.0})
    assert [finding.number for finding in findings] == [14.0]


# --- comparative claims (relational consistency) -----------------------------

# The sentence the 4062.T report published: every figure right, the relation false.
C_CLAIM = ("(c) Net profit already exceeds the prior full year at the half-year mark: "
           "H1 FY3/27 net profit of 38.6bn versus 63.7bn for all of FY3/26 63.7.")
BULL_CLAIM = ("Positive indicators are everywhere except the daily chart. Net cash is ~¥258bn, "
              "the equity ratio is 53.5%, H1 net profit of ¥38.6bn already exceeds all of "
              "FY3/26, and the dividend is progressive with a 20% payout guideline.")


def _relations(text, table=None):
    return audit.relational_findings(text, "S", {} if table is None else table, "JPY_bn")


def test_a_claim_contradicted_by_its_own_printed_figures_is_flagged():
    findings = _relations(C_CLAIM)
    assert [finding.kind for finding in findings] == ["relation"]


def test_the_true_version_of_the_same_claim_is_not_flagged():
    assert _relations(
        "H1 net profit of 63.7bn already exceeds the 38.6bn of the prior full year."
    ) == []


def test_the_debate_repetition_is_caught_by_the_model_period_label():
    """The bull prints only one operand; the object is the model's own row."""
    table = {"FY3/26": {"net_profit": 63.7}}
    findings = _relations(BULL_CLAIM, table)
    assert [finding.kind for finding in findings] == ["relation"]
    assert _relations("H1 net profit of ¥70.0bn already exceeds all of FY3/26.", table) == []
    assert _relations(BULL_CLAIM) == []          # no model rows: Tier 2 has no object


def test_a_delta_welded_to_the_predicate_is_not_a_false_subject():
    assert _relations(
        "The model's 190.0 matches consensus and sits 25.0 above company guidance "
        "of 165.0, because management is conservative."
    ) == []
    # the unit suffix and markdown emphasis must not break the weld
    assert _relations(
        "The model assumes FY3/28 OP of ¥190bn, which is already §25bn above company "
        "guidance of ¥165bn."
    ) == []
    assert _relations(
        "The model's 190.0 sits 25.0 *above* company guidance of 165.0."
    ) == []
    # percent deltas: "2% above base and 3% above base" never compares 2 with 3
    assert _relations(
        "My aggressive case is modest: 2% above base revenue and 3% above base "
        "operating profit for FY3/28, with the base 50x multiple doing the work."
    ) == []


def test_a_case_blob_is_data_not_prose():
    blob = '{"stance": "bearish", "probability": 0.34, "revenue_pct": -2.0, "momentum": ' \
           '"Negative: lower high at 12,580 vs 13,750, below 50D, MACD -473"}'
    assert _relations(blob) == []


def test_a_claim_in_a_new_clause_is_not_paired_with_the_previous_figure():
    """The Nov 2 run's five findings were all this shape: the predicate opened
    its own clause with an implied subject, so the figure before it is not the
    thing being compared."""
    assert _relations(
        "But this is a capital-intensive manufacturer with capex around ¥200bn this "
        "year, ¥350bn next year and more than ¥400bn thereafter, and with operating "
        "cash flow ex-customer-advances of roughly ¥74bn against ¥48bn of H1 capex."
    ) == []
    assert _relations(
        "…ex-advances H1 operating cash flow is closer to ¥74bn against ¥48bn of H1 "
        "capex, and capex is stepping to roughly ¥350bn next year and above ¥400bn "
        "thereafter."
    ) == []
    assert _relations("**Stop Loss**: ¥7,779 on a weekly close (cut to half); full "
                      "exit below ¥7,400.") == []
    assert _relations(
        "Hold at 1.0x standard allocation through a 12.8% gap-down, with a "
        "weekly-close half-cut only at 7,779 and full exit below 7,400, means you "
        "stay fully exposed."
    ) == []
    # a true comparison in the same shape is skipped too — precision first
    assert _relations(
        "Free cash flow of ¥26.1bn is far below the capex step-up to ¥350bn in "
        "FY3/28, and more than ¥400bn a year thereafter."
    ) == []


def test_a_hedge_counts_only_inside_the_claim():
    """An 'about' elsewhere in the sentence must not widen THIS comparison's
    tolerance — that accident let a stop-loss line through on Nov 2."""
    assert len(_relations(
        "Revenue of 395bn exceeds 416.2bn, and about 60% of that is seasonal."
    )) == 1
    assert _relations("Revenue of 395bn roughly exceeds 416.2bn.") == []


def test_the_object_must_stay_in_the_claims_own_clause():
    """The Nov 2 11:55 findings: a duration idiom whose 'object' sits in the
    next clause, and a negated cap whose two percentages are unrelated."""
    assert _relations(
        "4062.T has fallen from a 12400 close (2026-10-06) to 8165, −34.2% in under "
        "a month, and now sits only 4.9% above a rising average."
    ) == []
    assert _relations(
        "The plan's current sizing guidance—each tranche sized so stop risk is no "
        "more than 0.5% of portfolio—is based on a 5.7% stop distance."
    ) == []
    # the colon appositive is NOT a boundary: the known true positive still flags
    assert len(_relations(
        "(c) Net profit already exceeds the prior full year at the half-year mark: "
        "H1 FY3/27 net profit of 38.6bn versus 63.7bn for all of FY3/26 63.7."
    )) == 1


def test_operands_read_the_unicode_minus():
    operands = audit._operands("changed −34.2% versus -5.0%")
    assert [operand[2] for operand in operands] == [-34.2, -5.0]


def test_a_sentence_ending_before_a_ticker_still_splits():
    """The digit guard used to glue "…the analyst will. 4062.T is below…" into
    one sentence, and the claim then paired 10 against 9,047.92."""
    text = ("On the downtrend, I agree with the tape more than the conservative "
            "analyst will. 4062.T is below the 10 EMA at 9,047.92 and recovered.")
    assert _relations(text) == []


def test_an_indicators_period_is_not_a_figure():
    """"8,165 is below the 10 EMA at 9,047.92" must pair the price with the
    price, not with the 10 in the indicator's name — and the claim is true."""
    assert _relations(
        "4062.T at 8,165 is below the 10 EMA at 9,047.92 and the 50 SMA at "
        "10,170.47, RSI is 37.85, MACD histogram is −144.48, and VWMA at 9,182.74 "
        "is above the 8,165 price."
    ) == []


def test_a_spaced_sign_belongs_to_the_marked_figure():
    """The bridge writer puts the minus outside and a space after it: the figure
    is −19.0 (which the model has), not +19.0."""
    assert audit.marked_figures("base 89.0 + 57.0 OP increase − [[19.0]] tax") == ["−19.0"]
    # …but a completed figure before the sign means subtraction, not a sign
    assert audit.marked_figures("[[1,200]] − [[300]] costs") == ["1,200", "300"]
    assert audit.marked_figures("margin fell from 5 − [[3]] points") == ["3"]


def test_a_prepositional_object_without_a_subject_is_not_paired_backwards():
    assert _relations("**Invalidation:** a close below 7,883, which would open the "
                      "200-day SMA at 7,756.7.") == []


def test_units_that_do_not_line_up_are_skipped():
    assert _relations("The 22.9% margin exceeds 15.0bn.") == []
    assert _relations("Profit of 38.6 exceeds 63.7bn.") == []   # a bare number's unit is unknowable


def test_a_range_makes_the_pairing_ambiguous():
    assert _relations("The band 6,915-8,644 exceeds spot 8,359.") == []


def test_table_rows_are_column_structure_not_claims():
    assert _relations("| Zone | Basis |\n| 7,883 - 8,248 | above the low |") == []


def test_a_hedged_claim_needs_a_gross_violation_to_flag():
    assert _relations("Revenue of 395bn roughly exceeds 416.2bn.") == []
    assert len(_relations("Revenue of 395bn exceeds 416.2bn.")) == 1


def test_dates_ticks_days_and_basis_points_are_not_operands():
    text = ("On 2026-10-30 the 4062.T close of 8,359 is below VWMA (9,340.8), while the "
            "50-day line sits above and the range spanned 250bp.")
    assert _relations(text) == []


def test_the_state_audit_finds_false_comparisons_in_every_prose_section():
    model, valuation, _values = known()
    state = {
        "fundamentals_report": C_CLAIM,
        "investment_debate_state": {
            "challenge_history": "Adversarial Analyst: H1 net profit of 4.0 already exceeds all of FY24.",
        },
    }
    stats: dict = {}
    findings = audit.audit_state(state, model, valuation, stats=stats)
    relations = [finding for finding in findings if finding.kind == "relation"]
    assert {finding.section for finding in relations} == {"Fundamentals Analyst", "Adversarial Reviewer"}
    # figure statistics are untouched by the relational scan: only zeroes were
    # recorded (these sections mark no figures), never a relation count
    assert not any(stats.get("sections", {}).values())


def test_render_reports_relational_findings_in_their_own_block():
    relation = audit.Finding("Fundamentals Analyst", 38.6, C_CLAIM, kind="relation")
    text = audit.render([relation], stats={"marked": 0, "sections": {}})
    assert "contradict the figures" in text
    assert "nothing was checked" not in text


def test_render_keeps_the_figure_table_and_adds_the_relation_block():
    mixed = [
        audit.Finding("F", 999.0, "FY26 revenue 999.0"),
        audit.Finding("F", 38.6, C_CLAIM, kind="relation"),
    ]
    text = audit.render(mixed, stats={"marked": 1, "sections": {"F": 1}})
    assert "do not match the verified model" in text
    assert "contradict the figures" in text


def test_the_repair_replaces_the_false_claim_with_the_true_relationship():
    model = kernel.run(spec())
    state = {"fundamentals_report": C_CLAIM}
    findings = audit.relational_findings(
        C_CLAIM, "Fundamentals Analyst", audit._period_table(model), model.spec.unit
    )
    fixed = ("H1 FY3/27 net profit reached 38.6bn, approximately 61% of the prior full "
             "year's 63.7bn.")
    changed = audit.repair_relations(state, findings, lambda prompt: fixed, model=model)
    assert changed == ["Fundamentals Analyst"]
    assert state["fundamentals_report"] == fixed
    assert audit.relational_findings(fixed, "F", audit._period_table(model), model.spec.unit) == []


def test_a_repair_that_still_contradicts_the_figures_is_rejected():
    model = kernel.run(spec())
    state = {"fundamentals_report": C_CLAIM}
    findings = audit.relational_findings(
        C_CLAIM, "Fundamentals Analyst", audit._period_table(model), model.spec.unit
    )
    worse = "H1 net profit of 10.0bn already exceeds the prior full year of 63.7bn."
    assert audit.repair_relations(state, findings, lambda prompt: worse, model=model) == []
    assert state["fundamentals_report"] == C_CLAIM


def test_a_participle_opens_a_new_clause_so_the_figure_is_not_its_subject():
    """'Capex of 350bn ... rising above 400bn thereafter' compares no two figures.

    The 4062.T report published this sentence; the checker paired 350 with 400
    and would have rewritten a correct claim.
    """
    claim = ("Capex of ¥350bn in FY3/28 rising above ¥400bn thereafter, against "
             "substantially negative normalised FCF.")
    assert _relations(claim) == []


def test_a_failed_or_stub_repair_leaves_the_text_alone():
    model = kernel.run(spec())
    state = {"fundamentals_report": C_CLAIM}
    findings = audit.relational_findings(
        C_CLAIM, "Fundamentals Analyst", audit._period_table(model), model.spec.unit
    )

    def boom(prompt):
        raise RuntimeError("provider unavailable")

    assert audit.repair_relations(state, findings, boom, model=model) == []
    assert audit.repair_relations(state, findings, lambda prompt: "Report.", model=model) == []
    assert state["fundamentals_report"] == C_CLAIM


# --- what "checked" means ----------------------------------------------------


def test_a_wrong_margin_is_checked_even_beside_another_percentage():
    """The line used to be the unit, so a percentage elsewhere shielded the mark.

    The model's FY25 operating margin is 12.5; the marked 42.0 must fail even
    though the same table row prints 12.5% next to it.
    """
    _model, _valuation, values = known()
    text = "| Operating margin | 12.5% | [[42.0]]% |"
    findings = audit.audit_text(text, "F", values)
    assert [finding.number for finding in findings] == [42.0]


def test_a_label_mark_is_skipped_not_checked():
    _model, _valuation, values = known()
    stats: dict = {}
    audit.audit_text("Source [[doc-5803.T-x]] and EPS [[1.0]].", "F", values, stats=stats)
    assert stats["marked"] == 2
    assert stats["checked"] == 1
    assert stats["skipped"] == 1


def test_the_rendered_audit_separates_skipped_from_checked():
    text = audit.render(
        [], stats={"marked": 5, "references": 1, "skipped": 2, "sections": {"F": 2}}
    )
    assert "Independently checked: 2" in text
    assert "Skipped: 2" in text
    assert "Reference-only: 1" in text
    assert "Failed: 0" in text


def test_a_numerical_review_sentence_without_a_tag_is_a_warning():
    review = (
        "## Strongest Bear Case\n"
        "Electronics operating profit grew 97% and the backlog is 350 index points. "
        "[fundamental] Revenue grew 34.3% in H1."
    )
    findings = audit.grounding_findings(review)
    assert len(findings) == 1
    assert findings[0].kind == "grounding"
    assert "97%" in findings[0].context


def test_a_marked_figure_needs_no_evidence_tag():
    review = "## Strongest Bull Case\nOperating profit is [[185.0]], a real improvement."
    assert audit.grounding_findings(review) == []


def test_a_grounding_warning_is_rendered_but_not_counted_as_a_failed_figure():
    text = audit.render(
        [audit.Finding("Adversarial Reviewer", 97.0, "grew 97%", kind="grounding")],
        stats={"marked": 3, "references": 0, "skipped": 0, "sections": {"F": 3}},
    )
    assert "no evidence-class tag" in text
    assert "Failed: 0" in text
