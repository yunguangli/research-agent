# Phase 4 — short reviews, local corrections, and an honest decision status

Changes after the 17:20 production review. None of them changes the model or the call; they
change how much a reader can trust the decision and how long the debate is.

## Debate: round 2 onwards is a delta

| | before | after |
|---|---|---|
| Round 1 | full adversarial review | unchanged |
| Round N+1 | the whole prior review, asked again | only the last response, with a fixed delta format |
| Output | another essay | `NEW EVIDENCE / CHANGED VIEW / WITHDRAWN CLAIMS / UNRESOLVED ISSUE`, or `NO MATERIAL UPDATE` |
| Stop | when the configured round count is reached | also when a round reports no material update |

`state["investment_debate_state"]` gained `last_review` and `no_material_update`;
`ConditionalLogic.should_continue_debate` routes to the Research Manager on the flag.
Deep mode prints that rounds after the first are delta-only.

## Corrections are computed, not trusted to the reviewer

The 17:20 market report said *"50-day SMA 10,168.07 … sits 20.9% above price"*; against
8,045 the SMA is 26.4% above, and the same report's own table said 26%. The reviewer
caught it, but the published section kept both statements.

`audit.percentage_findings` now recomputes a claim of the shape *"<indicator> <level>
… X% above|below price|spot|close"* against the run price, flags it when the sign or the
magnitude is wrong (10% relative tolerance), and routes it through the same one-shot repair
as contradicted comparisons. The repaired section is accepted only if the percentage now
matches. A percentage against a level ("3.4% above the 200-day SMA") is left alone: it is a
different claim.

## The decision says when it is advisory

`prepare_final_state` now computes `decision_integrity`:

- `validated`, or
- `DEGRADED: <reasons>` when the research model was unavailable, a structured agent fell
  back to free text, or exact sizing was proposed with no portfolio context.

The header carries the line, and the decision itself ends with a `**Decision integrity**`
block; with no portfolio context and exact sizes in the text it also carries a
`**Sizing note**` saying they are illustrative. Both are appended before recording, so the
state log and the memory entry agree with the report. Idempotent: a second save does not
add them twice.

Two deliberate non-changes:

- **"Sell" was not renamed to "Reduce".** The enum is shared by the trader, the memory log
  and the backtest; the intent line (`reduce long`) carries the meaning without moving
  three subsystems.
- **"Price Target" was not relabelled "central reference".** The parser and several tests
  read that label; the number's precision problem is better fixed in the PM prompt than in
  the renderer.

## Smaller items from the review

- Header: `holding period 5 trading days` → `memory-log settlement window 5 trading days`,
  so it cannot be read as the recommendation horizon.
- Fundamentals prompt: a falling customer-advance balance is not a warning by itself;
  ordinary delivery/revenue recognition is neutral, refunds/cancellations/reduced
  commitments are the warning. The report must say which cause the evidence supports.
- Research Manager docstring no longer claims a bull/bear debate.

## Tests

New: delta prompt shape; `NO MATERIAL UPDATE` stops the debate and skips cases; a material
delta keeps it going; percentage claim recomputed (wrong and correct); a percentage against
a level is ignored; a repair fixing the percentage is accepted; unavailable model in the
header; degraded decision + sizing note, idempotent; a clean decision stays validated;
header shows the integrity status. Suite **1709 passed**, 9 skipped, 5 pre-existing Windows
failures; `ruff` clean.

## The 10:54 production run invalidated its own model

Sequence: the run loaded arm C's spec at 10:40 (`compatible`), the analysts scanned the
corpus at 10:41 (the vendor rewrites `_corpus_manifest.json` and stamps `retrieved_at` with
the scan date), and the audit at 10:54 compared that file's **raw bytes** with the hash
recorded at build time. Different bytes, so the spec was declared `incompatible`; a
production audit may not rebuild, so it refused and the report said `UNAVAILABLE` — while
the analysts had in fact used the model (`1b_research` holds it).

That is a provenance bug, not a corpus change. `provenance.corpus_digest` now hashes the
document identities, content hashes, publication dates, licence, use and exclusions, sorted;
`retrieved_at`, paths and page counts are excluded. A spec written before the change has no
digest, so a raw-hash mismatch is reported as `unknown`, never `incompatible` — the honest
verdict for a spec that cannot be verified.

The 10:54 report and state log were regenerated from the stored state after the fix
(originals kept in `production-1103-as-run`): the header now reads `reused … unknown`, the
audit runs (**207 marked = 203 checked + 4 references, 1 failed**), and `1d_cases` exists
with Base 7,395 / Bull 8,771 / Bear 6,454, all on FY3/28. The one failed figure is a real
mark-rule violation: `~154bn` is the analyst's own margin-sensitivity calculation, not a
model line, and it was marked as one.

The run also showed why the header and the audit must not disagree: when the audit failed,
`prepare_final_state` used to overwrite a *used* model's provenance with `unavailable`. It
now only marks an outage when no model was loaded; a later audit failure leaves the
provenance intact and the audit note explains itself.


## Arm D result

The validated path ran at 17:48 on arm C's spec. Everything present: `1b_research`,
`1c_audit`, `1d_cases`; provenance `reused/compatible`; audit 204 marked = 202 checked +
2 references, **1 failed** (a real sign error: −20 depreciation written as +20), 1 relation
and 1 percentage finding. The challenge fell to **6,078 characters** and a second round
answered `NO MATERIAL UPDATE`, so the debate stopped early (duplicate ratio 0.00). The
decision is marked `DEGRADED` with reasons and carries the sizing note because the Research
Manager and Portfolio Manager fell back to free text and exact sizes were proposed with no
portfolio context.

