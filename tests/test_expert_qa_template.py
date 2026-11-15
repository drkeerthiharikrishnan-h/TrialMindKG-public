"""Tests for pipelines/make_expert_qa_template.py (writes into a temp folder)."""
import openpyxl
import make_expert_qa_template as mq


def test_rows_are_stems_only_and_cover_every_program_and_kind():
    rows = mq.rows()
    assert len(rows) == len(mq.PROGRAMS) * sum(len(s) for s in mq.STEMS.values())
    assert {r[1] for r in rows} == {p[0] for p in mq.PROGRAMS}
    assert {r[5] for r in rows} == set(mq.STEMS)
    assert all(v is None for r in rows for v in r[8:])            # no answers pre-filled


def test_rewrite_keeps_owner_answers_and_added_questions(tmp_path):
    p = tmp_path / "qa.xlsx"
    mq.write(p)
    wb = openpyxl.load_workbook(p)
    ws = wb["Questions"]
    ws.cell(row=2, column=9, value="my answer")
    ws.append(["X1", "P13", "", "SOD1", "ALS", "multihop", "", "my own question"] + [None] * 6)
    wb.save(p)
    _, n = mq.write(p)
    ws = openpyxl.load_workbook(p)["Questions"]
    assert ws.cell(row=2, column=9).value == "my answer"
    assert n == len(mq.rows()) + 1 and ws.cell(row=ws.max_row, column=1).value == "X1"
