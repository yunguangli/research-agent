"""Scan a research directory into document records with provenance metadata.

A "research corpus" is a folder of materials for one company: filings, earnings
call transcripts, presentations, sell-side notes, and your own meeting notes.
Nothing here talks to a network or a vendor; the folder is the source of truth.

Every record carries what a point-in-time system needs: when the document became
public, what kind of document it is, what may be mined from it, and whether it
may be sent to an LLM. When the date cannot be established the record is kept
but flagged, and it is withheld from historical runs rather than guessed.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

MANIFEST_NAME = "_corpus_manifest.json"

DOC_TYPES = (
    "filing",
    "transcript",
    "presentation",
    "sell_side",
    "internal_note",
    "reference_output",
    "note",
)

# Licence labels. Only ``licensed_no_llm`` is blocked from a prompt; the others
# differ in whether the content may leave the firm, which is a policy decision
# recorded here rather than enforced by code.
LICENSES = ("public", "internal_only", "licensed_summary_only", "licensed_no_llm")

_LLM_USE = {
    "public": "full_text",
    "internal_only": "full_text",
    "licensed_summary_only": "summary_and_quotes",
    "licensed_no_llm": "none",
}

# Ordered: the first matching rule wins. "reference_output" is checked first so
# an existing investment report is not ingested as evidence for its own run.
_CLASSIFIERS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("reference_output", ("latest forecast", "investment report", "research report"), ("thesis",)),
    (
        "filing",
        (
            "quarterly consolidated",
            "financial results",
            "financial statements",
            "tanshin",
            "決算短信",
            "annual report",
            "securities report",
            "10-k",
            "10-q",
        ),
        ("statements", "guidance"),
    ),
    ("transcript", ("earnings call", "transcript", "決算説明会", "q&a"), ("guidance", "management_commentary")),
    (
        "presentation",
        ("presentation", "results briefing", "investor deck", "説明会資料", "slides"),
        ("guidance", "segment_data", "industry"),
    ),
    (
        "sell_side",
        (
            "quick note",
            "nomura",
            "morgan stanley",
            "goldman",
            "ubs",
            "citi",
            "jefferies",
            "daiwa",
            "mizuho",
            "smbc",
            "macquarie",
            "initiation",
            "buy)",
            "overweight)",
            "target price",
        ),
        ("estimates", "thesis", "industry"),
    ),
    (
        "internal_note",
        ("meeting notes", "meeting questions", "minutes", "memo", "call notes"),
        ("management_commentary", "analysis"),
    ),
)

_DEFAULT_LICENSE = {
    "filing": "public",
    "presentation": "public",
    "transcript": "internal_only",
    "sell_side": "licensed_summary_only",
    "internal_note": "internal_only",
    "reference_output": "internal_only",
    "note": "internal_only",
}

_DATE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<!\d)(20\d{2})[-_.](\d{1,2})[-_.](\d{1,2})(?!\d)"), "day"),
    (re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)"), "day"),
    (re.compile(r"(?<!\d)(20\d{2})(\d)(\d{2})(?!\d)"), "day"),
    (re.compile(r"(?<!\d)(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日"), "day"),
    (re.compile(r"(?<!\d)(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+(20\d{2})", re.IGNORECASE), "day_dmy"),
    (re.compile(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(20\d{2})", re.IGNORECASE), "day_mdy"),
    (re.compile(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+(20\d{2})", re.IGNORECASE), "month_name"),
    (re.compile(r"(?<!\d)(20\d{2})[-_.](\d{1,2})(?!\d)"), "month"),
)

_FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


class CorpusError(RuntimeError):
    """A corpus operation failed for a reason the caller must report, not guess around."""


@dataclass
class DocumentRecord:
    """One file in the corpus, with everything later stages need to judge it."""

    document_id: str
    ticker: str
    doc_type: str
    contains: list[str]
    title: str
    source: str
    author: str
    published_at: str | None
    date_precision: str          # day | month | unknown
    date_source: str             # manifest | filename | document | mtime
    retrieved_at: str
    language: str
    mime_type: str
    pages: int
    license: str
    llm_use: str
    content_hash: str
    storage_path: str            # relative to the corpus root
    doc_quality: str = "ok"      # ok | corrected_transcript | reference_output
    excluded: bool = False
    exclusion_reason: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> DocumentRecord:
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


def _sha256(path: Path) -> str:
    """Content hash, or an empty string when the file cannot be read.

    A file open in Word (or held by OneDrive) raises PermissionError; that must
    not fail the whole corpus, because every other document is still usable.
    """
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return "sha256:" + digest.hexdigest()


def _document_id(ticker: str, relative_path: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", Path(relative_path).stem.lower()).strip("-")[:48]
    return f"doc-{ticker}-{slug}" if ticker else f"doc-{slug}"


def classify(name: str) -> tuple[str, list[str]]:
    """Infer the document type from its filename. Filenames are the only signal
    available before extraction, so this is deliberately conservative."""
    lowered = name.lower()
    for doc_type, needles, contains in _CLASSIFIERS:
        if any(needle in lowered for needle in needles):
            return doc_type, list(contains)
    return "note", ["notes"]


def _parse_date(text: str) -> tuple[str, str] | None:
    """Return ``(iso_date, precision)`` for the first date found in ``text``.

    Full-width digits (``2026年８月７日``) are normalised first: Japanese filings
    and transcripts mix both widths, and a missed date would silently withhold a
    document from a historical run.
    """
    text = text.translate(_FULLWIDTH)
    for pattern, kind in _DATE_PATTERNS:
        hit = pattern.search(text)
        if not hit:
            continue
        try:
            if kind == "day_dmy":
                day, month, year = int(hit.group(1)), _MONTHS[hit.group(2).lower()[:3]], int(hit.group(3))
            elif kind == "day_mdy":
                month, day, year = _MONTHS[hit.group(1).lower()[:3]], int(hit.group(2)), int(hit.group(3))
            elif kind == "month_name":
                month, year, day = _MONTHS[hit.group(1).lower()[:3]], int(hit.group(2)), 1
            else:
                year, month = int(hit.group(1)), int(hit.group(2))
                day = int(hit.group(3)) if kind == "day" else 1
            return date(year, month, day).isoformat(), "day" if kind.startswith("day") else "month"
        except (ValueError, KeyError):
            continue
    return None


def _date_from_document(path: Path) -> tuple[str, str] | None:
    """Look for a publication date in the first pages of a document.

    Used only when the filename carries no date. An explicit ``Date:`` line wins;
    otherwise the first day-precision date near the top is taken, which is where
    a filing, note, or deck states its own publication date.
    """
    try:
        from .extract import extract

        text = extract(path).text[:6000]
    except Exception:
        return None
    text = text.translate(_FULLWIDTH)
    for line in text.splitlines():
        if re.match(r"\s*\**\s*(date|dated|as of|report date|published|日付|公表日)\s*\**\s*[:：]", line, re.IGNORECASE):
            parsed = _parse_date(line)
            if parsed:
                return parsed
    parsed = _parse_date(text[:1200])
    if parsed and parsed[1] == "day":
        return parsed
    return None


def _detect_language(name: str) -> str:
    return "ja" if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", name) else "en"


def _mime_type(path: Path) -> str:
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".md": "text/markdown",
        ".txt": "text/plain",
        ".html": "text/html",
        ".htm": "text/html",
    }.get(path.suffix.lower(), "application/octet-stream")


def _manifest_path(root: Path) -> Path:
    return root / MANIFEST_NAME


def _recover_first_json_value(text: str) -> tuple[object, str] | None:
    """The first complete JSON value in ``text``, plus whatever follows it.

    A write interrupted mid-flight can leave a valid document with a second,
    partial copy of its own tail appended. That file cannot be parsed as a
    whole, but the first value in it is intact.
    """
    start = text.find("{")
    if start < 0:
        return None
    try:
        value, end = json.JSONDecoder().raw_decode(text, start)
    except json.JSONDecodeError:
        return None
    return value, text[end:]


def _quarantine_tail(path: Path, tail: str) -> None:
    """Keep an unreadable remainder beside the manifest rather than deleting it."""
    if not tail.strip():
        return
    with contextlib.suppress(OSError):
        path.with_name(path.name + ".torn").write_text(tail, encoding="utf-8")


def _read_manifest_raw(root: Path) -> dict:
    path = _manifest_path(root)
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CorpusError(f"Cannot read corpus manifest {path}: {exc}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        # Refusing to load would block every later run until someone edits the
        # file by hand, so recover the intact part and set the rest aside. The
        # next scan rewrites the manifest cleanly.
        recovered = _recover_first_json_value(text)
        if recovered is None:
            raise CorpusError(f"Cannot read corpus manifest {path}: {exc}") from exc
        raw, tail = recovered
        _quarantine_tail(path, tail)
    return raw if isinstance(raw, dict) else {}


def _manifest_ticker(raw: dict) -> str:
    """The ticker this manifest was last scanned for, when it is unambiguous."""
    documents = raw.get("documents")
    if not isinstance(documents, dict):
        return ""
    tickers = {
        str(entry.get("ticker") or "")
        for entry in documents.values()
        if isinstance(entry, dict)
    }
    tickers.discard("")
    return tickers.pop() if len(tickers) == 1 else ""


def _write_text_atomic(path: Path, text: str) -> None:
    """Replace ``path`` in one step, so a reader never sees a half-written file.

    The manifest lives in the user's OneDrive folder; an interrupted write there
    previously left a truncated file that made every later run fail.
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise CorpusError(f"Cannot write corpus manifest {path}: {exc}") from exc


def load_manifest(root: Path) -> dict[str, dict]:
    """Load user overrides as ``{relative_path: fields}``.

    Only the ``overrides`` section is authoritative. The ``documents`` section is
    the last scan's output, kept for inspection: treating detected metadata as an
    override would freeze a wrong date (for example a file's download time) into
    every later scan.
    """
    raw = _read_manifest_raw(root)
    overrides = raw.get("overrides", {})
    return {str(k): v for k, v in overrides.items() if isinstance(v, dict)}


# Fields a user may pin. Anything else is recomputed on every scan.
OVERRIDABLE_FIELDS = (
    "title",
    "source",
    "author",
    "published_at",
    "license",
    "doc_type",
    "contains",
    "excluded",
    "exclusion_reason",
    "doc_quality",
    "language",
)


def save_manifest(root: Path, records: list[DocumentRecord]) -> Path:
    """Write the scan snapshot and preserve the user's overrides section.

    Skipped entirely when nothing changed: every corpus tool call rescans, and
    rewriting an identical file in a synced folder is how a manifest gets torn.
    """
    path = _manifest_path(root)
    previous = _read_manifest_raw(root)
    overrides = {
        str(k): v for k, v in (previous.get("overrides") or {}).items() if isinstance(v, dict)
    }
    known_tickers = {
        str(key): str(entry.get("ticker") or "")
        for key, entry in (previous.get("documents") or {}).items()
        if isinstance(entry, dict)
    }
    documents = {}
    for record in records:
        # A tool call that does not know the ticker (reading one document by id)
        # must not blank the ticker the previous scan recorded.
        if not record.ticker:
            record.ticker = known_tickers.get(record.storage_path, "")
        documents[record.storage_path] = record.to_dict()
    payload = {"version": 2, "overrides": overrides, "documents": documents}
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    try:
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            return path
    except OSError:
        pass
    _write_text_atomic(path, text)
    return path


def _matches_any(relative_path: str, globs: tuple[str, ...]) -> bool:
    name = Path(relative_path).name
    return any(Path(name).match(pattern) or Path(relative_path).match(pattern) for pattern in globs)


def scan_corpus(
    root: str | Path,
    ticker: str = "",
    *,
    exclude: tuple[str, ...] = (),
    language: str | None = None,
) -> list[DocumentRecord]:
    """Walk ``root`` and return one record per supported file.

    ``exclude`` takes glob patterns (matched against the filename or the path
    relative to the root). Use it to keep a reference report out of the evidence
    set when re-generating that same report.
    """
    root = Path(root).expanduser()
    if not root.is_dir():
        raise CorpusError(f"Research corpus is not a directory: {root}")

    raw = _read_manifest_raw(root)
    overrides = {
        str(k): v for k, v in (raw.get("overrides") or {}).items() if isinstance(v, dict)
    }
    # Reading one document by id builds a store without a ticker. Without this,
    # that store would mint ids without the ticker prefix and then fail to find
    # the ids the listing produced.
    ticker = ticker or _manifest_ticker(raw)
    records: list[DocumentRecord] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == MANIFEST_NAME or path.name.startswith("."):
            continue
        relative = path.relative_to(root).as_posix()
        if path.suffix.lower() not in {".pdf", ".docx", ".md", ".txt", ".html", ".htm"}:
            continue

        override = overrides.get(relative, {})
        doc_type, contains = classify(path.name)
        warnings: list[str] = []

        published_at = override.get("published_at")
        precision = "day" if published_at else "unknown"
        date_source = "manifest" if published_at else ""
        if not published_at:
            parsed = _parse_date(path.stem)
            if parsed:
                published_at, precision = parsed
                date_source = "filename"
            else:
                parsed = _date_from_document(path)
                if parsed:
                    published_at, precision = parsed
                    date_source = "document"
                    warnings.append(
                        "published_at inferred from the document text; "
                        "confirm it in the corpus manifest"
                    )
                else:
                    warnings.append("published_at not found in filename or header; using file mtime")
                    try:
                        mtime = path.stat().st_mtime
                    except OSError:
                        mtime = None
                    published_at = (
                        date.fromtimestamp(mtime).isoformat() if mtime else date.today().isoformat()
                    )
                    precision = "unknown"
                    date_source = "mtime"

        license_label = override.get("license") or _DEFAULT_LICENSE.get(doc_type, "internal_only")
        if license_label not in LICENSES:
            raise CorpusError(
                f"Unknown licence {license_label!r} for {relative}; expected one of {LICENSES}"
            )

        excluded = override.get("excluded", False)
        reason = override.get("exclusion_reason", "")
        if _matches_any(relative, exclude):
            excluded, reason = True, reason or "matched the run's exclude pattern"

        content_hash = _sha256(path)
        if not content_hash:
            warnings.append(
                "file could not be read when the corpus was scanned (open in another "
                "application, or not permitted); its text may be unavailable"
            )

        record = DocumentRecord(
            document_id=override.get("document_id") or _document_id(ticker, relative),
            ticker=override.get("ticker", ticker),
            doc_type=override.get("doc_type", doc_type),
            contains=list(override.get("contains", contains)),
            title=override.get("title") or path.stem,
            source=override.get("source", "local_corpus"),
            author=override.get("author", ""),
            published_at=published_at,
            date_precision=precision,
            date_source=date_source,
            retrieved_at=date.today().isoformat(),
            language=override.get("language") or language or _detect_language(path.name),
            mime_type=_mime_type(path),
            pages=int(override.get("pages", 0) or 0),
            license=license_label,
            llm_use=_LLM_USE[license_label],
            content_hash=content_hash,
            storage_path=relative,
            doc_quality=override.get(
                "doc_quality", "reference_output" if doc_type == "reference_output" else "ok"
            ),
            excluded=bool(excluded),
            exclusion_reason=reason,
            warnings=warnings,
        )
        if record.doc_type not in DOC_TYPES:
            raise CorpusError(
                f"Unknown document type {record.doc_type!r} for {relative}; expected {DOC_TYPES}"
            )
        if any(existing.document_id == record.document_id for existing in records):
            suffix = 2
            base = record.document_id
            while any(existing.document_id == f"{base}-{suffix}" for existing in records):
                suffix += 1
            record.document_id = f"{base}-{suffix}"
        records.append(record)

    save_manifest(root, records)
    return records
