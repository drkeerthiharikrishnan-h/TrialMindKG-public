"""CC-31a (review finding M2): evidence-first ordering of graph answers before LIMIT, duplicate answers merged, the same
order for rows another database returned (querier.rank_rows), and the summary wording that goes with it. A tiny Pharos-shaped
graph in which alphabetical order would put the text-mined rows first."""
import sys

import pandas as pd
import pytest

sys.path.insert(0, "pipelines")
import querier  # noqa: E402
import answer_summary  # noqa: E402
from pharos_app import PharosGraph, KGX  # noqa: E402
from fixtures.pharos_fixture import edge, write  # noqa: E402

G = "NCBIGene:1"
DIS = {"MONDO:1": "aaa disease", "MONDO:2": "bbb disease", "MONDO:3": "ccc disease", "MONDO:4": "ddd disease"}


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    root = tmp_path_factory.mktemp("ev")
    (root / "sources").mkdir()
    (root / "sources" / "pharos.yaml").write_text("name: pharos\n")
    nodes = pd.DataFrame([dict(id=G, category="biolink:Gene", name="GENE1")] +
                         [dict(id=i, category="biolink:Disease", name=n) for i, n in DIS.items()])
    p = "biolink:gene_associated_with_condition"
    edges = pd.DataFrame([
        edge(1, G, p, "MONDO:1", "JensenLab Text Mining", "pharos", "v", knowledge_level="text_co_occurrence"),
        edge(2, G, p, "MONDO:2", "TIGA", "pharos", "v", knowledge_level="statistical_association"),
        edge(3, G, p, "MONDO:3", "CTD", "pharos", "v"),                                   # knowledge_assertion
        edge(4, G, p, "MONDO:3", "DisGeNET", "pharos", "v"),                              # the same pair, second source
        edge(5, G, p, "MONDO:4", "JensenLab Text Mining", "pharos", "v", knowledge_level="text_co_occurrence")])
    write(root / "data" / "kg" / "pharos", "nodes", nodes)
    write(root / "data" / "kg" / "pharos", "edges", edges[KGX])
    write(root / "data" / "kg" / "pharos", "edge_verdicts", pd.DataFrame(
        [dict(id=f"e{i:016x}", verdict="keep", confidence=str(c)) for i, c in ((1, 0.2), (2, 0.6), (3, 0.9), (4, 0.8), (5, 0.3))]))
    return PharosGraph(root=root)


CY = ("MATCH (g:Gene {name:'GENE1'})-[r:gene_associated_with_condition]->(d:Disease) "
      "RETURN d.name AS disease, r.source AS source, r.knowledge_level AS knowledge_level, r.id AS evidence "
      "ORDER BY disease LIMIT NN")


def test_level_rank_puts_curated_first_and_text_mining_last():
    r = querier.LEVEL_RANK
    assert r["knowledge_assertion"] < r["statistical_association"] < r["not_provided"] < r["prediction"] < r["text_co_occurrence"]


def test_evidence_key_uses_the_weakest_edge_then_corroboration_then_confidence(graph):
    e = graph.edges
    curated, stat, text = e[f"e{3:016x}"], e[f"e{2:016x}"], e[f"e{1:016x}"]
    k = lambda *xs: querier.evidence_key(list(xs), graph)
    assert k(curated) < k(stat) < k(text)
    assert k(curated)[1] == -2                               # CTD and DisGeNET state the same pair
    assert k(curated, text) == k(text, curated) and k(curated, text)[0] == querier.LEVEL_RANK["text_co_occurrence"]
    assert querier.evidence_key([], graph)[0] == querier.LEVEL_RANK["not_provided"]


def test_plain_run_local_is_unchanged(graph):
    res = querier.run_local(CY.replace("NN", "10"), graph)
    assert [r["disease"] for r in res["rows"]] == ["aaa disease", "bbb disease", "ccc disease", "ccc disease", "ddd disease"]
    assert "ordering" not in res


def test_evidence_first_orders_before_limit_and_merges_the_same_answer(graph):
    res = querier.run_local(CY.replace("NN", "2"), graph, evidence_first=True)
    assert [r["disease"] for r in res["rows"]] == ["ccc disease", "bbb disease"]        # curated (two sources), statistical
    top = res["rows"][0]
    assert top["source"] == ["CTD", "DisGeNET"] and top["evidence"] == [f"e{3:016x}", f"e{4:016x}"]
    o = res["ordering"]
    assert (o["n_total"], o["n_shown"], o["n_more"], o["n_more_text_mined"], o["collapsed"]) == (4, 2, 2, 2, 1)
    assert o["sentence"].startswith("Top 2 of 4 answers by evidence") and "2 more not shown, 2 of them text-mined only" in o["sentence"]
    assert set(res["evidence"]) == {f"e{i:016x}" for i in (2, 3, 4)}
    full = querier.run_local(CY.replace("NN", "10"), graph, evidence_first=True)
    assert [r["disease"] for r in full["rows"]] == ["ccc disease", "bbb disease", "ddd disease", "aaa disease"]   # text-mined: higher confidence first
    assert full["ordering"]["sentence"].startswith("All 4 answers, strongest evidence first")


def test_aggregates_keep_the_plain_order(graph):
    cy = ("MATCH (g:Gene {name:'GENE1'})-[r:gene_associated_with_condition]->(d:Disease) "
          "RETURN d.name AS disease, collect(r.id) AS evidence ORDER BY disease LIMIT 10")
    assert querier.run_local(cy, graph, evidence_first=True) == querier.run_local(cy, graph)


def test_rank_rows_orders_another_databases_rows_like_run_local(graph):
    cy = CY.replace("NN", "10")
    remote = querier.run_local(cy, graph)                      # what Neptune returns: the query's own order
    ranked = querier.rank_rows(cy, remote, graph)
    local = querier.run_local(cy, graph, evidence_first=True)
    assert ranked["rows"] == local["rows"] and ranked["evidence"] == local["evidence"]
    assert ranked["ordering"]["sentence"].endswith("(among the rows the database returned).")


def test_summary_leads_with_the_strongest_supported_answers(graph):
    res = querier.run_local(CY.replace("NN", "2"), graph, evidence_first=True)
    res = dict(res, question="q", cypher=CY.replace("NN", "2"), problems=[], entities=[G])
    facts = answer_summary.edge_facts(graph, res["evidence"])
    s = answer_summary.pharos_summary(res, facts)
    assert "ccc disease and bbb disease" in s["headline"] and s["headline_refs"]["rows"] == [1, 2]
    assert s["headline"].endswith("4 diseases in all, 2 text-mined only.")
    assert any("Showing the top 2 of 4 answers by evidence; 2 more are not shown, 2 of them text-mined only." == c for c in s["caveats"])
    assert not any("LIMIT" in c for c in s["caveats"]) and s["ordering"]["by"] == "evidence"


def test_above_the_lookup_cap_only_the_level_orders(graph, monkeypatch):
    monkeypatch.setenv("TMKG_EVIDENCE_LOOKUP_MAX", "2")
    res = querier.run_local(CY.replace("NN", "10"), graph, evidence_first=True)
    assert res["ordering"]["full"] is False and "more than 2 matches" in res["ordering"]["sentence"]
    assert res["rows"][0]["disease"] == "ccc disease" and res["rows"][-1]["disease"] in ("aaa disease", "ddd disease")
