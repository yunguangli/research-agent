"""A reference report is a comparison baseline, never an input."""

from __future__ import annotations

from tradingagents.research import compare, kernel
from tradingagents.research.compare import Comparison
from tradingagents.research.valuation import value

from .test_research_kernel import spec

REFERENCE = """# Test Corp

## Annual forecast

| | FY25 | FY26 |
|---|---|---|
| Revenue | 120.0 | 130.0 |
| Operating profit | 15.0 | 16.0 |
| OP margin | 12.5% | 12.3% |
| EPS | 0.9 | 1.0 |

## Scenario analysis

| Scenario | Target |
|---|---|
| Base | 12.0 |

Fair-value range: 10-12
"""


def test_markdown_tables_are_read(tmp_path):
    path = tmp_path / "reference.md"
    path.write_text(REFERENCE, encoding="utf-8")
    tables = compare.load_tables(path)
    assert len(tables) == 2
    assert tables[0].headers == ["", "FY25", "FY26"]
    assert tables[1].rows[0][0] == "Base"


def test_matching_reference_agrees_cell_by_cell(tmp_path):
    path = tmp_path / "reference.md"
    path.write_text(REFERENCE, encoding="utf-8")
    model = kernel.run(spec())
    results = compare.compare(model, value(model), compare.load_tables(path))

    assert results
    assert all(result.ok for result in results), [r.describe() for r in results if not r.ok]
    labels = {result.label for result in results}
    assert "FY26 revenue" in labels
    assert "scenario Base target" in labels


def test_a_disagreeing_reference_is_flagged(tmp_path):
    path = tmp_path / "reference.md"
    path.write_text(REFERENCE.replace("| Revenue | 120.0 | 130.0 |",
                                      "| Revenue | 120.0 | 140.0 |"), encoding="utf-8")
    model = kernel.run(spec())
    results = compare.compare(model, value(model), compare.load_tables(path))
    difference = next(result for result in results if result.label == "FY26 revenue")
    assert not difference.ok
    assert difference.delta == -10.0


def test_cash_flow_rows_are_not_mistaken_for_operating_profit(tmp_path):
    path = tmp_path / "reference.md"
    path.write_text(
        "| | FY25 |\n|---|---|\n| Operating cash flow | 11.0 |\n| Free cash flow | 8.0 |\n",
        encoding="utf-8",
    )
    model = kernel.run(spec())
    results = compare.compare(model, value(model), compare.load_tables(path))
    assert results == []


def test_quarterly_tables_are_not_compared_as_annual(tmp_path):
    path = tmp_path / "reference.md"
    path.write_text(
        "| | Q1 FY25 | Q2 FY25 |\n|---|---|---|\n| Revenue | 28.0 | 27.0 |\n",
        encoding="utf-8",
    )
    model = kernel.run(spec())
    assert compare.compare(model, value(model), compare.load_tables(path)) == []


def test_eps_rows_pick_the_right_share_basis(tmp_path):
    reference = tmp_path / "reference.md"
    reference.write_text(
        "| | FY26 |\n|---|---|\n| EPS, full-share basis | 1.0 |\n| EPS, company-style basis | 1.0 |\n",
        encoding="utf-8",
    )
    model_spec = spec()
    model_spec.share_bases[0].name = "full_share"
    model_spec.share_bases.append(type(model_spec.share_bases[0])("company_style", 10.0))
    model = kernel.run(model_spec)
    results = compare.compare(model, value(model), compare.load_tables(reference))
    assert {result.label for result in results} == {"FY26 eps (full_share)", "FY26 eps (company_style)"}


def test_fair_value_is_read_from_prose(tmp_path):
    path = tmp_path / "reference.md"
    path.write_text(REFERENCE, encoding="utf-8")
    assert compare.fair_value_from_text(path) == (10.0, 12.0)


def test_comparison_tolerance_is_reported():
    ok = Comparison("x", reference=100.0, model=100.1, delta=0.1, tolerance=0.15)
    bad = Comparison("x", reference=100.0, model=101.0, delta=1.0, tolerance=0.15)
    assert ok.ok and not bad.ok
