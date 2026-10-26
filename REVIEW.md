# REVIEW.md

**Project:** TradingAgents  
**Version reviewed:** 0.7.0  
**Commit reviewed:** `e8f5503` (upstream `main`)  
**Review date:** 2026-10-25  
**Audience:** product owners, portfolio managers, analysts, and engineers  
**Status:** assessment only — no application code was changed

> **One-paragraph summary for PMs**
>
> TradingAgents is a strong research-analysis engine. Given one stock and one date, it runs several specialist AI analysts, has them debate, applies risk review, and produces a written recommendation with a full report. It is careful about not using future information, it can remember past decisions, and it can be backtested.
>
> It is **not** a monitoring platform. It does not watch a list of stocks, detect an intraday price move, search mailboxes, send email, run on a schedule, or provide a web page that PMs can browse. It also does not ship with Bloomberg, web scraping, or PDF export.
>
> Your target workflow — watch a list, detect a meaningful move, gather relevant news/email, email a summary, then produce a deeper report for PMs — is **feasible**, but it requires a new service layer around TradingAgents. The analysis engine and report generation are reusable. The control tower, evidence desk, mailroom, report library, and audit layer must be built.
>
> The safest path is to keep the original TradingAgents behavior intact as a “vanilla” mode, and add firm-specific capabilities as optional providers and plugins.

**How to read this document**

- **PMs / business readers:** sections 1–4, 7, 9, 10, 12, and 13. These explain what exists, whether it fits, the risks, the recommended direction, and why a future upstream update should be manageable.
- **Engineers:** sections 5–6, 8, 11, and the appendices. These cover architecture, extension points, data sources, compatibility, and exact commands.

---

## 1. Purpose of this review

This document answers four questions:

1. What does TradingAgents currently do well?
2. How is it built, and where can it be extended safely?
3. Does it fit the firm’s event-driven PM workflow?
4. What must be built, and what are the main risks?

Companion documents:

- `PLAN.md` describes the target architecture, phased roadmap, acceptance criteria, and deployment model.
- `ANALYST_WORKFLOWS.md` explains how the four analysts, the bull/bear research team, the Research Manager, and the Trader currently work — their tools, data sources, prompts, algorithms, outputs, and limitations.

### Method and limitations

- Reviewed `README.md`, `pyproject.toml`, CI workflows, configuration, source code, and tests.
- Used Serena’s code-intelligence tools for symbol overviews, references, and source inspection.
- Did not run the full test suite in this environment because the active virtual environment does not have `pytest` installed. Findings are based on static inspection and on the test expectations written in the repository.
- No network calls, Bloomberg calls, Outlook calls, or mailbox access were performed during this review.
- The review itself did not modify application files; implementation work starts separately, in the wrapper layer described in `PLAN.md`.

---

## 2. Plain-language glossary

| Term | What it means here |
|---|---|
| **Agent** | An AI worker with a narrow job: for example, study price charts, read news, or judge risk. |
| **LLM** | Large language model — the AI model that writes and reasons. |
| **LangGraph** | The workflow engine that connects the agents and controls which one runs next. |
| **Graph** | The whole workflow: analysts → debate → trader → risk → portfolio manager. |
| **Vendor** | An external data source: Yahoo Finance, Alpha Vantage, SEC EDGAR, FRED, Bloomberg, etc. |
| **Point-in-time** | The rule that an analysis dated in the past may only use information that was public on that date. |
| **Signal** | A computed score or indicator, for example “fundamental demand is strong, technical flow is weak.” |
| **Plugin** | A self-contained module that adds a new signal, model, data source, or output without changing the core workflow. |
| **Scenario** | A “what if” version of the analysis: different horizon, macro regime, demand assumption, or weights. |
| **Backtest** | Replaying historical dates to see how the analysis would have performed. |
| **Memory log** | A Markdown file recording past decisions and later reflections on their outcomes. |
| **Checkpoint** | A saved mid-run state that lets an interrupted analysis resume. |
| **RAG** | Retrieval-augmented generation — searching an internal document store and giving the relevant passages to the LLM. The repo does not currently include this. |
| **BLPAPI** | Bloomberg’s desktop/terminal API, typically used from a Windows machine with a Bloomberg session. |
| **B-PIPE** | Bloomberg’s server-side data feed, typically used by enterprise systems. |
| **Data License** | Bloomberg’s bulk/reference/historical data product, typically batch-oriented. |
| **COM / pywin32** | The Windows mechanism used here to drive Outlook: searching mail and sending messages. |
| **Provider** | A firm-level adapter that supplies quotes, news, fundamentals, or flows in a standard shape. |
| **Vanilla mode** | The original TradingAgents behavior, with no firm extensions enabled. |

---

## 3. What TradingAgents is designed to do

In plain terms, TradingAgents is a **research factory for one stock on one date**.

A normal run looks like this:

1. The user chooses a ticker, an analysis date, which analysts to use, an LLM provider, and a research depth.
2. The system resolves the company’s identity so the agents do not confuse it with another company.
3. The selected analysts work in parallel:
   - **Market analyst** studies prices, volume, and technical indicators.
   - **Sentiment analyst** reads news, StockTwits, and Reddit.
   - **News analyst** reads company and global news, macro data, and prediction markets.
   - **Fundamentals analyst** reads financial statements, company fundamentals, and insider transactions.
4. The analysts file their reports.
5. A **bull researcher** and a **bear researcher** debate the investment case.
6. A **research manager** turns the debate into an investment plan and a five-tier rating.
7. A **trader** proposes an action, entry, stop, and sizing.
8. Three risk analysts — aggressive, neutral, and conservative — debate the risk.
9. A **portfolio manager** makes the final call: Buy, Overweight, Hold, Underweight, or Sell.
10. The system writes:
    - a decision memory entry,
    - a JSON state log,
    - and, if the caller asks for it, a report folder with Markdown sections and a self-contained HTML page.

This is a **pull-based, one-run-at-a-time** design. It is not a daemon, a watchlist monitor, or an alerting system.

### High-level flow

```text
User / CLI / Python
        │
        ▼
TradingAgentsGraph.propagate(ticker, date, portfolio=...)
        │
        ▼
LangGraph workflow
  ├─ analysts run in parallel
  ├─ bull vs bear debate
  ├─ research manager
  ├─ trader
  ├─ risk debate
  └─ portfolio manager
        │
        ├─ JSON state log
        ├─ memory log entry
        └─ optional report tree (Markdown + HTML)
```

---

## 4. Capability inventory

| Capability | Status | Plain-language assessment |
|---|---|---|
| Multi-agent analysis | **Strong** | Four specialist analysts, debate, risk review, and final PM-style decision. |
| Point-in-time discipline | **Strong** | Historical runs are deliberately prevented from using later data in many paths. |
| Data-provider abstraction | **Good** | Vendor routing is explicit, typed, and testable; but it is method-based and not a full market-data platform. |
| Data source breadth | **Partial** | Yahoo, Alpha Vantage, SEC EDGAR, FRED, Polymarket, StockTwits, Reddit. No Bloomberg, no email inbox, no general web search. |
| Intraday / real-time data | **Missing** | Price and indicator paths are daily. Not suitable for “stock moved 5% intraday” detection by itself. |
| LLM provider breadth | **Strong** | OpenAI, Anthropic, Google, xAI, DeepSeek, Qwen, GLM, MiniMax, OpenRouter, Mistral, Kimi, Groq, NVIDIA, Ollama, Azure, Bedrock, ChatGPT plan, and generic OpenAI-compatible endpoints. |
| Report generation | **Strong** | Per-section Markdown, `complete_report.md`, and a self-contained `complete_report.html`. |
| PDF / Excel export | **Missing** | No PDF or spreadsheet exporter. |
| Memory and reflection | **Good** | Past decisions and realised outcomes are stored and fed into later runs. File-based, one decision per ticker/date. |
| Checkpoint/resume | **Good** | Optional per-ticker SQLite checkpoints. |
| Backtesting | **Good but narrow** | Ticker/date grid scoring against a benchmark; not an intraday event replay. |
| Portfolio context | **Strong** | Cash and positions can be passed to the decision agents. |
| CLI | **Good** | Interactive and headless flags, portfolio file, checkpoint, backtest, login/logout. |
| Scheduling / queue | **Missing** | No scheduler, worker queue, or daemon. |
| Email ingestion | **Missing** | No mailbox reader. |
| Email sending | **Missing** | No mail sender. `pywin32` + Outlook would be a new adapter. |
| Web API / PM portal | **Missing** | No web server or report catalogue. |
| Authentication / RBAC | **Missing** | No user accounts, roles, or access control. |
| Audit / observability | **Partial** | Logs and report metadata exist; no metrics, tracing, immutable event store, or full source archive. |
| Multi-user / multi-tenant | **Missing** | Designed around a local user and local files. |
| Broker / OMS execution | **Missing** | The system recommends; it does not place orders. |

> **PM takeaway:** the “brain” exists and is reasonably well built. The “eyes, ears, hands, and filing cabinet” around it do not.

---

## 5. Technical architecture

This section is for engineers, but the intent is explained in plain language.

### 5.1 Entry points

| Entry point | Purpose |
|---|---|
| `tradingagents` console script (`cli.main:app`) | Normal interactive CLI. |
| `python -m cli.main` | Same CLI from source. |
| `python main.py` | Minimal Python example. |
| `TradingAgentsGraph` | Programmatic API for a single run. |
| `run_backtest()` | Historical grid evaluation. |
| `run_analysis()` in `cli/run.py` | Interactive/live-display run path. This is UI-coupled and not the right entry point for a background service. |

### 5.2 Main modules

```text
tradingagents/
  agents/          agent factories and shared state
  dataflows/       data tools, vendor router, point-in-time rules
  graph/           LangGraph construction, execution, checkpoints
  llm_clients/     provider clients, model catalogue, capabilities
  memory/          decision log, settlement, reflection
  backtest.py      historical evaluation
  portfolio.py     holdings/cash context
  reporting.py     report-tree writer
  report_html.py   self-contained HTML report
  default_config.py
cli/               Typer CLI, prompts, display, preferences
tests/             offline unit/integration tests
```

### 5.3 How a run is wired

- `TradingAgentsGraph.__init__` creates the two model tiers, memory log, conditional logic, and graph.
- `propagate()` validates the date, opens the run-scoped config, and calls `_run_graph()`.
- `_run_graph()` builds the initial state, invokes or streams the graph, records the decision, and clears any checkpoint on success.
- `record_decision()` writes the JSON state log and the memory-log entry.
- `save_reports()` is a separate call; a programmatic run does **not** automatically write the report tree.

### 5.4 Agent roles

| Agent | What it contributes |
|---|---|
| Market Analyst | Price/volume, technical indicators, verified market snapshot. |
| Sentiment Analyst | News headlines, StockTwits, Reddit, optional Jev screening. |
| News Analyst | Company news, global/macro news, FRED macro data, Polymarket. |
| Fundamentals Analyst | Company overview, statements, insider transactions. |
| Bull / Bear Researchers | Structured debate of the investment case. |
| Research Manager | Investment plan and five-tier recommendation. |
| Trader | Buy/Hold/Sell proposal, entry, stop, position sizing. |
| Aggressive / Neutral / Conservative Risk Analysts | Risk debate. |
| Portfolio Manager | Final five-tier rating and decision text. |
| Memory Log step | Settles past decisions and loads lessons. |

Important: selecting fewer analysts removes only the analyst nodes. The research debate, trader, risk debate, and portfolio manager still run. There is no “summary-only” or “cheap triage” mode in the original graph.

### 5.5 Data access and vendor routing

The data flow is:

```text
Agent tool (agents/tools.py)
        │
        ▼
dataflows/router.route_to_vendor(method, ...)
        │
        ▼
dataflows/vendors/<vendor>/
```

Key properties:

- The data layer is the only place that should import vendor libraries such as `yfinance`; a test enforces this.
- The configured `data_vendors` chain is **exact**. There is no silent fallback to vendors the user did not choose. A comma-separated list opts into ordered fallback.
- `tool_vendors` overrides the category for one method.
- `"default"` means “all available vendors for this method.”
- Vendor failures raise typed errors:
  - `NoMarketDataError` — the vendor answered but had no usable data.
  - `VendorUnavailableError` — throttled or failed request; try the next vendor.
  - `VendorNotConfiguredError` — missing key/config.
- Optional enrichment categories (`macro_data`, `prediction_markets`) degrade to a `DATA_UNAVAILABLE` sentinel instead of aborting the run.
- Core categories may raise the first real error if no vendor can serve the request.

### 5.6 Current default vendor chains

```python
"data_vendors": {
    "core_stock_apis": "yfinance",
    "technical_indicators": "yfinance",
    "fundamental_data": "sec_edgar,yfinance",
    "news_data": "yfinance",
    "macro_data": "fred",
    "prediction_markets": "polymarket",
},
"tool_vendors": {},
```

Important detail: `get_fundamentals` is supported by only **yfinance** and **Alpha Vantage**. SEC EDGAR supports balance sheet, cash flow, and income statement, but not the company overview. So the default `sec_edgar,yfinance` category chain still leaves only yfinance for `get_fundamentals`.

### 5.7 Configuration and environment

- `tradingagents/__init__.py` loads `.env` at import.
- `DEFAULT_CONFIG` is built once at import time, with `TRADINGAGENTS_*` overrides applied.
- The mapping from environment variable to config key is centralized in `_ENV_OVERRIDES` in `default_config.py`.
- In v0.7.0 there is **no** environment override for `data_vendors` or `tool_vendors`, and the CLI has no vendor-selection flag. Changing vendors currently requires code/config changes.
- `dataflows.config.get_config()` returns the run-scoped config; library code should use it rather than the mutable global `DEFAULT_CONFIG`.

### 5.8 Persistence

| Artifact | Location / format | Notes |
|---|---|---|
| Report tree | `results_dir/reports/<ticker>_<timestamp>/` | Per-section Markdown, `complete_report.md`, `complete_report.html`. |
| State log | `results_dir/<ticker>/TradingAgentsStrategy_logs/full_states_log_<date>.json` | One file per ticker/date; overwritten on a later run for the same date. |
| Memory log | `~/.tradingagents/memory/trading_memory.md` by default | Append-only Markdown with decisions and reflections. |
| Checkpoints | `~/.tradingagents/cache/checkpoints/<TICKER>.db` | Optional SQLite LangGraph checkpoints. |
| CLI preferences | User home | Last selections; tests isolate this. |
| Vendor cache | `data_cache_dir` | OHLCV and other vendor caches; stale data is rejected. |

### 5.9 LLM providers and tiers

- Quick model serves analysts, researchers, debaters, and trader.
- Deep model serves the Research Manager and Portfolio Manager.
- Each tier can use a different provider and endpoint.
- OpenAI-compatible providers are registered in one place; native Anthropic/Google/Azure/Bedrock clients are separate.
- The model catalogue powers the CLI picker; custom model IDs are accepted.

### 5.10 Testing and CI

- Test suite is deliberately offline: sockets and `curl_cffi` are blocked, `TRADINGAGENTS_*` is blanked, and user files are isolated.
- Tests that need live services are marked `integration` and excluded by default.
- CI runs Python 3.11–3.14, one job in `America/New_York`, strict `ruff check`, and a clean-install smoke.
- The project has a large regression suite around data look-ahead, vendor routing, date boundaries, reports, and prompt integrity.

### 5.11 Architecture invariants worth preserving

1. **No look-ahead.** Dated tools clamp to the run date; live-only vendor snapshots are withheld for historical runs.
2. **No silent vendor substitution.** The configured chain is the chain.
3. **Vendor isolation.** Only the data layer imports vendor libraries.
4. **Offline tests.** Unit tests must not reach the network or the user’s home.
5. **Run-scoped config.** Concurrent graphs must not share mutable global config.
6. **Typed data errors.** Do not return empty strings or fabricated values.
7. **One decision per ticker/date** in the memory log.
8. **Vanilla mode must remain usable.** Firm extensions should be opt-in.

---

## 6. Important facts that are easy to miss

These are the details most likely to surprise a new team.

### 6.1 The graph always runs the full decision chain

Choosing only the `news` analyst does not produce a cheap news-only summary. The bull/bear debate, research manager, trader, risk debate, and portfolio manager still run. For alert triage you need a separate lightweight summarizer or a different graph.

### 6.2 `propagate()` does not save the report tree

It records the state log and memory entry. If you want the Markdown/HTML report, call `save_reports()` or use the CLI. A background service must call it explicitly.

### 6.3 The internal analyst key is `social`, but the report label is `sentiment`

Any new code that displays analyst names must handle this mapping (`reporting.analyst_names()` does).

### 6.4 The system is date-based, not time-based

`trade_date` is `YYYY-MM-DD`. Data tools are daily. There is no intraday timestamp in the graph. This matters for a “stock moved 5% at 10:30” workflow.

### 6.5 One decision per ticker per date

`TradingMemoryLog.store_decision()` refuses a second entry for the same `(trade_date, ticker)`, pending or settled. The state log is also one file per ticker/date and is overwritten. Multiple intraday alerts for the same stock would collide unless you add an event identity or keep intraday history elsewhere.

### 6.6 Yahoo is still hardwired in important places

Even if Bloomberg becomes the primary price vendor:

- Company identity is resolved through Yahoo’s company profile.
- Memory settlement uses Yahoo daily closes to compute realised returns and alpha.
- Benchmark/backtest history is Yahoo-based unless replaced.

These need provider abstraction for a Bloomberg-first deployment.

### 6.7 New config keys can invalidate checkpoints

`_run_signature()` hashes nearly the entire config. Adding firm defaults to `DEFAULT_CONFIG` can change checkpoint thread IDs even when the feature is off. Keep firm settings out of the default config, or exclude them when disabled.

### 6.8 The CLI is not a service API

`cli/run.run_analysis()` is tied to Rich live display and interactive prompts. For a background monitor, use `TradingAgentsGraph` directly and call `save_reports()`.

### 6.9 Reports are not archived source evidence

The report captures the analysis and some run metadata, but not the raw news text, email bodies, or a complete source manifest. For compliance and replay, an immutable evidence store is needed.

### 6.10 `.env` cannot change vendor routing today

It can hold API keys, but `data_vendors` / `tool_vendors` are nested dicts with no env override. This is a usability gap for a firm deployment.

---

## 7. Fit to the firm’s event-driven workflow

### 7.1 The proposed workflow, in plain language

1. Keep a list of stocks to watch.
2. Receive prices from Bloomberg or another feed.
3. If a stock moves more than X% from its previous close, raise an event.
4. Gather relevant information: news, web sources, email, filings, internal notes.
5. Produce a short, source-backed summary.
6. Email the summary to the relevant PMs and analysts.
7. Run a deeper multi-agent analysis in the background.
8. Attach the report and/or provide a link.
9. Let PMs pull and read the report when ready.

### 7.2 What already exists

| Need | Existing support | Assessment |
|---|---|---|
| Deep multi-agent analysis | `TradingAgentsGraph` | Strong; reuse. |
| Technical / fundamental / news / sentiment analysts | Existing agents | Strong base; extend data sources. |
| Debate and risk review | Existing graph | Strong; preserve. |
| Portfolio-aware decision | `PortfolioContext` | Good. |
| Written report | Markdown + HTML | Strong. |
| Memory of past decisions | Memory log + reflection | Useful, but file-based and one per ticker/date. |
| Backtest | `run_backtest` | Useful for daily decisions, not intraday events. |
| Headless execution | CLI flags + Python API | Good, but no scheduler. |
| Multi-provider LLM | `llm_clients` | Strong. |

### 7.3 What must be built

| Need | Current state | New component |
|---|---|---|
| Watchlist + polling/subscription | Missing | Monitor service. |
| Intraday quote and previous close | Missing | QuoteProvider + Bloomberg adapter. |
| Trigger threshold and cooldown | Missing | Trigger engine + event store. |
| News search across web | Partial (vendor news only) | Web/RSS news provider + extractors. |
| Email search | Missing | Outlook gateway (pywin32) or Graph adapter. |
| Source normalization and dedup | Partial | Evidence pipeline and source registry. |
| Fast triage summary | Missing | Lightweight summarizer, separate from full graph. |
| Email to PMs | Missing | Notification service / Outlook sender. |
| Report attachment | Markdown/HTML only | PDF exporter or HTML attachment path. |
| PM “pull” experience | Files only | Report index + portal or shared drive. |
| Audit trail | Partial | Immutable event/report/source store. |
| Access control | Missing | SSO/RBAC and mailbox permissions. |
| Scheduling / queue | Missing | Windows Task Scheduler for pilot; queue for scale. |
| Observability | Logs only | Metrics, health checks, alerts for failures. |

### 7.4 Verdict

**Feasible — but as a new service layer around the existing engine, not as a switch inside it.**

A good analogy:

- TradingAgents is the **analyst team and report writer**.
- You still need the **watchtower** (monitor), **research desk** (evidence gathering), **mailroom** (Outlook), **library** (report index), and **quality office** (audit).

### 7.5 Recommended shape: two lanes

```text
Market feed / Bloomberg
        │
        ▼
Watchtower: watchlist + threshold + cooldown + event store
        │
        ├─► Triage lane
        │     gather news/web/email/files
        │     quick LLM summary
        │     email PMs immediately
        │
        └─► Deep lane
              TradingAgentsGraph
              selected analysts, reduced rounds
              save report (Markdown + HTML/PDF)
              email PMs with attachment/link
```

This avoids the biggest cost and latency mistake: running the full multi-agent graph for every small price move.

### 7.6 Market-only and market-plus-news modes

The system must work both ways:

- **Market-only:** no news provider. The News Analyst is omitted or explicitly told news is disabled. The report says “news disabled,” not “no news.”
- **Market-plus-news:** public web/RSS, user-provided files, and optionally Bloomberg news.

The mode must be visible in the email, report, and confidence. A missing news source must not turn into a silent “no news” conclusion.

---

## 8. Data-source review

### 8.1 Yahoo Finance

- Free, but **unofficial and unreliable**.
- `yfinance` scrapes/uses Yahoo endpoints; there is no SLA.
- Common failures: HTTP 429 rate limiting, 401/403 anti-bot responses, corporate proxy/TLS blocking, cloud/VPN IP blocking, endpoint changes.
- `get_fundamentals` is especially exposed because it calls `Ticker.info`, a present-day snapshot endpoint.
- Good for development, prototyping, and fallback. Not appropriate as the only production source for a firm.

### 8.2 Alpha Vantage

- Free key, but a low daily/minute quota.
- Supports company overview, statements, news sentiment, and indicators.
- Useful as a fallback for `get_fundamentals` and news.
- For statements, SEC EDGAR is usually better for US filers.

### 8.3 SEC EDGAR

- Free, no key; requires a contact `SEC_EDGAR_USER_AGENT`.
- Strong point-in-time support for US filers: statements as filed, with filing dates.
- Does not serve the company overview used by `get_fundamentals`.

### 8.4 FRED and Polymarket

- FRED: free key; macro series.
- Polymarket: keyless prediction-market data.
- Both are enrichment sources; failures degrade rather than abort.

### 8.5 StockTwits and Reddit

- Useful sentiment inputs, but public feeds are not archived and can change over time.
- Historical sentiment is therefore not fully reproducible.
- Optional Jev screening helps filter off-topic posts.

### 8.6 Bloomberg

Bloomberg is not one API. The provider layer should support multiple backends:

| Backend | Typical runtime | Real-time | Historical | Fundamentals | News |
|---|---|---:|---:|---:|---:|
| BLPAPI / Terminal | Windows desktop | If entitled | Yes | Yes | If entitled |
| B-PIPE | Linux/server | Yes | Yes | Yes | If entitled |
| Data License | Server/batch | Usually no | Yes | Yes | If entitled |

Recommended approach:

- Build a backend-neutral `BloombergProvider`.
- Implement the backend the firm is actually entitled to first.
- Declare capabilities and fail clearly when a requested capability is unavailable.
- Do not use Bloomberg website scraping as a substitute for licensed API access.

For the Windows desktop deployment described, BLPAPI is the likely pilot. For a central Linux service later, B-PIPE or Data License is more appropriate.

### 8.7 Public news and web scraping

Public news is useful but requires discipline:

- Prefer licensed API, then RSS, then public HTML, then a headless browser.
- One versioned extractor per site.
- Respect `robots.txt`, terms of service, rate limits, and copyright.
- Do not bypass logins or paywalls.
- Store source URL, published time, retrieved time, content hash, and credibility metadata.
- Deduplicate syndicated stories.
- Archive evidence at retrieval time if legally allowed.
- Treat all scraped text as untrusted for prompt-injection purposes.

### 8.8 User-provided files and observations

Two distinct inputs:

1. **User-provided news/article files**: evidence documents, possibly public.
2. **User observations/notes**: subjective, unverified PM/analyst input.

Both need provenance and confidence. Neither should be silently blended with vendor data. Observations should influence narrative/confidence first; only later, if validated, should they affect hard scores.

### 8.9 Outlook email

`pywin32` + Outlook COM is practical for a pilot.

Constraints:

- Windows-only; Outlook must be installed and signed in.
- A single Outlook profile sees its own mailbox plus explicitly delegated/shared mailboxes.
- Individual PM/Analyst inboxes on separate machines are **not** transparently accessible from one central desktop.
- Options:
  - **Central delegate access**: one account with permissions to each mailbox.
  - **Per-user local agent**: each user’s machine searches its own mailbox and sends matched evidence to the central service.
  - **Microsoft Graph**: production-grade, but needs tenant/admin consent.
- Outlook COM is stateful; serialize calls, use a dedicated thread/process, and handle COM errors.
- Running Outlook automation as a Windows service is fragile; an interactive desktop session or scheduled task is more realistic. Validate with IT.

Recommended for privacy: **per-user local agent** for inbox search, plus a central service mailbox for outbound alerts. For a small pilot, central delegate access may be simpler if explicitly approved.

---

## 9. Risk register

| Risk | Why it matters | Mitigation |
|---|---|---|
| Yahoo/YFinance outage or block | Alerts and reports fail or degrade unexpectedly. | Treat as fallback only; add licensed/Bloomberg primary; health checks. |
| Mixed-vendor price comparison | False triggers if last price and previous close come from different sources. | One source for the percentage calculation; or use the vendor’s own change field. |
| Stale or delayed data | PMs act on an old price. | Freshness checks, source labels, fail-closed trigger policy. |
| Intraday same-day collisions | Current memory/state log assume one analysis per ticker/date. | Event IDs and a separate event/analysis store; define one-per-day vs multi-alert policy. |
| Full graph cost/latency | Expensive, slow alerts. | Two-lane triage/deep design; reduced analysts/rounds; quick model for triage. |
| LLM hallucination | Confident but unsupported claims. | Deterministic data snapshots, source citations, confidence, validation. |
| Prompt injection | Scraped news/email can contain hostile instructions. | Sanitize, no-tools summarizer, strong system prompts, source isolation. |
| Email privacy | Individual inboxes contain sensitive data. | Per-user agents, consent, least privilege, retention, access logs. |
| Bloomberg licensing | Redistribution or storage may violate contract. | Legal review, entitlement checks, derived summaries only, no raw redistribution. |
| Web scraping legal/ToS | Copyright, robots, paywalls, rate limits. | Source registry, legal review, API/RSS first, no bypassing access controls. |
| LLM data leakage | Public or internal text leaves the firm. | Enterprise/zero-retention model terms; classification policy; no private email unless approved. |
| Plugin sprawl | Too many one-off models and graph changes. | Stable plugin contracts, one generic signal node, versioned manifests. |
| No authentication | Anyone with report access can read sensitive analysis. | SSO/RBAC, signed links, access-controlled storage. |
| Audit gaps | Hard to explain a past decision. | Immutable source archive, model/prompt/config versioning, report hashes. |
| Windows runtime fragility | Outlook/desktop sleeps or logs out. | Health monitoring, restart policy, dedicated machine, Graph fallback plan. |
| Checkpoint incompatibility | New config keys silently invalidate resume. | Keep firm config out of default; test signatures. |
| Overfitting signal models | Scores look good historically but fail live. | Point-in-time data, out-of-sample tests, model cards, advisory-first use. |

---

## 10. Recommended direction

### 10.1 Keep the original product intact

- Default behavior remains **vanilla**.
- No firm defaults in `DEFAULT_CONFIG`.
- No mandatory new dependencies.
- No new graph nodes, prompts, or report sections unless the firm profile is enabled.
- Original CLI, Python API, backtest, memory, and checkpoints keep working.

### 10.2 Add a firm layer

```text
tradingagents/     original engine, minimal optional hooks
cli/               original CLI
firm/              monitor, Outlook, Bloomberg, portal, profiles
firm_signals/      proprietary signal/analysis plugins
tests/             original tests
tests_firm/        firm extensions and compatibility tests
```

### 10.3 Provider-first data architecture

- Quote, historical, fundamentals, news, and flow providers.
- A security master to map Bloomberg IDs, internal IDs, Yahoo symbols, and company aliases.
- Capability and entitlement metadata.
- Explicit health, freshness, and fallback behavior.

### 10.4 Pluggable signal framework

- One generic `SignalPlugin` contract.
- One generic `Signal Analyst` node.
- Deterministic computation, LLM narrative.
- Scenario/sensitivity as a kernel feature.
- Momentum as the first plugin, not a special case.
- Model versioning and model cards.

### 10.5 Two-lane alerting

- Fast triage: evidence + short summary + email.
- Deep analysis: full graph + report + second email/link.
- Market-only and market-plus-news modes.
- Fail-closed for real-time triggers; degrade with labels for analysis.

### 10.6 Outlook and reports

- `EmailGateway` abstraction; Outlook COM first, Graph later.
- Per-user inbox agent for privacy, central outbound mailbox.
- Attach a compact report; also provide a link.
- Add a PDF exporter; fallback to HTML if necessary.
- Do not use the full HTML report as the email body.

### 10.7 Governance

- Immutable event, evidence, signal, and report records.
- Model/plugin version, config hash, data source, as-of timestamp, prompt version.
- Access control and retention policy.
- Audit review before production.

---

## 11. Backward-compatibility contract

The following must remain true in vanilla mode:

| Surface | Guarantee |
|---|---|
| CLI | Bare `tradingagents` behaves as before. |
| Python API | Existing `TradingAgentsGraph(...).propagate(...)` call forms and return shape remain compatible. |
| Config | `DEFAULT_CONFIG` unchanged; no new required keys. |
| Graph | No signal nodes, no extra LLM calls, original topology. |
| Prompts | No added signal context. |
| Reports | Same files and sections; no empty new sections. |
| Memory | Same format and one-decision-per-ticker/date behavior. |
| Checkpoints | Default run signatures remain compatible. |
| Backtest | Same behavior. |
| Dependencies | Base install unaffected by Outlook/Bloomberg/portal extras. |
| Platforms | Importing on Linux does not import Windows-only modules. |
| Upstream | Core diff stays small; firm code lives outside the original flow. |

This should be enforced by tests, not just documented.

---

## 12. Decision summary and open questions

### Recommendations already supported by the review

1. Build a wrapper/service layer rather than rewriting the graph.
2. Keep vanilla mode as the default.
3. Use a provider layer above the existing vendor router.
4. Build a backend-neutral Bloomberg adapter; implement one backend first.
5. Use public web/RSS plus user-provided files for news; support news-on and news-off modes.
6. Use `pywin32`/Outlook for the pilot, behind an `EmailGateway` abstraction; plan for Graph later.
7. Prefer per-user Outlook agents for individual inboxes; use a central service mailbox for outbound.
8. Use a two-lane alert workflow: fast triage email, then deep analysis.
9. Make signal models plugins with a generic signal node; momentum is the first plugin.
10. Compute signals deterministically; let the LLM explain, not calculate.
11. Treat signal scores as advisory until backtested.
12. Add PDF export and a report link; attach only generated analysis, not raw sources.
13. Fail closed for real-time alert triggers; degrade with labels for deeper analysis.
14. Add audit, versioning, and access control before production.

### Open decisions for the firm

1. Which Bloomberg route will be used for the pilot: BLPAPI, B-PIPE, or Data License?
2. Are individual inboxes reached via delegate access or per-user local agents?
3. Which public news sites are approved, and which are paywalled or restricted?
4. Are user-provided files article content, source URL lists, PM notes, or all three?
5. Is the first production deployment a single Windows desktop or a central server plus Windows gateway?
6. What are the acceptable latency and cost targets for triage and deep analysis?
7. Which LLM provider and data-retention terms are approved for public news and internal research?
8. Is a PDF report required at launch, or is an HTML attachment acceptable for the pilot?
9. Should momentum/signal scores be advisory or allowed to gate the PM decision?
10. What retention period and access policy apply to alerts, reports, emails, and source evidence?

---

## 13. Upstream upgrade and compatibility

The firm plans to build a wrapper rather than fork TradingAgents. That is the right direction, but “wrapper” must mean a **disciplined compatibility layer**, not a collection of monkeypatches.

### 13.1 What “1.0 should not be an issue” can realistically mean

A major release can change public APIs, report formats, or provider behavior. We cannot promise zero changes. We can promise that upgrading should be a **bounded adapter-and-test task**, not a rewrite of the firm platform.

The goal:

- monitor, Outlook, portal, news, and signal plugins keep working
- only `firm/engine/compat/` and the thin facade may need adjustment
- vanilla TradingAgents remains available as a fallback
- the upgrade is tested against a pinned version before production

### 13.2 Public surface vs private surface

The wrapper should rely on a short allowlist, to be recorded in a single compatibility module (for example, a future `firm/engine/compat.py`):

- `TradingAgentsGraph`
- `propagate`
- `save_reports`
- `settle_pending` / `settle_all_pending`
- documented config keys and `build_default_config`
- `PortfolioContext` / `load_portfolio`
- `run_backtest` / `iter_grid` / `summarize`
- documented report artifacts and CLI flags

Avoid outside `firm/engine/compat/`:

- `_run_graph`, `_run_signature`, `_log_state`
- `GraphSetup`, `ANALYST_NODE_SPECS`
- `VENDOR_METHODS`, `TOOLS_CATEGORIES`
- state-log JSON field layout
- memory-log file format
- CLI display internals

This boundary should be enforced by tests and stated in the wrapper’s contribution rules.

### 13.3 The three integration options

| Option | Description | Upgrade cost | Recommendation |
|---|---|---|---|
| **A. Upstream hooks** | Ask upstream for generic, vanilla-safe hooks: data-provider registration, analysis/event context, optional signal node, conditional report sections, public API contract. | Lowest long-term | Preferred |
| **B. Thin shim** | Keep a small version-guarded shim in `firm/engine/compat/` if a hook is not accepted. No full fork. | Medium | Fallback |
| **C. Pure wrapper, reduced integration** | Use original agents with existing data vendors; use Bloomberg/news only for monitor and triage. | Low maintenance, weaker product | Pilot fallback |

Recommended sequence: **A first, C during the pilot, B only if deep integration must ship before A is accepted.**

### 13.4 Why a pure wrapper is not enough for the full target

A wrapper can own watchlists, Bloomberg quotes, news scraping, Outlook, reports, and signal computation. It cannot, by itself:

- make the original agents call a new Bloomberg data vendor;
- inject “this run was triggered by a 4.2% move” into the agents’ prompts;
- add a firm signal report to the original graph;
- add a new report section without changing upstream reporting code.

These are generic extension problems. A small, generic upstream-hook proposal should address them; if upstream declines, the fallback stays confined to one compatibility module.

### 13.5 Upgrade runbook

1. Read the upstream changelog and breaking-change notes.
2. Create an upgrade branch; pin the new version and lockfile.
3. Run the firm compatibility suite and the original upstream tests.
4. Run a fake end-to-end workflow: trigger → triage email → deep report → attachment/link.
5. Compare golden report/state mappings.
6. Run a Windows Outlook smoke test if the email path changed.
7. Canary with test tickers and dry-run email.
8. Review cost, latency, and error rates.
9. Roll back if thresholds are exceeded; keep the prior version available.
10. Update the compatibility matrix and upgrade notes.

### 13.6 Compatibility matrix and tests

Maintain a table such as:

| Firm wrapper | TradingAgents | Status |
|---|---|---|
| 0.1.x | 0.7.x | Supported |
| 0.2.x | 1.0.x | Tested |
| … | … | … |

Tests should cover:

- public imports and construction
- `propagate` and `save_reports` call forms
- fake LLM/data end-to-end
- report artifact presence
- backtest call with a tiny grid
- no extra calls in vanilla mode
- no Windows imports on Linux
- no private upstream imports outside `firm/engine/compat/`
- checkpoint behavior when used

### 13.7 What this means for the firm

- The firm wrapper should be a **separate internal package or a clearly separated `firm/` package**, not a fork of `tradingagents/`.
- Upstream stays installable from a tagged release and can be upgraded independently.
- The wrapper should fail fast with a clear message if the installed version is outside the tested range.
- The firm can always fall back to vanilla CLI/API if an extension breaks.
- A 1.0 upgrade should be a planned maintenance exercise with tests and a rollback path — not a platform rebuild.

---

## Appendix A — Key files and symbols

| Area | Key files / symbols |
|---|---|
| Graph entry | `tradingagents/graph/trading_graph.py`: `TradingAgentsGraph`, `propagate`, `_run_graph`, `save_reports`, `_log_state` |
| Graph wiring | `tradingagents/graph/setup.py`: `GraphSetup.setup_graph`, `_analyst_graph` |
| Analyst plan | `tradingagents/graph/analyst_execution.py`: `ANALYST_NODE_SPECS`, `build_analyst_execution_plan` |
| State | `tradingagents/agents/state.py`: `AgentState` |
| Agent tools | `tradingagents/agents/tools.py` |
| Data router | `tradingagents/dataflows/router.py`: `TOOLS_CATEGORIES`, `VENDOR_METHODS`, `route_to_vendor` |
| Point-in-time | `tradingagents/dataflows/date_window.py` |
| Errors | `tradingagents/dataflows/errors.py` |
| Config | `tradingagents/default_config.py`, `tradingagents/dataflows/config.py` |
| Memory | `tradingagents/memory/log.py`, `settlement.py`, `reflection.py` |
| Reports | `tradingagents/reporting.py`, `report_html.py` |
| Backtest | `tradingagents/backtest.py` |
| Portfolio | `tradingagents/portfolio.py` |
| LLM clients | `tradingagents/llm_clients/` |
| CLI | `cli/main.py`, `cli/run.py`, `cli/selections.py`, `cli/prompts.py` |
| Tests | `tests/`, especially `test_layering.py`, `test_vendor_routing.py`, `test_*lookahead*.py`, `test_suite_isolation.py` |
| CI | `.github/workflows/ci.yml` |
| Packaging | `pyproject.toml`, `Dockerfile`, `docker-compose.yml` |

## Appendix B — Current defaults and commands

### Default paths

- Results: `~/.tradingagents/logs`
- Cache: `~/.tradingagents/cache`
- Memory log: `~/.tradingagents/memory/trading_memory.md`
- Checkpoints: `~/.tradingagents/cache/checkpoints/<TICKER>.db`

### Default vendor chains

```text
core_stock_apis        yfinance
technical_indicators   yfinance
fundamental_data       sec_edgar,yfinance
news_data              yfinance
macro_data             fred
prediction_markets     polymarket
```

### Development commands

```bash
pip install -e ".[dev]"
pytest -q                       # offline unit/smoke tests
pytest -m integration           # live-service tests, only when asked
pytest tests/test_x.py::test_y  # a single test
ruff check .                    # what CI enforces
python -m cli.main              # CLI from source
```

### Optional extras

```bash
pip install ".[bedrock]"        # AWS Bedrock
pip install ".[outlook]"        # proposed: Windows Outlook/pywin32 integration
```

## Appendix C — Why this review is not a code audit

This is an architecture and fit review, not a line-by-line security audit or a performance benchmark. It identifies the main structural facts, extension points, gaps, and risks. Before production, the firm should also perform:

- dependency and security review,
- data-licensing review,
- privacy impact assessment,
- Windows/Outlook operational validation,
- load and cost testing,
- and a pilot with representative tickers and users.

---

## Appendix D — Diagram vs. implementation

A product diagram commonly shown with TradingAgents has boxes for Yahoo Finance, X, Reddit, CoinGecko, Bloomberg, Finnhub, Reuters, company profile, financial history, insider transactions, and execution.

That diagram is a **conceptual target**, not a current capability inventory. The vendor registry and source code are authoritative.

| Diagram box | Current implementation | Status |
|---|---|---|
| Yahoo Finance | `yfinance` vendor | Implemented |
| X / Twitter | No client or API integration | Not implemented |
| Reddit | Public Reddit feed fetch | Implemented |
| CoinGecko | No client or dependency | Not implemented |
| Bloomberg | No client or entitlement handling | Not implemented |
| Finnhub | No client | Not implemented |
| Reuters | No client or scraper | Not implemented |
| StockTwits | Public StockTwits fetch | Implemented (not shown in the diagram) |
| Company profile | Yahoo / Alpha Vantage overview | Implemented |
| Financial history | SEC EDGAR as filed; Yahoo / Alpha Vantage alternative | Implemented, partial |
| Insider transactions | Yahoo / Alpha Vantage | Implemented, partial |
| Research team / debate | LangGraph agents | Implemented |
| Trader / risk / manager | Agent nodes | Implemented |
| “Execution” | No broker or OMS integration | Not implemented |
| “OpenAI Deep Thinking” | Multiple LLM providers, not only OpenAI | Implemented more broadly |

The diagram also suggests one shared data pipe feeding all agents. In the code, each analyst has its own tool set:

- Market/Technical: stock data, indicators, verified market snapshot.
- Fundamentals: overview, statements, insider transactions.
- News: company/global news, macro data, prediction markets.
- Sentiment: pre-fetched news, StockTwits, Reddit.

“The architecture can connect to Bloomberg or social media” is true in the sense that the vendor/router seams exist. It is not true in the sense that those connectors are already present. Adding them is code, credentials, licensing, and tests — not just a configuration entry.

For the analyst, research-team, and trader workflows, see `ANALYST_WORKFLOWS.md`.
