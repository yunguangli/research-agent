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
  missing inputs instead of prompting.
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
- Debate cases: Bull/Bear append capped deltas (`research/cases.py`); the managers get
  the model and the case table. Caps are ±5% on revenue/operating profit, ±20% on the
  multiple.
- Reported actuals come from the vendor's annual statement (`research/vendor_facts.py`),
  not from the model: a fiscal year is used only when it ended at least 90 days before
  the run date, and a >25% disagreement is treated as a scale/currency error and refused.
  Filing facts and a supplied consensus still win over the vendor.
- The number audit (`1c_audit`) checks only figures an agent marked as the model's
  (`[[229.5]]`); `reporting.py` strips the marks. Do not reintroduce inference over
  unmarked prose: it cannot tell the model's figures from a vendor's or a computed
  ratio, and the false findings outnumbered the real ones.
- Vendor chains are per-category env overrides: `TRADINGAGENTS_DATA_VENDORS` and
  `TRADINGAGENTS_TOOL_VENDORS` (JSON). `bloomberg` is registered with no backend yet.
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
