# Phase 6 — the six remaining decision-architecture fixes

Implemented after the 11:16 report review. Verified against the 11:16 state where the
change is computable without a new run; the rest applies to the next run.

## 1. Exact sizing is rejected, not annotated

`agents/sizing.py` detects a size in context — "trim to 50-60% of a standard allocation",
"500 shares", "two tranches", "one-third of a full position" — and deliberately does not
fire on valuation percentages ("35.9x", "0.25% yield", "53.5% capital adequacy").

Without portfolio context, the Research Manager, Trader and Portfolio Manager each get
**one rewrite pass**; the answer is accepted only if no size remains and it is not a stub.
A failed rewrite leaves the original and the decision stays `DEGRADED` (the integrity
reason is computed from the final text, so a successful rewrite removes it). The structured
Trader path still clears `position_sizing` outright.

## 2. A relative rating needs a benchmark

`PortfolioDecision` keeps the 5-tier value for memory and settlement, but the published
rating now has a basis:

- `benchmark_ticker` supplied → the 5-tier framework is offered, unchanged.
- no benchmark → the prompt asks for **Buy / Hold / Sell** plus an absolute view
  (Positive / Neutral / Negative); a model that still answers Overweight/Underweight is
  remapped to **Hold** with an `**Absolute view**: Positive|Negative` line.

The published rating is therefore a position the run can support. The 5-tier value is
still what the memory log stores.

## 3. The audit says what it compared

`Finding` now carries `subject`, `object` and `expected`. The rendered tables show the
sentence, the two figures, and what the figures actually say:

```
| Section | The claim | Figures | What the figures say |
| Portfolio Manager | … | 118 vs 150.7 | 118 is below 150.7 |
```

The percentage table shows level vs price and the recomputed percentage.

## 4. The audit checks bridge deltas and stops guessing clauses

- A leading `+` is a figure: `[[+16]]` is now checked as 16. It used to be skipped and
  reported as "a label or identifier".
- Tier 2 picks the metric **nearest** the predicate, rejects a metric more than 80
  characters away, and rejects a subject separated from the claim by a comma, semicolon,
  colon, bracket or conjunction. The 11:16 false positive (net profit 118 paired with
  FY3/29 via a "capex above …" clause) is gone.

## 5. The report header shows rounds actually run

`research_rounds_used` and `risk_rounds_used` are computed before recording. The header
reads `research debate rounds: 2 of 3`, not the configured maximum. The state log stores
both.

## 6. The main report is investor-facing; the debate is an appendix

`complete_report.md` (and the HTML) now contains the analysts, the model package, the
Research Manager, the Trader and the Portfolio decision. The adversarial review, the three
risk speeches, the number audit and the case table are written to `appendix.md` and their
own section files. The main report ends with a pointer to the appendix.

## Verification on the 11:16 state (no new run)

| | before | after |
|---|---|---|
| marked | 144 | 144 |
| checked | 130 | **142** |
| skipped (`+16`, `+60`, `+100`) | 12 | **0** |
| failed | 0 | 0 |
| relation findings | 1 (false positive) | **0** |

## Tests

New: size in context vs valuation percentages; gate rewrite accepted/rejected/failed;
Underweight without a benchmark becomes Hold with an absolute view; with a benchmark the
relative rating survives; audit shows operands and truth; plus-signed mark checked; the
cross-clause period label is rejected; main report excludes debate/audit; header shows
rounds run. Suite **1733 passed**, 9 skipped, 5 pre-existing Windows failures; `ruff` clean.
