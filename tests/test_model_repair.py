"""Tests for the model-truth layer: periods, share bases, conflicts, repair."""

from __future__ import annotations

import pytest

from tradingagents.research import conflicts, kernel, periods, repair, shares
from tradingagents.research.audit import audit_text
from tradingagents.research.schemas import (
    Bridge,
    Consensus,
    Financials,
    Line,
    ModelSpec,
    Period,
    Scenario,
    ShareBase,
    ValuationSpec,
)


def spec() -> ModelSpec:
    return ModelSpec(
        ticker="TEST",
        company="Test Corp",
        currency="USD",
        unit="USD_m",
        fiscal_year_end_month=12,
        periods=[
            Period("FY25", "2025-01-01", "2025-12-31", "actual"),
            Period("Q1 FY26", "2026-01-01", "2026-03-31", "forecast", kind="quarter"),
            Period("FY26", "2026-01-01", "2026-12-31", "forecast"),
        ],
        actuals={"FY25": Financials(revenue=100.0, operating_profit=10.0, net_profit=6.0)},
        # Guidance for a forecast year merges into that year's row, so it must
        # state the same topline the bridges build; the consensus below is what
        # creates the recorded disagreement.
        guidance={"FY26": Financials(revenue=110.0, operating_profit=12.0, net_profit=8.0)},
        forecast={"FY26": Financials(net_profit=8.0)},
        bridges=[
            Bridge("revenue", "FY25", "FY26", 100.0, [Line("growth", 10.0)], declared_total=110.0),
            Bridge("operating_profit", "FY25", "FY26", 10.0, [Line("growth", 2.0)],
                   declared_total=12.0),
        ],
        share_bases=[
            ShareBase("basic", 10.0, basic_weighted_average=10.0),
            ShareBase("diluted", 10.6, basic_weighted_average=10.0,
                      diluted_weighted_average=10.6, potential_cb_shares=0.6),
        ],
        scenarios=[
            Scenario("Base", "steady", eps=0.80, multiple=11.0, period="FY26"),
            Scenario("Bear", "weak", eps=0.65, multiple=10.0, period="FY26"),
        ],
        consensus=Consensus("broker", periods={"FY26": {
            "revenue": 108.0, "operating_profit": 11.5, "net_profit": 7.0,
        }}),
        eps_basis="basic",
        warnings=[
            "FY26: the forecast lands on guidance because both are built from the same "
            "disclosed drivers; this is stated so the agreement is not read as adoption.",
        ],
        valuation=ValuationSpec(
            price=20.0,
            fair_value_multiple_low=10.0,
            fair_value_multiple_high=12.0,
            fair_value_low=8.0,
            fair_value_high=9.6,
            fair_value_basis="basic",
            fair_value_period="FY26",
            market_cap_basis="diluted",
            forecast_eps_basis="basic",
        ),
    )


# --- periods ------------------------------------------------------------------


def test_a_march_year_end_gets_the_fy3_label_and_its_dates():
    year = periods.fiscal_year(3, 2028)

    assert (year.label, year.start, year.end) == ("FY3/28", "2027-04-01", "2028-03-31")


def test_a_december_year_end_keeps_the_short_label():
    assert periods.fiscal_year(12, 2026).label == "FY26"
    assert periods.canonical_label("2026-12-31", 12) == "FY26"


def test_a_company_label_that_disagrees_with_its_dates_is_flagged():
    model = spec()
    model.periods[1] = Period("FY2027", "2026-01-01", "2026-12-31", "forecast")

    warnings = periods.audit_periods(model)

    assert any("FY2027 ends 2026-12-31, which is FY26" in message for message in warnings)


# --- share bases --------------------------------------------------------------


def test_the_market_cap_and_eps_bases_are_named_and_their_gap_is_reported():
    model = spec()

    assert shares.market_cap_basis(model) == "diluted"
    assert shares.forecast_eps_basis(model) == "basic"
    assert shares.divergence(model) == pytest.approx(0.06)
    assert any("P/E printed beside them" in message for message in shares.basis_warnings(model))


# --- conflicts ----------------------------------------------------------------


def test_the_conflict_register_records_guidance_consensus_and_model():
    result = kernel.run(spec())

    entries = {(c.period, c.metric) for c in result.conflicts}

    assert ("FY26", "revenue") in entries      # 105 guidance vs 108 consensus vs 110 model
    assert ("FY26", "operating_profit") in entries
    assert ("FY26", "net_profit") in entries   # 7.0 vs 8.0
    revenue = next(c for c in result.conflicts if c.metric == "revenue")
    assert revenue.controlling_value == 110.0
    assert revenue.controlling_source.startswith("model")


def test_the_register_renders_a_table_and_an_empty_one_renders_nothing():
    rendered = conflicts.render(kernel.run(spec()).conflicts)

    assert "| FY26 | revenue |" in rendered
    assert conflicts.render([]) == ""


def test_period_metrics_pairs_only_annual_rows():
    from tradingagents.research.metrics import period_metrics, render_metrics

    metrics = period_metrics(kernel.run(spec()))

    assert [row.label for row in metrics] == ["FY26"]
    assert "Q1 FY26" not in render_metrics(metrics)


# --- repair -------------------------------------------------------------------


def _challenge(values: dict[str, float], severity: str = "material") -> repair.ModelChallenge:
    return repair.ModelChallenge(
        challenge_type="share_count",
        severity=severity,
        affected_fields=["FY26 EPS"],
        proposed_correction="correct the share count",
        proposed_values=values,
    )


def test_a_repair_applies_values_revalidates_and_reports_what_moved():
    repaired, result, changes = repair.repair_model(
        spec(),
        [_challenge({
            "forecast.FY26.net_profit": 9.0,
            "valuation.fair_value_low": 9.0,
            "valuation.fair_value_high": 10.8,
            "scenarios.Base.eps": 0.90,
        })],
    )

    fields = {change["field"] for change in changes}
    assert "FY26 net_profit" in fields
    assert "FY26 EPS (basic)" in fields
    assert repaired.forecast["FY26"].net_profit == 9.0
    assert result.row("FY26").eps["basic"] == pytest.approx(0.9)


def test_a_repair_that_breaks_an_identity_is_rejected():
    with pytest.raises(repair.RepairError):
        repair.repair_model(
            spec(),
            [_challenge({"forecast.FY26.net_profit": 20.0})],  # band now disagrees
        )


def test_an_unknown_spec_path_is_refused():
    with pytest.raises(repair.RepairError):
        repair.repair_model(spec(), [_challenge({"valuation.nonsense": 1.0})])


def test_superseded_values_are_the_old_values_of_changed_fields():
    _repaired, _result, changes = repair.repair_model(
        spec(),
        [_challenge({
            "forecast.FY26.net_profit": 9.0,
            "valuation.fair_value_low": 9.0,
            "valuation.fair_value_high": 10.8,
            "scenarios.Base.eps": 0.90,
        })],
    )

    replaced = set(repair.superseded_values(changes))

    assert 8.0 in replaced       # old net profit
    assert 0.8 in replaced       # old EPS


def test_the_audit_calls_a_v1_figure_superseded_not_a_failure():
    stats: dict = {}

    findings = audit_text(
        "The model shows [[8.0]] net profit.", "Fundamentals Analyst",
        known={9.0}, stats=stats, superseded={8.0},
    )

    assert findings == []
    assert stats["superseded"] == 1
    assert stats["superseded_values"] == [8.0]
    assert stats["checked"] == 1


def test_the_deterministic_addendum_lists_every_change():
    text = repair.render_addendum(
        [{"field": "FY26 EPS (basic)", "old": 0.8, "new": 0.9}], ["Cell 6 timing"]
    )

    assert "FY26 EPS (basic): 0.8 → 0.9" in text
    assert "Cell 6 timing" in text
    assert "superseded" in text.lower()


def test_an_unapplied_challenge_does_not_claim_a_repair():
    text = repair.render_addendum([], ["Cut the Ceramics segment profit"])

    assert "not applied" in text
    assert "Model repair (v2)" not in text
    assert "Cut the Ceramics segment profit" in text


def test_an_addendum_that_invents_a_number_is_not_grounded():
    addendum = repair.ModelRepairAddendum(
        replacement_conclusions=["Fair value is now 12,345."]
    )

    assert not repair.addendum_is_grounded(addendum, allowed={0.9, 9.0})


def test_an_addendum_that_prints_machine_precision_is_not_grounded():
    addendum = repair.ModelRepairAddendum(
        replacement_conclusions=["EPS is 213.77256001401787 at the same price."]
    )

    assert not repair.addendum_is_grounded(addendum, allowed={213.77256001401787})


def test_the_addendum_prompt_shows_values_as_the_tables_print_them():
    assert repair.shown(213.77256001401787) == "213.8"


# --- challenge verification ---------------------------------------------------


def _extracted(**overrides) -> repair.ModelChallenge:
    base = {
        "challenge_type": "share_count",
        "severity": "material",
        "affected_fields": ["FY26 EPS"],
        "proposed_correction": "correct the share count",
        "proposed_values": {"forecast.FY26.net_profit": 9.0},
    }
    base.update(overrides)
    return repair.ModelChallenge(**base)


def test_a_challenge_naming_a_missing_field_is_unverified():
    verified, unverified, _ = repair.verify_challenges(
        spec(), [_extracted(proposed_values={"valuation.nope": 1.0})]
    )

    assert verified == []
    assert len(unverified) == 1


def test_a_stated_value_that_does_not_match_v1_is_unverified():
    verified, unverified, _ = repair.verify_challenges(
        spec(), [_extracted(stated_model_value=123.0)]
    )

    assert verified == []
    assert len(unverified) == 1


def test_a_challenge_whose_stated_value_matches_v1_is_verified():
    verified, unverified, _ = repair.verify_challenges(
        spec(), [_extracted(stated_model_value=8.0)]
    )

    assert len(verified) == 1
    assert unverified == []


def test_a_structural_challenge_is_recorded_not_applied():
    verified, unverified, structural = repair.verify_challenges(
        spec(),
        [_extracted(requires_rebuild=True, proposed_values={}, severity="blocking")],
    )

    assert verified == [] and unverified == []
    assert len(structural) == 1


def test_a_scenario_disagreement_is_not_a_repair():
    verified, unverified, structural = repair.verify_challenges(
        spec(), [_extracted(severity="scenario_only", proposed_values={}, requires_rebuild=False)]
    )

    assert verified == [] and unverified == [] and structural == []


def test_the_extraction_schema_requires_an_explicit_status():
    from pydantic import ValidationError

    from tradingagents.research.repair import ModelChallengeList

    with pytest.raises(ValidationError):
        ModelChallengeList(challenges=[])


def test_an_extraction_miss_is_a_failure_not_an_empty_success(monkeypatch):
    from unittest.mock import MagicMock

    from tradingagents.graph.trading_graph import TradingAgentsGraph

    structured = MagicMock()
    structured.invoke.return_value = None          # twice: the call and the retry
    llm = MagicMock()
    llm.with_structured_output.return_value = structured

    status, challenges = TradingAgentsGraph._extract_challenges(
        object.__new__(TradingAgentsGraph), llm, "report", "package"
    )

    assert (status, challenges) == ("failure", [])
    assert structured.invoke.call_count == 2


def test_no_challenges_must_be_explicit(monkeypatch):
    from unittest.mock import MagicMock

    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.research.repair import ModelChallengeList

    result = ModelChallengeList(status="no_challenges_found", challenges=[])
    structured = MagicMock()
    structured.invoke.return_value = result
    llm = MagicMock()
    llm.with_structured_output.return_value = structured

    status, challenges = TradingAgentsGraph._extract_challenges(
        object.__new__(TradingAgentsGraph), llm, "report", "package"
    )

    assert status == "no_challenges_found" and challenges == []

