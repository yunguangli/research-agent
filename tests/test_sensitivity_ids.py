"""Ranked sensitivities are keyed by stable ids, and legacy aliases still resolve."""

from __future__ import annotations

import pytest

from tradingagents.research import sensitivity
from tradingagents.research.schemas import Bridge, Line

from .test_research_kernel import spec


def ranked():
    model_spec = spec(
        bridges=[
            Bridge("revenue", "FY25", "FY26", 120.0,
                   [Line("New business", 10.0)], declared_total=130.0),
            Bridge(
                "operating_profit", "FY25", "FY26", 15.0,
                [Line("Cell 8 loading", 3.0, driver="Cell 8 loading", alternative=2.0)],
                declared_total=18.0,
            ),
        ]
    )
    items, gaps = sensitivity.rank(model_spec, top=5)
    return items, gaps


def test_the_id_names_the_bridge_and_the_assumption_not_the_display_string():
    items, _gaps = ranked()
    item = next(entry for entry in items if entry.driver.startswith("Cell 8 loading"))

    assert item.sensitivity_id == "operating_profit_fy26_cell_8_loading_op_contribution"
    assert item.bridge_id == "operating_profit_fy26"
    assert item.model_field_id == "cell_8_loading"
    assert "cell_8_loading" in item.aliases


def test_a_legacy_alias_resolves_to_the_canonical_id():
    items, _gaps = ranked()
    item = next(entry for entry in items if entry.driver.startswith("Cell 8 loading"))

    assert sensitivity.canonical_id("cell_8_loading", items) == item.sensitivity_id
    assert sensitivity.alias_map(items)["cell_8_loading"] == item.sensitivity_id


def test_an_unknown_id_raises_the_named_error():
    items, _gaps = ranked()

    with pytest.raises(sensitivity.SensitivityError) as error:
        sensitivity.canonical_id("not_a_driver", items)
    assert error.value.code == "SENSITIVITY_FIELD_NOT_FOUND"


def test_a_driver_without_an_alternative_is_a_named_research_gap():
    model_spec = spec(
        bridges=[
            Bridge("revenue", "FY25", "FY26", 120.0,
                   [Line("New business", 10.0)], declared_total=130.0),
            Bridge(
                "operating_profit", "FY25", "FY26", 15.0,
                [Line("Unmodelled driver", 1.0, driver="Unmodelled driver")],
                declared_total=16.0,
            ),
        ]
    )
    _items, gaps = sensitivity.rank(model_spec)

    assert any(
        gap.startswith("RESEARCH_GAP_NO_PROPAGATION") for gap in gaps
    ), gaps
