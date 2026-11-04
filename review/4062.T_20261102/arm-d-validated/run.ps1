# Arm D — validated path, Phase 1-4 code.
# The spec is arm C's (built under the one-period rule, frozen at 16:50) and is
# loaded through TRADINGAGENTS_RESEARCH_MODEL, so 1b/1c/1d can finally all run.

$ErrorActionPreference = 'Continue'
$repo = 'C:\Users\yunguang\OneDrive - Trilogy Partners Limited\Documents\coding\research-agent'
$arm = Join-Path $repo 'review\4062.T_20261102\arm-d-validated'
Set-Location -LiteralPath $repo
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
$env:TRADINGAGENTS_RESEARCH_MODEL = Join-Path $repo 'review\4062.T_20261102\arm-c-phase3\cache\models\4062.T.json'
$env:TRADINGAGENTS_RESULTS_DIR = Join-Path $arm 'results'
$env:TRADINGAGENTS_BLOOMBERG_BACKEND = 'blpapi'
$env:TRADINGAGENTS_RUN_PURPOSE = 'comparison'
Remove-Item Env:TRADINGAGENTS_REBUILD_MODEL -ErrorAction SilentlyContinue
Remove-Item Env:TRADINGAGENTS_MODEL_PRICE -ErrorAction SilentlyContinue

python -m cli.main --ticker 4062.T --date 2026-11-02 --analysts market,fundamentals `
    --save --no-show --html --run-purpose comparison --log-level INFO *> (Join-Path $arm 'run.out')
exit $LASTEXITCODE
