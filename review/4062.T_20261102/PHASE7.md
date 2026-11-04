# Phase 7 — decision schema, hard sizing, verified-model reuse

Implemented after the 11:46 report review. The run was already on the Phase 6 code (Hold,
provisional, 2 of 3 rounds, appendix); the defects it exposed were enforcement gaps, not
old behaviour.

## 1. Sizing is blocked, not annotated

The 11:46 Research Manager wrote "1.0x standard neutral allocation", "1.25x", "1.5x",
"0.5x". The first gate only understood percentages and share counts, so it missed them.

- `agents/sizing.py` now recognises **allocation multiples** near a position word
  (`1.0x standard neutral allocation`, `scale to 1.5x`) without confusing a valuation
  multiple (`35.9x FY3/28 EPS`).
- Without portfolio context the RM, Trader and PM still get one rewrite pass; if a size
  survives, `scrub_sizing` removes the offending line and replaces it with a qualitative
  sentence. The structured Trader path clears `position_sizing` outright.

## 2. One rating no longer carries four decisions

`PortfolioDecision` gained optional typed fields:

- `fundamental_view` (Positive / Neutral / Negative)
- `valuation_view` (Attractive / Fair / Full)
- `technical_timing` (Favorable / Neutral / Unfavorable)
- `new_investor_action` (Initiate / Wait / Avoid)
- `existing_holder_action` (Add / Hold / Review / Reduce / Exit)
- `confidence` (High / Medium / Low)

They are rendered as a **Decision framework** table after the rating. The prompt asks for
them explicitly and tells the model not to compress them into the rating.

## 3. A failed decision schema retries typed, not from prose

The RM and PM now bind a **minimal schema** (`rating` + `executive_summary` / `rationale`)
in addition to the full one. If the full schema fails, the minimal one is tried before any
free text; the fallback is recorded as "minimal schema used" and named in the header. The
11:46 run rebuilt the RM decision from prose — that no longer happens first.

## 4. "Compatibility unknown" rebuilds in production

Previously `unknown` was warn-and-reuse. A production run now rebuilds the spec once when
its dependencies cannot be verified (no canonical digest, raw hash mismatch), and writes a
new manifest with a digest. Comparison and replay still reuse it, labelled. This is the
policy that would have rebuilt arm C's pre-digest spec before the 11:46 run.

## 5. Prompt corrections from the review

- **Consensus:** state "the forecast equals consensus" at most once, as a neutral
  observation — not a recurring bearish argument (RM and PM).
- **Residuals:** label one *unsupported* unless contradicted; it lowers confidence, it does
  not prove the forecast is too high (fundamentals).
- **Technical:** without an execution mandate, no specific support-retest buy, exact entry,
  stop or size — zones and state changes only (market analyst).
- **Marks:** a variance or percentage change is your arithmetic, not a model figure; the
  11:46 audit's single "failure" was `[[+0.0]]` in a variance column.

## Not implemented in this phase

- **Typed assumption challenges** (the bridge engine): the LLM still argues about the model
  in prose. Making it propose `{field, base, low, high, evidence_status}` and letting the
  kernel recompute is the next architectural phase.
- **Target as a range** rather than a point.
- **Risk-team restructure**; the three personas still speak `N` rounds.

## Tests

New: allocation multiples detected but valuation multiples not; scrub removes a surviving
size; decision framework rendered separately from the rating; PM and RM retry on the minimal
schema before free text. Suite **1739 passed**, 9 skipped, 5 pre-existing Windows failures;
`ruff` clean.
