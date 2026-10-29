"""Golden test: reproduce the reference report's model sections from its inputs.

The fixture is confidential and lives outside the repository, so this test skips
unless ``RESEARCH_GOLDEN_DIR`` points at a directory holding
``fujikura/model.json``. It is the acceptance test for the kernel: if a rounding
rule, a unit conversion, or a bridge changes, this fails.

The variable is deliberately not prefixed ``TRADINGAGENTS_``: ``tests/conftest.py``
blanks every such variable so a contributor's settings cannot leak into the suite.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tradingagents.research import kernel, report
from tradingagents.research.valuation import value

SPEC = Path(os.environ.get("RESEARCH_GOLDEN_DIR", "")) / "fujikura" / "model.json"

pytestmark = pytest.mark.skipif(
    not str(SPEC.parent) or not SPEC.is_file(),
    reason="local golden fixture not available (set RESEARCH_GOLDEN_DIR)",
)


@pytest.fixture(scope="module")
def model():
    return kernel.run(report.load_spec(SPEC))


@pytest.fixture(scope="module")
def valuation(model):
    return value(model)


def test_annual_forecast_matches_the_reference(model):
    fy26 = model.row("FY3/26")
    fy27 = model.row("FY3/27")
    fy28 = model.row("FY3/28")

    assert fy26.revenue == pytest.approx(1182.4, abs=0.1)
    assert fy27.revenue == pytest.approx(1755.0, abs=0.1)
    assert fy27.operating_profit == pytest.approx(432.0, abs=0.1)
    assert fy27.net_profit == pytest.approx(326.0, abs=0.1)
    assert fy28.revenue == pytest.approx(1650.0, abs=0.1)
    assert fy28.operating_profit == pytest.approx(410.0, abs=0.1)
    assert fy28.net_profit == pytest.approx(310.0, abs=0.1)
    assert fy27.operating_margin == pytest.approx(24.6, abs=0.05)
    assert fy28.operating_margin == pytest.approx(24.8, abs=0.05)


def test_dual_basis_eps_matches_the_reference(model):
    assert model.row("FY3/28").eps["full_share"] == pytest.approx(174.6, abs=0.1)
    assert model.row("FY3/28").eps["company_style"] == pytest.approx(187.2, abs=0.1)


def test_quarterly_build_reproduces_the_reported_quarters(model):
    assert model.quarterly_comparison == []
    quarters = model.spec.quarterly
    assert quarters["Q2 FY3/27"].revenue == pytest.approx(419.0, abs=0.1)
    assert quarters["Q2 FY3/27"].operating_profit == pytest.approx(93.2, abs=0.1)
    assert quarters["Q3 FY3/27"].revenue == pytest.approx(455.0, abs=0.1)
    assert quarters["Q4 FY3/27"].net_profit == pytest.approx(92.0, abs=0.1)


def test_valuation_matches_the_reference(valuation):
    assert valuation.pe == pytest.approx(28.6, abs=0.05)
    assert valuation.fair_value_low == pytest.approx(4900, abs=15)
    assert valuation.fair_value_high == pytest.approx(5600, abs=15)


def test_every_check_passes(model):
    assert all(check.passed for check in model.checks), [
        c.describe() for c in model.checks if not c.passed
    ]
