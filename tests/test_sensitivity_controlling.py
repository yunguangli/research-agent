"""Controlling candidacy is fixed before verification; eligibility is separate."""

from __future__ import annotations

from tradingagents.research import status
from tradingagents.research.sensitivity import Sensitivity, mark_controlling


def _row(row_id: str, impact: float, verified: bool = True) -> Sensitivity:
    return Sensitivity(
        sensitivity_id=row_id,
        driver=row_id,
        model_field="field",
        base_assumption=1.0,
        alternative_assumption=0.0,
        fair_value_impact=impact,
        verification_status="VERIFIED" if verified else "UNVERIFIED",
    )


def test_the_top_three_are_candidates_and_a_failed_candidate_stays_visible():
    rows = [
        _row("largest", -300.0, verified=False),
        _row("second", -200.0),
        _row("third", -100.0),
        _row("fourth", -50.0),
    ]

    mark_controlling(rows, top=3)

    assert [row.sensitivity_id for row in rows if row.controlling_candidate] == [
        "largest", "second", "third"
    ]
    assert rows[0].controlling_candidate is True
    assert rows[0].decision_eligible is False
    assert rows[1].decision_eligible is True
    assert rows[3].controlling_candidate is False


def test_a_failed_candidate_makes_only_the_controlling_status_unverified():
    overall = status.assess(
        model_status="VERIFIED",
        basis={
            "market_cap_basis": "period_end_net",
            "forecast_eps_basis": "diluted_average",
            "dilution_sensitivity_basis": "diluted_average",
        },
        has_band=True,
        price=100.0,
        sensitivity_count=4,
        propagation_ok=False,
        controlling_propagation_ok=False,
    )

    assert overall["sensitivity_model_status"] == "UNVERIFIED"
    assert overall["controlling_sensitivity_status"] == "UNVERIFIED"


def test_three_controlling_pass_and_seven_fail_keeps_controlling_verified():
    result = status.assess(
        model_status="VERIFIED",
        basis={
            "market_cap_basis": "period_end_net",
            "forecast_eps_basis": "diluted_average",
            "dilution_sensitivity_basis": "diluted_average",
        },
        has_band=True,
        price=100.0,
        sensitivity_count=10,
        propagation_ok=False,
        controlling_propagation_ok=True,
    )

    assert result["sensitivity_model_status"] == "UNVERIFIED"
    assert result["controlling_sensitivity_status"] == "VERIFIED"


def test_all_rows_verified_makes_both_statuses_verified():
    result = status.assess(
        model_status="VERIFIED",
        basis={
            "market_cap_basis": "period_end_net",
            "forecast_eps_basis": "diluted_average",
            "dilution_sensitivity_basis": "diluted_average",
        },
        has_band=True,
        price=100.0,
        sensitivity_count=3,
        propagation_ok=True,
        controlling_propagation_ok=True,
    )

    assert result["sensitivity_model_status"] == "VERIFIED"
    assert result["controlling_sensitivity_status"] == "VERIFIED"


def test_failed_rows_are_not_decision_eligible():
    rows = [_row("ok", -100.0), _row("bad", -500.0, verified=False)]
    mark_controlling(rows, top=3)

    eligible = [row.sensitivity_id for row in rows if row.decision_eligible]
    assert eligible == ["ok"]
    assert "bad" not in eligible
