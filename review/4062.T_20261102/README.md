# 4062.T — 2026-11-02 review baseline

Frozen before any fix, so the before/after comparison has something to compare against.

## Arms

| Arm | Code | Model spec | Purpose | Authority |
|---|---|---|---|---|
| `baseline-1519` | pre-fix (nearest commit `6630404`) | `cache/models/4062.T.json`, built 11:45 | diagnostic | non-authoritative |
| `arm-a-reused` | fixed (Phases 1–2) | the same spec, byte-identical | comparison | non-authoritative |
| `arm-b-rebuilt` | fixed (Phases 1–2) | rebuilt once, frozen at 16:25 | comparison | non-authoritative |
| `arm-c-phase3` | fixed (Phases 1–3) | rebuilt once under the one-period rules, frozen at 16:50 | comparison | non-authoritative |
| `arm-d-validated` | fixed (Phases 1–4) | arm C's spec, loaded explicitly | comparison | non-authoritative |

Read `COMPARISON.md` for the arm metrics, `PHASE1.md`, `PHASE3.md` and `PHASE4.md` for what each
phase changed and the evidence behind it. `metrics.py` writes a row per arm;
`compare.py` renders `metrics.csv` as a table.

The 15:19 report was generated from an uncommitted tree; commit `6630404` was created at
15:28 with one further change (`portfolio_manager.py`: copy `decision_horizon` into the
structured decision's `time_horizon`). The exact pre-15:28 tree is not recoverable, so
`baseline-1519` is the report artifact, and `code/` records the nearest committed state.

## Contents

```
baseline-1519/
  report/            the 15:19 report tree, copied (original left in place)
  state/             full_states_log_2026-11-02.json
  spec/4062.T.json   the specification the run loaded (sha256 below)
  run/message_tool.log   the only live trace of tool calls and agent messages
  corpus/_corpus_manifest.json   Ibiden corpus manifest (4062.T)
  prefs.json         CLI selections persisted at run time
  code/              commit stat + diff since d47b0a6
```

## Recorded identity

- spec sha256 `084ebdc8e6e5a48934ab8e3a1f68d11c5e9cf3eabbbb2ab3142ebf15e75ba26f`
- corpus manifest sha256 `00dc4b6809d0bc396438bf891f606c70714afdf010dc6e5d931e89a4bbd9835a`
- state log sha256 `deb850f78ce52377222837aaaa0c0aeb63d03fc33216b472a4696bbfeede28ae`
- corpus `C:\Users\yunguang\OneDrive - Trilogy Partners Limited\YL P Drive\Ibiden`

## Run configuration (from prefs.json + state log run_settings)

- provider `openrouter`, quick and deep both `deepseek/deepseek-v4.1-flash-1017`
- analysts `market`, `fundamentals`; output language `English`; horizon `3-6 months`
- `max_debate_rounds=3`, `max_risk_discuss_rounds=3`
- date `2026-11-02`; price 8,045 (run-date close, no override recorded)
- vendors `core_stock_apis=bloomberg,yfinance`, `technical_indicators=bloomberg,yfinance`,
  `fundamental_data=bloomberg,sec_edgar,yfinance`, `news_data=yfinance`,
  `macro_data=fred`, `prediction_markets=polymarket`

## Verified against the report artifacts

- `1b_research/research_package.md` header `model 1.0.0`; the spec above is model 1.0.0.
- `3_trading/trader.md` has no `FINAL TRANSACTION PROPOSAL:` trailer, which
  `render_trader_proposal` always emits — evidence of the free-text fallback.
- `5_portfolio/decision.md` has `**Time Horizon**: not provided`, which the 15:28 change
  addresses.
