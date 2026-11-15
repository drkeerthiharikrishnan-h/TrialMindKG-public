"""Tests for pipelines/pharos_index.py: the build-time card index gives the same cards and search results as the live
PharosGraph on the fixture (tests/fixtures/pharos_fixture.py), streams edges in chunks, and reports staleness.
No real data: Claude Science runs the build on the Mac and reports the timing."""
import gzip
import sys
import time

import pandas as pd
import pytest

sys.path.insert(0, "pipelines")
import pharos_index  # noqa: E402
from pharos_app import PharosGraph  # noqa: E402
from pharos_index import CardIndex, build_index, edge_kinds  # noqa: E402

from fixtures.pharos_fixture import build, GENE, PROT, DIS1, DIS2, CMP  # noqa: E402


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    return build(tmp_path_factory.mktemp("repo"))


@pytest.fixture(scope="module")
def built(repo):
    stats = build_index(repo, chunk=3, log=lambda *_: None)          # chunk=3: several chunks per layer
    return stats, CardIndex(stats["path"]), PharosGraph(root=repo)


def strip(card):
    return {k: v for k, v in card.items() if k not in ("served_from", "mechanism_neighbours")}   # index-only sections


def test_build_writes_the_index_where_the_app_looks(built, repo):
    stats, idx, g = built
    assert stats["path"] == str(pharos_index.index_path(repo)) and stats["nodes"] == len(g.nodes)
    assert stats["edges"] == len(g.edges) and stats["predictions"] > 0
    st = idx.status()
    assert st["layers"] == ["pharos", "opentargets_layer"] and "chembl37" in st["layers_missing"]
    assert st["predictions_available"] is True and int(st["edges"]) == 10
    assert not pharos_index.index_path(repo).with_suffix(".tmp").exists()


def test_status_and_schema_match_the_live_graph(built):
    _, idx, g = built
    st = idx.graph_status()
    assert st.pop("card_index")["built_at"]
    live = g.status()
    assert st == live
    assert list(st["edges_by_predicate"].values()) == sorted(st["edges_by_predicate"].values(), reverse=True)
    sc, lsc = idx.schema_card(), g.schema_card()
    assert sc["labels"] == lsc["labels"] and sc["patterns"] == lsc["patterns"]


@pytest.mark.parametrize("n_pred", [10, 1])
def test_every_card_matches_the_live_card(built, n_pred):
    _, idx, g = built
    for nid in g.nodes:
        live, fast = g.card(nid, n_pred=n_pred), idx.card(nid, n_pred=n_pred)
        assert fast["served_from"] == "card index"
        assert strip(fast) == live, nid


def test_card_cut_per_kind_keeps_full_counts(built):
    _, idx, g = built
    c = idx.card(GENE, per_kind=1)
    cur = next(k for k in c["known"] if k["kind"] == "curated")
    assert cur["n"] == 3 and len(cur["rows"]) == 1 and cur["rows"] == g.card(GENE, per_kind=1)["known"][1]["rows"]
    assert c["summary"]["known"] == 6
    assert idx.card("nope") is None


def test_search_matches_the_live_search(built):
    _, idx, g = built
    for q in ("ptgs2", "PTGS2", "MONDO:0005148", "disease", "sod", "chembl", "", "zzz"):
        assert idx.search(q) == g.search(q), q
    assert idx.search("a", limit=2) == g.search("a", limit=2)


def test_predicted_links_carry_paths_and_layers(built):
    _, idx, g = built
    c = idx.card(DIS1)
    assert c["predicted"][0]["other_label"] == "SOD1" and c["predicted"][0]["known_in"] == []
    p = idx.card(CMP)["predicted"]
    assert p == g.card(CMP)["predicted"] and p[0]["other"] == PROT
    assert p[0]["known_in"] == ["opentargets_layer", "pharos"]          # both layers state ibuprofen -> PTGS2


def test_without_predictions_the_card_says_so(tmp_path):
    repo = build(tmp_path / "r")
    (repo / "outputs" / "linkpred" / "predictions.tsv.gz").unlink()
    stats = build_index(repo, log=lambda *_: None)
    c = CardIndex(stats["path"]).card(GENE)
    assert c["predicted"] == [] and "no predictions file" in c["prediction_note"]
    assert strip(c) == PharosGraph(root=repo).card(GENE)


def test_layers_with_different_node_columns_build(tmp_path):
    """Real data (2026-11-05): a layer whose nodes.tsv.gz has no 'name' column left NaN after the concat and the
    build crashed on .lower(). A node only in that layer must index with an empty name."""
    repo = build(tmp_path / "r")
    other = sorted(d for d in (repo / "data" / "kg").iterdir()
                   if d.name != "pharos" and (d / "nodes.tsv.gz").exists())
    assert other, "fixture needs a second layer with nodes"
    p = other[0] / "nodes.tsv.gz"
    n = pd.read_csv(p, sep="\t", dtype=str, keep_default_na=False)
    n = n.drop(columns=[c for c in ("name", "symbol") if c in n.columns])
    new = n.iloc[0].to_dict() | {"id": "TEST:nameless"}                 # same category etc. as a real row
    n = pd.concat([n, pd.DataFrame([new])], ignore_index=True)
    n.to_csv(p, sep="\t", index=False, compression="gzip")
    stats = build_index(repo, log=lambda *_: None)
    import sqlite3
    row = sqlite3.connect(stats["path"]).execute("SELECT name FROM nodes WHERE id = ?", ("TEST:nameless",)).fetchone()
    assert row is not None and row[0] in (None, "")


def test_stale_lists_changed_inputs(tmp_path):
    repo = build(tmp_path / "r")
    stats = build_index(repo, log=lambda *_: None)
    idx = CardIndex(stats["path"])
    assert idx.stale(repo) == []
    vp = repo / "data" / "kg" / "pharos" / "edge_verdicts.tsv.gz"
    with gzip.open(vp, "at") as f:
        f.write("e9999999999999999\tkeep\t0.5\t\n")
    assert idx.stale(repo) == ["data/kg/pharos/edge_verdicts.tsv.gz"]
    with pytest.raises(FileNotFoundError):
        CardIndex(tmp_path / "none.sqlite")
    with pytest.raises(FileNotFoundError):
        build_index(tmp_path / "empty", log=lambda *_: None)


def test_edge_kinds_matches_personas():
    import personas
    f = pd.DataFrame([dict(predicate="biolink:gene_associated_with_condition", knowledge_level="text_co_occurrence", source="TIN-X"),
                      dict(predicate="biolink:genetically_associated_with", knowledge_level="statistical_association", source="TIGA"),
                      dict(predicate="biolink:has_gene_product", knowledge_level="knowledge_assertion", source="Pharos"),
                      dict(predicate="biolink:gene_associated_with_condition", knowledge_level="knowledge_assertion", source="CTD")])
    assert list(edge_kinds(f)) == [personas.evidence_kind(r) for r in f.to_dict("records")] == \
        ["text mining", "human genetics", "annotation", "curated"]


def test_app_serves_status_search_and_card_from_the_index_without_loading_the_graph(built, repo, monkeypatch):
    from fastapi.testclient import TestClient
    import app as appmod
    stats, idx, g = built
    monkeypatch.setattr(appmod, "_pharos", {"index": idx})
    monkeypatch.setattr(appmod, "pharos", lambda: pytest.fail("the full graph must not load"))
    c = TestClient(appmod.app)
    st = c.get("/api/pharos/status").json()
    assert st["nodes"] == 8 and st["card_index"]["path"] == stats["path"]
    assert c.get("/api/pharos/search", params={"q": "sod1"}).json()[0]["id"] == "NCBIGene:6647"
    card = c.get(f"/api/pharos/card/{GENE}").json()
    assert card["served_from"] == "card index" and card["summary"]["known"] == 6
    assert c.get("/api/pharos/card/nope").status_code == 404
    r = c.get("/api/results").json()
    assert r["verdicts"] == {"keep": 7, "prune": 1} and r["schema"]["n_classes"] == 6


def test_card_index_switch(built, repo, monkeypatch):
    import app as appmod
    monkeypatch.setattr(appmod, "_pharos", {})
    monkeypatch.setenv("TMKG_PHAROS_INDEX", "off")
    assert appmod.card_index() is None
    monkeypatch.setattr(appmod, "_pharos", {})
    monkeypatch.setenv("TMKG_PHAROS_INDEX", str(pharos_index.index_path(repo)))
    assert appmod.card_index() is not None


# ---- meta-path predictions on the card (CC-3) ----
import json  # noqa: E402
from fixtures.pharos_fixture import write, BP  # noqa: E402

E1, E4, E6 = "e0000000000000001", "e0000000000000004", "e0000000000000006"


def metapath_repo(tmp_path, bare=False):
    """The fixture plus a predictions_metapath.tsv.gz: PTGS2 -> type 2 diabetes with a named 3-step path made of real
    fixture edges (gene -> its protein -> GO term <- ...: only edges that exist), and SOD1 -> Alzheimer with no path.
    bare=True writes the path predicates as linkpred.predict really does (bare PyG relation names, 7 Nov G3 file) and adds
    a second path through the context relation (gene_associated_with_condition_context)."""
    repo = build(tmp_path / ("mp_bare" if bare else "mp"))
    pre = "" if bare else "biolink:"
    path = [{"shape": "Gene -has_gene_product-> Protein -actively_involved_in-> BiologicalProcess", "count": 1,
             "contribution": 1.2, "via": PROT, "nodes": [GENE, PROT, BP],
             "edges": [{"id": E4, "subject": GENE, "predicate": pre + "has_gene_product", "object": PROT},
                       {"id": E6, "subject": PROT, "predicate": pre + "actively_involved_in", "object": BP}]}]
    if bare:
        path.append({"shape": "Gene -gene_associated_with_condition_context-> Disease", "count": 1, "contribution": 0.4,
                     "via": DIS1, "nodes": [GENE, DIS1],
                     "edges": [{"id": E1, "subject": GENE, "predicate": "gene_associated_with_condition_context",
                                "object": DIS1}]})
    write(repo / "outputs" / "linkpred", "predictions_metapath", pd.DataFrame([
        dict(graph="G3", task="B", subject=GENE, object=DIS2, score="2.5000", rank="1", by="subject", model="metapath",
             best_shape=path[0]["shape"], n_shapes="1", paths=json.dumps(path)),
        dict(graph="G3", task="B", subject="NCBIGene:6647", object=DIS1, score="1.0000", rank="1", by="subject",
             model="metapath", best_shape="", n_shapes="0", paths="[]")]))
    return repo


def test_card_shows_the_named_path_and_edge_ids_from_the_live_graph(tmp_path):
    repo = metapath_repo(tmp_path)
    g = PharosGraph(root=repo)
    p = g.predicted_links(GENE)["predictions"][0]
    assert p["other"] == DIS2 and p["score"] == 2.5 and p["model"] == "meta-path model"
    pa = p["paths"][0]
    assert pa["shape"].startswith("Gene -has_gene_product-> Protein") and pa["via"] == PROT
    assert [e["id"] for e in pa["edges"]] == [E4, E6]
    assert pa["edges"][0]["statement"].startswith("PTGS2 -has_gene_product->")
    assert g.predicted_links("NCBIGene:6647")["predictions"][0]["paths"] == []
    assert g.status()["predictions"]["model"] == "metapath"
    card = g.card(GENE)
    assert card["predicted"][0]["paths"][0]["edges"][1]["id"] == E6


def test_the_metapath_file_wins_over_the_gnn_file_and_without_it_the_gnn_file_is_shown(tmp_path, repo):
    mp = metapath_repo(tmp_path)
    assert (mp / "outputs/linkpred/predictions.tsv.gz").exists()                       # both files present
    assert PharosGraph(root=mp).predicted_links(GENE)["predictions"][0]["score"] == 2.5
    plain = PharosGraph(root=repo)
    assert plain.status()["predictions"]["model"] == "gnn"
    assert plain.predicted_links(GENE)["predictions"][0]["model"] == "R-GCN link prediction"


def test_the_card_index_serves_the_same_metapath_card(tmp_path):
    repo = metapath_repo(tmp_path)
    stats = build_index(repo, chunk=3, log=lambda *_: None)
    idx, g = CardIndex(stats["path"]), PharosGraph(root=repo)
    for nid in (GENE, DIS2, DIS1, "NCBIGene:6647", PROT):
        assert strip(idx.card(nid)) == g.card(nid), nid
    c = idx.card(GENE)["predicted"][0]
    assert c["model"] == "meta-path model" and c["paths"][0]["edges"][0]["id"] == E4 and c["paths"][0]["shape"]
    assert idx.graph_status()["predictions"]["model"] == "metapath"


def test_path_predicate_words_curies_bare_names_and_the_context_relation():
    from pharos_app import path_predicate
    assert path_predicate("biolink:has_gene_product") == "has_gene_product"
    assert path_predicate("has_gene_product") == "has_gene_product"                   # bare name: no IndexError
    assert path_predicate("gene_associated_with_condition_context") == \
        "gene_associated_with_condition (statistical or not-provided level)"
    assert path_predicate("biolink:gene_associated_with_condition") == "gene_associated_with_condition"
    assert path_predicate(None) == "" and path_predicate("") == ""


def test_cards_with_bare_path_predicates_open_in_the_live_graph_and_the_index(tmp_path):
    """7 Nov: the real G3 predictions file writes bare relation names; both cards crashed with IndexError before the fix."""
    repo = metapath_repo(tmp_path, bare=True)
    g = PharosGraph(root=repo)
    stats = build_index(repo, chunk=3, log=lambda *_: None)
    idx = CardIndex(stats["path"])
    for card in (g.card(GENE), idx.card(GENE)):
        paths = card["predicted"][0]["paths"]
        assert paths[0]["edges"][0]["statement"].startswith("PTGS2 -has_gene_product->")
        assert "-gene_associated_with_condition (statistical or not-provided level)->" in paths[1]["edges"][0]["statement"]
        assert [e["id"] for e in paths[0]["edges"]] == [E4, E6] and paths[1]["edges"][0]["id"] == E1
    assert strip(idx.card(GENE)) == g.card(GENE)
    live, served = g.predicted_links(GENE), idx.predicted_links(GENE)       # the predicted-links endpoint, both backends
    stmt = served["predictions"][0]["paths"][1]["edges"][0]["statement"]
    assert "-gene_associated_with_condition (statistical or not-provided level)->" in stmt
    assert stmt == live["predictions"][0]["paths"][1]["edges"][0]["statement"]


def test_index_path_prefers_v2_when_present(tmp_path):
    """CC-38 follow-up (a) and PK5 (finding ES-16): the default index is the newest that exists, v3 (the shipped graph,
    the hosted index) before v2 before v1."""
    d = tmp_path / "data" / "kg" / "pharos_index"
    assert pharos_index.index_path(tmp_path) == d / "card_index.sqlite"          # nothing built: the v1 build path
    d.mkdir(parents=True)
    (d / "card_index.sqlite").write_bytes(b"")
    assert pharos_index.index_path(tmp_path) == d / "card_index.sqlite"
    (d / "card_index_v2.sqlite").write_bytes(b"")
    assert pharos_index.index_path(tmp_path) == d / "card_index_v2.sqlite"
    (d / "card_index_v3.sqlite").write_bytes(b"")
    assert pharos_index.index_path(tmp_path) == d / "card_index_v3.sqlite"
    assert pharos_index.INDEX_NAMES[0] == "card_index_v3.sqlite"


def test_card_index_rows_carry_the_ctd_link_like_the_live_card(built):
    stats, idx, g = built
    rows = [r for k in idx.card(GENE)["known"] for r in k["rows"]]
    live = {r["id"]: r["source_url"] for k in g.card(GENE)["known"] for r in k["rows"]}
    ctd = [r for r in rows if r["source"] == "CTD"]
    assert ctd and ctd[0]["source_url"] == "https://ctdbase.org/detail.go?type=gene&acc=5743&view=disease"
    assert {r["id"]: r["source_url"] for r in rows} == {i: u for i, u in live.items() if i in {r["id"] for r in rows}}
