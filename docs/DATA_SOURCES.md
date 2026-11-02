# Data source policy — decision record

**Decided:** 2026-10-29 · **Status:** prices/indicators/overview/income statements in force; news vendor pending
**Principle:** Bloomberg for numbers, the web for news. Yahoo is a fallback link, never the primary.

Every source sits behind `dataflows/router.py`. A comma inside a vendor chain is the
ordered fallback — `"bloomberg,yfinance"` means Bloomberg first, Yahoo only when
Bloomberg cannot answer (terminal closed, symbol not mapped).

## The policy

| Category (config key) | Primary | Fallback | Status |
|---|---|---|---|
| Prices — `core_stock_apis` | **Bloomberg** (`blpapi`) | `yfinance` | **Built** |
| Indicators — `technical_indicators` | **Bloomberg** (computed locally from its bars) | `yfinance` | **Built** |
| Company overview — `fundamental_data` | **Bloomberg** | `sec_edgar`, `yfinance` | **Built** (overview only) |
| Financial statements | research corpus first, then **Bloomberg** (income: quarterly + trailing-4 annual) | `sec_edgar` (US lines), `yfinance` | **Income built**; balance/cashflow pending a mnemonic sweep |
| **News — `news_data`** | **`webnews` (web/RSS scraping)** | `alpha_vantage` | **Planned — see below** |
| Social sentiment | `stocktwits`, `reddit` (built-in pre-fetch) | — | Built, unchanged |
| Macro — `macro_data` | `fred` | — | Built |
| Prediction markets | `polymarket` | — | Built |
| Settlement returns & benchmark | price chain (same as above) | — | **Migrated** — `dataflows/prices.closes()` walks the chain; empty = pending, outage = reported |
| Company identity profile | **Bloomberg** when a backend is configured | Yahoo profile, then ticker-only | **Migrated** — `context._bloomberg_identity()`, fail-open |

### Effective configuration

```powershell
$env:TRADINGAGENTS_BLOOMBERG_BACKEND = "blpapi"       # Terminal running + pip install "tradingagents[bloomberg]"
$env:TRADINGAGENTS_DATA_VENDORS = '{"core_stock_apis":"bloomberg,yfinance","technical_indicators":"bloomberg,yfinance","fundamental_data":"bloomberg,sec_edgar,yfinance","news_data":"webnews,alpha_vantage"}'
# Symbols the mapping table does not know (crypto, Yahoo index notation, odd listings):
$env:TRADINGAGENTS_BLOOMBERG_TICKERS = '{"BTC-USD": "XBTUSD Curncy", "^N225": "NKY Index"}'
```

The Tokyo benchmark (`^N225`, used for alpha when settling a `.T` decision) is
Yahoo notation, so it is refused rather than guessed — either map it as above or
keep `,yfinance` in the chain so the fallback vendor serves it.

Drop `,yfinance` from a chain to remove Yahoo from that path entirely; the run then
reports the outage instead of quietly switching sources.

## Ticker dialects

Tickers enter the system in two spellings and leave it in one. `canonical_ticker`
(`dataflows/symbols.py`) translates the Bloomberg suffix to this system's at every
entry point — CLI flag, CLI prompt, `propagate`, `begin_checkpoint`, backtests, the
corpus resolver, the `research` CLI, portfolio lookups — so identity, benchmarks,
corpus folders, memory log and report folders all key on the same spelling:

| You type | System uses | Bloomberg key (generated) |
|---|---|---|
| `3711.TT` | `3711.TW` | `3711 TT Equity` |
| `5803.JT` | `5803.T` | `5803 JP Equity` |
| `AZN.LN` | `AZN.L` | `AZN LN Equity` |
| `NVDA.US` | `NVDA` | `NVDA US Equity` |
| `3711.TW`, `NVDA` | unchanged | as mapped above |

Only suffixes that are *not* Yahoo's are translated, so a valid ticker can never be
rewritten (`.SS` stays Shanghai even though Bloomberg's Sweden code is `SS`), and an
unknown suffix passes through to fail loudly where a mapping is actually required.

## Why this split

1. **Numbers need one source per calculation.** Mixed sources disagree (a price from
   one vendor, a close from another) and produce false signals. Bloomberg is licensed,
   point-in-time by construction (historical bars are the bars that existed), and the
   terminal is already paid for.
2. **News does not exist on the desktop API.** Probed 2026-10-29: all news field
   mnemonics return `Field not valid` on `//blp/refdata`. Bloomberg news is a separate
   product (B-PIPE / Information Services) with its own entitlement — a licensing
   decision, not a code change.
3. **RSS beats HTML scraping as the first web source.** Keyless, stable, worldwide
   (Japanese queries included), carries title/link/published-time natively — exactly
   what `get_news` consumes — and sits well inside robots/ToS. HTML extractors come
   per-site only where RSS cannot serve.
4. **Yahoo stays in the chain, not in the seat.** It is free and broad, which makes it
   the right *fallback*; it is unreliable (rate limits, anti-bot blocks) and
   present-day-only in places, which makes it the wrong primary.

## News: the `webnews` vendor (next build)

Register a vendor under `news_data`, replacing Yahoo as the default news source.

**Source tiers, in order:**

1. **Google News RSS by ticker + company name** — `news.google.com/rss/search?q=…`.
   Keyless, global, includes Japanese results. Gives title, link, publication time.
2. **Company IR and exchange releases** — per-ticker feed URLs from
   `TRADINGAGENTS_WEBNEWS_FEEDS` (JSON: `{"5803.T": ["https://…/rss.xml"]}`).
   Highest-quality source; no intermediary.
3. **Per-site HTML extractors** — only where RSS cannot serve, one versioned extractor
   per site, gated on `robots.txt` and terms (PLAN.md Phase 3 rules).

**Contract (same as every other vendor):**

- Provenance per item: source URL, published time, retrieved time, content hash.
- Point-in-time: items published after `trade_date` are filtered out; undated items
  are withheld from historical runs with a note — never silently shown.
- Headline + snippet + link only; no paywall or login bypass, no full text of
  licensed articles (licence rule in PLAN.md §10.6).
- Typed failures (`VendorUnavailableError` / `NoMarketDataError`) so a dead feed
  reads as "source unavailable", never as "no news".
- Fetched text is evidence, never instructions (prompt-injection rule).
- Tests run on saved RSS/HTML fixtures — no network in CI; the live path is behind
  `@pytest.mark.integration`.

**Acceptance for the first cut:**

- `news_data: "webnews"` returns dated, deduplicated headlines for `5803.T` (JP) and
  `NVDA` (US) with links.
- A run dated in the past sees only what was published by then.
- With the feed unreachable, the News Analyst gets the typed sentinel and reports the
  gap instead of "no news".

## Not in scope (and why)

- **Bloomberg news** — separate entitlement; revisit only if B-PIPE/Information
  Services is licensed (PLAN.md Phase 0.5 matrix).
- **X/Twitter, CoinGecko, Finnhub, Reuters** — no approved source or licence yet.
- **Broker research as news** — stays a research-desk source; only metadata-only
  event pointers may cross into the news lane (PLAN.md §5.11).
- **Execution/broker connectivity** — out of scope entirely.
