# PLAN.md

**Project:** TradingAgents firm extension  
**Companions:** `REVIEW.md`, `ANALYST_WORKFLOWS.md`  
**Status:** proposed plan — not yet implemented  
**Audience:** product owners, portfolio managers, analysts, and engineers  
**Guiding rule:** the original TradingAgents behavior must remain available and unchanged in **vanilla mode**

> **Plain-language summary for PMs**
>
> We will keep the original TradingAgents analysis team exactly as it is, and build a new operating layer around it:
>
> - a **watchtower** that watches a list of stocks and detects meaningful moves;
> - an **evidence desk** that collects news, web sources, email, files, and user notes;
> - a **triage writer** that sends a short, source-backed email quickly;
> - the **analysis factory** (the existing TradingAgents graph) for deeper work;
> - a **mailroom** that uses Outlook to search and send;
> - a **report library** where PMs can open the full report;
> - a **quality office** that records what happened, what data was used, and why.
>
> The first version should be a small, controlled pilot on the Windows desktop. The architecture is designed so more models — momentum, quality, valuation, risk, macro — can be added later as plug-ins without rewriting the core.

**How to read this document**

- **PMs / business readers:** sections 1–4, 7, 13, 15–17. These cover the workflow, what PMs will see, the rollout, decisions, “done,” and the upstream-upgrade promise.
- **Engineers:** sections 5–6, 8–12, and the appendices. These cover architecture, contracts, configuration, security, testing, and deployment.

---

## 1. What we are trying to achieve

### 1.1 The target workflow

1. Maintain a watchlist of stocks.
2. Receive prices from Bloomberg (or another configured source).
3. Detect when a stock moves more than a configured percentage from its previous close.
4. Gather relevant evidence: news, public web sources, analyst/PM email, filings, internal notes.
5. Produce a short, source-backed summary.
6. Email the summary to the relevant PMs/analysts.
7. Run a deeper multi-agent analysis in the background.
8. Attach the report and/or provide a link.
9. Let PMs pull and read the report when ready.
10. Record what happened for audit, learning, and backtesting.

### 1.2 What “done” looks like for a PM

A PM should be able to say:

- “I got an alert only when something meaningful happened.”
- “The alert told me what moved, when, by how much, and from which source.”
- “The summary pointed me to the evidence and did not invent facts.”
- “I could open the full report from the email.”
- “The report used the same price and timestamp as the alert.”
- “If news was disabled or unavailable, it said so.”
- “I did not get five duplicate emails for the same move.”
- “I can see why the model scored the stock the way it did.”
- “I can find the report and its evidence later.”

### 1.3 What we will not do

- We will not replace the existing agents or debate flow.
- We will not make firm extensions mandatory.
- We will not rely on Yahoo Finance as the only production data source.
- We will not scrape sources that prohibit it or bypass paywalls/logins.
- We will not send raw licensed Bloomberg content or private email to an LLM without an approved policy.
- We will not let an LLM calculate a score that is supposed to be deterministic and auditable.
- We will not let a plugin add a new graph node per model.

---

## 2. Design principles

1. **Vanilla first.** The original CLI, Python API, prompts, reports, memory, and checkpoints keep working with no firm configuration.
2. **Add, do not rewrite.** New capabilities sit around or beside the original engine.
3. **Provider-first data.** Bloomberg, public news, email, files, and fallbacks all implement standard provider contracts.
4. **One signal kernel, many models.** Momentum is the first signal plugin, not a special case.
5. **Deterministic computation, LLM explanation.** Numbers are computed in Python; the model writes narrative and interpretation.
6. **Point-in-time by default.** Every evidence item carries source, as-of time, retrieval time, and confidence.
7. **Explicit failure.** A missing source says “unavailable,” not “no news.” An alert with stale data fails closed.
8. **Two lanes.** Fast triage email first; deep analysis second.
9. **Version everything.** Model version, config hash, prompt version, provider, data snapshot, report hash.
10. **No silent fallback.** The configured provider chain is the chain; fallback is explicit and labelled.
11. **Windows-only code stays at the edge.** Outlook COM and Bloomberg desktop adapters are optional and lazily imported.
12. **Maximum flexibility with guardrails.** Plugins are trusted code with declared capabilities, not arbitrary configuration scripts.

---

## 3. Target system in plain language

Think of six departments:

```text
┌──────────────────────────────────────────────────────────────────┐
│  1. WATCHTOWER                                                   │
│  watchlist, market feed, % move, cooldown, event record          │
└───────────────────────────────┬──────────────────────────────────┘
                                │ event
                ┌───────────────┴────────────────┐
                ▼                                ▼
┌──────────────────────────────┐   ┌────────────────────────────────┐
│  2. EVIDENCE DESK            │   │  3. TRIAGE WRITER              │
│  news, web, email, files,    │──►│  short summary + source links  │
│  filings, user notes         │   │  + confidence                  │
└──────────────────────────────┘   └───────────────┬────────────────┘
                                                    │
                                                    ▼
                                  ┌────────────────────────────────┐
                                  │  4. MAILROOM (Outlook)         │
                                  │  send alert to PMs/analysts    │
                                  └────────────────────────────────┘

┌──────────────────────────────┐   ┌────────────────────────────────┐
│  5. ANALYSIS FACTORY         │   │  6. REPORT LIBRARY             │
│  existing TradingAgents      │──►│  Markdown, HTML, PDF, index    │
│  graph + optional signals    │   │  links, access control         │
└──────────────────────────────┘   └────────────────────────────────┘
```

The **quality office** runs across all six: audit records, model versions, metrics, access logs, and retention.

### 3.1 Watchtower

- Keeps a list of stocks with per-stock thresholds and cooldowns.
- Gets quotes from Bloomberg or a configured development feed.
- Computes percentage move consistently from one source.
- Creates one event per threshold crossing, with an event ID.
- Suppresses duplicates and repeated alerts.
- Marks stale/delayed data and pauses if required.

### 3.2 Evidence desk

- Collects news from public sites, RSS, user-provided files, and optionally Bloomberg news.
- Collects email evidence from the configured mailbox model.
- Stores source, published time, retrieval time, content hash, language, and confidence.
- Deduplicates syndicated stories.
- Never converts “source unavailable” into “no news.”

### 3.3 Triage writer

- Uses the quick/cheap model.
- Has **no tools**; it only summarizes the evidence it is given.
- Produces a short, PM-readable email.
- Includes what moved, when, why it might matter, source links, data quality, and whether deep analysis is running.
- Does not compute a formal investment rating.

### 3.4 Mailroom

- Uses Outlook (pywin32) for the pilot.
- Searches individual inboxes according to the chosen access model.
- Sends alerts from a dedicated service account or shared mailbox.
- Queues and retries when Outlook is unavailable.
- Supports dry-run and test recipients.
- Records message IDs for audit.

### 3.5 Analysis factory

- Existing `TradingAgentsGraph`.
- Optionally includes a generic Signal Analyst node and firm signal plugins.
- Runs market-only or market-plus-news.
- Saves the report tree explicitly via `save_reports()`.
- Does not depend on the CLI display path.

### 3.6 Report library

- Writes an immutable report folder per event.
- Produces Markdown, HTML, and optionally PDF.
- Provides a stable link for email.
- Can be a shared folder in the pilot and an authenticated portal later.
- Shows source evidence and data-quality labels.

---

## 4. Operating profiles

The system should support explicit profiles. Vanilla is the repository default; firm profiles are opt-in.

| Profile | Purpose | News | Bloomberg | Outlook | Signals |
|---|---|---|---|---|---|
| `vanilla` | Original TradingAgents | Existing vendors | No | No | No |
| `firm_market_only` | Alert + deep analysis without news | Disabled | Optional | Yes | Optional |
| `firm_market_news` | Alert + news + deep analysis | Public/user/Bloomberg | Optional | Yes | Optional |
| `firm_research` | Development/research | Configurable | Optional | No | Optional |

### 4.1 News-disabled behavior

When news is disabled:

- Do not run the News Analyst.
- Do not call news tools from other agents.
- The Sentiment Analyst runs on social sources only, or is disabled.
- The report says “news disabled.”
- Signal plugins that need news report “not computed: news disabled.”
- Confidence is reduced where news would normally contribute.
- Email triage says “news sources disabled.”

### 4.2 News-enabled behavior

When news is enabled:

- Collect from the configured providers.
- If a provider fails, mark that source unavailable.
- If retrieval succeeds but nothing matches, say “no matching articles found” with the window and sources.
- Preserve links, timestamps, and confidence.

---

## 5. Architecture

### 5.1 Layers

```text
┌──────────────────────────────────────────────────────────────────┐
│ Firm services: monitor, Outlook gateway, portal, profiles        │
├──────────────────────────────────────────────────────────────────┤
│ Firm extension kernel: providers, signals, scenarios, evidence  │
├──────────────────────────────────────────────────────────────────┤
│ TradingAgents core: agents, graph, dataflows, memory, reports   │
├──────────────────────────────────────────────────────────────────┤
│ Optional dependencies: Bloomberg, pywin32, web stack, PDF       │
└──────────────────────────────────────────────────────────────────┘
```

### 5.2 Proposed repository layout

```text
tradingagents/            # upstream core; small optional hooks only
cli/                      # upstream CLI; original behavior
firm/                     # firm-specific services
  config/
    profiles/
  providers/
    bloomberg/
    news_web/
    news_files/
    email_outlook/
    security_master.py
  monitor/
    watchlist.py
    trigger.py
    events.py
    scheduler.py
  notifications/
    email_gateway.py
    outlook_com.py
    templates/
  reports/
    pdf.py
    index.py
    portal/
  audit/
  cli.py                  # `tradingagents-firm` entry point
firm_signals/             # proprietary signal plugins
  momentum/
  quality/
  ...
tests/                    # original tests
tests_firm/               # firm tests, fakes, compatibility tests
```

### 5.3 Core extension points

Keep the core hooks small:

1. Optional `signal_engine` / `signal_node` in the graph.
2. Optional `signal_bundle` / `signal_report` in `AgentState`.
3. Optional `event_context` / `event_id` for alert-driven runs.
4. Conditional report sections.
5. Optional config namespace, not firm defaults in `DEFAULT_CONFIG`.
6. Optional `EmailGateway`-style adapters outside the core.

Nothing is enabled unless the firm profile asks for it.

### 5.4 Provider contracts

```python
class QuoteProvider(Protocol):
    def snapshot(self, symbols: list[str]) -> list[Quote]: ...
    def previous_close(self, symbol: str, as_of: datetime) -> float: ...

class HistoricalProvider(Protocol):
    def bars(self, symbol: str, start: date, end: date) -> list[Bar]: ...

class FundamentalsProvider(Protocol):
    def overview(self, symbol: str, as_of: date) -> FundamentalSnapshot: ...
    def statements(self, symbol: str, as_of: date) -> list[Statement]: ...

class NewsProvider(Protocol):
    def search(self, query: NewsQuery) -> list[NewsItem]: ...

class EmailProvider(Protocol):
    def search(self, query: EmailQuery) -> list[EmailMessage]: ...
    def send(self, message: OutboundEmail) -> str: ...
```

Every provider declares capabilities:

- asset classes and markets
- real-time / delayed / end-of-day
- supported fields
- entitlement tier
- point-in-time support
- rate limits and expected latency
- licensing and redistribution constraints

### 5.5 Security master

The security master maps:

```text
internal instrument ID
  ↔ Bloomberg ticker / FIGI / ISIN / SEDOL
  ↔ exchange and currency
  ↔ Yahoo symbol for existing tools
  ↔ company name and aliases for news/email search
```

Without this, the watchlist, trigger, email search, and analysis can disagree about which company is meant.

### 5.6 Signal plugin kernel

```text
AnalysisRequest
  ├─ ticker, as_of, horizon
  ├─ portfolio
  ├─ user observations
  ├─ provider handles
  ├─ plugin config
  └─ cache/run metadata
        │
        ▼
SignalPlugin.compute()
        │
        ▼
SignalResult
  ├─ value, scale, band
  ├─ confidence, divergence
  ├─ contributions
  ├─ evidence
  ├─ warnings
  └─ scenario
```

The engine:

- resolves enabled plugins from config
- validates plugin manifests and configs
- topologically orders dependencies
- runs independent plugins in parallel
- caches by plugin/version/inputs/config
- isolates failures and timeouts
- stores results and metadata

### 5.7 Scenario and sensitivity engine

Scenarios are first-class:

- horizon: intraday / tactical / strategic
- macro regime: higher rates, risk-off, recession, liquidity squeeze
- company assumptions: demand growth, margin, competition
- user observations
- model weights
- data-source variant

The engine returns base results plus scenario results and sensitivity:

- which factors change the score most
- whether rankings flip
- how confidence changes

Deterministic plugins run across all scenarios; LLM narrative runs for the base case and a compact scenario summary.

### 5.8 Generic Signal Analyst

One node, not one per model:

```text
Signal Engine (deterministic plugins)
        │
        ▼
Signal Analyst (LLM narrative over the bundle)
        │
        ▼
Original bull/bear debate → research manager → trader → risk → PM
```

Adding a plugin after this requires config and a plugin package, not graph rewiring.

### 5.9 Momentum as the first plugin

Momentum is a plugin with four sides:

| Axis | Demand | Supply |
|---|---|---|
| Fundamental | volume/revenue growth, pricing power, backlog, customer adds, share gains | capacity, inventory, competition, substitutes, supplier/buyer power, new entrants |
| Technical | buying pressure, volume expansion, relative strength, fund/institutional inflows, breadth | distribution, outflows, short interest, lockups, issuance/dilution |

Outputs:

- fundamental demand score
- fundamental supply score
- technical demand score
- technical supply score
- net fundamental pressure
- net technical pressure
- composite score
- confidence and divergence
- factor contributions and evidence

Porter’s Five Forces is a structured qualitative submodule with per-force scores, rationale, citations, and confidence.

### 5.10 Momentum and PM decision

- Momentum is both a **report** and a **PM input**.
- PMs see the score, factors, evidence, confidence, and scenario sensitivity.
- The PM retains discretion.
- Hard gates or position-size rules can be added later, after validation.
- The deterministic score is the source of truth; the LLM explains it.

---

## 6. Workstreams

The work is organized as workstreams, not a single big-bang release. Some can run in parallel. “Effort” is relative (S/M/L), not a calendar commitment.

### Phase 0 — Compatibility and configuration foundation

**Goal:** guarantee that the original product still behaves as before, and make firm configuration possible without editing core defaults.

**Plain-language outcome:** “Nothing changes unless we turn it on.”

**Deliverables:**

- `vanilla` profile; firm profiles.
- Config overlay outside `DEFAULT_CONFIG`.
- `TRADINGAGENTS_DATA_VENDORS` and `TRADINGAGENTS_TOOL_VENDORS` JSON overrides, or a firm config file.
- Vendor diagnostic command showing effective chains and key availability.
- Compatibility test suite:
  - graph topology unchanged in vanilla
  - no extra LLM/vendor calls
  - same report files/sections
  - checkpoint signature stable
  - Linux import does not touch Windows modules
- Separate `tradingagents-firm` entry point.

**Acceptance criteria:**

- Existing tests pass unchanged.
- `tradingagents` bare run behaves as before.
- `TradingAgentsGraph(...).propagate(...)` unchanged.
- No new mandatory dependencies.
- No firm settings in `DEFAULT_CONFIG`.
- `pytest` offline suite still passes.

**Effort:** S–M.  
**Dependencies:** none.

---

### Phase 1 — Provider layer and security master

**Goal:** establish a standard way to add Bloomberg, public news, email, files, and fallbacks.

**Plain-language outcome:** “Any data source can be plugged in without changing the analysis team.”

**Deliverables:**

- Provider contracts and normalized schemas.
- Capability, entitlement, freshness, and health metadata.
- Provider registry with explicit priority and fallback.
- Security master interface and a fake implementation.
- Fake providers for tests.
- Bridge from selected providers into the existing `dataflows/router` for agent tools.
- Freshness policy and stale-data rejection.
- Source labels: real-time, delayed, end-of-day, scraped, user file.

**Acceptance criteria:**

- Contract tests pass for every provider.
- A failed provider returns a typed degradation, not fabricated data.
- No silent fallback to unentitled or delayed sources.
- The security master maps at least one Bloomberg ID and one Yahoo symbol to the same internal instrument.
- Unit tests need no network.

**Effort:** M.  
**Dependencies:** Phase 0.

---

### Phase 2 — Bloomberg backend

**Goal:** support Bloomberg as the primary market-data source without committing the whole system to one Bloomberg product.

**Plain-language outcome:** “The watchtower gets reliable quotes and previous closes from Bloomberg.”

**Deliverables:**

- `BloombergProvider` façade.
- Backend adapters, implemented in this order as entitlement is confirmed:
  - BLPAPI / Terminal (likely Windows pilot)
  - B-PIPE (server streaming)
  - Data License (bulk/historical)
- Capability detection and startup validation.
- Quotes, previous close, currency, exchange session, quote timestamp.
- Historical bars and fundamentals.
- News only if entitled.
- Entitlement and redistribution controls.
- Reconnect/health handling.
- Integration tests, opt-in and environment-gated.

**Acceptance criteria:**

- The same instrument can be queried through the provider regardless of backend.
- A requested capability that the backend lacks fails clearly.
- Alert calculation uses one source for last price and previous close.
- Stale quotes are rejected or flagged.
- No Bloomberg credentials or raw licensed content in logs/reports.
- The system can run against a fake provider on Linux CI.

**Effort:** M–L, depending on backend and entitlement.  
**Dependencies:** Phase 1; Bloomberg access confirmed.

---

### Phase 3 — News and evidence layer

**Goal:** collect public news, RSS, user files, and optionally Bloomberg news, with provenance and deduplication.

**Plain-language outcome:** “The evidence desk can explain what happened and where it read it.”

**Deliverables:**

- `NewsProvider` and `EmailProvider` contracts.
- Public web/RSS source registry.
- Versioned per-site extractors.
- Fetch layer with rate limits, retries, caching, and optional headless browser.
- User-provided file ingestion (JSON/Markdown/HTML).
- Source manifest: URL, title, author, published_at, retrieved_at, language, hash, credibility.
- Dedupe and near-duplicate detection.
- Archive policy: extracted text + metadata; raw HTML only if permitted.
- News-enabled / news-disabled profiles wired through agents and reports.
- Prompt-injection safeguards.

**Acceptance criteria:**

- Extractor tests use saved HTML fixtures; no live network in CI.
- News disabled produces an explicit “news disabled” state.
- Source unavailable produces “unavailable,” not “no news.”
- Duplicate syndicated stories are collapsed.
- User files validate and reject malformed input.
- Every evidence item has provenance and timestamp.

**Effort:** M–L.  
**Dependencies:** Phase 1. Can run in parallel with Phase 2.

---

### Phase 4 — Signal and scenario kernel

**Goal:** support many pluggable analysis models without changing the graph for each one.

**Plain-language outcome:** “We can add a new model by installing a plugin and changing configuration.”

**Deliverables:**

- `SignalPlugin` contract, manifest, config schema, model card.
- Plugin registry with version pinning.
- Execution engine: dependency order, parallel branches, caching, timeouts, failure isolation.
- Scenario expansion and sensitivity engine.
- User observation schema and store.
- Generic `Signal Analyst` node and `signal_bundle` / `signal_report` state.
- Optional report section rendered from the bundle.
- CLI/config selection of enabled plugins.
- Plugin authoring guide and contract tests.

**Acceptance criteria:**

- A fake plugin can be added without graph changes.
- One plugin failure does not fail the run.
- Scenario matrix works with a fake deterministic plugin.
- LLM narrative receives only computed signals and warnings; it does not compute scores.
- Signal plugin versions are recorded in the report.
- Vanilla mode has no signal node and no extra calls.

**Effort:** M–L.  
**Dependencies:** Phase 0; can start with fake plugins before Bloomberg/news are ready.

---

### Phase 5 — Momentum and Porter plugin

**Goal:** deliver the first firm signal model.

**Plain-language outcome:** “PMs see whether fundamental demand or technical flow is winning, with evidence and scenarios.”

**Deliverables:**

- Fundamental demand/supply factors.
- Technical demand/supply factors.
- Volume/flow proxies from OHLCV.
- Porter’s Five Forces structured rubric with citations and confidence.
- Horizon profiles: tactical and strategic; intraday later if data supports it.
- Scenario matrix and sensitivity output.
- User observations as optional evidence.
- Advisory integration into the report and PM prompt.
- Model card and versioning.

**Acceptance criteria:**

- Factor calculations are deterministic and unit-tested.
- Missing data lowers confidence and is visible.
- Porter scores require evidence and confidence.
- Scenario output shows ranking/score changes.
- The report explains the score without inventing numbers.
- The PM can override; no hard gate at launch.
- Historical runs use point-in-time inputs.

**Effort:** M–L.  
**Dependencies:** Phases 1, 3, 4; licensed flow/industry data for firm-grade factors.

---

### Phase 6 — Monitor and trigger service

**Goal:** watch the list and raise events.

**Plain-language outcome:** “We get an alert when a stock makes a meaningful move, not every few seconds.”

**Deliverables:**

- Watchlist configuration: ticker, threshold, cooldown, recipients, analysis profile.
- Quote polling or subscription.
- Consistent percentage move calculation.
- Event store with event ID, timestamp, source, freshness.
- Cooldown, deduplication, and session/market calendar handling.
- Queue/worker model.
- Fail-closed behavior for stale/real-time gaps.
- Market-only and market-plus-news execution.
- Event replay for testing.
- Health/status endpoint or log heartbeat.

**Acceptance criteria:**

- One event per qualifying move; no duplicate alerts inside cooldown.
- Stale quotes pause or flag the alert.
- The event record identifies source and timestamp.
- A failed quote feed does not silently fall back to delayed data unless explicitly allowed.
- A simulated event can run end-to-end with a fake provider.
- The monitor can be restarted without losing state.

**Effort:** M–L.  
**Dependencies:** Phases 1 and 2; Phase 3 for news mode.

---

### Phase 7 — Outlook gateway

**Goal:** search individual inboxes and send PM alerts using Outlook.

**Plain-language outcome:** “The mailroom works inside our actual Outlook environment.”

**Deliverables:**

- `EmailGateway` contract and `OutlookComGateway`.
- Optional `pywin32` extra with Windows-only marker.
- Lazy COM imports; fake gateway for Linux/tests.
- Inbox access model chosen:
  - per-user local agent, or
  - central delegate/shared mailbox.
- Bounded search: folders, date window, sender allowlist, item cap.
- Default folder constants, not localized folder names.
- Plain-text extraction and HTML sanitization.
- Dedup by message ID, conversation ID, and content hash.
- Outbound template, dry-run, test recipients.
- Attachment support.
- Queue/retry and message-ID audit.
- Windows scheduled task/startup task; not a Windows service.
- Manual Windows smoke test.

**Acceptance criteria:**

- Linux CI never imports `win32com`.
- Fake gateway tests cover search, send, dedup, and failure.
- Windows smoke test can search a test inbox and send a test email.
- The service excludes its own alert emails from search.
- No raw private email leaves the machine without policy approval.
- The gateway recovers from Outlook restart.

**Effort:** M.  
**Dependencies:** Phase 1; Windows machine and Outlook profile.

---

### Phase 8 — Report delivery, attachment, and PM portal

**Goal:** make the report easy to receive and easy to pull.

**Plain-language outcome:** “PMs get a useful email and can open the full report whenever they want.”

**Deliverables:**

- Email-safe HTML template, separate from the full report page.
- PDF exporter or HTML attachment fallback.
- Stable report link.
- Report metadata: event ID, ticker, date, rating, model versions, sources, data quality.
- Report index (SQLite/JSON first).
- Static shared-folder view for pilot.
- Optional FastAPI portal with search, filtering, and report view.
- Access control and signed links.
- Retention policy.

**Acceptance criteria:**

- Email renders correctly in Outlook.
- Attachment opens; link works.
- Report shows what data produced it.
- PM can find a past report by ticker/date/event.
- Access is limited to intended recipients.
- A failed PDF renderer falls back to a link/HTML with a warning.

**Effort:** M–L.  
**Dependencies:** Phase 7 for delivery; Phase 6 for event records.

---

### Phase 9 — Audit, security, and observability

**Goal:** make the system trustworthy and operable.

**Plain-language outcome:** “We can explain what happened, who saw it, and why.”

**Deliverables:**

- Immutable event, evidence, signal, and report records.
- Model/plugin version, prompt version, config hash, data source, as-of timestamp.
- Source archive (where legally allowed).
- Access control / SSO for portal.
- Secrets management.
- Metrics: trigger count, alert latency, analysis latency, provider error rate, cost per alert.
- Health checks and alerting for the monitor/Outlook gateway.
- Audit log for mailbox access and report views.
- Runbook and rollback plan.

**Acceptance criteria:**

- A past alert/report can be reconstructed.
- Every score has a model version and evidence trail.
- Access is logged.
- Failed services raise an operational alert.
- Secrets are not in logs or reports.
- Retention and deletion policies are enforced.

**Effort:** M–L.  
**Dependencies:** Phases 1–8.

---

### Phase 10 — Evaluation and continuous improvement

**Goal:** prove the workflow is useful and improve it safely.

**Plain-language outcome:** “We know whether alerts and signals actually help PMs.”

**Deliverables:**

- Event replay harness.
- Signal evaluation: IC, hit rate, decay, turnover, scenario stability.
- Alert precision/recall review with PM feedback.
- Cost and latency reports.
- A/B comparison of model versions.
- Shadow mode before live email.
- Model cards and change review.

**Acceptance criteria:**

- Historical replay uses archived point-in-time evidence.
- A new model version can be compared to the previous one.
- PM feedback is captured and reviewed.
- No model change goes live without a model card and validation.
- The firm can roll back to a previous approved version.

**Effort:** M–L, ongoing.  
**Dependencies:** Phases 1–9.

---

## 7. End-to-end scenarios

### 7.1 Market-only alert

```text
Bloomberg quote
  → NVDA +4.2% vs previous close
  → event created
  → no news provider: “news disabled”
  → triage email with price/source/deep-analysis status
  → deep analysis (market/fundamentals, reduced rounds)
  → report saved
  → second email with attachment/link
```

**What PMs see:** a clean alert that does not pretend to know why the stock moved.

### 7.2 Market-plus-news alert

```text
Bloomberg quote
  → event created
  → news providers: public web/RSS + user files + optional Bloomberg news
  → evidence deduplicated and ranked
  → triage summary with citations
  → email with “deep analysis running”
  → deep analysis with news/sentiment
  → report + second email
```

**What PMs see:** a source-backed “what happened / why it might matter” summary, followed by a deeper report.

### 7.3 User-provided observation

```text
PM note: “Channel checks suggest demand is weakening.”
  → observation stored as unverified
  → included in evidence with author/time/confidence
  → narrative may use it; hard score only if policy allows
  → report shows it separately
```

**What PMs see:** their own observation is visible and attributable, not silently mixed with vendor data.

### 7.4 News disabled

```text
Profile: firm_market_only
  → News Analyst omitted
  → news tools not called
  → report says “news disabled”
  → confidence reduced where appropriate
```

**What PMs see:** no false “no news” conclusion.

### 7.5 Bloomberg outage

```text
Quote provider unavailable/stale
  → new alerts paused or flagged
  → operations notified
  → analysis may use labelled fallback for historical data
  → no false real-time alert
```

**What PMs see:** either a clearly degraded report or no alert, never a silent stale trigger.

### 7.6 Outlook outage

```text
Email send fails
  → message queued
  → retry
  → alert stored and visible in report library
  → operations notified if retries exhaust
```

**What PMs see:** no lost alert because Outlook was restarting.

---

## 8. Data contracts

### 8.1 Event

```json
{
  "event_id": "2026-10-25T14:31:05Z-NVDA-4f2a",
  "ticker": "NVDA",
  "internal_id": "US0378331005",
  "triggered_at": "2026-10-25T14:31:05Z",
  "last_price": 182.40,
  "previous_close": 175.05,
  "pct_change": 0.04199,
  "currency": "USD",
  "session": "regular",
  "source": "bloomberg",
  "freshness": "realtime",
  "threshold": 0.04,
  "profile": "firm_market_news",
  "status": "open"
}
```

### 8.2 Quote

```json
{
  "symbol": "NVDA",
  "internal_id": "US0378331005",
  "last": 182.40,
  "previous_close": 175.05,
  "currency": "USD",
  "quote_time": "2026-10-25T14:31:04Z",
  "received_at": "2026-10-25T14:31:05Z",
  "session": "regular",
  "source": "bloomberg",
  "freshness": "realtime"
}
```

### 8.3 News item

```json
{
  "id": "news-...",
  "ticker": "NVDA",
  "title": "...",
  "body": "...",
  "url": "https://...",
  "source": "public-web",
  "author": "...",
  "published_at": "2026-10-25T13:55:00Z",
  "retrieved_at": "2026-10-25T14:32:00Z",
  "language": "en",
  "content_hash": "...",
  "verification": "public",
  "confidence": "medium"
}
```

### 8.4 User observation

```json
{
  "ticker": "NVDA",
  "text": "Channel checks suggest demand is weakening.",
  "author": "PM name",
  "timestamp": "2026-10-25T14:20:00Z",
  "source": "PM note",
  "confidence": "medium",
  "verification": "unverified"
}
```

### 8.5 Signal result

```json
{
  "plugin_id": "momentum.demand_supply",
  "plugin_version": "1.0.0",
  "ticker": "NVDA",
  "as_of": "2026-10-25",
  "horizon": "tactical",
  "fundamental_demand": 0.62,
  "fundamental_supply": -0.18,
  "fundamental_net": 0.44,
  "technical_demand": 0.71,
  "technical_supply": -0.12,
  "technical_net": 0.59,
  "composite_score": 0.53,
  "band": "positive",
  "confidence": 0.68,
  "divergence": "aligned",
  "contributions": [],
  "evidence": [],
  "warnings": []
}
```

### 8.6 Report record

```json
{
  "report_id": "...",
  "event_id": "...",
  "ticker": "NVDA",
  "analysis_date": "2026-10-25",
  "created_at": "2026-10-25T14:45:00Z",
  "rating": "Overweight",
  "report_path": "results/reports/NVDA_20261025_144500/complete_report.html",
  "pdf_path": null,
  "data_quality": "degraded: news unavailable",
  "model_versions": {"llm": "...", "signals": ["momentum.demand_supply@1.0.0"]},
  "source_manifest": ["..."],
  "access": "PM-group"
}
```

### 8.7 Scenario result

```json
{
  "scenario": "bear_demand",
  "horizon": "strategic",
  "composite_score": -0.21,
  "delta_vs_base": -0.74,
  "confidence": 0.55,
  "key_drivers": ["revenue_growth", "inventory", "relative_strength"]
}
```

---

## 9. Proposed configuration

```yaml
profile: firm_market_news

data:
  market_data:
    primary: bloomberg
    fallback: [alpha_vantage, yfinance]
    freshness: realtime
  fundamentals:
    primary: bloomberg
    fallback: [sec_edgar, alpha_vantage, yfinance]
  news:
    enabled: true
    providers: [public_web, user_files, bloomberg_news]
    fallback: [alpha_vantage]
  macro:
    primary: fred
  flows:
    primary: bloomberg
    fallback: []

security_master:
  source: bloomberg

watchlist:
  - ticker: NVDA
    threshold_pct: 4.0
    cooldown_minutes: 60
    recipients: [technology-pms]
    profile: firm_market_news
  - ticker: AAPL
    threshold_pct: 3.0
    cooldown_minutes: 60
    recipients: [technology-pms]

signals:
  enabled: true
  plugins:
    - id: momentum.demand_supply
      version: "1.0.0"
      horizons: [tactical, strategic]
      weight: 0.4
    - id: quality.earnings
      version: "0.1.0"
      horizons: [strategic]

scenarios:
  base: {}
  bull_demand:
    assumptions: { demand_growth: "+20%" }
  bear_demand:
    assumptions: { demand_growth: "-20%" }
  higher_rates:
    regime: higher_rates

notifications:
  email:
    provider: outlook
    mode: per_user_agent   # or central_delegate
    dry_run: true

news_sources:
  - id: reuters-public
    type: rss
    url: https://...
    extractor: reuters_v1
  - id: company-ir
    type: html
    url: https://...
    extractor: company_ir_v1

user_news:
  enabled: true
  paths:
    - data/news/*.json
    - data/news/*.md

reporting:
  html: true
  pdf: auto
  attach: true
  link: true

audit:
  archive_evidence: true
  retention_days: 365
```

---

## 10. Security and compliance

### 10.1 Individual inboxes

The safest model for individual PM/Analyst inboxes is a **per-user local Outlook agent** that searches only that user’s mailbox and sends matched snippets to the central service. This avoids a central account with broad access.

Alternatively, a central desktop with delegate/shared-mailbox permissions can work for a small pilot, but requires:

- explicit IT approval,
- least-privilege mailbox permissions,
- access logging,
- data-minimization rules,
- a clear retention policy.

### 10.2 Outlook runtime

- Use an interactive Windows session; Outlook must be running and signed in.
- Prefer a scheduled task/startup task over a Windows service.
- Keep the gateway on a dedicated, always-on machine if possible.
- Add heartbeat and restart handling.
- Plan a Microsoft Graph adapter for production if tenant policy allows.

### 10.3 Web and news

- Prefer licensed API/RSS; scrape only where allowed.
- Respect `robots.txt`, terms, rate limits, and copyright.
- Do not bypass logins or paywalls.
- Preserve source URL, published time, retrieval time, and hash.
- Treat all text as untrusted for prompt injection.
- Do not send private email or licensed Bloomberg content under a “public info” policy.

### 10.4 LLM data policy

- Public web text may be sent to an approved LLM provider.
- Internal email/notes need explicit classification and approval.
- Use enterprise/zero-retention terms where required.
- Do not include secrets, credentials, or unnecessary personal data.
- Prefer summaries and links over reproducing full articles.

### 10.5 Access and audit

- Authenticate portal users with firm SSO.
- Role-based access to reports and alerts.
- Signed/expiring report links.
- Log mailbox searches and report views.
- Retention and deletion rules.
- Immutable event/report/source records.

---

## 11. Testing strategy

### 11.1 Compatibility suite

- Vanilla graph topology unchanged.
- Vanilla prompts do not include signal context.
- Vanilla report tree has the same files and sections.
- Vanilla run makes no extra LLM/vendor calls.
- Vanilla checkpoint signature unchanged.
- Original CLI tests pass.
- Linux import does not load Windows-only modules.

### 11.2 Provider and plugin contracts

- Every provider returns the normalized schema.
- Capability and entitlement checks.
- Failure/timeout/circuit-breaker behavior.
- No silent fallback.
- Every signal plugin has contract tests with fake data.
- Model version and config hash are recorded.

### 11.3 News and email

- Saved HTML fixtures; no live scraping in CI.
- Dedup and canonical URL tests.
- Missing timestamp/paywall/login detection.
- User file validation.
- Email gateway fake: search, send, dedup, retry.
- Windows-only Outlook smoke test, manual or gated.

### 11.4 End-to-end

- Fake quote provider → event → triage email → deep report → report link.
- Market-only and market-plus-news.
- Stale quote → fail closed.
- Missing news → explicit unavailable.
- Duplicate trigger → cooldown.
- Outlook failure → queue/retry.

### 11.5 PM acceptance tests

A PM reviewer should confirm:

- the alert is readable and not overly long;
- the trigger number and timestamp are correct;
- sources are visible and credible;
- no unsupported claim is presented as fact;
- “news disabled/unavailable” is explicit;
- the report attachment/link opens;
- the signal score has a clear explanation;
- duplicate alerts are suppressed;
- access is limited to intended recipients.

---

## 12. Deployment

### 12.1 Pilot topology

```text
Windows desktop
  ├─ Outlook (signed in)
  ├─ per-user Outlook agents or central delegate gateway
  ├─ monitor service
  ├─ analysis worker
  ├─ local SQLite event store
  ├─ report folder
  └─ Windows Task Scheduler / startup task
```

Linux is used for development, CI, and optionally the analysis worker with fake providers.

### 12.2 Production topology

```text
Windows gateway(s)                 Linux service
  Outlook / pywin32                 monitor/worker
  per-user agents                   queue + Postgres
  Bloomberg BLPAPI (if desktop)     report store / object storage
                                    FastAPI portal + SSO
                                    Bloomberg B-PIPE/Data License (if server)
```

### 12.3 Operational rules

- Single Windows desktop is a single point of failure; add heartbeat and restart.
- Do not run Outlook COM in Linux Docker.
- Keep the original `tradingagents` package installable without firm extras.
- Use a queue for retries and backpressure.
- Store reports outside the container/host filesystem in production.
- Back up the event/report/audit store.
- Provide a rollback path to vanilla mode.

---

## 13. Rollout plan

### Step 1 — Shadow mode

- Run the monitor and analysis without sending live emails.
- Compare alerts with what PMs would consider material.
- Review source quality, false positives, and latency.
- Validate that the original TradingAgents path still works.

### Step 2 — Controlled pilot

- 5–10 tickers, 2–5 PMs/analysts.
- Dry-run email to a test list, then live email to the pilot group.
- Market-only first; add news after evidence quality is acceptable.
- Daily review of alerts, reports, and failures.

### Step 3 — Production readiness

- Security/privacy review.
- Bloomberg/legal review.
- Outlook operational validation.
- Access control and audit.
- Runbook, on-call, and rollback.
- Cost/latency baselines.

### Step 4 — Expansion

- Add more tickers, markets, and recipients.
- Add signal plugins and scenarios.
- Add portal search/filtering.
- Replace Outlook COM with Graph if needed.
- Add model evaluation and A/B testing.

### Proposed success metrics

- Alert precision: share of alerts PMs consider material.
- Alert recall: material moves that were missed.
- Time from trigger to triage email.
- Time from trigger to deep report.
- Source coverage and citation completeness.
- Report usefulness rating from PMs.
- Cost per alert/report.
- Duplicate-alert rate.
- Provider failure rate.
- Percentage of reports with degraded data.
- Number of unsupported claims found in review.

Targets should be set after the pilot baseline, not guessed now.

---

## 14. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Bloomberg entitlement unclear | Build backend-neutral provider; confirm access before Phase 2. |
| Individual inbox access is politically/legally sensitive | Prefer per-user agents; require approval and audit for delegate access. |
| Yahoo is unreliable | Treat as fallback; use Bloomberg/Alpha Vantage/SEC for production. |
| Mixed data sources create false alerts | One source per percentage calculation; label freshness. |
| Full graph too slow/expensive | Two lanes; reduced analysts/rounds; quick model for triage. |
| One-analysis-per-day memory collision | Event IDs; separate event store; define cooldown/one-per-day policy. |
| News scraping breaks or violates terms | Versioned extractors, fixtures, legal review, API/RSS first. |
| Prompt injection from news/email | Sanitize, no-tools summarizer, source isolation, review. |
| Report attachments blocked | PDF preferred; link always; test Outlook policies. |
| Windows desktop sleeps/logs out | Dedicated always-on machine; heartbeat; Graph fallback. |
| Plugin sprawl | Stable contracts, one generic node, manifests, versioning. |
| Audit gaps | Immutable records, source archive, model/prompt/config versions. |
| Checkpoint invalidation | Keep firm config out of defaults; compatibility tests. |
| PM distrust | Show evidence, confidence, divergence, and data quality; start advisory. |

---

## 15. Decisions and open questions

### Decisions already made in this plan

- Vanilla mode remains the default.
- The firm layer is additive and optional.
- Momentum is both a report and a PM input.
- Momentum is advisory at launch; no hard gate.
- Porter’s Five Forces is in v1 as a structured qualitative module.
- Scenario/sensitivity analysis is a kernel feature.
- News-on and news-off modes are both required.
- Public web/RSS and user-provided files are supported; Bloomberg news is optional.
- Outlook is the first email transport; Graph is a future adapter.
- Per-user Outlook agents are preferred for individual inboxes; central delegate access is a pilot fallback.
- Attach the report and include a link.
- Real-time alert triggers fail closed on stale data.
- Signals are deterministic; the LLM explains.

### Still to decide

1. **Bloomberg route for the pilot:** BLPAPI, B-PIPE, or Data License?
2. **Inbox access model:** per-user local agents or central delegate access?
3. **Approved public news sources:** which sites, RSS feeds, languages, and paywall rules?
4. **User-provided files:** article content, source URL lists, PM notes, or all three?
5. **Deployment:** single Windows desktop or central Linux service plus Windows gateway?
6. **LLM provider and data-retention terms** for public news and internal research.
7. **PDF at launch:** required, or is HTML attachment acceptable for the pilot?
8. **Alert policy:** one deep analysis per ticker per day, or one per cooldown window?
9. **Signal role:** advisory only at launch (recommended), or configurable gate later?
10. **Retention and access:** how long are alerts, reports, emails, and source evidence kept, and who can see them?

---

## 16. Definition of done

### For the pilot

- Original TradingAgents tests pass; vanilla mode unchanged.
- A Windows pilot runs the monitor, triage email, and deep analysis end-to-end.
- Bloomberg or a clearly labelled development feed supplies quotes.
- Market-only and market-plus-news modes both work.
- Outlook search and send pass a Windows smoke test.
- PMs receive a readable alert and a report attachment/link.
- Duplicate alerts are suppressed by cooldown.
- Stale data fails closed or is clearly flagged.
- News disabled/unavailable is explicit.
- The same price/time source is used for alert and report.
- Basic audit records exist: event, sources, model versions, report path, recipients.

### For production

- Provider contracts and Bloomberg entitlement are validated.
- Security/privacy/legal reviews are complete.
- Access control, retention, and audit are enforced.
- Operational monitoring and runbooks exist.
- Model cards and versioning are in place.
- Signal and alert evaluation has been reviewed.
- Rollback to vanilla is tested.
- PM acceptance tests pass with real users and representative tickers.

---

## 17. Upstream upgrade strategy

This is the “1.0 should not hurt” workstream.

### 17.1 Goal

Keep the firm platform upgradeable by changing only a thin compatibility layer, not the monitor, mailroom, portal, report library, or signal plugins.

### 17.2 Public-API contract

- Allowed upstream surface should be listed in one compatibility module (for example, a future `firm/engine/compat.py`).
- Firm code should call a wrapper facade such as `FirmAnalysisEngine`, not `TradingAgentsGraph` directly.
- Private upstream symbols should be forbidden outside that compatibility module.
- The original `tradingagents` and `cli` packages never import `firm`.

### 17.3 Integration sequence

1. **Propose generic upstream hooks** in a separate proposal (not in wrapper code):
   - data-provider registration
   - analysis/event context
   - optional signal/analysis extension node
   - conditional report sections
   - public API/version contract
2. **Build the pilot wrapper without those hooks**:
   - original analysis + report generation
   - monitor, Bloomberg/news providers, Outlook, report library
   - no dependence on private graph internals
3. **If deep integration must ship before upstream accepts the hooks**, add a small version-guarded shim only in the compatibility module.
4. **Remove the shim** when an upstream hook replaces it.

### 17.4 Version policy

- Production: pin an exact tested version.
- Development: test a supported range.
- Do not auto-upgrade production.
- Widen the range only after the compatibility suite passes.
- Keep the previous version available for rollback.

### 17.5 Contract-test matrix

Run against:

- the pinned production version
- the latest supported 0.x
- the next major version when available
- optionally upstream `main` in a scheduled job

Test:

- public imports and construction
- `propagate` and `save_reports`
- fake end-to-end analysis
- report artifacts
- tiny backtest
- vanilla no-extra-call behavior
- Linux/Windows import boundaries
- private-import guard
- checkpoint behavior when used

### 17.6 Upgrade runbook

1. Read changelog and breaking changes.
2. Branch; bump pin and lockfile.
3. Run firm compatibility suite and upstream tests.
4. Run fake end-to-end alert → email → report.
5. Compare golden report/state mappings.
6. Run Windows Outlook smoke test if needed.
7. Canary with dry-run email and test recipients.
8. Review cost/latency/errors.
9. Roll back if needed.
10. Update compatibility matrix and notes.

### 17.7 Acceptance criteria

- A new upstream release can be evaluated without changing firm service code.
- The only expected changes are in the compatibility module and, if necessary, one adapter.
- Vanilla behavior remains available.
- Rollback is one version pin away.
- No private upstream imports exist outside the compatibility module.
- A documented compatibility matrix exists.

### 17.8 What is explicitly out of scope

- Guaranteeing zero changes for every future upstream release.
- Supporting unreleased upstream branches in production.
- Forking the whole `tradingagents/` tree.
- Patching upstream internals without a version guard and removal plan.

---

## Appendix A — Sample triage email

```text
Subject: [Alert] NVDA +4.2% vs prior close — deep analysis running

NVDA moved +4.2% versus its previous close at 14:31 UTC.
Price: 182.40 USD | Previous close: 175.05 USD
Source: Bloomberg | Freshness: realtime | Session: regular

What we found
- [Headline 1] — source, time
- [Headline 2] — source, time
- No company filing matched the move in the last 24 hours.
- Two user-provided notes are included and marked unverified.

Why it may matter
[2–4 sentence summary, source-backed, no invented facts.]

Data quality
News: available (3 sources)
Fundamentals: available
Fund flows: unavailable
Signal: fundamental +0.44, technical +0.59, confidence 0.68

Deep analysis: running
The full report will follow in a second email and will be available at:
[report link]

This is research support, not investment advice.
```

## Appendix B — Sample signal snapshot

```text
Momentum: demand/supply (momentum.demand_supply@1.0.0)
Ticker: NVDA | As of: 2026-10-25 | Horizon: tactical

Fundamental demand:   +0.62
Fundamental supply:   -0.18
Fundamental net:      +0.44

Technical demand:     +0.71
Technical supply:     -0.12
Technical net:        +0.59

Composite:            +0.53 (positive)
Confidence:           0.68 (medium)
Divergence:           aligned

Top contributors
+ revenue growth
+ relative strength
+ volume expansion
- inventory growth
- supplier concentration

Porter's Five Forces (qualitative)
Rivalry: high | New entrants: medium | Substitutes: medium
Supplier power: medium | Buyer power: low
Evidence: 10-K competition section; two industry articles
Confidence: medium
```

## Appendix C — Sample report record

```text
Report: NVDA 2026-10-25 14:45 UTC
Rating: Overweight
Event: 2026-10-25T14:31:05Z-NVDA-4f2a
Data quality: fundamentals available; news available; flows unavailable
Models: quick=..., deep=..., signals=momentum.demand_supply@1.0.0
Sources: Bloomberg quotes; public news; user file; SEC EDGAR
Files: complete_report.md, complete_report.html, complete_report.pdf
Access: technology-pms
Audit: source manifest and prompt/model versions stored
```

## Appendix D — Glossary

See `REVIEW.md` section 2 for the full glossary. Key terms:

- **Vanilla mode:** original TradingAgents behavior.
- **Firm profile:** opt-in configuration that enables the new services.
- **Provider:** adapter for quotes, news, fundamentals, or email.
- **Signal plugin:** computed model that returns a standard signal result.
- **Scenario:** a “what if” variant of a signal calculation.
- **Triage:** fast, short summary email before the full analysis.
- **Deep analysis:** the existing multi-agent TradingAgents run.

---

## Appendix E — Diagram vs. implementation

When describing the current system, distinguish the conceptual architecture diagram from the implemented data sources.

| Diagram box | Current code | Planning implication |
|---|---|---|
| Yahoo Finance | Implemented via `yfinance` | Keep as fallback, not sole production source. |
| X / Twitter | Not implemented | Needs licensed API or approved alternative. |
| Reddit | Implemented via public feeds | Keep; monitor reliability and terms. |
| CoinGecko | Not implemented | Add adapter only if crypto detail is required. |
| Bloomberg | Not implemented | Add provider/backends with entitlements. |
| Finnhub / Reuters | Not implemented | Add only if licensed and useful. |
| Company profile | Yahoo / Alpha Vantage | Add licensed/alternative overview provider. |
| Financial history | SEC EDGAR / Yahoo / Alpha Vantage | Preserve point-in-time as-filed behavior. |
| Insider transactions | Yahoo / Alpha Vantage | Add as-filed Form 4 source if needed. |
| Execution | Not implemented | Separate OMS/broker project with controls; not an analyst feature. |
| “OpenAI Deep Thinking” | Multiple LLM providers | Provider abstraction already exists. |

The diagram’s four data boxes should not be read as one shared pipeline. Each analyst has its own tool set; see `ANALYST_WORKFLOWS.md`.

“Can connect” means the extension seams exist. “Connected” means an adapter, credentials, licensing, tests, and operational support exist. The current code has the former, not the latter, for Bloomberg, X, CoinGecko, Finnhub, and Reuters.

Execution is a separate product boundary. TradingAgents produces a decision, not an order. Any execution integration needs order state, pre-trade compliance, entitlements, audit, and broker/OMS operations.
