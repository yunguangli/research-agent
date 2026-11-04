# 4062.T — five-arm comparison

| Metric | baseline (old code) | arm A (fixed, reused spec) | arm B (fixed, rebuilt spec) | arm C (Phase 3, one-period spec) | arm D (Phases 1–4, validated path) |
|---|---|---|---|---|---|
| marked | — | 183 | 197 | 158 | 204 |
| checked | 143 | 180 | 197 | 158 | 202 |
| reference-only | 3 | 3 | 0 | 0 | 2 |
| skipped | — | 0 | 0 | 0 | 0 |
| failed | — | 0 | 0 | 0 | **1 (real sign error)** |
| relation findings | 0 | 0 | 0 | 0 | 1 |
| percentage findings | — | — | — | — | 1 |
| report == state log | **mismatch: fundamentals** | **ok** | **ok** | **ok** | **ok** |
| spec source | — | reused | reused | reused | reused |
| spec compatibility | — | **unknown** | **compatible** | **compatible** | **compatible** |
| run purpose | — | comparison | comparison | comparison | comparison |
| structured fallbacks | invisible | **Trader, Portfolio Manager** | none | **Research Manager, Trader** | **Portfolio Manager, Research Manager** |
| decision integrity | absent | absent | absent | absent | **DEGRADED (named reasons)** |
| final rating | Underweight | Underweight | Underweight | Underweight | Underweight |
| Research Manager | Underweight | Underweight | Underweight | Underweight | Underweight |
| Trader action | Sell | Sell | Sell | Sell | Sell |
| Trader typed trailer | no | no | **yes** | no | **yes** |
| PM price target | 7,300 | — (free text) | 7,600 | 7,400 | — (free text) |
| PM time horizon | not provided | — (free text) | 3-6 months | 3-6 months | — (free text) |
| fundamentals chars | 33,112 | 31,385 | 36,663 | 35,482 | 35,873 |
| challenge chars | 23,853 | 16,588 | 21,287 | 22,604 | **6,078** |
| risk chars | 69,106 | 59,961 | 64,205 | 60,352 | 67,088 |
| duplicate ratio, challenge | 0.10 | 0.15 | 0.38 | 0.06 | **0.00** |
| duplicate ratio, risk | 0.10 | 0.03 | 0.06 | 0.06 | 0.04 |

The 17:20 production run is not in this table: it loaded the old cached spec, was refused
by the Phase 3 rule, and is preserved as `production-1720-as-run` / `production-1720-regenerated`
(the latter with the audit note and the UNAVAILABLE header).

## What each arm establishes

1. **The conclusion is stable.** All five reach Underweight with a Sell trader action. The
   fixes changed what the report can prove, not the recommendation.
2. **B5 was real and is fixed.** The baseline's published fundamentals sentence ("is below
   the >¥400bn level") differs from its state log ("rising above"): the old save-time
   repair rewrote the report after the decision was recorded. A, B, C and D are all
   `sections_vs_state=ok`.
3. **The audit reconciles.** Baseline: "143 verified" with 3 references and no skipped
   bucket. A: 183 = 180 + 3. B: 197 = 197. C: 158 = 158. The counts differ because the
   models marked different numbers of figures; what matters is that each total now adds up.
4. **The hidden fallback was transient and decided the fields.** Same provider, model and
   prompts: A fell back for Trader and PM, B for neither, C for the Research Manager and
   Trader. Under fallback the PM's prose loses the labelled fields; with the schema it
   prints a target and a horizon. The baseline's "not provided" was this, and it was
   invisible until Phase 2.
5. **Provenance works.** A is `reused/unknown` (spec predates manifests); B and C are
   `reused/compatible` against their own manifests. Price is not in the key: both B and C
   reused their operating forecast and only re-valued at the run-date close.
6. **Phase 3 removed the valuation mix.** B's spec priced the band on FY3/27 and the Base
   scenario on FY3/28; the new rule rejects it by name. C's spec declares
   `fair_value_period: FY3/28`, EPS 224.1, band 6,274–8,515, and all three scenarios on
   FY3/28 (Bear 4,945 / Base 8,000 / Bull 9,530). Band, P/E (35.9x), scenarios and the
   case table now share one EPS.
7. **Repetition is not fixed by any of this.** The challenge duplicate ratio moved
   0.10 → 0.15 → 0.38 → 0.06 across runs with no reviewer change: it follows the spec and
   the sampling, not the code. Phase 4 (delta-only rounds) remains the lever.

## Arm D — the validated path (Phases 1–4)

Arm D loaded arm C's one-period spec explicitly, so every layer ran:

- `1b_research`, `1c_audit` and `1d_cases` all exist; provenance `reused/compatible`.
- Audit: **204 marked = 202 checked + 2 references, 1 failed**. The failure is a genuine
  sign error in the fundamentals report: the FY3/29 bridge has **−20** of depreciation,
  the prose wrote "with 20 of depreciation increase". The old audit could not see it.
- **Delta rounds work**: the challenge is 6,078 characters (A–C: 16.6k–22.6k), the second
  round answered `NO MATERIAL UPDATE`, the debate stopped, and the duplicate ratio is 0.00.
- **Decision integrity works**: the header and the decision both say `DEGRADED` because the
  Research Manager and Portfolio Manager fell back to free text and exact sizes were
  proposed with no portfolio context; the decision carries the `Sizing note`.
- Two findings remain in the aggressive speech: one relation claim and the repeated
  "50-day sits 20.9% above price" (correct: 26.4%). The percentage now has a deterministic
  fixer (`fix_percentage_claims`) applied before any model repair; it landed after arm D,
  so the next run is where it shows.

## What the comparison first looked like, and what it actually was

While diffing logs I read the 2026-11-02 memory entry as a blank placeholder and wrote
that a Hold nobody made was blocking the real decision. Reading the block in full showed
the opposite: it is a complete **Hold** decision (price target 7,970, horizon 6-12 months,
`[[215.4]]` marks) written at 11:19. `store_decision` was right to keep it: one decision
per ticker and date is the rule, and the later Underweight was a second call for the same
day.

The empty-entry guard added in Phase 1 remains, but as a defensive rule rather than a fix
for this log: an interrupted write can leave an entry with no decision text, it must not
block a real one, and settlement must not ask the model to reflect on nothing.

The production run at 17:20 also exposed a reporting gap: when the research spec is
rejected, the header said nothing at all. It now prints `Research model: UNAVAILABLE —
<reason>. This is the deterministic fundamental model, not the LLM`, so a rejected spec
cannot be mistaken for a model outage.

## Verdict

Phase 1–3 hold on a real run: the audit is truthful, the recorded decision and the report
are the same artifact, the fallback is visible, the spec's dependencies are checked, and
one valuation period is enforced. What remains before a production run you can trust is
Phase 4 (review length) — not correctness of the numbers.
