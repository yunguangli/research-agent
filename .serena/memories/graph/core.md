# graph & agents

- `graph/trading_graph.py` = public entry (`TradingAgentsGraph`), `graph/setup.py` = LangGraph wiring,
  `graph/analyst_execution.py` = per-analyst node specs (default `("market","social","news","fundamentals")`),
  `graph/conditional_logic.py` = debate/risk routing, `graph/propagation.py` = state propagation,
  `graph/checkpointer.py` = SQLite checkpointing/resume, `graph/propagation` state is `agents/state.py`.
- Agent factories: `agents/analysts/` (market, sentiment—key `social`, news, fundamentals, `turn.py`),
  `agents/researchers/` (bull/bear), `agents/managers/` (research + portfolio manager),
  `agents/risk_mgmt/` (aggressive/conservative/neutral), `agents/trader/`.
- Analyst tool surface: `agents/tools.py`; portfolio context: `portfolio.py`; post-trade screening:
  `agents/post_screen.py`; rating parsing: `agents/rating.py`; memory/reflection:
  `memory/log.py`, `memory/reflection.py`, `memory/settlement.py` (point-in-time aware).
- `backtest.py` drives dated runs; `reporting.py` + `report_html.py` render the report tree.
- Quick model = analysts/researchers/debaters/trader; deep model = research + portfolio manager.
- Analyst nodes are compiled as independent subgraphs and executed concurrently.
