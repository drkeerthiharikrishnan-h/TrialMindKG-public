"""Tests for pipelines/ctgov.py, using the frozen TULIP-1 registry record from data/snapshots."""
import pytest
import ctgov
from snapshot import Snapshot

@pytest.fixture(scope="module")
def tulip1():
    return Snapshot(mode="offline").get("ctgov", "https://clinicaltrials.gov/api/v2/studies/NCT02446912").body


def test_study_facts(tulip1):
    f = ctgov.study_facts(tulip1)
    assert f["status"] == "COMPLETED" and f["has_results"] is True and f["phase"] == "PHASE3"


def test_primary_outcome_has_posted_analysis(tulip1):
    p1 = [o for o in ctgov.outcomes(tulip1) if o["oid"] == "P1"][0]
    assert p1["source"] == "results" and p1["kind"] == "PRIMARY"
    assert "0.412" in [a["p_value"] for a in p1["analyses"]]
    assert set(p1["groups"].values()) == {"Anifrolumab 150 mg", "Anifrolumab 300 mg", "Placebo"}


def test_outcome_ids_unique(tulip1):
    ids = [o["oid"] for o in ctgov.outcomes(tulip1)]
    assert len(ids) == len(set(ids))


def test_protocol_outcomes_when_no_results():
    s = {"protocolSection": {"outcomesModule": {"primaryOutcomes": [{"measure": "ORR", "timeFrame": "1y"}]}}}
    assert ctgov.outcomes(s) == [dict(oid="P1", kind="PRIMARY", title="ORR", time_frame="1y", source="protocol")]


@pytest.mark.parametrize("name, parts", [
    ("Paclitaxel + Carboplatin", ["Paclitaxel", "Carboplatin"]),
    ("MEDI4736 (Durvalumab)+Tremelimumab", ["MEDI4736 (Durvalumab)", "Tremelimumab"]),
    ("Verubecestat (Part I and Part II)", ["Verubecestat (Part I and Part II)"]),
    ("Xanomeline and Trospium Chloride Capsules", ["Xanomeline", "Trospium Chloride Capsules"]),
])
def test_split_combo(name, parts):
    assert ctgov.split_combo(name) == parts


def test_name_candidates():
    assert ctgov.name_candidates("Aducanumab (BIIB037)") == ["Aducanumab (BIIB037)", "Aducanumab", "BIIB037"]
    assert "Trospium Chloride" in ctgov.name_candidates("Trospium Chloride Capsules")
    assert "Verubecestat" in ctgov.name_candidates("Verubecestat 12 mg (Parts 1 and 2)")


def test_intervention_role():
    assert ctgov.intervention_role(["ACTIVE_COMPARATOR"]) == "comparator"
    assert ctgov.intervention_role(["EXPERIMENTAL", "PLACEBO_COMPARATOR"]) == "background"


def test_adverse_event_summary():
    epoch = Snapshot(mode="offline").get("ctgov", "https://clinicaltrials.gov/api/v2/studies/NCT01739348").body
    ae = ctgov.adverse_event_summary(epoch)
    first = ae["ae_groups"][0]
    assert (first["serious_affected"], first["serious_at_risk"], first["deaths_affected"]) == (134, 702, 9)
    assert ctgov.adverse_event_summary({}) == {}


def test_parse_dose_only_written_doses():
    import ctgov
    assert [d["text"] for d in ctgov.parse_dose("Verubecestat 60mg/40mg; 2.5 mg/kg")] == ["60mg", "40mg", "2.5 mg/kg"]
    assert ctgov.parse_dose("Anifrolumab - higher dose") == []
    assert ctgov.parse_dose("Anifrolumab 300 mg")[0] == {"value": 300.0, "unit": "mg", "text": "300 mg"}


def test_arms_and_registered_endpoints_keep_registry_order():
    import ctgov
    s = {"protocolSection": {"armsInterventionsModule": {"armGroups": [
            {"label": "Drug 10 mg", "type": "EXPERIMENTAL"}, {"label": "Placebo", "type": "PLACEBO_COMPARATOR"}]},
         "outcomesModule": {"primaryOutcomes": [{"measure": "A", "timeFrame": "W52"}],
                            "secondaryOutcomes": [{"measure": "B"}, {"measure": "C"}]}}}
    a = ctgov.arms(s)
    assert [x["aid"] for x in a] == ["A1", "A2"] and a[0]["dose"][0]["value"] == 10.0 and a[1]["dose"] == []
    assert [e["eid"] for e in ctgov.registered_endpoints(s)] == ["P1", "S1", "S2"]


def test_eligibility_ae_terms_and_baseline():
    import ctgov
    s = {"protocolSection": {"eligibilityModule": {"eligibilityCriteria": "Inclusion: adults", "sex": "ALL",
                                                   "minimumAge": "18 Years"}},
         "resultsSection": {"adverseEventsModule": {"eventGroups": [{"id": "EG000", "title": "Drug"}],
                            "seriousEvents": [{"term": "Pneumonia", "organSystem": "Infections", "sourceVocabulary":
                                               "MedDRA 21.0", "stats": [{"groupId": "EG000", "numAffected": 1,
                                                                         "numAtRisk": 93, "numEvents": 1}]}]},
                            "baselineCharacteristicsModule": {"populationDescription": "All randomised",
                                "groups": [{"id": "BG000", "title": "Drug"}],
                                "measures": [{"title": "Age", "paramType": "MEAN", "unitOfMeasure": "years",
                                              "classes": [{"categories": [{"measurements": [
                                                  {"groupId": "BG000", "value": "41.2", "spread": "12"}]}]}]}]}}}
    assert ctgov.eligibility(s) == {"criteria": "Inclusion: adults", "sex": "ALL", "minimum_age": "18 Years"}
    t = ctgov.adverse_event_terms(s, "serious")[0]
    assert t["term"] == "Pneumonia" and t["arms"][0] == dict(group="EG000", title="Drug", affected=1, at_risk=93, events=1)
    assert ctgov.adverse_event_terms(s, "other") == []
    b = ctgov.baseline(s)
    assert b["measures"][0]["values"][0]["value"] == "41.2" and b["groups"] == {"BG000": "Drug"}
