# Commands (Windows / PowerShell)

Project path contains spaces and lives under OneDrive — always quote paths.

## Setup / install
- `pip install -e ".[dev]"` — quote the extras: PowerShell parses bare `[dev]` as a wildcard.
- `pip install ".[bedrock]"` for AWS Bedrock.
- Plain `pip install .` omits pytest/ruff.

## Test / lint
- `pytest -q` — default suite (integration excluded by addopts).
- `pytest -m integration` — live-service tests; run only when the user asks.
- Single test: `pytest tests/test_cli_commands.py::test_no_arguments_still_runs_an_analysis -q`
- `ruff check .` — exactly what CI enforces. No repo-wide `ruff format` (deferred on purpose).

## Run
- Interactive: `tradingagents` (installed) or `python -m cli.main`.
- Minimal Python example: `python main.py`.
- Automation/headless: pass `--ticker`, `--date`, `--analysts`, save/show flags and set
  `TRADINGAGENTS_*` env vars for provider/model. A no-TTY run exits naming the missing inputs
  instead of prompting.

## Serena / repo hygiene
- `serena memories check` (project root) — report stale `mem:` references. On Windows it crashes
  with `UnicodeEncodeError: charmap ... \u2713` unless wrapped: `$env:PYTHONIOENCODING='utf-8'; serena memories check`.
- `git status`, `git diff` behave normally; for file search prefer `rg` or Serena pattern search
  over `Select-String`.
