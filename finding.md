# TradingAgents — architecture, workflow and code review

Reviewed: `e1e4fda` plus 34 uncommitted modified files (+1,132/−1,000), 2026-11-06.
The working tree carries a new verification layer, a correction ledger and two
decision-postprocessing gates, so this review covers the working tree, not only the tag.

Method: read the graph, agents, research and reporting paths directly; reproduced the
arithmetic, regex and postprocessing defects with short scripts; verified every router return
value and every renderer against the parser that consumes it. `ruff check .` is clean, so
nothing here is lint. The full suite was run: 2,092 passed, 5 failed, 9 skipped, 2 deselected.
All five failures are POSIX assumptions in tests running on Windows (§11); none is a product
defect.

Severity: **H** = wrong output or wrong recorded decision; **M** = quality loss or silent
degradation; **L** = cosmetic, diagnostic, or latent.

---

## 1. What the system is

A LangGraph multi-agent equity-research pipeline. Four analysts gather evidence in parallel,
an adversarial reviewer challenges it, a Research Manager sets a plan, a Trader converts it
into an action, three risk analysts debate it, and a Portfolio Manager issues the controlling
decision. Reports are written as a sectioned markdown tree plus a self-contained HTML page.

Two packages: `tradingagents/` (library) and `cli/` (Typer + Rich).

| Layer | Modules | Lines |
|---|---|---|
| `agents/` | 28 | ~6,250 |
| `dataflows/` | 41 | ~5,300 |
| `research/` | 34 | ~10,000 |
| `graph/` | 7 | ~2,700 |
| `llm_clients/` | 13 | ~1,500 |
| `memory/` | 4 | ~550 |

An opt-in deterministic layer sits on top of the LLM debate: with
`TRADINGAGENTS_RESEARCH_DIR` set, a validated fundamental model (periods, bridges, valuation,
scenarios) is built from the local corpus, then every agent that decides sees that model, and
a number audit checks the figures the agents marked as the model's. The working tree adds a
three-check verification result, a correction ledger, and source-class policing of forecast
claims on top of it.

---

## 2. Run flow

```
cli/main.py                      parse flags, --price -> TRADINGAGENTS_MODEL_PRICE
  cli/selections.py              env / prefs / prompts -> ticker, date, analysts, provider, models
  cli/run.py::run_analysis       headless gap check, config, graph, stream, display, memory, save
    TradingAgentsGraph.__init__  set_config, two LLM clients, memory log, GraphSetup, compile
    GraphSetup.setup_graph       one StateGraph; analyst subgraphs built per selected key
      .stream(...)               node-by-node events -> cli/display.py
    prepare_final_state          case table -> audit -> repair -> conflicts -> verification -> stage
    record_decision              append to the memory log (production runs only)
    save_reports                 reporting.write_report_tree (+ HTML)
```

`TradingAgentsGraph.propagate()` is the library equivalent and runs the same four steps; the
CLI drives `stream_run()` and calls them itself so it can display progress.

---

## 3. The graph

`graph/setup.py:165-213`.

```
START ─┬─ Market Analyst ───────┐
       ├─ Sentiment Analyst ────┤
       ├─ News Analyst ─────────┼─► Adversarial Reviewer ─┐ (loop, max_debate_rounds)
       ├─ Fundamentals Analyst ─┤        ▲                │
       └─ Memory Log ───────────┘        └────────────────┘
                                        └─► Research Manager ─► Trader
                                              ─► Aggressive ─► Conservative ─► Neutral ─┐
                                                   ▲                                      │
                                                   └──────────────────────────────────────┘
                                                                 (3 × max_risk_discuss_rounds)
                                              ─► Portfolio Manager ─► END
```

### 3.1 Analysts (parallel, isolated)

Each selected analyst is compiled as its **own** `StateGraph` (`setup.py:53-94`):

- node `agent` (the analyst), node `tools` (`ToolNode` over its tool tuple), node `wrap_up`;
- `START → agent`, `agent → tools | END`, `tools → agent | wrap_up`;
- `output_schema` is a one-field TypedDict, so the subgraph returns only its report key.

Consequences: analysts never see each other's messages, never write the same state key, and a
model that keeps calling tools is cut off after `max_tool_rounds` and told to write
(`WRAP_UP`, `agents/analysts/turn.py:7-27`). The wrap-up turn binds no tools and converts the
history's tool calls/results into plain text first, so a provider that rejects an orphaned
tool history still answers.

| Analyst (state key) | Node label | Tools | Model tier |
|---|---|---|---|
| `market` (`market_report`) | Market Analyst | `get_stock_data`, `get_indicators`, `get_verified_market_snapshot` | quick |
| `social` (`sentiment_report`) | Sentiment Analyst | none — news + StockTwits + Reddit are prefetched into the prompt | quick |
| `news` (`news_report`) | News Analyst | `get_news`, `get_global_news`, `get_macro_indicators` | quick |
| `fundamentals` (`fundamentals_report`) | Fundamentals Analyst | statements, insider, plus `CORPUS_TOOLS` only when a corpus is configured | quick |

The analyst key is `social`; the report name is `sentiment`
(`reporting.analyst_names()`). Saved configs and the state use `social`.

### 3.2 Adversarial Reviewer (the debate)

`agents/researchers/adversarial_reviewer.py`.

- **Round 1** uses `TEMPLATE`: a nine-heading Markdown contract (bull case, bear case, what
  each fails to explain, decisive disagreement, resolving evidence, strongest opposing
  argument, rebuttal/concession, confidence), a `Decisive sensitivity ID` line, and two JSON
  case objects capped at ±25% revenue/operating profit and ±20% multiple
  (`research/cases.py`).
- **Round 2+** uses `DELTA_TEMPLATE` (and `delta.py`): "report only what changed", or the
  literal `NO MATERIAL UPDATE`, which ends the loop early (`conditional_logic.py:19-20`).
- Missing sections trigger **one repair pass that only asks for the missing headings**
  (`merge_sections`): the raw answer is never replaced, only completed. Missing rebuttal
  triggers a second focused retry.
- The decisive sensitivity ID is validated against the ranked IDs in state; a missing or
  unrecognised one gets one retry, then `not established`.
- `first_review` / `first_review_raw` / `last_review` are kept separately so the report can
  show "Round 1" and a later material update without losing what was first written.

Exit: `Research Manager` when `count >= max_debate_rounds` or no material update.
`DEBATE_PATH_MAP` (`setup.py:36-39`) maps *every* return value, so a router surprise cannot
crash LangGraph with a missing path.

### 3.3 Research Manager and Trader

- **Research Manager** (`agents/managers/research_manager.py`): structured `ResearchPlan` →
  retry → `ResearchPlanMinimal` → free text. Enforces the no-benchmark rule: `Overweight` /
  `Underweight` are relative ratings and are rewritten to `Hold` plus an explicit absolute
  view when no benchmark is configured.
- **Trader** (`agents/trader/trader.py`): structured `TraderProposal` → retry → free text;
  receives the technical market report so entry/stop levels are grounded in price structure.
  With no portfolio or an unverified model it clears sizing/entry/stop, downgrades `Buy` to
  `Hold`, and scrubs any execution levels the free text invented.

### 3.4 Risk loop

Three fixed personas (`agents/risk_mgmt/*.py`) alternate
`Aggressive → Conservative → Neutral` until `count >= 3 * max_risk_discuss_rounds`
(`conditional_logic.py:25-35`). Each gets the others' latest arguments through
`opponent_argument_or_opening`, which substitutes an explicit "has not spoken yet" marker
instead of an empty string — an empty string used to make the model invent the opponent's
position. Each module is ~93 lines and differs mainly in role constant and prompt text.

### 3.5 Portfolio Manager

`agents/managers/portfolio_manager.py` (1,697 lines). Structured `PortfolioDecision` → retry →
`PortfolioDecisionMinimal` → free text. The typed object is the decision; the markdown is its
rendering, so a rating quoted in the thesis cannot replace the actual call.

It also:

- stores the verified model's fair-value band on state and prefers it over parsing prose;
- backfills `time_horizon` from the run's decision horizon;
- clears valuation fields and forces `confidence = Low` when the model is unavailable;
- rewrites `Overweight`/`Underweight` to `Hold` without a benchmark;
- runs the sizing gate when there is no portfolio;
- detects a contradictory entry condition (`agents/resolution.py`) and a reconstructed P/E;
- renders the decision framework table with explicit `not provided` cells
  (`agents/schemas.py:423-517`);
- (working tree) removes rejected-scenario outputs from the decision text
  (`_enforce_rejected_outputs`) and gates a technical trigger above base fair value
  (`_technical_trigger_gate`). Both run after `decision_audit.assess` — see F15–F17.

---

## 4. How the agents talk to the LLM

### 4.1 Two tiers

`llm_clients/factory.create_tier_client(config, "quick"|"deep")`. Quick serves analysts,
the reviewer, debaters and trader; deep serves Research Manager and Portfolio Manager. Each
tier may name its own provider and endpoint (`deep_think_provider`, `quick_think_provider`).

### 4.2 Structured-output ladder

`agents/structured.py`:

1. `bind_structured(llm, Schema, name)` at agent creation — skipped if the provider has no
   structured output.
2. `invoke_structured(...)` at call time; any failure records `status["reason"]` and returns
   `None`.
3. The agent retries the **full** schema once with `STRUCTURED_RETRY` ("call the tool, do not
   answer in prose"), then tries a **minimal schema** (rating + summary only), then free text.
4. `invoke_structured_or_freetext` is the sentiment analyst's version.

Failures are reported, not hidden: the agent returns `structured_fallbacks`, which merge into
state (`agents/state.py`) and reach the header (`- Structured output: …`) and
`decision_integrity`.

### 4.3 Prompt assembly

- `agents/context.py` owns the shared blocks: `instrument_context`, the verified-model block
  (`research_package_block`), `FIGURE_MARKER_RULE`, `COMPARATIVE_CLAIM_RULE`, `SIZING_RULE`,
  `EVIDENCE_DISCIPLINE`, `case_availability`, `case_block`, `corrections_block`,
  `evidence_pack`, `report_or_absent`, `get_language_instruction`.
- The model figures are marked `[[229.5]]` by the agents that quote them; `reporting` strips
  the brackets once for the published tree.
- `NO_EXTERNAL_TOOLS` is appended wherever structured output binds exactly one tool, because
  a model that reaches for search emits an unknown tool call and loses the whole attempt.

### 4.4 Language

`get_language_instruction()` is appended to every report-producing agent; the labelled lines
(`**Rating**:`) stay English so the parsers keep working.

---

## 5. The deterministic layer

This is the strongest part of the design: Python decides what a model's words are allowed to
mean. It has grown into seven gates plus the working tree's verification and ledger.

### 5.1 Model build and repair

`corpus manifest → filing parser → filing facts → vendor statements → builder (one JSON spec)
→ kernel (every identity recomputed) → package + provenance`. A failing spec is handed back
once, then rejected; a rejected spec is reported as `MODEL UNAVAILABLE`, never silently
dropped.

The repair gate runs at the **entry of the existing Adversarial Reviewer node**
(`setup.py:145-156`), so the graph keeps its nodes and checkpoint topology. It mines the
Fundamentals report for explicit model defects, applies scalar corrections to a copy of the
spec, revalidates through the kernel, and writes a v2 spec (`<TICKER>.v2.json`). It is
idempotent via `active_model_version`. A blocking challenge that cannot be applied marks the
model unavailable; the existing degradation path takes over.

### 5.2 Technical evidence

`research/technical.py`. The Market Analyst appends a `TECHNICAL_EVIDENCE:` JSON array of
items with stable IDs (`tech_close_below_50sma`, `tech_macd_negative`, …), two compared
values, a direction, a timeframe and source refs. Each item verifies only when its own
numbers support its direction and its ID belongs to the catalogue. The block is stripped from
the report; only verified IDs reach the PM, and the audit rejects a technical claim the
analyst did not verify.

### 5.3 Packets and output statuses

`research/packets.py` builds purpose-specific decision inputs. When the model is unverified,
`filter_decision_inputs` removes every model-derived field rather than annotating it, and the
PM's answer is scanned for a reconstructed P/E; a second violation invalidates the
controlling decision instead of scrubbing a sentence. `research/dependencies.py` derives each
output's status from its declared inputs, so an unverified EPS basis suppresses P/E and
per-share targets while EV/EBITDA stays usable.

### 5.4 Sensitivities and cases

`research/sensitivity.py` shocks one bridge line at a time and propagates the shock through
every later period (bridges rebased, forecast rows carried, revenue shocks as an explicit
pass-through line), then ranks by fair-value impact. Items carry stable IDs, the valuation
method and a verification status; a shock that cannot propagate is a named failure, never a
zero. The reviewer must cite a `Decisive sensitivity ID` from that table.

`research/cases.py` recomputes each debate case as a delta chain (operating profit → pretax →
tax → net income → EPS → value) and rejects a case whose figures do not reconcile. Caps are
±25% on revenue/operating profit and ±20% on the multiple.

### 5.5 Audits

`research/audit.py` checks only figures an agent marked as the model's (`[[229.5]]`);
`reporting` strips the marks. It deliberately does not infer over unmarked prose — that
produced more false findings than real ones. The relational check verifies a comparative
claim only against operands printed in the same sentence, or an explicit model period label,
with one LLM repair pass that must pass the same check.

The percentage audit searches the text **before** the claim for the indicator level, so a
claim that quotes another analyst's percentage cannot be read as its own operand
(`audit.py:752`). The figure audit is provenance-based, not inference-based.

### 5.6 Decision audit and publication

A directional call without a verified valuation must pass `research/decision_audit.py`:
zero suppressed figures, three independent evidence clusters, a cash-flow/balance-sheet/event
or severe-operating leg, two primary disclosures, and stated horizon/confirmation/
invalidation. Failure normalises Sell → Reduce/Hold and marks the decision provisional.

`complete_report.md` is the investor report and is capped at `MAX_MAIN_WORDS = 5000`; the
full analyst reports, debate transcript, model diagnostics, audit findings, retry logs,
conflicts and rejected scenarios live in `appendix.md`. Sections are budgeted, never cut
mid-sentence.

### 5.7 Corrections and verification (working tree)

- `trading_graph._correction_records` turns every extracted challenge into a ledger record:
  original value from v1, corrected value, evidence refs, affected fields, downstream
  outputs and `ACCEPTED` / `REJECTED` / `UNRESOLVED`. The PM receives the accepted rows as
  text (`context.corrections_block`) as well as through the model.
- `research/verification.py` computes SOURCE_CHECK, MODEL_CHECK and DECISION_CHECK from
  disjoint argument groups; `trading_graph._derive_verification` assembles them and publishes
  the worst applicable outcome in the header (`reporting.py:310-318`).
- The intended behaviour matches `AGENTS.md`. The wiring does not: DECISION_CHECK is handed
  an empty sensitivity list on every run (F14).

### 5.8 Source classes (working tree)

`research/evidence.py` classifies every material input as `COMPANY_ACTUAL`,
`COMPANY_GUIDANCE`, `AGENT_FORECAST`, `EXTERNAL_ESTIMATE` or `AGENT_DERIVED` from the spec
section, and `claim_findings` flags a forecast presented as company guidance or as a
company-guided hurdle. `benchmark_statement` publishes which benchmark the run used. The
rewrite that precedes the audit is silent (F16).

---

## 6. Data layer

`agents/tools.py` → `dataflows/router.route_to_vendor()` → `dataflows/vendors/<vendor>/`.

- The configured vendor string **is** the chain (comma-separated = ordered fallback); no
  silent fallback to a vendor the user did not choose. `"default"` means all vendors that
  implement the method.
- Failures: `VendorUnavailableError` / `VendorNotConfiguredError` / `NoMarketDataError` are
  turned into explicit `DATA_UNAVAILABLE` / `NO_DATA_AVAILABLE` sentinels with instructions
  not to fabricate; optional categories (macro, prediction markets) degrade instead of
  aborting a core call.
- Point-in-time: every dated tool takes `trade_date` from state via `InjectedState` and
  clamps through `dataflows/date_window.py`. Present-day-only vendor snapshots are withheld
  from historical runs. `read_research_document` carries the same clamp as list and search
  (`agents/tools.py:269-291`, `as_of=trade_date`).

---

## 7. Memory log

`memory/log.py` — an append-only markdown file of decisions and, after settlement, their
outcome (realized return, alpha, reflection).

- The `Memory Log` node runs **alongside** the analysts: it settles every ticker's due
  decisions, then reads the past context known by the trade date.
- Only the Portfolio Manager consumes `past_context`.
- A failure there does not end the run; `memory_note` says what could not be settled.
- Diagnostic runs (`run_purpose != production`) do not write decisions.

---

## 8. Reporting and CLI

`reporting.write_report_tree` writes:

- the investor-facing sections to `complete_report.md` and their own files
  (`1_analysts/`, `1b_research/`, `1c_audit/`, `1d_cases/`, `1e_metrics/`, `2_research/`,
  `3_trading/`, `4_risk/`, `5_portfolio/`);
- the uncut analyst reports, debate transcript, number audit, cases and repair note to
  `appendix.md`;
- `complete_report.html` when asked — a self-contained page with inline CSS, no scripts, raw
  HTML and images disabled, and a content-security policy.

The header states analysis date, rating (with `(provisional)` when degraded), version/
providers/models, analysts, rounds actually used, horizon, vendors, research-model
provenance, run authority, decision integrity, stage status, controlling decision, the
benchmark basis, every per-check verification outcome, and every structured fallback.

The CLI writes a second report tree under `<results_dir>/<ticker>/<date>/reports/*.md`; it
strips the `[[…]]` audit marks like the published tree (`cli/run.py:224-234`).

---

## 9. Findings

### A. Verification and decision postprocessing (working-tree additions)

#### F14 (H) — DECISION_CHECK never sees a ranked sensitivity

`graph/trading_graph.py:841`, `:349`, `:1381`.

```python
sensitivities = _json(final_state.get("sensitivity_items"), [])
```

`sensitivity_items` is not a state key and nothing ever writes it. The ranked table lives on
`self._sensitivity_items` (`:349`, set at `:1381`) and is passed to the number audit directly
(`:1044`, `:1063`). So `verification.decision_check` always receives `sensitivities=[]`, and
the loop that returns `FAIL` on a `DECISIVE` item (`research/verification.py:273-296`) never
runs in production.

Effect: the header can publish `DECISION_CHECK PASS` while the top-ranked shock crosses the
price or the neutral band. That is the exact failure `AGENTS.md` says the check exists to
catch ("Only DECISIVE fails DECISION_CHECK"). `tests/test_verification_checks.py` calls
`decision_check(sensitivities=...)` directly, so the wiring gap is invisible.

Fix: write the items to state once (or pass `getattr(self, "_sensitivity_items", [])`, the
pattern the audit already uses) and add a run-level test where a DECISIVE item must yield
`FAIL`.

#### F15 (M) — rejected-scenario cleanup deletes verified sentences by substring

`agents/managers/portfolio_manager.py:758-850`.

`_drop_sentences` removes any sentence containing a token; the tokens include the scenario
percentage as a bare number (`-30%` contributes `-30%`, `-30` and `30%`) and the target as
printed plus its ungrouped form. The test is plain substring containment (`t in s`), not a
whole-token match.

Reproduced with a rejected bear case at `-30%` on base 7,400 (target 5,180), token set
`{"5,180", "5180", "-30%", "-30", "30%"}`:

```
input : The model carries a 30% incremental margin. Verified band 5,180-9,246 with base
        7,400. Cash flow covers the dividend.
output: Cash flow covers the dividend.
```

The margin sentence is dropped because it states the same percentage as the rejected case; the
band sentence is dropped because it quotes the rejected target. The ungrouped token also
collides with longer figures:

```
tokens {"5,180", "5180", ...}
input : Consensus revenue is 15,180 million. Operating margin is 18%.
output: Operating margin is 18%.
```

The executive summary and investment thesis therefore lose verified content while the appended
note tells the reader that only rejected outputs were removed.

Fix: compare whole numeric tokens (regex boundaries or numeric equality), remove the clause
that names the rejected scenario, and never drop a sentence merely because it repeats the
rejected case's percentage.

#### F16 (M) — a forecast relabelled as company guidance is rewritten silently

`graph/trading_graph.py:1030-1039`, `research/evidence.py:266-292`, `research/audit.py:1400-1411`.

`enforce_forecast_labels` rewrites the Portfolio Manager's decision and the Research
Manager's plan **before** `audit.audit_state` runs, and the `findings` it returns are
discarded (`rewritten, findings = ...` only tests truthiness). Because the audit's
`claim_findings` pass runs on the rewritten text, the `evidence` finding that `AGENTS.md`
promises ("a violation is an audit finding of kind `evidence`") never appears, and no note
records that the published decision is not what the PM wrote.

The substitution is also narrow: it replaces "company/management guidance" and "verified
consensus", but a sentence with bare "guidance" keeps its wording and its figure, so the
claim survives the rewrite and only the audit would have caught it.

Fix: emit the rewrite as an `evidence` finding with a `repaired` flag, and add a visible
"forecast label corrected" note to the decision or the appendix; or drop the rewrite and let
the audit finding stand.

#### F17 (M) — the technical-trigger gate reads any large number as the trigger

`agents/managers/portfolio_manager.py:855-880`, pattern at `:733`.

`_PRICE_IN_TEXT` collects every 4-digit or thousands-grouped number in `entry_condition`, and
the gate treats `max(prices)` as the trigger.

```
"entry on a close above 3,000 with a 4,500 target" -> ['3,000', '4,500']  -> trigger 4,500
"entry above 3,000 (stop 2,700)"                   -> ['3,000', '2,700']  -> trigger 3,000
```

With base fair value 4,000 the first case fires the gate, appends "confirmation changes timing
only", and downgrades `INITIATE` to `WAIT` although the entry is 25% below base. The gate also
runs after `decision_audit.assess` (`:1414`) and after `action = _action_from_decision(...)`
(`:1395`), so the published action is not the audited one.

Fix: read the number that follows "above/below/at/through" and ignore numbers attributed to
a stop or target; re-run the audit after an action change.

### B. Decision-input gaps

#### F1 (H) — News and sentiment never reach the research phase

`agents/researchers/adversarial_reviewer.py:95-111` and `:444-486`;
`agents/managers/portfolio_manager.py:1229` (no `news_report` or `sentiment_report`
reference anywhere in the file).

The reviewer template says: *"Use news and sentiment only when the evidence is
company-specific…"* and *"Select the strongest bull case, bear case and opposing argument only
from the evidence in this prompt: the current-run fundamental analysis, technical analysis,
news and sentiment reports."* The fill mapping supplies corpus listing, fundamentals,
technical, model package, horizon, position and prior review — there is no `news_report` or
`sentiment_report` entry anywhere in it.

The Portfolio Manager is the same: it receives `case_availability(state)`
(`agents/context.py:297-309`), which prints *"News and sentiment reports: both available"*
when the keys are non-empty — a statement that they exist, with none of their content. The
only path by which a news event or retail sentiment reaches the controlling decision is the
prose of the risk-debate transcript, compressed by three other models.

Effect: the reviewer is asked to tag evidence `[news_sentiment]` and to reason over inputs it
was never given; the PM is told the sources are available. That is a recorded decision that
claims to have weighed evidence it never saw. Either pass both reports into `_fill` for the
reviewer and the PM, or delete the instructions that ask for them.

#### F2 (M) — A pretty-printed technical block is silently dropped and leaves residue

`research/technical.py:41-43`.

```python
_BLOCK = re.compile(r"TECHNICAL_EVIDENCE:\s*(\[[\s\S]*?\])\s*(?:\n|$)", re.IGNORECASE)
```

The lazy `\[[\s\S]*?\]` stops at the first `]` followed by a newline. Every item contains
`source_refs`, a nested array, so an indented answer ends the match at `source_refs`'s closing
bracket. `json.loads` then fails, `parse` returns no items, and it still strips the matched
prefix — leaving the tail of the JSON in the reader-facing report.

Reproduced with an indented array:

```
items: 0
tail: 'Lead in.\n  }\n]\nTrend down.\n'
```

Effect: the technical leg disappears (`render` prints "No verified technical evidence was
produced"), the PM loses a verified technical input, and the market report published to the
debate contains stray JSON. `tests/test_technical_evidence.py:101-125` only uses single-line
`json.dumps`, so this shape is untested.

Fix: match to the last bracket (`\[.*\]\s*$` with `re.S`) or use
`json.JSONDecoder().raw_decode` from the marker; strip only after a successful parse and
record a warning when the block is malformed.

#### F3 (L) — The market analyst's fundamentals input is always empty

`agents/analysts/market_analyst.py:66` vs `graph/setup.py:192`.

```python
fundamental = (state.get("fundamentals_report") or "").strip()
```

All analysts are added as `workflow.add_edge(START, node)` and run in the same superstep;
state channels merge at the end of it. `fundamentals_report` is therefore `""` on every fresh
run, and the block reliably prints "not supplied". It is dead prompt text in the largest
prompt of the run. Remove the block, or move the market analyst after fundamentals if the
intent was real.

#### F4 (L) — The reviewer's "evidence pack" is an index, not evidence

`agents/context.py:421`.

`evidence_pack()` returns the corpus document listing (ids, dates, page counts, withheld
section), not document text. The reviewer therefore reasons over the fundamentals analyst's
prose plus a table of contents and cannot independently check a claim against a filing. That
is defensible — the analyst did the reading — but "adversarial review" here checks reasoning,
not sources.

### C. Numbers and model inputs

#### F5 (M) — Thousand-yen tables are labelled as millions

`research/balance_sheet.py:53-59`.

```
million[s]? of yen|百万円   -> JPY_m
thousand[s]? of yen|千円    -> JPY_m
million[s]? of u.s. dollars|百万米ドル -> USD_m
thousand[s]? of u.s. dollars|千米ドル  -> USD_m
```

千円 is thousand yen, not million yen. The label reaches the builder through
`balance_sheet.render` ("unit JPY_m"), and `KNOWN_UNITS` in `input_probe.py` has no `_k`
suffix, so a standalone (千円) statement can hand the builder a source unit 1,000× too large.
Add `JPY_k`/`USD_k` (and EUR equivalents) to the alias table and `KNOWN_UNITS`, or convert at
extraction and state the conversion.

#### F6 (L) — `entry_price` can read an indicator period as the price

`agents/resolution.py:19`.

`_PRICE` takes the first number within 40 characters of "entry", so `"entry on a close above
the 50-day SMA"` returns `50.0`. On a low-priced name this trips `contradiction_detected`
(`entry > fair_value * 1.001`) and marks a sound decision as contradictory. Require a price
shape (currency symbol or a 3+ digit number) and ignore `\d+-day` / `\d+-week` tokens. The
working tree's new `_PRICE_IN_TEXT` gate fixes the `50-day` shape but introduces F17.

### D. Structure and contract

#### F7 (M) — The state contract is unenforced

`agents/state.py` vs `tradingagents/graph/trading_graph.py` and `reporting.py`.

Nine keys are written and read on `final_state` but absent from `AgentState`:
`action_status`, `controlling_decision`, `debate_summary`, `decision_conflicts`,
`decision_integrity`, `report_audit`, `research_rounds_used`, `risk_rounds_used`,
`stage_status`. The working tree declared its new keys (`model_corrections`,
`verification_checks`, `verification_status`, `decision_model_version`, `benchmark_basis`) but
left these nine undeclared, so the report is still coupled to string keys the schema does not
describe. The same gap is what `sensitivity_items` fell through (F14).

#### F8 (M) — `trading_graph.py` is a 2,456-line class with a `self` side channel

`_research_package()` returns four values and smuggles eight more through `self`
(`_ev_diagnostic`, `_output_status`, `_basis_names`, `_sensitivity_items`,
`_sensitivity_alias_map`, `_sensitivity_gaps`, `_sensitivity_table`), which
`create_run_state` then reads with `getattr(self, ..., default)` (`:347-352`, `:1131-1187`).
A run's state depends on call order on the same object, and two graphs sharing an instance
would cross-contaminate. A dataclass return value removes the whole class of bug.

The new verification code shows the cost: `_derive_verification` reads a *state* key for
`sensitivity_items` while the data is on `self`, so the check silently loses its input (F14).
The class still mixes graph compile, checkpoint lifecycle, model discovery/building, EV
diagnostics, repair gate, number audit, status derivation, conflict detection, report saving
and state logging. It is the single hardest file to change safely.

#### F9 (L) — Dead branch in `create_run_state`

`trading_graph.py:1166-1172` assigns `VERIFIED` or `NOT_APPLICABLE`, then tests for
`UNVERIFIED` in the same expression. Harmless today, but the "unverified" arm of that status
derivation cannot run there.

#### F10 (L) — Global config mutation survives

`TradingAgentsGraph.__init__` calls `set_config(self.config)` (`:297`), even though the
contract says data/agent code reads `get_config()`. Two graphs in one process share whichever
config was constructed last.

#### F11 (L) — The three risk debators are near-copies

`agents/risk_mgmt/*.py`, 93/93/92 lines, differing mainly in role constant and prompt text. A
single factory parameterised by role would cut ~150 lines with no behaviour change.

#### F12 (L) — `get_prediction_markets` is a maintained but unreachable tool

`agents/tools.py:363` is wired end to end — router category, `polymarket` vendor, default
config key, HTML label, tests — but no agent is offered it. Either re-expose it behind an
explicit analyst or drop the tool and its category.

#### F13 (L) — `ModelSpec.sector` is never produced by the build path

`research/schemas.py:679` carries `sector`, and `default_config.severity_thresholds` has five
sector-specific entries, but `research/builder.py` never mentions "sector". The sector table
therefore only fires for hand-authored fixtures. Add it to the builder schema/prompt, or
delete the table.

---

## 10. Dead code and dormant surface

Confirmed by scanning every package module plus tests for references.

| Symbol | Location | Evidence |
|---|---|---|
| `batch_update_with_outcomes` | `memory/log.py:234` | only a test reference; superseded by `update_with_outcome` used by `memory/settlement.py` |
| `parse_case` | `research/cases.py:322` | tests only; `parse_cases` uses `_scan_cases` |
| `normalize_probabilities` | `research/cases.py:379` | tests only; probabilities are displayed unnormalised |
| `fiscal_year` | `research/periods.py:57` | tests only |
| `dilution_shares` | `research/capital.py:218` | tests only; the dilution sensitivity computes its own count |
| `upside_to_mid` | `research/valuation.py:64` | tests only |
| `TradingAgentsGraph.research_package` | `graph/trading_graph.py:1227` | 7 test calls, 0 production callers — keep only if it is the documented programmatic API |

One near-miss, for the record: `research/quarterly.py` looks orphaned to a top-level import
scan, but `kernel.py:386` imports it inside the quarterly build. It is live.

### Dead prompt slots

Template fields that are filled with literal `"null"` on every run and can only produce
hedged prose:

- `fundamentals_analyst.py`: `primary_listing_or_null`, `exchange_timezone_or_null`,
  `reporting_currency_or_null`, `fiscal_year_end_or_null`, `accounting_standard_or_null`,
  `portfolio_weight_or_null`, `mandate_constraints_or_null`, `risk_tolerance_or_null`,
  `rating_vocabulary_or_null` — nine permanently-null slots.
- `portfolio_manager.py`: `portfolio_weight_or_null`, `mandate_constraints_or_null`,
  `validated_trading_rules_or_null`.
- `market_analyst.py`: hardcoded "Exchange and timezone: not supplied", "Validated trading
  rules, if supplied: not supplied".

Either wire a source (portfolio weight and mandate are the obvious candidates) or remove the
slots so the model stops spending attention on absences it cannot fix.

---

## 11. Tests that hide bugs

- `tests/test_technical_evidence.py:101-125` builds the evidence block with a single-line
  `json.dumps`. The nested `source_refs` array and a pretty-printed answer — the shape that
  triggers F2 — are never exercised.
- `tests/test_cli_shutdown.py:20-29` asserts "nothing saved without an answer", but the fake
  graph has no `save_reports` and `_offer_reports` catches every exception around the save
  call (`cli/run.py:516-522`). If `_yes` regressed and returned `True`, the save would raise
  `AttributeError`, print "Error saving report", create no directory, and the assertion would
  still pass. The test never exercises the branch it names.
- `tests/test_structured_agents.py:851` still builds the PM range-backfill fixture from a
  hand-written `- Fair-value range: 6,304-9,246 JPY` line. The renderer-shaped case is covered
  separately in `tests/test_review_fixes.py:197`, so this is a fixture smell rather than a
  hole — but the test no longer proves the production path on its own.
- `tests/test_verification_checks.py` tests each check in isolation with hand-built inputs.
  Nothing asserts that `_derive_verification` supplies them, which is how F14 survives.
- Portability: the suite is green only on POSIX. On Windows, 5 tests fail:
  `test_chatgpt_auth` (2) assert `0o600` on a file the platform reports as `0o666`;
  `test_cli_updates` (2) expect a POSIX upgrade command although `upgrade_command` returns
  `None` when `os.name == "nt"` (`cli/updates.py:186-187`); `test_suite_isolation` (1) asserts
  the yfinance cache is outside `Path.home()` while the temp root is under it. The first and
  third are test-only assumptions; the second leaves the Windows user without an upgrade
  hint, by design.

---

## 12. Checked and found sound

- Graph routing: every router return value is present in `DEBATE_PATH_MAP` /
  `RISK_ANALYSIS_PATH_MAP`; `count // 3` is correct for the risk rounds.
- The rating parser (`agents/rating.py`) and the conflict detectors match the renderers:
  `**Rating**: X`, `**Recommendation**: X`, `**Action**: X`, `| New investor | X |`, and the
  `**Stop Loss**: not provided` negative lookahead.
- The corpus read path carries the run date: `read_research_document` injects `trade_date` and
  passes it as `as_of`, so a post-cutoff filing is refused with the same sentinel shape as
  list and search.
- The percentage audit searches only text before the claim, so a quoted percentage is not read
  as the indicator level.
- The CLI honours `TRADINGAGENTS_RUN_PURPOSE` (`_resolve_run_purpose` falls back to config),
  and the save/show prompts use `_yes()`, so "nobody answered" is no.
- The CLI's live report tree strips `[[…]]` marks; the published tree and the HTML page carry
  the same provisional/authority/integrity qualifiers.
- Report path containment (`safe_ticker_component`) and HTML escaping (CommonMark with
  `html=False`, images disabled, no scripts, CSP) hold.
- Publication is split correctly: the main report is capped and sectioned; raw prose and
  diagnostics are in the appendix.
- The three verification checks are genuinely independent as functions: each accepts only its
  own arguments, and `assess` takes the worst applicable outcome, never an average
  (`research/verification.py:325-352`). Only the wiring is wrong (F14).
- The correction ledger's identity matching is sound: `repair.verify_challenges` returns the
  same `ModelChallenge` objects it classifies (`repair.py:233-260`), so `id()` membership in
  `_correction_records` cannot silently mislabel an accepted challenge.

---

## 13. Suggested order

1. **F14** — one line, and it decides whether the published verification verdict means
   anything. Add the run-level test with it.
2. **F2** — small, user-visible, and it silently weakens the decision audit.
3. **F1** — decide the intent: pass news and sentiment to the reviewer and PM, or delete the
   instructions that ask for them. This is the only finding that changes what a rating is
   based on.
4. **F15 + F16 + F17** — the three postprocessing gates: stop deleting sentences, stop
   rewriting the decision silently, and stop reading a target as the entry trigger.
5. **F5** — small; prevents a 1,000× unit error in the EV build.
6. **F7 + F8** — declare the missing keys, then replace `_research_package`'s `self` side
   channel with a return dataclass. This is the enabling change for every later refactor and
   is what allowed F14.
7. **F3, F4, F6, F9–F13**, then the dead-code sweep.

Steps 1–5 are self-contained. Step 6 is a few hours but mechanical. Splitting
`trading_graph.py` (research model, repair gate, finalize) is a multi-hour refactor that
should wait for step 6.

---

## 14. Summary

The architecture is sound and unusually well defended: isolated analyst subgraphs, a bounded
tool loop, provenance-checked structured output with visible fallbacks, a validated
deterministic model, a repair gate that revalidates, and a report/state pair written from the
same text.

The working tree adds the right instincts — a correction ledger, source classes, three
independent verification checks — and three of them misfire at the seam: the published
verification never sees the ranked sensitivities (F14), the rejected-scenario cleanup deletes
verified sentences (F15), and the forecast relabelling erases its own audit finding (F16). The
older defects remain: two narrative analysts whose reports the prompt promises and the fill
never supplies (F1); a technical-evidence parser that only survives single-line JSON (F2); a
unit table that confuses thousand with million (F5); and a 2,456-line orchestrator that keeps
part of each run's state on `self` (F8).

None of them changes the investment conclusion on its own. All of them change what the report
can prove about it.
