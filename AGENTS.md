# AGENTS.md

TradingAgents is a LangGraph multi-agent trading-research framework. The library is
`tradingagents/`; the interactive CLI is a separate top-level package `cli/`. Both are
packaged by `pyproject.toml`, with `tradingagents` and `cli.main:app` as the entry points.

## Setup and commands

- Python >= 3.11 (CI matrix: 3.11–3.14).
- `pip install -e ".[dev]"` for tests and lint. Plain `pip install .` omits `pytest`/`ruff`;
  use the `[bedrock]` extra for AWS Bedrock.
- `tradingagents/__init__.py` loads `.env` at import, and `DEFAULT_CONFIG` is built once
  then. To see env changes made later in a process, call `build_default_config()` or pass
  an explicit config.
- `pytest -q` — default suite. `addopts` excludes tests marked `integration`.
- `pytest -m integration` — live-service tests only. Run them only when explicitly asked.
- Single test: `pytest tests/test_cli_commands.py::test_no_arguments_still_runs_an_analysis -q`.
- `ruff check .` — exactly what CI enforces. E501 is disabled and `ruff format` adoption is
  intentionally deferred; do not mass-reformat the repository.
- CLI: `tradingagents` (installed) or `python -m cli.main` (source). A bare run is
  interactive; automation must pass `--ticker`, `--date`, `--analysts`, save/show flags,
  and set provider/model via `TRADINGAGENTS_*` variables. A no-TTY run exits naming the
  missing inputs instead of prompting. The decision horizon question is optional:
  default `3-6 months`, `TRADINGAGENTS_DECISION_HORIZON` answers it, and a run with
  no terminal takes the default rather than being blocked.
- Minimal Python example: `python main.py`.
- CI also runs a clean-install smoke: `pip install .` then `import tradingagents, cli.main`.
  Any new runtime import must be declared in `pyproject.toml`, not only in the dev extra.

## Architecture

- Run flow: `cli/main.py` / `cli/run.py` -> `TradingAgentsGraph`
  (`tradingagents/graph/trading_graph.py`) -> LangGraph wired in `graph/setup.py` ->
  agent factories in `agents/`. Reports go through `reporting.write_report_tree` and
  `TradingAgentsGraph.save_reports`.
- Quick model serves analysts, researchers, debaters, and trader; deep model serves the
  Research Manager and Portfolio Manager. Each tier can use its own provider.
  Analysts are independent subgraphs and run concurrently.
- The analyst key is `social`, but the user-visible/report name is `sentiment`
  (`reporting.analyst_names()` handles the mapping).
- Data access: `agents/tools.py` -> `dataflows/router.route_to_vendor()` ->
  `dataflows/vendors/<vendor>/`. Only `dataflows/` may import `yfinance`
  (`tests/test_layering.py` enforces this).
- `data_vendors` is an exact ordered chain — there is no silent fallback to unselected
  vendors. A comma-separated value opts into fallback; `tool_vendors` overrides the
  category per method; `"default"` means all available vendors.
- Read config through `tradingagents.dataflows.config.get_config()` in data/agent code,
  not the mutable global `DEFAULT_CONFIG`, so concurrent graphs keep their own run config.
- Add a new env override only to `_ENV_OVERRIDES` in `tradingagents/default_config.py`.
  OpenAI-compatible providers register in `OPENAI_COMPATIBLE_PROVIDERS`
  (`llm_clients/openai_client.py`); picker model lists live in `llm_clients/model_catalog.py`.
- Where run artifacts live — report folders, model specs, corpus manifest, cache, memory
  log — is mapped in `docs/WHERE_THINGS_ARE.md`. `python -m tradingagents.research paths
  --ticker <T>` prints the same paths for the current machine.

## Fundamental research model

- Opt-in: `TRADINGAGENTS_RESEARCH_DIR` enables the corpus and the model. Unset, vanilla
  behaviour is unchanged (no corpus tools, no model, no extra report sections).
- Pipeline: corpus → filing facts (Japanese parser, optional) → the LLM builds the spec →
  `research/kernel.py` validates every identity → `1b_research/research_package.md` →
  the analyst prompt and the report.
- `TRADINGAGENTS_REBUILD_MODEL=1` builds a new spec from the corpus;
  `TRADINGAGENTS_RESEARCH_MODEL` points at a spec; `TRADINGAGENTS_MODEL_PRICE` overrides
  the valuation price. A failed build writes `<TICKER>.failed.json` beside the spec.
- Repair gate: before the Adversarial Reviewer runs (inside its node entry, no new graph
  node), the Fundamentals Analyst's report is mined for explicit model defects
  (`research/repair.py`), scalar corrections are applied to a copy of the spec and
  revalidated by the kernel, and a v2 spec is written as `<TICKER>.v2.json`. The audit then
  accepts v2, classifies a v1-only figure whose field changed as **superseded** (not a
  failure), and the debate reads the v2 package plus a repair note. A structural challenge
  is recorded as unresolved; a blocking one that cannot be applied marks the model
  unavailable. `active_model_version` makes the gate idempotent.
- Price authority (`research/price_authority.py`): every controlling price carries a source
  class — `USER_EXPLICIT` > `VERIFIED_CLOSE` > `REPORT_PRICE` > `SPEC_PRICE` >
  `DERIVED_FROM_MARKET_CAP`. A repair may not replace a verified analysis-date close with an
  older report price or a value reverse-derived from market capitalisation; a same-class
  disagreement is a conflict, never a silent pick. A user-supplied price for the current
  analysis is recorded as `USER_OVERRIDE`, not treated as a conflict. Market capitalisation
  is a consistency check (`price × shares`), not a price source. `price_source`,
  `price_as_of` and `price_ref` travel on the spec and the run state.
- Publication gate: `publication_status` is derived (`prepare_final_state`) and is `BLOCKED`
  when a controlling price conflict remains, the decision was written against a different
  model version, the decision presents a repaired-away controlling figure as current, or
  the spec's `controlling_valuation_method` disagrees with the method-level `controlling`
  flags (`CONTROLLING_VALUATION_METHOD_CONFLICT`). A defect in a non-controlling method
  whose `decision_use` is `CROSS_CHECK_ONLY` is a qualification, not a blocker:
  `OK_WITH_QUALIFICATION` publishes the controlling decision and lists the defect in
  `publication_qualifications`. A blocked report publishes a header, the reasons and the
  appendix, but no rating, fair value or target; the decision is Not Rated / PROVISIONAL.
  Investor-facing sections that cite a superseded controlling figure are replaced by the
  repair note or withheld (appendix keeps the original), and the header prints the
  controlling price's source.
- Two valuation statuses (`research/assumptions.py`): `calculation_status` answers whether
  arithmetic, units, share counts and propagation reconcile; `assumption_status` classifies
  the weakest controlling input as `COMPANY_REPORTED`, `COMPANY_GUIDANCE`,
  `EXTERNAL_ESTIMATE`, `DERIVED_FROM_SOURCED_INPUTS`, `ANALYST_ASSUMPTION` or
  `UNRESOLVED`. A P/E can be `CALCULATION_STATUS=VERIFIED` with
  `ASSUMPTION_STATUS=ANALYST_ASSUMPTION`, and the publication says "Mechanically verified;
  valuation multiple is an analyst assumption." An `EXTERNAL_ESTIMATE` multiple needs source,
  date, metric, forecast period, comparability and refs, or it is downgraded to
  `UNRESOLVED`. The decision gate still keys off calculation, never assumption.
- Dominant-segment reconciliation (`research/segments.py`): when one disclosed segment
  carries at least `segment_profit_share_threshold` (default 0.60) of positive segment
  profit, the model needs that segment's revenue, operating profit and margin for every
  annual forecast year, and segment sums plus eliminations must reconcile to the group.
  Valid evidence with no forecast-year segment rows is `SEGMENT_RECONCILIATION_STATUS =
  INCOMPLETE`, `FORECAST_BUILD_TYPE = TOP_DOWN`, `BOTTOM_UP_CLAIM_ALLOWED = FALSE` and
  `OPERATING_MODEL_CONFIDENCE = DEGRADED`; it publishes with a visible qualification. The
  consolidated forecast is never called "segment-reconciled" or "bottom-up". Publication
  blocks on incomplete segments only when `segment_reconciliation_controlling` is set on
  the spec; a missing or malformed evidence block is `SEGMENT_EVIDENCE_SCHEMA_MISSING`
  and blocks unconditionally. Never fabricate the segment.
- The Fundamentals Analyst appends a machine-readable `SEGMENT_EVIDENCE` block; the node
  validates it, retries once for JSON only when absent or malformed, and persists the
  validated object in `AgentState`. The Fundamentals subgraph must return
  `segment_evidence`, `segment_evidence_source`, `segment_evidence_schema_missing` and
  `segment_evidence_retry` alongside `fundamentals_report`; reviewer entry revalidates
  from that state, never from a legacy Markdown table.
- Decision publication fields are distinct: `PROVISIONAL_DECISION` is the Portfolio
  Manager's own rating, and `PUBLISHED_DECISION` is the rating a reader may use
  (`NOT_RATED` whenever publication is blocked). The appendix may keep the PM's
  provisional rating, fair value and entry plan, labelled `INTERNAL_DIAGNOSTIC_ONLY`,
  `NOT_PUBLISHED` and `NOT_DECISION_EVIDENCE`.
- Bridge drivers (`research/issues.py`): every material line names a segment, basis,
  capacity source, start period, utilisation, price/mix, incremental cost and depreciation
  treatment; two lines sharing a revenue base, capacity block or volume increment without an
  allocation are `POTENTIAL_DOUBLE_COUNT`. A direct operating-profit shock is named as an
  operating-profit contribution, not capacity utilisation, unless utilisation is modelled.
- Trigger types (`research/triggers.py`): `FORECAST_REVISION_TRIGGER`,
  `RATING_REVIEW_TRIGGER` and `THESIS_INVALIDATION_TRIGGER` are separate. Repeating company
  guidance is a forecast revision; a quarterly threshold is thesis invalidation only when
  material, unexplained and linked to the structural thesis. A customer-advances decline is
  a warning only with a documented cancellation, deferral, renegotiation or loss-of-funding
  cause while committed capex continues. The validator reclassifies or drops events and
  never changes the rating, correction ledger or scenarios. The Research Manager's plan uses
  the same three lists; the Trader's deterioration condition is a rating-review trigger.
- A failed current-run model (`_repair_failed`) is fail-closed: every dependent operating,
  EPS, valuation, sensitivity, case and metric output becomes `NOT_TESTED` in this run; no
  VERIFIED label from an earlier model survives. Model-derived packets, cases and fair
  values are cleared, no case recomputation runs, and `prepare_final_state` forces
  `final_rating = Not Rated` with no controlling fair value. The report header prints
  `DECISION_SAFETY_CHECK=PASS`, `DECISION_PROCESS_CHECK=DEGRADED`,
  `PRODUCTION_DECISION_ALLOWED=FALSE` for that safe fallback (`research/decision_checks.py`).
- Source facts are separate from model facts (`research/facts.py`): reported results,
  company guidance, balance-sheet inputs, disclosed segments and projects remain available
  when the model fails, and every downstream prompt receives `SOURCE_FACTS` plus
  `MODEL_DERIVED_FACTS: WITHHELD`. The Fundamentals report is withheld from the main report
  and decision prompts; analyst commentary is passed as `ANALYST_JUDGMENTS` with
  `[[...]]`-marked model figures removed. The raw report, failed structured attempts and the
  reviewer's raw first answer are appendix diagnostics only.
- Cached-spec revalidation (`research/segment_evidence.py`): the Fundamentals Analyst
  appends a machine-readable `SEGMENT_EVIDENCE` block (segment, period, revenue, operating
  profit, currency, source ids, evidence status). The block is stripped from the narrative.
  At reviewer entry the current evidence audits the cached spec before any LLM challenge:
  `NOT_REQUIRED` only with `NO_SEGMENT_DISCLOSURE`, else the required status is
  `REQUIRED_AND_PRESENT`, `REQUIRED_BUT_INSUFFICIENT_DATA` or `REQUIRED_BUT_MISSING`. An
  absent block is `SEGMENT_EVIDENCE_SCHEMA_MISSING` and blocks publication; a legacy
  Markdown table is diagnostics-only and cannot support publication. The cached
  `NOT_REQUIRED` is provisional until this audit runs, and the visible limitation is
  published in the main report.
- Deterministic checks run unconditionally at reviewer entry, before the challenge
  extraction: `normalize_spec`, driver registry normalization, `collect_deterministic_issues`,
  overlap checks, then the LLM challenges. A zero-challenge answer cannot hide
  `INCOMPLETE_DRIVER` or `POTENTIAL_DOUBLE_COUNT`. Triggers, bridge arithmetic and the final
  EV/multiple/equity/per-share status are structured objects, never punctuation-parsed prose.
  `research/resolved_status.py` separates EV calculation, multiple assumption, equity bridge,
  per-share basis, per-share comparison and decision use; contradictory combinations block
  publication and are named in the header/appendix.
- Fidelity checks that outrank prose: `research/periods.py` derives the canonical fiscal
  label from the period's own dates, `research/shares.py` names the share basis behind the
  market cap and the forecast EPS (and warns when they diverge), and
  `research/conflicts.py` records guidance/consensus/model disagreements at 0.5%. All three
  surface as warnings or a package table; none changes a computed number.
- Fiscal-year labels are derived from the period's own end date, never from the company's
  label. Write a March year-end as `FY3/2027` (or `FY3/27` — the same period; keep one form
  within a report). Write a December year-end as the plain end year, `FY2027`. Write any
  other month as `FY<month>/<year>` (a February year-end is `FY2/2027`). The same rule
  applies to every fiscal-year end. Japanese companies often call the year ending
  2027-03-31 "FY2026" (the 2026年度 convention): that label refers to our `FY3/2027`, so
  check the period's end date rather than the company's label and use the canonical label in
  every table, bridge and citation. The company's own label may appear in brackets on first
  use. A label whose dates disagree is a warning that names the canonical label; it is never
  silently accepted.
- Corrections have a ledger (`model_corrections`): original, corrected, primary evidence,
  affected fields and downstream outputs, with `ACCEPTED` only for a correction the kernel
  applied, `REJECTED` when the proposed value contradicts the model, and `UNRESOLVED` for an
  ambiguous or structural challenge (scenario basis, never a silent value). The PM receives
  the accepted corrections in its prompt and records `decision_model_version`; MODEL_CHECK
  fails when that differs from the controlling model.
- Three verification checks are computed independently (`research/verification.py`) and
  published in the report header: SOURCE_CHECK (inputs and classifications against primary
  evidence), MODEL_CHECK (calculations reconcile and accepted corrections propagated) and
  DECISION_CHECK (the call against every accepted correction, unresolved defect and decisive
  sensitivity). Outcomes are PASS / PASS_WITH_QUALIFICATION / FAIL / N/A; the overall verdict
  is the worst applicable check, never a derived average, and a non-controlling failure
  qualifies rather than fails the report.
- Decision relevance is classified against the call, not a universal percentage:
  `DECISIVE` when plausible variation crosses the decision boundary (price, or the band
  for a neutral call), `MATERIAL_SECONDARY` when it consumes at least half the margin of
  safety without crossing, `MINOR` otherwise (`research/relevance.py`). Only DECISIVE fails
  DECISION_CHECK; MATERIAL_SECONDARY qualifies it; MINOR is not decision evidence.
- Material inputs carry a source class derived from the spec section, not from prose
  (`research/evidence.py`): COMPANY_ACTUAL, COMPANY_GUIDANCE, EXTERNAL_ESTIMATE,
  AGENT_FORECAST and AGENT_DERIVED. An agent forecast may be used as a forecast, scenario,
  sensitivity or variance benchmark, but never described as company guidance, a company
  commitment or verified consensus, never as mechanically derived when an assumption is
  discretionary, and never as a company-guided beat/miss, upgrade/downgrade or catalyst
  hurdle. The package and the report header state whether the benchmark is company-issued or
  internally modelled; a violation is an audit finding of kind `evidence`.
- Sensitivities (`research/sensitivity.py`): a bridge line with a `driver` and an
  `alternative` is shocked one at a time and ranked by fair-value impact; a line without
  both is a research gap, never a proxy. The ranked table is part of the research package,
  and the reviewer must cite a `Decisive sensitivity ID` from it (one retry, then
  "not established"). Two scopes are reported separately: `MODEL_NODE_SENSITIVITY_STATUS`
  describes only constructed model-node shocks, while `ECONOMIC_DRIVER_MAPPING_STATUS`
  (`COMPLETE`/`PARTIAL`) and `FAILED_DRIVER_MAPPINGS` count distinct canonical
  `economic_driver_id`s with no verified model-node mapping. The same canonical driver may
  not be both verified and propagation-failed; `filter_gaps` strips qualifiers such as
  "/ utilisation" and parenthetical suffixes before matching, then drops the stale failure.
- PM traceability: a published controlling range must match the active model band or a
  reproducible `valuation_override`; a rejected scenario cannot supply a bound. An
  untraceable range is cleared and the decision is provisional, never silently published.
  Repair conclusions replace superseded ones in the decision; superseded figures and
  conclusions are appendix-only.
- UNVERIFIED means unavailable for decision use, not available with a warning. The
  downstream packet drops the model package, cases, metrics and ranked sensitivities
  (`research/packets.py`), withholds the Fundamentals report (it was written against the
  model), and the PM returns a deterministic Not Rated / Provisional decision unless an
  audited event basis (merger, delisting, insolvency, accounting breach, suspension) is
  present. The header names the blocking challenge, its fields, the verification result
  and the publication impact.
- Output statuses are derived, not asserted (`research/dependencies.py`): each output
  declares its inputs, and a failed input suppresses only its dependents — an unverified
  EPS basis suppresses P/E and per-share targets while EV/EBITDA stays verified. The
  header prints every component plus `Overall research status: PARTIALLY VERIFIED`.
- A directional call without a verified valuation must pass `research/decision_audit.py`:
  zero suppressed figures, three independent evidence clusters (one `underlying_driver_id`
  per fact), a cash-flow/balance-sheet/event or severe-operating leg (sector thresholds in
  `default_config.severity_thresholds`), two independent primary disclosures, and stated
  horizon/confirmation/invalidation. Failing Sell normalises to Reduce or Hold and the
  decision is PROVISIONAL. `decision_basis` technical cannot publish a fundamental rating.
- A Trader that misses the schema twice is `trader_status = advisory`: its levels, size
  and target are scrubbed and the decision integrity says so.
- Computed analyst figures may be marked `[[computed: 130 = 13/10*100]]`. The audit
  evaluates only arithmetic (numbers, parentheses, + - * / and signs); a formula that
  reproduces the figure joins the known values, and one that does not is a `computed`
  finding. A correct ratio whose attribution is incomplete — an operating-profit delta
  over a revenue delta with a multi-line bridge — is an `attribution` warning, never a
  failed figure.
- Company facts live only in `tests/fixtures/<ticker>/`; production code names no plant,
  customer, project or share count. The fixtures at least cover an industrial case
  (`4062.T`) and a synthetic bank, and the bank proves that P/E and EV/EBITDA verify
  independently.
- Capital structure (`research/capital.py`): an optional ledger on the spec — issued,
  treasury, current net, per-period basic/diluted weighted averages, post-period issuance,
  buybacks, treasury cancellations, convertibles with conversion dates, splits. Current
  net shares drive the market cap; forecast EPS uses the period's weighted average (split
  history restates pre-split periods); the dilution sensitivity uses full conversion.
  An undated convertible is unresolved timing, not eligibility: the forecast EPS count is
  withheld rather than silently diluted. A stated net must equal issued − treasury, and a
  cancellation requires a treasury balance. Absent the ledger, the named `ShareBase`
  entries remain the source of truth. Every field is additive and `None` means
  "not established".
- Two valuation methods are status-independent: P/E needs forecast EPS, EV/EBITDA needs
  EBITDA (operating profit + D&A), net debt and `ev_ebitda_multiple`. EV is
  `multiple x EBITDA`; equity is `EV - net debt` once. An unverified EPS basis removes
  EPS, P/E, the P/E case table and the PM's controlling band from the decision inputs,
  while EV/EBITDA stays usable. The per-share EV target uses `equity_value_share_basis`
  and is the only EV output a missing share basis suppresses.
- Net debt is derived, never asserted: the spec carries `NetDebt` with gross debt, cash
  and equivalents, lease liabilities plus treatment, minority interest plus treatment,
  and source refs. `components_verified()` requires gross debt and cash; a legacy single
  figure loads as `reported_net_debt` for display but cannot feed a verified EV/EBITDA.
  A missing component is never defaulted to zero — the builder refuses a bare number and
  EV/EBITDA becomes UNAVAILABLE on its own, leaving P/E untouched. The EV/EBITDA multiple
  is `analyst_assumption` with a rationale and refs, never presented as company-reported.
- EV input readiness is classified, not guessed (`research/input_probe.py`): a
  deterministic corpus probe finds numbered cash/debt/depreciation lines independently
  of the builder, using a table-aware extractor (`research/balance_sheet.py`) that maps
  Japanese and English balance-sheet aliases, preserves column, unit, page and document,
  and excludes receivables, repayments and narrative conversion notes.
  `PRESENT_NOT_EXTRACTED_BY_BUILDER` is a corpus-extraction failure,
  `ABSENT_IN_CORPUS` is genuine unavailability, and `EXTRACTED_NOT_RECONCILED` is model
  insufficiency (unstated date, unit, scope, lease treatment, or a stated net that
  disagrees with its components). The extracted rows are injected into the builder
  prompt; only EV/EBITDA is affected.
- Share basis quality: `shares.diluted_kind()` labels the forecast denominator
  `weighted_average` or `conservative_full_conversion` (basic plus every convertible).
  The latter keeps P/E and the band but forces `decision_status = PROVISIONAL`; the
  ledger's per-period diluted average is weighted by conversion date, while full
  conversion stays a separate dilution sensitivity.
- Technical evidence is structured (`research/technical.py`): the Market Analyst appends
  a `TECHNICAL_EVIDENCE:` JSON block of items with stable IDs
  (`tech_close_below_50sma`, `tech_macd_negative`, …), two compared values, a direction,
  a timeframe and source refs. The block is stripped from the report; an item verifies
  only when its own numbers support its direction. The PM may cite only verified IDs,
  and the audit drops a technical claim the analyst did not verify. A mixed basis whose
  only defect is the technical leg falls back to `fundamental` plus an advisory technical
  overlay instead of failing.
- `decision_status` is derived, never forced: FINAL requires a production run purpose,
  a verified valuation, a passed decision audit, a structured PM, no blocking issue and
  a weighted-average EPS basis. A diagnostic run, a conservative full-conversion basis
  or an advisory PM stays PROVISIONAL.
- The decision stage receives purpose-specific packets (`research/packets.py`): a
  fundamental packet (revenue/OP/net profit, no price or share count), a valuation
  packet (only verified methods) and a technical packet (price/timing). When P/E is
  suppressed, a deterministic check rejects any PM answer that states a P/E, market cap,
  EPS-derived value, target or fair value; one retry is allowed, and a second violation
  invalidates the controlling decision (Not Rated / PROVISIONAL) instead of scrubbing a
  sentence. Net profit stays available to the fundamental packet.
- The Trader schema reuses the shared action enums (`new_investor_action`:
  Initiate/Wait/Avoid; `holder_action`: Add/Hold/Review/Reduce/Exit) plus
  `entry_condition`, `deterioration_condition`, `evidence_ids`, `confidence`. It is
  tactical and non-controlling: never execution prices, size or the fundamental rating.
  Two structured attempts, then `trader_status = advisory` and an integrity reason.
  The legacy BUY/HOLD/SELL line is derived from the action pair.
- Rating and holder action must agree: a partial reduction is canonical Underweight
  with holder action Reduce; Sell is reserved for a full exit. The PM sets
  `downgrade_applicability` (APPLICABLE / ALREADY_AT_LOWEST_RATING / NOT_ASSESSED) and
  `evidence_to_downgrade`, which is null at the lowest rating and required for a Reduce.
- Controlling valuation source IDs carry the method and model version
  (`pe_model_band_v1`); a generic `model_band` is rejected.
- Sensitivities (`research/sensitivity.py`): a shock moves its period and every later
  period built on it (bridges rebased, forecast rows and net profit carried, revenue
  shocks landing as an explicit pass-through line), so a multi-year model yields a
  ranked table instead of silent zeros. Each item records `valuation_method` and
  `verification_status`; a shock that cannot propagate is a named failure
  (`SENSITIVITY_PROPAGATION_FAILED`, `RESEARCH_GAP_NO_PROPAGATION`) rather than a
  zero. IDs are stable (`<metric>_<target period>_<assumption line>` with `bridge_id`
  and `model_field_id`); legacy driver slugs are aliases that resolve with a warning,
  never a second item. The top three are published and the reviewer must cite one ID.
- Debate cases: the Adversarial Reviewer appends both capped deltas
  (aggressive + bearish, `research/cases.py`); the managers get the model and the
  case table. Caps are ±25% on revenue/operating profit, ±20% on the multiple. A case
  is a delta chain — operating profit → pretax → tax → net income → EPS on the period's
  diluted count → value — and a case whose figures do not reconcile is rejected. Tax
  hierarchy: a stated rate (verified), else the base year's implied rate (INDICATIVE),
  else the case stops at operating profit. A scenario multiple records its source id
  (`pe_model_band_v1` or `analyst_case_multiple_v1`) and whether it was shocked.
- Publication: `complete_report.md` is a concise investor report (executive summary,
  fundamental summary, forecast and verified valuation, debate summary, technical
  summary, PM decision, catalysts/risks), capped at 5,000 words with a visible notice
  when a section is truncated. The full analyst reports, full debate transcript, model
  diagnostics, audit findings, retry logs, source conflicts and rejected scenarios are
  in `appendix.md`. Do not concatenate raw reports into the main report.
- Reported actuals come from the vendor's annual statement (`research/vendor_facts.py`),
  not from the model: a fiscal year is used only when it ended at least 90 days before
  the run date, and a >25% disagreement is treated as a scale/currency error and refused.
  Filing facts and a supplied consensus still win over the vendor.
- The number audit (`1c_audit`) checks only figures an agent marked as the model's
  (`[[229.5]]`); `reporting.py` strips the marks. Do not reintroduce inference over
  unmarked prose: it cannot tell the model's figures from a vendor's or a computed
  ratio, and the false findings outnumbered the real ones. The relational claim check
  is deliberately different, and allowed: it verifies a comparative claim only against
  the operands the same sentence prints, or an explicit model period label plus a
  stated metric — never a number's provenance. A contradicted claim ("38.6 already
  exceeds 63.7") enters one LLM repair pass before publication; the rewrite must pass
  the same check or the original text and the finding stand. That repair call is bounded
  (`audit_repair_timeout`, default 180s): on a timeout the original text and its finding
  stand and the report still saves. Save failures are written to `run.log`, not only the
  console.
- `Line.from_dict` keeps a qualitative `price_mix`, `incremental_cost` or `utilisation`
  answer as the corresponding `*_assumption` string, and reads `"100%"` as 1.0, so a
  model that writes prose in a legacy numeric field does not fail the whole build.
- Two more spec rules enter the repair round: a forecast that equals guidance on every
  line needs a warnings entry naming the period that explains the coincidence, and a
  quarterly plan whose company published no H1 guidance row is rebuilt from reported
  Q1+Q2 (with a warning) instead of being dropped — labels that disagree about the
  year still drop it.
- Vendor chains are per-category env overrides: `TRADINGAGENTS_DATA_VENDORS` and
  `TRADINGAGENTS_TOOL_VENDORS` (JSON); a comma inside a chain is the ordered fallback
  (e.g. `"bloomberg,yfinance"`). `bloomberg` has a working `blpapi` desktop backend for
  prices, indicators, the company overview and the income statement (quarterly plus
  trailing-4 annual, filing-lag gated); balance/cashflow statements are not implemented
  yet. Vendor statement figures are fallback evidence — a figure stated in a research
  document wins (`vendor_facts`, and the fundamentals prompt says so).
  The source policy — Bloomberg for numbers, the web for news, Yahoo as fallback — is
  `docs/DATA_SOURCES.md`.
- Workflow detail: `ANALYST_WORKFLOWS.md` §3, §6, §9. File locations:
  `docs/WHERE_THINGS_ARE.md`.

## Point-in-time contract

- Every dated path must clamp to the run's `trade_date` using
  `dataflows/date_window.py` (`as_of`, `as_of_window`, `in_window`). Never serve data
  later than the run date, and withhold present-day-only vendor snapshots from historical
  runs with the `withhold_*` helpers.
- Vendor failures must raise the typed error hierarchy in `dataflows/errors.py`
  (`NoMarketDataError`, `VendorUnavailableError`, `VendorNotConfiguredError`); do not
  return empty strings or fabricate values. Look-ahead tests cover fundamentals, news,
  social, and the memory log.
- Adding a vendor/method: implement under `dataflows/vendors/<name>/`, register the method
  in `router.VENDOR_METHODS` and `router.TOOLS_CATEGORIES`, update defaults/`.env.example`
  when user-configurable, and add a routing test.

## Testing gotchas

- `tests/conftest.py` blocks all network access (including `curl_cffi`) and blanks every
  `TRADINGAGENTS_*` variable. A test that needs live data must be marked
  `@pytest.mark.integration`; do not depend on real keys, the user's home, or their cache.
- The suite isolates results, cache, memory log, yfinance timezone cache, and CLI prefs
  from the user's home. Preserve that when adding tests.
- `dataflows.config.set_config()` merges into a process-global; reset it after a test that
  mutates config, as `conftest.py` does.
- Every report-producing agent must call `get_language_instruction()`; add new ones to
  `REPORT_AGENTS` in `tests/test_i18n_coverage.py`. Analyst prompts must not ask for
  `FINAL TRANSACTION PROPOSAL`.
- One CI job runs with `TZ=America/New_York`; keep date/time handling UTC-aware and never
  assume local time equals UTC.

## Release

- Version has one source: `__version__` in `tradingagents/__init__.py`; `pyproject.toml`
  reads it dynamically. A release tag must be `v<version>` (the release workflow checks).
- Update `CHANGELOG.md` (Keep a Changelog; note breaking changes under "Upgrading from").
