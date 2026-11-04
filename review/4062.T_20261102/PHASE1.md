# Phase 1 — truthfulness and record ordering

Code change for arm A and arm B. Recorded against baseline commit `6630404`.

## What changed

| Finding | Change | Where |
|---|---|---|
| B1 audit skipped/verified | The mark is the unit, not the line: `_is_percentage` and its line-scoped skip are gone; a percentage mark is compared like any other figure. `stats` now separates marked / checked / reference / skipped; `render()` prints the counters and never calls a skipped mark checked. | `research/audit.py` |
| B1 false relation | A participle opens a new clause, so "Capex of 350bn ... rising above 400bn thereafter" no longer pairs 350 with 400. | `research/audit.py` |
| B2 tautological checks | The EPS re-derivation is gone. A **stated** EPS (`Financials.eps`) is compared with `EPS_TOL`; with no disclosure there is no check, and that is visible as a missing check rather than a pass. `Scenario.target_price` is now a stored, optional field; a declared target is checked against `eps × multiple` (`resolved_target` derives when absent, and the spec is not mutated). | `research/kernel.py`, `research/schemas.py` |
| B3 instructions in the report | `compact_summary(..., instructions=False)` renders the artifact; the prompt rendering is unchanged. `state.research_package_artifact` feeds `1b_research`, with a fallback to the old key for checkpoints saved before the split. | `research/report.py`, `graph/trading_graph.py`, `reporting.py` |
| B4 truncated case notes | Notes are published whole (pipes escaped). | `research/cases.py` |
| B5 record before audit | `prepare_final_state()` (case table → audit → repair) runs before `record_decision` on both `propagate()` and the CLI. `save_reports` calls it too and is idempotent. The state log now stores the package, audit, case table, horizon and provenance. | `graph/trading_graph.py`, `cli/run.py` |
| B6 marks on screen | `display_complete_report` strips `[[...]]` like the saved report. | `cli/display.py` |
| B7 invisible failures | `--log-level` (default WARNING) writes `<results_dir>/<ticker>/<date>/run.log`; the path is printed at the end. | `cli/main.py`, `cli/run.py` |
| B8 metadata | `run_settings()` adds horizon, holding period, run purpose, price override, benchmark; the header adds the spec path, source, creation time and compatibility. | `graph/trading_graph.py`, `reporting.py` |
| P5 provenance (lite) | `create_run_state` records `research_model_source` (`built`/`reused`), the spec's file timestamp, and `spec_compatibility` (`unknown` for any reused spec until a dependency manifest exists). | `graph/trading_graph.py`, `agents/state.py` |
| P5 provenance (full) | A built spec gets `<TICKER>.meta.json` (spec hash, corpus-manifest hash, ticker, unit, currency, builder/kernel versions). A reused spec is checked on load: `compatible` / `unknown` / `incompatible`; incompatible + production rebuilds (or refuses without a build), comparison/replay loads it labelled. Price stays out of the key. | `research/provenance.py`, `graph/trading_graph.py` |
| Fallback visibility | `invoke_structured` records why it fell back; the Trader, Research Manager and Portfolio Manager return it under `structured_fallbacks`, merged per agent; the header names every agent that answered in free text. | `agents/structured.py`, `agents/{trader,managers}`, `agents/state.py` |
| Quarantine | `--run-purpose comparison|replay` labels the report "Authority: diagnostic" and skips the memory-log write; the state log is still written. | `cli/main.py`, `cli/run.py`, `default_config.py`, `graph/trading_graph.py` |

## Acceptance evidence on the 15:19 baseline

Before (old audit): "All 143 figure(s) ... were found", no skipped bucket, 3 references.

After (new audit, same state, same spec, price 8,045):

```
All 141 checked figure(s) were found in the verified model.

- Marked: 146
- Independently checked: 141
- Reference-only: 3 (plain reference numbers, a bare single digit such as 6, are counted, not checked)
- Skipped: 2 (a label or identifier, not a figure)
- Failed: 0

Figures checked by section: Fundamentals Analyst 128, Research Manager 7, Trader 6, Portfolio Manager 0.
```

`Marked = checked + reference + skipped` (146 = 141 + 3 + 2). The counts now reconcile.

Two false positives the old code could not distinguish were fixed on the way: the
`[[51.2]]` P/E is correct once the run price (8,045) is applied instead of the spec's
stale 8,015, and the capex "rising above 400bn" sentence no longer triggers a repair.

## The baseline proves B5 (record before audit)

Diffing the published `1_analysts/fundamentals.md` against the state log shows exactly one
sentence changed:

```
published:  Capex of ¥350bn in FY3/28 is below the >¥400bn level thereafter, ...
state log:  Capex of ¥350bn in FY3/28 rising above ¥400bn thereafter, ...
```

The old audit flagged that sentence as a contradicted comparison (a false positive: 350 is
the FY3/28 level, 400 the later level), the save-time repair rewrote it, and because
`record_decision` ran before `save_reports`, the state log kept the original. The report a
reader gets and the decision the system recorded differ. Phase 1 fixes both ends: the
participle no longer pairs the figures, and the repair now happens before recording.

`metrics.py` reports this as `sections_vs_state=mismatch:fundamentals` for the baseline;
arm A must report `ok`.

## Tests

- New: wrong margin beside another percentage; label mark counted as skipped; disclosed
  EPS mismatch; declared scenario target mismatch; derived target is not persisted;
  participle clause boundary; instructions absent from the artifact; artifact preferred
  over the prompt package; diagnostic run writes no memory entry; header discloses
  authority and reuse; long case note not truncated; dependency manifest (8 cases);
  structured fallbacks merged across agents; a blank memory placeholder does not block
  the real decision.
- `python -m pytest -q`: 1688 passed, 9 skipped, 5 failed. The 5 are the pre-existing
  platform failures (POSIX file mode on Windows ×2, `upgrade_command` returning None on
  Windows ×2, temp dir under `$HOME` ×1).
- `ruff check .`: clean.

## Found by the comparison, fixed after the arms ran

I first read the 2026-11-02 memory entry as a blank placeholder and concluded that the
guard had dropped the real decision. The full block shows a complete **Hold** decision
(price target 7,970, horizon 6-12 months, written 11:19): the guard was correct, one
decision per ticker and date. The empty-entry guard added here is therefore a defensive
rule, not a repair of this log: an interrupted write can leave an entry with no decision
text, it must not block a real one, and `get_pending_entries` no longer offers such an
entry to settlement.

The production run at 17:20 (old cached spec, rejected by the Phase 3 rule) also showed
that a rejected spec left the report header silent. The header now prints
`Research model: UNAVAILABLE — <reason>` and says explicitly that this is the
deterministic fundamental model, not the LLM.


## Not in this phase

- Dependency-manifest cache keys and the `compatible` / `incompatible` states (Phase 2b).
- Per-agent structured-output fallback flags in the header (the warning is now in
  `run.log`).
- Single-period valuation invariant and Trader `intent` validators (Phase 3).
- Reviewer delta rounds and the Deep-mode warning (Phase 4).
