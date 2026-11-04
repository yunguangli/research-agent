# Phase 11 — the Round 1 review survives a repair, and the numbers are labelled

The 13:09 report exposed four defects beyond rendering: a repair pass replaced the review
instead of completing it, the review argued from remembered facts, a growth rate was called
an incremental margin, and the fallback decision dropped the valuation it had used. This
phase closes them, plus the stage-status gap and the missed FY3/28 guidance figure.

## 1. The raw review is never overwritten

`debate_state` now carries:

- `first_review_raw` — exactly what the reviewer first wrote;
- `first_review` — the completed review (raw when nothing was missing).

The flow:

```text
missing = missing_headings(raw)          # all nine contract headings
if missing:
    repair = one pass asking ONLY for those headings
    repaired = merge_sections(raw, repair)
```

`merge_sections` rebuilds the review in contract order, takes each section from the raw
answer when it is substantive (and from the repair only when it is not), keeps any preamble,
and keeps headings outside the contract. An unstructured answer with no headings is kept
whole, with the repaired sections appended.

The main report renders the completed review; a focused retry alone is never the report.

## 2. Debate claims are grounded in the current run

- The reviewer prompt selects both cases and the opposing argument from the current-run
  fundamental, technical, news/sentiment and verified-model inputs. Customer concentration,
  market share, competitor capacity and roadmap figures that those inputs do not state are
  not allowed; an undisclosed exposure is written as unknown.
- Every sentence with a number must carry `[fundamental]`, `[valuation]`, `[technical]`,
  `[news_sentiment]` or `[portfolio_action]`.
- `audit.grounding_findings` flags a numerical sentence in the review with no tag and no
  model mark. It is a **warning**, not a rejection; it is rendered as a separate audit block
  and does not count as a failed figure.

`overlaps_current_run_evidence` is deliberately not implemented: the review cites evidence
classes, not document ids, so a semantic overlap check would be guessing again.

## 3. Growth is not incremental margin

New `research/metrics.py`:

```text
op_growth          = current_op / prior_op - 1
incremental_margin = (current_op - prior_op) / (current_revenue - prior_revenue)
```

`period_metrics(model)` computes both for every consecutive pair of model rows, and
`render_metrics` prints them together. The kernel does not carry segment series, so the
segment-level 97% cannot be recomputed from consolidated rows; the audit closes that gap
with a narrow warning:

- a percentage labelled "incremental … margin/contribution" that matches an
  operating-profit growth figure in the same sentence, or
- an "incremental margin" at or above 90%, which is not a margin.

The check only considers the percentage nearest the word "incremental", so a growth rate
printed earlier in the sentence is not mistaken for the labelled figure. The 13:09 text is
flagged in all four sections where it repeats ("a 97% incremental contribution margin").

Prompts (fundamentals, Research Manager) state both formulas and forbid swapping the labels.

## 4. The fallback decision keeps its valuation

`PortfolioDecisionMinimal` and the full schema carry `fair_value_low/base/high` and
`valuation_status` (`point_target` / `range` / `unavailable`). The renderer prints:

```text
**Fair Value**: 6,304–9,246 (base 7,984)
**Valuation status**: range
```

Rules:

- a point target is a complete answer; a range is a complete answer; neither is "not
  provided";
- when the minimal fallback returns neither but the run used a verified model, the range is
  read from the model's printed `Fair-value range` line in the research package;
- under an unverified model the fields are cleared and the status is `unavailable`.

## 5. Stage status is explicit

`prepare_final_state` writes:

```text
stage_status = {
  "research_manager": "completed_appendix_only",
  "trader": "completed_appendix_only",
  "portfolio_manager": "completed_main_report",
}
controlling_decision = "portfolio_manager"
```

The header prints both. A stage that produced no output says `failed`; a policy removal says
`completed_appendix_only`, so a reader no longer has to infer whether a stage ran.

## 6. FY3/28 guidance

The audit's `[[165.0]]` failure was a true positive: the analyst marked FY3/28 company
guidance, but the built spec's guidance block held only FY3/27. The builder prompt now
requires every guided year the documents disclose to be captured in `guidance`; the audit
already accepts any figure in that block.

## Verification against the 13:09 state (in memory)

```text
STAGE:        research_manager completed_appendix_only
              trader completed_appendix_only
              portfolio_manager completed_main_report
CONTROL:      portfolio_manager
AUDIT:        the 165 finding still stands (the stored model lacks FY3/28 guidance)
TERMINOLOGY:  4 sentences flagged, all repeating the 97% "incremental contribution"
GROUNDING:    no ungrounded numerical sentence in this review
```

## Tests

New: a repair adds only the missing sections and keeps the raw answer; a numerical review
sentence without a tag is a warning and does not count as a failed figure; growth and
incremental margin are computed separately; the 97%-called-a-margin shape is flagged and the
real incremental margin is not; the minimal fallback renders a fair-value range and backfills
it from the model package; stage status and the controlling decision are recorded.

Suite **1767 passed**, 9 skipped, 5 pre-existing Windows failures; `ruff` clean.
