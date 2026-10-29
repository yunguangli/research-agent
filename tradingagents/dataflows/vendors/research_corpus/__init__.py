"""Local research-corpus vendor: filings, transcripts, notes, and sell-side PDFs.

This vendor reads a folder instead of an API. It is opt-in: nothing calls it
unless the run configures the ``research_documents`` category or a caller passes
an explicit directory, so vanilla behaviour is unchanged.
"""

from .manifest import (
    DOC_TYPES,
    LICENSES,
    MANIFEST_NAME,
    CorpusError,
    DocumentRecord,
    load_manifest,
    save_manifest,
    scan_corpus,
)
from .store import (
    RESEARCH_DIR_ENV,
    CorpusStore,
    SearchHit,
    list_research_documents,
    read_research_document,
    research_dir_from_env,
    resolve_corpus_root,
    search_research_documents,
)

__all__ = [
    "DOC_TYPES",
    "LICENSES",
    "MANIFEST_NAME",
    "RESEARCH_DIR_ENV",
    "CorpusError",
    "CorpusStore",
    "DocumentRecord",
    "SearchHit",
    "list_research_documents",
    "load_manifest",
    "read_research_document",
    "research_dir_from_env",
    "resolve_corpus_root",
    "save_manifest",
    "scan_corpus",
    "search_research_documents",
]
