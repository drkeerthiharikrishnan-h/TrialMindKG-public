"""CC-20: free-text questions must not scan the graph. Anchors bound first, pair lookups, typed adjacency, node cache, work budget.
Old run_local (tests/fixtures/querier_before_cc20.py) vs new on the fixture graph and the fixture card index."""
import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "pipelines")
import querier as Q  # noqa: E402
import pharos_app  # noqa: E402
import usability  # noqa: E402
from pharos_app import PharosGraph  # noqa: E402
from pharos_index import CardIndex, build_index  # noqa: E402
from index_graph import IndexGraph  # noqa: E402
from fixtures.pharos_fixture import build  # noqa: E402

_spec = importlib.util.spec_from_file_location("querier_before", Path(__file__).parent / "fixtures" / "querier_before_cc20.py")
OLD = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(OLD)

EXTRA = [
    "MATCH (g:Gene)-[r:gene_associated_with_condition]->(d:Disease) RETURN g.name AS g, d.name AS d, r.id AS e ORDER BY g, d LIMIT 50",
    "MATCH (g:Gene {name:'PTGS2'})-[r]->(x) RETURN x.id AS x, r.id AS e ORDER BY x, e LIMIT 50",
    "MATCH (d:Disease)<-[r:gene_associated_with_condition]-(g:Gene {id:'NCBIGene:5743'}) RETURN d.name AS d, r.id AS e LIMIT 50",
    "MATCH (a {id:'NCBIGene:5743'})-[r]-(b {id:'MONDO:0004975'}) RETURN r.id AS e LIMIT 50",
    "MATCH (a {id:'NCBIGene:5743'}), (b {id:'MONDO:9999999'}), (a)-[r]->(b) RETURN r.id AS e LIMIT 5",
]


@pytest.fixture(scope="module")
def graphs(tmp_path_factory):
    repo = build(tmp_path_factory.mktemp("repo"))
    stats = build_index(repo, chunk=3, log=lambda *_: None)
    return PharosGraph(root=repo), IndexGraph(CardIndex(stats["path"]))


def queries():
    return [d["cypher"] for d in pharos_app.DEMO_QUESTIONS] + EXTRA


@pytest.mark.parametrize("which", [0, 1])
def test_old_and_new_run_local_agree_on_every_demo_and_extra_query(graphs, which):
    kg = graphs[which]
    for cy in queries():
        want = OLD.run_local(cy, kg)
        got = Q.run_local(cy, kg)
        assert (got["columns"], got["rows"], got["evidence"]) == (want["columns"], want["rows"], want["evidence"]), cy
        assert "problems" not in got


def test_existing_trial_queries_agree_with_the_old_code():
    from kg_query import KGIndex
    kg = KGIndex()
    for g in Q.load_gold():
        want, got = OLD.run_local(g["cypher"], kg), Q.run_local(g["cypher"], kg)
        assert (got["columns"], got["rows"], got["evidence"]) == (want["columns"], want["rows"], want["evidence"]), g["id"]


class Counting:
    """Graph wrapper that counts adjacency reads per node; offers edges_between."""

    def __init__(self, kg):
        self.kg, self.reads, self.pair_calls = kg, [], 0
        self.nodes = kg.nodes
        self.by_subject, self.by_object = self._adj(kg.by_subject), self._adj(kg.by_object)

    def _adj(self, m):
        outer = self

        class A:
            def get(self, nid, default=None):
                outer.reads.append(nid)
                return m.get(nid, [] if default is None else default)
        return A()

    def edges_between(self, a, b):
        self.pair_calls += 1
        return [e for e in self.kg.by_subject.get(a, []) if e["object"] == b]


def test_a_two_anchor_query_reads_the_pair_not_the_middle_nodes_edges(graphs):
    live = graphs[0]
    cy = next(d["cypher"] for d in pharos_app.DEMO_QUESTIONS if d["id"] == "pharos_demo_3")
    c = Counting(live)
    res = Q.run_local(cy, c)
    assert res["rows"] and c.pair_calls >= 1
    genes = {r["gene"] for r in res["rows"]}
    gene_ids = {i for i in live.nodes if live.nodes[i].get("name") in genes}
    assert gene_ids and not (set(c.reads) & gene_ids)             # the gene nodes' own adjacency was never read


def test_a_missing_anchor_gives_no_rows_without_reading_any_edge(graphs):
    c = Counting(graphs[0])
    res = Q.run_local("MATCH (a {id:'NCBIGene:5743'}), (b {id:'MONDO:nope'}), (a)-[r]->(b) RETURN r.id AS e LIMIT 5", c)
    assert res["rows"] == [] and c.reads == [] and c.pair_calls == 0


def test_the_index_pushes_the_relationship_type_into_sql(graphs):
    idx = graphs[1]
    allr = idx.by_subject.get("NCBIGene:5743")
    typed = idx.adjacent("NCBIGene:5743", "subject", ["gene_associated_with_condition"])
    assert typed and len(typed) < len(allr)
    assert all(Q.local(e["predicate"]) == "gene_associated_with_condition" for e in typed)
    assert idx.edges_between("NCBIGene:5743", "MONDO:0004975") == [e for e in allr if e["object"] == "MONDO:0004975"]


def test_nodes_are_read_once_per_query(graphs):
    idx = graphs[1]
    reads = []
    real = idx.ci._node
    idx.ci._node = lambda nid: (reads.append(nid), real(nid))[1]
    try:
        Q.run_local("MATCH (g:Gene)-[r:gene_associated_with_condition]->(d:Disease) RETURN g.name AS g, d.name AS d, r.id AS e LIMIT 50", idx)
    finally:
        idx.ci._node = real
    assert len(reads) == len(set(reads))


def test_the_step_budget_returns_the_too_broad_problem_not_an_exception(graphs):
    cy = "MATCH (g:Gene)-[r:gene_associated_with_condition]->(d:Disease) RETURN g.name AS g, r.id AS e LIMIT 50"
    res = Q.run_local(cy, graphs[0], max_steps=1)
    assert res["rows"] == [] and res["evidence"] == [] and res["problems"][0].startswith(Q.TOO_BROAD)
    assert Q.run_local(cy, graphs[0], max_steps=0, max_seconds=0)["rows"]
    res = Q.run_local(cy, graphs[0], max_seconds=-1)               # already past the deadline on the first check
    assert res["problems"][0].startswith(Q.TOO_BROAD)


def test_the_budget_comes_from_env_vars(graphs, monkeypatch):
    cy = "MATCH (g:Gene)-[r:gene_associated_with_condition]->(d:Disease) RETURN g.name AS g, r.id AS e LIMIT 50"
    monkeypatch.setenv("TMKG_QUERY_MAX_STEPS", "1")
    assert Q.run_local(cy, graphs[0])["problems"][0].startswith(Q.TOO_BROAD)


def test_the_page_label_says_too_broad_in_plain_words():
    lab = usability.answer_label(dict(problems=[Q.TOO_BROAD], backend="nim"))
    assert lab["kind"] == "too_broad" and "specific gene" in lab["text"]


def test_a_too_broad_answer_survives_ask(graphs, tmp_path, monkeypatch):
    from test_index_graph import hosted
    monkeypatch.setenv("TMKG_QUERY_MAX_STEPS", "1")
    cy = "MATCH (g:Gene)-[r:gene_associated_with_condition]->(d:Disease) RETURN g.name AS g, r.id AS e LIMIT 50"
    out = Q.Querier(hosted(cy, tmp_path, "b"), graphs[0], examples=[], pattern_examples=[]).ask("Which genes are linked to diseases?", entities=[])
    assert out["problems"][0].startswith(Q.TOO_BROAD) and out["rows"] == []


def test_a_dict_graph_without_the_new_methods_is_unchanged():
    class Plain:
        def __init__(self):
            self.nodes = {"a": dict(id="a", category="biolink:Drug"), "b": dict(id="b", category="biolink:Drug")}
            e = dict(id="e1", subject="a", predicate="biolink:interacts_with", object="b")
            self.by_subject, self.by_object = {"a": [e]}, {"b": [e]}
    kg = Plain()
    for cy in ["MATCH (x:Drug {id:'a'})-[r:interacts_with]->(y:Drug {id:'b'}) RETURN x.id AS x, r.id AS e LIMIT 5",
               "MATCH (x:Drug)-[r:interacts_with]->(y:Drug) RETURN x.id AS x, r.id AS e LIMIT 5"]:
        got = Q.run_local(cy, type("G", (), {"nodes": kg.nodes, "by_subject": type("D", (), {"get": lambda s, k, d=None: kg.by_subject.get(k, d or [])})(),
                                             "by_object": type("D", (), {"get": lambda s, k, d=None: kg.by_object.get(k, d or [])})()})())
        assert got["rows"] == [{"x": "a", "e": "e1"}], cy


# ------------------------------------------------------------------------------------------------ CC-21
BROAD = "MATCH (g:Gene)-[r:gene_associated_with_condition]->(d:Disease) RETURN g.name AS g, d.name AS d, r.id AS e LIMIT 10"


@pytest.mark.parametrize("which", [0, 1])
def test_the_bindings_budget_returns_too_broad_not_an_exception(graphs, which):
    res = Q.run_local(BROAD, graphs[which], max_bindings=1)
    assert res["rows"] == [] and res["problems"][0].startswith(Q.TOO_BROAD)
    assert Q.run_local(BROAD, graphs[which], max_bindings=0)["rows"]           # 0 = no limit


def test_the_bindings_budget_comes_from_env(graphs, monkeypatch):
    monkeypatch.setenv("TMKG_QUERY_MAX_BINDINGS", "1")
    assert Q.run_local(BROAD, graphs[0])["problems"][0].startswith(Q.TOO_BROAD)


def test_the_node_cache_is_bounded_and_answers_do_not_change(graphs):
    nodes = Q._CachedNodes(graphs[1].nodes, 2)
    for nid in list(graphs[1].nodes)[:6]:
        nodes[nid]
    assert len(nodes.cache) == 2
    for cy in queries():
        want = Q.run_local(cy, graphs[1])
        got = Q.run_local(cy, graphs[1], node_cache=1)
        assert (got["columns"], got["rows"], got["evidence"]) == (want["columns"], want["rows"], want["evidence"]), cy
        assert Q.run_local(cy, graphs[1], node_cache=0)["rows"] == want["rows"]


def test_the_node_cache_env_var_is_read(monkeypatch):
    monkeypatch.setenv("TMKG_QUERY_NODE_CACHE", "7")
    assert Q._limits(None, None)[3] == 7 and Q._limits(None, None)[2] == 100_000


def test_candidates_count_toward_the_step_budget(graphs):
    cy = "MATCH (g:Gene)-[r:gene_associated_with_condition]->(d:Disease) RETURN g.name AS g, r.id AS e LIMIT 5"
    n_nodes = len(graphs[0].nodes)
    assert Q.run_local(cy, graphs[0], max_steps=2)["problems"][0].startswith(Q.TOO_BROAD)
    assert Q.run_local(cy, graphs[0], max_steps=n_nodes * 50 + 1000)["rows"]


# ------------------------------------------------------------------------------------------------ CC-31e
def test_too_broad_names_the_limit_that_fired_and_the_page_says_it(graphs):
    cy = "MATCH (g:Gene)-[r:gene_associated_with_condition]->(d:Disease) RETURN g.name AS g, r.id AS e LIMIT 50"
    steps = Q.run_local(cy, graphs[0], max_steps=1, max_seconds=0)["problems"][0]
    assert steps == Q.too_broad_text("steps", 1) and "more than 1 link checks; limit TMKG_QUERY_MAX_STEPS" in steps
    rows = Q.run_local(BROAD, graphs[0], max_bindings=1, max_seconds=0)["problems"][0]
    assert "limit TMKG_QUERY_MAX_BINDINGS" in rows
    lab = usability.answer_label(dict(problems=[steps], backend="nim"))
    assert lab["kind"] == "too_broad" and "it needs more than 1 link checks" in lab["text"] and "specific gene" in lab["text"]


def test_the_count_guard_is_deterministic_and_the_clock_is_only_a_safety_net(graphs, monkeypatch):
    cy = "MATCH (g:Gene)-[r:gene_associated_with_condition]->(d:Disease) RETURN g.name AS g, r.id AS e LIMIT 50"
    first = [Q.run_local(cy, graphs[i], max_steps=3, max_seconds=0)["problems"] for i in (0, 1)]
    assert all(Q.run_local(cy, graphs[i], max_steps=3, max_seconds=0)["problems"] == first[i] for i in (0, 1) for _ in range(3))
    monkeypatch.delenv("TMKG_QUERY_MAX_SECONDS", raising=False)
    assert Q._limits(None, None)[1] == 60                          # well above the step budget's usual time
    assert "safety limit" in Q.run_local(cy, graphs[0], max_steps=0, max_seconds=-1)["problems"][0]
