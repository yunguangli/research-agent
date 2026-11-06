"""Model-spec coercion and bounded-call regressions for the 2383/6857 failures."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from tradingagents.agents.schemas import DecisionTrigger
from tradingagents.graph.trading_graph import _invoke_with_timeout
from tradingagents.research.schemas import Line


def test_percentage_utilization_and_qualitative_assumptions_are_preserved():
    line = Line.from_dict({
        "label": "Capacity volume ramp",
        "value": 10.0,
        "utilization": "100%",
        "price_mix": "flat ASP on base volume",
        "incremental_cost": "raw-material pass-through",
    })

    assert line.utilization == 1.0
    assert line.price_mix is None
    assert line.incremental_cost is None
    assert line.price_mix_assumption == "flat ASP on base volume"
    assert line.incremental_cost_assumption == "raw-material pass-through"


def test_a_numeric_price_mix_still_parses():
    line = Line.from_dict({"label": "x", "value": 1.0, "price_mix": "3.5"})

    assert line.price_mix == 3.5
    assert line.price_mix_assumption == ""


def test_a_decision_trigger_accepts_minor_severity():
    trigger = DecisionTrigger(
        kind="RATING_REVIEW_TRIGGER", description="x", severity="minor"
    )

    assert trigger.severity == "minor"


def test_a_stalled_model_call_is_bounded():
    class Slow:
        def invoke(self, _prompt):
            time.sleep(0.5)
            return SimpleNamespace(content="x")

    with pytest.raises(TimeoutError):
        _invoke_with_timeout(Slow(), "prompt", 0.05)
