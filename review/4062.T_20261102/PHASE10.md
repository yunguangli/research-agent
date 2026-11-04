# Phase 10 — the reviewer contract can fail, and is now checked

Checks from the 13:09 report and the one regression it exposed.

## What the 13:09 run got right

- **The model rebuilt.** Self-bridge validation and the prompt rule worked; `1b_research`
  is the full model, provenance `built / not_applicable`, and the audit ran.
- **The Executive Summary survived.** The sentence-level sizing scrub kept the label and
  the non-size sentences.
- **The conflict layer worked.** The header names `Research Manager recommended Buy; the
  decision is Hold`, and the decision carries the superseded block.
- **The Trader published Hold with no entry, stop or size.**
- **The main report excludes the RM and Trader**; they are in the appendix.

## The regression: an incomplete review

Round 1 returned only the two retry sections, not the review. The 13:09 `debate.md` has
`## Strongest Opposing Argument` and `## Rebuttal or Concession` and nothing else; the RM
then adjudicated a debate with no bull case, no bear case, no decisive disagreement and no
case JSON. That is why `1d_cases` is absent.

Cause: the retry predicate only checked the rebuttal section. A response that dropped the
whole contract was "repaired" by asking for the rebuttal alone.

Fix:

- `missing_headings` checks the four core sections (bull, bear, decisive disagreement,
  resolving evidence).
- If any is missing on round 1, **one full retry** asks for the complete contract, not just
  the rebuttal. The best of the two answers is kept.
- `review_missing` records what is still absent; the header prints
  `Adversarial review: incomplete — missing or empty sections: …`.
- When no case table is produced, `1d_cases/cases.md` now says why ("the review did not
  return the two case objects", or "this run had no verified model") instead of the folder
  silently disappearing.

## What remains in the 13:09 artifact

- The audit's one failure is a true positive: `[[165.0]]` is FY3/28 company guidance, and
  the built model's guidance block only contains FY3/27. The analyst marked a company number
  as the model's. The builder should capture FY3/28 guidance when the corpus discloses it.
- The PM used the minimal schema (full schema failed), so the decision framework table is
  absent; the prose summary lists the fields and says Confidence Medium. The fallback is
  named in the header.
- The 2026-11-03 memory entry is still the 11:46 Underweight; this Hold was not recorded
  (one decision per ticker and date).

## Tests

New: a review missing the core cases triggers one full retry; an incomplete review records
`review_missing`; a missing case table is reported, not omitted. Suite **1754 passed**,
9 skipped, 5 pre-existing Windows failures; `ruff` clean.
