# Phase 9 — controlling decision, honest model failure, no deleted summary

Fixes for the 12:42 report. The debate layer was already right; these are the
decision-layer and failure-path defects.

## 1. The sizing scrub keeps the sentence, not the whole line

`scrub_sizing` now splits a line into sentences, removes only the sentences that state a
size, and keeps the label and the rest. The 12:42 report lost its entire Executive Summary
because one sentence in it carried a size; that cannot happen now. A bullet that is only a
size is still replaced whole.

## 2. No model, no audit

When the run used no model (`research_model_source == "unavailable"`), `prepare_final_state`
writes "The number audit did not run: no research model was used in this run" and never
reopens an older spec. The 12:42 audit said "No figure was marked" because it audited
against a spec the run did not use.

## 3. Stale model, not fallback

A failed rebuild does **not** fall back to the old spec as the active model. The old spec is
kept as a clearly separated reference:

- `state["stale_model_path"]` and `stale_model_package`;
- rendered in the appendix (`1b_research/stale_model.md`) under "STALE MODEL — NOT USED";
- the header names the path;
- it cannot drive the rating, valuation, transaction or audit.

## 4. Integrity constrains the action

- The Trader clears `entry_price`, `stop_loss` and `position_sizing` when there is no
  portfolio or no verified valuation.
- Under an unavailable model, a Trader **Buy is downgraded to Hold** with an action note.
- The Portfolio Manager forces `confidence = Low` under an unavailable model.
- Exact levels are scrubbed from the PM decision when the portfolio is missing or the model
  is unverified.

## 5. The Portfolio Manager is controlling

- `_detect_conflicts` names: RM recommendation vs final rating, Trader action vs the
  decision's new-investor action, and an exact stop without a portfolio.
- The decision ends with `**Superseded intermediate decisions**` naming them; the header
  lists them too.
- The Research Manager and Trader are now **appendix-only**. The main report is: analysts,
  verified model, adversarial debate, Portfolio Manager decision.

## 6. Builder rejects a self-bridge

`kernel.run` rejects `base_period == target_period` with a clear message ("a bridge moves to
a different period"), and the builder prompt states that `base_value` is the prior period's
reported figure. The 12:42 build failed on `FY3/27 → FY3/27` bridges with base 570 and lines
summing to 570, which the kernel computed as 1140.

## 7. Prompt corrections

- **RM:** evaluate a claim over its own period; do not rebut a full-year/forward cash-flow
  claim with a partial-period result unless the remaining period is reconciled. An
  unavailable model lowers conviction and never relaxes valuation discipline.
- **Trader:** a stop closer than about one ATR is not protection; if confirmation is
  required, the action is Wait, not a first tranche.
- **News:** when both feeds fail, one short paragraph plus a small table; diagnostics at the
  end.
- **Fundamentals:** say "no independent forecast differentiation" once, neutrally.

## Verification on the 12:42 state (in memory, no rewrite)

```
AUDIT:      The number audit did not run: no research model was used in this run…
CONFLICTS:  Research Manager recommended Buy; the decision is Hold
            Trader proposed Buy; the decision tells a new investor to Wait
            an exact stop was proposed with no portfolio context
SUPERSEDED: the decision carries the block
```

## Tests

New: scrub keeps a labelled summary; audit skips when no model; conflicts named and the
superseded block appended; stale model rendered with the NOT USED banner; Trader clears
levels without a portfolio; unverified valuation downgrades Buy; PM forces Low confidence.
Suite **1751 passed**, 9 skipped, 5 pre-existing Windows failures; `ruff` clean.
