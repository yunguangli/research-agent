"""The research corpus is evidence: it must be dated, labelled, and bounded."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tradingagents.dataflows.vendors.research_corpus import (
    CorpusError,
    CorpusStore,
    scan_corpus,
)
from tradingagents.dataflows.vendors.research_corpus.extract import extract

FILING = """Fujikura Ltd — Consolidated Financial Results
Date: 2026-05-14

Revenue for the fiscal year was 1,182.4 billion yen.
Operating profit was 188.7 billion yen.
"""

TRANSCRIPT = """Earnings call transcript
Management said component capacity can be added in about six months.
The current project is scheduled to complete by March 2027.
"""

BROKER = """Quick Note — Buy, target price JPY 6,000.
We forecast operating profit of 498.8 billion yen for the next fiscal year.
"""


def _corpus(tmp_path: Path) -> Path:
    root = tmp_path / "fujikura"
    root.mkdir()
    (root / "Fujikura Financial Results 2026514.md").write_text(FILING, encoding="utf-8")
    (root / "Fujikura Earnings Call 2026807.md").write_text(TRANSCRIPT, encoding="utf-8")
    (root / "Nomura Quick Note.md").write_text(BROKER, encoding="utf-8")
    (root / "Fujikura Latest Forecast .md").write_text("FY3/28 revenue 1,650\n", encoding="utf-8")
    undated = root / "meeting notes.md"
    undated.write_text(TRANSCRIPT, encoding="utf-8")
    os.utime(undated, (1_700_000_000, 1_700_000_000))
    return root


def test_scan_classifies_dates_and_licences(tmp_path):
    records = {r.storage_path: r for r in scan_corpus(_corpus(tmp_path), "5803.T")}

    filing = records["Fujikura Financial Results 2026514.md"]
    assert filing.doc_type == "filing"
    assert filing.published_at == "2026-05-14"
    assert filing.date_precision == "day"
    assert filing.license == "public"

    transcript = records["Fujikura Earnings Call 2026807.md"]
    assert transcript.doc_type == "transcript"
    assert transcript.published_at == "2026-08-07"

    broker = records["Nomura Quick Note.md"]
    assert broker.doc_type == "sell_side"
    assert broker.license == "licensed_summary_only"
    assert broker.llm_use == "summary_and_quotes"

    reference = records["Fujikura Latest Forecast .md"]
    assert reference.doc_type == "reference_output"
    assert reference.doc_quality == "reference_output"


def test_point_in_time_hides_later_documents(tmp_path):
    store = CorpusStore(_corpus(tmp_path), "5803.T")

    visible = {r.storage_path for r in store.list_documents("2026-08-07")}
    assert "Fujikura Financial Results 2026514.md" in visible
    assert "Fujikura Earnings Call 2026807.md" in visible
    assert "Nomura Quick Note.md" not in visible  # undated -> fail closed

    withheld = {r.storage_path for r in store.withheld("2026-08-07")}
    assert "Nomura Quick Note.md" in withheld


def test_undated_document_is_withheld_from_a_dated_run(tmp_path):
    root = _corpus(tmp_path)
    store = CorpusStore(root, "5803.T")
    visible = {r.storage_path for r in store.list_documents("2026-10-01")}
    assert "meeting notes.md" not in visible
    assert "meeting notes.md" in {r.storage_path for r in store.withheld("2026-10-01")}

    records = {r.storage_path: r for r in store.records}
    assert records["meeting notes.md"].date_precision == "unknown"
    assert any("mtime" in w for w in records["meeting notes.md"].warnings)

    # With no as-of date the corpus shows everything, undated included.
    assert "meeting notes.md" in {r.storage_path for r in store.list_documents(None)}


def test_exclude_keeps_the_reference_report_out_of_evidence(tmp_path):
    store = CorpusStore(_corpus(tmp_path), "5803.T", exclude=("*Latest Forecast*.md",))
    assert all(r.doc_type != "reference_output" for r in store.list_documents("2026-12-31"))
    excluded = [r for r in store.records if r.excluded]
    assert excluded and excluded[0].doc_quality == "reference_output"


def test_reference_output_is_never_evidence(tmp_path):
    store = CorpusStore(_corpus(tmp_path), "5803.T")
    assert "Fujikura Latest Forecast .md" not in {
        record.storage_path for record in store.list_documents("2026-12-31")
    }
    assert "Fujikura Latest Forecast .md" not in {
        record.storage_path for record in store.withheld("2026-12-31")
    }
    # Not even for an undated request: a report about a company is not evidence
    # for that company, whatever its date.
    assert "Fujikura Latest Forecast .md" not in {
        record.storage_path for record in store.list_documents(None)
    }
    included = {
        record.storage_path for record in store.list_documents(None, include_excluded=True)
    }
    assert "Fujikura Latest Forecast .md" in included


def test_the_withheld_lines_name_the_real_reason(tmp_path):
    """An unknown publication date is fail-closed, not future-dated: the tool
    must not tell the analyst a document was published after the run."""
    from tradingagents.dataflows.vendors.research_corpus import list_research_documents

    root = _corpus(tmp_path)
    (root / "Fujikura Presentation 20261101.md").write_text(TRANSCRIPT, encoding="utf-8")
    out = list_research_documents(ticker="5803.T", as_of="2026-10-01", root=str(root))

    later = next(line for line in out.splitlines() if "published after the analysis date" in line)
    assert "Fujikura Presentation 20261101.md" in later

    unknown = next(line for line in out.splitlines() if "publication date unknown" in line)
    assert "meeting notes.md" in unknown
    assert "Fujikura Presentation" not in unknown


def test_reference_output_cannot_be_read_into_an_analysis(tmp_path):
    store = CorpusStore(_corpus(tmp_path), "5803.T")
    reference = next(r for r in store.records if r.doc_quality == "reference_output")
    with pytest.raises(CorpusError, match="reference output"):
        store.read_document(reference.document_id)
    # It stays on disk for the comparison command, which reads it directly; the
    # evidence path refuses it whether or not a model is involved.
    assert (store.root / reference.storage_path).is_file()


def test_a_file_that_cannot_be_read_does_not_break_the_scan(tmp_path):
    from tradingagents.dataflows.vendors.research_corpus.manifest import _sha256

    assert _sha256(tmp_path / "does-not-exist.docx") == ""


def test_manifest_overrides_win_and_survive_a_rescan(tmp_path):
    root = _corpus(tmp_path)
    scan_corpus(root, "5803.T")
    manifest_path = root / "_corpus_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["overrides"] = {
        "Nomura Quick Note.md": {"published_at": "2026-10-27", "license": "licensed_no_llm"}
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    records = {r.storage_path: r for r in scan_corpus(root, "5803.T")}
    broker = records["Nomura Quick Note.md"]
    assert broker.published_at == "2026-10-27"
    assert broker.date_source == "manifest"
    assert broker.license == "licensed_no_llm"
    assert broker.llm_use == "none"


def test_detected_metadata_is_not_frozen_as_an_override(tmp_path):
    root = _corpus(tmp_path)
    scan_corpus(root, "5803.T")
    payload = json.loads((root / "_corpus_manifest.json").read_text(encoding="utf-8"))
    assert payload["overrides"] == {}
    assert "Fujikura Financial Results 2026514.md" in payload["documents"]


def test_a_torn_manifest_is_recovered_instead_of_blocking_the_run(tmp_path):
    root = _corpus(tmp_path)
    scan_corpus(root, "5803.T")
    manifest_path = root / "_corpus_manifest.json"
    text = manifest_path.read_text(encoding="utf-8")
    # A write interrupted mid-flight: a valid document with its own tail appended.
    manifest_path.write_text(text + text[text.index('"documents"') - 2 :], encoding="utf-8")

    records = scan_corpus(root, "5803.T")
    assert records
    assert (root / "_corpus_manifest.json.torn").is_file()
    # The next scan rewrote it cleanly.
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["version"] == 2


def test_an_unreadable_manifest_is_still_an_error(tmp_path):
    root = _corpus(tmp_path)
    (root / "_corpus_manifest.json").write_text("not json at all", encoding="utf-8")
    with pytest.raises(CorpusError, match="Cannot read corpus manifest"):
        scan_corpus(root, "5803.T")


def test_an_unchanged_scan_does_not_rewrite_the_manifest(tmp_path, monkeypatch):
    from tradingagents.dataflows.vendors import research_corpus
    from tradingagents.dataflows.vendors.research_corpus import manifest

    root = _corpus(tmp_path)
    scan_corpus(root, "5803.T")
    writes: list[str] = []
    original = manifest._write_text_atomic

    def counting(path, text):
        writes.append(str(path))
        return original(path, text)

    monkeypatch.setattr(research_corpus.manifest, "_write_text_atomic", counting)
    scan_corpus(root, "5803.T")
    assert writes == []


def test_a_scan_without_a_ticker_keeps_the_one_the_manifest_recorded(tmp_path):
    root = _corpus(tmp_path)
    scan_corpus(root, "5803.T")

    # A store that knows only the root (a read by document id) must not mint ids
    # without the ticker, nor rewrite the manifest with an empty one.
    store = CorpusStore(root)
    assert all(record.ticker == "5803.T" for record in store.records)
    assert all(record.document_id.startswith("doc-5803.T-") for record in store.records)


def test_a_no_llm_document_cannot_be_read_into_a_prompt(tmp_path):
    root = _corpus(tmp_path)
    scan_corpus(root, "5803.T")
    manifest_path = root / "_corpus_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["overrides"] = {
        "Nomura Quick Note.md": {"license": "licensed_no_llm"}
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    store = CorpusStore(root, "5803.T")
    broker = next(r for r in store.records if r.storage_path == "Nomura Quick Note.md")
    with pytest.raises(CorpusError, match="may not be sent"):
        store.read_document(broker.document_id, for_llm=True)
    # A reviewer may still read it locally.
    assert "target price" in store.read_document(broker.document_id, for_llm=False)


def test_search_returns_citation_ready_snippets(tmp_path):
    store = CorpusStore(_corpus(tmp_path), "5803.T")
    hits = store.search("component capacity", "2026-12-31")
    assert hits
    assert all(hit.document_id.startswith("doc-5803.T-") for hit in hits)
    assert any("six months" in hit.snippet for hit in hits)


def test_search_without_matches_says_so_without_claiming_absence(tmp_path):
    store = CorpusStore(_corpus(tmp_path), "5803.T")
    assert store.search("nonexistenttopic", "2026-12-31") == []


def test_missing_root_is_an_error_not_an_empty_corpus():
    with pytest.raises(CorpusError, match="not a directory"):
        scan_corpus(Path("does-not-exist-anywhere"), "5803.T")


def test_unknown_document_id_is_rejected(tmp_path):
    store = CorpusStore(_corpus(tmp_path), "5803.T")
    with pytest.raises(CorpusError, match="Unknown document_id"):
        store.get("doc-5803.T-invented")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Fujikura Presentation 2026807.pdf", "presentation"),
        ("Fujikura Ltd Quarterly Consolidated 2026807.md", "filing"),
        ("Nomura 5803 Quick Note.pdf", "sell_side"),
        ("meeting questions.md", "internal_note"),
    ],
)
def test_filename_classification(name, expected):
    from tradingagents.dataflows.vendors.research_corpus.manifest import classify

    assert classify(name)[0] == expected


def test_month_precision_date_is_read_from_the_filename(tmp_path):
    root = tmp_path / "c"
    root.mkdir()
    (root / "meeting notes sept 2026.md").write_text(TRANSCRIPT, encoding="utf-8")
    record = scan_corpus(root, "X")[0]
    assert record.published_at == "2026-09-01"
    assert record.date_precision == "month"


def test_language_detection_covers_the_document_set():
    from tradingagents.dataflows.vendors.research_corpus.extract import detect_language

    assert detect_language("Revenue grew 12% year over year.") == "en"
    assert detect_language("当第１四半期の売上高は増加しました。") == "ja"
    assert detect_language("당기 매출이 증가했습니다.") == "ko"
    assert detect_language("本季度收入增长，公司业绩良好。") == "zh-Hans"
    assert detect_language("本季度收入增長，公司業績良好。") == "zh-Hant"


def test_a_document_header_names_its_language(tmp_path):
    root = tmp_path / "c"
    root.mkdir()
    (root / "call 20260807.md").write_text("当第１四半期の売上高は増加しました。" * 20, encoding="utf-8")
    store = CorpusStore(root, "5803.T")
    record = store.list_documents("2026-10-28")[0]
    assert "language: ja" in store.read_document(record.document_id)


def test_text_extraction_handles_markdown_and_rejects_unknown_types(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("# Heading\n\nBody text.\n", encoding="utf-8")
    extracted = extract(path)
    assert "Body text." in extracted.text

    binary = tmp_path / "image.png"
    binary.write_bytes(b"\x89PNG")
    with pytest.raises(CorpusError, match="Unsupported corpus file type"):
        extract(binary)


def test_missing_docs_extra_names_the_fix():
    from tradingagents.dataflows.vendors.research_corpus.extract import _missing

    with pytest.raises(CorpusError, match=r"tradingagents\[docs\]"):
        raise _missing("docs", "pypdf")
