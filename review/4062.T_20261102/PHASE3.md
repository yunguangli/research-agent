# Phase 3 — one valuation period, and a Sell that says what it means

## What changed

| Finding | Change | Where |
|---|---|---|
| B9 / P2 valuation basis | `ValuationSpec.fair_value_period` names the year whose EPS the band multiplies (default: first forecast). The kernel rejects a scenario that declares another period, and rejects one whose EPS is another model row's EPS. `valuation.value` uses the same period, so band, scenarios, P/E and the case engine share one EPS. | `research/schemas.py`, `research/kernel.py`, `research/valuation.py` |
| P2 builder | `fair_value_period` and a per-scenario `period` are part of the build shape and rules: the builder now has to choose the year it values and say so. | `research/builder.py` |
| Replay policy | `kernel.run(spec, strict=False)` downgrades only the mixed-period rule to a warning, so a historical replay reproduces the archived report instead of correcting it. Every other check stays fatal. | `research/kernel.py`, `graph/trading_graph.py` |
| Trader semantics | `TraderProposal.intent` (`reduce_long` / `initiate_short` / `hold`) separates reducing a long from opening a short; the prompt requires a short mandate for the latter. A level above the entry is published as **Reassessment Level**, not as a stop the position does not have. | `agents/schemas.py`, `agents/trader/trader.py` |

## Evidence: the rule catches the real defect

Both specifications that produced the reviewed reports are now rejected, naming the
problem:

```
baseline  REJECTED: scenario Base EPS 215.4 is FY3/28's EPS while the
                    fair-value range is on FY3/27 (157.161)
arm-b     REJECTED: scenario Base EPS 211.9 is FY3/28's EPS while the
                    fair-value range is on FY3/27 (157.161)
```

A spec rebuilt under the new rules is coherent: `fair_value_period: FY3/28`, EPS 224.1,
band 6,274–8,515, and all three scenarios declare `FY3/28` (Base 224.1 × 35.7 = 8,000,
inside the band). That is arm C.

## The trader defect, concretely

The baseline trader published entry 8,045 and "Stop Loss 9,126" — 13% *above* the entry.
For a long reduction that is not protection, it is the level that would end the bearish
stance. `render_trader_proposal` now labels it:

```
**Entry Price**: 8045.0
**Reassessment Level**: 9126.0 (above the entry: an invalidation level, not a protective stop)
```

and a Sell can no longer be rendered as a short initiation without `initiate_short`.

## Tests

New: scenario on another year's EPS fails; declared other period fails; declared matching
period passes; `fair_value_period` moves the band and the P/E; replay keeps the mix as a
warning; a level above the entry becomes a reassessment level; a normal long stop keeps its
label; the intent is published. Suite: **1696 passed**, 9 skipped, 5 pre-existing Windows
failures; `ruff` clean.

## Still open

- Phase 4: delta-only review rounds (the rebuilt spec's challenge duplicate ratio rose to
  0.38), and the Deep-mode warning.
- Prompt slimming: deliberately deferred until the fixed reports have been compared.

## Run C (arm C) result

Arm C ran at 17:04 on the spec frozen at 16:50 and is the first run whose valuation is on
one period: FY3/28 EPS 224.1, P/E 35.9x, band 6,274–8,515, scenarios Bear 4,945 / Base
8,000 / Bull 9,530, all declared FY3/28. Audit 158 marked = 158 checked, 0 failed, 0
relation findings; `sections_vs_state=ok`; provenance `reused/compatible`. Rating
Underweight, trader Sell, PM target 7,400 with a 3-6 month horizon (the PM used the
structured path this time; the Research Manager and Trader fell back on this run, and the
header says so).

