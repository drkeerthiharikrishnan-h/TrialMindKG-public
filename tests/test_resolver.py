"""Tests for pipelines/resolver.py (offline stand-in backend) and curator drug overrides."""
import nim, resolver as rv, seed_kg as sk

IDX = rv.build_index()


def test_index_from_frozen_chembl_responses():
    ids = {m["chembl_id"] for m in IDX}
    assert "CHEMBL4298058" in ids and len(IDX) > 200
    bemp = next(m for m in IDX if m["chembl_id"] == "CHEMBL4298058")
    assert "Nktr-214" in bemp["synonyms"]


def test_exact_match_wins():
    r = rv.Resolver(nim.NIM(backend="offline"), IDX).resolve("NKTR-214")
    assert r[0]["chembl_id"] == "CHEMBL4298058" and r[0]["method"] == "exact name/synonym"


def test_passage_exclusion_prevents_leakage():
    bemp = next(m for m in IDX if m["chembl_id"] == "CHEMBL4298058")
    assert "nktr" not in rv.passage(bemp, exclude=["Nktr-214", "Nktr 214"]).split("synonyms:")[1].lower().split("aldesleukin")[0]


def test_evaluate_is_deterministic_and_counts_cases():
    a = rv.evaluate(nim.NIM(backend="offline"), IDX, limit=40)
    b = rv.evaluate(nim.NIM(backend="offline"), IDX, limit=40)
    assert a["n"] == 40 and a["top1"] == b["top1"] and 0 <= a["top1"] <= a["top3"] <= 1


def test_proposals_are_never_auto_accepted(tmp_path, monkeypatch):
    # a fixed review queue: the real one empties as the curator confirms names
    monkeypatch.setattr(rv, "unresolved_names", lambda: [("P33", "TQJ230", "NCT04023552; arms: EXPERIMENTAL")])
    rows = rv.propose(nim.NIM(backend="offline"), IDX, path=tmp_path / "p.csv")
    assert rows and all(r["status"].startswith("PROPOSED") for r in rows)


def test_drug_override_requires_curated_row(monkeypatch, tmp_path):
    f = tmp_path / "o.csv"
    f.write_text("registry_name,chembl_id,justification\nSemagludtide,CHEMBL2108724,registry typo of semaglutide\n")
    monkeypatch.setattr(sk, "DRUG_OVERRIDES_FILE", f)
    assert sk.load_drug_overrides()[sk.norm("semagludtide")]["chembl_id"] == "CHEMBL2108724"
    monkeypatch.setattr(sk, "DRUG_OVERRIDES_FILE", tmp_path / "missing.csv")
    assert sk.override_drug("Semagludtide") is None


def test_namer_suggestion_must_exactly_name_a_molecule(monkeypatch):
    """The model's generic name counts only when it is exactly a ChEMBL name/synonym; echoing the query does not."""
    monkeypatch.setitem(nim._HANDLERS, "namer", lambda m, s: '{"answers": [{"n": 1, "query": "Platinol", '
                        '"generic_names": ["cisplatin"]}, {"n": 2, "query": "XYZ-1", "generic_names": ["XYZ-1"]}]}')
    n = nim.NIM(backend="offline")
    sugg = rv.suggest_names(n, ["Platinol", "XYZ-1"])
    assert sugg == {"Platinol": ["cisplatin"], "XYZ-1": ["XYZ-1"]}
    res = rv.Resolver(n, IDX)
    top = res.rank("Platinol", suggestions=sugg["Platinol"])[0]
    assert top["chembl_id"] == "CHEMBL11359" and top["method"].startswith("namer+exact")
    assert not res.named("XYZ-1", ["XYZ-1"], IDX)
    assert not res.named("Platinol", ["notadrugname"], IDX)


def test_namer_answers_for_another_line_are_dropped(monkeypatch):
    monkeypatch.setitem(nim._HANDLERS, "namer", lambda m, s: '{"answers": [{"n": 1, "query": "B", "generic_names": ["x"]}]}')
    assert rv.suggest_names(nim.NIM(backend="offline"), ["A", "B"]) == {}


def test_namer_without_model_is_unused():
    assert rv.suggest_names(nim.NIM(backend="offline"), ["Platinol"]) == {}


def test_lexical_channel_finds_shared_rare_word():
    res = rv.Resolver(nim.NIM(backend="offline"), IDX)
    pool = [dict(m, synonyms=[s for s in m["synonyms"] if rv.norm(s) != "brochosaline"]) for m in IDX]
    hits = [pool[i]["pref_name"] for i in res.lexical("Broncho saline", pool)]
    assert "SODIUM CHLORIDE" in hits[:3]
    assert rv.words("Sodium chloride component of colyte") == {"colyte"}


def test_index_records_parent_molecule():
    m = {x["chembl_id"]: x for x in IDX}
    assert m["CHEMBL1200976"]["parent"] == "CHEMBL1117"          # idarubicin hydrochloride -> idarubicin
    a = rv.evaluate(nim.NIM(backend="offline"), IDX, limit=40)
    assert a["top1"] <= a["top1_same_active_ingredient"]


def test_suggestions_checked_against_full_chembl_dictionary():
    """A suggested generic name of a molecule outside the index is found in the full ChEMBL table."""
    extra = [dict(chembl_id="CHEMBL3989948", parent="CHEMBL3989948", pref_name="LANABECESTAT", synonyms=["AZD-3293"],
                  syn_types={"AZD-3293": ["RESEARCH_CODE"]}, molecule_type="Small molecule", max_phase=3.0),
             dict(chembl_id="CHEMBL3989942", parent="CHEMBL3989948", pref_name="LANABECESTAT CAMSYLATE", synonyms=[],
                  syn_types={}, molecule_type="Small molecule", max_phase=3.0)]
    small = [m for m in IDX if m["chembl_id"] not in {"CHEMBL3989948", "CHEMBL3989942"}]
    res = rv.Resolver(nim.NIM(backend="offline"), small, dictionary=extra)
    top = res.rank("AZD3293X", suggestions=["lanabecestat"])[0]
    assert top["chembl_id"] == "CHEMBL3989948" and top["method"].startswith("namer+chembl_dictionary")
    assert res.exact("AZD-3293") == ["CHEMBL3989948"]
