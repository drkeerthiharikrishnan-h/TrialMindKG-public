"""CC-11: the Numbers page rows for E003 (frozen protocol, cap-matched) and the EXPLORATORY E004-E008 plus the
overlap-control table (E005). Fixture result files only; every number must come from a file, wording from the numbers."""
import json
import sys

import pytest

sys.path.insert(0, "pipelines")
import numbers_page as npg  # noqa: E402

DIGEST = "d1"


def result(vals, drop=(), digest=DIGEST):
    """vals = {metric: [five fold values]}; a result file in run.py's shape (one seed, five folds)."""
    n = len(next(iter(vals.values())))
    folds = [{m: v[i] for m, v in vals.items()} for i in range(n)]
    summary = {m: {"mean": sum(v) / n, "ci95": 0.0} for m, v in vals.items()}
    return {"settings": {"epochs": 300, "hidden": 64, "seeds": 1, "wiring": {"drop_layers": list(drop)},
                         "test_set": {"digest": digest}},
            "per_seed": [{"folds": folds}], "summary": summary}


def shift(base, d, jitter=(0.0, 0.002, -0.002, 0.001, -0.001)):
    return [b + d + j for b, j in zip(base, jitter)]


B = [0.60, 0.61, 0.59, 0.60, 0.62]
MP, CN, COV = [0.68, 0.67, 0.69, 0.68, 0.68], [0.50] * 5, [0.87] * 5


@pytest.fixture
def lp(tmp_path):
    d = tmp_path / "outputs" / "linkpred"
    d.mkdir(parents=True)
    w = lambda name, obj: (d / name).write_text(json.dumps(obj))             # noqa: E731
    all_ = dict(auroc_metapath=shift(MP, 0.12), metapath_coverage=shift(COV, 0.12), auroc_cn=shift(CN, 0.23))
    w("G0_B_kfold@frozen_a_g0_1m.json", result(dict(auroc=B, auroc_metapath=MP, metapath_coverage=COV, auroc_cn=CN)))
    w("G2_B_kfold@frozen_a.json", result(dict(auroc=shift(B, -0.005), **all_)))
    w("G3_B_kfold@frozen_a.json", result(dict(auroc=shift(B, -0.005), **all_)))
    w("G2_B_kfold@diag_rebuilt_1m.json", result(all_))
    w("G3_B_kfold@diag_rebuilt_1m.json", result(all_))
    w("G2_B_kfold@diag_e005_nomondo.json",
      result(dict(auroc_metapath=shift(MP, 0.02), metapath_coverage=shift(COV, 0.08), auroc_cn=shift(CN, 0.06)),
             drop=["mondo_hierarchy"]))
    w("G2_B_kfold@diag_e005_nogo.json", result(all_, drop=["go_hierarchy"]))
    w("G0_B_kfold@diag_gnnpath_1m.json", result(dict(auroc=B, auroc_gnnpath=B, auroc_gnnhead=B, auroc_pathhead=B)))
    w("G2_B_kfold@diag_gnnpath_1m.json", result(dict(auroc=B, auroc_gnnpath=shift(B, 0.1), auroc_gnnhead=shift(B, 0.01),
                                                      auroc_pathhead=shift(B, 0.08))))
    return tmp_path / "outputs" / "linkpred"


def get(lp):
    return {e["id"]: e for e in npg.experiments(lp)}


def test_e003_is_the_frozen_protocol_row_and_the_rest_are_labelled_exploratory(lp):
    ex = get(lp)
    assert ex["E003"]["status"] == "frozen protocol" and not ex["E003"]["exploratory"]
    for k in ("E004", "E005", "E006", "E008"):
        assert ex[k]["status"] == "EXPLORATORY" and ex[k]["exploratory"]
    assert any("EXPLORATORY" in s for s in ex["E004"]["sentences"])
    assert not any("EXPLORATORY" in s for s in ex["E003"]["sentences"])


def test_e003_numbers_are_the_files_numbers_and_the_cap_matched_base(lp):
    e = get(lp)["E003"]
    assert e["base"]["label"] == "G0@frozen_a_g0_1m" and e["base"]["file"] == "G0_B_kfold@frozen_a_g0_1m.json"
    g2 = [r for r in e["rows"] if r["graph"] == "G2"][0]
    raw = json.loads((lp / g2["file"]).read_text())
    assert g2["means"]["auroc_metapath"] == pytest.approx(raw["summary"]["auroc_metapath"]["mean"])
    d = g2["deltas"]["auroc"]
    assert d["mean"] < 0 and d["ci_high"] < 0 and d["digest_match"] and d["n"] == 5          # GNN lower: sign from data
    assert g2["deltas"]["auroc_metapath"]["ci_low"] > 0 and "higher" in g2["deltas"]["auroc_metapath"]["verdict"]
    assert any("lower" in s for s in e["sentences"]) and any("higher" in s for s in e["sentences"])


def test_overlap_table_reads_dropped_layers_from_the_files_and_computes_gain_left(lp):
    e = get(lp)["E005"]
    assert e["overlap"] and e["available"]
    by = {r["tag"]: r for r in e["rows"]}
    assert by["diag_rebuilt_1m"]["dropped_layers"] == [] and by["diag_rebuilt_1m"]["gain_left"] == pytest.approx(1.0)
    mondo = by["diag_e005_nomondo"]
    assert mondo["dropped_layers"] == ["mondo_hierarchy"]
    assert mondo["gain_left"] == pytest.approx(0.02 / 0.12, abs=0.02)           # 0.02 of 0.12, from the fixture numbers
    assert by["diag_e005_nogo"]["gain_left"] == pytest.approx(1.0)
    assert {"auroc_metapath", "auroc_cn"} <= set(mondo["means"]) and mondo["deltas"]["auroc_cn"]["mean"] > 0
    assert any("mondo_hierarchy removed" in s for s in e["sentences"]) and "mondo_hierarchy" in " ".join(e["sentences"])


def test_missing_files_are_listed_not_invented(lp):
    e = get(lp)["E006"]
    assert e["available"] is False and e["rows"] == [] and e["sentences"] == []
    assert "G2_B_kfold@diag_kge_rotate_d256.json" in e["missing"]
    # E005 lacks most drop runs in this fixture: the ones present are shown, the others listed
    e5 = get(lp)["E005"]
    assert "G2_B_kfold@diag_e005_noot.json" in e5["missing"] and len(e5["rows"]) == 3


def test_partial_variant_without_its_base_has_no_deltas(lp):
    (lp / "G0_B_kfold@diag_gnnpath_1m.json").unlink()
    e = get(lp)["E008"]
    assert e["available"] and e["rows"][0]["deltas"] == {} and "G0_B_kfold@diag_gnnpath_1m.json" in e["missing"]


def test_gnnpath_row_reports_the_three_heads_against_g0_of_the_same_tag(lp):
    r = get(lp)["E008"]["rows"][0]
    assert set(r["means"]) == {"auroc_gnnpath", "auroc_gnnhead", "auroc_pathhead", "auroc"}
    assert r["deltas"]["auroc_gnnpath"]["mean"] == pytest.approx(0.1, abs=0.01)
    assert r["deltas"]["auroc_gnnhead"]["mean"] < r["deltas"]["auroc_gnnpath"]["mean"]


def test_different_test_sets_are_flagged_not_compared(lp):
    (lp / "G2_B_kfold@diag_rebuilt_1m.json").write_text(json.dumps(result(
        dict(auroc_metapath=shift(MP, 0.12), metapath_coverage=COV, auroc_cn=CN), digest="other")))
    row = [r for r in get(lp)["E004"]["rows"] if r["graph"] == "G2"][0]
    assert row["deltas"]["auroc_metapath"]["digest_match"] is False
    assert "not comparable" in row["deltas"]["auroc_metapath"]["verdict"]


def test_numbers_and_source_allow_list_include_only_spec_files(lp, tmp_path):
    n = npg.numbers(tmp_path)
    assert [e["id"] for e in n["experiments"]] == ["E003", "E004", "E005", "E006", "E008", "E008-G3", "E008-MONDO", "E009"]
    assert "G2_B_kfold@diag_rebuilt_1m.json" in n["sources"] and "G2_B_kfold@diag_e005_nomondo.json" in n["sources"]
    assert npg.source_file(tmp_path, "G2_B_kfold@diag_e005_nomondo.json") is not None
    (lp / "G2_B_kfold@diag_secret.json").write_text("{}")
    assert npg.source_file(tmp_path, "G2_B_kfold@diag_secret.json") is None              # not named by any spec
    assert npg.source_file(tmp_path, "../CLAUDE.md") is None
    # smoke runs never appear in the frozen list either
    assert all("diag_" not in r["file"] for r in n["benchmark"]["runs"])


def test_every_spec_file_name_matches_the_result_name_pattern():
    names = npg.experiment_files()
    assert names and all(npg.RESULT_RE.match(n) for n in names)


def test_committed_results_reproduce_the_ledger_numbers():
    """On the real committed files (skipped where absent): E003 GNN G2-G0 and E005 MONDO share, as in loop/ledger.tsv."""
    root = npg.ROOT / "outputs" / "linkpred"
    if not (root / "G0_B_kfold@frozen_a_g0_1m.json").exists() or not (root / "G2_B_kfold@diag_e005_nomondo.json").exists():
        pytest.skip("committed result files not present")
    ex = {e["id"]: e for e in npg.experiments(root)}
    g2 = [r for r in ex["E003"]["rows"] if r["graph"] == "G2"][0]
    assert round(g2["deltas"]["auroc"]["mean"], 4) == -0.0045 and round(g2["deltas"]["auroc_metapath"]["mean"], 4) == 0.0906
    nomondo = [r for r in ex["E005"]["rows"] if r["tag"] == "diag_e005_nomondo"][0]
    assert round(nomondo["deltas"]["auroc_metapath"]["mean"], 4) == 0.0223 and 0.15 < nomondo["gain_left"] < 0.22



# --- CC-24: rows with their own base per pair (E008 on G3, MONDO control, E009 random pruning, hidden D) ----------

def two_folds(vals):
    return result({m: v[:2] for m, v in vals.items()})


@pytest.fixture
def lp24(tmp_path):
    d = tmp_path / "outputs" / "linkpred"
    d.mkdir(parents=True)
    w = lambda name, vals: (d / name).write_text(json.dumps(two_folds(vals)))      # noqa: E731
    g = lambda x: dict(auroc_gnnpath=x, auroc_pathhead=x, auroc_gnnhead=[0.6, 0.6])   # noqa: E731
    w("G0_B_kfold@diag_gnnpath_1m.json", g([0.66, 0.67]))
    w("G0_B_kfold@diag_gnnpath_1m_gpu.json", g([0.66, 0.67]))
    w("G2_B_kfold@diag_gnnpath_1m.json", g([0.80, 0.79]))
    w("G2_B_kfold@diag_gnnpath_1m_nomondo.json", g([0.69, 0.71]))
    w("G3_B_kfold@diag_gnnpath_1m_gpu.json", g([0.79, 0.80]))
    w("G2_B_kfold@diag_kge_rotate_d256.json", dict(auroc=[0.63, 0.64]))
    w("G2R_B_kfold@diag_kge_rotate_d256_e009.json", dict(auroc=[0.62, 0.635]))
    w("G3_B_kfold@diag_kge_rotate_d256_e009.json", dict(auroc=[0.64, 0.645]))
    return d


def test_cc24_rows_appear_with_their_own_base_and_labels(lp24):
    ex = get(lp24)
    assert "D" not in ex                                                   # hidden while the release file is absent
    g3 = ex["E008-G3"]
    assert g3["status"] == "EXPLORATORY" and [r["label"] for r in g3["rows"]] == [
        "G3@diag_gnnpath_1m_gpu vs G0@diag_gnnpath_1m_gpu", "G0@diag_gnnpath_1m_gpu vs G0@diag_gnnpath_1m"]
    assert g3["rows"][0]["deltas"]["auroc_gnnpath"]["mean"] == pytest.approx(0.13) and g3["rows"][0]["deltas"]["auroc_gnnpath"]["n"] == 2
    e9 = ex["E009"]
    assert [r["base_label"] for r in e9["rows"]] == ["G2R@diag_kge_rotate_d256_e009", "G2@diag_kge_rotate_d256"]
    assert "G2R = same number of edges removed at random (seed 0)." in e9["sentences"]
    assert all("EXPLORATORY" in e["sentences"][-1] for e in (g3, e9, ex["E008-MONDO"]))


def test_cc24_mondo_share_is_computed_from_the_two_deltas(lp24):
    e = get(lp24)["E008-MONDO"]
    d1 = e["rows"][0]["deltas"]["auroc_gnnpath"]["mean"]                  # without MONDO - G0 = 0.035
    d2 = e["rows"][1]["deltas"]["auroc_gnnpath"]["mean"]                  # without MONDO - G2 = -0.095
    assert d1 == pytest.approx(0.035) and d2 == pytest.approx(-0.095)
    share = round(100 * -d2 / (d1 - d2))                                   # 73
    assert any(f"MONDO disease hierarchy carries about {share}% of the gain" in s for s in e["sentences"])
    assert set(e["rows"][0]["deltas"]) == {"auroc_gnnpath", "auroc_pathhead", "auroc_gnnhead"}


def test_cc24_missing_files_hide_or_list_and_do_not_crash(lp24):
    (lp24 / "G2_B_kfold@diag_gnnpath_1m.json").unlink()                   # the MONDO control loses its full-graph base
    e = get(lp24)["E008-MONDO"]
    assert "G2_B_kfold@diag_gnnpath_1m.json" in e["missing"] and e["rows"][1]["deltas"] == {}
    assert not any("carries about" in s for s in e["sentences"])
    (lp24 / "G2R_B_kfold@diag_kge_rotate_d256_e009.json").unlink()
    e9 = get(lp24)["E009"]
    assert len(e9["rows"]) == 1 and "G2R_B_kfold@diag_kge_rotate_d256_e009.json" in e9["missing"]
    assert e9["rows"][0]["deltas"] == {} and "G2R = same number of edges removed at random (seed 0)." in e9["sentences"]


def test_cc24_d_row_appears_when_the_release_file_exists(lp24):
    (lp24 / "G2_B_kfold@diag_gnnpath_1m_release.json").write_text(json.dumps(two_folds(dict(auroc_gnnpath=[0.8, 0.81]))))
    d = get(lp24)["D"]
    assert d["available"] and [r["graph"] for r in d["rows"]] == ["G2"] and "G3_B_kfold@diag_gnnpath_1m_release.json" in d["missing"]
    assert d["rows"][0]["deltas"]["auroc_gnnpath"]["mean"] == pytest.approx(0.14)


def test_cc24_share_wording_follows_the_numbers():
    rows = lambda d1, d2: [dict(deltas={"m": dict(mean=d1)}), dict(deltas={"m": dict(mean=d2)})]   # noqa: E731
    spec = dict(share=dict(metric="m", layer="X layer"))
    assert "carries none" in npg._share_sentences(spec, dict(rows=rows(0.10, 0.01)))[0]
    assert "does not beat" in npg._share_sentences(spec, dict(rows=rows(0.01, 0.02)))[0]
    assert npg._share_sentences(spec, dict(rows=rows(0.0, -0.1)))[0].startswith("The X layer carries about 100%")
