"""Tests for pipelines/contradictions.py."""
import json
import contradictions as cx


class KG:
    def __init__(self):
        self.nodes = {"P01": dict(category="tmkg:ClinicalTrialProgram", what_failed="Phase III: stopped for futility; HR 0.88"),
                      "NCT:1": dict(category="biolink:ClinicalTrial", status="COMPLETED"),
                      "OUT:1": dict(analyses=json.dumps([dict(param_value="0.89", p_value="0.1115")]),
                                    measurements=json.dumps([dict(value="47.26")]), category="tmkg:OutcomeMeasure")}
        self.e = {("P01", "tmkg:has_trial"): [dict(object="NCT:1")], ("NCT:1", "tmkg:has_outcome_measure"): [dict(object="OUT:1")]}

    def out(self, nid, pred):
        return self.e.get((nid, pred), [])


def test_stats_in_text():
    assert cx.stats_in_text("HR 0.88 (p=0.11); 46% responded") == [("hr", 0.88), ("p=", 0.11), ("%", 46.0)]


def test_status_and_number_checks():
    kg = KG()
    st = cx.status_conflicts(kg)
    assert len(st) == 1 and st[0]["result"].startswith("curated says stopped")
    res = {r["statement"]: r["result"] for r in cx.number_conflicts(kg, [("P01", "x", "HR 0.88, p=0.11, 46% and 47.26%")])}
    assert res["hr 0.88"].startswith("close_to_registry_value 0.89")
    assert res["p= 0.11"] == "matches_registry_rounded 0.1115"
    assert res["% 47.26"] == "matches_registry" and res["% 46.0"].startswith("close_to_registry_value 47.26")
