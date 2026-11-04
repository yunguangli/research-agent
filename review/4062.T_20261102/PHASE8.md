# Phase 8 — a structured adversarial review, round 1 kept for the reader

Implements the agreed Phase 1 of the debate proposal. No topology change, no schema for
the prose, no assumption-challenge engine yet.

## The reviewer's contract

The Adversarial Reviewer now writes one review with fixed headings:

```
## Strongest Bull Case
## Strongest Bear Case
## What the Bull Fails to Explain
## What the Bear Fails to Explain
## Decisive Disagreement
## Evidence That Would Resolve It
## Strongest Opposing Argument
## Rebuttal or Concession
## Confidence
```

The prompt requires a causal chain per side (driver → financial effect → expectations gap →
valuation/catalyst → share-price effect), evidence-class tags, same-class rebuttal, the
guidance/consensus anchoring rule, and no invented replacement figures. The numeric case
JSON is unchanged.

## Rebuttal retry is deterministic

`parse_review_sections` reads the headings; `needs_rebuttal_retry` fires only when
`## Rebuttal or Concession` is missing, blank, or one of `none / n/a / na / not applicable`.
The retry asks for the two rebuttal sections only and is merged back; the full review is
never re-run. A failed retry keeps the review as written.

## Round 1 is what the reader sees

- `first_review` stores the complete round-1 review (bull, bear, weaknesses, decisive
  issue, rebuttal).
- `unresolved_issue` stores the latest "what would settle it" (round 1's resolving-evidence
  section, later the delta's `UNRESOLVED ISSUE:` line).
- `prepare_final_state` builds `debate_summary` = round 1 + a material delta (if any) +
  the unresolved issue. `NO MATERIAL UPDATE` deltas are not published.
- The main report gains an **Adversarial Debate** section before the Research Manager; the
  full transcript stays in `appendix.md` and `2_research/challenge.md`.

## What is deliberately unchanged

- Single reviewer; no second persona.
- Research Manager schema and the PM decomposition are untouched.
- Delta-only later rounds and early stop.
- Risk-team topology (Phase 4).
- No assumption-challenge patching; the reviewer identifies disputed assumptions in prose
  and does not create replacement forecasts.

## Tests

New: heading parser; placeholder detection; one focused retry merges the rebuttal; round 1
and the unresolved issue are stored; the compact summary starts with round 1, includes a
material delta, omits a `NO MATERIAL UPDATE`, and reports rounds used. Suite **1744 passed**,
9 skipped, 5 pre-existing Windows failures; `ruff` clean.
