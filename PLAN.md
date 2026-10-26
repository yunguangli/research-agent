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
> - a **fundamental research desk** that turns filings, transcripts, sell-side research, and industry data into a forecast model, a valuation range, and a source-backed investment report;
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
7. Run a deeper multi-agent analysis in the background: the analysts produce fundamental, news, sentiment, and market findings.
8. Combine the fundamental evidence with the news and sentiment analysts' findings into a forecast model and valuation range, then run the Bull/Bear debate with the package. If a corporate action is involved, assess and value the deal separately.
9. Attach the report and/or provide a link.
10. Let PMs pull and read the report when ready.
11. Record what happened for audit, learning, and backtesting.

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
- “I can see the forecasts, valuation range, and assumptions, and the numbers tie to their sources.”
- “I can see how the news and sentiment findings changed the forecast, and what the numbers would have been without them.”
- “I can tell which documents were used and which were missing.”
- “I can find the report and its evidence later.”

### 1.3 What we will not do

- We will not replace the existing agents or debate flow.
- We will not make firm extensions mandatory.
- We will not rely on Yahoo Finance as the only production data source.
- We will not scrape sources that prohibit it or bypass paywalls/logins.
- We will not send raw licensed Bloomberg content or private email to an LLM without an approved policy.
- We will not let an LLM calculate a score that is supposed to be deterministic and auditable.
- We will not let an LLM calculate or invent forecast or valuation numbers.
- We will not send licensed research, transcripts, or broker notes to an LLM without an approved policy.
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
13. **Fundamental research is a deterministic model with an LLM narrative.** Forecasts and valuations are computed in Python, versioned, and auditable; the model extracts evidence, and news and sentiment enter as banded, capped signals rather than prose.
14. **Licensed evidence stays inside the firm boundary.** Bloomberg, broker research, and transcripts carry explicit licence labels and an approved LLM-use policy.

---

## 3. Target system in plain language

Think of six departments, plus a fundamental research desk that feeds the analysis factory:

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

The **quality office** runs across all seven components: audit records, model versions, metrics, access logs, and retention.

The **fundamental research desk** sits between the evidence desk and the analysis factory:

```text
Evidence Desk
  news, filings, transcripts, sell-side, industry, user notes
        │
        ▼
Fundamental Research Desk
  analyst reports + documents → structured evidence → forecast model → valuation
        │
        ▼
Research package
  investment report + quarterly/annual forecasts + valuation range + evidence
        │
        ▼
Analysis Factory
  Bull/Bear debate → Research Manager → Trader → Risk → Portfolio Manager
```


### 3.1 Watchtower

- Keeps a list of stocks with per-stock thresholds and cooldowns.
- Gets quotes from Bloomberg or a configured development feed.
- Computes percentage move consistently from one source.
- Creates one event per threshold crossing, with an event ID.
- Suppresses duplicates and repeated alerts.
- Marks stale/delayed data and pauses if required.

### 3.2 Evidence desk

- Collects news from Bloomberg News, public websites and RSS, company IR and exchange releases, user-provided articles, and optionally other licensed feeds.
- Collects email evidence from the configured mailbox model.
- Keeps broker research out of the news lane: broker notes feed the research desk, which may publish a **metadata-only event pointer** (ticker, event type, timestamp, headline) to the shared claim store.
- Deduplicates by claim and event, not by file type, so a news story and a research-desk event pointer about the same catalyst are one piece of evidence with two sources.
- Stores source, source role, published time, retrieval time, content hash, language, and confidence.
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

### 3.7 Fundamental research desk

- Consumes the Fundamentals, News, Sentiment, and Market analyst reports, not just the document store.
- Treats a broker note as a mixed document for the research desk: it carries estimates, a thesis, industry datapoints, and often the news catalyst itself.
- Deduplicates the same catalyst across broker notes, news articles, transcripts, and analyst reports at the claim level.
- Publishes a metadata-only event pointer when a broker note breaks a catalyst the news lane may not have yet; the pointer carries no licensed body text.
- Collects filings, earnings call transcripts, conference transcripts, sell-side notes, industry data, and user materials.
- Stores every document once, with ticker, type, source, author, published time, retrieval time, licence, and content hash.
- Extracts structured evidence: guidance, estimates, industry metrics, transcript segments, and sell-side takeaways.
- Converts news and sentiment findings into bounded, typed signals that adjust named forecast drivers and scenario probabilities.
- Routes corporate actions — M&A, divestitures, spin-offs, capital raises — to a deal-assessment module that values the transaction, instead of leaving them as news color or as a footnote in the fundamentals report.
- Lets more than one owner analyze the same document, but keeps each output separate and labelled.
- Builds a deterministic forecast model: historicals, drivers, quarterly and annual periods, and scenarios.
- Runs a valuation: multiples, DCF, peer set, and sensitivity.
- Produces a **research package**: an investment report, forecast tables, valuation tables, assumptions, and citations.
- States a **fundamental view** and a **fair value range**, not a final rating; the Research Manager keeps rating authority.
- Flags missing data, unsupported sectors, stale inputs, and licensing restrictions.
- Requires human review before the package enters the debate during the pilot.


---

## 4. Operating profiles

The system should support explicit profiles. Vanilla is the repository default; firm profiles are opt-in.

| Profile | Purpose | News | Bloomberg | Outlook | Signals | Fundamental research |
|---|---|---|---|---|---|---|
| `vanilla` | Original TradingAgents | Existing vendors | No | No | No | No |
| `firm_market_only` | Alert + deep analysis without news | Disabled | Optional | Yes | Optional | Optional |
| `firm_market_news` | Alert + news + deep analysis | Public/user/Bloomberg | Optional | Yes | Optional | Optional |
| `firm_fundamental_research` | Deep analysis with forecasts and valuation before the debate | Optional | Yes | Optional | Optional | Yes |
| `firm_research` | Development/research | Configurable | Optional | No | Optional | Optional |

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

### 4.3 Fundamental research behavior

When fundamental research is enabled:

- Run the fundamental research desk after the analysts and before the Bull/Bear debate.
- Feed it the Fundamentals, News, Sentiment, and Market analyst reports as first-class inputs, alongside the documents.
- Keep the News Analyst on Bloomberg News, websites, company IR, exchange releases, and user articles; do not give it the broker-research tool.
- Let the research desk publish metadata-only broker event pointers into the shared claim store, labelled `broker`, so the news lane can see a catalyst without receiving licensed text.
- Convert news and sentiment into typed signals with direction, magnitude band, horizon, confidence, and evidence; the engine applies them to named drivers and scenario probabilities.
- Route a corporate action to the deal-assessment module: the News Analyst frames the event, the Fundamentals Analyst keeps the standalone business, and the deal module values the transaction.
- Let the deal module and the Fundamentals Analyst analyze the same document if each has a reason to; the News Analyst works from the event pointer, and every output stays separate, labelled by `owner`, and never averaged.
- Use Bloomberg where entitled, SEC EDGAR for point-in-time statements, and user materials for documents that are not licensed through a provider.
- Produce the research package even when news is disabled; label news-dependent inputs as unavailable.
- Mark unsupported sectors (banks, insurers, REITs, biotech) as `not_rated` rather than forcing a generic model.
- Require human review before the package enters the debate during the pilot.
- Keep vanilla mode free of the node, the extra calls, and the extra report section.

When fundamental research is disabled:

- Bull/Bear receive the original analyst reports only.
- No forecast, valuation, or research-package section is written.
- The report says “fundamental research disabled” only if the profile expected it.

---

## 5. Architecture

### 5.1 Layers

```text
┌──────────────────────────────────────────────────────────────────┐
│ Firm services: monitor, research desk, Outlook, portal, profiles   │
├──────────────────────────────────────────────────────────────────┤
│ Firm extension kernel: providers, documents, forecasts, signals    │
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
      market.py
      research.py
    news_web/
    user_materials/
      provider.py
      ingest.py
    email_outlook/
    security_master.py
  documents/
    manifest.py
    extract.py
    store.py
  research/
    schemas.py
    model.py
    forecast.py
    deals.py
    valuation.py
    report.py
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
3. Optional `research_package` / `fundamental_research_report` in `AgentState`.
4. Optional pre-debate research node.
5. Optional `event_context` / `event_id` for alert-driven runs.
6. Conditional report sections.
7. Optional config namespace, not firm defaults in `DEFAULT_CONFIG`.
8. Optional document-provider registration.
9. Optional `EmailGateway`-style adapters outside the core.

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
    def estimates(self, symbol: str, as_of: date) -> list[Estimate]: ...
    def industry(self, symbol: str, as_of: date) -> IndustrySnapshot: ...

class ResearchDocumentProvider(Protocol):
    def list_documents(
        self, symbol: str, as_of: date, types: list[str]
    ) -> list[DocumentRecord]: ...
    def read_document(self, document_id: str) -> ResearchDocument: ...

class NewsProvider(Protocol):
    def search(self, query: NewsQuery) -> list[NewsItem]: ...

class EmailProvider(Protocol):
    def search(self, query: EmailQuery) -> list[EmailMessage]: ...
    def send(self, message: OutboundEmail) -> str: ...
```

Every provider declares capabilities:

- asset classes and markets
- real-time / delayed / end-of-day
- supported fields and document types (statement, transcript, broker research, industry report)
- entitlement tier
- point-in-time support
- rate limits and expected latency
- licensing, redistribution, and LLM-use constraints

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

### 5.11 Fundamental research and valuation engine

The fundamental research desk is a firm component, not a new prompt. It turns evidence into a reproducible package.

#### Document store and extraction

- One document store per firm deployment, keyed by internal instrument ID.
- Accepts HTML, PDF, Markdown, text, and optionally DOCX/XLSX.
- Preserves the original file, extracts text and tables, and records page/section anchors.
- Stores `DocumentRecord` metadata: ticker, type, source, author, published time, retrieval time, language, licence, hash, and confidence.
- Records a **source class** (`primary_filing`, `transcript`, `broker`, `news`, `industry`, `user`) and a multi-label `contains` list, because a broker note is rarely just one thing.
- Deduplicates by content hash and canonical URL.
- Enforces point-in-time access: only documents published on or before the run's `trade_date` are visible.
- Marks undated documents and excludes them from historical runs.
- Treats all document text as untrusted evidence, never as instructions.

#### Evidence overlap and claim reconciliation

Broker research, news, and transcripts overlap. A sell-side note typically repeats the
quarter's news, adds channel checks, restates guidance, and includes the analyst's own estimate.
Counting that note as three separate pieces of evidence would triple-count the same fact.

- Extraction produces **claims**, not document summaries. A claim is one assertion about one
  theme, entity, metric, and period: `claim_id = hash(theme, entity, metric, period, event_date)`.
- Each claim collects every supporting source: `document_id`, `source_role`, `quote`,
  `published_at`, and `derived_from` when the document cites another source.
- A broker note and a news article describing the same catalyst resolve to **one claim** with
  two sources, not two claims.
- A News Analyst finding that matches an existing claim is attached to that claim; it does not
  create a second forecast adjustment.
- Independence is tracked. Two broker notes repeating the same channel check are one signal;
  a broker note quoting a news article is downstream of that article. Independent confirmation
  can raise confidence within a cap; repetition cannot.
- Conflicting sources keep `status: disputed` and show both sides. The engine does not average
  them into a false consensus.
- One claim produces at most one `AppliedAdjustment`, with all its sources listed.

The deterministic engine consumes these records. It never consumes free-form prose as a number.

#### Structured evidence

The LLM may extract, with a schema and citations:

- `GuidanceItem`: metric, period, low/high, currency, source document, quote.
- `EstimateItem`: metric, period, value, analyst/source, date.
- `IndustryMetric`: market size, growth, share, ASP, units, capacity, utilization, inventory, backlog.
- `TranscriptSegment`: speaker, role, section (prepared remarks / Q&A), text.
- `SellSideTakeaway`: rating, target, estimate changes, thesis, source.
- `NewsSignal`: theme, direction, magnitude band, horizon, driver, periods, confidence, source.
- `Claim`: theme, entity, metric, period, status, sources, independence.
- `BrokerEvent`: event type, headline, observed time, document ID, no body text.

Every record points back to a claim, and every claim points back to one or more documents.

#### Inputs from the analyst team

The forecast is not built from documents alone. It combines the four analyst reports, because a
transcript says what management expects while the news and sentiment analysts say what is
changing around that expectation. The News Analyst's own inputs are Bloomberg News, company IR
and exchange releases, public websites and RSS, and user articles.

Broker research is a research-desk source, not a news-feed source. It supplies estimates,
industry data, and event analysis, but the News Analyst does not read the broker document. When a
broker note breaks a catalyst before the news feed covers it, the desk publishes a metadata-only
event pointer (ticker, event type, timestamp, headline, owner `broker`, no body text) and both
the news view and the deal view attach to the same claim.

| Analyst report | What the engine takes from it |
|---|---|
| Fundamentals | statement trends, segment mix, margins, capital intensity, balance sheet |
| News | catalysts, demand and supply events, pricing, competition, policy, tariffs, litigation |
| Sentiment | positioning, crowding, tone, retail/institutional divergence, narrative momentum |
| Market | price/flow context and the current multiple the market is already paying |

The News and Sentiment analysts emit a `NewsSignal` alongside their prose, using a schema:

- `signal_id`, `theme`, `source_report`, `evidence_ids`
- `direction`: `-1` to `+1`
- `magnitude_band`: `low`, `medium`, `high` — a band, never a point estimate
- `horizon`: `next_quarter`, `next_year`, `structural`
- `driver`: one of the named drivers in the model
- `periods`: which forecast periods it may affect
- `confidence`: `0` to `1`

Application is deterministic and bounded:

| Signal theme | Permitted drivers |
|---|---|
| Demand / units | units, revenue growth |
| Pricing / ASP | ASP, gross margin |
| Supply constraints, inventory | units, gross margin, capex |
| Competition / share | market share, ASP |
| Input costs | COGS, gross margin |
| Policy, tariffs, litigation | revenue, margin, capex, risk premium |
| Sentiment / positioning | scenario probabilities, confidence, risk premium |

- A versioned rule table maps `(theme, direction, magnitude_band, confidence)` to a driver delta;
  the LLM classifies into the band, it does not choose the delta at run time.
- Each driver has a cap (for example ±5% of the base driver value or ±200 bps of margin) and the
  model has a cap on the total news effect, so a noisy week cannot rewrite the forecast.
- Sentiment adjusts scenario probabilities and the risk premium, not base-case cash flows, by
  default.
- A theme already reflected in guidance, consensus, or the fundamentals trend is flagged and
  suppressed to avoid double counting. This includes the same catalyst repeated in a broker
  note and a news article.
- No signal, a stale signal, or news disabled means **zero adjustment** plus a visible warning;
  the base case is never silently moved.
- Every applied delta is recorded as an `AppliedAdjustment`: signal ID, driver, base value,
  delta, capped delta, rule version, confidence weight.
- The rules are deterministic and testable; two runs on the same signals produce the same
  adjustments.

#### Corporate actions and deal assessment

An M&A broker report is not a news item and not a normal fundamentals note. It carries deal
terms, a pro forma view, and a valuation opinion. Sending it to the News Analyst produces
commentary; sending it to the Fundamentals Analyst produces a footnote. Neither answers “what is
this deal worth to the acquirer?”

**Routing is by claim, not by document or by analyst:**

| Content in the broker report | Owner |
|---|---|
| Event: talks, bid, signed agreement, regulatory review, closing | News Analyst frames the event from the pointer and news coverage; the deal module owns the event's numbers |
| Deal terms: price, premium, cash/stock mix, financing, conditions, break fees | Deal module |
| Target and acquirer standalone fundamentals | Fundamentals Analyst, unchanged |
| Broker pro forma, accretion/dilution, synergy estimate, target price | Deal module as a sourced `EstimateItem` / `SellSideTakeaway`, reconciled, not copied |
| Arbitrage spread, implied close probability, peer deal multiples | Deal module with the Market Analyst |

The broker report is ingested once into the document store. Its claims fan out to the research
owners; the News Analyst receives only the metadata-only event pointer. Claim-level reconciliation
stops the same deal from being counted as news, as a fundamentals datapoint, and as a broker
estimate.

**Shared evidence, separate outputs.** The deal module and, where relevant, the Fundamentals
Analyst may read the same broker report and form their own views. The News Analyst does not read
the report; it may see only the metadata-only event pointer. What is not allowed is blending
any of these into one unlabeled voice.

- Each analysis names its `owner`: `news`, `fundamentals`, `sentiment`, `market`, or `deal`.
- Each output cites its own `source_claim_ids`, so a reader can see exactly which claims and documents each one used.
- The News Analyst produces event framing, market reaction, and a banded news signal. It does
  **not** produce deal valuation numbers.
- The deal module produces the deal assessment and valuation. It does **not** rewrite the news
  narrative.
- The forecast uses the deal module's numbers. The narrative may use the News Analyst's framing.
  Neither is substituted for the other.
- The package renders the news view and the deal assessment as separate sections. If they
  disagree, the disagreement is shown, not averaged.
- If the two conflict on the same claim, the claim is marked `disputed`; the standalone forecast
  remains the fallback until the deal view passes human review.
- A shared document is still one document: the store, hash, licence, and point-in-time rules are
  identical for every consumer.

**The deal module does the assessment and valuation.** For an announced transaction it adds a
deal overlay to the forecast:

- close probability and expected close date;
- consideration mix and the resulting share count, debt, and interest;
- synergies, integration costs, and transaction costs, each with a source or an explicit
  analyst assumption;
- amortization of acquired intangibles and any purchase-accounting effects that move EPS;
- pro forma revenue, margin, EPS, leverage, and free cash flow;
- **accretion / dilution** versus the standalone case, and break-even synergies;
- standalone value, deal-adjusted value, and a probability-weighted value;
- for a divestiture or spin-off, a sum-of-the-parts view;
- for a public target, the offer premium and the market-implied probability from the spread.

**Scope and guardrails:**

- Announced deals with disclosed terms get a base-case overlay with an explicit close
  probability.
- Rumored or unsigned deals get a scenario only; they do not move the base case.
- Competing bids and unsolicited offers get a scenario tree, not a single probability.
- Cross-border, antitrust, and financing conditions are explicit assumptions with warnings.
- Deal modeling is human-reviewed before it enters the debate; a wrong close probability or
  consideration mix can dominate every other input.
- v1 covers technology/semiconductor deals; other sectors stay `not_rated` for deal work until
  reviewed.

#### Forecast model

- Sector driver pack for the first sector: technology/semiconductors.
- Revenue by segment, for example data center, gaming, professional visualization, automotive, and OEM/other; for broader semis, compute, networking, memory, analog, foundry, and equipment.
- Drivers: AI/cloud capex, unit growth, ASP/pricing, market share, supply constraints (foundry, HBM, advanced packaging), inventory, backlog, gross margin, opex, tax, capex, working capital, and share count.
- Quarterly horizon: next 8 quarters, with seasonality.
- Annual horizon: next 3 years.
- Scenarios: base, bull, and bear.
- The **fundamentals-only build** is the baseline; the base scenario is that baseline plus the applied news and sentiment adjustments, shown as a separate bridge so a reviewer can see the pre-news and post-news forecast side by side.
- Every assumption has a value, a source, and a rationale.
- Missing inputs lower confidence and remain visible; they are not filled with a silent default.

#### Valuation

- Multiples: EV/EBITDA, EV/Sales, P/E, and FCF yield, with a sector-appropriate peer set.
- DCF: FCFF, with explicit WACC, terminal growth, and net-debt bridge; only for stable, positive-FCF companies.
- WACC inputs: risk-free rate, equity risk premium, beta, cost of debt, tax, and capital structure, or an explicit user-supplied WACC.
- Peer groups are separated by business model: fabless, foundry, equipment, and memory are not compared as one group.
- Sensitivity: WACC, terminal growth, margin, and revenue growth.
- Output is a range, not false precision.

#### Research package

- Investment summary and fundamental view: `undervalued`, `fairly_valued`, `overvalued`, or `not_rated`.
- Fair value range: low, mid, high, currency.
- Quarterly and annual forecast tables.
- Valuation tables for multiples and DCF, with base/bull/bear and sensitivity.
- Deal assessments when a corporate action exists, with the standalone/deal/probability-weighted bridge kept separate from the news view.
- Assumptions, evidence citations, data-quality status, warnings, and model version.
- No final 5-tier rating; the Research Manager keeps rating authority.
- Human review is required before the package enters the debate during the pilot.

### 5.12 Pre-debate integration

The package is produced after the analysts and before the Bull/Bear debate:

```text
Analysts
   │  market / sentiment / news / fundamentals reports
   ▼
Fundamental Research
   │  document evidence → forecasts → valuation → report
   ▼
Bull Researcher / Bear Researcher
   │
   ▼
Research Manager → Trader → Risk → Portfolio Manager
```

- New state fields: `research_package` and `fundamental_research_report`.
- The node reads the four analyst reports from state; it does not re-run them or re-fetch their sources.
- If the News or Sentiment Analyst is disabled, the desk delivers the fundamentals-only forecast and labels the missing input instead of inventing a neutral signal.
- Bull/Bear receive a compact package summary and the key forecast/valuation tables.
- Deal assessments travel with the package, and the news view of the same event is shown separately rather than folded into the deal numbers.
- The Research Manager receives the package summary as well, so the forecasts and valuation are not lost in the debate transcript.
- The Trader and Portfolio Manager may receive the fair value range and key assumptions; the full package stays in the report.
- The report tree gains a **Fundamental Research** section before the Research Team Decision.
- The CLI and HTML report display the package and its data-quality labels.
- Vanilla mode has no node, no extra calls, and no extra report section.

A fast prototype may append a compact package summary to `fundamentals_report` so Bull/Bear see it without a graph fork. That is a spike shortcut, not the production design.

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

### Phase 0.5 — Feasibility spikes

**Goal:** de-risk the three unknowns before committing production code: Bloomberg access, user-supplied evidence, and a defensible fundamental model.

**Plain-language outcome:** “We know what data we can actually get, and we have one company’s forecasts and valuation that a PM can read.”

**Deliverables:**

- **Bloomberg access and data-availability matrix.** For last price, previous close, OHLCV history, statements, segments, consensus estimates, industry/peer data, earnings transcripts, broker research, and news, record: API availability, backend, history/as-of support, entitlement, redistribution rights, LLM-use policy, and sample output. Test **Bloomberg News** and **Bloomberg Research** as separate entitlements; a Terminal license for one does not imply the other.
- **News-source registry.** Company IR and exchange releases, licensed Bloomberg news, reputable RSS, and trade press, with tiers, paywall rules, `robots.txt` handling, canonical URL, and `published_at`. The News Analyst must report “Bloomberg news unavailable” rather than silently substituting a lower-tier feed.
- **User-news prototype.** A `data/user_materials/<TICKER>/` layout, a manifest, HTML/PDF/URL ingestion, point-in-time filtering, dedupe, and a news-analyst tool that cites the article.
- **Technology/semiconductor fundamental prototype.** One company, 8 quarters plus 3 years, guidance from one transcript, one sell-side note, one industry data point, multiples and DCF, base/bull/bear, and sensitivity.
- **Go/no-go memo.** The chosen Bloomberg backend, the data types that need user-file fallback, the forecast assumptions that need human review, and the licensing constraints.

**Acceptance criteria:**

- The Bloomberg backend and entitled data types are confirmed, or the blocked types are named explicitly.
- The prototype news report cites a user article, excludes a future-dated article, collapses a duplicate, and rejects a malformed file.
- The prototype research package reproduces its numbers from the stated inputs; accounting identities hold; assumptions are sourced.
- The licensing/LLM-use decision is recorded before any licensed content reaches a model.
- No production dependency is added to the core package for a spike.

**Effort:** S–M.  
**Dependencies:** Bloomberg access for the first spike; none for the other two.

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

### Phase 2A — Bloomberg research data

**Goal:** extend the Bloomberg adapter from market data to the research evidence the fundamental desk needs: transcripts, broker research, industry data, and estimates, where entitled.

**Plain-language outcome:** “The fundamental desk can use Bloomberg for the same evidence a human analyst would open on the Terminal.”

**Deliverables:**

- Capability detection for research data, separate from market-data capability.
- Earnings call and conference transcript retrieval, with publication time and document IDs.
- Broker research / ratings / target-price retrieval where entitled; explicit “not entitled” otherwise.
- Industry classification, peer groups, and industry metrics (BICS/GICS or the firm’s taxonomy).
- Consensus estimates, where entitled.
- Normalization into `DocumentRecord`, `EstimateItem`, and `IndustryMetric`.
- Licence labels: `public`, `internal_only`, `licensed_no_llm`, `licensed_summary_only`.
- Explicit fallback to user materials for anything not API-entitled.
- Integration tests, opt-in and environment-gated.

**Acceptance criteria:**

- Terminal visibility and API entitlement are tested separately; the plan does not assume one implies the other.
- A missing entitlement returns a typed, labelled result, not an empty string.
- Transcripts and broker notes carry source, publication time, and licence.
- Licensed content cannot be sent to an unapproved LLM path.
- The adapter works against a fake research provider on Linux CI.
- No Bloomberg credentials or raw licensed content in logs or reports.

**Effort:** M–L, depending on entitlement.  
**Dependencies:** Phase 0.5; Phase 2; approved licensing/LLM-use policy.

---

### Phase 3 — News and evidence layer

**Goal:** collect Bloomberg news, public web news, RSS, company releases, and user files, with provenance and deduplication.

**Plain-language outcome:** “The evidence desk can explain what happened and where it read it.”

**Deliverables:**

- `NewsProvider` and `EmailProvider` contracts.
- Tiered source registry: company IR/exchange releases, licensed Bloomberg news, reputable RSS, trade press, user articles.
- Bloomberg News as a distinct entitlement from Bloomberg Research.
- Versioned per-site extractors, with `robots.txt`, paywall, and terms checks before a source is enabled.
- Fetch layer with rate limits, retries, caching, and optional headless browser.
- User-provided file ingestion (JSON/Markdown/HTML).
- Source manifest: URL, title, author, published_at, retrieved_at, language, hash, credibility, tier.
- Dedupe and near-duplicate detection.
- Archive policy: extracted text + metadata; raw HTML only if permitted.
- News-enabled / news-disabled profiles wired through agents and reports.
- Prompt-injection safeguards.
- Explicit policy that broker research is not a news provider; only metadata-only event pointers may cross into the news lane.

**Acceptance criteria:**

- Extractor tests use saved HTML fixtures; no live network in CI.
- News disabled produces an explicit “news disabled” state.
- Source unavailable produces “unavailable,” not “no news.”
- Bloomberg news unavailable is labelled, not replaced silently by a lower-tier feed.
- A broker-research provider is not reachable from the News Analyst's tool set.
- A broker event pointer contains ticker, event type, timestamp, and headline, and no licensed body text.
- Duplicate syndicated stories are collapsed.
- User files validate and reject malformed input.
- Every evidence item has provenance and timestamp.

**Effort:** M–L.  
**Dependencies:** Phase 1. Can run in parallel with Phase 2.

---

### Phase 3A — User materials and document store

**Goal:** give the desk a single, safe place to drop any research document under the ticker it belongs to, and make those documents usable as point-in-time evidence.

**Plain-language outcome:** “If I have a call transcript or a broker note, I drop it in the `NVDA` folder and the analysis can use it and cite it.”

**Deliverables:**

- Directory convention with one folder per ticker:
  ```text
  data/user_materials/
    NVDA/
      manifest.json
      news/
      transcripts/
      sell_side/
      industry/
      filings/
      notes/
  ```
- Configurable root via `TRADINGAGENTS_USER_MATERIALS_DIR`; default `./data/user_materials`.
- Safe ticker paths: normalize the symbol, reject `..`, absolute paths, and symlinks that escape the root.
- Ingestion CLI:
  ```bash
  tradingagents-firm ingest --ticker NVDA --type transcript \
    --file ./q4_call.pdf --published 2026-02-26 --source company_ir
  ```
- Manifest validation and write-back; `manifest.json` is the index, the files are read-only originals.
- Extraction for HTML, PDF, Markdown, and text; DOCX/XLSX optional behind an extra.
- Metadata per document: `document_id`, `ticker`, `type`, `source_role`, `contains`, `topics`, `title`, `source`, `author`, `published_at`, `retrieved_at`, `language`, `mime_type`, `pages`, `license`, `confidence`, `content_hash`. A broker note is `type: sell_side` but `contains: [news, estimates, industry, thesis]`.
- Dedupe by content hash and canonical URL; report near-duplicates instead of silently dropping them.
- Point-in-time filter: a document published after the run's `trade_date` is invisible to that run.
- Undated documents are accepted but flagged and excluded from historical runs.
- `ResearchDocumentProvider` implementation over the store, so the engine does not know whether a document came from disk or a vendor.
- Prompt-injection isolation: document text is evidence, never instruction; extracted claims carry a document ID and a quote.

**Acceptance criteria:**

- A PDF, an HTML page, and a Markdown note for one ticker are listed, hashed, and retrievable by type and as-of date.
- A document dated after `trade_date` never appears in a run for that date.
- An undated document is visible for current runs and withheld from historical runs, with a warning.
- A duplicate file is reported, not double-counted.
- A document with `contains` multi-labels is retrievable by each label, not only by `type`.
- A malformed or oversized file is rejected with a typed error.
- Path traversal and ticker-injection attempts are rejected.
- Document text cannot change agent instructions in the injection test.
- Unit tests use fixtures only; no network, no dependency on the user's home.

**Effort:** M.  
**Dependencies:** Phase 1; Phase 3 for HTML extraction patterns.

---

### Phase 3B — Fundamental research engine

**Goal:** turn the evidence into a reproducible forecast and valuation package instead of a prose report.

**Plain-language outcome:** “I get quarterly and annual forecasts, a fair value range, the assumptions behind them, and the sources — before the debate starts.”

**Deliverables:**

- `firm/research/schemas.py`: `DocumentRecord`, `Claim`, `GuidanceItem`, `EstimateItem`, `IndustryMetric`, `TranscriptSegment`, `SellSideTakeaway`, `NewsSignal`, `AppliedAdjustment`, `ForecastModel`, `ForecastPeriod`, `ValuationResult`, `Assumption`, `ResearchPackage`.
- Analyst-report intake: the fundamentals, news, sentiment, and market reports enter the engine as identified inputs, not as re-fetched data.
- News and Sentiment analysts emit typed `NewsSignal` records (theme, direction, magnitude band, horizon, driver, periods, confidence, evidence IDs) alongside their prose.
- Claim reconciliation: a broker note, a news article, and a transcript that describe the same catalyst resolve to one `Claim` with multiple sources and independence labels.
- A versioned rule table maps signals to driver deltas, scenario probabilities, and the risk premium, with per-driver and total-effect caps.
- A fundamentals-only baseline plus a visible adjustment bridge, so news and sentiment never hide inside a single blended number.
- Double-count suppression when a theme is already in guidance, consensus, or the fundamentals trend, including when a broker note and a news item repeat it.
- Evidence extraction: schema-constrained LLM extraction from transcripts, sell-side notes, industry reports, and filings, always with document ID and quote.
- Driver model for the first sector, technology/semiconductors:
  - revenue by segment (data center, gaming, professional visualization, automotive, OEM/other; compute, networking, memory, analog, foundry, equipment for broader coverage);
  - drivers: AI/cloud capex, units, ASP, share, supply constraints, inventory, backlog, gross margin, opex, tax, capex, working capital, share count;
  - quarterly and annual build with explicit seasonality.
- Forecast horizon: 8 quarters and 3 years, base/bull/bear.
- Valuation: EV/EBITDA, EV/Sales, P/E, FCF yield; FCFF DCF with WACC and terminal growth; net-debt bridge; sensitivity grids.
- Peer-set handling by business model, not by broad sector label.
- Assumption register: value, unit, source (document ID or market data), rationale, and whether it was user-supplied.
- Accounting identities and roll-forwards checked in code; a failed check fails the package rather than the narrative.
- Data-quality states: `complete`, `partial`, `degraded`, with explicit warnings for missing data.
- Deterministic tables written to the report folder as CSV/JSON so a PM or reviewer can reproduce them.
- Model version and input hashes recorded in the package.
- Sector guard: banks, insurers, REITs, and biotech return `not_rated` rather than a forced generic model.

**Acceptance criteria:**

- A technology/semiconductor company produces 8 quarters and 3 years of forecasts, multiples, a DCF, a fair value range, and a stance.
- The package names the four analyst reports it consumed and shows the pre-news and post-news forecast side by side.
- A `news_signal` moves only a permitted driver, never an arbitrary line item, and never exceeds its cap.
- A broker note and a news article about the same catalyst produce one claim and one adjustment; the second source raises confidence at most within its cap.
- A source that merely repeats another is marked `derived` and does not count as independent confirmation.
- A disputed claim shows both sides and does not produce a blended adjustment.
- News-disabled or unavailable runs produce a neutral, labelled fundamentals-only forecast; they do not fabricate a signal.
- The same inputs always produce the same outputs; the golden test compares to stored fixtures.
- Accounting identities and roll-forwards hold to the cent/rounding tolerance.
- Every assumption is sourced or explicitly marked as an analyst assumption.
- Sensitivity tables move in the expected direction.
- A missing transcript or missing peer set degrades confidence and appears in warnings; it is never invented.
- The LLM writes narrative only; no forecast or valuation number originates in prose.
- Licensed documents are not sent to an unapproved model path.

**Effort:** L.  
**Dependencies:** Phase 1; Phase 3A; Phase 3 for the news and sentiment reports; Phase 2A or user materials for transcripts and sell-side; approved licensing policy.

---

### Phase 3C — Pre-debate integration and reporting

**Goal:** deliver the research package into the existing debate without disturbing vanilla behavior.

**Plain-language outcome:** “The Bull and Bear argue over a real model, and the Research Manager sees the forecasts and valuation.”

**Deliverables:**

- New `Fundamental Research` node wired after the analysts and before Bull/Bear:
  ```text
  Analysts → Fundamental Research → Bull/Bear → Research Manager → Trader → Risk → PM
  ```
- `AgentState` gains optional `research_package` and `fundamental_research_report`.
- Compact package summary passed to Bull/Bear and the Research Manager; fair value range and key assumptions optionally to Trader and PM.
- Report tree gains a **Fundamental Research** section before the Research Team Decision, with forecasts, valuation, assumptions, citations, and data-quality labels.
- When a corporate action exists, the report adds a **Deal Assessment** subsection with the standalone/deal/probability-weighted bridge and the separate news view.
- CLI and HTML report rendering for the package; CSV/JSON tables linked from the report.
- Human review gate before the package enters the debate during the pilot.
- Profile flag `fundamental_research.enabled`; vanilla mode gets no node, no calls, and no section.
- Compatibility test: a vanilla run is byte-identical to today's output apart from timestamps.

**Acceptance criteria:**

- A run with the profile enabled produces a package, a report section, and a debate that references the forecasts.
- A run with the profile disabled produces no node, no state fields, and no report section.
- The Research Manager's decision cites the package when present, including the news/sentiment adjustment bridge.
- The debate can challenge a news-driven adjustment without re-deriving the forecast.
- A failed or degraded package does not break the run; it is labelled and the debate proceeds on analyst reports.
- The compact summary given to agents is bounded in size and contains no licensed full text.
- Report links resolve and the CSV/JSON tables are present.

**Effort:** M.  
**Dependencies:** Phase 3B; Phase 0 profiling; Phase 8 for PDF/portal rendering.

---

### Phase 3D — Corporate actions and deal assessment

**Goal:** value M&A, divestitures, spin-offs, and capital actions properly, instead of leaving them as news color or an unexplained EPS adjustment.

**Plain-language outcome:** “When a broker note talks about a deal, the desk tells me what the deal is worth, what it does to EPS, and how likely it is to close.”

**Deliverables:**

- `firm/research/deals.py` and `DealAssessment` schema.
- Claim routing: event claims to the News Analyst, deal terms and pro forma claims to the deal module, standalone business to the Fundamentals Analyst.
- Deal overlay on the forecast: close probability, expected close date, consideration mix, new shares/debt/interest, synergies, integration and transaction costs, intangible amortization, pro forma statements.
- Accretion/dilution versus the standalone forecast, with break-even synergies.
- Valuation: standalone value, deal-adjusted value, probability-weighted value; sum-of-the-parts for divestitures and spin-offs; offer premium and market-implied probability for public targets.
- Scenario handling for rumored, unsolicited, competing, and terminated deals.
- Separate outputs: a news view and a deal assessment, each labelled with its `owner` and citing the same claim without merging.
- Human review gate for deal assumptions; close probability and consideration mix are review-critical.
- Technology/semiconductor scope; other sectors `not_rated` for deal work.

**Acceptance criteria:**

- An announced tech/semis deal with disclosed terms produces a deal assessment, an accretion/dilution table, a probability-weighted value, and a report section.
- The same broker report can inform the deal module and the News Analyst's event pointer; the package shows both outputs separately and does not average them.
- A rumored deal appears only in scenarios and does not move the base case.
- Standalone forecast and deal-adjusted forecast reconcile through the deal bridge.
- A terminated deal removes the overlay cleanly and returns to the standalone case.
- Deal numbers are deterministic and reproducible from the recorded assumptions.
- Missing terms are labelled and lower confidence; they are never filled with a silent assumption.
- A deal in an unsupported sector returns `not_rated` with a warning.

**Effort:** L.  
**Dependencies:** Phase 3B; Phase 3C for reporting; licensing policy for broker research.

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
  → news providers: Bloomberg News + company IR + public web/RSS + user articles
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

### 7.7 Fundamental research package

```text
Profile: firm_fundamental_research
  → analysts finish (market / sentiment / news / fundamentals)
  → document store: transcripts + sell-side + industry + filings for NVDA
  → evidence extraction: guidance, estimates, industry metrics, takeaways
  → news/sentiment signals: typed, banded, sourced, capped
  → fundamentals-only baseline
  → driver adjustments + scenario weights + risk premium
  → deterministic forecast: 8 quarters + 3 years, base/bull/bear
  → valuation: multiples + DCF + sensitivity
  → research package: fair value range + fundamental view + adjustment bridge + citations
  → human review approved
  → Bull/Bear debate with the package
  → Research Manager decides the rating
  → report section “Fundamental Research” before the Research Team Decision
```

**What PMs see:** a forecast and valuation they can audit, including how the news and sentiment
findings moved the numbers, and a debate that argues over real numbers instead of prose.

### 7.8 Missing or unentitled research data

```text
Transcript entitlement absent
  → Bloomberg returns “not entitled”
  → provider falls back to data/user_materials/NVDA/transcripts/
  → no transcript found
  → package marked degraded with a warning
  → debate proceeds on analyst reports and the visible gap
```

**What PMs see:** an honest gap, not an invented estimate.

### 7.9 M&A broker report

```text
Broker note: “NVDA to acquire ACME Semiconductor”
  → ingested once into the document store (research-desk only)
  → claims: deal event, deal terms, target fundamentals, synergy estimate, target price
  → metadata-only broker event pointer published to the shared claim store
  → News Analyst: event framing, market reaction, banded news signal (from news/web + pointer)
  → Fundamentals Analyst: standalone NVDA and ACME business trends
  → Deal module: consideration, close probability, synergies, accretion/dilution,
                 standalone vs deal-adjusted vs probability-weighted value
  → package keeps the news view and the deal assessment separate
  → human review: terms, probability, financing
  → Bull/Bear debate; Research Manager decides the rating
```

**What PMs see:** a news view and a deal valuation side by side, with one shared evidence trail
and no blended “house view” that hides who said what.

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

### 8.8 Document record

Every ingested file becomes one record, whether it came from Bloomberg, a broker feed, or a
user folder. The file itself is immutable; only the manifest changes.

```json
{
  "document_id": "doc-NVDA-2026Q4-call-7c1e",
  "ticker": "NVDA",
  "internal_id": "US67066G1040",
  "type": "transcript",
  "subtype": "earnings_call",
  "source_role": "transcript",
  "contains": ["guidance", "transcript"],
  "topics": ["data_center_demand", "supply_constraint"],
  "title": "NVIDIA Q4 FY2026 Earnings Call",
  "source": "bloomberg",
  "source_id": "BBG-...",
  "author": "NVIDIA Corporation",
  "published_at": "2026-02-26T21:00:00Z",
  "retrieved_at": "2026-10-24T09:12:31Z",
  "language": "en",
  "mime_type": "application/pdf",
  "pages": 24,
  "license": "licensed_summary_only",
  "llm_use": "summary_and_quotes",
  "content_hash": "sha256:...",
  "url": "https://...",
  "storage_path": "data/user_materials/NVDA/transcripts/2026Q4_call.pdf",
  "confidence": 0.98,
  "warnings": []
}
```

`type` is one of `news`, `transcript`, `sell_side`, `industry`, `filing`, `note`.
`source_role` is one of `primary_filing`, `transcript`, `broker`, `news`, `industry`, `user`.
`contains` is multi-label and may repeat: a broker note is typically
`["news", "estimates", "industry", "thesis"]`. `type` says what the file is; `contains` says
what can be mined from it. `license` is one of `public`, `internal_only`, `licensed_no_llm`,
`licensed_summary_only`.
A record whose `published_at` is later than the run's `trade_date` is invisible to that run.

### 8.9 Structured evidence

The model never reads a number out of prose. Extraction produces typed records, each tied to
a document and a quote.

```json
{
  "evidence_id": "ev-NVDA-guid-q1fy27-rev",
  "kind": "guidance",
  "claim_id": "clm-NVDA-guid-q1fy27-rev",
  "ticker": "NVDA",
  "metric": "revenue",
  "period": "2026Q1",
  "period_type": "quarter",
  "low": 78.0,
  "mid": 80.0,
  "high": 82.0,
  "unit": "USD_bn",
  "document_id": "doc-NVDA-2026Q4-call-7c1e",
  "quote": "We expect first-quarter revenue of $78.0 billion, plus or minus 2%.",
  "speaker": "Chief Financial Officer",
  "extracted_at": "2026-10-24T09:15:02Z",
  "extractor": "guidance_v1",
  "confidence": 0.95
}
```

Companion records share the same envelope with `kind` set to:

| `kind` | Key fields |
|---|---|
| `estimate` | `metric`, `period`, `value`, `analyst`, `source_date` |
| `industry_metric` | `metric`, `period`, `value`, `unit`, `geography` |
| `transcript_segment` | `speaker`, `role`, `section` (`prepared_remarks`/`qa`), `text` |
| `sell_side_takeaway` | `broker`, `rating`, `target_price`, `estimate_changes`, `thesis` |
| `news_signal` | `theme`, `direction`, `magnitude_band`, `horizon`, `driver`, `periods`, `source_report`, `evidence_ids` |
| `catalyst` | `event`, `expected_date`, `probability`, `affected_drivers`, `evidence_ids` |
| `claim` | `theme`, `entity`, `metric`, `period`, `status`, `sources`, `weight_basis` |
| `broker_event` | `event_type`, `headline`, `observed_at`, `document_id`, `no_body_text` |

A `news_signal` is how the News and Sentiment analysts enter the model. It is a banded,
sourced claim, not a number:

```json
{
  "evidence_id": "ev-NVDA-news-dc-supply",
  "kind": "news_signal",
  "claim_id": "clm-NVDA-dc-supply-2026h1",
  "ticker": "NVDA",
  "theme": "supply_constraint_easing",
  "direction": 0.6,
  "magnitude_band": "medium",
  "horizon": "next_year",
  "driver": "data_center_units",
  "periods": ["2026Q1", "2026Q2", "2026Q3", "2026Q4"],
  "source_report": "news",
  "owner": "news",
  "sources": [
    {"document_id": "news-...", "source_role": "news"}
  ],
  "quote": "Supply is expected to improve through the first half.",
  "confidence": 0.55,
  "extracted_at": "2026-10-24T09:20:11Z",
  "extractor": "news_signal_v1"
}
```

`magnitude_band` is `low`, `medium`, or `high`; `direction` is a sign and strength, not a
percent change. The rule table, not the analyst, decides how many basis points a `medium`
signal is worth.

A **claim** is the shared object that prevents the broker note, the news article, and the
transcript from counting three times:

```json
{
  "claim_id": "clm-NVDA-dc-supply-2026h1",
  "ticker": "NVDA",
  "theme": "supply_constraint_easing",
  "entity": "data_center",
  "metric": "units",
  "period": "2026H1",
  "status": "confirmed",
  "sources": [
    {"document_id": "doc-NVDA-2026Q4-call-7c1e", "source_role": "transcript", "independence": "primary"},
    {"document_id": "doc-NVDA-broker-2026-02-27", "source_role": "broker", "independence": "independent", "derived_from": null},
    {"document_id": "news-...", "source_role": "news", "independence": "derived", "derived_from": "doc-NVDA-broker-2026-02-27"}
  ],
  "weight_basis": "2 independent sources",
  "first_seen": "2026-02-26T21:00:00Z",
  "applied_adjustment": "adj-NVDA-dc-units-2026h1"
}
```

`status` is `unconfirmed`, `confirmed`, or `disputed`. `independence` is `primary`,
`independent`, or `derived`; a source that merely repeats another is not a second confirmation.
The claim, not the document, is the unit that moves the forecast.

A **broker event pointer** is the only broker content the news lane may see. It is metadata, not
a document:

```json
{
  "evidence_id": "ev-NVDA-broker-acme-bid",
  "kind": "broker_event",
  "claim_id": "clm-NVDA-acme-bid",
  "ticker": "NVDA",
  "owner": "broker",
  "event_type": "m_and_a",
  "headline": "Broker reports NVDA bid for ACME Semiconductor",
  "observed_at": "2026-09-14T12:05:00Z",
  "document_id": "doc-NVDA-broker-2026-09-15",
  "license": "licensed_summary_only",
  "no_body_text": true
}
```

The News Analyst may cite the pointer as “broker-sourced event” and must not quote it. If the
news feed later covers the same event, both attach to the same claim.

### 8.10 Forecast model

The forecast is a deterministic artifact. Re-running the same inputs and model version must
produce the same table.

```json
{
  "model_id": "tech_semis_driver_v1",
  "model_version": "1.0.0",
  "ticker": "NVDA",
  "as_of": "2026-10-25",
  "currency": "USD",
  "scenario": "base",
  "periods": [
    {
      "period": "2026Q1",
      "period_type": "quarter",
      "revenue": 80.4,
      "gross_profit": 60.1,
      "gross_margin": 0.747,
      "operating_income": 48.9,
      "net_income": 41.2,
      "eps_diluted": 1.68,
      "capex": 2.1,
      "free_cash_flow": 39.8,
      "share_count_diluted": 24.5
    }
  ],
  "drivers": {
    "data_center_revenue_growth": 0.18,
    "gaming_revenue_growth": -0.05,
    "gross_margin": 0.747,
    "opex_growth": 0.09,
    "tax_rate": 0.14
  },
  "assumptions": [
    {
      "key": "data_center_revenue_growth",
      "value": 0.18,
      "unit": "ratio",
      "source": "ev-NVDA-sellside-dc-growth",
      "rationale": "Consensus plus management commentary on supply easing",
      "origin": "evidence"
    }
  ],
  "analyst_inputs": {
    "fundamentals_report": {"id": "rep-NVDA-fundamentals", "hash": "sha256:..."},
    "news_report": {"id": "rep-NVDA-news", "hash": "sha256:..."},
    "sentiment_report": {"id": "rep-NVDA-sentiment", "hash": "sha256:..."},
    "market_report": {"id": "rep-NVDA-market", "hash": "sha256:..."}
  },
  "applied_adjustments": [
    {
      "signal_id": "ev-NVDA-news-dc-supply",
      "driver": "data_center_units",
      "base_value": 0.12,
      "delta": 0.025,
      "capped_delta": 0.025,
      "rule_version": "news_rules_v1",
      "confidence_weight": 0.55,
      "source_report": "news"
    }
  ],
  "input_hashes": {"statements": "sha256:...", "evidence": "sha256:..."},
  "warnings": []
}
```

`origin` is `evidence`, `market_data`, or `analyst`. An `analyst` assumption is allowed but is
always visible in the report.

The period table is the post-adjustment forecast. `applied_adjustments` is the audit trail from
the fundamentals-only build to the final base case; the package also carries the pre-adjustment
table so the effect of news and sentiment is visible on its own.

### 8.11 Valuation result

```json
{
  "valuation_id": "val-NVDA-20261025-base",
  "ticker": "NVDA",
  "as_of": "2026-10-25",
  "scenario": "base",
  "methods": {
    "multiples": {
      "ev_ebitda": {"peer_median": 28.4, "applied": 27.0, "implied_price": 191.0},
      "ev_sales": {"peer_median": 12.1, "applied": 11.5, "implied_price": 186.0},
      "pe": {"peer_median": 34.2, "applied": 32.0, "implied_price": 188.0}
    },
    "dcf": {
      "wacc": 0.089,
      "terminal_growth": 0.03,
      "enterprise_value": 4520.0,
      "equity_value": 4488.0,
      "implied_price": 183.0
    }
  },
  "fair_value_range": {"low": 176.0, "mid": 187.0, "high": 199.0, "currency": "USD"},
  "current_price": 182.4,
  "fundamental_view": "fairly_valued",
  "sensitivity": {
    "dcf_wacc": [{"wacc": 0.079, "price": 205.0}, {"wacc": 0.099, "price": 165.0}],
    "dcf_terminal_growth": [{"growth": 0.02, "price": 171.0}, {"growth": 0.04, "price": 197.0}]
  },
  "method_weights": {"multiples": 0.5, "dcf": 0.5},
  "warnings": ["DCF sensitive to terminal growth above 4%"]
}
```

`fundamental_view` is `undervalued`, `fairly_valued`, `overvalued`, or `not_rated`. There is no
five-tier rating in this record; rating authority stays with the Research Manager.

### 8.12 Research package

The package is what the debate receives and what the report renders.

```json
{
  "package_id": "pkg-NVDA-20261025",
  "ticker": "NVDA",
  "as_of": "2026-10-25",
  "summary": "...",
  "fundamental_view": "fairly_valued",
  "fair_value_range": {"low": 176.0, "mid": 187.0, "high": 199.0, "currency": "USD"},
  "current_price": 182.4,
  "forecasts": {"base": "fc-NVDA-base", "bull": "fc-NVDA-bull", "bear": "fc-NVDA-bear"},
  "valuation": "val-NVDA-20261025-base",
  "key_assumptions": ["..."],
  "evidence": ["doc-NVDA-2026Q4-call-7c1e", "doc-NVDA-2026-10-sellside-1"],
  "analyst_inputs": {
    "fundamentals": "rep-NVDA-fundamentals",
    "news": "rep-NVDA-news",
    "sentiment": "rep-NVDA-sentiment",
    "market": "rep-NVDA-market"
  },
  "deal_assessments": ["deal-NVDA-2026-acme"],
  "separate_views": {
    "news": {"report": "rep-NVDA-news", "owner": "news"},
    "deal": {"assessment": "deal-NVDA-2026-acme", "owner": "deal"}
  },
  "news_impact": {
    "applied_signals": 3,
    "claims": 12,
    "documents": 9,
    "net_driver_effect": {"data_center_units": 0.025, "gross_margin": -0.004},
    "scenario_probabilities": {"base": 0.55, "bull": 0.25, "bear": 0.20}
  },
  "data_quality": "partial",
  "warnings": ["No industry utilization data for the quarter"],
  "model_versions": {"forecast": "tech_semis_driver_v1@1.0.0", "valuation": "val_v1@1.0.0", "news_rules": "news_rules_v1@1.0.0", "deal": "deal_v1@1.0.0"},
  "review": {"required": true, "status": "approved", "reviewer": "analyst-1", "reviewed_at": "2026-10-25T08:40:00Z"},
  "tables": {
    "quarterly": "forecast_quarterly.csv",
    "annual": "forecast_annual.csv",
    "valuation": "valuation.csv",
    "adjustment_bridge": "forecast_bridge.csv",
    "deal_bridge": "deal_bridge.csv"
  }
}
```

`data_quality` is `complete`, `partial`, or `degraded`. A `degraded` package is still delivered,
clearly labelled, and never silently upgraded to `complete`.

`analyst_inputs` makes the combination explicit: every package names the four analyst reports it
consumed. `news_impact` shows how much the news and sentiment signals moved the forecast and the
scenario weights, and `adjustment_bridge` carries the pre-news and post-news tables so a reviewer
can separate the fundamentals view from the news view.

`separate_views` is the no-mix-up rule in data form. When a broker note or a corporate action is
analyzed by more than one owner, the package keeps each output distinct, with its own `owner` and
its own claims. The package may contain a news view and a deal assessment of the same document;
it must not contain a single blended view that hides which one supplied a number.

### 8.13 Deal assessment

A corporate action gets its own record so the deal overlay is auditable and never hides inside
the forecast table.

```json
{
  "deal_id": "deal-NVDA-2026-acme",
  "ticker": "NVDA",
  "owner": "deal",
  "role": "acquirer",
  "deal_type": "acquisition",
  "counterparty": "ACME Semiconductor",
  "status": "announced",
  "announced_at": "2026-09-14T12:00:00Z",
  "consideration": {"cash": 6.0, "stock": 18.0, "unit": "USD_bn", "new_shares_m": 95.0},
  "premium_pct": 0.32,
  "financing": {"new_debt": 6.0, "interest_rate": 0.052},
  "synergies": {"run_rate": 1.2, "phase_in_years": 2, "source": "ev-NVDA-deal-synergy"},
  "integration_costs": {"one_off": 0.8, "source": "analyst"},
  "close_probability": 0.75,
  "expected_close": "2027Q1",
  "conditions": ["HSR", "China SAMR", "shareholder vote"],
  "accretion_dilution": [
    {"period": "2027", "eps_accretion_pct": -0.03, "break_even_synergies": 1.6}
  ],
  "pro_forma": {"revenue": 118.0, "eps": 4.12, "net_debt": 14.0, "period": "2027"},
  "valuation_impact": {
    "standalone_value": 187.0,
    "deal_adjusted_value": 193.0,
    "probability_weighted_value": 191.5,
    "currency": "USD"
  },
  "sources": ["doc-NVDA-broker-2026-09-15", "doc-NVDA-8k-2026-09-14"],
  "source_claim_ids": ["clm-NVDA-acme-bid", "clm-NVDA-acme-synergies"],
  "warnings": ["Synergy phase-in assumed linear; not company-guided"],
  "review": {"required": true, "status": "approved", "reviewer": "analyst-1"}
}
```

`deal_type` is `acquisition`, `merger`, `tender_offer`, `divestiture`, `spin_off`,
`capital_raise`, `buyback`, or `restructuring`. `status` is `rumored`, `announced`, `pending`,
`closed`, or `terminated`. A `rumored` deal appears in scenarios only, never in the base case.

`accretion_dilution` requires the standalone forecast as its denominator, so the deal record
always points back to a `ForecastModel` and a standalone valuation.

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
    # Broker research is a research-document source, never a news provider.
    providers: [bloomberg_news, public_web, company_ir, user_materials]
    fallback: [alpha_vantage]
    broker_event_pointers: metadata_only
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

bloomberg:
  backend: blpapi            # blpapi | bpipe | data_license
  entitlements:
    market_data: true
    fundamentals: true
    estimates: false
    industry: true
    transcripts: true
    broker_research: false
    news: true
  llm_policy:
    licensed_no_llm: blocked
    licensed_summary_only: summary_and_quotes

user_materials:
  root: ./data/user_materials   # or TRADINGAGENTS_USER_MATERIALS_DIR
  require_published_at: true
  allowed_types: [news, transcript, sell_side, industry, filing, note]
  extractors: [html, pdf, markdown, text]
  max_file_mb: 50

fundamental_research:
  enabled: false             # true under the firm_fundamental_research profile
  sectors: [technology, semiconductors]
  exclude_sectors: [banks, insurers, reits, biotech]
  forecast_quarters: 8
  forecast_years: 3
  scenarios: [base, bull, bear]
  valuation_methods: [multiples, dcf]
  dcf_requires_positive_fcf: true
  peer_set: sector_business_model
  require_human_review: true
  pass_to_agents: [bull_researcher, bear_researcher, research_manager]

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
- Bloomberg news and Bloomberg research are separate entitlements; each needs its own LLM-use decision.
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

### 10.6 Licensed research and transcripts

Broker research, transcripts, and industry reports are licensed content. They are treated
differently from public news.

- Every document carries a `license` label: `public`, `internal_only`, `licensed_no_llm`, or
  `licensed_summary_only`.
- `licensed_no_llm` content is never sent to any model. It may be stored, searched locally, and
  cited by title and metadata, but not quoted or summarized by an LLM.
- `licensed_summary_only` content may be summarized and briefly quoted, with the source named.
- Licensed full text stays inside the firm boundary and is never written to a report, log, or
  prompt that leaves it.
- The research package stores document IDs and quotes, not the licensed documents themselves.
- A broker event pointer is metadata only and inherits the broker document's licence; it never carries licensed body text into a news prompt, report, or log.
- Bloomberg, broker, and transcript entitlements are checked per data type; Terminal visibility
  does not imply API or redistribution rights.
- Vendor and broker terms are reviewed before the first licensed document is ingested.
- Access to licensed documents is role-restricted and logged.
- Retention follows the licence, not the report retention policy, when the licence is stricter.

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

### 11.6 Fundamental research

- **Document store:** fixture HTML, PDF, Markdown, and malformed files; path-traversal and
  ticker-injection attempts; dedupe by hash; oversized-file rejection.
- **Point-in-time:** a document dated after `trade_date` is invisible; an undated document is
  withheld from historical runs with a warning; a current run sees both.
- **Licensing:** `licensed_no_llm` content never reaches a model call — assert on the prompt
  payload, not on the model's output.
- **Extraction:** guidance, estimate, industry, transcript, and sell-side fixtures produce the
  expected typed records with document IDs and quotes.
- **News-to-forecast mapping:** a `news_signal` fixture moves only its permitted driver; the
  applied delta matches the rule table; per-driver and total caps are enforced; an unknown theme
  is rejected rather than applied.
- **Double counting:** a theme already in guidance or consensus produces no second adjustment.
- **Claim reconciliation:** a broker note, a news article, and a transcript describing the same
  catalyst resolve to one claim with three sources; the news article marked `derived` from the
  broker note does not add independent confirmation; the forecast is adjusted once.
- **Broker-note fan-out:** one broker note tagged `contains: [news, estimates, industry, thesis]`
  feeds each extraction path without creating duplicate claims for the same assertion.
- **News-lane gate:** the News Analyst's tool list contains no broker-research provider, and an
  attempted call fails at the tool boundary rather than returning licensed text.
- **Event pointer:** a broker event pointer carries ticker, event type, timestamp, and headline
  only; a test asserts the payload contains no body text or quote from the broker document.
- **News disabled or unavailable:** the forecast equals the fundamentals-only baseline, the
  warning is present, and no signal is invented.
- **Bridge:** the pre-news and post-news tables reconcile, and every applied adjustment appears
  in the bridge with its rule version and confidence weight.
- **Sentiment scope:** sentiment changes scenario probabilities and the risk premium, not
  base-case cash flows, unless the rule table explicitly says otherwise.
- **Forecast:** golden tests compare the full table to stored fixtures for the same inputs and
  model version; accounting identities and roll-forwards hold; missing inputs degrade quality
  rather than defaulting.
- **Valuation:** multiples and DCF recompute from the forecast fixtures; the net-debt bridge
  reconciles; sensitivity tables move in the expected direction; DCF is refused for negative
  or unstable FCF.
- **Sector guard:** banks, insurers, REITs, and biotech return `not_rated`, not a generic model.
- **Determinism:** two runs on the same inputs produce identical tables and hashes.
- **Integration:** a fake package flows to Bull/Bear and the Research Manager; a degraded
  package does not break the run; vanilla runs gain no node, state, calls, or section.
- **End-to-end:** a technology/semis ticker produces a package, report section, and CSV/JSON
  tables from fixtures only; no live Bloomberg or network call in CI.
- **Live Bloomberg:** opt-in, environment-gated integration tests only; skipped by default.

### 11.7 Corporate actions and deals

- **Routing:** an M&A broker note produces a metadata-only event pointer for the News Analyst and
  a deal-terms claim for the deal module; neither consumes the other's output as its own.
- **Separation:** the same document can produce a news view and a deal assessment; the package
  keeps two outputs with distinct `owner` values, and no test path merges them.
- **News-lane isolation:** the News Analyst never receives the broker document; the pointer payload
  contains no body text.
- **Accretion/dilution:** a known deal fixture reproduces the standalone and pro forma EPS; the
  delta ties to the deal bridge; break-even synergies solve correctly.
- **Probability weighting:** a change in close probability moves the probability-weighted value
  in the expected direction and never alters the standalone value.
- **Scenarios:** a rumored deal leaves the base case unchanged; an announced deal with terms
  moves it; a terminated deal returns to standalone exactly.
- **Consideration mix:** cash, stock, and mixed consideration each update share count, debt, and
  interest correctly.
- **Missing terms:** an announced deal missing a term is `degraded`, not silently filled.
- **Sector guard:** a bank or REIT deal returns `not_rated` in v1.
- **Human review:** an unreviewed deal assessment never enters the debate.

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

### Step 2A — Fundamental research shadow

- One technology/semiconductor ticker, one or two analysts reviewing.
- Run the research package without letting it enter the debate; compare with the analyst's own
  numbers.
- Review assumptions, missing data, and the fair value range.
- Enable human review, then let the package feed the debate for a small set of names.
- Do not expand to a second sector until the first sector's model is reviewed.

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
- Forecast error versus actuals after the quarter closes (revenue, EPS, margin).
- Share of packages with sourced assumptions.
- Share of packages marked `complete` / `partial` / `degraded`.
- Reviewer time per package and override rate.

Targets should be set after the pilot baseline, not guessed now.

---

## 14. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Bloomberg entitlement unclear | Build backend-neutral provider; confirm access before Phase 2. |
| Bloomberg Terminal access mistaken for API entitlement | Test each data type separately in Phase 0.5; fail explicitly when not entitled. |
| Broker research and transcripts cannot go to an LLM | Licence labels and an approved LLM policy; `licensed_no_llm` is blocked at the prompt boundary. |
| Licensed content leaks into reports or logs | Store document IDs and short quotes only; scan report output for licensed full text. |
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
| Forecast model overfits or is not understood | Explicit drivers, sourced assumptions, base/bull/bear, sensitivity, and human review. |
| DCF misapplied to unstable or negative-FCF companies | Refuse DCF, fall back to multiples, and label the method gap. |
| Wrong peer set produces misleading multiples | Peer groups by business model, not broad sector; show peer list in the report. |
| Point-in-time leakage from documents | `published_at` filter, undated-document exclusion for historical runs, and look-ahead tests. |
| Sector mismatch | `not_rated` for banks, insurers, REITs, and biotech in v1. |
| Users drop the wrong file or a duplicate into a ticker folder | Manifest validation, hash dedupe, type checks, and a visible warning. |
| Forecast numbers drift between runs | Deterministic engine, pinned model version, input hashes, golden tests. |
| News and sentiment overwhelm the fundamentals | Banded signals, a versioned rule table, per-driver and total caps, and a fundamentals-only baseline. |
| Same theme counted twice (guidance + news + sell-side) | Theme-level dedupe and double-count suppression with a recorded reason. |
| A broker note, a news article, and a transcript describe one catalyst | Claim-level reconciliation, `derived_from` and independence labels, one adjustment per claim. |
| Broker notes treated as independent corroboration when they all repeat one source | Independence is `derived` unless the note shows its own work; confidence caps. |
| A news signal is applied to the wrong line item | Closed driver list, theme→driver mapping, and a test that rejects unknown themes. |
| Sentiment treated as a cash-flow input | Sentiment adjusts scenario weights and the risk premium by default, not base-case cash flows. |
| Analysts disagree and the package hides it | The bridge shows each report's contribution; conflicts are surfaced as warnings, not averaged away. |
| News view and deal view get blended into one voice | Separate `owner`-labelled outputs, `separate_views` in the package, no averaging, and a test that asserts distinct outputs. |
| Deal terms misread from a broker note | Terms come from the filing or the announced release where possible; broker terms are labelled and reviewed. |
| Close probability dominates the valuation | Explicit probability with review, a sensitivity range, and a rumored-vs-announced rule. |
| Deal overlay hides the standalone business | Always compute standalone first and publish the deal bridge. |
| Corporate action in an unsupported sector | `not_rated` for deal work outside technology/semiconductors in v1. |

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
- Bloomberg News and public web/user articles are the News Analyst's inputs; broker research is a research-document source, not a news provider.
- Broker research may publish metadata-only event pointers into the news lane; full broker text never reaches the News Analyst.
- Outlook is the first email transport; Graph is a future adapter.
- Per-user Outlook agents are preferred for individual inboxes; central delegate access is a pilot fallback.
- Attach the report and include a link.
- Real-time alert triggers fail closed on stale data.
- Signals are deterministic; the LLM explains.
- Fundamental research is a deterministic engine with an LLM narrative; the LLM never computes
  a forecast or valuation number.
- The first fundamental sector is technology/semiconductors; banks, insurers, REITs, and biotech
  are `not_rated` in v1.
- Forecasts cover 8 quarters and 3 years, in base/bull/bear scenarios.
- Valuation uses multiples and DCF (FCFF), with DCF only for stable positive-FCF companies.
- The engine outputs a fair value range and a fundamental view; **no five-tier rating**. The
  Research Manager keeps rating authority.
- The research package is produced after the analysts and before the Bull/Bear debate.
- The forecast combines the fundamentals, news, sentiment, and market analyst reports; documents alone are not enough.
- News and sentiment enter as typed, banded signals applied through a versioned rule table with caps, never as free prose into the model.
- Broker research is treated as both a fundamentals source and a news source; the same catalyst is reconciled at the claim level, not counted per document.
- Corporate actions are routed by claim: the News Analyst frames the event, the deal module owns the assessment and valuation.
- The same document may be analyzed by more than one owner, but outputs stay separate and labelled; nothing is averaged into one house view.
- The fundamentals-only forecast is kept as a baseline and the news/sentiment adjustment is shown as a bridge.
- User-supplied material lives under `data/user_materials/<TICKER>/` with a manifest.
- Licensed research carries a licence label and follows an approved LLM-use policy.

### Still to decide

1. **Bloomberg route for the pilot:** BLPAPI, B-PIPE, or Data License? Not confirmed; this is the
   Phase 0.5 gate.
2. **Bloomberg entitlements by data type:** which of market data, statements, estimates,
   industry, transcripts, broker research, and news are actually API-entitled.
3. **Licensed-content LLM policy:** which documents may be summarized, which may only be stored,
   and which provider/region is approved to process them.
4. **Inbox access model:** per-user local agents or central delegate access?
5. **Approved public news sources:** which sites, RSS feeds, languages, and paywall rules?
6. **User-provided files:** article content, source URL lists, PM notes, or all three?
7. **Deployment:** single Windows desktop or central Linux service plus Windows gateway?
8. **LLM provider and data-retention terms** for public news and internal research.
9. **PDF at launch:** required, or is HTML attachment acceptable for the pilot?
10. **Alert policy:** one deep analysis per ticker per day, or one per cooldown window?
11. **Signal role:** advisory only at launch (recommended), or configurable gate later?
12. **Consensus estimates source:** Bloomberg, user files, or a separate vendor?
13. **WACC source:** computed from market inputs, a firm-standard assumption, or analyst-supplied
    per name?
14. **Human review gate:** required for every package in the pilot, or only for degraded packages?
15. **Package recipients:** Bull/Bear and Research Manager only (recommended), or Trader and PM too?
16. **Retention and access:** how long are alerts, reports, emails, source evidence, and licensed
    documents kept, and who can see them?
17. **Signal producer:** do the News and Sentiment analysts emit `NewsSignal` directly through a
    schema, or does a separate extractor read their prose? Direct emission is easier to audit.
18. **Signal weight and caps:** how many basis points is a `medium` signal worth, and what is the
    total cap on news-driven change to the base case?
19. **Sentiment scope:** scenario weights and risk premium only (recommended), or also a small
    direct adjustment to the base case?
20. **Disagreement policy:** when the News Analyst is bullish and the Fundamentals Analyst is
    bearish, does the package show both sides (recommended) or produce a single net signal?
21. **Broker research for the News Analyst:** may the News Analyst read licensed broker research,
    or only the unlicensed news feeds, with broker content reserved for the research desk?
22. **Independence rule:** how do we decide that a broker note is independent rather than
    repeating a news story, a transcript, or another broker? Manual tagging, disclosure parsing,
    or a default of `derived`?
23. **Deal scope in v1:** announced tech/semis deals only (recommended), or also rumored deals,
    divestitures, spin-offs, and capital raises?
24. **Close-probability source:** market-implied from the arbitrage spread, management/merger
    agreement terms, or analyst judgment?
25. **Deal ownership:** does the deal module sit inside the fundamental research desk for v1, or
    become a separate service with its own review?
26. **Conflict presentation:** when the news view and the deal assessment disagree, does the
    package state a preferred view, or present both to the Research Manager without a
    recommendation?

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
- A technology/semiconductor ticker produces a research package with 8 quarters, 3 years,
  multiples, DCF, a fair value range, and a fundamental view.
- The package reproduces from its stated inputs, assumptions are sourced, and accounting
  identities hold.
- The package names the four analyst reports it consumed and shows the fundamentals-only baseline
  beside the news-adjusted forecast.
- A catalyst reported by both a broker note and a news article appears as one claim with two
  sources and produces one adjustment.
- An M&A broker note produces a news view and a deal assessment as separate, owner-labelled
  outputs; the package does not blend them.
- An announced deal shows standalone value, deal-adjusted value, probability-weighted value, and
  accretion/dilution, and a terminated deal returns to standalone.
- A news or sentiment signal can be traced from report → typed signal → applied delta → forecast
  period → valuation, and never exceeds its cap.
- Disagreement between the news and fundamental views is visible, not averaged away.
- The report has a Fundamental Research section before the Research Team Decision, with
  forecast and valuation tables and citations.
- Bull/Bear and the Research Manager receive the package summary; vanilla mode gains nothing.
- Documents dropped into `data/user_materials/<TICKER>/` are listed, cited, and filtered by
  `published_at`.
- Licensed research is labelled and never sent to an unapproved model path.

### For production

- Provider contracts and Bloomberg entitlement are validated.
- Security/privacy/legal reviews are complete.
- Access control, retention, and audit are enforced.
- Operational monitoring and runbooks exist.
- Model cards and versioning are in place.
- Signal and alert evaluation has been reviewed.
- Rollback to vanilla is tested.
- PM acceptance tests pass with real users and representative tickers.
- Forecast and valuation models have an owner, a model card, and a review cadence.
- Licensed-data terms, retention, and LLM-use policy are documented and enforced.
- The fundamental model covers more than one sector, or the `not_rated` guard is accepted as
  the long-term boundary.

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
   - data-provider registration, including a research-document provider
   - analysis/event context
   - optional signal/analysis extension node
   - optional pre-debate research node and `research_package` state
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
- **Document store:** ticker-keyed, point-in-time collection of transcripts, sell-side notes,
  industry reports, filings, and user notes.
- **Structured evidence:** typed records (guidance, estimates, industry metrics, transcript
  segments, sell-side takeaways) with a document ID and quote.
- **News signal:** a banded, sourced record from the News or Sentiment Analyst that names a
  theme, direction, horizon, and permitted forecast driver.
- **Claim:** one assertion about a theme, entity, metric, and period, with every supporting
  source attached; the unit the forecast adjusts.
- **Source role:** `primary_filing`, `transcript`, `broker`, `news`, `industry`, or `user`.
- **Independence:** whether a source does its own work (`independent`), is the origin
  (`primary`), or merely repeats another source (`derived`).
- **Owner:** the analyst or module responsible for an output — `news`, `fundamentals`,
  `sentiment`, `market`, or `deal`.
- **Separate views:** two analyses of the same evidence kept distinct by owner rather than
  merged into one voice.
- **Deal assessment:** the module's structured record for an M&A, divestiture, spin-off, or
  capital action.
- **Accretion / dilution:** the change in EPS versus the standalone forecast caused by a deal.
- **Deal bridge:** the reconciliation from standalone value to deal-adjusted and
  probability-weighted value.
- **Adjustment bridge:** the audit table from the fundamentals-only baseline to the
  news/sentiment-adjusted forecast.
- **Fundamentals-only baseline:** the forecast before any news or sentiment adjustment.
- **Forecast model:** deterministic quarterly/annual build with explicit drivers and scenarios.
- **Valuation:** multiples and DCF applied to the forecast, with a fair value range.
- **Research package:** report + forecasts + valuation + assumptions + citations, produced
  before the Bull/Bear debate.
- **Fundamental view:** `undervalued`, `fairly_valued`, `overvalued`, or `not_rated`; not a
  five-tier rating.
- **WACC:** weighted average cost of capital used in the DCF.
- **Licence label:** `public`, `internal_only`, `licensed_no_llm`, or `licensed_summary_only`,
  controlling how a document may be used and quoted.

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
| Earnings call transcripts | Not implemented | Bloomberg entitlement or user materials; never assume Terminal access means API access. |
| Broker research / sell-side notes | Not implemented | Licensed only; label and restrict LLM use. |
| Industry data / peer sets | Partial (sector labels only) | Add a provider and a business-model-aware peer set. |
| Document store | Not implemented | `data/user_materials/<TICKER>/` plus a manifest and extraction. |
| Forecast model | Not implemented | Deterministic engine; no LLM-computed numbers. |
| Valuation (multiples / DCF) | Not implemented | Multiples first, DCF only for stable positive-FCF names. |
| Research package / pre-debate node | Not implemented | Optional state field and node; disabled in vanilla. |
| Execution | Not implemented | Separate OMS/broker project with controls; not an analyst feature. |
| “OpenAI Deep Thinking” | Multiple LLM providers | Provider abstraction already exists. |

The diagram’s four data boxes should not be read as one shared pipeline. Each analyst has its own tool set; see `ANALYST_WORKFLOWS.md`.

“Can connect” means the extension seams exist. “Connected” means an adapter, credentials, licensing, tests, and operational support exist. The current code has the former, not the latter, for Bloomberg, X, CoinGecko, Finnhub, and Reuters.

Execution is a separate product boundary. TradingAgents produces a decision, not an order. Any execution integration needs order state, pre-trade compliance, entitlements, audit, and broker/OMS operations.
