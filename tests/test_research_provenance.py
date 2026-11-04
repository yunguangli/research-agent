"""A spec's dependency manifest: reused becomes a verdict, not a guess."""

from __future__ import annotations

import json

from tradingagents.research import provenance


def _corpus(tmp_path, manifest: dict | None = None):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "_corpus_manifest.json").write_text(
        json.dumps(manifest or {"documents": [{"document_id": "doc-a", "content_hash": "1"}]}),
        encoding="utf-8",
    )
    return corpus


def _spec(tmp_path, **overrides):
    path = tmp_path / "TEST.json"
    path.write_text(json.dumps({"ticker": "TEST", "unit": "JPY_bn"}), encoding="utf-8")
    provenance.write_manifest(
        path,
        ticker=overrides.pop("ticker", "TEST"),
        unit=overrides.pop("unit", "JPY_bn"),
        currency=overrides.pop("currency", "JPY"),
        corpus=overrides.pop("corpus", None),
        as_of="2026-11-02",
        **overrides,
    )
    return path


def _check(path, tmp_path, **overrides):
    return provenance.check(
        path,
        ticker=overrides.pop("ticker", "TEST"),
        unit=overrides.pop("unit", "JPY_bn"),
        currency=overrides.pop("currency", "JPY"),
        corpus=overrides.pop("corpus", None),
    )


def test_a_manifest_written_with_the_same_inputs_is_compatible(tmp_path):
    corpus = _corpus(tmp_path)
    path = _spec(tmp_path, corpus=corpus)
    status, reason = _check(path, tmp_path, corpus=corpus)
    assert status == provenance.COMPATIBLE
    assert "match" in reason


def test_a_spec_without_a_manifest_is_unknown_not_compatible(tmp_path):
    path = tmp_path / "TEST.json"
    path.write_text("{}", encoding="utf-8")
    status, reason = _check(path, tmp_path)
    assert status == provenance.UNKNOWN
    assert "no dependency manifest" in reason


def test_editing_the_spec_after_its_manifest_is_incompatible(tmp_path):
    path = _spec(tmp_path)
    path.write_text(json.dumps({"ticker": "TEST", "unit": "JPY_bn", "changed": True}),
                    encoding="utf-8")
    status, reason = _check(path, tmp_path)
    assert status == provenance.INCOMPATIBLE
    assert "changed after" in reason


def test_a_new_corpus_document_is_incompatible(tmp_path):
    corpus = _corpus(tmp_path)
    path = _spec(tmp_path, corpus=corpus)
    (corpus / "_corpus_manifest.json").write_text(
        json.dumps({"documents": [{"document_id": "doc-a", "content_hash": "1"},
                                  {"document_id": "doc-b", "content_hash": "2"}]}),
        encoding="utf-8",
    )
    status, reason = _check(path, tmp_path, corpus=corpus)
    assert status == provenance.INCOMPATIBLE
    assert "corpus content changed" in reason


def test_a_rescan_does_not_invalidate_the_spec(tmp_path):
    """The manifest stamps retrieved_at on every scan; that is not a content change."""
    corpus = _corpus(tmp_path)
    path = _spec(tmp_path, corpus=corpus)
    data = json.loads((corpus / "_corpus_manifest.json").read_text(encoding="utf-8"))
    data["documents"][0]["retrieved_at"] = "2026-11-03"
    (corpus / "_corpus_manifest.json").write_text(json.dumps(data), encoding="utf-8")

    status, _reason = _check(path, tmp_path, corpus=corpus)
    assert status == provenance.COMPATIBLE


def test_a_changed_document_content_hash_is_incompatible(tmp_path):
    corpus = _corpus(tmp_path)
    path = _spec(tmp_path, corpus=corpus)
    data = json.loads((corpus / "_corpus_manifest.json").read_text(encoding="utf-8"))
    data["documents"][0]["content_hash"] = "changed"
    (corpus / "_corpus_manifest.json").write_text(json.dumps(data), encoding="utf-8")

    status, _reason = _check(path, tmp_path, corpus=corpus)
    assert status == provenance.INCOMPATIBLE


def test_a_pre_digest_manifest_with_a_different_raw_hash_is_unknown(tmp_path):
    """An old manifest's raw hash includes the scan date; a mismatch proves nothing."""
    corpus = _corpus(tmp_path)
    path = _spec(tmp_path, corpus=corpus)
    meta_path = provenance.meta_path(path)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.pop("corpus_digest")
    meta["corpus_manifest_sha256"] = "0" * 64
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    status, reason = _check(path, tmp_path, corpus=corpus)
    assert status == provenance.UNKNOWN
    assert "scan date" in reason


def test_a_different_unit_is_incompatible(tmp_path):
    path = _spec(tmp_path, unit="JPY_bn")
    status, reason = _check(path, tmp_path, unit="USD_m")
    assert status == provenance.INCOMPATIBLE
    assert "unit" in reason


def test_a_different_ticker_is_incompatible(tmp_path):
    path = _spec(tmp_path)
    status, reason = _check(path, tmp_path, ticker="OTHER")
    assert status == provenance.INCOMPATIBLE
    assert "ticker" in reason


def test_a_new_kernel_version_invalidates_the_spec(tmp_path, monkeypatch):
    path = _spec(tmp_path)
    monkeypatch.setattr(provenance, "KERNEL_VERSION", "999")
    status, reason = _check(path, tmp_path)
    assert status == provenance.INCOMPATIBLE
    assert "kernel_version" in reason


def test_a_missing_corpus_manifest_on_one_side_is_unknown(tmp_path):
    corpus = _corpus(tmp_path)
    path = _spec(tmp_path, corpus=None)   # no corpus hash recorded
    status, reason = _check(path, tmp_path, corpus=corpus)
    assert status == provenance.UNKNOWN
    assert "corpus manifest" in reason
