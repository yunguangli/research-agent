"""The investor report is composed from complete components, never truncated prose."""

from __future__ import annotations

import re

from tradingagents.reporting import write_report_tree

SETTINGS = {"version": "0.7.0", "llm_provider": "openai", "deep_think_llm": "d",
            "quick_think_llm": "q", "analysts": ["market", "fundamentals"],
            "max_debate_rounds": 2, "max_risk_discuss_rounds": 1,
            "data_vendors": {"core_stock_apis": "yfinance"}}

FUNDAMENTALS = """# Fundamental Analysis: TEST

## 1. Executive conclusion
Rating: Neutral. The shares are full at 39x with a negative free-cash-flow floor.
[fundamental][valuation] IFRS-equivalent JP GAAP accounting is used.

## 2. Latest results
H1 revenue rose 34% to 262.5 and operating profit rose 85% to 60.1.

### Segment detail
Electronics contributed 83% of group operating profit at a 29% margin.

## 3. Management guidance
Guidance is 650 of revenue and 165 of operating profit for FY3/28.

| Metric | Guidance | Model |
|---|---|---|
| Revenue | 650.0 | 652.0 |
| Operating profit | 165.0 | 180.0 |

## 4. Consensus and variant perception
Consensus operating profit is 190.0, so the model is 5.3% below the street.

## 5. Forecast bridges
The bridge adds 35 from premium volume, 17 from CPU volume, and a residual of 18.

## 6. Cash flow and balance sheet
Cash is 420.5 against gross debt of 106.0; capital adequacy is 53.5%.

## 7. Risks and invalidation
A Q3 miss below 34.3 with Electronics margin below 30% would invalidate the view.

## 8. Key monitoring indicators
Watch Q3 operating profit, Cell 8 loading and substrate pricing.
"""

DEBATE = """### Round 1

## Strongest Bull Case
Customer-funded capacity converts AI-substrate demand into a durable earnings base.

## Strongest Bear Case
The forecast needs a 54% incremental margin against 41% actually delivered.

## Decisive Disagreement
Cell 8 loading carries the FY3/28 step-up.

Decisive sensitivity ID: operating_profit_fy3_28_cell_8_loading

## Rebuttal or Concession
I concede the advances lower stranded-capacity risk, but they fund capex rather than margins.
"""

MARKET = """## 1. Technical conclusion
The medium-term trend is down while the 200-day average still holds.

## 2. Evidence
Close 8,037 is below the 50-day average and above the 200-day average.
RSI is 37.16 and the MACD histogram is -160.82, still widening.

## 3. Decision levels
| Zone | Basis |
|---|---|
| 7,700-7,800 | 200-day average |
| 8,850-8,950 | 10 EMA |

## 4. Conditional outlook
A close above 8,850 would establish initial repair; below 7,700 invalidates it.

## 5. Investment implication
ATR is 10.4% of price and two gaps exceeded 700 points.
Timing is unfavorable for adding, so wait for repair.
Data limitations: the split adjustment basis is unresolved.
"""

PACKAGE = """FUNDAMENTAL MODEL — Test (TEST) — model 1.0.0

Annual figures:
- FY3/27 (forecast): revenue 570.0, operating profit 137.0
- FY3/28 (forecast): revenue 652.0, operating profit 180.0

Management guidance versus the model:
  Period | Metric | Guidance | Model | Variance
  FY3/28 | Operating profit | 165.0 | 180.0 | +9.1%

External consensus:
  Period | Metric | Consensus | Model | Variance
  FY3/28 | Operating profit | 190.0 | 180.0 | -5.3%

Valuation: price 8,037 JPY, FY3/28 EPS 204.4, P/E 39.3x, fair value 6,132-8,176.
Scenarios: Bear 4,866 (-39%); Base 7,563 (-6%); Bull 9,460 (+18%).
EV/EBITDA: 16x FY3/28 EBITDA 270.0 = enterprise value 4,320.0; per share 8,217 JPY.
Net debt -314.4 JPY: gross debt 106.0, cash 420.5.
"""

PM = """**Rating**: Hold

**Executive Summary**: **Rating**: Hold — full valuation with no cash-flow floor.

**Investment Thesis**: The operating inflection is genuine but the decision hinges on conversion, not demand, and the gap sits in the largest discretionary line of the model.

**Fair Value**: 6,132–8,176 (base 7,563)

**Time Horizon**: 3-6 months

**Decision framework**:

| Item | Assessment |
|---|---|
| New investor | Wait |
| Existing holder | Hold |

**Thesis invalidation**: A close below 7,700 invalidates the reading.

**Evidence to upgrade**: Q3 operating profit above 36bn with a 30% Electronics margin.

**Evidence to downgrade**: Q3 operating profit at or below 34.3bn.

**Downgrade applicability**: APPLICABLE
"""


def _state(**overrides):
    state = {
        "trade_date": "2026-11-04",
        "fundamentals_report": FUNDAMENTALS,
        "market_report": MARKET,
        "debate_summary": DEBATE,
        "research_package_artifact": PACKAGE,
        "final_trade_decision": PM,
    }
    state.update(overrides)
    return state


def _write(tmp_path, **overrides):
    write_report_tree(_state(**overrides), "TEST", tmp_path, settings=SETTINGS, html=False)
    return (tmp_path / "complete_report.md").read_text(encoding="utf-8"), (
        tmp_path / "appendix.md"
    ).read_text(encoding="utf-8")


def _sections(main: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for chunk in main.split("\n## ")[1:]:
        title, _, body = chunk.partition("\n")
        result[title.strip()] = body
    return result


def test_capped_markdown_keeps_newlines_bullets_tables_and_headings(tmp_path):
    main, _appendix = _write(tmp_path)
    sections = _sections(main)

    assert "### Latest results" in main
    assert "- " in sections["Catalysts and Risks"]
    assert "|---|" in main                      # a real table survives
    assert max(len(line) for line in main.splitlines()) < 400


def test_internal_heading_depth_is_preserved_when_demoted(tmp_path):
    main, _appendix = _write(tmp_path)

    # The source's ### subsection sits one level below its ## parent.
    assert "#### Segment detail" in main


def test_no_section_ends_mid_paragraph_list_or_table(tmp_path):
    main, _appendix = _write(tmp_path)

    for title, body in _sections(main).items():
        last = next((line for line in reversed(body.splitlines()) if line.strip()), "")
        assert not re.search(r"[a-z,;:]$", last), f"{title} ends mid-sentence: {last!r}"


def test_no_truncation_message_is_published(tmp_path):
    main, _appendix = _write(tmp_path)

    assert "truncated at" not in main.lower()


def test_the_technical_summary_carries_every_required_item(tmp_path):
    main, _appendix = _write(tmp_path)
    section = _sections(main)["Technical Summary"]

    for label in ("**Trend**", "**Momentum**", "**Volatility**",
                  "**Primary support and resistance**", "**Technical action**",
                  "**Confirmation and deterioration**", "**Material data limitation**"):
        assert label in section, label
    assert len(section.split()) <= 300


def test_the_technical_summary_is_never_dropped_to_fit_the_total_budget(tmp_path):
    main, _appendix = _write(
        tmp_path,
        research_package_artifact=PACKAGE + ("Filler model. " * 5000),
        fundamentals_report=FUNDAMENTALS + ("Filler analysis. " * 5000),
    )

    assert "## Technical Summary" in main
    assert "**Trend**" in _sections(main)["Technical Summary"]


def test_required_pm_fields_are_present(tmp_path):
    main, _appendix = _write(tmp_path)
    section = _sections(main)["Portfolio Manager Decision"]

    for field in ("**Rating**", "New investor:", "Existing holder:",
                  "**Core rationale**", "**Evidence to upgrade**", "**Evidence to downgrade**"):
        assert field in section, field


def test_section_limits_include_the_appendix_notice(tmp_path):
    main, _appendix = _write(tmp_path)
    sections = _sections(main)

    assert len(sections["Fundamental Summary"].split()) <= 1800
    assert len(sections["Debate Summary"].split()) <= 500
    assert len(sections["Portfolio Manager Decision"].split()) <= 400


def test_the_whole_report_including_header_stays_under_five_thousand_words(tmp_path):
    main, _appendix = _write(
        tmp_path,
        fundamentals_report=FUNDAMENTALS + ("Filler analysis. " * 3000),
        research_package_artifact=PACKAGE + ("Filler model. " * 3000),
    )

    assert len(main.split()) <= 5000


def test_a_dropped_component_leaves_an_appendix_pointer(tmp_path):
    main, appendix = _write(
        tmp_path,
        fundamentals_report=FUNDAMENTALS.replace(
            "H1 revenue rose 34% to 262.5 and operating profit rose 85% to 60.1.",
            "H1 revenue rose 34% to 262.5 and operating profit rose 85% to 60.1. "
            + "filler " * 4000,
        ),
    )

    assert "Further detail is in `appendix.md`" in main
    assert "filler" in appendix


def test_full_uncut_material_stays_in_the_appendix(tmp_path):
    _main, appendix = _write(tmp_path)

    assert "## 8. Key monitoring indicators" in appendix
    assert "## Strongest Bear Case" in appendix
    assert "| Zone | Basis |" in appendix


def test_broken_tags_and_vendor_jargon_are_repaired(tmp_path):
    main, _appendix = _write(tmp_path)

    assert "][fundamental" not in main
    assert "] [" in main
    assert "Japanese GAAP" in main
    assert "IFRS-equivalent" not in main


def test_audit_marks_never_reach_the_reader(tmp_path):
    state = {
        "fundamentals_report": "Net profit is [[128.0]] and revenue is [[652.0]].",
        "market_report": "",
        "debate_summary": "",
        "research_package_artifact": "",
        "final_trade_decision": (
            "**Rating**: Hold\n\n**Executive Summary**: Fair value [[7,992]] versus 8,037.\n\n"
            "**Investment Thesis**: Base value [[7,992]] is close to spot.\n\n"
            "| Item | Assessment |\n|---|---|\n| New investor | Wait |\n| Existing holder | Hold |\n"
        ),
    }
    main, _appendix = _write(tmp_path, **state)

    assert "[[" not in main and "]]" not in main
    assert "128.0" in main and "7,992" in main


def test_the_forecast_heading_appears_once(tmp_path):
    main, _appendix = _write(tmp_path)

    assert main.count("### Forecast versus guidance") == 1
    assert "### Independent forecast" in main


def test_technical_bullets_are_not_doubled_and_the_caveat_is_not_repeated(tmp_path):
    main, _appendix = _write(tmp_path)
    section = _sections(main)["Technical Summary"]

    assert "- - " not in main
    caveat = "Material data limitation"
    if caveat in section:
        assert section.count("split adjustment basis") <= 1


def test_sizing_language_is_redacted_in_the_main_report_only(tmp_path):
    state = {
        "final_trade_decision": (
            "**Rating**: Hold\n\n**Executive Summary**: Take a small starter position only.\n\n"
            "**Investment Thesis**: Cut by half if the trend breaks.\n\n"
            "**Decision framework**:\n\n| Item | Assessment |\n|---|---|\n"
            "| New investor | Wait |\n| Existing holder | Hold |\n"
        )
    }
    main, appendix = _write(tmp_path, **state)

    # No placeholder is shown to the reader; the clause is rewritten.
    assert "SIZING REDACTED" not in main
    assert "small starter" not in main
    assert "no target size is prescribed without portfolio context" in main
    # The original and the finding stay in the appendix.
    assert "small starter" in appendix
    assert "## Publication redactions" in appendix


def test_an_unresolved_debt_challenge_marks_ev_provisional(tmp_path):
    import json

    state = {
        "ev_input_diagnostics": json.dumps({
            "inputs": {"gross_debt": {"status": "EXTRACTED"}},
            "status": "EXTRACTED",
        }),
        "model_challenges": json.dumps({
            "challenges": [{
                "severity": "material",
                "challenge_type": "convertible bond classification unresolved",
                "affected_fields": ["net_debt.gross_debt"],
                "proposed_values": {},
            }]
        }),
    }
    main, _appendix = _write(tmp_path, **state)

    assert "EV/EBITDA: PROVISIONAL" in main
    assert "convertible bond classification unresolved" in main
    assert "detected but unresolved; not repaired" in main


def test_a_run_without_a_decision_still_publishes(tmp_path):
    main, _appendix = _write(tmp_path, final_trade_decision="")

    assert "No decision was produced in this run." in main
    assert "## Portfolio Manager Decision" in main
