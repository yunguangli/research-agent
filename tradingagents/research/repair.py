"""The model repair gate: turn the analyst's explicit challenges into a v2 model.

The gate does not invent numbers. The Fundamentals Analyst states which fields it
believes are wrong and, when it can, the value it believes is right; this module
applies those values to a copy of the spec, re-runs the kernel, and reports what
changed. A challenge that needs a structural rebuild (a capacity start date, a
segment re-cut) is recorded as unresolved: Python cannot rebuild it, and the
debate must see it as an open issue rather than a repaired fact.

Repair is versioned. The v1 spec stays on disk, the v2 spec is written beside it,
and every field that moved is listed with its old and new value so the audit can
call a v1-only figure superseded instead of hallucinated.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from . import kernel, price_authority
from .schemas import ModelError, ModelSpec

logger = logging.getLogger(__name__)

MATERIAL_SEVERITIES = {"blocking", "material"}
SCENARIO_FIELDS = {"eps", "multiple", "target_price"}
VALUATION_FIELDS = {
    "price",
    "fair_value_low",
    "fair_value_high",
    "fair_value_multiple_low",
    "fair_value_multiple_high",
}
SHARE_FIELDS = {
    "shares_m",
    "basic_weighted_average",
    "diluted_weighted_average",
    "post_period_conversion",
    "potential_cb_shares",
}
SEGMENT_FIELDS = {"revenue", "operating_profit"}


class RepairError(RuntimeError):
    """A repair that cannot be applied safely; the caller keeps v1 or reports it."""


class ModelChallenge(BaseModel):
    """One explicit defect the Fundamentals Analyst found in the model."""

    challenge_type: str = Field(description="e.g. share_count, period_mapping, bridge, tax")
    severity: Literal["blocking", "material", "minor", "scenario_only"]
    affected_fields: list[str] = Field(default_factory=list)
    # The model outputs this challenge makes unusable (e.g. forecast_eps,
    # pe_valuation). Empty means the affected fields decide.
    blocked_outputs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    # What the analyst says the model currently shows. Checked against v1 before
    # any override is accepted.
    stated_model_value: float | str | None = None
    proposed_correction: str = ""
    requires_rebuild: bool = False
    # Where a proposed price came from. A price correction is only a correction
    # if its source outranks the value already in the model; an older research
    # note cannot replace the analysis-date close.
    source_class: str = ""          # USER_EXPLICIT | VERIFIED_CLOSE | REPORT_PRICE | SPEC_PRICE | DERIVED_FROM_MARKET_CAP
    as_of: str = ""                 # the date the proposed value belongs to
    source_ref: str = ""            # the document/page that states it
    rejection_reason: str = ""      # set by verification, never by the model
    # Scalar overrides keyed by the spec paths listed in the extraction prompt.
    # A challenge with no override is recorded, not applied.
    proposed_values: dict[str, float] = Field(default_factory=dict)


class ModelChallengeList(BaseModel):
    """The extraction result. ``status`` is mandatory: an empty answer is not success."""

    status: Literal["challenges_found", "no_challenges_found"]
    challenges: list[ModelChallenge] = Field(default_factory=list)


class ModelRepairAddendum(BaseModel):
    affected_conclusions: list[str] = Field(default_factory=list)
    replacement_conclusions: list[str] = Field(default_factory=list)
    unchanged_conclusions: list[str] = Field(default_factory=list)


EXTRACTION_PROMPT = r"""Read the Fundamentals Analyst report below and extract ONLY the places where
it explicitly challenges a figure in the verified model.

Do not create new criticisms. Do not turn a forecast disagreement into a defect.
A repair is only for a demonstrated error:
- a wrong share count or diluted basis,
- a period mapped to the wrong fiscal year,
- a bridge that does not sum,
- a segment total that does not reconcile,
- a tax or non-operating line that contradicts the reported statements,
- a contribution included before it is commissioned.

These are NOT repairs (leave them out): "the incremental margin looks optimistic",
"consensus may be too high", "the technical trend is weak", "customer concentration
is a risk", "the valuation multiple is full". Those belong to the debate.

For each genuine challenge return:
- challenge_type
- severity: blocking (a number that changes the valuation), material (changes a
  reported line) or minor (a label or presentation)
- affected_fields: the model fields, e.g. "FY3/28 EPS", "share count"
- evidence_refs: only sources the report itself cites
- proposed_correction: one sentence
- requires_rebuild: true when the fix needs a new build (a segment re-cut, a new
  capacity start date), false when a scalar value corrects it
- proposed_values: {spec path: number} for scalar fixes only
- for a challenge that proposes "valuation.price", also return:
  - source_class: USER_EXPLICIT, VERIFIED_CLOSE, REPORT_PRICE, SPEC_PRICE or
    DERIVED_FROM_MARKET_CAP
  - as_of: the date the proposed price belongs to, ISO format
  - source_ref: the document id and page that states it
  A price quoted in an older research note is REPORT_PRICE. A price obtained by
  dividing a market capitalisation by the share count is DERIVED_FROM_MARKET_CAP.
  Neither may replace the verified analysis-date close; state the class honestly
  rather than calling an older value a close.

The only spec paths accepted in proposed_values:
- "share_bases.<name>.shares_m" (and the weighted-average / dilution fields)
- "actuals.<period>.<revenue|operating_profit|ordinary_profit|net_profit|eps>"
- "forecast.<period>.<same metrics>"
- "guidance.<period>.<same metrics>"
- "valuation.price" / "valuation.fair_value_low" / "valuation.fair_value_high"
- "scenarios.<name>.eps" / "scenarios.<name>.multiple" / "scenarios.<name>.target_price"
- "segments.<period>.<segment>.revenue" / "segments.<period>.<segment>.operating_profit"

For a segment that conflicts with reported year-to-date results plus the remaining
company guidance, give the corrected segment values. For a contribution that starts
before the disclosed commissioning period, set the unsupported segment revenue to 0
and name the commissioning period in proposed_correction.

If the report discusses uncertainty without identifying a model defect, return no
challenge for it.

Return an object with:
- status: "challenges_found" when at least one genuine challenge is listed, or
  "no_challenges_found" when none is. An empty list without this status is invalid.
- challenges: the list described above.

VERIFIED MODEL:
{package}

FUNDAMENTALS ANALYST REPORT:
{report}
"""

ADDENDUM_PROMPT = r"""A deterministic model was repaired after the Fundamentals Analyst wrote its
report. The figures below changed. Rewrite ONLY the conclusions that the changes
affect, and do not introduce a number that is not in the list.

CHANGED FIELDS (old -> new):
{changes}

The report's conclusions that may be affected:
{conclusions}

Return:
- affected_conclusions: the conclusions that are now wrong or stale, quoted or
  closely paraphrased from the report
- replacement_conclusions: one replacement sentence per affected conclusion,
  using only the new values
- unchanged_conclusions: the conclusions the repair does not affect

Do not restate the report. Do not add analysis. If a conclusion is still true,
put it under unchanged_conclusions and change nothing.
"""

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
# Three or more decimals is a float the model printed, not a reported figure.
_MACHINE_PRECISION = re.compile(r"\d[\d,]*\.\d{3,}")


def shown(value) -> str:
    """A model value as the report prints it: one decimal, thousands separated."""
    try:
        return f"{float(value):,.1f}"
    except (TypeError, ValueError):
        return str(value)


def _coerce_numbers(values: dict[str, float]) -> dict[str, float]:
    clean: dict[str, float] = {}
    for path, value in (values or {}).items():
        try:
            clean[str(path).strip()] = float(value)
        except (TypeError, ValueError):
            continue
    return clean


def read_value(spec: ModelSpec, path: str) -> float | None:
    """The v1 value at a supported spec path, or None when the path is unknown."""
    parts = str(path).strip().split(".")
    head = parts[0]
    try:
        if head == "share_bases" and len(parts) == 3:
            if parts[2] not in SHARE_FIELDS:
                return None
            base = next((b for b in spec.share_bases if b.name == parts[1]), None)
            return None if base is None else getattr(base, parts[2], None)
        if head in {"actuals", "forecast", "guidance"} and len(parts) == 3:
            block = getattr(spec, head)
            if parts[1] not in block:
                return None
            return getattr(block[parts[1]], parts[2], None)
        if head == "valuation" and len(parts) == 2 and parts[1] in VALUATION_FIELDS:
            return getattr(spec.valuation, parts[1], None)
        if head == "scenarios" and len(parts) == 3:
            if parts[2] not in SCENARIO_FIELDS:
                return None
            scenario = next((s for s in spec.scenarios if s.name == parts[1]), None)
            return None if scenario is None else getattr(scenario, parts[2], None)
        if head == "segments" and len(parts) == 4:
            if parts[3] not in SEGMENT_FIELDS:
                return None
            segments = spec.segments.get(parts[1]) or []
            segment = next((s for s in segments if s.name == parts[2]), None)
            return None if segment is None else getattr(segment, parts[3], None)
    except (AttributeError, TypeError):
        return None
    return None


def _price_resolution_from_challenge(
    challenge: ModelChallenge, value: float, analysis_date: str = ""
) -> price_authority.PriceResolution:
    """The authority a proposed price may claim, from the challenge's own evidence.

    An analyst cannot grant itself the user's override or a verified-close class:
    a price in the report is a report price unless it is the analysis-date close
    and the challenge names that date and source. Anything vaguer is REPORT_PRICE;
    a market-cap derivation stays DERIVED and can never win.
    """
    source = str(challenge.source_class or "").strip().upper()
    if source not in price_authority.SOURCES:
        source = price_authority.REPORT_PRICE
    if source == price_authority.USER_EXPLICIT:
        source = price_authority.REPORT_PRICE
    if source == price_authority.VERIFIED_CLOSE and (
        not analysis_date
        or str(challenge.as_of or "") != str(analysis_date)
        or not str(challenge.source_ref or "").strip()
    ):
        source = price_authority.REPORT_PRICE
    return price_authority.PriceResolution(
        value=float(value),
        source=source,
        as_of=str(challenge.as_of or ""),
        ref=str(challenge.source_ref or ""),
    )


def verify_challenges(
    spec: ModelSpec,
    challenges: list[ModelChallenge],
    *,
    price_resolution: price_authority.PriceResolution | None = None,
) -> tuple[list[ModelChallenge], list[ModelChallenge], list[ModelChallenge]]:
    """Split extracted challenges into ``(verified, unverified, structural)``.

    Verified means the model contradiction is real: every proposed path exists in
    v1, and a stated current value matches the field (within a rounding
    tolerance). Whether the cited document supports the correction is not
    mechanisable here, so a challenge with a number but no path, or a path whose
    stated value does not match, is unverified and cannot change the model.

    A proposed price is checked against the run's resolution as well: a lower
    source class (an older report, a market-cap derivation) is rejected, and two
    values at the same class are a conflict that blocks publication rather than a
    silent pick.
    """
    verified: list[ModelChallenge] = []
    unverified: list[ModelChallenge] = []
    structural: list[ModelChallenge] = []
    current = price_resolution
    for challenge in challenges:
        if challenge.severity == "scenario_only":
            continue
        values = _coerce_numbers(challenge.proposed_values)
        if not values:
            if challenge.requires_rebuild:
                structural.append(challenge)
            else:
                unverified.append(challenge)
            continue
        if "valuation.price" in values and current and current.value:
            proposed = _price_resolution_from_challenge(
                challenge, values["valuation.price"], current.as_of
            )
            reason = price_authority.authority_rejection(current, proposed)
            if reason:
                challenge.rejection_reason = reason
                unverified.append(challenge)
                continue
        mismatch = False
        for path in values:
            actual = read_value(spec, path)
            if actual is None:
                mismatch = True
                break
            stated = challenge.stated_model_value
            if stated is not None and not isinstance(stated, bool):
                try:
                    stated_value = float(str(stated).replace(",", ""))
                except ValueError:
                    stated_value = None
                if stated_value is not None and abs(stated_value - float(actual)) > max(
                    0.01, abs(float(actual)) * 0.005
                ):
                    mismatch = True
                    break
        (unverified if mismatch else verified).append(challenge)
    return verified, unverified, structural


def apply_values(spec: ModelSpec, values: dict[str, float]) -> list[str]:
    """Apply scalar overrides to ``spec`` in place; return the paths applied."""
    applied: list[str] = []
    for path, value in _coerce_numbers(values).items():
        parts = path.split(".")
        head = parts[0]
        if head == "share_bases" and len(parts) == 3:
            name, field = parts[1], parts[2]
            if field not in SHARE_FIELDS:
                raise RepairError(f"unsupported share-base field {field!r}")
            base = next((b for b in spec.share_bases if b.name == name), None)
            if base is None:
                raise RepairError(f"unknown share base {name!r}")
            setattr(base, field, value)
        elif head in {"actuals", "forecast", "guidance"} and len(parts) == 3:
            period, metric = parts[1], parts[2]
            block = getattr(spec, head)
            if period not in block:
                raise RepairError(f"unknown {head} period {period!r}")
            if metric not in {"revenue", "operating_profit", "ordinary_profit", "net_profit", "eps"}:
                raise RepairError(f"unsupported metric {metric!r}")
            setattr(block[period], metric, value)
        elif head == "valuation" and len(parts) == 2 and parts[1] in VALUATION_FIELDS:
            setattr(spec.valuation, parts[1], value)
        elif head == "scenarios" and len(parts) == 3:
            name, field = parts[1], parts[2]
            if field not in SCENARIO_FIELDS:
                raise RepairError(f"unsupported scenario field {field!r}")
            scenario = next((s for s in spec.scenarios if s.name == name), None)
            if scenario is None:
                raise RepairError(f"unknown scenario {name!r}")
            setattr(scenario, field, value)
        elif head == "segments" and len(parts) == 4:
            period, name, field = parts[1], parts[2], parts[3]
            if field not in SEGMENT_FIELDS:
                raise RepairError(f"unsupported segment field {field!r}")
            segments = spec.segments.get(period)
            if segments is None:
                raise RepairError(f"unknown segment period {period!r}")
            segment = next((s for s in segments if s.name == name), None)
            if segment is None:
                raise RepairError(f"unknown segment {name!r} in {period}")
            setattr(segment, field, value)
        else:
            raise RepairError(f"unsupported spec path {path!r}")
        applied.append(path)
    if not applied:
        raise RepairError("no applicable values were proposed")
    return applied


def repair_model(
    spec: ModelSpec,
    challenges: list[ModelChallenge],
    *,
    price_resolution: price_authority.PriceResolution | None = None,
) -> tuple[ModelSpec, kernel.ModelResult, list[dict]]:
    """A validated v2 spec, its result, and the fields that changed, or RepairError."""
    values: dict[str, float] = {}
    for challenge in challenges:
        if challenge.severity not in MATERIAL_SEVERITIES:
            continue
        proposed = _coerce_numbers(challenge.proposed_values)
        if (
            price_resolution is not None
            and price_resolution.value
            and "valuation.price" in proposed
        ):
            reason = price_authority.authority_rejection(
                price_resolution,
                _price_resolution_from_challenge(
                    challenge, proposed["valuation.price"], price_resolution.as_of
                ),
            )
            if reason:
                raise RepairError(reason)
        values.update(proposed)
    if not values:
        raise RepairError("no scalar correction was proposed; the challenge needs a rebuild")

    candidate = copy.deepcopy(spec)
    applied = apply_values(candidate, values)
    # A repaired number that breaks an identity is not a repair.
    try:
        result = kernel.run(candidate, strict=True)
    except ModelError as exc:
        raise RepairError(f"the repaired model does not tie: {exc}") from exc
    return candidate, result, changed_fields(spec, result, applied=applied)


def changed_fields(
    before: ModelSpec,
    after_result: kernel.ModelResult,
    applied: list[str] | None = None,
) -> list[dict]:
    """Every scalar that moved, as ``{field, old, new}`` (applied paths + rows)."""
    changes: list[dict] = []
    # The path the repair actually wrote is the most specific record: a price
    # change used to appear only as the market capitalisation it implied, which
    # hid which input moved and made the old price invisible to the audit.
    for path in applied or []:
        old = read_value(before, path)
        new = read_value(after_result.spec, path)
        if _different(old, new):
            changes.append({"field": path, "old": old, "new": new})
    before_rows = _rows(before)
    for row in after_result.rows:
        old = before_rows.get(row.label)
        if old is None:
            continue
        for metric in ("revenue", "operating_profit", "net_profit"):
            previous, current = getattr(old, metric), getattr(row, metric)
            if _different(previous, current):
                changes.append({"field": f"{row.label} {metric}", "old": previous, "new": current})
        for basis, current in row.eps.items():
            previous = old.eps.get(basis)
            if _different(previous, current):
                changes.append(
                    {"field": f"{row.label} EPS ({basis})", "old": previous, "new": current}
                )
    before_cap = _market_cap(before)
    if _different(before_cap, after_result.market_cap):
        changes.append(
            {"field": "market capitalisation", "old": before_cap, "new": after_result.market_cap}
        )
    for period, segments in after_result.spec.segments.items():
        previous = {segment.name: segment for segment in before.segments.get(period, [])}
        for segment in segments:
            old = previous.get(segment.name)
            if old is None:
                continue
            for metric in ("revenue", "operating_profit"):
                if _different(getattr(old, metric), getattr(segment, metric)):
                    changes.append(
                        {
                            "field": f"{period} {segment.name} {metric}",
                            "old": getattr(old, metric),
                            "new": getattr(segment, metric),
                        }
                    )
    return changes


def superseded_values(changes: list[dict]) -> list[float]:
    """The v1 values a reader must no longer treat as current."""
    values: list[float] = []
    for change in changes:
        old = change.get("old")
        new = change.get("new")
        if (
            isinstance(old, (int, float))
            and not isinstance(old, bool)
            and old
            and (not isinstance(new, (int, float)) or abs(float(new) - float(old)) > 1e-9)
        ):
            values.append(float(old))
    return values


def render_addendum(changes: list[dict], unresolved: list[str] | None = None) -> str:
    """The deterministic repair note; used when the model-written one is unusable."""
    if not changes and not unresolved:
        return ""
    if changes:
        lines = ["**Model repair (v2)** — figures below changed before the debate:"]
    else:
        # Nothing was applied: say so. "Model repair (v2)" over an empty change
        # list claimed a repair that never happened (15:19 run).
        lines = [
            "**Model challenges — not applied (the model is unchanged):**",
            "",
            "The Fundamentals Analyst raised these as defects, but they need a rebuild "
            "rather than a scalar correction. They remain open questions for the debate:",
        ]
    for change in changes:
        lines.append(f"- {change['field']}: {_fmt(change.get('old'))} → {_fmt(change.get('new'))}")
    if unresolved:
        if changes:
            lines.append("")
            lines.append("**Unresolved challenges (not applied):**")
        lines.extend(f"- {item}" for item in unresolved)
    if changes:
        lines.append("")
        lines.append(
            "Figures in the Fundamentals report that match the old values above are superseded: "
            "they are historically valid but no longer current. Use the repaired model."
        )
    return "\n".join(lines)


def render_conclusions(addendum: ModelRepairAddendum, changes: list[dict]) -> str:
    """The addendum as published text, shaped like the deterministic fallback."""
    if not (addendum.affected_conclusions or addendum.replacement_conclusions):
        return render_addendum(changes)
    lines = ["**Model repair (v2)** — the following conclusions changed:"]
    for affected, replacement in zip(
        addendum.affected_conclusions, addendum.replacement_conclusions, strict=False
    ):
        lines.append(f"- Superseded: {affected}")
        lines.append(f"  Repaired: {replacement}")
    if len(addendum.affected_conclusions) != len(addendum.replacement_conclusions):
        for extra in addendum.affected_conclusions[len(addendum.replacement_conclusions):]:
            lines.append(f"- Superseded: {extra}")
    if addendum.unchanged_conclusions:
        lines.append("")
        lines.append("Unaffected conclusions: " + "; ".join(addendum.unchanged_conclusions))
    if changes:
        lines.append("")
        lines.append(
            "Changed figures: "
            + "; ".join(f"{c['field']} {_fmt(c.get('old'))}→{_fmt(c.get('new'))}" for c in changes)
        )
    return "\n".join(lines)


_SUPERSEDED_LINE = re.compile(r"(?im)^\s*[-*]?\s*superseded:\s*(.+)$")
_REPAIRED_LINE = re.compile(r"(?im)^\s*[-*]?\s*repaired:\s*(.+)$")


def conclusion_pairs(addendum: str) -> list[tuple[str, str]]:
    """(superseded, replacement) sentence pairs from a repair addendum."""
    pairs: list[tuple[str, str]] = []
    pending: str | None = None
    for line in (addendum or "").splitlines():
        text = line.strip()
        superseded = _SUPERSEDED_LINE.match(text)
        if superseded:
            pending = superseded.group(1).strip()
            continue
        repaired = _REPAIRED_LINE.match(text)
        if repaired and pending:
            pairs.append((pending, repaired.group(1).strip()))
            pending = None
    return pairs


def numbers_in(text: str) -> set[float]:
    values: set[float] = set()
    for token in _NUMBER.findall(text or ""):
        try:
            values.add(float(token.replace(",", "")))
        except ValueError:
            continue
    return values


def addendum_is_grounded(addendum: ModelRepairAddendum, allowed: set[float]) -> bool:
    """Every number in the replacement must come from the v1/v2 models.

    Machine precision is rejected too: a published sentence must not print
    ``213.77256001401787`` where the model's own table prints ``213.8``.
    """
    return all(
        not (numbers_in(sentence) - allowed) and not _MACHINE_PRECISION.search(sentence)
        for sentence in addendum.replacement_conclusions
    )


def _rows(spec: ModelSpec) -> dict:
    """The spec's declared rows, for a before/after comparison."""
    try:
        result = kernel.run(spec, strict=False)
    except ModelError:
        return {}
    return {row.label: row for row in result.rows}


def _market_cap(spec: ModelSpec) -> float | None:
    try:
        from .shares import market_cap_basis

        shares = spec.valuation.market_cap_shares_m
        if shares is None:
            shares = spec.share_base(market_cap_basis(spec)).shares_m
        return kernel.market_cap(spec.valuation.price, shares, spec.unit)
    except ModelError:
        return None


def _different(left, right) -> bool:
    if left is None and right is None:
        return False
    if left is None or right is None:
        return True
    return abs(float(left) - float(right)) > 1e-9


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):,.1f}"
    except (TypeError, ValueError):
        return str(value)


def to_json(
    challenges: list[ModelChallenge],
    changes: list[dict] | None = None,
    issues: list[dict] | None = None,
) -> str:
    return json.dumps(
        {
            "challenges": [c.model_dump() for c in challenges],
            "changes": changes or [],
            "deterministic_issues": issues or [],
        },
        ensure_ascii=False,
    )
