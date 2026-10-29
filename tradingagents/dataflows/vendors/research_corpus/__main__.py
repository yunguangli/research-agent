"""Diagnose a research corpus before a run uses it.

    python -m tradingagents.dataflows.vendors.research_corpus <directory> \\
        --ticker 5803.T --as-of 2026-10-28

Prints what the analyst would see on that date, what was withheld because it was
published later (or has no known date), and how much text each file yielded. Run
this first when a document you expected does not appear in a report.
"""

from __future__ import annotations

import argparse

from .extract import extract
from .manifest import MANIFEST_NAME, CorpusError
from .store import CorpusStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect a local research corpus.")
    parser.add_argument("root", help="Directory holding the company's documents")
    parser.add_argument("--ticker", default="", help="Instrument the corpus belongs to")
    parser.add_argument("--as-of", default="", help="Run date, YYYY-MM-DD")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Glob of documents to keep out of this run (repeatable)",
    )
    parser.add_argument("--search", default="", help="Run a keyword search after listing")
    args = parser.parse_args(argv)

    try:
        store = CorpusStore(args.root, args.ticker, exclude=tuple(args.exclude))
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return 2

    visible = store.list_documents(args.as_of or None)
    print(f"root: {store.root}")
    print(f"as of: {args.as_of or 'unset (everything visible)'}")
    print(f"\n{len(visible)} visible document(s)\n")
    for record in visible:
        print(
            f"  {record.document_id}\n"
            f"    type={record.doc_type} published={record.published_at} "
            f"({record.date_precision} via {record.date_source}) "
            f"licence={record.license} pages={record.pages or '?'}\n"
            f"    {record.storage_path}"
        )
        for warning in record.warnings:
            print(f"    ! {warning}")

    withheld = store.withheld(args.as_of or None)
    if withheld:
        print("\nWithheld for this date:")
        for record in withheld:
            reason = (
                "no known publication date"
                if record.date_precision == "unknown"
                else f"published {record.published_at}"
            )
            print(f"  {record.storage_path}  ({reason})")
        print(
            f"  To include one anyway, add it under \"overrides\" in "
            f"{MANIFEST_NAME} with an explicit published_at."
        )

    excluded = [
        record for record in store.records
        if record.excluded or record.doc_quality == "reference_output"
    ]
    if excluded:
        print("\nExcluded from evidence:")
        for record in excluded:
            print(f"  {record.storage_path}  ({record.exclusion_reason or 'reference output'})")

    print("\nExtraction:")
    for record in visible:
        path = store.root / record.storage_path
        try:
            extracted = extract(path)
        except CorpusError as exc:
            print(f"  {record.storage_path}: UNAVAILABLE ({exc})")
            continue
        note = f"  ! {extracted.warnings[0]}" if extracted.warnings else ""
        print(
            f"  {record.storage_path[:58]:60} pages={extracted.pages:3} "
            f"empty={extracted.empty_pages:3} chars={len(extracted.text):7}{note}"
        )

    if args.search:
        print(f"\nSearch {args.search!r}:")
        hits = store.search(args.search, args.as_of or None, limit=8)
        if not hits:
            print("  no visible document mentions this; that is not evidence of absence")
        for hit in hits:
            print(f"  {hit.snippet[:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
