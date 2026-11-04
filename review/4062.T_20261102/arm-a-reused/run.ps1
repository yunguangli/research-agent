# Arm A — fixed code (Phases 1–2), the existing 11:45 specification reused.
# Same inputs as baseline-1519 except the code; identical date, analysts, rounds,
# models, corpus and price source. Diagnostic purpose: no memory-log write.

$ErrorActionPreference = 'Continue'
$repo = 'C:\Users\yunguang\OneDrive - Trilogy Partners Limited\Documents\coding\research-agent'
$arm = Join-Path $repo 'review\4062.T_20261102\arm-a-reused'
Set-Location -LiteralPath $repo
# A headless shell has no UTF-8 console; Rich writes the report's arrows and
# yen signs to stdout and dies on cp1252 without this.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$env:TRADINGAGENTS_LLM_PROVIDER = 'openrouter'
$env:TRADINGAGENTS_QUICK_THINK_LLM = 'deepseek/deepseek-v4.1-flash-1017'
$env:TRADINGAGENTS_DEEP_THINK_LLM = 'deepseek/deepseek-v4.1-flash-1017'
$env:TRADINGAGENTS_OUTPUT_LANGUAGE = 'English'
$env:TRADINGAGENTS_DECISION_HORIZON = '3-6 months'
$env:TRADINGAGENTS_MAX_DEBATE_ROUNDS = '3'
$env:TRADINGAGENTS_MAX_RISK_ROUNDS = '3'
$env:TRADINGAGENTS_RESEARCH_DIR = 'C:\Users\yunguang\OneDrive - Trilogy Partners Limited\YL P Drive\Ibiden'
$env:TRADINGAGENTS_RESULTS_DIR = Join-Path $arm 'results'
$env:TRADINGAGENTS_BLOOMBERG_BACKEND = 'blpapi'
$env:TRADINGAGENTS_RUN_PURPOSE = 'comparison'
$env:TRADINGAGENTS_LOG_LEVEL = 'INFO'
# Reuse the existing spec: no rebuild flag, default cache dir.
Remove-Item Env:TRADINGAGENTS_REBUILD_MODEL -ErrorAction SilentlyContinue
Remove-Item Env:TRADINGAGENTS_MODEL_PRICE -ErrorAction SilentlyContinue

python -m cli.main --ticker 4062.T --date 2026-11-02 --analysts market,fundamentals `
    --save --no-show --html --run-purpose comparison --log-level INFO *> (Join-Path $arm 'run.out')
exit $LASTEXITCODE
