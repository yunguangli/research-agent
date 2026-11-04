# Phase 5 — follow-up from the 2026-11-03 report review

Changes after reading the 11:03 production report. The rating stayed Underweight in every
arm; these changes are about what the report is allowed to conclude, and from what.

## News is no longer a macro channel into a single name

- `get_prediction_markets` is **removed from the news analyst's tool set**. Election,
  recession and "AI bubble" odds have no demonstrated transmission to one company's
  earnings, and they were being elevated when the company-news feed failed.
- The prompt now says: if `get_news` returns `DATA_UNAVAILABLE`/`NO_DATA_AVAILABLE`, say so
  and make **no** incremental news adjustment; do not substitute macro, election odds,
  themes or unrelated cross-asset moves. A macro item is usable only with a stated
  company-specific transmission mechanism (a disclosed FX sensitivity, an input cost share).
- Market-implied probabilities may not be presented as scenario probabilities, position
  sizes or risk budgets.
- The tool and its vendor stay registered for other uses (crypto/macro); they are simply
  not offered to this analyst.

## Confidence is not probability

- The case table's column is now **Weight (agent confidence)**, and the prompt block says
  explicitly that it is the reviewer's certainty, not a calibrated probability, and must not
  be used to compute an expected value.
- The Research Manager and Portfolio Manager prompts carry the same rule.

## Rating, action and timing are separated

- The Trader prompt no longer maps a rating mechanically. `Underweight` means reduce an
  existing long or avoid a new one, **not** a short; `Sell` needs an existing holding to
  exit or an explicit short mandate; `Overweight` is an add, not a chase.
- The Research Manager and Portfolio Manager must decide the rating from valuation,
  expectations, catalysts and downside protection. A forecast that equals guidance or
  consensus is **no differentiation, not evidence for a negative rating**. Technical
  evidence affects timing, not fundamental value.
- The Portfolio Manager must state the existing-holder action and the new-investor action
  separately.

## Sizing and the degraded path

- In the structured Trader path, `position_sizing` is **cleared** when no portfolio context
  was supplied: the field says "not provided" instead of inventing a percentage. The
  free-text path cannot be rewritten safely, so it keeps the `Sizing note` and the
  `Decision integrity` block.
- A degraded decision is now labelled **provisional** in the report header
  (`- Rating: Underweight (provisional)`); the 5-tier value itself is unchanged so memory,
  settlement and backtest keep parsing it.
- The decision-integrity reasons are no longer overwritten when the model *was* used and a
  later audit failed: provenance is only marked unavailable when no model was loaded.

## Debate length

- Research depth now maps to **1 / 2 / 3** rounds (was 1 / 3 / 5). "Deep" still means more
  scrutiny, without nine essays; rounds after the first are delta-only and stop on
  `NO MATERIAL UPDATE`.

## Forecast discipline in the fundamentals prompt

- A historical **group** incremental margin is not a forward margin. It must be normalised
  for segment mix, utilisation, pricing, depreciation, start-up and yield before it can size
  a future year.
- A downside built from it is a labelled **stress case** with its inputs stated — never the
  base forecast and never by itself the reason for the rating.
- A residual widens the forecast range; it does not select the bear case.

## What this does not do

- It does not split the Portfolio Manager schema into
  `fundamental_rating / relative_rating / holder_action / new_investor_action`. Those
  fields are prompt-level for now: adding four typed fields to an agent that already falls
  back to free text would trade one failure mode for another. Revisit once the structured
  path is reliable.
- It does not suppress the protective stop when portfolio context is absent. A stop is an
  analytical price level, not a position size; only the sizing was removed.
- It does not restructure the risk team into one synthesis. Three personas × N rounds is
  still the shape; only the research debate is delta-only.

## Tests

New: prediction markets absent from the news analyst's tools and prompt; macro substitution
rule present; structured trader clears an invented size without a portfolio; the trader
prompt no longer maps Underweight to Sell; the case table calls the column confidence; a
degraded decision is labelled provisional. Suite **1720 passed**, 9 skipped, 5 pre-existing
Windows failures; `ruff` clean.
