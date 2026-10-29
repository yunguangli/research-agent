# Where things live

One question this answers: *"I ran something — where did it go?"*

Fastest way to see it on your machine:

```powershell
python -m tradingagents.research paths --ticker 5803.T
```

That prints every path below with an `exists` flag and the newest report folders.

## Directories

| Path | What it is | Written by | Safe to delete? |
|---|---|---|---|
| `<corpus>` (e.g. `...\YL P Drive\fujikura`) | Your research documents, plus `_corpus_manifest.json` (dates, licences, exclusions) | you; manifest auto-generated | **No** — this is the evidence |
| `~\.tradingagents\out\<company>\model.json` | A model spec kept outside the corpus (what `TRADINGAGENTS_RESEARCH_MODEL` points at) | `research extract --merge` | Keep if env points here |
| `~\.tradingagents\fixtures\<company>\model.json` | Calibration spec transcribed from a reference report; used by the local golden test | you / me | Keep for comparison work |
| `~\.tradingagents\cache\models\<TICKER>.json` | Spec the **LLM built** from the corpus (`TRADINGAGENTS_REBUILD_MODEL=1`) | the graph | Yes — rebuilt on demand |
| `~\.tradingagents\cache\models\<TICKER>.failed.json` | The last raw model response and the validation error when a build failed | the builder | Yes — diagnostic only |
| `~\.tradingagents\logs\reports\<TICKER>_<stamp>\` | **The final investment report** | `save_reports` | Yes, but it is the product — archive instead |
| `~\.tradingagents\logs\<TICKER>\TradingAgentsStrategy_logs\full_states_log_<date>.json` | Machine-readable final state of a run | `record_decision` | Yes |
| `~\.tradingagents\memory\trading_memory.md` | Past decisions and their reflection, fed into later runs | memory step | **No** — it is the learning history |
| `~\.tradingagents\cache\` | Vendor cache: prices, filings, news already fetched | vendor layer | Yes — refetched |
| `~\.tradingagents\cache\checkpoints\` | LangGraph resume points, only with checkpointing on | checkpointer | Yes |

`~` is `C:\Users\yunguang`. Every path honours `TRADINGAGENTS_RESULTS_DIR`, `TRADINGAGENTS_CACHE_DIR`, and `TRADINGAGENTS_MEMORY_LOG_PATH` if you want them elsewhere.

## Inside one report folder

```
reports\5803.T_20261028_104355\
  complete_report.md            the whole report, all teams, one document  <- read this
  complete_report.html          same content, one page
  1_analysts\fundamentals.md    the LLM-written analyst report
  1b_research\research_package.md   the verified model (computed, no prose)
  1c_audit\numbers.md           figures in the prose that do not match the model
  1d_cases\cases.md             base + each debate case, recomputed from capped deltas
  2_research\bull.md, bear.md, manager.md
  3_trading\trader.md
  4_risk\aggressive.md, conservative.md, neutral.md
  5_portfolio\decision.md
```

The folder name is `<TICKER>_<YYYYMMDD>_<HHMMSS>`. Sorting by name sorts by time, so the newest is last.

## How the pieces connect

1. **Corpus** — documents you drop in a folder. `_corpus_manifest.json` records what was detected and any overrides.
2. **Filing parser** — reads a Japanese earnings release into disclosed facts (actuals, guidance, issued and treasury shares) with page citations. Other markets skip this step; the model reads the filing itself.
3. **Model spec** — either `model.json` (hand-maintained or `extract --merge`d) or built by the LLM from the corpus. The kernel validates it: bridges must sum, margins recompute, EPS divides, segments tie, citations must exist in the corpus.
4. **Research package** — the spec rendered to `1b_research\research_package.md` and injected into the Fundamentals Analyst prompt, and into the Research Manager and Portfolio Manager prompts.
5. **Report** — analyst prose plus the package plus the debate, written to `logs\reports\<TICKER>_<stamp>`.
6. **Number audit** — compares figures in the prose with the model's; anything unmatched is listed in `1c_audit\numbers.md`.
7. **Cases** — each debate pitch becomes capped deltas on the model; `1d_cases\cases.md` shows base plus each case, recomputed.

## Commands that produce or use these paths

| Command | Reads | Writes |
|---|---|---|
| `research paths --ticker T` | nothing | nothing (prints locations) |
| `research extract --corpus D --merge M --out O` | corpus filings | `O` (spec) |
| `research validate --spec S` | `S` | nothing |
| `research build --spec S --out D [--as-of]` | `S` | `D\report.md`, `D\tables\*.csv`, `D\package.json` |
| `research compare --spec S --reference R` | `S`, `R` | nothing (prints) |
| `dataflows.vendors.research_corpus D --as-of A` | corpus | nothing (prints) |
| `cli.main --ticker T ...` | corpus, spec, vendors | report folder, state log, memory log |

## Scratch and experimental output

`~\.tradingagents\out\` holds outputs from development runs. Only the spec your environment variable points at matters (`out\fujikura\model.json`); anything else there is disposable.
