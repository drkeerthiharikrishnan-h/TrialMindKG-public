"""Tests for pipelines/protocol_findings.py (offline stand-in; proposals only)."""
import nim, protocol_findings as pf


def test_documents_are_protocols_and_saps_only():
    docs = pf.documents()
    assert docs and all(d["doc"].startswith(("Prot", "SAP")) for d in docs) and all(d["path"].exists() for d in docs)


def test_extract_checks_the_quote(monkeypatch):
    passages = [dict(text="The primary efficacy endpoint is the change from baseline in CDR-SB at Week 78. Other text.")]
    x = pf.extract(nim.NIM(backend="offline"), "primary_endpoint", passages)
    assert x["found"] and x["status"] == "ok" and "Week 78" in x["quote"]
    monkeypatch.setitem(nim._HANDLERS, "protocol", lambda m, s: '{"found": true, "value": "x", "quote": "Week 52"}')
    assert pf.extract(nim.NIM(backend="offline"), "primary_endpoint", passages)["status"] == "REJECTED_quote"
    monkeypatch.setitem(nim._HANDLERS, "protocol", lambda m, s: '{"found": false, "value": "", "quote": ""}')
    assert pf.extract(nim.NIM(backend="offline"), "sample_size", passages)["status"] == "not_stated"


def test_only_confirmed_rows_load(tmp_path):
    f = tmp_path / "p.csv"
    f.write_text("nct,document,finding,value,quote,justification\n"
                 "NCT02446912,Prot_000,primary_endpoint,SRI(4) at week 52,q,Confirmed by owner 2026-10-06: protocol p.12\n"
                 "NCT02446912,Prot_000,sample_size,x,q,proposed\n"
                 "NCT02446912,Prot_000,unknown_type,x,q,Confirmed by owner 2026-10-06: x\n")
    assert [r["finding"] for r in pf.load_confirmed(f)] == ["primary_endpoint"]


def test_recheck_accepts_only_verbatim_quotes_from_the_document():
    x = dict(status="REJECTED_quote", quote="spans two passages")
    assert pf.recheck(x, "a quote that spans two passages here")["status"] == "ok"
    assert pf.recheck(x, "a paraphrase of it")["status"] == "REJECTED_quote"
