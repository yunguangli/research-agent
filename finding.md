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


---

## Review 2026-11-04 — architecture, agent workflow and dead code

> This is a second, independent review pass. Its section numbers restart at 1 and are
> standalone; the review above (§1–§12) covers the 2026-11-03 tree and its fixes.

Repository: `research-agent` @ `3c9f399` (2026-11-04)
Scope: `tradingagents/` (138 modules, ~32.5k lines) and `cli/` (10 modules), read against `AGENTS.md`.
Method: Serena symbol/reference lookups, AST scan of every top-level definition and class method,
cross-module reference counts, `ruff check .` (clean), and targeted reads of each agent node.

---

### TL;DR — five findings, ranked

1. **News and sentiment analysts are a side branch.** Their reports reach only the three risk debators.
   The Adversarial Reviewer, the Research Manager, the Trader and the Portfolio Manager never receive them.
   The reviewer prompt even tells the model to "use news and sentiment", but no news or sentiment text is in
   its prompt. ([adversarial_reviewer.py:95](tradingagents/agents/researchers/adversarial_reviewer.py#L95),
   [portfolio_manager.py:441](tradingagents/agents/managers/portfolio_manager.py#L441) — zero mentions of `news`/`sentiment`)
2. **One input in the market analyst's prompt can never be populated.** It injects `fundamentals_report`,
   but all four analysts start in the same LangGraph superstep, so that key is always `""`.
   ([market_analyst.py:66](tradingagents/agents/analysts/market_analyst.py#L66), [setup.py:191-193](tradingagents/graph/setup.py#L191))
3. **`tradingagents/research/quarterly.py` is an orphan module** — 131 lines, imported by nothing, not even tests.
   A second, older implementation of quarterly-plan derivation that the schema + builder now own.
4. **`research/packets.py` carries a whole dead API** (`filter_decision_inputs`, `verified_packet`,
   `unverified_packet`, `packet_for_prompt`, `unverified_reason`, `technical_packet`). Production withholds
   model inputs elsewhere; these functions are only exercised by tests.
5. **The state contract is unenforced.** Nine keys are written and read on `final_state` but absent from
   `AgentState`, and `TradingAgentsGraph` is a 2,036-line class mixing orchestration, model loading,
   repair, audit and reporting.

---

### 1. Layer map

| Layer | Location | Size | Responsibility |
|---|---|---|---|
| CLI | `cli/` | 10 files | Typer app, prompts, Rich live view, run loop, backtest command |
| Orchestration | `graph/` | 7 files, 2,554 lines | `trading_graph.py` (entry + post-processing), `setup.py` (LangGraph wiring), `analyst_execution.py` (analyst specs), `conditional_logic.py`, `propagation.py`, `checkpointer.py` |
| Agents | `agents/` | 26 files, 6,751 lines | 4 analysts, 1 adversarial reviewer, RM, Trader, 3 risk debators, PM, plus schemas/structured/sizing/rating/context |
| Deterministic research | `research/` | 31 files, 9,952 lines | Model spec, kernel, valuation, audit, repair, packets, status, sensitivities, cases |
| Data | `dataflows/` | 41 files, 6,353 lines | Router → vendors (bloomberg, yahoo, sec_edgar, alpha_vantage, fred, polymarket, reddit, stocktwits, research_corpus), point-in-time clamping |
| LLM clients | `llm_clients/` | 13 files | Provider factory, per-provider adapters, tier selection, model catalog |
| Memory | `memory/` | 4 files | Append-only decision log, settlement, reflection |

Dependency direction is clean and enforced: `agents → dataflows.router → dataflows.vendors`; only
`dataflows/` imports `yfinance` (`tests/test_layering.py`). No agent imports a vendor module directly
except the sentiment analyst, which imports the Reddit and StockTwits fetchers at module import
([sentiment_analyst.py:34-40](tradingagents/agents/analysts/sentiment_analyst.py#L34)) — a small
layering exception worth noting, since those are vendor code paths bypassing `route_to_vendor`.

---

### 2. Run workflow

Entry points: `cli/main.py` (Typer) → `cli/run.py:run_analysis`, or `TradingAgentsGraph.propagate()`.

```
START ─┬─ Market Analyst ────────┐
       ├─ Sentiment Analyst ─────┤   (independent subgraphs, same superstep)
       ├─ News Analyst ──────────┤
       ├─ Fundamentals Analyst ──┤
       └─ Memory Log ────────────┘
                                 └──▶ Adversarial Reviewer ──┐
                                        ▲                   │ loop while round < max_debate_rounds
                                        └───────────────────┤ and not "NO MATERIAL UPDATE"
                                                            ▼
                                                    Research Manager
                                                            │
                                                          Trader
                                                            │
                                                    Aggressive Analyst ──┐
                                                            ▲            │ 3 speakers per round
                                          Conservative ◀────┤            │ then Portfolio Manager
                                              │              │
                                              └──▶ Neutral ──┘
                                                            │
                                                    Portfolio Manager ──▶ END
```

Wiring is in [setup.py:167-211](tradingagents/graph/setup.py#L167); routing rules in
[conditional_logic.py:12-35](tradingagents/graph/conditional_logic.py#L12).

#### Step by step

1. **CLI collects selections** (`ticker`, `date`, analysts, depth, provider, models, language, horizon).
   No TTY ⇒ every missing answer is named before any model is called (`cli/run.py:144-150`).
2. **Config assembled** from `DEFAULT_CONFIG` + selections + `TRADINGAGENTS_*` env, with "explicit env wins"
   rules (`cli/run.py:71-116`).
3. **Graph constructed** (`TradingAgentsGraph.__init__`): two tier clients, memory log, conditional logic,
   `GraphSetup.setup_graph()`. `set_config(self.config)` still mutates the process-global config at
   construction (`trading_graph.py:173`).
4. **Run state built** by `create_run_state` (`trading_graph.py:835-957`): instrument identity, portfolio
   block, decision horizon, and — when a research corpus is configured — the verified model package,
   packets, share bases, sensitivity table and output statuses.
5. **Optional checkpoint**: graph recompiled with a per-ticker `SqliteSaver`; the thread id folds in a
   signature of everything that changes the graph shape (`trading_graph.py:252-284`).
6. **Analysts run concurrently**, each as its own subgraph with a private message history and its own tool
   node. After `max_tool_rounds` the graph injects a wrap-up message and forces the report
   ([setup.py:53-94](tradingagents/graph/setup.py#L53), [turn.py:13-27](tradingagents/agents/analysts/turn.py#L13)).
7. **Memory Log** settles past decisions for this ticker and loads lessons; it runs in parallel with the
   analysts because only the PM consumes `past_context`.
8. **Repair gate** runs inside the reviewer's entry (`setup.py:145-156` → `trading_graph.py:1434`): mine the
   fundamentals report for model challenges, apply scalar corrections, re-run the kernel, write a v2 spec,
   emit a repair note. Idempotent via `active_model_version`.
9. **Adversarial review loop**: one reviewer per round; round ≥ 2 is a delta-only prompt; a round answering
   `NO MATERIAL UPDATE` ends the loop early.
10. **Research Manager** turns the review into a typed `ResearchPlan` (depth model).
11. **Trader** produces a 5-field technical stance — no size, no stop price (`trader.py:57-100`).
12. **Risk debate**: Aggressive → Conservative → Neutral, then PM. Each debator sees all four analyst
    reports plus the trader plan.
13. **Portfolio Manager** produces a typed `PortfolioDecision` (depth model), then a deterministic layer
    audits it (see §5).
14. **Post-run `prepare_final_state`** (`trading_graph.py:406-644`, ~240 lines): number audit + one LLM
    repair pass, case table, metrics table, superseded-conclusion replacement, model/decision status
    derivation, conflict detection, stage status, debate summary.
15. **Record + render**: memory log entry, state log JSON, report tree (`reporting.py`) and HTML.

---

### 3. How each agent talks to the LLM

Two clients are built once (`trading_graph.py:179-180`): **quick** and **deep**. Nothing else varies per
agent — no per-agent temperature, token cap or caching.

| Agent | Tier | Call pattern | Extra deterministic guards |
|---|---|---|---|
| Market | quick | ReAct loop: `prompt \| llm.bind_tools(TOOLS)`, ToolNode, wrap-up turn | `technical.parse()` extracts `TECHNICAL_EVIDENCE:` JSON and strips it |
| Sentiment | quick | No tools; three sources pre-fetched into the prompt; structured `SentimentReport` → free text | Jev screening optional (`post_screen.py`) |
| News | quick | ReAct loop over news / global news / FRED | — |
| Fundamentals | quick | ReAct loop over statements + insider + optional corpus tools | model block with `[[figure]]` marking rules |
| Adversarial Reviewer | quick | Plain `llm.invoke(prompt)`; up to 3 extra calls (section repair, rebuttal retry, sensitivity retry) per round | heading contract, case JSON parse, sensitivity-id validation |
| Research Manager | deep | Structured `ResearchPlan` → retry → `ResearchPlanMinimal` → free text | sizing gate, benchmark rule |
| Trader | quick | Structured `TraderProposal` ×2 → free text | sizing gate, execution-level scrub, unverified-Buy downgrade |
| Risk debators ×3 | quick | One `llm.invoke` each | — |
| Portfolio Manager | deep | Structured `PortfolioDecision` → retry → minimal → free text, plus P/E-reconstruction retry, action retry, range retry | decision audit, rating normalisation, `decision_integrity` |
| Repair gate | deep | Structured challenge extraction ×2 + addendum call | kernel revalidation |
| Number-audit repair | quick | One rewrite call when a claim contradicts its own operands | re-audit must pass |
| Reflection (memory) | quick | One call per settled entry | — |

**Consequence:** the nominal 9 nodes can issue 20+ model calls in one run. The stated benefit is
resilience; the cost is that "one agent = one prompt" no longer holds, and latency/cost are dominated by
retry paths rather than by the agents themselves.

**Prompt-as-code.** 85% of `fundamentals_analyst.py` (1,291 of 1,527 lines) is one module-level string.
The portfolio manager carries a 300-line `TEMPLATE` plus a 70-line `_rating_framework` and an `ACTION_RETRY`.
Prompts are not versioned, diffed or evaluated anywhere; a wording change is indistinguishable from a
logic change in review.

---

### 4. Agent-to-agent information flow

What each stage actually receives (verified from each node's prompt construction):

| Consumer | market | sentiment | news | fundamentals | model pkg | review | plan | trader | risk debate | lessons |
|---|---|---|---|---|---|---|---|---|---|---|
| Reviewer | ✅ | ❌ | ❌ | ✅ (with repair notice) | ✅ | prior rounds | — | — | — | — |
| Research Manager | — | ❌ | ❌ | via review only | ✅ | ✅ | — | — | — | — |
| Trader | ✅ | ❌ | ❌ | via plan | ✅ | — | ✅ | — | — | — |
| Risk debators | ✅ | ✅ | ✅ | ✅ | — | — | via trader | ✅ | history | — |
| Portfolio Manager | ✅ | ❌ | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

#### Finding 1 — news and sentiment bypass the research phase and the decision

Only `risk_mgmt/*` and `reporting.py`/`audit.py` reference `news_report` / `sentiment_report`.
The reviewer's template says *"Use news and sentiment only when the evidence is company-specific…"*
but its inputs are: corpus listing, fundamentals report, model audit, technical report, horizon,
position, mandate, prior review. No news, no sentiment.

The PM's template contains **no occurrence of "news" or "sentiment"** at all. It receives
`case_availability(state)`, which appends "News and sentiment reports: both available" — a statement
that they exist, with none of their content. The only path by which retail sentiment or a company news
event reaches the final decision is the prose of the risk-debate transcript, compressed by three other
models.

If the design intent is "sentiment/news are tactical colour for the risk debate only", the reviewer prompt
should stop asking for them. If not, this is a wiring bug with a real effect on ratings.

#### Finding 2 — the market analyst's fundamentals input is always empty

```python
fundamental = (state.get("fundamentals_report") or "").strip()   # market_analyst.py:66
...
"Fundamental conclusion, if supplied: " + (... if fundamental else "not supplied")
```

All analysts are added as `workflow.add_edge(START, node)` and therefore run in the same superstep; state
channels only merge at the end of it. `fundamentals_report` is `""` on every fresh run. The block is dead
prompt text (it reliably prints "not supplied"), and it costs tokens in the largest prompt of the run.

#### Finding 3 — the reviewer's "evidence pack" is an index, not evidence

`evidence_pack()` returns the corpus document listing (ids, dates, page counts), not document text
([context.py:366-388](tradingagents/agents/context.py#L366)). The reviewer therefore reasons over the
fundamentals analyst's prose plus a table of contents — it cannot independently check a claim against a
filing. This is defensible (the analyst did the reading), but it means "adversarial review" checks
reasoning, not sources.

---

### 5. The determinism layer (the strongest part of the design)

The interesting architecture is not the agent graph — it is the set of deterministic gates around it.
Each gate converts a model output into a checked object or refuses it:

1. **Verified model** (`research/kernel.py`): every identity in the spec (bridges, EPS, market cap,
   EV/EBITDA) is recomputed in Python. `research/schemas.py` + `research/valuation.py` + `research/capital.py`.
2. **Packets** (`research/packets.py`): when the model is unverified, model-derived inputs are replaced by
   notices; the PM's answer is then scanned for a reconstructed P/E (`reconstruction_findings`) and
   invalidated on a second violation.
3. **Decision audit** (`research/decision_audit.py`): a directional call without a verified valuation must
   prove three independent evidence clusters, one risk leg and two primary disclosures; failure normalises
   Sell → Reduce/Hold.
4. **Output statuses** (`research/status.py` → `research/dependencies.py`): per-output statuses, so an
   unverified EPS suppresses P/E and per-share targets while EV/EBITDA stays usable.
5. **Number audit** (`research/audit.py`, 1,382 lines): only figures the agent marked `[[…]]` are checked;
   relational claims are checked against the sentence's own operands, with one LLM repair pass.
6. **Sizing and level scrubbing** (`agents/sizing.py`): exact sizes and execution levels are detected by
   regex and rewritten when no portfolio is supplied.
7. **Rating integrity** (`agents/rating.py`): unreadable decisions become `REVIEW`, never `Hold`.

This is a coherent "LLM proposes, Python disposes" architecture, and it is where most of the test mass sits.
The weakness is that the gates live partly in the graph (`prepare_final_state`) and partly in agents
(PM normalisation), so the same rule appears in two places — e.g. the unverified-model suppression is
implemented in the PM node *and* re-derived in `prepare_final_state`.

---

### 6. State contract and orchestration

- `AgentState` declares 100 fields; `final_state` carries nine more that nothing declares:
  `action_status`, `controlling_decision`, `debate_summary`, `decision_conflicts`, `decision_integrity`,
  `report_audit`, `research_rounds_used`, `risk_rounds_used`, `stage_status`.
  They are written in `trading_graph.py` and read by `reporting.py` / `report_html.py`, so the report is
  coupled to string keys the state schema does not describe.
- `trading_graph.py` is one class with 40 methods over 2,036 lines: graph compile, checkpoint lifecycle,
  model discovery/building, EV diagnostics, repair gate, number audit, status derivation, conflict
  detection, report saving and state logging. It is the single hardest file to change safely.
- `_research_package()` returns four values and smuggles eight more through `self`
  (`_ev_diagnostic`, `_output_status`, `_basis_names`, `_sensitivity_items`, `_sensitivity_gaps`,
  `_sensitivity_table`, `_fundamental_packet`, `_valuation_packet`). `create_run_state` then reads them with
  `getattr(self, ..., default)`. A run's state therefore depends on call order on the same object, and two
  graphs sharing an instance would cross-contaminate. A dataclass return value would remove the whole class
  of bug.
- `create_run_state` contains an unreachable branch: it sets `model_status` to `"VERIFIED"` or
  `"NOT_APPLICABLE"` and then tests `if state["model_status"] == "UNVERIFIED"` in the same expression
  ([trading_graph.py:906-914](tradingagents/graph/trading_graph.py#L906)).

---

### 7. Dead code to remove

Line numbers are current-file. Every item below was confirmed by scanning all 138 package modules plus
tests for references — none are reached by a decorator, `getattr`, or string dispatch (except where noted).

#### High confidence — delete

| # | Symbol | Location | Evidence |
|---|---|---|---|
| 1 | **entire module** `research/quarterly.py` (`QuarterlyPlan`, `QuarterLine`, `build`, `compare`, 131 lines) | `tradingagents/research/quarterly.py:1` | no import of `research.quarterly` anywhere, including tests; superseded by `ModelSpec.quarterly_plan` + `builder._repair_quarterly_plan` |
| 2 | `filter_decision_inputs`, `verified_packet`, `unverified_packet` | `research/packets.py:42,55,72` | used only by `packet_for_prompt` (itself unused) and `tests/test_unverified_mode.py` |
| 3 | `packet_for_prompt` | `research/packets.py:77` | zero references |
| 4 | `unverified_reason` | `research/packets.py:83` | zero references |
| 5 | `technical_packet` | `research/packets.py:138` | zero references |
| 6 | `capital.convertible_list` | `research/capital.py:157` | zero references; also makes the `Convertible` import removable |
| 7 | `repair.is_material` | `research/repair.py:176` | zero references |
| 8 | `status._material_per_share` (+ the `_PER_SHARE` regex it alone uses) | `research/status.py:31,44` | zero references; `challenge_fields` is forwarded straight to `dependencies` |
| 9 | `TradingMemoryLog.batch_update_with_outcomes` | `memory/log.py:234` | only a test reference; superseded by `update_with_outcome` used by `memory/settlement.py:157` |

Deleting #2–#5 lets `packets.py` shrink to `model_verified`, `UNVERIFIED_FUNDAMENTALS_NOTICE`,
`fundamental_packet`, `valuation_packet`, `pe_suppressed`, `reconstruction_findings` — the parts actually
imported by `agents/context.py` and `portfolio_manager._packet_block`.

#### Lower priority — test-only helpers

Either delete with their test, or mark as the public API they are:

- `research/cases.py:208 parse_case` and `:265 normalize_probabilities` — tests only. Probabilities are
  displayed unnormalised now.
- `research/periods.py:57 fiscal_year` — tests only.
- `research/capital.py:136 dilution_shares` — tests only; the dilution sensitivity computes its own count.
- `research/valuation.py:51 Valuation.upside_to_mid` — tests only.
- `graph/trading_graph.py:959 TradingAgentsGraph.research_package` — 7 test references, 0 production callers.
  Keep if it is the documented programmatic API; otherwise remove.

#### Dormant surface — decide, don't delete blindly

- `agents/tools.py:362 get_prediction_markets` — a tool no agent is offered (deliberately removed from the
  news analyst per `review/4062.T_20261102/PHASE5.md`), but still wired end to end: router category,
  `polymarket` vendor, default config key, HTML label, and two test files. This is ~250 lines of maintained
  code for an unreachable tool. Either re-expose it behind an explicit analyst or drop the tool + category.
- `ModelSpec.sector` (`research/schemas.py:670`) — nothing produces it in the LLM build path: `builder.py`
  never mentions "sector", so it stays `""` unless a spec is hand-authored. The five sector-specific entries
  in `default_config.severity_thresholds` (`bank`, `insurer`, `reit`, `software`, `commodity`) therefore only
  fire for fixtures. Add `sector` to the builder schema/prompt, or delete the sector table.
- `InvestDebateState.bull_history` / `bear_history` / `first_review_raw` — retained deliberately for old
  checkpoints and the state log (comment at `adversarial_reviewer.py:513`). `first_review_raw` is written but
  never read by any decision path; it is diagnostic only.

#### Dead prompt slots (not code, but always-null inputs)

These template fields are filled with literal `"null"` on every run and can only produce hedged prose:

- `fundamentals_analyst.py:1468-1491`: `primary_listing_or_null`, `exchange_timezone_or_null`,
  `reporting_currency_or_null`, `fiscal_year_end_or_null`, `accounting_standard_or_null`,
  `portfolio_weight_or_null`, `mandate_constraints_or_null`, `risk_tolerance_or_null`,
  `rating_vocabulary_or_null` — 9 permanently-null slots.
- `portfolio_manager.py:480-482`: `portfolio_weight_or_null`, `mandate_constraints_or_null`,
  `validated_trading_rules_or_null`.
- `market_analyst.py:70-77`: hardcoded "Exchange and timezone: not supplied", "Validated trading rules, if
  supplied: not supplied".

Either wire a source (portfolio weight and mandate are obvious candidates) or remove the slots so the model
stops spending attention on absences it cannot fix.

---

### 8. Further debt worth tracking

1. **`reporting.py` depends on undeclared state keys** (`decision_integrity`, `stage_status`,
   `controlling_decision`, `debate_summary`, `report_audit`) — see §6. Add them to `AgentState` and the
   report's coupling becomes typed.
2. **Global config mutation survives** at `TradingAgentsGraph.__init__` (`set_config(self.config)`), even
   though `AGENTS.md` tells data/agent code to read `get_config()`/`run_config()`. Two graphs in one process
   (the backtest runner is sequential today, but the API allows it) share whichever config was constructed last.
3. **Three risk debators duplicate ~70 lines each** (69% similar by diff). A single factory parameterised by
   role constant would cut ~140 lines with no behaviour change.
4. **Documentation drift.** `AGENTS.md` and `.serena/memories/graph/core.md` still describe "researchers
   (bull/bear)"; the code has one Adversarial Reviewer and the bull/bear histories are legacy fields.
5. **Two modules named `extract`** (`research/extract.py` = filing facts → spec; `dataflows/vendors/research_corpus/extract.py`
   = pdf/docx text extraction). Renaming one would remove a recurring source of confusion.
6. `get_config()` deep-copies the whole config on every call, including inside vendor and audit loops.
   Correctness wins over micro-optimisation here, but it is measurable on a 20-round run.

---

### 9. Suggested order of work

1. Decide the news/sentiment intent (§4, Finding 1). If they should reach the debate, add both reports to
   `_fill` in the reviewer and PM; if not, delete the "use news and sentiment" instruction.
2. Remove the market analyst's `fundamentals_report` block (§4, Finding 2) — 12 lines, zero behaviour change.
3. Delete the dead symbols in §7 "High confidence" (9 items, ~230 lines incl. `quarterly.py`), then run
   `pytest -q`; expect only `test_unverified_mode.py` to need rewriting.
4. Declare the nine missing state keys in `AgentState`; replace `_research_package`'s `self` side channel with
   a return dataclass.
5. Only then consider splitting `trading_graph.py`: the natural seams are `research_model.py` (load/build/
   diagnostic), `repair_gate.py`, and `finalize.py` (`prepare_final_state` + audit + statuses).

Steps 1–4 are mechanical and low-risk; step 5 is a multi-hour refactor that needs the integration tests.
