"""Arm C step 1: build the model spec for 4062.T into this arm's cache.

No analysts run: `create_run_state` is the call that resolves the corpus and
builds the spec, and everything after it would be a paid run we do separately
on the frozen file.
"""

import json
import os
from pathlib import Path

from tradingagents.default_config import build_default_config
from tradingagents.graph.trading_graph import TradingAgentsGraph

ARM = Path(__file__).resolve().parent
os.environ["TRADINGAGENTS_REBUILD_MODEL"] = "1"

config = build_default_config()
config["results_dir"] = str(ARM / "results")
config["data_cache_dir"] = str(ARM / "cache")
config["run_purpose"] = "comparison"

graph = TradingAgentsGraph(("fundamentals",), config=config, debug=False)
state = graph.create_run_state("4062.T", "2026-11-02", "stock")

keys = (
    "research_model_path",
    "research_model_source",
    "research_model_created_at",
    "research_price",
    "spec_compatibility",
    "spec_compatibility_reason",
)
print(json.dumps({key: state.get(key) for key in keys}, indent=2, default=str))

