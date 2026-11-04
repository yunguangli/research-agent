"""One row of comparison metrics for one arm of the 4062.T review.

Reads a report tree and its state log; writes a CSV row. Stdlib only, so it can
run against an old artifact without importing the current package.

    python metrics.py --arm baseline --report <dir> --state <json> --out metrics.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

SECTIONS = {
    "fundamentals": ("1_analysts/fundamentals.md", ("fundamentals_report",)),
    "market": ("1_analysts/market.md", ("market_report",)),
    "challenge": ("2_research/challenge.md", ("investment_debate_state", "challenge_history")),
    "manager": ("2_research/manager.md", ("investment_plan",)),
    "trader": ("3_trading/trader.md", ("trader_investment_plan",)),
    "risk_aggressive": ("4_risk/aggressive.md", ("risk_debate_state", "aggressive_history")),
    "risk_conservative": ("4_risk/conservative.md", ("risk_debate_state", "conservative_history")),
    "risk_neutral": ("4_risk/neutral.md", ("risk_debate_state", "neutral_history")),
    "decision": ("5_portfolio/decision.md", ("final_trade_decision",)),
    "package": ("1b_research/research_package.md", ("research_package_artifact",)),
}

MARK = re.compile(r"\[\[([^\]]+)\]\]")


def _state_value(state: dict, path: tuple):
    value = state
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
    return [p.strip().lower() for p in parts if len(p.strip()) > 40]


def _dup_ratio(text: str) -> float:
    sentences = _sentences(text)
    if not sentences:
        return 0.0
    counts = Counter(sentences)
    repeated = sum(n - 1 for n in counts.values() if n > 1)
    return round(repeated / len(sentences), 3)


def _audit_numbers(report: Path) -> dict:
    """Counters from 1c_audit/numbers.md: new format first, old one as fallback."""
    path = report / "1c_audit" / "numbers.md"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    row = {"marked": "", "checked": "", "references": "", "skipped": "", "failed": "",
           "relations": ""}
    for key, label in (("marked", "Marked"), ("checked", "Independently checked"),
                       ("references", "Reference-only"), ("skipped", "Skipped"),
                       ("failed", "Failed")):
        match = re.search(rf"^- {label}: (\d+)", text, re.MULTILINE)
        if match:
            row[key] = int(match.group(1))
    old = re.search(r"All (\d+) figure\(s\)", text)
    if row["checked"] == "" and old:
        row["checked"] = int(old.group(1))
    old_refs = re.search(r"(\d+) mark\(s\) were plain reference numbers", text)
    if row["references"] == "" and old_refs:
        row["references"] = int(old_refs.group(1))
    relations = re.search(r"(\d+) comparative claim\(s\) contradict", text)
    row["relations"] = int(relations.group(1)) if relations else 0
    return row


def _first_line(text: str, pattern: str) -> str:
    match = re.search(pattern, text or "", re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def metrics(arm: str, report: Path, state: dict) -> dict:
    texts = {}
    for name, (relative, _path) in SECTIONS.items():
        file = report / relative
        texts[name] = file.read_text(encoding="utf-8") if file.is_file() else ""
    decision = texts["decision"] or str(state.get("final_trade_decision") or "")
    trader = texts["trader"] or str(state.get("trader_investment_plan") or "")
    manager = texts["manager"] or str(state.get("investment_plan") or "")
    challenge = texts["challenge"] or ""
    risk = " ".join(texts[key] for key in ("risk_aggressive", "risk_conservative", "risk_neutral"))

    row = {"arm": arm}
    row.update(_audit_numbers(report))
    row.update({
        "rating": _first_line(decision, r"^\*\*Rating\*\*: (.+)$"),
        "rm_recommendation": _first_line(manager, r"^\*\*Recommendation\*\*: (.+)$"),
        "trader_action": _first_line(trader, r"^\*\*Action\*\*: (.+)$"),
        "trader_has_final_line": "FINAL TRANSACTION PROPOSAL" in trader,
        "pm_price_target": _first_line(decision, r"^\*\*Price Target\*\*: (.+)$"),
        "pm_time_horizon": _first_line(decision, r"^\*\*Time Horizon\*\*: (.+)$"),
        "spec_source": state.get("research_model_source", ""),
        "spec_compatibility": state.get("spec_compatibility", ""),
        "fallbacks": ";".join(sorted((state.get("structured_fallbacks") or {}).keys())),
        "run_purpose": state.get("run_purpose", ""),
        "chars_fundamentals": len(texts["fundamentals"]),
        "chars_challenge": len(challenge),
        "chars_risk_total": len(risk),
        "dup_ratio_challenge": _dup_ratio(challenge),
        "dup_ratio_risk": _dup_ratio(risk),
        "sections_vs_state": _consistency(report, state),
    })
    return row


def _consistency(report: Path, state: dict) -> str:
    """Whether each published section equals the state's text once marks are stripped."""
    mismatched = []
    for name, (relative, path) in SECTIONS.items():
        if name == "package":
            continue
        file = report / relative
        value = _state_value(state, path)
        if not file.is_file() or not isinstance(value, str) or not value.strip():
            continue
        expected = MARK.sub(lambda m: m.group(1), value).strip()
        if file.read_text(encoding="utf-8").strip() != expected:
            mismatched.append(name)
    if not mismatched:
        return "ok"
    return "mismatch:" + ",".join(mismatched)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    report = Path(args.report)
    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    row = metrics(args.arm, report, state)

    out = Path(args.out)
    exists = out.is_file()
    with out.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        if not exists:
            writer.writeheader()
        writer.writerow(row)
    print(json.dumps(row, indent=2))


if __name__ == "__main__":
    main()
