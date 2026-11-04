"""What a model spec was built from, and whether it still matches.

A spec on disk is a forecast made from a corpus on a date. Reusing it later is
normal — rebuilding costs a model call — but only when the inputs it was built
from are the same. This module writes the inputs beside the spec and compares
them on the next load, so "reused" comes with a verdict instead of a guess.

Elapsed time is not a dependency: a spec built a month ago is still compatible
if nothing it read has changed, and one built an hour ago is not if a filing
was added. The price is not a dependency either: it is applied to the valuation
after the operating forecast is loaded.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

# Bump when the builder prompt/repair rules, or the arithmetic that produces
# the forecast rows, change: an old spec was produced by different logic.
BUILDER_VERSION = "1"
KERNEL_VERSION = "1"

META_SUFFIX = ".meta.json"

COMPATIBLE = "compatible"
UNKNOWN = "unknown"
INCOMPATIBLE = "incompatible"
NOT_APPLICABLE = "not_applicable"


def meta_path(spec_path) -> Path:
    """The manifest beside a spec: ``4062.T.json`` -> ``4062.T.meta.json``."""
    return Path(spec_path).with_suffix(META_SUFFIX)


def _sha256(path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def _manifest_sha(corpus) -> str:
    if not corpus:
        return ""
    manifest = Path(corpus) / "_corpus_manifest.json"
    return _sha256(manifest) if manifest.is_file() else ""


def _canonical_documents(manifest: dict) -> list[dict]:
    """The document identity a model depends on, without volatile metadata.

    The manifest is rewritten on every scan and stamps ``retrieved_at`` with the
    scan date. Hashing the file therefore made a corpus "change" every day, and
    a run invalidated its own model between the analysts' scan and the audit.
    Only the content and the fields that change how it is used are hashed.
    """
    documents = manifest.get("documents") or {}
    values = documents.values() if isinstance(documents, dict) else documents
    entries = []
    for doc in values:
        if not isinstance(doc, dict):
            continue
        entries.append({
            "document_id": doc.get("document_id"),
            "content_hash": doc.get("content_hash"),
            "published_at": doc.get("published_at"),
            "license": doc.get("license"),
            "llm_use": doc.get("llm_use"),
            "doc_type": doc.get("doc_type"),
            "excluded": bool(doc.get("excluded")),
            "exclusion_reason": doc.get("exclusion_reason") or "",
        })
    entries.sort(key=lambda entry: (str(entry["document_id"] or ""), str(entry["content_hash"] or "")))
    return entries


def corpus_digest(corpus) -> str:
    """A stable hash of what the corpus contains, ignoring scan timestamps."""
    if not corpus:
        return ""
    manifest = Path(corpus) / "_corpus_manifest.json"
    if not manifest.is_file():
        return ""
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _sha256(manifest)     # unreadable: fall back to the bytes
    if not isinstance(data, dict):
        return _sha256(manifest)
    payload = {
        "version": data.get("version"),
        "overrides": data.get("overrides") or {},
        "documents": _canonical_documents(data),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def build_manifest(
    spec_path,
    *,
    ticker: str,
    unit: str,
    currency: str,
    corpus,
    as_of: str,
) -> dict:
    """The dependencies a spec's numbers depend on, as a JSON-ready dict."""
    return {
        "spec_sha256": _sha256(spec_path),
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "builder_version": BUILDER_VERSION,
        "kernel_version": KERNEL_VERSION,
        "ticker": str(ticker or ""),
        "unit": str(unit or ""),
        "currency": str(currency or ""),
        "as_of": str(as_of or ""),
        "corpus_root": str(corpus) if corpus else "",
        "corpus_manifest_sha256": _manifest_sha(corpus),
        "corpus_digest": corpus_digest(corpus),
        "corpus_digest_version": 1,
    }


def write_manifest(spec_path, **kwargs) -> Path:
    """Write the manifest beside a spec that was just built; return its path."""
    path = meta_path(spec_path)
    path.write_text(
        json.dumps(build_manifest(spec_path, **kwargs), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def check(spec_path, *, ticker: str, unit: str, currency: str, corpus) -> tuple[str, str]:
    """``(status, reason)`` for a spec loaded from disk.

    ``incompatible`` means a material dependency differs; ``unknown`` means the
    file has no manifest (or one that cannot be read), so compatibility cannot
    be established either way.
    """
    path = meta_path(spec_path)
    if not path.is_file():
        return UNKNOWN, "the spec has no dependency manifest (written before reuse tracking)"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return UNKNOWN, f"the dependency manifest is unreadable ({exc})"
    if not isinstance(manifest, dict):
        return UNKNOWN, "the dependency manifest is not a JSON object"

    if manifest.get("spec_sha256") != _sha256(spec_path):
        return INCOMPATIBLE, "the spec file changed after its manifest was written"

    differences = []
    for key, expected in (("ticker", ticker), ("unit", unit), ("currency", currency)):
        recorded = str(manifest.get(key) or "")
        if recorded and str(expected or "") and recorded.casefold() != str(expected).casefold():
            differences.append(f"{key} {recorded!r} != {expected!r}")
    for key, expected in (
        ("builder_version", BUILDER_VERSION),
        ("kernel_version", KERNEL_VERSION),
    ):
        recorded = str(manifest.get(key) or "")
        if recorded and recorded != expected:
            differences.append(f"{key} {recorded} != {expected}")
    if differences:
        return INCOMPATIBLE, "; ".join(differences)

    recorded_corpus = str(manifest.get("corpus_manifest_sha256") or "")
    current_corpus = _manifest_sha(corpus)
    recorded_digest = str(manifest.get("corpus_digest") or "")
    if recorded_digest:
        current_digest = corpus_digest(corpus)
        if not current_digest:
            return UNKNOWN, "the corpus manifest is missing, so its digest cannot be compared"
        if recorded_digest != current_digest:
            return INCOMPATIBLE, "the corpus content changed since the spec was built"
    else:
        # A manifest written before canonical digests: its raw hash includes the
        # scan date, so a mismatch cannot tell a content change from a re-scan.
        if not recorded_corpus or not current_corpus:
            return UNKNOWN, "the corpus manifest is missing on one side, so its hash cannot be compared"
        if recorded_corpus != current_corpus:
            return UNKNOWN, (
                "the spec predates canonical corpus digests; its raw manifest hash no "
                "longer matches, which may only be the scan date"
            )

    return COMPATIBLE, (
        "spec hash, ticker, unit/currency, corpus content and builder/kernel versions match"
    )


def describe(status: str, reason: str) -> str:
    """One line for the report header or a log."""
    label = {
        COMPATIBLE: "compatible",
        UNKNOWN: "compatibility unknown",
        INCOMPATIBLE: "INCOMPATIBLE",
        NOT_APPLICABLE: "built for this run",
    }.get(status, status or "unknown")
    return f"{label} — {reason}" if reason else label
