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
- Fidelity checks that outrank prose: `research/periods.py` derives the canonical fiscal
  label from the period's own dates, `research/shares.py` names the share basis behind the
  market cap and the forecast EPS (and warns when they diverge), and
  `research/conflicts.py` records guidance/consensus/model disagreements at 0.5%. All three
  surface as warnings or a package table; none changes a computed number.
- Sensitivities (`research/sensitivity.py`): a bridge line with a `driver` and an
  `alternative` is shocked one at a time and ranked by fair-value impact; a line without
  both is a research gap, never a proxy. The ranked table is part of the research package,
  and the reviewer must cite a `Decisive sensitivity ID` from it (one retry, then
  "not established").
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
- Debate cases: the Adversarial Reviewer appends both capped deltas
  (aggressive + bearish, `research/cases.py`); the managers get the model and the
  case table. Caps are ±25% on revenue/operating profit, ±20% on the multiple.
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
  the same check or the original text and the finding stand.
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
