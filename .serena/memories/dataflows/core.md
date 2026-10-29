# dataflows — vendor routing & point-in-time

File map: `config.py` (run config + `get_config`), `router.py` (`route_to_vendor`,
`VENDOR_METHODS`, `TOOLS_CATEGORIES`, `OPTIONAL_CATEGORIES`), `date_window.py` (`as_of`,
`as_of_window`, `in_window`, `withhold_*`), `errors.py` (typed vendor errors), `symbols.py`
(symbol normalization/assets), `net.py` (HTTP/rate limits), `files.py` (cache paths),
`vendors/<vendor>/` implementations (yahoo, alpha_vantage, sec_edgar, reddit, stocktwits,
polymarket, fred).

- Vendor selection is an exact ordered chain from config `data_vendors`; `tool_vendors` overrides per
  method; `"default"` = all available; comma-separated = explicit fallback order. No hidden fallback.
- Look-ahead protection is a hard contract (see `mem:conventions`); present-day-only snapshots are
  withheld from historical runs instead of being silently used.
- Cache lives under `TRADINGAGENTS_CACHE_DIR`; results under `TRADINGAGENTS_RESULTS_DIR`; the memory log
  under `TRADINGAGENTS_MEMORY_LOG_PATH`. Tests redirect all three to a temp dir.
- `yfinance` import is allowed here only (`tests/test_layering.py`); other layers call `route_to_vendor`.
