"""Tests for pipelines/pharos_app.py and the /api/pharos endpoints on a tiny two-layer fixture (no real data, no
network). The fixture holds the entities the demo questions name, so the demo runs end to end."""
import gzip
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, "pipelines")
import pharos_app  # noqa: E402
from pharos_app import PharosGraph, DEMO_QUESTIONS  # noqa: E402

KGX = pharos_app.KGX


from fixtures.pharos_fixture import build, GENE, PROT, DIS1, DIS2, CMP, BP


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    return build(tmp_path_factory.mktemp("repo"))


@pytest.fixture(scope="module")
def g(repo):
    return PharosGraph(root=repo)


def test_loads_layers_and_kgindex_interface(g):
    assert g.layers_used == ["pharos", "opentargets_layer"] and "chembl37" in g.layers_missing
    assert len(g.nodes) == 8 and len(g.edges) == 10
    e = g.edges["e0000000000000001"]
    assert e["subject"] == GENE and e["disgenet_score"] == "0.4" and "activity" not in e      # empty cells dropped
    assert [x["object"] for x in g.out(GENE, "biolink:gene_associated_with_condition")] == [DIS1, DIS1, DIS1, DIS2]
    assert g.by_subject.get("nobody") == [] and "e0000000000000001" in g.edges and "nope" not in g.edges
    s = g.status()
    assert s["edges_by_layer"] == {"pharos": 8, "opentargets_layer": 2} and s["verdicts"] == {"keep": 7, "prune": 1}
    assert s["predictions"]["rows"] == 3 and s["source_versions"] == ["26.09", "pharos319"]


def test_search_and_node_view(g):
    assert [h["id"] for h in g.search("ptgs2")] == [GENE, PROT]            # shorter name first
    assert g.search("MONDO:0005148")[0]["label"] == "type 2 diabetes mellitus" and g.search("") == []
    v = g.node_view(GENE)
    assert v["degree"] == 6 and v["predictions_available"]
    rows = v["edges_by_predicate"]["biolink:gene_associated_with_condition"]
    assert {r["verdict"] for r in rows} == {"keep", "prune", None} and {r["layer"] for r in rows} == {"pharos", "opentargets_layer"}
    assert g.node_view("nope") is None


def test_explain_edge_has_verdict_corroboration_and_timeline(g):
    x = g.explain_edge("e0000000000000003")                                 # the text-mining edge, pruned
    assert x["verdict"]["verdict"] == "prune" and "corroboration" in x["verdict"]["reasons"]
    assert x["provenance"]["source"] == "JensenLab Text Mining" and x["provenance"]["layer"] == "pharos"
    ids = {c["id"] for c in x["corroboration"]}
    assert ids == {"e0000000000000001", "e0000000000000008", "e0000000000000065"}   # DisGeNET, TIGA, Open Targets: same pair
    dates = [ev["date"] for ev in x["timeline"]["events"]]
    assert dates == sorted(dates) and dates[0] == "2024-02" and "2026-09" in dates
    assert x["timeline"]["summary"]["n_supporting"] == 3 and x["timeline"]["summary"]["first_seen"] == "2024-02"
    assert [ev for ev in x["timeline"]["events"] if ev["direction"] == "neutral"][0]["source"].endswith("- pruned")
    y = g.explain_edge("e0000000000000066")                                 # dated by its own first_year
    assert y["verdict"] is None and y["timeline"]["events"][0]["date"] == "1991"
    assert g.explain_edge("missing") is None


def test_predicted_links_with_paths(g):
    p = g.predicted_links(GENE)
    assert [x["other"] for x in p["predictions"]] == [DIS2]
    pred = p["predictions"][0]
    assert pred["score"] == 0.91 and pred["known_in"] == ["pharos"]        # CTD already states the pair
    assert g.predicted_links(DIS1)["predictions"][0]["other_label"] == "SOD1"
    assert g.predicted_links(GENE, task="A")["predictions"] == []
    # a two-hop path: PTGS2 -> Alzheimer? no shared neighbour with DIS2; compound -> protein has none either
    assert pred["paths"] == []
    assert g.paths(CMP, BP)[0]["via"] == PROT and len(g.paths(CMP, BP)[0]["edges"]) == 2
    assert g.predicted_links("nope") is None
    g2 = PharosGraph(root=g.root, predictions=g.root / "nowhere.tsv.gz")
    assert "no predictions file" in g2.predicted_links(GENE)["note"]


def test_schema_card_and_entities(g):
    card = g.schema_card()
    assert "Gene" in card["labels"] and "name" in card["labels"]["Gene"] and "tdl" in card["labels"]["Protein"]
    assert ("Gene", "gene_associated_with_condition", "Disease", 4) in card["patterns"]
    assert "id" in card["rel_props"] and "activity" in card["rel_props"] and card["enums"] == {}
    q = "Does ibuprofen (CHEMBL521) act on PTGS2 in Alzheimer disease or P35354?"
    assert g.find_entities(q) == sorted([CMP, GENE, PROT, DIS1])


def test_demo_questions_run_on_the_fixture(g):
    out = g.demo()
    assert [d["id"] for d in out] == [d["id"] for d in DEMO_QUESTIONS] and all(d["passed"] for d in out), \
        [(d["id"], d["note"]) for d in out if not d["passed"]]
    d3 = out[2]
    assert d3["rows"][0]["gene"] == "PTGS2" and set(d3["evidence"]) >= {"e0000000000000001", "e0000000000000002"}
    assert all("evidence" in d["columns"] or "e2" in d["columns"] for d in out)
    assert out[1]["rows"][0]["compound"] == "IBUPROFEN" and out[4]["rows"][0]["studies"] == "3"


def test_ask_offline_and_endpoints(g):
    from fastapi.testclient import TestClient
    import app as appmod
    from nim import NIM
    appmod._pharos["g"] = g
    appmod._pharos["index"] = None                      # the live graph answers (a Mac may have a real index)
    appmod._state.update(kg=None, nim=NIM(backend="offline"))
    c = TestClient(appmod.app)
    assert c.get("/api/pharos/status").json()["nodes"] == 8
    assert c.get("/api/pharos/search", params={"q": "sod1"}).json()[0]["id"] == "NCBIGene:6647"
    assert c.get(f"/api/pharos/node/{GENE}").json()["degree"] == 6 and c.get("/api/pharos/node/x").status_code == 404
    assert c.get("/api/pharos/explain/e0000000000000003").json()["verdict"]["verdict"] == "prune"
    assert c.get("/api/pharos/explain/zzz").status_code == 404
    assert c.get("/api/pharos/predict", params={"node": GENE, "n": 5}).json()["predictions"][0]["other"] == DIS2
    demo = c.get("/api/pharos/demo").json()
    assert len(demo) == 5 and all(d["passed"] for d in demo)
    r = c.post("/api/pharos/ask", json={"question": "Which diseases is PTGS2 associated with?"}).json()
    assert r["backend"].startswith("offline") and r["entities"] == [GENE, PROT] and isinstance(r["problems"], list)
    assert "Pharos graph" in c.get("/").text


def test_results_page_with_the_fixture_graph(g, repo):
    import json
    import app as appmod
    lp = repo / "outputs" / "linkpred"
    (lp / "G0_B_kfold.json").write_text(json.dumps({"summary": {"auroc": {"mean": 0.7, "ci95": 0.01}}}))   # smoke: hidden
    (lp / "G0_B_kfold@frozen_a.json").write_text(json.dumps({"settings": {"test_set": {"digest": "d"}},
        "per_seed": [{"folds": [{"auroc": 0.6}, {"auroc": 0.62}]}], "summary": {"auroc": {"mean": 0.61, "ci95": 0.0}}}))
    (lp / "G3_B_kfold@frozen_a.json").write_text(json.dumps({"settings": {"test_set": {"digest": "d"}},
        "per_seed": [{"folds": [{"auroc": 0.7}, {"auroc": 0.71}]}], "summary": {"auroc": {"mean": 0.705, "ci95": 0.0}}}))
    r = appmod.results_page(repo, g)
    assert [x["label"] for x in r["benchmark"]["runs"]] == ["G0@frozen_a", "G3@frozen_a"]
    assert r["benchmark"]["comparisons"][0]["rows"][0]["variant"] == "G3@frozen_a" and r["calibration"] is None
    assert r["verdicts"] == {"keep": 7, "prune": 1} and "G3_B_kfold@frozen_a.json" in r["sources"]
    sc = r["schema"]
    gene = next(c for c in sc["classes"] if c["name"] == "Gene")
    assert gene["iri"] == "https://w3id.org/biolink/vocab/Gene" and gene["n_nodes"] == 2
    assert next(p for p in sc["predicates"] if p["name"] == "gene_associated_with_condition")["n_edges"] == 4
    assert sc["n_classes"] == 6 and {f["name"] for f in r["files"]} == {"predictions.tsv.gz", "edge_verdicts.tsv.gz"}
    for f in ("G0_B_kfold.json", "G0_B_kfold@frozen_a.json"):     # leave the shared fixture as it was
        (lp / f).unlink()


def test_entity_card_groups_known_links_by_evidence_kind(g):
    c = g.card(GENE)
    assert c["about"]["label"] == "PTGS2" and c["about"]["type_word"] == "gene" and GENE in c["about"]["ids"]
    kinds = {k["kind"]: k for k in c["known"]}
    assert set(kinds) == {"curated", "text mining", "human genetics", "annotation"}
    assert kinds["text mining"]["n_pruned"] == 1 and kinds["text mining"]["rows"][0]["word"] == "pruned"
    assert kinds["curated"]["n"] == 3 and kinds["curated"]["n_kept"] == 2        # DisGeNET, CTD kept; the Open Targets
    assert kinds["curated"]["rows"][-1]["word"] == "unscored"                     # layer has no verdict file
    row = kinds["curated"]["rows"][0]
    assert row["sentence"].startswith("PTGS2 is associated with") and row["other_label"] in ("Alzheimer disease", "type 2 diabetes mellitus")
    assert row["level_words"] == "curated statement"
    assert kinds["annotation"]["rows"][0]["sentence"] == "PTGS2 encodes Prostaglandin G/H synthase 2"
    assert c["summary"] == dict(known=6, kinds=4, kept=4, pruned=1, predicted=1, layers=["opentargets_layer", "pharos"])
    assert c["predicted"][0]["other_label"] == "type 2 diabetes mellitus"   # a direct pair: no two-hop path in the fixture
    pic = c["picture"]
    assert any(e["predicted"] for e in pic["edges"]) and not any(e["evidence_type"] == "text mining" for e in pic["edges"])
    assert {n["id"] for n in pic["nodes"]} >= {GENE, DIS1, DIS2, PROT}
    assert g.card("nobody") is None
    disease = g.card(DIS1)
    assert disease["about"]["type_word"] == "disease" and disease["known"][0]["rows"][0]["outgoing"] is False
    assert disease["known"][0]["rows"][0]["sentence"].endswith("Alzheimer disease")


# ---- CC-41: CTD rows link to the CTD data page (CTD terms: hyperlink to the CTD data pages) ----


def test_source_url_links_ctd_rows_from_the_rows_own_ids_only():
    from pharos_app import source_url
    gene = dict(source="CTD", subject="NCBIGene:5743", object="MONDO:0005148")
    assert source_url(gene) == "https://ctdbase.org/detail.go?type=gene&acc=5743&view=disease"
    assert source_url(dict(source="CTD", subject="ENSEMBL:ENSG00000073756", object="OMIM:104300")) == \
        "https://ctdbase.org/detail.go?type=disease&acc=OMIM:104300"
    assert source_url(dict(source="CTD", subject="ENSEMBL:ENSG1", object="MESH:D003924")) == \
        "https://ctdbase.org/detail.go?type=disease&acc=MESH:D003924"
    assert source_url(dict(source="CTD", subject="ENSEMBL:ENSG1", object="MONDO:0005148")) is None   # no CTD id: no link
    assert source_url(dict(source="CTD", subject="NCBIGene:57x3", object="OMIM:1/../x")) is None      # malformed: no link
    assert source_url(dict(source="DisGeNET", subject="NCBIGene:5743", object="MONDO:0005148")) is None
    assert source_url({}) is None


def test_card_rows_carry_the_ctd_link_and_the_pages_show_it(g):
    rows = [r for k in g.card(GENE)["known"] for r in k["rows"]]
    ctd = [r for r in rows if r["source"] == "CTD"]
    assert ctd and ctd[0]["source_url"] == "https://ctdbase.org/detail.go?type=gene&acc=5743&view=disease"
    assert all(r["source_url"] is None for r in rows if r["source"] != "CTD")
    pages = Path(__file__).resolve().parents[1] / "pipelines"
    for page in ("app_page.html", "app_page_v2.html", "app_page_v3.html"):
        html = (pages / page).read_text()
        assert 'safeUrl(r.source_url) ? `<a href="${esc(safeUrl(r.source_url))}" target="_blank" rel="noopener"' in html, page      # PK12 (SEC-14): only an http(s) or site-relative address becomes a link
