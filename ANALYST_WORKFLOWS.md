# ANALYST_WORKFLOWS.md — Analysts, Research Team, and Trader

**Project:** TradingAgents 0.7.0  
**Purpose:** explain how the four analysts, the bull/bear research team, the Research Manager, and the Trader actually work — their inputs, tools, data sources, prompts, algorithms, outputs, and limitations.  
**Audience:** PMs, analysts, product owners, and engineers  
**Scope:** understanding the current system. This document does not propose new wrappers or plugins.

> **One-paragraph summary for PMs**
>
> The four analysts are not four fixed formulas. They are four specialist AI workers with different tools and data sources. The **Market/Technical Analyst** reads daily prices and technical indicators. The **Fundamentals Analyst** reads company profiles, financial statements, and insider transactions. The **News Analyst** reads company news, global/macro news, macro data, and prediction markets. The **Sentiment Analyst** reads news, StockTwits, and Reddit, then produces a structured sentiment score.
>
> Their reports feed a **Bull Researcher** and a **Bear Researcher**, who debate the case. A **Research Manager** turns that debate into an investment plan and a five-tier recommendation. A **Trader** turns the plan into a Buy/Hold/Sell proposal with optional entry, stop, and sizing guidance. Then a risk debate and the Portfolio Manager produce the final decision.
>
> The data retrieval is largely deterministic. The interpretation, debate, and report writing are done by LLMs. Only the Sentiment Analyst produces a structured numeric score; the other agents produce written reports or structured decision records. None of them places an order.

**How to read this document**

- **PMs / business readers:** sections 1–7. These explain what each analyst does, what it reads, what it outputs, and what it does not do.
- **Engineers:** sections 8–10. These cover testing, the LLM/harness mechanism, source files, and default limits.

---

## 1. The common analyst pattern

All four analysts share the same execution pattern, even though their tools differ.

```text
Analyst node starts
      │
      ▼
Build a prompt with:
  - the run date
  - the resolved instrument identity
  - the analyst's instructions
  - output-language instruction
      │
      ▼
LLM chooses a tool and calls it
      │
      ▼
Tool result returned to the LLM
      │
      ├─ more tools needed? → another round
      │
      └─ no more tools? → write the report
      │
      ▼
Report field written into shared state
      │
      ▼
All analysts finish → bull/bear debate begins
```

### 1.1 Analysts run in parallel

Each analyst is built as its own small LangGraph subgraph:

- an `agent` node (the LLM),
- a `tools` node (the analyst's tool set),
- a `wrap_up` node for the final turn.

The four analyst subgraphs start at the same time. The debate starts only after all selected analysts have filed their reports.

### 1.2 Tool rounds and the wrap-up

- `max_tool_rounds` defaults to **20**.
- Each tool round is roughly two graph steps: the model asks for a tool, then the tool result comes back.
- When the limit is reached, the analyst receives a `WRAP_UP` instruction: write the report now from the evidence already gathered.
- The final turn is given **no tools**, and the previous tool calls/results are rewritten as text. This prevents a model from endlessly calling tools.
- If the model has no tool calls, its response becomes the report immediately.

### 1.3 The analyst does not make the trade call

The analyst prompts explicitly say another agent decides the trade. Analyst prompts must not ask for `FINAL TRANSACTION PROPOSAL`; a test enforces this. The reports are evidence for the downstream debate and decision agents.

### 1.4 Same model tier

The analysts use the **quick-thinking model**. The deep-thinking model is reserved for the Research Manager and Portfolio Manager.

### 1.5 Shared state fields

| Analyst | Report field |
|---|---|
| Market / Technical | `market_report` |
| Sentiment | `sentiment_report` |
| News | `news_report` |
| Fundamentals | `fundamentals_report` |

The final state also contains the debate histories, investment plan, trader proposal, final decision, and rating.

### 1.6 Failure behavior

The data tools convert vendor problems into explicit sentinels rather than empty strings:

- `DATA_UNAVAILABLE` — the vendor could not answer; this says nothing about the company.
- `NO_DATA_AVAILABLE` — the vendor answered but had no usable data.
- A coverage-gap message — the feed could not observe the requested historical window.

The analyst is instructed to report missing data rather than invent values. The run continues.

### 1.7 Language instruction

Every report-producing analyst receives `get_language_instruction()`. In English it adds no text; in another language it instructs the model to write the report in that language while keeping program-read labels in English.

---

## 2. Market / Technical Analyst

Code name: `market_analyst`  
Report field: `market_report`  
Tools: `get_stock_data`, `get_indicators`, `get_verified_market_snapshot`

### 2.1 What it is trying to do

Answer: **What is price, trend, momentum, volatility, and volume doing?**

The analyst is asked to:

- choose up to eight complementary technical indicators,
- retrieve the price history first,
- calculate the selected indicators,
- verify exact price and indicator claims against a deterministic snapshot,
- write a detailed technical report with a summary table.

The “up to eight” is a prompt instruction, not a hard code limit. The code enforces which indicator names are valid, not how many the model chooses.

### 2.2 Workflow

```text
Run date + instrument identity
        │
        ▼
Prompt lists supported indicators and rules
        │
        ▼
Model calls get_stock_data(start, end)
        │
        ▼
Model calls get_indicators(indicator, current date, lookback)
        │
        ├─ may call several indicators
        │
        ▼
Model calls get_verified_market_snapshot(ticker, current date)
        │
        ▼
Treat snapshot as source of truth
        │
        ▼
Write narrative report + Markdown summary table
```

### 2.3 Tools in detail

#### `get_stock_data`

- Retrieves daily OHLCV data.
- The window end is clamped to the run’s `trade_date`; the model cannot request future data.
- Uses the configured `core_stock_apis` vendor chain.
- Default: Yahoo Finance; Alpha Vantage is an alternative.
- Daily data only; not intraday.

#### `get_indicators`

- Retrieves one or more technical indicators over a lookback window.
- The model can pass a comma-separated string; the code splits it and calls the vendor per indicator.
- The date is clamped to `trade_date`.
- Uses the configured `technical_indicators` vendor chain.
- Default: Yahoo Finance with local `stockstats` calculations; Alpha Vantage is an alternative.

Supported indicators in the current code:

- Moving averages: `close_50_sma`, `close_200_sma`, `close_10_ema`
- MACD family: `macd`, `macds`, `macdh`
- Momentum: `rsi`, plus `mfi` in the data layer
- Volatility: `boll`, `boll_ub`, `boll_lb`, `atr`
- Volume: `vwma`

The prompt lists a slightly smaller set than the data layer accepts. Unknown indicator names raise a `ValueError`, which the tool returns to the model as text.

#### `get_verified_market_snapshot`

This is the important quality-control tool.

It deterministically produces:

- the latest OHLCV row on or before the analysis date,
- a fixed set of common indicators,
- the last several closes.

The fixed snapshot indicators are:

```text
close_10_ema, close_50_sma, close_200_sma,
rsi, boll, boll_ub, boll_lb,
macd, macds, macdh, atr
```

The analyst is told to treat this snapshot as the source of truth for any exact price, indicator, support/resistance, or percentage claim. If another tool conflicts with it, the model should flag the discrepancy rather than invent a reconciled number.

The snapshot reads OHLCV with `fill_gaps=False`, so a missing price is not silently replaced by the previous session’s price under a different date.

### 2.4 How the technical “algorithm” works

There are two layers:

1. **Deterministic data layer**
   - OHLCV retrieval
   - date filtering and look-ahead prevention
   - stale-data rejection
   - indicator calculation via `stockstats`
   - verified snapshot computation

2. **LLM interpretation layer**
   - selects indicators
   - decides which signals matter
   - describes trend, momentum, volatility, and volume
   - writes the report and summary table

There is **no fixed rule** such as “RSI below 30 means buy.” The LLM interprets the values. The deterministic snapshot prevents it from inventing exact numbers, but it does not force a particular trading conclusion.

### 2.5 Data details

- Daily OHLCV, not intraday.
- The OHLCV loader downloads roughly five years, caches per symbol, filters rows after the analysis date, and rejects stale frames.
- Indicators are computed on a gap-filled series for continuity.
- The verified snapshot uses the data as reported, without gap filling.
- `yfinance` treats the end date as exclusive; the code requests one extra day so the requested end date is included.

### 2.6 What it is good at

- Trend and momentum description.
- Volatility and risk context.
- Volume confirmation.
- Grounded exact-price claims through the verified snapshot.
- Producing a readable technical narrative for downstream agents.

### 2.7 Limitations and misconceptions

- **Daily, not intraday.** A 10:31 price move cannot be derived from this path.
- **No true capital-flow data.** VWMA/MFI/volume are proxies; there are no fund flows, 13F changes, options flow, or dark-pool data.
- **No fixed signal score.** The report is narrative, not a numeric factor.
- **Only the listed indicators.** New indicators require code changes.
- **LLM interpretation is still variable.** The snapshot grounds numbers but not the narrative conclusion.
- **No automatic support/resistance engine.** The model may describe levels from the data; the snapshot is the authority for exact values.

---

## 3. Fundamentals Analyst

Code name: `fundamentals_analyst`  
Report field: `fundamentals_report`  
Tools: `get_fundamentals`, `get_balance_sheet`, `get_cashflow`, `get_income_statement`, `get_insider_transactions`

### 3.1 What it is trying to do

Answer: **What is the company’s financial condition, operating performance, and recent insider behavior?**

The analyst is asked to write a comprehensive fundamental report covering:

- company profile and basic financials,
- financial statements,
- financial history,
- insider buying and selling,
- specific, actionable insights with a summary table.

### 3.2 Workflow

```text
Run date + resolved company identity
        │
        ▼
Prompt asks for a comprehensive fundamental report
        │
        ▼
Model chooses among:
  - get_fundamentals
  - get_balance_sheet
  - get_cashflow
  - get_income_statement
  - get_insider_transactions
        │
        ▼
Tool results return company overview and/or statements
        │
        ▼
Model writes narrative + table
```

The model is not forced to call every tool. It decides what it needs. In practice, a thorough run often calls most of them.

### 3.3 Tools and data sources

#### `get_fundamentals`

- Company overview: name, sector, industry, market cap, valuation ratios, margins, returns, debt, cash flow, etc.
- Default vendors: Yahoo Finance or Alpha Vantage.
- **SEC EDGAR does not serve this method.** The current default category chain `sec_edgar,yfinance` therefore leaves only Yahoo available for `get_fundamentals`.
- Yahoo/Alpha Vantage overview data is present-day only. For a historical analysis date, the system withholds it and returns a notice rather than leaking today’s profile into the past.

#### `get_balance_sheet`, `get_cashflow`, `get_income_statement`

- SEC EDGAR for US filers, as filed, with filing dates.
- Yahoo or Alpha Vantage as alternative vendors.
- SEC EDGAR filters to facts filed on or before the analysis date and uses the values as first filed, not later restatements.
- Yahoo/Alpha Vantage date statements by the period they cover, not by filing date, so historical runs withhold them.
- Quarterly or annual frequency is selectable.

#### `get_insider_transactions`

- Yahoo or Alpha Vantage.
- Historical runs withhold these because the vendors date a trade by when it happened, not when it was publicly filed. The filing can lag by up to two business days.
- Not a full Form 4 parser.

### 3.4 How the fundamental “algorithm” works

The fundamental analyst is more qualitative than the technical analyst:

1. **Retrieve** company overview and financial statements.
2. **Read** the data, including units and currencies.
3. **Interpret** growth, margins, leverage, cash generation, and insider activity.
4. **Write** a narrative and summary table.

There is no built-in ratio engine, scoring model, or fixed checklist. The LLM does the comparison and interpretation. The deterministic layer retrieves and formats the data; the LLM decides what it means.

### 3.5 Units and currencies

The data layer deliberately carries units because otherwise the LLM can misread the numbers:

- Some figures are in the quote currency (which can be a minor unit such as GBp).
- Market cap and per-share figures may be in the main currency unit.
- Income figures may be in the company’s reporting currency, which can differ from the quote currency for an ADR.
- Margins and returns are fractions; dividend yield and debt-to-equity are percentages.

The model is expected to preserve these distinctions.

### 3.6 What it is good at

- Financial statement analysis.
- Growth, margin, leverage, and cash-flow narrative.
- As-filed US statement history.
- Insider activity, when available.
- Producing a detailed report for the debate.

### 3.7 Limitations and misconceptions

- **No Bloomberg, Finnhub, or Reuters fundamentals.**
- **No consensus estimates or revisions.**
- **No industry/competitor dataset.** Porter’s Five Forces is not implemented.
- **No deterministic valuation model.** The LLM interprets the numbers.
- **Company overview is live-only.** Historical overview is withheld.
- **Yahoo failure can leave a partial report.** Statements may still work via SEC EDGAR.
- **Insider data is not as-filed.** It is withheld for historical dates.
- **No automatic detection of accounting quality.** The model may discuss it, but there is no dedicated forensic model.
- **Data can be very long.** Quarterly histories can produce long tool outputs and large prompts.

> This is exactly what happened in the prior NVDA run: `get_fundamentals` and `get_insider_transactions` returned `DATA_UNAVAILABLE` from Yahoo, while the three SEC EDGAR statements succeeded. The analyst still wrote a substantial report.

---

## 4. News Analyst

Code name: `news_analyst`  
Report field: `news_report`  
Tools: `get_news`, `get_global_news`, `get_macro_indicators`, `get_prediction_markets`

### 4.1 What it is trying to do

Answer: **What recent company, macro, geopolitical, and forward-looking event information could affect this instrument?**

The analyst is asked to write a comprehensive report on:

- recent news about the company or asset,
- broader global and macroeconomic news,
- macro indicators from FRED,
- prediction-market probabilities for forward-looking events,
- a summary table.

### 4.2 Workflow

```text
Run date + instrument identity
        │
        ▼
Prompt describes news, macro, and prediction tools
        │
        ▼
Model chooses:
  - get_news(start, end)
  - get_global_news(curr_date, lookback, limit)
  - get_macro_indicators(indicator, curr_date, lookback)
  - get_prediction_markets(topic, limit)
        │
        ▼
Tool results return articles, macro series, or event probabilities
        │
        ▼
Model writes narrative + table
```

### 4.3 Tools in detail

#### `get_news`

- Company/asset news over a requested window.
- The end date is clamped to the run date.
- Default vendor: Yahoo Finance; Alpha Vantage alternative.
- Yahoo first tries the symbol’s news feed. If empty, it uses Yahoo search results tagged with the symbol.
- The article limit defaults to **20** per ticker.
- Results are filtered to the requested publication window.
- If Yahoo search only returns a sample and cannot observe the whole window, the output says the feed is unavailable/coverage-limited rather than “no news.”

#### `get_global_news`

- Broad macro/global news.
- Default lookback: **7 days**.
- Default limit: **10 articles**.
- Search queries are configurable in `global_news_queries` (Fed/rates/inflation, S&P earnings/GDP, geopolitics, central banks, oil/commodities).
- Yahoo search results are deduplicated by title and filtered to the window.
- Because search results are a sample, an empty result is not automatically treated as proof that nothing happened.

#### `get_macro_indicators`

- FRED series: policy rates, inflation, labor, growth, Treasury yields, yield curve, etc.
- Friendly aliases such as `cpi`, `core_pce`, `unemployment`, `fed_funds_rate`, `10y_treasury`, `yield_curve`, `real_gdp`, `vix`, plus euro-area aliases.
- Can also accept raw FRED series IDs.
- Requires `FRED_API_KEY`.
- Optional category: failure degrades to a `DATA_UNAVAILABLE` note instead of aborting the run.

#### `get_prediction_markets`

- Polymarket event probabilities: Fed decisions, recession, elections, geopolitics, crypto, sector events.
- Returns implied probability, volume, resolution date, and recent move.
- Keyless.
- Optional category: failure degrades.

### 4.4 How the news “algorithm” works

1. Retrieve articles and macro data with date windows.
2. Deduplicate and trim to the window.
3. Let the LLM identify relevant events, themes, and cross-currents.
4. Let the LLM write a narrative and summary table.

There is no event-scoring formula, no entity extraction model, and no automatic impact ranking. The LLM reads the retrieved text and decides what matters. The data layer enforces windows and source availability.

### 4.5 Point-in-time behavior

- Company and global news are filtered by publication time.
- Undated items are kept only when the requested window reaches the present.
- A window a feed cannot observe is reported as a coverage gap, not as an absence of news.
- Macro series are time series, not present-day snapshots.
- Prediction markets are forward-looking and may not have historical archives; treat historical runs cautiously.

### 4.6 What it is good at

- Company-specific event context.
- Macro backdrop and rates/inflation context.
- Global risk themes.
- Prediction-market probabilities.
- Producing a readable event narrative.

### 4.7 Limitations and misconceptions

- **No general web search.** Yahoo search is for news, not arbitrary web research.
- **No Bloomberg, Reuters, Finnhub, or X integration.**
- **No email inbox.**
- **No full-text article archive.** Yahoo/Alpha Vantage provide summaries/snippets.
- **No event-impact model.** The LLM interprets relevance.
- **Global news is a sample.** Empty results are not proof of no news.
- **FRED is optional.** Without a key, macro data is unavailable.
- **Social media is not here.** It belongs to the Sentiment Analyst.

---

## 5. Sentiment Analyst

Code name: `sentiment_analyst`; internal analyst key: `social`  
Report field: `sentiment_report`  
Tools: none — it pre-fetches its sources before calling the model.

### 5.1 What it is trying to do

Answer: **What is the current sentiment around this instrument across news and social sources, and how confident are we?**

The Sentiment Analyst is different from the other three:

- it does not use a tool-calling loop,
- it fetches all sources before the model call,
- it produces a structured report with a band, a 0–10 score, and a confidence level,
- it has a fixed seven-day window.

### 5.2 Workflow

```text
Run date
   │
   ▼
Fetch three source blocks:
  1. Yahoo Finance news headlines (last 7 days)
  2. StockTwits cashtag messages (up to 30)
  3. Reddit posts from configured subreddits (last 7 days)
   │
   ├─ optional Jev screening if TYPESAFE_API_KEY is set
   │
   ▼
Build a prompt with the three source blocks
   │
   ▼
Structured output SentimentReport if supported
   │  (fallback: free-text generation)
   ▼
Render header + narrative into sentiment_report
```

### 5.3 Sources in detail

#### News headlines

- Uses the same `get_news` tool, over `trade_date - 7 days` to `trade_date`.
- Yahoo Finance by default; Alpha Vantage possible through configuration.
- Provides institutional framing.

#### StockTwits

- Fetches the cashtag stream for the ticker.
- Up to **30 messages**.
- Each message may carry a user-labeled Bullish/Bearish tag.
- Messages are trimmed to the analysis window.
- No authentication required for the public feed.

#### Reddit

- Default stock subreddits: `r/wallstreetbets`, `r/stocks`, `r/investing`.
- Crypto: `r/CryptoCurrency`, `r/CryptoMarkets`, plus `r/Bitcoin`, `r/ethereum`, or `r/solana` for those coins.
- Public RSS/search path, no account required.
- Posts are trimmed to the window.
- No vote or comment counts, so the model is told not to infer engagement.

#### Jev screening

- Optional; requires `TYPESAFE_API_KEY`.
- Filters off-topic posts and labels stance (bullish/bearish/neutral/unclear).
- The report shows how many posts were screened and their stance counts.
- Without the key, posts pass through unscreened.

### 5.4 Structured output

If the provider supports structured output, the model returns a `SentimentReport`:

| Field | Meaning |
|---|---|
| `overall_band` | Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish |
| `overall_score` | 0–10; 0 = maximally bearish, 5 = neutral, 10 = maximally bullish |
| `confidence` | low / medium / high, based on data quality and sample size |
| `narrative` | Source-by-source analysis, divergences, themes, catalysts/risks, summary table |

The code renders a deterministic header:

```text
**Overall Sentiment:** **Bullish** (Score: 7.2/10)
**Confidence:** Medium
```

If structured output is unsupported or fails, the analyst falls back to free text. The header may then be absent or less machine-readable.

### 5.5 How the sentiment “algorithm” works

1. **Deterministic retrieval** of news, StockTwits, and Reddit.
2. **Optional deterministic screening** of posts by Jev.
3. **LLM interpretation** of the source blocks.
4. **Structured scoring** if the provider supports it.
5. **Rendering** to a human-readable report.

The score is not computed by a formula. The model assigns it using guidelines and the evidence. The band and score are validated by the schema (`0–10`), but the mapping between narrative and score is the model’s judgment.

The prompt explicitly asks the model to:

- read the StockTwits Bullish/Bearish ratio as a leading retail signal,
- look for cross-source divergence,
- judge Reddit by post content, not engagement,
- distinguish opinion from event,
- identify recurring narratives and catalysts,
- state data limits and lower confidence when sources are sparse.

### 5.6 What it is good at

- Fast read of retail versus institutional tone.
- Cross-source divergence detection.
- Structured sentiment band and score.
- Confidence-aware summary.
- Crypto-aware subreddit selection.

### 5.7 Limitations and misconceptions

- **No X/Twitter.** The diagram’s X box is not implemented.
- **No CoinGecko.** Crypto prices use Yahoo/Alpha Vantage; sentiment uses Reddit/StockTwits.
- **Social feeds are not archived.** A historical run trims recent posts to the window, but it cannot recover posts that existed at that time. The module docstring says this explicitly.
- **No full social-media coverage.** StockTwits and Reddit are samples, not the whole market.
- **No engagement metrics.** Reddit vote/comment counts are not fetched.
- **Jev is optional.** Without the key, off-topic posts are included.
- **Score is LLM judgment.** It is structured and bounded, but not a deterministic factor.
- **Seven-day window.** It is not an intraday sentiment monitor.
- **No direct trade call.** It is evidence for the debate.

---

## 6. How the four analysts feed the research team and trader

```text
Market/Technical ─┐
Sentiment ────────┼─► Bull vs Bear debate
News ─────────────┤      │
Fundamentals ─────┘      ▼
                    Research Manager
                         │
                         ▼
                      Trader
                         │
                         ▼
              Aggressive / Neutral / Conservative
                         │
                         ▼
                  Portfolio Manager
                         │
                         ▼
               Final rating + decision text
```

### 6.1 What each downstream agent receives

The downstream agents receive different slices of the evidence — not one common data pipe and not a fixed numeric weighting:

- The **bull and bear researchers** receive all four analyst reports plus the debate history.
- The **research manager** receives the debate history, not the raw analyst reports.
- The **trader** receives the research manager's investment plan, the technical market report if one was produced, and the portfolio context.
- The **risk analysts** receive the trader proposal, research plan, risk debate history, and portfolio context.
- The **portfolio manager** receives the risk debate, research plan, trader proposal, portfolio context, and memory lessons, then produces the final decision.

This funnel matters for improvement: detail can be compressed or lost as reports become debate, plan, and final decision. There is no automatic “technical 40%, fundamentals 30%, news 20%, sentiment 10%” formula.

### 6.2 What each analyst is not

| Analyst | Not its job |
|---|---|
| Technical | Not a fixed trading strategy or a fund-flow model |
| Fundamentals | Not a valuation model, consensus-estimate engine, or Porter analysis |
| News | Not a general web researcher or event-impact scorer |
| Sentiment | Not a complete social-media firehose or a deterministic sentiment formula |

### 6.3 Research team: Bull Researcher and Bear Researcher

The research team is two opposing LLM agents.

| Role | Goal | Prompt emphasis |
|---|---|---|
| **Bull Researcher** | Build the strongest evidence-based case for investing | Growth potential, competitive advantages, positive financial/market/news indicators, rebut the bear |
| **Bear Researcher** | Build the strongest evidence-based case against investing | Risks, competitive weaknesses, financial instability, negative indicators, rebut the bull |

#### Inputs

Both researchers receive:

- the technical `market_report`,
- the `sentiment_report`,
- the `news_report`,
- the `fundamentals_report`,
- the instrument identity context,
- the full debate history,
- the opponent's latest argument.

Missing reports are replaced with an explicit “no report in this run” marker, not an empty section. This prevents the model from inventing a finding to fill a blank.

#### Tools and output

- They have **no tools**. They only read the reports already in state.
- They use the **quick-thinking model**.
- They produce free-text arguments; there is no structured bull/bear score.
- They do not call the Portfolio Manager or the Trader.

#### Debate state

Each turn updates `investment_debate_state`:

```text
history              full debate transcript
bull_history         only the bull arguments
bear_history         only the bear arguments
current_response     the latest argument
count                number of turns so far
```

The argument is prefixed with `Bull Analyst:` or `Bear Analyst:`.

#### First turn

When the debate has no opponent yet, `opponent_argument_or_opening()` inserts a clear “the opponent has not spoken yet — open with your own case” marker. This avoids the model fabricating an opponent argument.

#### Debate loop

`ConditionalLogic.should_continue_debate()` controls the turns:

```python
if count >= 2 * max_debate_rounds:
    return "Research Manager"
if current_response.startswith("Bull"):
    return "Bear Researcher"
return "Bull Researcher"
```

With the default `max_debate_rounds = 1`:

```text
Bull speaks once → Bear speaks once → Research Manager
```

With `max_debate_rounds = 2`, each side speaks twice.

There is no separate judge during the debate. The Research Manager evaluates the transcript after the loop.

#### What the research-team image gets right

- Bull evaluates potential; bear assesses risks.
- The two sides debate.
- The output is a balanced view, not a single analyst's opinion.

#### What it omits

- The debate has a hard turn limit.
- The researchers have no tools and cannot fetch new data.
- Missing reports are explicitly marked as absent.
- The debate is not a numeric scoring model; it is free text.
- The final recommendation is not made by the researchers.

### 6.4 Research Manager

The Research Manager is the bridge from debate to action.

- Uses the **deep-thinking model**.
- Receives the debate history and instrument context.
- Produces a structured `ResearchPlan`:
  - `recommendation`: Buy / Overweight / Hold / Underweight / Sell
  - `rationale`: which arguments won
  - `strategic_actions`: concrete steps for the trader, relative to a standard allocation
- Uses structured output when the provider supports it; otherwise falls back to free text.
- Does **not** see the caller's actual holdings. The plan is written relative to a standard allocation; the Trader and Portfolio Manager apply the real position.

The prompt tells the manager that conflict alone is not a reason to Hold; it must commit to the stronger side or explain why the evidence is genuinely balanced.

### 6.5 Trader Agent

The Trader turns the research plan into a transaction proposal.

#### Inputs

- the Research Manager's investment plan,
- the technical `market_report`, if present,
- the instrument identity context,
- the caller's portfolio context, if provided,
- an explicit notice if no portfolio was provided.

The Trader does **not** directly receive the raw sentiment, news, or fundamentals reports. It sees the research plan, the market report, and the portfolio. This is a deliberate compression: the research plan is supposed to carry the debate's conclusions.

#### Tools and model

- No tools.
- Uses the **quick-thinking model**.
- Uses structured output (`TraderProposal`) when supported, with free-text fallback.

#### Structured output

```text
action            Buy / Hold / Sell
reasoning         2–4 sentences anchored in the plan and reports
entry_price       optional absolute price, not a percentage or range
stop_loss         optional absolute price, not a percentage
position_sizing   optional text, e.g. "5% of portfolio"
```

The renderer adds a trailing:

```text
FINAL TRANSACTION PROPOSAL: **BUY / HOLD / SELL**
```

for backward compatibility with downstream text consumers.

#### How “timing and magnitude” work

- **Direction:** the prompt maps a research recommendation of Overweight to Buy and Underweight to Sell.
- **Entry/stop:** the model is told to use absolute price levels grounded in the technical market report (current price, support/resistance, ATR, volatility). If the market report is absent, it has no price structure to ground them.
- **Magnitude:** `position_sizing` is free text, not a hard risk model. The Portfolio Manager later applies the actual portfolio and risk debate.
- **Timing:** there is no intraday timing engine, order scheduler, or market-microstructure model. “Timing” is the model's narrative judgment, not an execution algorithm.

#### Numeric-field guardrails

The structured schema normalizes model-written numbers:

- placeholders like “N/A” become `None`,
- percentages are dropped rather than misread as prices,
- formatted prices like `$1,234.50` become `1234.5`,
- ranges like `150–160` are dropped rather than guessed.

#### What the trader image gets right

- The Trader evaluates market opportunities.
- It composes the analysts'/researchers' conclusions into a decision.
- It can say Buy, Hold, or Sell and give reasoning.
- It can provide entry, stop, and sizing guidance.

#### What it omits

- The Trader does not receive every raw analyst report; it gets the research plan, market report, and portfolio.
- “Timing and magnitude” is not a deterministic execution plan.
- “Buy Apple Shares” is a proposal, not an order.
- No broker, OMS, or pre-trade compliance is involved.
- The final firm-level rating still comes from the Portfolio Manager.

### 6.6 Risk team and Portfolio Manager

After the Trader:

1. Aggressive, Neutral, and Conservative risk analysts debate the proposal.
2. The risk debate runs for `max_risk_discuss_rounds`; the default is one turn each.
3. The Portfolio Manager uses the deep model, reads the risk debate, research plan, trader proposal, portfolio context, and past lessons.
4. The Portfolio Manager produces the final five-tier rating and decision text.

The risk team and PM are decision layers, not data-gathering layers.

### 6.7 The two images vs. the code

| Image claim | Code reality |
|---|---|
| “Bull evaluates the investment potential.” | Yes, but as free-text argument, not a score. |
| “Bear assesses the risks.” | Yes, but as free-text argument, not a score. |
| “Through structured debates, they balance gains against risks.” | The debate has a fixed turn order and round limit; the Research Manager judges afterward. |
| “Trader composes reports from analysts and researchers.” | Partially: it receives the Research Manager's plan, the market report, and portfolio context — not all raw reports. |
| “Determines the timing and magnitude of trades.” | Partially: it proposes entry, stop, and sizing; there is no execution or intraday timing engine. |
| “Decisions: BUY Apple Shares.” | It produces a Buy/Hold/Sell proposal with reasoning; it does not place an order. |
| “Strong financials and growth prospects outweigh valuation and liquidity risks.” | This is the kind of judgment the model can write from the research plan and market report. |

### 6.8 The information funnel

```text
Raw analyst reports
        │
        ▼
Bull / Bear debate
        │
        ▼
Debate history
        │
        ▼
Research Manager → investment plan
        │
        ▼
Trader → proposal + optional levels
        │
        ▼
Risk debate
        │
        ▼
Portfolio Manager → final rating
```

The funnel is the most important harness fact for future improvement:

- The Research Manager sees the **debate transcript**, not the raw analyst reports.
- The Trader sees the **research plan**, the **market report**, and the **portfolio**, not the raw sentiment/news/fundamentals reports.
- The Portfolio Manager sees the **risk debate**, the plan, the trader proposal, portfolio context, and memory lessons.

If the firm wants richer decisions, the highest-leverage improvements are:

1. Pass structured analyst summaries into the Research Manager and Trader, not only prose.
2. Add a synthesis layer that preserves citations and key numbers.
3. Add deterministic signal scores alongside the prose.
4. Add a fact/evidence ledger so details do not disappear between stages.
5. Evaluate each stage separately, not only the final rating.

---

## 7. Common misconceptions

| Misconception | Reality |
|---|---|
| “The diagram shows Bloomberg, so Bloomberg works.” | No Bloomberg adapter exists. |
| “The diagram shows X/Twitter, so X is used.” | Only StockTwits and Reddit are used. |
| “The diagram shows CoinGecko.” | No CoinGecko client exists. |
| “The diagram shows Finnhub/Reuters.” | Neither is implemented. |
| “Execution is automated.” | No broker/OMS integration; only a decision. |
| “Analysts produce buy/sell ratings.” | Only the sentiment analyst has a numeric score; the others write reports. |
| “The technical analyst has a fixed algorithm.” | It is an LLM interpreting deterministic indicators. |
| “Fundamentals are always available.” | Yahoo overview can fail; SEC statements may still work. |
| “News means all news on the internet.” | It means configured vendor news and Yahoo search, not general web search. |
| “Social sentiment is historical and reproducible.” | Social feeds are not archived; historical sentiment is limited. |
| “The Bull and Bear researchers make the final call.” | They argue; the Research Manager and Portfolio Manager decide. |
| “The Trader sees every analyst report.” | It sees the Research Manager's plan, the market report, and the portfolio context. |
| “The Trader places the order.” | It produces a Buy/Hold/Sell proposal; there is no broker or OMS execution. |
| “The Trader has a deterministic timing algorithm.” | Entry, stop, and sizing are model-written suggestions, not an execution algorithm. |

---

## 8. How to test each analyst

The test suite runs offline by default. It does not need Yahoo, Bloomberg, or API keys.

```bash
pip install -e ".[dev]"

# Whole graph with scripted model and fake vendors
pytest -q tests/test_graph_end_to_end.py

# Analyst execution, prompts, and tool limits
pytest -q tests/test_analyst_execution.py tests/test_prompt_integrity.py

# News prompt behavior
pytest -q tests/test_news_analyst_prompt.py

# Point-in-time and source behavior
pytest -q tests/test_fundamentals_lookahead.py tests/test_news_lookahead.py
pytest -q tests/test_social_lookahead.py tests/test_post_screen.py
pytest -q tests/test_tool_date_enforcement.py tests/test_undated_tools_as_of.py

# Language instruction coverage
pytest -q tests/test_i18n_coverage.py
```

`tests/test_graph_end_to_end.py` is the best single test: it runs the full graph with a scripted model, calls every analyst tool through the vendor router, and verifies reports, memory, concurrency, and checkpoints.

For a real data test without Yahoo:

- Use SEC EDGAR for statements.
- Use Alpha Vantage for company overview/news if you have a key.
- Use FRED for macro if you have a key.
- Use a configured LLM or local Ollama for the model.

A fundamentals-only real run works with SEC EDGAR even when Yahoo overview and insider data fail.

---

## 9. How the LLM and harness work together

This is the key section for future improvement. The model is only one part of the system; most of the reliability comes from the **harness** around it.

### 9.1 What “harness” means here

The harness is all the code that:

- builds the prompt,
- decides which tools the model may use,
- executes tool calls,
- carries the message history,
- limits the number of tool rounds,
- forces a final report when the limit is reached,
- validates structured output,
- writes the report into shared state,
- runs analysts in parallel,
- and joins their reports before the debate.

The LLM does the reasoning and writing. The harness controls what it can see, what it can do, and when it must stop.

### 9.2 The layers

| Layer | Responsibility | Main code |
|---|---|---|
| Outer graph | Orchestrates analysts, debate, trader, risk, PM | `tradingagents/graph/setup.py`, `trading_graph.py` |
| Analyst subgraph | One analyst's private agent/tool loop | `_analyst_graph` in `graph/setup.py` |
| Agent node | Builds the prompt and invokes the model | `agents/analysts/*.py` |
| Tool node | Executes the tools the model requested | `ToolNode` in `graph/setup.py` |
| Turn logic | Decides whether the response is a tool call or a report | `agents/analysts/turn.py` |
| Tool functions | Retrieve and format data | `agents/tools.py` |
| Data router | Chooses the vendor and handles failures | `dataflows/router.py` |
| Model client | Connects to the chosen LLM provider | `llm_clients/` |
| Structured output | Typed output with free-text fallback | `agents/structured.py` |
| State | Carries messages, reports, debates, portfolio context | `agents/state.py` |
| Reporting | Writes Markdown/HTML reports | `reporting.py`, `report_html.py` |

### 9.3 One analyst turn, simplified

The real code is more defensive, but the shape is:

```python
def analyst_node(state):
    prompt = build_prompt(
        role_instructions=...,
        trade_date=state["trade_date"],
        instrument_context=...,
        language=...,
        tool_names=...,
    )
    result, report = take_turn(prompt, llm, TOOLS, state["messages"])
    return {"messages": [result], report_key: report}


def take_turn(prompt, llm, tools, messages):
    if is_wrap_up(messages):
        # No more tools: write the report from the tool results already gathered.
        prompt = prompt.partial(tool_names="none; your tool rounds are spent")
        result = (prompt | llm).invoke(as_text(messages))
        return result, result.content

    result = (prompt | llm.bind_tools(tools)).invoke(messages)
    return result, "" if result.tool_calls else result.content
```

Key consequences:

- While the model is calling tools, its `report` field is **empty**.
- Only the turn with no tool calls becomes the report.
- The `messages` history carries the assistant's tool requests and the tool results.
- The model is bound to a fixed tool list; it cannot invent a new tool.

### 9.4 The analyst subgraph

Each analyst is compiled as a small LangGraph:

```text
START → agent ──tool calls?──► tools ──rounds left?──► agent
             │                         │
             └── no tool calls ────────┴──► END (report)
                                       │
                                       └── limit reached ──► wrap_up → END
```

The subgraph has an `output_schema` containing only that analyst's report field. That is why:

- one analyst cannot overwrite another's report,
- the analyst's private message history does not pollute the outer state,
- all analyst reports can be merged safely when the slowest one finishes.

### 9.5 Parallelism

`GraphSetup.setup_graph()` adds every selected analyst as a node and adds edges from `START` to all of them. It then joins all analyst nodes into the `Bull Researcher` node.

In plain terms:

- all selected analysts start together,
- each has its own tools and message history,
- the research debate waits for all reports,
- a slow analyst delays the debate,
- a missing/failed analyst can still produce a report explaining what was unavailable.

The analysts do **not** read each other's reports during their own run. Cross-analyst synthesis happens later in the debate and manager nodes.

### 9.6 The prompt harness

Every analyst prompt is built from:

- a role-specific system message,
- the tool names available to that analyst,
- the run date (`trade_date`),
- the resolved instrument identity,
- the output-language instruction,
- the conversation history placeholder.

The prompt harness also enforces a role boundary: analysts report evidence, but another agent decides the trade. A test ensures analyst prompts do not ask for `FINAL TRANSACTION PROPOSAL`.

The instrument identity is resolved once at run start and injected into the state. It exists to stop the model from inventing a different company from a price chart.

### 9.7 The tool harness

The tools are Python functions decorated with LangChain's `@tool`. They receive:

- the symbol from graph state (`InjectedState`),
- the run date (`trade_date`),
- model-supplied arguments such as dates, indicators, or frequency.

The tool then:

1. clamps dates to the run date,
2. calls `route_to_vendor`,
3. returns formatted text or a clear sentinel.

The model never sees vendor credentials or directly calls a vendor. The data router enforces the configured chain and typed failure behavior.

### 9.8 The state harness

`AgentState` extends LangGraph's `MessagesState` and adds:

- company and asset identity,
- trade date,
- instrument context,
- portfolio context,
- past lessons from the memory log,
- four analyst report fields,
- research debate state,
- risk debate state,
- investment plan, trader proposal, final decision, and rating.

The analyst writes only its report field and message. The outer graph merges the fields after the subgraph completes.

### 9.9 Structured-output harness

The Sentiment Analyst, Research Manager, Trader, and Portfolio Manager can use Pydantic structured output.

- `bind_structured()` tries to create a structured-output model.
- If the provider does not support it, the agent uses free text.
- If the structured call fails at runtime, it falls back to plain `llm.invoke`.
- The parsed object is rendered back into the Markdown shape the rest of the system expects.

This is why a weak or local model can still run, but its output may be less consistent.

### 9.10 What is deterministic vs. LLM-driven

| Step | Deterministic code | LLM |
|---|---:|---:|
| Fetch prices/news/statements | Yes | No |
| Date clamping / point-in-time filtering | Yes | No |
| Technical indicator calculation | Yes | No |
| Verified market snapshot | Yes | No |
| Choose which tools to call | No | Yes |
| Interpret the evidence | No | Yes |
| Write the report | No | Yes |
| Assign sentiment band/score | No | Yes, within schema |
| Final trade rating | No | Yes, in manager/PM nodes |

### 9.11 What the Analyst Team image says vs. the code

| Image statement | Code reality |
|---|---|
| “Fundamentals evaluates company financials and performance metrics, identifying intrinsic values and potential red flags.” | It retrieves overview/statements/insider data and writes a narrative. There is no DCF or intrinsic-value engine. |
| “Sentiment aggregates news headlines, StockTwits, and Reddit chatter into a single sentiment read.” | Yes. It adds a structured band, 0–10 score, and confidence. No X/Twitter. |
| “News monitors global news and macroeconomic indicators, interpreting the impact of events.” | Yes, via Yahoo/Alpha Vantage news, FRED macro, and Polymarket. No Bloomberg/Reuters/Finnhub. |
| “Technical uses technical indicators such as MACD and RSI to detect patterns and forecast price movements.” | It calculates indicators and asks the LLM to interpret them. There is no separate forecasting model. |
| “Selected analysts work at the same time, each on its own tools.” | Yes — separate subgraphs, separate tools, parallel start. |
| “The research debate starts once all reports are in.” | Yes — the debate is joined after the analyst nodes complete. |

### 9.12 Improvement levers

| Lever | What could change | Risk / guardrail |
|---|---|---|
| Data | Add Bloomberg, Reuters, Finnhub, X, internal research, email | Licensing, credentials, normalization, point-in-time |
| Tools | Add deterministic calculations, flow models, ratio engines, valuation models | Must be tested and grounded; avoid duplicate logic |
| Prompt | Sharpen role instructions, citation rules, output format | Prompt tests and regression reports |
| Harness | Add a synthesis node, retries, caching, better tool limits | Preserve vanilla topology; test concurrency |
| Model | Use a stronger model for a specific analyst or a cheaper model for triage | Tool-calling/structured-output compatibility |
| Structured output | Add typed schemas for technical/fundamental/news reports | Weak-model fallback and schema versioning |
| Evaluation | Fake-LLM fixtures, golden reports, PM review, point-in-time tests | No network in unit tests; integration separately |
| Observability | Log prompt/model/tool versions, token cost, latency | Privacy and data-retention policy |
| Signal layer | Separate deterministic signal plugins from analyst prose | Do not let the LLM compute the score |

### 9.13 What to read first

For an engineer trying to improve the analysts:

1. `tradingagents/agents/analysts/turn.py` — tool/report turn logic.
2. `tradingagents/graph/setup.py` — analyst subgraphs, parallelism, wrap-up.
3. `tradingagents/agents/tools.py` — the actual data tools.
4. `tradingagents/dataflows/router.py` — vendor selection and failure behavior.
5. `tradingagents/agents/state.py` — what the agents share.
6. `tradingagents/agents/analysts/*.py` — the role prompts and tool sets.
7. `tradingagents/agents/structured.py` — structured-output fallback.

### 9.14 What this means for improving the system later

- Changing a prompt is easy but needs regression tests.
- Adding a data source is a tool plus a vendor adapter plus licensing.
- Adding a deterministic signal should be a separate computed layer, not a longer prompt.
- Changing the harness topology can improve cross-analyst reasoning, but it risks cost, latency, and vanilla compatibility.
- The safest improvements are: better data, better tool outputs, clearer prompts, structured schemas, and evaluation — in that order.

---

## 10. Technical appendix

### 10.1 Files

| Area | File |
|---|---|
| Market/Technical | `tradingagents/agents/analysts/market_analyst.py` |
| Fundamentals | `tradingagents/agents/analysts/fundamentals_analyst.py` |
| News | `tradingagents/agents/analysts/news_analyst.py` |
| Sentiment | `tradingagents/agents/analysts/sentiment_analyst.py` |
| Common turn logic | `tradingagents/agents/analysts/turn.py` |
| Analyst plan/specs | `tradingagents/graph/analyst_execution.py` |
| Analyst subgraphs | `tradingagents/graph/setup.py` |
| Tools | `tradingagents/agents/tools.py` |
| Data router | `tradingagents/dataflows/router.py` |
| Point-in-time rules | `tradingagents/dataflows/date_window.py` |
| Verified snapshot | `tradingagents/dataflows/vendors/yahoo/snapshot.py` |
| Technical indicators | `tradingagents/dataflows/vendors/yahoo/market.py` |
| OHLCV cache | `tradingagents/dataflows/vendors/yahoo/ohlcv.py` |
| Fundamentals | `tradingagents/dataflows/vendors/yahoo/fundamentals.py`, `alpha_vantage/fundamentals.py`, `sec_edgar.py` |
| News | `tradingagents/dataflows/vendors/yahoo/news.py`, `alpha_vantage/news.py` |
| Sentiment sources | `tradingagents/dataflows/vendors/stocktwits.py`, `reddit.py` |
| Jev screening | `tradingagents/agents/post_screen.py` |
| Sentiment schema | `tradingagents/agents/schemas.py` |
| Structured-output fallback | `tradingagents/agents/structured.py` |

### 10.2 Default limits

| Setting | Default |
|---|---|
| `max_tool_rounds` | 20 |
| `max_recur_limit` | 100 |
| `news_article_limit` | 20 |
| `global_news_article_limit` | 10 |
| `global_news_lookback_days` | 7 |
| Sentiment window | 7 days |
| StockTwits sample | up to 30 messages |
| Reddit subreddits | `wallstreetbets`, `stocks`, `investing` |
| Crypto subreddits | `CryptoCurrency`, `CryptoMarkets`, plus coin-specific |

### 10.3 Default vendor chains relevant to analysts

```text
core_stock_apis        yfinance
technical_indicators   yfinance
fundamental_data       sec_edgar,yfinance
news_data              yfinance
macro_data             fred
prediction_markets     polymarket
```

Remember: for `get_fundamentals`, only Yahoo and Alpha Vantage are registered. SEC EDGAR is used for statements, not the company overview.

### 10.4 Where each report goes

| Report | State key | Used by |
|---|---|---|
| Market/Technical | `market_report` | Bull/bear debate, trader grounding |
| Sentiment | `sentiment_report` | Bull/bear debate |
| News | `news_report` | Bull/bear debate |
| Fundamentals | `fundamentals_report` | Bull/bear debate |

The final Portfolio Manager decision is stored in `final_trade_decision`; the parsed rating is in `final_rating`.

### 10.5 What “algorithm” means in this document

For the Technical, Fundamentals, and News analysts, “algorithm” means:

- deterministic data retrieval and filtering,
- deterministic indicator computation where applicable,
- LLM interpretation and report writing.

For the Sentiment analyst, it additionally means:

- deterministic source collection and optional screening,
- LLM scoring into a structured schema.

None of the four is a fully deterministic trading model.
