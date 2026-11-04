# Arm B step 1 — build the specification once, into this arm's own cache.
#
# The build is the expensive LLM call; it is not the measured run. Running
# `run.ps1` afterwards loads this frozen spec with the rebuild flag off, so the
# comparison measures the code, not a second build.

$ErrorActionPreference = 'Continue'
$repo = 'C:\Users\yunguang\OneDrive - Trilogy Partners Limited\Documents\coding\research-agent'
$arm = Join-Path $repo 'review\4062.T_20261102\arm-b-rebuilt'
Set-Location -LiteralPath $repo
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$env:TRADINGAGENTS_LLM_PROVIDER = 'openrouter'
$env:TRADINGAGENTS_QUICK_THINK_LLM = 'deepseek/deepseek-v4.1-flash-1017'
$env:TRADINGAGENTS_DEEP_THINK_LLM = 'deepseek/deepseek-v4.1-flash-1017'
$env:TRADINGAGENTS_OUTPUT_LANGUAGE = 'English'
$env:TRADINGAGENTS_DECISION_HORIZON = '3-6 months'
$env:TRADINGAGENTS_RESEARCH_DIR = 'C:\Users\yunguang\OneDrive - Trilogy Partners Limited\YL P Drive\Ibiden'
$env:TRADINGAGENTS_RESULTS_DIR = Join-Path $arm 'results'
$env:TRADINGAGENTS_CACHE_DIR = Join-Path $arm 'cache'
$env:TRADINGAGENTS_BLOOMBERG_BACKEND = 'blpapi'
$env:TRADINGAGENTS_RUN_PURPOSE = 'comparison'
$env:TRADINGAGENTS_REBUILD_MODEL = '1'

python (Join-Path $arm 'build_spec.py') *> (Join-Path $arm 'build.out')
exit $LASTEXITCODE
