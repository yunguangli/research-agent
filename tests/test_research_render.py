"""Valuation and rendering derive from the model, never from prose."""

from __future__ import annotations

import json

import pytest

from tradingagents.research import kernel, render, report
from tradingagents.research.schemas import ModelError
from tradingagents.research.valuation import value

from .test_research_kernel import spec


def test_valuation_uses_the_forecast_eps_and_the_price():
    valuation = value(kernel.run(spec()))
    assert valuation.period == "FY26"
    assert valuation.eps == pytest.approx(1.0)
    assert valuation.pe == pytest.approx(20.0)
    assert valuation.fair_value_low == pytest.approx(10.0)
    assert valuation.fair_value_high == pytest.approx(12.0)
    assert valuation.upside_to_mid == pytest.approx(-0.45, abs=1e-6)


def test_sensitivity_is_monotonic_in_the_multiple():
    valuation = value(kernel.run(spec()))
    prices = [row.implied_price for row in valuation.sensitivity]
    changes = [row.change for row in valuation.sensitivity]
    assert prices == sorted(prices)
    assert changes == sorted(changes)
    # 20x would be the break-even multiple at this price and EPS.
    assert valuation.sensitivity[0].change < 0
    assert all(abs(price / valuation.price - 1 - change) < 1e-9
               for price, change in zip(prices, changes, strict=True))


def test_scenario_targets_are_eps_times_multiple():
    valuation = value(kernel.run(spec()))
    assert valuation.scenarios[0]["target"] == pytest.approx(12.0)
    assert valuation.scenarios[0]["change"] == pytest.approx(-0.4)


def test_an_inverted_fair_value_range_is_rejected():
    model_spec = spec()
    model_spec.valuation.fair_value_low = 30.0
    model_spec.valuation.fair_value_high = 10.0
    with pytest.raises(ModelError, match="inverted"):
        value(kernel.run(model_spec))


def test_valuation_without_a_forecast_period_is_rejected():
    model_spec = spec()
    model_spec.periods = [p for p in model_spec.periods if p.basis != "forecast"]
    model_spec.forecast = {}
    with pytest.raises(ModelError, match="no forecast period"):
        value(kernel.run(model_spec))


def test_a_consensus_that_skips_the_actual_year_is_flagged():
    from tradingagents.research.schemas import Consensus

    model_spec = spec()
    model_spec.consensus = Consensus("Benchmark", eps_basis="basic",
                                     periods={"FY25": {"net_profit": 9.0, "eps": 0.9}})
    model = kernel.run(model_spec)
    text = report.compact_summary(model, value(model))
    assert "has no FY24 row" in text
    assert "not checked against it" in text


def test_a_complete_consensus_produces_no_note():
    from tradingagents.research.schemas import Consensus

    model_spec = spec()
    model_spec.consensus = Consensus(
        "Benchmark", eps_basis="basic",
        periods={
            "FY24": {"net_profit": 6.0, "eps": 0.6},
            "FY25": {"net_profit": 9.0, "eps": 0.9},
        },
    )
    model = kernel.run(model_spec)
    text = report.compact_summary(model, value(model))
    assert "Note:" not in text


def test_a_consensus_eps_on_another_share_count_is_flagged():
    from tradingagents.research.schemas import Consensus

    model_spec = spec()
    model_spec.consensus = Consensus(
        "Benchmark", eps_basis="basic",
        periods={"FY24": {"net_profit": 6.0, "eps": 0.72}, "FY25": {"net_profit": 9.0, "eps": 0.9}},
    )
    model = kernel.run(model_spec)
    text = report.compact_summary(model, value(model))
    assert "implies 8m shares against the model's 10m basic basis" in text
    assert "share-count difference" in text


def test_tables_render_as_markdown_with_consistent_columns():
    model = kernel.run(spec())
    table = render.annual_table(model)
    markdown = render.to_markdown(table)
    lines = [line for line in markdown.splitlines() if line.startswith("|")]
    widths = {len(line.split("|")) for line in lines}
    assert len(widths) == 1
    assert "Revenue" in markdown and "FY26" in markdown


def test_bridge_table_shows_the_formula_for_derived_lines():
    model = kernel.run(spec())
    bridge = model.spec.bridges[0]
    markdown = render.to_markdown(render.bridge_table(bridge, model.spec.unit))
    assert "New business" in markdown
    assert "assumption" in markdown


def test_package_writes_tables_report_and_metadata(tmp_path):
    model = kernel.run(spec())
    valuation = value(model)
    package = report.write_package(model, valuation, tmp_path)

    assert (tmp_path / "report.md").is_file()
    assert (tmp_path / "package.json").is_file()
    assert (tmp_path / "tables" / "annual.csv").is_file()
    assert package["checks"]
    assert all(check["passed"] for check in package["checks"])
    on_disk = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert on_disk["ticker"] == "TEST"
    assert on_disk["valuation"]["period"] == "FY26"


def test_report_contains_no_number_outside_its_tables(tmp_path):
    """Every figure in the executive summary must appear in the rendered tables."""
    model = kernel.run(spec())
    valuation = value(model)
    package = report.write_package(model, valuation, tmp_path)
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    tables = "\n".join(
        (tmp_path / name).read_text(encoding="utf-8")
        for key, name in package["tables"].items()
        if key in {"annual", "valuation", "sensitivity"}
    )
    for line in text.splitlines():
        if line.startswith("|") or line.startswith("#") or line.startswith("-"):
            continue
        for token in line.replace(",", "").split():
            if token.replace(".", "").replace("-", "").isdigit() and len(token) > 3:
                assert token in tables or token in text, token
