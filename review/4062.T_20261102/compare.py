"""Render metrics.csv as a Markdown table: one row per metric, one column per arm.

    python compare.py metrics.csv > comparison.md
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

# Order the metrics the way the review reads them, not alphabetically.
ORDER = (
    "marked", "checked", "references", "skipped", "failed", "relations",
    "sections_vs_state",
    "spec_source", "spec_compatibility", "run_purpose", "fallbacks",
    "rating", "rm_recommendation", "trader_action", "trader_has_final_line",
    "pm_price_target", "pm_time_horizon",
    "chars_fundamentals", "chars_challenge", "chars_risk_total",
    "dup_ratio_challenge", "dup_ratio_risk",
)


def main(path: str) -> None:
    with Path(path).open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise SystemExit("no rows")
    arms = [row["arm"] for row in rows]
    fields = [name for name in ORDER if all(name in row for row in rows)]
    fields += [name for name in rows[0] if name not in fields and name != "arm"]
    print("| Metric | " + " | ".join(arms) + " |")
    print("|---|" + "---|" * len(arms))
    for name in fields:
        cells = []
        for row in rows:
            value = str(row.get(name, "")).replace("|", "\\|")
            cells.append(value if value else "—")
        print(f"| {name} | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python compare.py metrics.csv")
    main(sys.argv[1])
