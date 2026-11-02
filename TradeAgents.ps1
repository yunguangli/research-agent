# TradeAgents — start an interactive session from the repo:
#
#     ./TradeAgents
#
# It sets the data sources (Bloomberg first, Yahoo as the fallback), asks for
# the research corpus folder on every start, then starts the CLI, which asks
# for the ticker, the date and the rest. Directory and file names may contain
# spaces; a path pasted with quotes around it is accepted as-is.
#
# Arguments pass straight through to the CLI, e.g.:
#     ./TradeAgents --ticker 3711.TT --date 2026-10-30
#
# If Windows refuses to run the script (execution policy), use:
#     powershell -ExecutionPolicy Bypass -File .\TradeAgents.ps1
#
# No param block on purpose: without one, every argument lands in $args
# untouched, so --ticker and friends are never parsed as script parameters.

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

# Bloomberg-first sources. An already-exported value always wins, so a session
# (or .env) that chose something else is left alone.
if (-not $env:TRADINGAGENTS_BLOOMBERG_BACKEND) {
    $env:TRADINGAGENTS_BLOOMBERG_BACKEND = 'blpapi'
}
if (-not $env:TRADINGAGENTS_DATA_VENDORS) {
    $env:TRADINGAGENTS_DATA_VENDORS = '{"core_stock_apis":"bloomberg,yfinance","technical_indicators":"bloomberg,yfinance","fundamental_data":"bloomberg,sec_edgar,yfinance"}'
}

# The corpus is asked for on EVERY start: a value left over from an earlier
# test would quietly feed one company's documents to another company's run.
# Enter keeps what is already set; anything typed replaces it; a path that
# does not exist clears it rather than falling back to the stale one.
$before = $env:TRADINGAGENTS_RESEARCH_DIR
$hint = if ($before) { "Enter keeps: $before" } else { "Enter to skip: fundamentals runs vendor-only" }
$answer = Read-Host "Research corpus folder ($hint)"
# Spaces are part of the path. Trim the whitespace and any quotes a paste
# carries, but nothing inside the path itself.
$answer = $answer.Trim().Trim('"').Trim().TrimEnd('\')
if ($answer) {
    if (Test-Path -LiteralPath $answer -PathType Container) {
        $env:TRADINGAGENTS_RESEARCH_DIR = (Resolve-Path -LiteralPath $answer).Path
    } else {
        Write-Warning "No folder there: $answer - continuing without a corpus."
        Remove-Item Env:TRADINGAGENTS_RESEARCH_DIR -ErrorAction SilentlyContinue
    }
}
if ($env:TRADINGAGENTS_RESEARCH_DIR) {
    Write-Host "  corpus: $env:TRADINGAGENTS_RESEARCH_DIR" -ForegroundColor Green
} else {
    Write-Host "  corpus: none (fundamentals runs vendor-only)" -ForegroundColor Yellow
}
Write-Host ''

# The CLI asks for the ticker (Step 1) and everything else from here.
python -m cli.main @args
exit $LASTEXITCODE
