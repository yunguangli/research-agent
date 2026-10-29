"""Build a deterministic fundamental model from a spec.

    python -m tradingagents.research validate --spec model.json
    python -m tradingagents.research build --spec model.json --out ./out
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import kernel, report, valuation as valuation_module
from .schemas import ModelError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic fundamental model.")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="Run the model's arithmetic checks only")
    validate.add_argument("--spec", required=True)

    build = sub.add_parser("build", help="Validate, value, and write the report package")
    build.add_argument("--spec", required=True)
    build.add_argument("--out", required=True)
    build.add_argument("--price", type=float, default=None,
                       help="Share price for the valuation; overrides the spec and any market lookup")
    build.add_argument("--as-of", dest="as_of", default=None,
                       help="Use the latest close on this date as the valuation price")

    compare = sub.add_parser("compare", help="Score the model against a reference report")
    compare.add_argument("--spec", required=True)
    compare.add_argument("--reference", required=True, help="DOCX, Markdown, or text report")

    extract = sub.add_parser("extract", help="Build or refresh a spec from the research corpus")
    extract.add_argument("--corpus", required=True, help="Research directory for the company")
    extract.add_argument("--ticker", required=True)
    extract.add_argument("--date", default="", help="Run date, YYYY-MM-DD; only earlier filings are visible")
    extract.add_argument("--company", default="")
    extract.add_argument("--price", type=float, default=None, help="Share price for a new draft")
    extract.add_argument("--merge", default=None, help="Existing spec to refresh with filing facts")
    extract.add_argument("--out", required=True)

    paths = sub.add_parser("paths", help="Show where reports, models, caches and logs live")
    paths.add_argument("--ticker", default="", help="Also show this ticker's latest report folder")

    args = parser.parse_args(argv)

    if args.command == "paths":
        return _paths(args)

    if args.command == "extract":
        return _extract(args)

    try:
        spec = report.load_spec(args.spec)
        price, as_of = getattr(args, "price", None), getattr(args, "as_of", None)
        if price is None and as_of:
            from tradingagents.dataflows.prices import last_close

            price = last_close(spec.ticker, as_of)
            if price is None:
                print(
                    f"WARNING: no market price for {spec.ticker} on {as_of}; "
                    f"the spec price {spec.valuation.price:,.0f} is used and may be stale.",
                    file=sys.stderr,
                )
        if price is not None:
            spec.valuation.price = price
        model = kernel.run(spec)
    except (ModelError, OSError, KeyError) as exc:
        print(f"MODEL ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"{spec.ticker}: {len(model.rows)} periods, {len(model.checks)} checks, all passed")
    for check in model.checks:
        print(f"  {check.describe()}")

    if args.command == "validate":
        return 0

    try:
        valuation = valuation_module.value(model)
    except ModelError as exc:
        print(f"VALUATION ERROR: {exc}", file=sys.stderr)
        return 2

    if args.command == "compare":
        return _compare(model, valuation, args.reference)

    try:
        package = report.write_package(model, valuation, args.out, args.spec)
    except ModelError as exc:
        print(f"VALUATION ERROR: {exc}", file=sys.stderr)
        return 2

    print(
        f"\n{valuation.period} EPS {valuation.eps:,.1f} {spec.currency} at "
        f"{valuation.pe:,.1f}x; fair value "
        f"{valuation.fair_value_low:,.0f}-{valuation.fair_value_high:,.0f} {spec.currency}"
    )
    print(f"wrote {len(package['tables'])} tables and report.md to {Path(args.out).resolve()}")
    return 0


def _extract(args) -> int:
    """Build or refresh a model spec from the corpus, then report the gaps."""
    import json
    from pathlib import Path

    from . import extract as extract_module

    try:
        extraction = extract_module.extract_filing(args.corpus, args.ticker, args.date)
    except ModelError as exc:
        print(f"EXTRACTION ERROR: {exc}", file=sys.stderr)
        return 2

    print(extract_module.describe(extraction))

    try:
        if args.merge:
            spec = report.load_spec(args.merge)
            spec = extract_module.merge(spec, extraction)
        else:
            if args.price is None:
                print(
                    "\nERROR: a share price is not in the corpus. Pass --price for a new draft, "
                    "or --merge an existing spec.",
                    file=sys.stderr,
                )
                return 2
            spec = extract_module.skeleton(extraction, args.ticker, args.company, args.price)
    except (ModelError, OSError, KeyError) as exc:
        print(f"EXTRACTION ERROR: {exc}", file=sys.stderr)
        return 2

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote draft spec to {path.resolve()}")

    warnings = [w for w in spec.warnings if "Draft spec" in w or "not in a quarterly" in w]
    if warnings:
        print("still needed before this spec can build a full report:")
        for warning in warnings:
            print(f"  - {warning}")
    return 0


def _paths(args) -> int:
    """Print every directory this feature reads or writes, and whether it exists."""
    import os
    from pathlib import Path

    from tradingagents.dataflows.vendors.research_corpus import research_dir_from_env
    from tradingagents.default_config import DEFAULT_CONFIG

    results = Path(DEFAULT_CONFIG["results_dir"]).expanduser()
    cache = Path(DEFAULT_CONFIG["data_cache_dir"]).expanduser()
    memory = Path(DEFAULT_CONFIG["memory_log_path"]).expanduser()
    corpus = research_dir_from_env()
    configured_model = os.environ.get("TRADINGAGENTS_RESEARCH_MODEL", "").strip()

    rows: list[tuple[str, Path, str]] = [
        ("Reports (one folder per run)", results / "reports", "the product: complete_report.md/.html"),
        ("State log", results / (args.ticker or "<TICKER>") / "TradingAgentsStrategy_logs",
         "full_states_log_<date>.json"),
        ("Memory log", memory, "decisions carried into later runs"),
        ("Model specs (LLM-built)", cache / "models", "one <TICKER>.json per built model"),
        ("Checkpoints", cache / "checkpoints", "only when checkpointing is enabled"),
        ("Vendor cache", cache, "prices, filings, news already fetched"),
        ("Golden fixtures", Path(os.environ.get("RESEARCH_GOLDEN_DIR", "")).expanduser()
         if os.environ.get("RESEARCH_GOLDEN_DIR") else Path.home() / ".tradingagents" / "fixtures",
         "reference reports used for comparison only"),
    ]
    if corpus is not None:
        rows.insert(0, ("Research corpus (input)", corpus, "documents + _corpus_manifest.json"))
    if configured_model:
        rows.insert(1, ("Model spec in use", Path(configured_model).expanduser(),
                        "TRADINGAGENTS_RESEARCH_MODEL"))

    print(f"{'What':32} {'Exists':7} Path")
    print("-" * 110)
    for label, path, note in rows:
        print(f"{label:32} {'yes' if path.exists() else 'no':7} {path}")
        print(f"{'':32} {'':7} ({note})")

    if args.ticker:
        reports = results / "reports"
        latest = sorted(
            (p for p in reports.glob(f"{args.ticker}_*") if p.is_dir()), reverse=True
        )
        print("\nLatest report folders for " + args.ticker + ":")
        for folder in latest[:3]:
            marker = "  <-- newest" if folder == latest[0] else ""
            print(f"  {folder}{marker}")
        if not latest:
            print("  none yet")
    return 0


def _compare(model, valuation, reference: str) -> int:
    """Score the model against a reference report. Exit 1 when numbers disagree."""
    from . import compare as compare_module

    try:
        tables = compare_module.load_tables(reference)
        results = compare_module.compare(model, valuation, tables)
    except (ModelError, OSError) as exc:
        print(f"COMPARISON ERROR: {exc}", file=sys.stderr)
        return 2

    if not results:
        print("\nNo comparable table cells found in the reference. Nothing to score.")
        return 0

    print(f"\nComparison against {Path(reference).name} ({len(results)} cells):")
    for comparison in results:
        print(f"  {comparison.describe()}")

    differences = [c for c in results if not c.ok]
    fair_value = compare_module.fair_value_from_text(reference)
    if fair_value:
        low, high = fair_value
        within = (
            abs(valuation.fair_value_low - low) <= max(50.0, low * 0.01)
            and abs(valuation.fair_value_high - high) <= max(50.0, high * 0.01)
        )
        print(
            f"  [{'ok' if within else 'DIFF':4}] fair-value range                "
            f"reference {low:,.0f}-{high:,.0f}  model {valuation.fair_value_low:,.0f}-"
            f"{valuation.fair_value_high:,.0f}"
        )
        if not within:
            differences.append("fair value range")

    matched = len(results) - len(differences)
    print(f"\n{matched}/{len(results)} cells agree within tolerance")
    if differences:
        print(f"{len(differences)} difference(s) need review:")
        for comparison in differences[:20]:
            label = comparison if isinstance(comparison, str) else comparison.label
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
