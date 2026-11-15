"""Tests for scripts/make_spotcheck_blinded.py on small fixtures (no data/ needed)."""
import gzip
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import make_spotcheck_blinded as m  # noqa: E402

PG = {"gene_condition": ["biolink:gene_associated_with_condition", "biolink:genetically_associated_with"]}


def _verdicts(n=600):
    preds = ["biolink:gene_associated_with_condition", "biolink:genetically_associated_with",
             "biolink:directly_physically_interacts_with", "biolink:expressed_in"]
    rows = []
    for i in range(n):
        p = preds[i % 4] if i % 50 else "biolink:expressed_in"
        rows.append({"id": f"e{i}", "subject": f"S:{i % 7}", "predicate": p, "object": f"O:{i % 11}",
                     "source": "Src", "primary_knowledge_source": "infores:x" if i % 2 else "",
                     "plausibility": "0.1", "confidence": f"{(i % 10) / 10:.1f}",
                     "verdict": "prune" if i % 3 == 0 else "keep", "reasons": f"r{i}"})
    return pd.DataFrame(rows)


def _write_gz(df, path):
    with gzip.open(path, "wt") as f:
        df.to_csv(f, sep="\t", index=False)
    return path


def test_predicate_group():
    assert m.predicate_group("biolink:genetically_associated_with", PG) == "gene_condition"
    assert m.predicate_group("biolink:expressed_in", PG) == "expressed_in"
    assert m.predicate_group("biolink:expressed_in", {}) == "expressed_in"


def test_load_pair_groups(tmp_path):
    card = tmp_path / "c.yaml"
    card.write_text("prune:\n  pair_groups:\n    g: [biolink:a, biolink:b]\n")
    assert m.load_pair_groups(card) == {"g": ["biolink:a", "biolink:b"]}
    (tmp_path / "d.yaml").write_text("name: x\n")
    assert m.load_pair_groups(tmp_path / "d.yaml") == {}


def test_stream_reservoirs_sizes_and_determinism(tmp_path):
    df = _verdicts()
    p = _write_gz(df, tmp_path / "v.tsv.gz")
    res, sizes, n = m.stream_reservoirs(p, PG, k=5, seed=1, chunksize=97)
    assert n == len(df)
    assert sum(sizes.values()) == len(df)
    assert sizes[("prune", "gene_condition")] == ((df["verdict"] == "prune") &
                                                  df["predicate"].isin(PG["gene_condition"])).sum()
    assert res.groupby(["verdict", "predicate_group"]).size().max() <= 5
    res2, _, _ = m.stream_reservoirs(p, PG, k=5, seed=1, chunksize=200)
    # same seed, keys drawn in file order: chunking does not change the sample
    assert sorted(res["id"]) == sorted(res2["id"])
    res3, _, _ = m.stream_reservoirs(p, PG, k=5, seed=2, chunksize=97)
    assert sorted(res["id"]) != sorted(res3["id"])


def test_allocate():
    a = m.allocate({"big": 900, "mid": 90, "tiny": 10}, 100)
    assert sum(a.values()) == 100 and a["tiny"] >= 1 and a["big"] > a["mid"]
    assert m.allocate({"a": 3, "b": 2}, 100) == {"a": 3, "b": 2}       # fewer rows than asked
    assert m.allocate({"a": 999, "b": 1}, 10) == {"a": 9, "b": 1}      # at least one per group
    assert m.allocate({}, 10) == {}


def test_draw_counts(tmp_path):
    p = _write_gz(_verdicts(), tmp_path / "v.tsv.gz")
    res, sizes, _ = m.stream_reservoirs(p, PG, k=20, seed=0, chunksize=100)
    s = m.draw(res, sizes, {"prune": 20, "keep": 20})
    assert (s["verdict"] == "prune").sum() == 20 and (s["verdict"] == "keep").sum() == 20
    assert s["id"].is_unique
    assert set(s["predicate_group"]) == {"gene_condition", "directly_physically_interacts_with", "expressed_in"}
    assert (s["stratum_sampled"] <= s["stratum_size"]).all()


def test_edge_details(tmp_path):
    e = pd.DataFrame({"id": ["e1", "e2", "e3"], "subject": ["a"] * 3, "knowledge_level": ["k1", "k2", "k3"],
                      "activity": ["6.4", "", ""], "activity_type": ["Ki", "", ""], "pmids": ["1|2", "", ""],
                      "unrelated": ["x"] * 3})
    p = _write_gz(e, tmp_path / "e.tsv.gz")
    d = m.edge_details(p, ["e3", "e1"], chunksize=1)
    assert set(d.index) == {"e1", "e3"} and d.loc["e3", "knowledge_level"] == "k3"
    assert "unrelated" not in d.columns


def test_stat_string_and_references():
    assert m.stat_string({"activity": "6.38", "activity_type": "Ki"}) == "activity=6.38 (Ki)"
    assert m.stat_string({"disgenet_score": "0.3", "n_records": "4"}) == "disgenet_score=0.3; n_records=4"
    assert m.stat_string({"n_records": "1"}) == ""
    assert m.references({"pmids": "1|2"}) == "PMID:1 | PMID:2"
    assert m.references({"pmids": "1|2|3"}, k=2) == "PMID:1 | PMID:2 (+1 more)"
    assert m.references({}) == ""


def test_node_names(tmp_path):
    n = pd.DataFrame({"id": ["G:1", "D:1"], "name": ["epidermal growth factor receptor", "asthma"],
                      "symbol": ["EGFR", ""]})
    p = _write_gz(n, tmp_path / "n.tsv.gz")
    assert m.node_names(p) == {"G:1": "EGFR", "D:1": "asthma"}


def test_build_sheet_is_blinded_and_shuffled(tmp_path):
    p = _write_gz(_verdicts(), tmp_path / "v.tsv.gz")
    res, sizes, _ = m.stream_reservoirs(p, PG, k=20, seed=0, chunksize=100)
    s = m.draw(res, sizes, {"prune": 20, "keep": 20})
    details = pd.DataFrame({"id": s["id"], "knowledge_level": "knowledge_assertion", "ligand_name": "aspirin"}).set_index("id")
    sheet, key = m.build_sheet(s, details, {"S:1": "EGFR"}, seed=0)
    assert list(sheet.columns) == m.SHEET_COLS and list(key.columns) == m.KEY_COLS
    for c in ("verdict", "confidence", "reasons", "edge_id"):
        assert c not in sheet.columns
    assert list(sheet["row"]) == list(key["row"]) == list(range(1, 41))
    assert not (key["verdict"].iloc[:20] == "prune").all()            # interleaved, not two blocks
    assert set(sheet["subject_name"]) <= {"EGFR", "aspirin"}           # node name, else the edge's ligand name
    assert (sheet["owner_judgement"] == "").all()


def test_write_blind_xlsx_and_readme(tmp_path, monkeypatch):
    sheet = pd.DataFrame({c: ["x", "y"] for c in m.SHEET_COLS})
    f = tmp_path / "v.tsv.gz"
    f.write_text("x")
    monkeypatch.setattr(m, "ROOT", tmp_path)
    rows = m.readme_rows(7, [f], 10, 1, 1, PG, "k.tsv")
    path = m.write_blind_xlsx(sheet, rows, tmp_path / "out" / "s.xlsx")
    back = pd.read_excel(path, sheet_name="spot_check", dtype=str)
    assert list(back.columns) == m.SHEET_COLS and len(back) == 2
    rd = pd.read_excel(path, sheet_name="README", dtype=str)
    assert rd.loc[rd["item"] == "seed", "value"].iloc[0] == "7"
    assert not rd["value"].str.contains("prune:|keep:", regex=True).any()
    import openpyxl
    ws = openpyxl.load_workbook(path)["spot_check"]
    assert ws.data_validations.dataValidation[0].formula1 == '"supported,unsupported,cannot tell"'
