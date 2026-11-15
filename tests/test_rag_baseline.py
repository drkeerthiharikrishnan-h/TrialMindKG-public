"""Tests for pipelines/rag_baseline.py (no model calls)."""
import pytest
import rag_baseline as rb
import nim


def test_chunks_and_bm25_rank_relevant_passage_first():
    docs = [("a.md", "TULIP-1 missed its primary endpoint SRI(4) at week 52. " * 5 + "x" * 2000),
            ("b.md", "Unrelated text about pathways and genes. " * 40)]
    ch = rb.chunks(docs)
    assert all(len(c["text"]) <= rb.CHUNK for c in ch) and {c["path"] for c in ch} == {"a.md", "b.md"}
    top = rb.bm25("Why did TULIP-1 miss its primary endpoint?", ch, k=3)
    assert top[0]["path"] == "a.md"


def test_program_documents_cover_raw_and_pdf_text():
    docs = rb.program_documents("P19")
    assert docs and all(p.startswith(("raw/P19_", "raw_text/P19_")) for p, _ in docs)


def test_offline_backend_cannot_answer():
    with pytest.raises(nim.OfflineUnavailable):
        rb.answer(nim.NIM(backend="offline"), "Why did TULIP-1 miss its primary endpoint?", "P19")
