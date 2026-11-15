"""Tests for pipelines/biomarkers.py (offline stand-in; proposals only, owner confirmation required)."""
import nim, biomarkers as bm


class FakeKG:
    nodes = {"OUTCOME:NCT02453282:S1": {"category": "tmkg:OutcomeMeasure",
                                         "name": "OS; PD-L1 (TC >=25%) Analysis Set Population"},
             "OUTCOME:NCT00000001:P1": {"category": "tmkg:OutcomeMeasure", "name": "Change in LDL cholesterol"}}


def test_candidates_need_a_subgroup_cue():
    assert [n for n, _ in bm.candidate_titles(FakeKG())] == ["OUTCOME:NCT02453282:S1"]


def test_extract_verifies_quote(monkeypatch):
    rows = bm.extract(nim.NIM(backend="offline"), "OS; PD-L1 (TC >=25%) Analysis Set Population")
    assert rows[0]["definition_quote"] == "PD-L1 (TC >=25%)" and rows[0]["status"] == "ok"
    monkeypatch.setitem(nim._HANDLERS, "biomarker", lambda m, s: '{"biomarkers": [{"biomarker": "EGFR", '
                        '"definition_quote": "EGFR mutation-positive"}]}')
    assert bm.extract(nim.NIM(backend="offline"), "OS; PD-L1 (TC >=25%)")[0]["status"] == "REJECTED_quote"


def test_only_owner_confirmed_rows_load(tmp_path):
    f = tmp_path / "b.csv"
    f.write_text("node_id,biomarker,definition,ensembl,justification\n"
                 "OUTCOME:NCT02453282:S1,PD-L1 tumour cells,PD-L1 (TC >=25%),ENSG00000120217,"
                 "Confirmed by owner 2026-10-06: title text\n"
                 "OUTCOME:NCT02453282:S2,PD-L1,x,,proposed\n")
    rows = bm.load_confirmed(f)
    assert len(rows) == 1 and bm.slug(rows[0]["biomarker"]) == "PD_L1_TUMOUR_CELLS"
