# TradingAgents — architecture, workflow and code review

Reviewed: `6630404` (working tree), 2026-11-03.
Evidence run: `C:\Users\yunguang\.tradingagents\logs\reports\4062.T_20261103_135144`
(produced by this tree). Corpus: `...\YL P Drive\Ibiden`.
Prior review artifacts in `review\4062.T_20261102\` were read for context only; findings
below are re-verified against the current code.

Method: read the graph, agents and reporting paths directly; reproduced the arithmetic
and regex bugs with short scripts; re-read the run's report, audit, decision and `run.log`
to confirm which paths actually fired. `ruff check .` is clean, so nothing here is lint.

> **Status (implemented in this tree).** Every defect A1–A13 below is fixed, and the
> model-truth layer, repair gate, dual audit and prompt changes are in place. See §12 for
> what changed, how it is tested, and what is deliberately left out.

---

## 1. What the system is

A LangGraph multi-agent equity-research pipeline. Four analysts gather evidence in
parallel, an adversarial reviewer challenges it, a Research Manager sets a plan, a Trader
converts it into an action, three risk analysts debate it, and a Portfolio Manager issues
the controlling decision. Reports are written as a sectioned markdown tree plus a
self-contained HTML page.

Two packages: `tradingagents/` (library) and `cli/` (Typer + Rich).

An opt-in deterministic layer sits on top of the LLM debate: with
`TRADINGAGENTS_RESEARCH_DIR` set, a validated fundamental model (periods, bridges,
valuation, scenarios) is built from the local corpus, then every agent that decides sees
that model, and a number audit checks the figures the agents marked as the model's.

---

## 2. Run flow

```
cli/main.py                      parse flags, --price -> TRADINGAGENTS_MODEL_PRICE
  cli/selections.py              env / prefs / prompts -> ticker, date, analysts, provider, models
  cli/run.py::run_analysis       headless gap check, config, graph, stream, display, memory, save
    TradingAgentsGraph.__init__  set_config, two LLM clients, memory log, GraphSetup, compile
    GraphSetup.setup_graph       one StateGraph; analyst subgraphs built per selected key
      .stream(...)               node-by-node events -> cli/display.py
    prepare_final_state          case table -> audit -> repair -> conflicts -> stage status
    record_decision              append to the memory log (production runs only)
    save_reports                 reporting.write_report_tree (+ HTML)
```

`TradingAgentsGraph.propagate()` is the library equivalent and runs the same four steps;
the CLI drives `stream_run()` and calls them itself so it can display progress.

---

## 3. The graph

`graph/setup.py:113-200`.

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

Consequences: analysts never see each other's messages, never write the same state key, and
a model that keeps calling tools is cut off after `max_tool_rounds` and told to write
(`WRAP_UP`, `agents/analysts/turn.py:7-27`). The wrap-up turn binds no tools and converts
the history's tool calls/results into plain text first, so a provider that rejects an
orphaned tool history still answers.

| Analyst (state key) | Node label | Tools | Model tier |
|---|---|---|---|
| `market` (`market_report`) | Market Analyst | `get_stock_data`, `get_indicators`, `get_verified_market_snapshot` | quick |
| `social` (`sentiment_report`) | Sentiment Analyst | none — news + StockTwits + Reddit are prefetched into the prompt | quick |
| `news` (`news_report`) | News Analyst | `get_news`, `get_global_news`, `get_macro_indicators` | quick |
| `fundamentals` (`fundamentals_report`) | Fundamentals Analyst | statements, news, insider, plus `CORPUS_TOOLS` only when a corpus is configured | quick |

The analyst key is `social`; the report name is `sentiment`
(`reporting.analyst_names()`). Saved configs and the state use `social`.

### 3.2 Adversarial Reviewer (the debate)

Replaces the older bull/bear pair (`agents/researchers/adversarial_reviewer.py`).

- **Round 1** uses `TEMPLATE` (`:52-164`): a nine-heading Markdown contract (bull case, bear
  case, what each fails to explain, decisive disagreement, resolving evidence, strongest
  opposing argument, rebuttal/concession, confidence) plus two JSON case objects capped at
  ±5% revenue/operating profit and ±20% multiple (`research/cases.py`).
- **Round 2+** uses `DELTA_TEMPLATE` (`:167-195`): "report only what changed", or the literal
  `NO MATERIAL UPDATE`, which ends the loop early (`conditional_logic.py:12-23`).
- Missing sections trigger **one repair pass that only asks for the missing headings**
  (`merge_sections`, `:281-319`): the raw answer is never replaced, only completed. Missing
  rebuttal triggers a second focused retry (`:409-416`).
- `first_review` / `first_review_raw` / `last_review` are kept separately so the report can
  show "Round 1" and a later material update without losing what was first written.

Exit: `Research Manager` when `count >= max_debate_rounds` or no material update.
`DEBATE_PATH_MAP` (`setup.py:36-39`) maps *every* return value, so a router surprise cannot
crash LangGraph with a missing path.

### 3.3 Research Manager and Trader

- **Research Manager** (`agents/managers/research_manager.py`): structured `ResearchPlan` →
  `ResearchPlanMinimal` → free text. Enforces the no-benchmark rule: `Overweight` /
  `Underweight` are relative ratings and are rewritten to `Hold` plus an explicit absolute
  view when no benchmark is configured (`:115-126`).
- **Trader** (`agents/trader/trader.py`): structured `TraderProposal` → free text; receives
  the technical market report so entry/stop levels are grounded in price structure. With no
  portfolio or an unverified model it clears sizing/entry/stop, downgrades `Buy` to `Hold`,
  and scrubs any execution levels the free text invented (`:112-147`).

### 3.4 Risk loop

Three fixed personas (`agents/risk_mgmt/*.py`) alternate
`Aggressive → Conservative → Neutral` until `count >= 3 * max_risk_discuss_rounds`
(`conditional_logic.py:25-35`). Each gets the others' latest arguments through
`opponent_argument_or_opening`, which substitutes an explicit "has not spoken yet" marker
instead of an empty string — an empty string used to make the model invent the opponent's
position.

### 3.5 Portfolio Manager

`agents/managers/portfolio_manager.py`. Structured `PortfolioDecision` → `PortfolioDecisionMinimal`
→ free text. The typed object is the decision; the markdown is its rendering, so a rating
quoted in the thesis cannot replace the actual call.

It also:
- backfills `time_horizon` from the run's decision horizon (`:430-432`);
- backfills a missing fair-value range from the research package (`:433-440`, see finding B2);
- clears valuation fields and forces `confidence = Low` when the model is unavailable
  (`:441-449`);
- rewrites `Overweight`/`Underweight` to `Hold` without a benchmark (`:472-482`);
- runs the sizing gate when there is no portfolio (`:456-461`).

---

## 4. How the agents talk to the LLM

### 4.1 Two tiers

`llm_clients/factory.create_tier_client(config, "quick"|"deep")`. Quick serves analysts,
the reviewer, debaters and trader; deep serves Research Manager and Portfolio Manager.
Each tier may name its own provider and endpoint (`deep_think_provider`, `quick_think_provider`).

### 4.2 Structured-output ladder

`agents/structured.py:42-107`:

1. `bind_structured(llm, Schema, name)` at agent creation — skipped if the provider has no
   structured output.
2. `invoke_structured(...)` at call time; any failure records `status["reason"]` and returns
   `None`.
3. The agent then tries a **minimal schema** (rating + summary only), then free text.
4. `invoke_structured_or_freetext` is the sentiment analyst's version.

Failures are reported, not hidden: the agent returns `structured_fallbacks`, which merge
into state (`agents/state.py:7-9`) and reach the header
(`- Structured output: …`, `reporting.py:113-121`) and `decision_integrity`.

### 4.3 Prompt assembly

- `agents/context.py` owns the shared blocks: `instrument_context`, the verified-model
  block (`research_package_block`, `:265-282`), `FIGURE_MARKER_RULE` (`:227-241`),
  `COMPARATIVE_CLAIM_RULE` (`:243-254`), `SIZING_RULE`, `case_block`, `evidence_pack`,
  `report_or_absent`, `get_language_instruction`.
- The model figures are marked `[[229.5]]` by the agents that quote them; `reporting` strips
  the brackets once for the published tree.
- `NO_EXTERNAL_TOOLS` is appended wherever structured output binds exactly one tool, because
  a model that reaches for search emits an unknown tool call and loses the whole attempt.

### 4.4 Language

`get_language_instruction()` is appended to every report-producing agent; the labelled
lines (`**Rating**:`) stay English so the parsers keep working.

---

## 5. Deterministic research model (opt-in)

`TRADINGAGENTS_RESEARCH_DIR` enables it; unset, behaviour is unchanged.

```
corpus manifest -> filing parser (Japanese tanshin, optional) -> filing facts
   -> vendor annual/quarterly statement (fallback evidence)
   -> builder.py: the LLM returns one JSON spec
   -> kernel.py: validates every identity (bridges sum, margins recompute, EPS divides);
                 a failing spec is handed back once, then rejected
   -> compact_summary (prompt text) + compact_summary(instructions=False) (published artifact)
   -> agents -> audit.py checks marked figures against the model
   -> cases.py recomputes the reviewer's bull/bear deltas on the model
   -> valuation.py sets fair value / scenarios / sensitivity
```

Provenance: a built spec gets a `.meta.json`; a reused spec is checked as
`compatible` / `unknown` / `incompatible` (`research/provenance.py`). A production run
rebuilds rather than publish numbers it cannot verify. A rejected spec is reported as
`MODEL UNAVAILABLE` in the header, never silently dropped.

The audit (`research/audit.py`) has four families, deliberately different in kind:

- **figures** — only `[[marked]]` values, checked against the model (provenance-based);
- **relations** — "38.6 already exceeds 63.7" checked against the operands printed in the
  same sentence, or an explicit model period label; one LLM repair pass, accepted only if
  the rewrite passes the same check;
- **percentages** — "the 50 SMA sits 20.9% above price" recomputed against the run price,
  with a deterministic fixer before any model repair;
- **grounding / terminology** — warnings: an untagged numeric sentence in the review, a
  growth rate labelled an incremental margin.

---

## 6. Data layer

`agents/tools.py` → `dataflows/router.route_to_vendor()` → `dataflows/vendors/<vendor>/`.

- The configured vendor string **is** the chain (comma-separated = ordered fallback); no
  silent fallback to a vendor the user did not choose. `"default"` means all vendors that
  implement the method (`router.py:238-263`).
- Failures: `VendorUnavailableError` / `VendorNotConfiguredError` / `NoMarketDataError` are
  turned into explicit `DATA_UNAVAILABLE` / `NO_DATA_AVAILABLE` sentinels with instructions
  not to fabricate; optional categories (macro, prediction markets) degrade instead of
  aborting a core call (`router.py:299-335`).
- Point-in-time: every dated tool takes `trade_date` from state via `InjectedState` and
  clamps through `dataflows/date_window.py`. Present-day-only vendor snapshots are withheld
  from historical runs.

---

## 7. Memory log

`memory/log.py` — an append-only markdown file of decisions and, after settlement, their
outcome (realized return, alpha, reflection).

- The `Memory Log` node runs **alongside** the analysts (`trading_graph.py:1028-1055`):
  settles every ticker's due decisions, then reads the past context known by the trade date.
- Only the Portfolio Manager consumes `past_context`.
- A failure there does not end the run; `memory_note` says what could not be settled.
- Diagnostic runs (`run_purpose != production`) do not write decisions.

---

## 8. Reporting and CLI

`reporting.write_report_tree` (`reporting.py:214-266`) writes:

- the investor-facing sections to `complete_report.md` and their own files;
- the debate transcript, number audit and case table to `appendix.md`;
- `complete_report.html` when asked (`report_html.render_report`) — a self-contained page
  with inline CSS, no scripts, raw HTML and images disabled.

The header (`reporting.py:20-122`) states analysis date, rating (with `(provisional)` when
the decision is degraded), version/providers/models, analysts, rounds actually used,
horizon, vendors, research-model provenance, run authority, decision integrity, stage
status, controlling decision, and every structured fallback.

The CLI's own `<results_dir>/<ticker>/<date>/reports/*.md` writer
(`cli/run.py:204-217`) is a second, independent report tree — see finding B13.

---

## 9. Findings

Severity: **H** = wrong output or wrong recorded decision; **M** = quality loss or silent
degradation; **L** = cosmetic/diagnostic.

### A. Bugs

#### A1 (H) — The percentage audit mis-reads its own target sentence

`research/audit.py:492-548`.

`_PCT_LEVEL` is `\b(?:SMA|EMA|VWMA|moving average|average)\b[^\d]{0,16}(\d[\d,]*(?:\.\d+)?)`.
`[^\d]{0,16}` happily spans the words between the indicator name and the **percentage in the
claim itself**, so the percentage becomes the "level".

Reproduced on the sentence the 13:51 run actually published:

```
sentence: "You say the 200 SMA is only 3.4% below spot and was pierced intraday to 7,695, ..."
level   : 3.4      (the claim's own percentage)
price   : 8,045
expected: -100.0%  -> finding
```

This is the only audit failure in that run (`1c_audit/numbers.md`), and it is false: 3.4 is
the distance, not a level. The docstring at `:505-509` claims the sentence "must name the
indicator and its level"; the code never checks that a level was printed.

Effect: a false finding on every run that quotes another analyst's percentage; it triggers
the LLM repair pass and survives into the appendix (verified in `appendix.md` of 13:51).

Fix: exclude the claim span and require a real price scale: add `(?!\s*%)` after the level
group, and skip a candidate whose span overlaps the claim. Add a test with the quoted
sentence — the existing tests only cover the "50-day SMA 10,168.07 … 20.9% above price"
shape.

#### A2 (H) — The Portfolio Manager's fair-value backfill cannot match the package it reads

`agents/managers/portfolio_manager.py:346-362` vs `research/report.py:31` and `:151-292`.

`_FAIR_VALUE_RANGE` looks for `Fair-value range: 6,304-9,246`. That line exists only in
`report._header`, the **full research report**. The state key the PM reads is
`research_package`, which is `compact_summary(...)` and prints:

```
Valuation: price 8,045 JPY, FY3/28 EPS 229.6 on full_share, P/E 35.0x, fair value 6,428-9,642 JPY.
```

Reproduced:

```
real package   -> (None, None)
test fixture   -> (6304.0, 9246.0)
```

The test (`tests/test_structured_agents.py:829-868`) feeds the hand-written
`"- Fair-value range: 6,304-9,246 JPY"`, a string the application never produces. So the
PHASE11 §4 fix ("the range is read from the model's printed `Fair-value range` line") is not
wired: when the minimal schema omits the range, the decision still prints
`**Price Target**: not provided` beside a verified model that has one.

Fix: match the renderer's actual line (`fair value`, case-insensitive, optional
`range:`/hyphen), or better, carry `fair_value_low/base/high` on the state when the package
is built instead of re-parsing prose. Build the test from `compact_summary` output.

#### A3 (H) — `read_research_document` has no point-in-time guard

`agents/tools.py:268-290`, `dataflows/vendors/research_corpus/store.py:203-238,341-348`.

`list_research_documents` and `search_research_documents` both take `trade_date` and clamp;
`read_research_document` takes only `document_id`, `ticker`, `max_chars`. The listing prints
withheld documents' `storage_path` (`store.py:329-332`) and ids are derived from the
filename (`manifest.py:186-188`), so the id of a document published after the run date is
both visible and guessable.

Effect: a historical run can read a post-cutoff filing in full. That is the one leak every
other dated path in `dataflows/` is built to prevent.

Fix: add `trade_date: InjectedState("trade_date")` to the tool, pass it into
`read_document(..., as_of=...)`, and refuse (same sentinel shape as the search tool) when
`published_at` is later. Add a lookahead test mirroring the existing corpus tests.

#### A4 (H) — `TRADINGAGENTS_RUN_PURPOSE` is ignored by the CLI

`cli/run.py:144`, `cli/main.py:77-81`.

```python
config["run_purpose"] = str(flags.get("run_purpose") or "production").strip().lower()
```

The typer option defaults to `"production"`, so `flags["run_purpose"]` is never falsy and
the `or` never fires. `_build_run_config` copied `DEFAULT_CONFIG`, which had already folded
in `TRADINGAGENTS_RUN_PURPOSE` (`default_config.py:22`) — and this line overwrites it.

Effect: `TRADINGAGENTS_RUN_PURPOSE=comparison` alone does nothing. The run is recorded as a
production decision in the memory log and loses the "Authority: diagnostic" header. The
harnesses under `review\4062.T_20261102\arm-*\run.ps1` set both the variable and the flag,
which is why it was not noticed.

Fix: make the option default `None` and fall back to `config.get("run_purpose")`; or only
write the line when the flag differs from its default.

#### A5 (M) — Structured output is best-effort on the default thinking model

`llm_clients/capabilities.py:54-60`, `llm_clients/openai_client.py:41-54`.

DeepSeek thinking models get `supports_tool_choice = False`, so
`with_structured_output(method="function_calling")` is called with `tool_choice=None`. The
schema is bound as a tool but **not forced**, so the model may answer in prose. LangChain
then returns no parsed object and `invoke_structured` records
"structured output returned no parsed result" (`structured.py:79-83`).

Evidence from the 13:51 run (`run.log`):

```
13:44:49  Research Manager: structured-output invocation failed (… returned no parsed result)
13:51:17  Portfolio Manager: structured-output invocation failed (… returned no parsed result)
```

Both retried on the minimal schema, which succeeded; the report therefore has no typed
`fundamental_view`, `valuation_view`, `technical_timing`, `new_investor_action`,
`existing_holder_action` or `confidence`, and `render_pm_decision` **omits the whole
Decision framework table** when every field is `None` (`schemas.py:424-475`). A reader
cannot tell "not assessed" from "not reported" — the 13:51 `decision.md` has no table at
all.

Fix, in order of value:

1. Retry the **full** schema once with an explicit "you must call the tool, do not answer in
   prose" instruction before dropping to the minimal schema. The failure is a sampling
   miss, not a capability gap.
2. Render the framework table with explicit `not provided` cells instead of omitting it.
3. For thinking models, consider `json_mode` + the schema in the prompt as
   `preferred_structured_method`; the capability table already models that choice.

#### A6 (M) — `--price` is rounded before it reaches the valuation

`cli/main.py:109-110`: `os.environ["TRADINGAGENTS_MODEL_PRICE"] = f"{price:g}"`.

`%g` keeps six significant digits: `1234.5678 → 1234.57`, `1234567 → 1.23457e+06 →
1234570.0`. The override is also not echoed in the header, so the substitution is
undetectable after the run.

Fix: `repr(price)` (round-trips exactly) and add the override to `run_settings()` so the
header can show it.

#### A7 (M) — "Nobody answered the prompt" means "yes"

`cli/run.py:470,496` vs `:502-522`.

```python
save = (_ask("Save report?") or "").strip().upper() in ("Y", "YES", "")
```

`_ask` returns `None` on EOF / Ctrl-C / Ctrl-D, and its docstring says callers treat `None`
as "no". `or ""` converts that to the empty string, which **is** in the accepted set (it
means "pressed Enter"). `_yes()` (`:518-522`) does it correctly with an explicit
`if answer is None: return False` — which is why `--open` is safe and these two are not.

Effect: a dropped SSH session or closed stdin finishes by writing the report tree and
printing the whole report without consent, after printing "skipping the remaining
questions".

Fix: use `_yes()` for both, and stop after the first `None`.

#### A8 (M) — Backtest `pending` is a residual, not a count

`backtest.py:187-217`.

```python
pending = len(entries) - len(resolved) - unscored
```

A **settled** entry whose alpha cannot be parsed (legacy four-field tag → `_alpha` returns
`None`) is neither resolved nor unscored, so it is reported as pending. The trailer then
says "pending cells are not scored; re-run to settle them" for cells that are already
settled and will never change.

Fix: `pending = sum(1 for e in entries if e["pending"])` and report unreadable settled
entries as their own count (or fold them into `unscored`).

#### A9 (M, latent) — `parse_last_close` assumes Yahoo's CSV shape

`dataflows/prices.py:20-36`.

It requires a `Close` header and takes the **last** row. Alpha Vantage's CSV is lowercase
(`close`) and newest-first, and `router` treats a successful call as a success. With
`core_stock_apis: alpha_vantage`, `last_close` returns `None` and the valuation silently
falls back to the spec price with a "no market price was available" warning — a parse
problem reported as a data outage.

Not triggered by the default chain (Bloomberg/Yahoo both fit). Fix: case-insensitive header
lookup and pick the row with the greatest date, or route through `load_bars` as
`closes()` already does.

#### A10 (M) — The HTML report drops the qualifiers the Markdown carries

`report_html.py` (no reference to `decision_integrity` or `run_purpose`) vs
`reporting.py:27-31,85-91`.

`complete_report.md` says `- Rating: Hold (provisional)` and lists
`- Decision integrity: DEGRADED: …` and, for diagnostic runs, `- Authority: diagnostic`.
`complete_report.html` renders the bare rating and a resource list that contains neither.

Effect: two artifacts in the same folder disagree about how trustworthy the decision is.

Fix: add both rows to `_run_details`, and apply the same `(provisional)` suffix in the page
masthead.

#### A11 (L) — Two clocks for "today"

`dataflows/date_window.py:34-36` uses `date.today()` (local); `in_window` (`:28-31`) uses
`datetime.now(UTC)`; `cli/prompts.py:84-92` validates a new date against local today.

On the CI job that runs `TZ=America/New_York`, a UTC-scheduled `--date` for the UTC date is
rejected as "in the future" for the hours between UTC midnight and local midnight, while
`in_window` already treats that date as "now". `reporting.py:32` and `report_html.py:226`
also stamp a naive local time with no offset.

Fix: one helper (`datetime.now(UTC).date()`) for the current date, and an explicit
`+00:00` or `UTC` suffix on generated timestamps.

#### A12 (L) — Trader downgrade note names a reason that may not apply

`agents/trader/trader.py:123-132`. `downgraded = unverified and proposal.action == BUY`,
but the appended note always says "because the valuation is unverified **and no portfolio
context was supplied**". With a portfolio supplied, the sentence is false.

Fix: build the note from the reasons that actually fired.

#### A13 (L) — The CLI's live report tree keeps the audit marks

`cli/run.py:204-217` writes `obj.report_sections[...]` verbatim; `reporting._report_parts`
and `display_complete_report` both call `strip_marks`. Verified on the 13:51 run:

```
<results>/4062.T/2026-11-03/reports/fundamentals_report.md   [[ marks: 133
<results>/reports/4062.T_20261103_135144/1_analysts/fundamentals.md  [[ marks: 0
```

The folder a user opens first is the one that shows the brackets.

Fix: `strip_marks(text)` in that writer too (one import).

---

### B. Dead code

| Symbol | Where | Status |
|---|---|---|
| `_periods_by_basis` | `research/render.py:38` | defined, never called anywhere |
| `render_metrics` | `research/metrics.py:69` | only tests call it; `period_metrics` is used by the audit, but the metrics table is never printed in production |
| `parse_case` | `research/cases.py:205` | production uses `parse_cases`; only tests import this |
| `normalize_probabilities` | `research/cases.py:262` | only tests call it; `render_results` normalises inline |
| `_package_fair_value_range` | `agents/managers/portfolio_manager.py:351` | reachable only through a string the app cannot produce (A2) |

Not dead, for the record: `BlpapiBackend` looks unreferenced to a naive scan but is
registered by the `@register_backend("blpapi")` decorator (`dataflows/vendors/bloomberg/`).

Cosmetic: `graph/trading_graph.py:447-448` carries a "sizing" comment immediately above
`_stage_status`, where it belongs to `_apply_decision_integrity` above it.

---

### C. Tests that hide the bugs they describe

- `tests/test_structured_agents.py:862` — the PM range backfill test uses
  `"- Fair-value range: 6,304-9,246 JPY"`, a line no renderer emits. It passes while the
  production path returns `(None, None)`.
- `tests/test_cli_shutdown.py` — asserts "nothing saved without an answer", but the fake
  graph has no `save_reports`, so the `AttributeError` lands in the `except` and the
  consent branch is never exercised.
- No test covers the percentage audit against a sentence that **quotes** another analyst's
  percentage, which is the shape that fires in every run with a risk debate.

---

### D. Checked and found sound

- The rating parser (`agents/rating.py`) and the conflict detectors
  (`trading_graph.py:473-499`) match the renderers: `**Rating**: X`, `**Recommendation**: X`,
  `**Action**: X`, `| New investor | X |`, and the `**Stop Loss**: not provided` negative
  lookahead.
- Graph routing: every router return value is present in `DEBATE_PATH_MAP` /
  `RISK_ANALYSIS_PATH_MAP`; `count // 3` is correct for the risk rounds.
- Report path containment (`safe_ticker_component`, `_run_directory`) and HTML escaping
  (CommonMark with `html=False`, images disabled, no scripts).
- Sizing gates: they only fire without a portfolio, and a failed rewrite falls back to
  scrubbing rather than trusting the model.
- The audit's figure check really is provenance-based (only `[[marks]]`), so it does not
  re-open the false-positive problem that inference over unmarked prose caused.
- The 13:51 run's number audit reconciles: 135 marked = 135 checked + 0 reference + 0
  skipped, 0 failed.

---

## 10. Suggested fix order

1. **A2 + its test** — the decision currently lies about its own valuation in the exact
   case the code was written for. Small, self-contained.
2. **A1** — five-line regex fix; removes a false audit finding from every debate run.
3. **A4** — one line; restores the diagnostic/production separation the review harness
   depends on.
4. **A3** — small tool signature change; closes the only point-in-time hole left.
5. **A5** — additive retry plus the framework table rendering; recovers the PM's typed
   fields on the default model.
6. Then A6–A13 as cleanup, and the dead-code sweep.

---

## 11. One-paragraph summary

The architecture is sound and unusually well defended: isolated analyst subgraphs, a
bounded tool loop, provenance-checked structured output with visible fallbacks, a validated
deterministic model, and a report/state pair that are now written from the same text. The
remaining defects are in the seams between components: a regex written for a renderer that
is not the one in use (A2), an audit pattern that matches the claim instead of the operand
(A1), a tool that skipped the date clamp its siblings apply (A3), and a CLI line that
overrides the environment it was supposed to read (A4). None of them changes the investment
conclusion; all of them change what the report can prove about it.

---

## 12. What changed in this tree

### 12.1 Reliability fixes (A1–A13)

| Fix | Change |
|---|---|
| A1 | `_PCT_LEVEL` no longer accepts the claim's own percentage, and a level must sit within an order of magnitude of the run price. Reproduced false positive now returns no finding. |
| A2 | `research/report.fair_value_range_from_summary` parses **both** renderers and lives beside them; `create_run_state` stores `research_fair_value_low/high`; the PM prefers state and falls back to the parser. |
| A3 | `read_research_document` takes `trade_date` and `CorpusStore.read_document(..., as_of=)` refuses a post-cutoff document with the same sentinel shape as list/search. |
| A4 | `--run-purpose` defaults to `None`; `_resolve_run_purpose` falls back to the environment-derived config. |
| A5 | The Research Manager and Portfolio Manager retry the **full** schema once with `STRUCTURED_RETRY` before the minimal schema or free text. |
| A6 | `--price` is written with `repr(price)`, which round-trips exactly. |
| A7 | Save/show prompts use `_yes()`, so "nobody answered" is no, not yes. |
| A8 | Backtest `pending` counts pending entries instead of subtracting a residual. |
| A9 | `parse_last_close` is header-case-insensitive and picks the greatest date, not the last row. |
| A10 | The HTML page carries decision integrity, structured fallbacks, run authority, and a `(provisional)` rating like the Markdown. |
| A11 | `get_current_date()` is UTC; the CLI's future-date check uses it; generated timestamps are labelled UTC. |
| A12 | The Trader's downgrade note names the reason that actually fired. |
| A13 | The CLI's live `reports/*.md` writer strips the `[[…]]` audit marks like the published tree. |

### 12.2 Model truth layer

- `research/periods.py` — canonical fiscal labels derived from the period's own end date
  (`FY3/28`, `FY26`), plus a warning when a company label disagrees with its dates.
- `research/shares.py` — named bases for market cap and forecast EPS, diluted-average and
  post-period-conversion fields, and a warning when the two counts diverge by more than 5%
  (the stale-566.3m failure).
- `research/conflicts.py` — a record-only register of guidance/consensus/model disagreements
  at a 0.5% tolerance, rendered into the research package.
- `research/schemas.py` — additive fields on `ShareBase`, `ValuationSpec` and `Scenario`
  (all optional, so existing specs still load).
- `research/kernel.py` — attaches the register and the period/share warnings to every
  result; market cap now reads the named basis.
- `research/metrics.py::render_metrics` is now published in the appendix
  (`1e_metrics/metrics.md`), so growth and incremental margin are both visible.

### 12.3 Repair gate (no graph change)

- `research/repair.py` — `ModelChallenge`, deterministic `apply_values`, `repair_model`
  (revalidates through the kernel), `changed_fields`, `superseded_values`, and a bounded
  addendum writer whose output is rejected if it introduces a figure the models do not
  contain.
- The gate runs at the **entry of the existing Adversarial Reviewer node**
  (`setup.py::reviewer_with_repair`), so nodes, edges, routing and the checkpoint topology
  are unchanged. It is idempotent: a second entry sees `active_model_version == "v2"`.
- State: `research_model_path_v1`, `active_model_version`, `model_challenges`,
  `model_repair_log`, `model_repair_addendum`, `fundamentals_challenge_check`, plus
  `research_fair_value_low/high` and `metrics_table`.
- A structural challenge that cannot be applied is recorded as unresolved; a **blocking**
  challenge that cannot be applied marks the model unavailable, and the existing degradation
  path (no valuation, confidence Low, Buy downgraded) takes over.
- The v2 spec is written beside v1 as `<TICKER>.v2.json`; v1 is never overwritten.

### 12.4 Audit across two model versions

`audit_text` accepts a `superseded` set. A marked figure that fails against v2 but matches
a value the repair replaced is counted under "superseded", listed in `1c_audit` as *not*
an audit failure, and never fed to the debate. A figure matching an unchanged field still
passes normally; anything matching neither is a genuine failure.

The PM's prose is checked too: if the decision reuses a superseded value,
`decision_integrity` becomes DEGRADED with that reason.

### 12.5 Prompts and decision shape

- The three risk personas keep their internal keys and gain display roles **Lead Analyst /
  Skeptical Analyst / Risk Analyst**, each with an explicit line that the internal label is a
  routing identifier, not a stance.
- `EVIDENCE_DISCIPLINE` + `case_availability()` are appended to the reviewer, managers and
  risk prompts: use every supplied input, label assumptions, and treat a missing news or
  sentiment report as a gap.
- The reviewer asks for one bull argument, one bear argument, a decisive load-bearing
  variable, and an explicit market-edge statement (guidance versus consensus).
- The scenario instructions now require driver-based construction, not a flat percentage.
- The PM template states the resolution rules (do not vote; say which input controls; price
  the cost of waiting; staged entry is allowed) and asks for the earnings-revision,
  catalyst, position-constraint, invalidation and upgrade/downgrade fields.
- `render_pm_decision` always prints the framework table and prints `not provided` where a
  field was not answered; the three condition lists are always shown.
- Case ceiling raised from ±5% to ±25% on revenue/operating profit (multiple stays ±20%),
  with the prompt still requiring evidence for a wide case.

### 12.6 Verification

- `ruff check .` clean.
- `pytest -q`: **1793 passed, 9 skipped, 5 failed**. The five failures are the pre-existing
  Windows/platform failures documented in `review/4062.T_20261102/PHASE1.md`
  (POSIX file modes ×2, `upgrade_command` on Windows ×2, a temp dir under `$HOME`).
- New tests: `tests/test_review_fixes.py` (A1–A12 regressions) and
  `tests/test_model_repair.py` (periods, share bases, conflicts, repair, superseded audit).

### 12.7 Deliberately not in this change
- **Driver-ranked sensitivities** (`research/sensitivity.py`): the valuation still exposes
  the declared multiple sensitivity. A driver ranking needs a mapping from bridge lines to
  scenario deltas, which the current schema does not carry; adding it would be a model-spec
  change, not a kernel tweak.
- **Rebuild-with-challenges**: a `requires_rebuild` challenge is recorded as unresolved
  rather than sent back through the corpus builder. Doing that well needs the builder to
  accept an instruction overlay and its own validation budget; the gate is already one
  LLM call, and this would be two to four more.
- **Full publication trimming**: the appendix already carries the audit, cases and repair
  note, and the header names missing inputs; the debate transcript is still a main section.
  Moving it is a renderer layout change with no correctness effect.
- **Conflict-register publication block**: the register records and the model's figure
  controls, as agreed. It does not block a run when sources disagree, because the
  precedence rules already picked a source before the spec existed.
