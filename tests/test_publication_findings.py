"""Tests for pipelines/publication_findings.py (offline stand-in; proposals only)."""
import nim, protocol_findings as pf, publication_findings as pubf


def test_documents_are_trial_papers_named_outside_the_full_text():
    docs = pubf.documents()
    assert len({(d["record"], d["part"]) for d in docs}) == len(docs) and all(p.exists() for d in docs for p in d["paths"])
    assert len(docs) > len(pubf.documents(all_records=False)) > 0
    assert {d["record"].split("_")[0] for d in docs} <= {"PMID", "PMC", "PPR"}


def test_extract_with_publication_findings():
    passages = [dict(text="The primary endpoint was not met (hazard ratio 0.88; 95% CI 0.75-1.03). Other text here.")]
    x = pf.extract(nim.NIM(backend="offline"), "primary_result", passages, findings=pubf.PUB_FINDINGS)
    assert x["found"] and x["status"] == "ok" and "0.88" in x["quote"]


def test_only_confirmed_rows_load(tmp_path):
    f = tmp_path / "p.csv"
    f.write_text("program,record,pmid,finding,value,quote,justification\n"
                 "P25,PMID_12345,12345,primary_result,HR 0.88,q,Confirmed by owner 2026-10-06: abstract\n"
                 "P25,PPR_PPR1,,primary_result,x,q,Confirmed by owner 2026-10-06: preprint\n"
                 "P25,PMID_12345,12345,subgroup_finding,x,q,\n")
    assert [r["record"] for r in pubf.load_confirmed(f)] == ["PMID_12345", "PPR_PPR1"]


def test_read_text_handles_gzip_supplements(tmp_path):
    import gzip
    gz, md = tmp_path / "s.txt.gz", tmp_path / "a.md"
    gz.write_bytes(gzip.compress("primary endpoint met".encode()))
    md.write_text("abstract text", encoding="utf-8")
    assert pubf.read_text(gz) == "primary endpoint met"
    assert pubf.read_text(md) == "abstract text"
