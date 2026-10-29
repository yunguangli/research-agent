# cli package

Typer app in `cli/main.py` (console script `tradingagents`), orchestration in `cli/run.py`.

- `cli/prompts.py` interactive questions, `cli/selections.py` analyst/model/tier selection,
  `cli/models.py` model choice model, `cli/prefs.py` persisted user preferences,
  `cli/display.py` Rich rendering, `cli/stats_handler.py` token/cost stats,
  `cli/updates.py` version/update check.
- Headless contract: a bare run is interactive; automation must pass `--ticker`, `--date`,
  `--analysts`, save/show flags, with provider/model from `TRADINGAGENTS_*` env vars. With no TTY the
  CLI exits naming the missing inputs instead of prompting (covered by `tests/test_cli_headless.py`,
  `test_cli_no_console.py`).
- Config precedence (CLI flags > env > prefs/defaults) is asserted in
  `tests/test_cli_config_precedence.py` and `test_cli_env_skip.py`.
- Static assets ship as package data (`cli = ["static/*"]`).
- Saved analyst choices use the key `social`, displayed as `sentiment`.
