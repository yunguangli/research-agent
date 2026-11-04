"""Growth and incremental margin are different numbers (#13:09 report)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tradingagents.research.audit import terminology_findings
from tradingagents.research.metrics import period_metrics, render_metrics


def _model(*rows):
    return SimpleNamespace(rows=list(rows))


def _row(label, revenue, operating_profit):
    return SimpleNamespace(label=label, revenue=revenue, operating_profit=operating_profit)


@pytest.mark.unit
def test_growth_and_incremental_margin_are_computed_separately():
    model = _model(
        _row("H1 FY3/27", 100.0, 10.0),
        _row("H1 FY3/28", 197.0, 19.7),
    )

    metrics = period_metrics(model)

    assert len(metrics) == 1
    assert metrics[0].op_growth == pytest.approx(0.97)
    assert metrics[0].incremental_margin == pytest.approx(9.7 / 97.0)


@pytest.mark.unit
def test_a_flat_revenue_year_has_no_incremental_margin():
    metrics = period_metrics(_model(
        _row("FY3/27", 100.0, 10.0),
        _row("FY3/28", 100.0, 12.0),
    ))

    assert metrics[0].op_growth == pytest.approx(0.20)
    assert metrics[0].incremental_margin is None


@pytest.mark.unit
def test_calling_growth_an_incremental_margin_is_flagged():
    metrics = period_metrics(_model(
        _row("H1 FY3/27", 100.0, 10.0),
        _row("H1 FY3/28", 197.0, 19.7),
    ))

    findings = terminology_findings(
        "Electronics delivered a 97.0% incremental operating margin.", metrics,
        "Fundamentals Analyst",
    )

    assert len(findings) == 1
    assert findings[0].kind == "terminology"


@pytest.mark.unit
def test_the_real_incremental_margin_is_not_flagged():
    metrics = period_metrics(_model(
        _row("H1 FY3/27", 100.0, 10.0),
        _row("H1 FY3/28", 197.0, 19.7),
    ))

    findings = terminology_findings(
        "Electronics delivered a 10.0% incremental operating margin.", metrics,
        "Fundamentals Analyst",
    )

    assert findings == []


@pytest.mark.unit
def test_a_growth_rate_called_a_margin_next_to_nearly_doubled_is_flagged():
    metrics = period_metrics(_model(
        _row("H1 FY3/27", 100.0, 10.0),
        _row("H1 FY3/28", 152.0, 19.7),
    ))

    findings = terminology_findings(
        "Electronics operating profit nearly doubled on 52% revenue growth — a 97% "
        "incremental contribution margin at segment level.",
        metrics,
        "Fundamentals Analyst",
    )

    assert len(findings) == 1
    assert findings[0].number == pytest.approx(97.0)
    assert "growth rate" in findings[0].expected


@pytest.mark.unit
def test_an_implausible_incremental_margin_is_flagged():
    findings = terminology_findings(
        "Electronics revenue +52% with a 97% incremental segment contribution.",
        [], "Portfolio Manager",
    )

    assert len(findings) == 1
    assert "implausible" in findings[0].expected


@pytest.mark.unit
def test_the_metrics_table_prints_both_series():
    rendered = render_metrics(period_metrics(_model(
        _row("H1 FY3/27", 100.0, 10.0),
        _row("H1 FY3/28", 197.0, 19.7),
    )))

    assert "Operating-profit growth" in rendered
    assert "Incremental operating margin" in rendered
    assert "+97.0%" in rendered
