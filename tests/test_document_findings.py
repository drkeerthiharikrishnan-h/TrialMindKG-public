"""Tests for pipelines/document_findings.py (offline stand-in; proposals only)."""
import nim, protocol_findings as pf, document_findings as df


def test_documents_are_regulatory_web_and_sources():
    docs = df.documents()
    assert docs and {d["kind"] for d in docs} <= set(df.KINDS) | {"registry"} and all(d["path"].exists() for d in docs)
    assert not any(d["doc"].lower().endswith(".csv.txt.gz") or d["doc"].lower().endswith(".csv") for d in docs)


def test_extract_regulatory_decision():
    passages = [dict(text="This letter grants accelerated approval of ADUHELM for the treatment of Alzheimer's disease.")]
    x = pf.extract(nim.NIM(backend="offline"), "regulatory_decision", passages, findings=df.DOC_FINDINGS)
    assert x["found"] and x["status"] == "ok"


def test_only_confirmed_rows_load(tmp_path):
    f = tmp_path / "d.csv"
    f.write_text("program,kind,document,finding,value,quote,justification\n"
                 "P05,regulatory,ltr.pdf,regulatory_decision,accelerated approval,q,Confirmed by owner 2026-10-06: letter\n"
                 "P5,regulatory,x.pdf,regulatory_decision,x,q,Confirmed by owner 2026-10-06: x\n")
    assert len(df.load_confirmed(f)) == 1
