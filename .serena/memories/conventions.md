# Conventions

## Style (ruff-enforced)
- `select = ["E","W","F","I","B","UP","C4","SIM"]`, `ignore = ["E501"]`; line-length 100 but long lines
  are allowed — do not reflow code to 100 chars.
- isort `combine-as-imports = true` (keep multiple aliased names from one module in one block, e.g.
  the vendor imports in `router.py`).
- `**/__init__.py` has `F401` ignored for intentional re-exports.
- Type hints on public functions; docstrings are short, explain *why*/contract, not restate the code.

## Config & extension points
- New env override: add only to `_ENV_OVERRIDES` in `tradingagents/default_config.py`.
- OpenAI-compatible providers: register in `OPENAI_COMPATIBLE_PROVIDERS` (`llm_clients/openai_client.py`);
  picker model lists in `llm_clients/model_catalog.py`.
- New vendor method: implement in `dataflows/vendors/<name>/`, register in `router.VENDOR_METHODS`
  and `router.TOOLS_CATEGORIES`, update defaults/`.env.example` when user-configurable, add a routing test.
- `data_vendors` is an exact ordered chain — no silent fallback to unselected vendors. A comma-separated
  value opts into fallback; `tool_vendors` overrides per method; `"default"` means all available.

## Data / point-in-time
- Clamp every dated path to the run's `trade_date` via `dataflows/date_window.py`
  (`as_of`, `as_of_window`, `in_window`); withhold present-day-only vendor snapshots from historical
  runs (`withhold_*`). Never serve data later than the run date.
- Failures raise the typed hierarchy in `dataflows/errors.py` (`NoMarketDataError`,
  `VendorUnavailableError`, `VendorNotConfiguredError`). Never return empty strings or fabricate values.

## Agents / reports
- Every report-producing agent calls `get_language_instruction()`; new ones must be added to
  `REPORT_AGENTS` in `tests/test_i18n_coverage.py`.
- Analyst prompts must NOT ask for `FINAL TRANSACTION PROPOSAL` (trader-only); enforced by
  `tests/test_prompt_integrity.py` / `test_news_analyst_prompt.py`.
- Prefer the structured-output helpers in `agents/structured.py` + schemas in `agents/schemas.py`
  over free-text parsing where a schema exists.
