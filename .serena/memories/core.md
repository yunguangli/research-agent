# TradingAgents — core map

LangGraph multi-agent equity/crypto research framework. Two packaged top-level packages:
- `tradingagents/` — library (graph, agents, dataflows, llm_clients, memory, reporting)
- `cli/` — Typer + Rich interactive CLI, console script `tradingagents` -> `cli.main:app`

**Authoritative rules live in `AGENTS.md` at repo root** (setup, architecture, point-in-time contract,
testing gotchas, release). Read it first for non-trivial work; the memories below add detail and
discovery paths, and must not be duplicated into it.

## Source map
- Run flow: `cli/main.py` / `cli/run.py` -> `tradingagents/graph/trading_graph.py` (`TradingAgentsGraph`)
  -> LangGraph wired in `graph/setup.py` -> agent factories under `agents/` (analysts, researchers,
  risk_mgmt, trader, managers). Report output via `reporting.write_report_tree` + `save_reports`.
- Data access: `agents/tools.py` -> `dataflows/router.route_to_vendor()` -> `dataflows/vendors/<vendor>/`.
  Details in `mem:dataflows/core`.
- Graph/agent wiring and node map: `mem:graph/core`. CLI structure and user config precedence: `mem:cli/core`.
- Model/provider plumbing: `llm_clients/` (factory, per-provider clients, `model_catalog.py`, `capabilities.py`).

## Cross-cutting invariants
- Analyst **key** is `social`, user-visible/report name is **`sentiment`** —
  `reporting.analyst_names()` maps it. Never rename the key in configs/state the user saved.
- Two model tiers: *quick* serves analysts, researchers, debaters, trader; *deep* serves Research
  Manager and Portfolio Manager. Each tier may use its own provider.
- Analysts are independent subgraphs and run concurrently (`graph/analyst_execution.py`).
- Only `dataflows/` may import `yfinance` (enforced by `tests/test_layering.py`).
- Read config in data/agent code through `dataflows.config.get_config()`, not the mutable global
  `DEFAULT_CONFIG`, so concurrent graphs keep their own run config.
- `tradingagents/__init__.py` loads `.env` at import and builds `DEFAULT_CONFIG` once; use
  `build_default_config()` to see later env changes or pass an explicit config.
- Version has a single source: `__version__` in `tradingagents/__init__.py` (pyproject reads it dynamically).
