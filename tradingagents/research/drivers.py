"""The driver registry: one economic driver, one identity, one propagation path.

The contract is deliberately structural. A bridge line names the economic driver it
moves, the stage it acts at, its segment and basis, and the evidence behind it.
Two lines may share a driver only when an explicit propagation relationship says
how one becomes the other; otherwise they are potential double counting.
"""

from __future__ import annotations

import re

REQUIRED_FIELDS = (
    "driver_id",
    "economic_driver_id",
    "model_stage",
    "affected_segment",
    "revenue_or_cost_basis",
    "capacity_source",
    "start_period",
    "utilisation_assumption",
    "price_mix_assumption",
    "incremental_cost_assumption",
    "depreciation_treatment",
    "ramp_cost_treatment",
    "evidence_class",
)


def slug(text) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")


def normalize(spec) -> None:
    """Fill the new fields from the old aliases; never invent a financial value."""
    for bridge in getattr(spec, "bridges", []) or []:
        for line in bridge.lines:
            if not line.model_stage:
                line.model_stage = str(bridge.metric or "unspecified")
            if not line.driver_id:
                line.driver_id = slug(line.driver or line.label)
            if not line.economic_driver_id:
                line.economic_driver_id = line.driver_id
            if not line.affected_segment:
                line.affected_segment = line.segment
            if not line.revenue_or_cost_basis:
                line.revenue_or_cost_basis = line.basis
            if not line.driver_aliases:
                line.driver_aliases = [slug(line.driver), slug(line.label)]


def records(spec) -> list[dict]:
    """One record per material bridge line."""
    rows: list[dict] = []
    for bridge in getattr(spec, "bridges", []) or []:
        for line in bridge.lines:
            if abs(float(line.value or 0.0)) < 1e-9:
                continue
            rows.append({
                "label": line.label,
                "bridge": bridge.metric,
                "target_period": bridge.target_period,
                "economic_driver_id": line.economic_driver_id,
                "model_stage": line.model_stage,
                "segment": line.affected_segment,
                "basis": line.revenue_or_cost_basis,
                "capacity_source": line.capacity_source,
                "start_period": line.start_period,
                "utilisation": line.utilisation_assumption,
                "price_mix": line.price_mix_assumption,
                "incremental_cost": line.incremental_cost_assumption,
                "depreciation": line.depreciation_treatment,
                "ramp": line.ramp_cost_treatment,
                "evidence_class": line.evidence_class,
                "propagation_ref": line.propagation_ref,
                "revenue_base_id": line.revenue_base_id,
                "capacity_block_id": line.capacity_block_id,
                "volume_increment_id": line.volume_increment_id,
            })
    return rows


def incomplete(spec) -> list[tuple[str, list[str]]]:
    """Material lines missing one or more required fields."""
    findings: list[tuple[str, list[str]]] = []
    for line in records(spec):
        missing = [field for field in REQUIRED_FIELDS if not str(line.get(field) or "").strip()]
        if missing:
            findings.append(
                (f"{line['bridge']} {line['target_period']}: {line['label']}", missing)
            )
    return findings


def overlaps(spec) -> list[tuple[str, list[str]]]:
    """Shared bases, or one economic driver used at two stages without a path."""
    findings: list[tuple[str, list[str]]] = []
    rows = records(spec)
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        for key in ("revenue_base_id", "capacity_block_id", "volume_increment_id"):
            value = str(row.get(key) or "").strip()
            if value:
                groups.setdefault((key, value), []).append(row)
    for (key, value), members in groups.items():
        if len(members) < 2:
            continue
        if all(str(member.get("propagation_ref") or "").strip() for member in members):
            continue
        findings.append((
            f"POTENTIAL_DOUBLE_COUNT: {key} {value} is shared by "
            + ", ".join(member["label"] for member in members),
            [member["label"] for member in members],
        ))
    by_driver: dict[str, list[dict]] = {}
    for row in rows:
        driver = str(row.get("economic_driver_id") or "").strip()
        if driver:
            by_driver.setdefault(driver, []).append(row)
    for driver, members in by_driver.items():
        stages = {str(member.get("model_stage") or "") for member in members}
        if len(stages) > 1 and not all(
            str(member.get("propagation_ref") or "").strip() for member in members
        ):
            findings.append((
                f"POTENTIAL_DOUBLE_COUNT: {driver} appears at stages "
                + ", ".join(sorted(stages))
                + " without an explicit propagation relationship",
                [member["label"] for member in members],
            ))
    return findings
