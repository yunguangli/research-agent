"""Point-in-time access to a research corpus.

The store answers three questions the analyst needs:

* what documents exist for this company as of the analysis date,
* what does one document say (with page anchors, so a claim can be cited),
* which documents mention a term, and what do the surrounding lines say.

Nothing is fetched from a network. A document dated after the run date is
invisible, and an undated document is visible only when no as-of date is given.
That is the same fail-closed rule the vendor router applies to market data.
"""

from __future__ import annotations

import calendar
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .extract import ExtractedText, clip, extract
from .manifest import (
    CorpusError,
    DocumentRecord,
    scan_corpus,
)

RESEARCH_DIR_ENV = "TRADINGAGENTS_RESEARCH_DIR"


def research_dir_from_env() -> Path | None:
    """The configured corpus root, or ``None`` when the feature is off."""
    value = os.environ.get(RESEARCH_DIR_ENV, "").strip()
    return Path(value).expanduser() if value else None


def _resolve_root(root: str | Path | None) -> Path:
    if root is not None:
        return Path(root).expanduser()
    configured = research_dir_from_env()
    if configured is None:
        raise CorpusError(
            f"No research corpus configured: pass a directory or set {RESEARCH_DIR_ENV}. "
            "This is not the same as 'no documents exist'."
        )
    return configured


def _as_of_iso(as_of: str | None) -> str | None:
    if not as_of:
        return None
    return str(as_of)[:10]


def resolve_corpus_root(base: str | Path, ticker: str) -> Path:
    """The folder for ``ticker`` under a shared research root.

    A root can hold one company (point it straight at the folder) or many, one
    subfolder per instrument. Both layouts resolve without configuration.
    """
    base = Path(base).expanduser()
    if not ticker:
        return base
    for name in (ticker, ticker.replace(".", "_"), ticker.split(".")[0]):
        candidate = base / name
        if candidate.is_dir():
            return candidate
    return base


def _effective_date(record: DocumentRecord) -> str | None:
    """When a document became usable, treating month precision as month-end.

    "September 2026" might have been published on the 30th, so a run dated in
    September cannot assume it existed. Day precision is used as given.
    """
    if record.published_at is None:
        return None
    if record.date_precision == "month":
        year, month = (int(part) for part in record.published_at[:7].split("-"))
        return date(year, month, calendar.monthrange(year, month)[1]).isoformat()
    return record.published_at[:10]


@dataclass
class SearchHit:
    document_id: str
    doc_type: str
    published_at: str | None
    snippet: str


class CorpusStore:
    """A scanned corpus plus its text cache, scoped to one ticker and date."""

    def __init__(
        self,
        root: str | Path | None = None,
        ticker: str = "",
        *,
        exclude: tuple[str, ...] = (),
        refresh: bool = True,
    ):
        self.root = _resolve_root(root)
        self.ticker = ticker
        self._exclude = exclude
        self._records: list[DocumentRecord] = []
        self._text: dict[str, ExtractedText] = {}
        if refresh or not self._records:
            self.refresh()

    def refresh(self) -> list[DocumentRecord]:
        self._records = scan_corpus(self.root, self.ticker, exclude=self._exclude)
        self._text.clear()
        return self._records

    @property
    def records(self) -> list[DocumentRecord]:
        return list(self._records)

    def list_documents(
        self,
        as_of: str | None = None,
        types: list[str] | tuple[str, ...] | None = None,
        *,
        include_excluded: bool = False,
    ) -> list[DocumentRecord]:
        """Documents visible for a run dated ``as_of`` (YYYY-MM-DD)."""
        cutoff = _as_of_iso(as_of)
        wanted = {t for t in (types or ()) if t}
        visible: list[DocumentRecord] = []
        for record in self._records:
            # A report written about this company is never evidence for its own
            # analysis, whether or not it carries a publication date.
            if record.doc_quality == "reference_output" and not include_excluded:
                continue
            if record.excluded and not include_excluded:
                continue
            if wanted and record.doc_type not in wanted:
                continue
            if cutoff is not None:
                # Fail closed: an unknown publication date cannot be shown to
                # have existed on the run date, so a historical run does not see it.
                effective = _effective_date(record)
                if effective is None or record.date_precision == "unknown":
                    continue
                if effective > cutoff:
                    continue
            visible.append(record)
        return sorted(visible, key=lambda r: (r.published_at or "", r.document_id))

    def withheld(self, as_of: str | None) -> list[DocumentRecord]:
        """Documents that exist now but were not public on ``as_of``."""
        cutoff = _as_of_iso(as_of)
        if cutoff is None:
            return []
        return [
            record
            for record in self._records
            if not record.excluded
            and record.doc_quality != "reference_output"
            and (
                record.date_precision == "unknown"
                or (_effective_date(record) or "9999") > cutoff
            )
        ]

    def get(self, document_id: str) -> DocumentRecord:
        for record in self._records:
            if record.document_id == document_id:
                return record
        raise CorpusError(
            f"Unknown document_id {document_id!r}. "
            "Call list_research_documents first; do not invent an id."
        )

    def text_of(self, document_id: str) -> ExtractedText:
        if document_id in self._text:
            return self._text[document_id]
        record = self.get(document_id)
        path = self.root / record.storage_path
        if not path.is_file():
            raise CorpusError(f"Document file is missing: {record.storage_path}")
        extracted = extract(path)
        if extracted.pages and not record.pages:
            record.pages = extracted.pages
        record.warnings.extend(w for w in extracted.warnings if w not in record.warnings)
        self._text[document_id] = extracted
        return extracted

    def read_document(
        self, document_id: str, max_chars: int = 20000, *, for_llm: bool = True
    ) -> str:
        """The document text, with a citation header and a licence guard."""
        record = self.get(document_id)
        if record.doc_quality == "reference_output":
            raise CorpusError(
                f"{record.document_id} is a reference output: a report written about this company. "
                "It is excluded from evidence by rule and cannot be read into this analysis."
            )
        if record.excluded:
            raise CorpusError(
                f"{record.document_id} is excluded from this run"
                + (f" ({record.exclusion_reason})" if record.exclusion_reason else "")
                + ". Use only the documents listed as visible."
            )
        if for_llm and record.llm_use == "none":
            raise CorpusError(
                f"{record.document_id} is labelled {record.license}: its content may not be sent "
                "to a model. Cite the document as unavailable for analysis instead."
            )
        extracted = self.text_of(document_id)
        from .extract import detect_language

        language = detect_language(extracted.text)
        header = (
            f"DOCUMENT {record.document_id}\n"
            f"title: {record.title}\n"
            f"type: {record.doc_type} | published: {record.published_at or 'unknown'} "
            f"({record.date_precision}) | pages: {record.pages or '?'} | licence: {record.license} | "
            f"language: {language}\n"
            f"source: {record.source} | hash: {record.content_hash}\n"
        )
        if record.warnings:
            header += "warnings: " + "; ".join(record.warnings) + "\n"
        return header + "\n" + clip(extracted.text, max_chars)

    def search(
        self,
        query: str,
        as_of: str | None = None,
        limit: int = 8,
        *,
        types: list[str] | tuple[str, ...] | None = None,
    ) -> list[SearchHit]:
        """Keyword search over visible documents, returning citation-ready snippets."""
        terms = [t for t in re.split(r"\W+", query.lower()) if len(t) > 1]
        if not terms:
            raise CorpusError("Search query needs at least one word of two or more characters.")

        hits: list[SearchHit] = []
        for record in self.list_documents(as_of, types):
            if record.llm_use == "none":
                continue
            text = self.text_of(record.document_id).text
            paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n(?=--- page)", text) if p.strip()]
            scored: list[tuple[int, int, str]] = []
            for index, paragraph in enumerate(paragraphs):
                lowered = paragraph.lower()
                score = sum(lowered.count(term) for term in terms)
                if score:
                    scored.append((score, index, paragraph))
            if not scored:
                continue
            scored.sort(key=lambda item: (-item[0], item[1]))
            for _score, index, paragraph in scored[:2]:
                page = _page_of(text, index, paragraphs)
                location = f"page {page}" if page else f"paragraph {index + 1}"
                snippet = re.sub(r"\s+", " ", paragraph)[:700]
                hits.append(
                    SearchHit(
                        document_id=record.document_id,
                        doc_type=record.doc_type,
                        published_at=record.published_at,
                        snippet=f"[{record.document_id} | {location} | {record.published_at}] {snippet}",
                    )
                )
        hits.sort(key=lambda h: (h.published_at or "", h.document_id))
        return hits[-limit:]


def _page_of(text: str, index: int, paragraphs: list[str]) -> int | None:
    """Page number for paragraph ``index``, from the nearest preceding marker."""
    position = text.find(paragraphs[index][:80])
    if position < 0:
        return None
    markers = list(re.finditer(r"--- page (\d+) ---", text[:position]))
    return int(markers[-1].group(1)) if markers else None


# --- vendor entry points ---------------------------------------------------
# Registered in dataflows/router.py as the "corpus" vendor so the analyst tools
# can reach a local folder the same way they reach any other data source.


def list_research_documents(
    ticker: str = "",
    as_of: str = "",
    types: str | list[str] | None = None,
    root: str | None = None,
) -> str:
    """List the documents visible for ``ticker`` on ``as_of``."""
    wanted = [t.strip() for t in types.split(",")] if isinstance(types, str) else list(types or [])
    store = CorpusStore(root, ticker)
    visible = store.list_documents(as_of, wanted)
    withheld = store.withheld(as_of)
    excluded = [
        r for r in store.records if r.excluded or r.doc_quality == "reference_output"
    ]

    lines = [f"RESEARCH CORPUS — {ticker or 'unspecified'} (as of {as_of or 'today'})"]
    lines.append(f"root: {store.root}")
    if not visible:
        lines.append("NO_DATA_AVAILABLE: no visible documents match this request.")
    for record in visible:
        lines.append(
            f"- {record.document_id} | {record.doc_type} | published {record.published_at} "
            f"({record.date_precision} via {record.date_source}) | {record.pages or '?'}p | "
            f"{record.license} | {record.title}"
        )
    if withheld:
        # Two different reasons, and the wrong one tells the analyst a document
        # exists on a date it may simply not have: an unknown publication date
        # is fail-closed, not future-dated.
        cutoff = _as_of_iso(as_of) or ""
        dated, undated = [], []
        for record in withheld:
            future = record.date_precision != "unknown" and (_effective_date(record) or "9999") > cutoff
            (dated if future else undated).append(record)
        if dated:
            lines.append("Withheld (published after the analysis date): " + ", ".join(
                f"{r.storage_path} ({r.published_at})" for r in dated
            ))
        if undated:
            lines.append(
                "Withheld (publication date unknown, so a dated run cannot be shown it existed "
                "by then): "
                + ", ".join(f"{r.storage_path} ({r.published_at or 'date unknown'})" for r in undated)
            )
    if excluded:
        lines.append(
            "Excluded from evidence; do not read these and do not treat them as source material: "
            + ", ".join(f"{r.storage_path} ({r.exclusion_reason or 'reference output'})" for r in excluded)
        )
    return "\n".join(lines)


def read_research_document(
    document_id: str,
    max_chars: int = 20000,
    root: str | None = None,
) -> str:
    """Read one corpus document, bounded and citation-headed."""
    store = CorpusStore(root, "")
    return store.read_document(document_id, max_chars=max_chars, for_llm=True)


def search_research_documents(
    query: str,
    ticker: str = "",
    as_of: str = "",
    limit: int = 8,
    types: str | list[str] | None = None,
    root: str | None = None,
) -> str:
    """Search the corpus and return citation-ready snippets."""
    wanted = [t.strip() for t in types.split(",")] if isinstance(types, str) else list(types or [])
    store = CorpusStore(root, ticker)
    hits = store.search(query, as_of, limit, types=wanted)
    if not hits:
        return (
            f"NO_DATA_AVAILABLE: no visible document mentions {query!r} on or before "
            f"{as_of or 'today'}. Nothing here says the topic is absent from the company."
        )
    return "\n\n".join(hit.snippet for hit in hits)
