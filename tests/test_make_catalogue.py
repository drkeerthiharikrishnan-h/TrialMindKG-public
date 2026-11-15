"""Tests for pipelines/make_catalogue.py."""
import csv
from openpyxl import load_workbook
import make_catalogue as mc
import raw_docs as rd


def test_cell_text_and_checklist():
    rows = [dict(program="P01", category="Protocol", covers="Protocol;Statistical analysis plan", status=rd.TO_FETCH),
            dict(program="P01", category="Press releases", covers="Press releases", status=rd.DOWNLOADED),
            dict(program="P01", category="Press releases", covers="Press releases", status=rd.CANDIDATE)]
    cl = mc.checklist(rows)
    assert mc.cell_text(cl["P01"]["Statistical analysis plan"]) == "◐ 1 to download"
    assert mc.cell_text(cl["P01"]["Press releases"]) == "✓ 1; ? 1 to verify"
    assert mc.cell_text(cl["P01"].get("FDA documents")) == "✗ none found"


def test_build_from_committed_index(tmp_path):
    out = mc.build(tmp_path / "c.xlsx")
    assert out["programs"] == 34 and out["categories"] == len(rd.CATEGORIES)
    wb = load_workbook(tmp_path / "c.xlsx")
    assert wb.sheetnames[:3] == ["Read me", "Checklist", "Summary"]
    ws = wb["Checklist"]
    assert ws["A2"].value == "P01" and ws.cell(1, 5).value == rd.CATEGORIES[0]


def test_searched_but_none_found_is_not_present():
    rows = [dict(program="P06", category="FAERS adverse-event reports", covers="", status=rd.NONE_FOUND)]
    assert mc.cell_text(mc.checklist(rows)["P06"]["FAERS adverse-event reports"]) == "✗ none found"


def test_summary_question_mark_is_escaped(tmp_path):
    mc.build(tmp_path / "c.xlsx")
    sm = load_workbook(tmp_path / "c.xlsx")["Summary"]
    assert '"~?*"' in sm["C2"].value          # '?' alone is a COUNTIF wildcard (any character)
