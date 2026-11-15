"""Tests for pipelines/subgraph_export.py and the Pharos answer endpoints that use it (subgraph zip, the demo
question strip), on the tiny Pharos fixture. No real data; the PyG loader runs only where torch_geometric exists."""
import csv
import io
import subprocess
import sys
import zipfile

import pytest

sys.path.insert(0, "pipelines")
import subgraph_export as sx  # noqa: E402
from pharos_app import PharosGraph, DEMO_QUESTIONS  # noqa: E402

from fixtures.pharos_fixture import build, GENE, PROT, DIS1, DIS2, CMP  # noqa: E402

E1, E2, E3, E5 = "e0000000000000001", "e0000000000000002", "e0000000000000003", "e0000000000000005"


@pytest.fixture(scope="module")
def g(tmp_path_factory):
    return PharosGraph(root=build(tmp_path_factory.mktemp("repo")))


def rows(text, delim="\t"):
    return list(csv.DictReader(io.StringIO(text), delimiter=delim))


def test_collect_takes_known_edges_with_verdicts_and_both_ends(g):
    nodes, edges = sx.collect(g, [E1, E3, "nope", E1, E5])
    assert [e["id"] for e in edges] == [E1, E3, E5]                    # order kept, unknown and repeats dropped
    assert edges[1]["verdict"] == "prune" and edges[1]["reasons"].startswith("text mining")
    assert edges[0]["verdict"] == "keep" and edges[0]["confidence"] == "0.9"
    assert set(nodes) == {GENE, DIS1, CMP, PROT} and nodes[GENE]["name"] == "PTGS2"


def test_kgx_tables_put_kgx_columns_first(g):
    nodes, edges = sx.collect(g, [E1, E2, E5])
    n_tsv, e_tsv = sx.kgx_tables(nodes, edges)
    er = rows(e_tsv)
    header = e_tsv.splitlines()[0].split("\t")
    assert header[:4] == ["id", "subject", "predicate", "object"] and "verdict" in header and "activity" in header
    assert {r["id"] for r in er} == {E1, E2, E5} and next(r for r in er if r["id"] == E5)["activity"] == "6.2"
    assert rows(n_tsv)[0].keys() >= {"id", "category", "name"}


def test_pyg_tables_index_every_edge_end(g):
    nodes, edges = sx.collect(g, [E1, E2, E5, "e0000000000000004"])
    files = sx.pyg_tables(nodes, edges)
    assert set(files) == {"pyg/nodes_Gene.csv", "pyg/nodes_Disease.csv", "pyg/nodes_Protein.csv",
                          "pyg/nodes_SmallMolecule.csv",
                          "pyg/edges_Gene__gene_associated_with_condition__Disease.csv",
                          "pyg/edges_SmallMolecule__directly_physically_interacts_with__Protein.csv",
                          "pyg/edges_Gene__has_gene_product__Protein.csv"}
    dis = {r["id"]: int(r["index"]) for r in rows(files["pyg/nodes_Disease.csv"], ",")}
    gd = rows(files["pyg/edges_Gene__gene_associated_with_condition__Disease.csv"], ",")
    assert {(r["edge_id"], int(r["dst"])) for r in gd} == {(E1, dis[DIS1]), (E2, dis[DIS2])}
    assert all(r["src"] == "0" for r in gd)                                  # one gene


def test_zip_holds_kgx_pyg_loader_and_readme(g):
    nodes, edges = sx.collect(g, [E1, E3])
    z = zipfile.ZipFile(io.BytesIO(sx.zip_bytes(nodes, edges, "Which diseases is PTGS2 associated with?")))
    names = set(z.namelist())
    assert {"README.txt", "kgx/nodes.tsv", "kgx/edges.tsv", "pyg/load_pyg.py"} <= names
    readme = z.read("README.txt").decode()
    assert "PTGS2" in readme and "2 nodes, 2 edges (1 of them pruned" in readme


def test_load_pyg_script_builds_heterodata(g, tmp_path):
    pytest.importorskip("torch_geometric")
    nodes, edges = sx.collect(g, [E1, E2, E5])
    zipfile.ZipFile(io.BytesIO(sx.zip_bytes(nodes, edges, "t"))).extractall(tmp_path)
    out = subprocess.run([sys.executable, str(tmp_path / "pyg" / "load_pyg.py")], capture_output=True, text=True)
    assert out.returncode == 0 and "gene_associated_with_condition" in out.stdout


def test_demo_filter_and_endpoints(g, monkeypatch):
    from fastapi.testclient import TestClient
    import app as appmod
    assert [d["id"] for d in g.demo(ids={"pharos_demo_3"})] == ["pharos_demo_3"]
    monkeypatch.setattr(appmod, "_pharos", {"index": None})
    monkeypatch.setattr(appmod, "pharos", lambda: pytest.fail("the strip must not load the graph"))
    c = TestClient(appmod.app)
    qs = c.get("/api/pharos/demo_questions").json()
    assert [q["id"] for q in qs] == [d["id"] for d in DEMO_QUESTIONS] and all(q["question"] and q["why"] for q in qs)
    monkeypatch.setattr(appmod, "pharos", lambda: g)
    one = c.get("/api/pharos/demo/pharos_demo_1").json()
    assert one["passed"] and one["evidence"]
    assert c.get("/api/pharos/demo/nope").status_code == 404
    r = c.post("/api/pharos/subgraph.zip", json={"edges": one["evidence"], "title": one["question"]})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "kgx/edges.tsv" in zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert c.post("/api/pharos/subgraph.zip", json={"edges": []}).status_code == 400
    assert c.post("/api/pharos/subgraph.zip", json={"edges": ["nope"]}).status_code == 404
    monkeypatch.setattr(appmod, "MAX_SUBGRAPH_EDGES", 1)
    assert c.post("/api/pharos/subgraph.zip", json={"edges": [E1, E2]}).status_code == 413
