"""Tests for pipelines/make_ground_truth_template.py."""
from openpyxl import load_workbook
import make_ground_truth_template as gt


def test_workbook_structure(tmp_path):
    counts = gt.build(tmp_path / "gt.xlsx")
    assert counts == dict(trials=46, drugs=41, conditions=45, outcomes=45, golden=counts["golden"])
    wb = load_workbook(tmp_path / "gt.xlsx")
    assert wb.sheetnames == ["Read me", "Trials", "Drugs", "Conditions", "Trial outcomes", "Golden questions", "Lists"]
    t = wb["Trials"]
    assert t["D1"].value == "Pipeline NCT" and t["H1"].value == "Confirmed NCT"
    assert t["O2"].value == '=IF(H2="","",IF(TRIM(H2)=TRIM(D2),"yes","DIFFERENT"))'
    assert t["D2"].value.startswith("NCT")
    assert len(t.data_validations.dataValidation) == 1


def test_trial_rows_match_report():
    import pandas as pd
    from kg_query import KGIndex
    rows = gt.trial_rows(KGIndex(), pd.read_csv(gt.sk.OUT / "resolution_report.csv"))
    tulip = [r for r in rows if r["trial_name"] == "TULIP-1"][0]
    assert tulip["pipeline_nct"] == "NCT02446912" and tulip["match_rule"] == "curator_override:high"
