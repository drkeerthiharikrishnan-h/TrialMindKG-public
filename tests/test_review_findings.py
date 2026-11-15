"""Tests for pipelines/review_findings.py: only rows the owner marks 'yes' are confirmed, never deleted, no duplicates."""
import csv
import review_findings as rf


def test_make_and_apply_round_trip(tmp_path, monkeypatch):
    cur = tmp_path
    (cur / "protocol_findings_candidates.csv").write_text(
        "nct,document,finding,value,quote,status,backend,snapshot_key,note\n"
        "NCT01,Prot_000,sample_size,420 participants,A sample size of 420,ok,nim,k,P\n"
        "NCT01,Prot_000,multiplicity,x,y,REJECTED_quote,nim,k,P\n"
        "NCT02,SAP_000,primary_endpoint,CDR-SB,The primary endpoint is CDR-SB,ok,nim,k,P\n")
    (cur / "protocol_findings.csv").write_text("nct,document,finding,value,quote,justification\n")
    monkeypatch.setattr(rf, "CUR", cur)
    book = cur / "r.xlsx"
    assert rf.make(book) == 2                                # the rejected quote is not offered
    from openpyxl import load_workbook
    wb = load_workbook(book)
    ws = wb["Protocols"]
    head = [c.value for c in ws[1]]
    ws.cell(row=2, column=head.index("Accept") + 1, value="yes")
    ws.cell(row=2, column=head.index("Note") + 1, value="p.45")
    ws.cell(row=3, column=head.index("Accept") + 1, value="no")
    wb.save(book)
    assert rf.apply(book, today="2026-10-06")["Protocols"] == 1
    assert rf.apply(book, today="2026-10-06")["Protocols"] == 0          # no duplicate on a second apply
    rows = list(csv.DictReader(open(cur / "protocol_findings.csv")))
    assert len(rows) == 1 and rows[0]["justification"] == "Confirmed by owner 2026-10-06: reviewed in review_findings.xlsx (p.45)"


def test_rebuild_keeps_owner_answers(tmp_path, monkeypatch):
    cur = tmp_path
    (cur / "protocol_findings_candidates.csv").write_text(
        "nct,document,finding,value,quote,status,backend,snapshot_key,note\n"
        "NCT01,Prot_000,sample_size,420 participants,A sample size of 420,ok,nim,k,P\n")
    monkeypatch.setattr(rf, "CUR", cur)
    book = cur / "r.xlsx"
    rf.make(book)
    from openpyxl import load_workbook
    wb = load_workbook(book)
    ws = wb["Protocols"]
    head = [c.value for c in ws[1]]
    ws.cell(row=2, column=head.index("Accept") + 1, value="yes")
    ws.cell(row=2, column=head.index("Value") + 1, value="420 randomised participants")
    wb.save(book)
    with open(cur / "protocol_findings_candidates.csv", "a") as f:          # new proposals arrive
        f.write("NCT02,SAP_000,primary_endpoint,CDR-SB,The primary endpoint is CDR-SB,ok,nim,k,P\n")
    assert rf.make(book) == 2
    rows = list(load_workbook(book)["Protocols"].iter_rows(values_only=True))
    d = {r[0]: dict(zip(rows[0], r)) for r in rows[1:]}
    assert d["NCT01"]["Accept"] == "yes" and d["NCT01"]["Value"] == "420 randomised participants"
    assert d["NCT02"]["Accept"] is None
