"""pipelines/export_graph_kgx.py: one KGX TSV pair for an assembled graph, with verdict / confidence, on a fixture of
two tiny layers (no real data). BioLink checks need data/external/biolink/biolink-model.yaml."""
import gzip
import sys

import pandas as pd
import pytest

sys.path.insert(0, "pipelines")
import export_graph_kgx as xk  # noqa: E402
import make_graphs as mg  # noqa: E402
from kgbuilder.biolink import DEFAULT as BIOLINK_YAML, BioLink  # noqa: E402

pytestmark = pytest.mark.skipif(not BIOLINK_YAML.exists(), reason="biolink-model.yaml not downloaded")

EDGE = dict(source="s", source_record="r", primary_knowledge_source="infores:x", aggregator_knowledge_source="infores:y",
            knowledge_level="knowledge_assertion", agent_type="manual_agent", source_version="v1", n_records="1")


def _w(path, df):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as f:
        df.to_csv(f, sep="\t", index=False)


def _layer(root, name, nodes, edges, layer_label, verdicts=None):
    (root / "sources").mkdir(exist_ok=True)
    (root / "sources" / f"{name}.yaml").write_text(f"name: {name}\n")
    d = root / "data" / "kg" / name
    _w(d / "nodes.tsv.gz", pd.DataFrame(nodes, columns=["id", "category", "name", "provided_by"]))
    _w(d / "edges.tsv.gz", pd.DataFrame([{**EDGE, "layer": layer_label, **e} for e in edges]))
    if verdicts is not None:
        _w(d / "edge_verdicts.tsv.gz", pd.DataFrame(verdicts, columns=["id", "verdict", "confidence", "reasons"]))


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setitem(mg.RECIPES, "GT", {"layers": ["pharos", "otl"], "prune": True})
    _layer(tmp_path, "pharos",
           [("NCBIGene:1", "biolink:Gene", "A", "infores:pharos"), ("MONDO:5", "biolink:Disease", "d", "infores:pharos"),
            ("MONDO:6", "biolink:Disease", "e", "infores:pharos")],
           [dict(id="e1", subject="NCBIGene:1", predicate="biolink:gene_associated_with_condition", object="MONDO:5"),
            dict(id="e2", subject="NCBIGene:1", predicate="biolink:gene_associated_with_condition", object="MONDO:6")],
           "L0", verdicts=[("e1", "keep", "0.9", "corroborated"), ("e2", "prune", "0.1", "generic object")])
    # a layer with no verdict file: kept whole, empty verdict columns. HP:1 is typed Disease by this layer.
    _layer(tmp_path, "otl",
           [("MONDO:5", "biolink:Disease", "d", "infores:ot"), ("HP:1", "biolink:Disease", "seizure", "infores:ot")],
           [dict(id="o1", subject="MONDO:5", predicate="biolink:has_phenotype", object="HP:1")], "L1")
    return tmp_path


def test_assemble_drops_pruned_by_default_and_attaches_verdicts(root):
    nodes, edges, rep = xk.assemble_kgx("GT", root)
    assert sorted(edges["id"]) == ["e1", "o1"] and rep["pruned"] == 1 and rep["n_edges"] == 2
    e = edges.set_index("id")
    assert (e.loc["e1", "verdict"], e.loc["e1", "confidence"], e.loc["e1", "reasons"]) == ("keep", "0.9", "corroborated")
    assert e.loc["o1", "verdict"] == "" and e.loc["o1", "confidence"] == ""        # never looked at: not called keep
    assert rep["not_pruned"] == ["otl"] and rep["used"] == ["pharos", "otl"]
    # one class per id prefix: HP:1 became a PhenotypicFeature although the layer said Disease
    assert dict(zip(nodes["id"], nodes["category"]))["HP:1"] == "biolink:PhenotypicFeature"


def test_include_pruned_keeps_them_marked(root):
    _, edges, rep = xk.assemble_kgx("GT", root, include_pruned=True)
    assert sorted(edges["id"]) == ["e1", "e2", "o1"]
    assert edges.set_index("id").loc["e2", "verdict"] == "prune" and rep["pruned"] == 1


def test_export_writes_valid_kgx_whose_row_counts_match(root, tmp_path):
    out = tmp_path / "kgx"
    rep, problems, paths = xk.export_graph("GT", root, out_dir=out)
    assert problems == [] and len(paths) == 2
    n = pd.read_csv(paths[0], sep="\t", dtype=str, keep_default_na=False)
    e = pd.read_csv(paths[1], sep="\t", dtype=str, keep_default_na=False)
    assert len(n) == rep["n_nodes"] == 4 and len(e) == rep["n_edges"] == 2
    assert list(e.columns[:4]) == ["id", "subject", "predicate", "object"]
    assert {"verdict", "confidence", "reasons"} <= set(e.columns) and list(n.columns[:2]) == ["id", "category"]
    assert xk.validate_kgx(n, e, BioLink()) == []                   # what was read back is still valid
    text = (out / "kgx_report.md").read_text()
    assert "4 nodes, 2 edges" in text and "otl" in text
    assert "passes the project validator (columns, ids, endpoints, BioLink terms)" in text and "valid KGX" not in text
    assert "Files: nodes.tsv.gz, edges.tsv.gz" in text and str(tmp_path) not in text   # CC-37: no machine paths
    first = paths[1].read_bytes()
    xk.export_graph("GT", root, out_dir=out)
    assert paths[1].read_bytes() == first                            # deterministic bytes


def test_missing_layer_writes_nothing(root, tmp_path):
    (root / "data" / "kg" / "otl" / "nodes.tsv.gz").unlink()
    rep, problems, paths = xk.export_graph("GT", root, out_dir=tmp_path / "x")
    assert paths == () and "otl" in problems[0] and not (tmp_path / "x").exists()
    rep, problems, paths = xk.export_graph("GT", root, out_dir=tmp_path / "x", allow_missing=True)
    assert len(paths) == 2 and rep["missing"] == ["otl"]


def _good():
    n = pd.DataFrame({"id": ["NCBIGene:1", "MONDO:5"], "category": ["biolink:Gene", "biolink:Disease"],
                      "name": ["A", "d"], "provided_by": "infores:x"})
    e = pd.DataFrame([{"id": "e1", "subject": "NCBIGene:1", "predicate": "biolink:gene_associated_with_condition",
                       "object": "MONDO:5", "verdict": "keep", "confidence": "0.5", "reasons": "", **{
                           k: v for k, v in EDGE.items() if k in ("primary_knowledge_source", "knowledge_level",
                                                                  "agent_type")}}])
    return n, e


@pytest.mark.parametrize("change,fragment", [
    (lambda n, e: e.assign(object="MONDO:99"), "not a node"),
    (lambda n, e: e.assign(predicate="biolink:made_up_thing"), "not a BioLink predicate"),
    (lambda n, e: e.assign(predicate="foo:bar"), "neither biolink: nor tmkg:"),
    (lambda n, e: e.assign(knowledge_level="sure"), "knowledge_level"),
    (lambda n, e: e.assign(verdict="maybe"), "not keep / prune"),
    (lambda n, e: e.assign(confidence="1.5"), "outside [0, 1]"),
    (lambda n, e: e.assign(confidence="high"), "not a number"),
    (lambda n, e: e.assign(confidence=""), "both set or both empty"),
    (lambda n, e: e.assign(primary_knowledge_source=""), "primary_knowledge_source"),
    (lambda n, e: pd.concat([e, e]), "duplicate ids"),
    (lambda n, e: e.drop(columns="verdict"), "missing columns"),
])
def test_validate_flags_each_kind_of_problem(change, fragment):
    n, e = _good()
    assert xk.validate_kgx(n, e) == []
    assert any(fragment in p for p in xk.validate_kgx(n, change(n, e)))


def test_validate_flags_bad_node_class_and_duplicate_node():
    n, e = _good()
    bad = pd.concat([n, n.iloc[:1]]).assign(category=lambda d: d["category"].where(d["id"] != "MONDO:5", "biolink:Nonsense"))
    probs = xk.validate_kgx(bad, e)
    assert any("duplicate ids" in p for p in probs) and any("not a BioLink class" in p for p in probs)


def test_tmkg_predicates_are_allowed():
    n, e = _good()
    assert xk.validate_kgx(n, e.assign(predicate="tmkg:arm_receives")) == []


def test_exporter_accepts_the_same_biolink_knowledge_levels_as_the_schema_check():
    """7 Nov: the exporter's list lacked text_co_occurrence (a BioLink KnowledgeLevelEnum value), so the G3 KGX export
    refused the Pharos text-mining edges while the schema card passed them. One set for both."""
    import export_graph_kgx as ex
    import kg_schema
    assert ex.KNOWLEDGE_LEVELS == kg_schema.KNOWLEDGE_LEVELS
    assert "text_co_occurrence" in ex.KNOWLEDGE_LEVELS and "bogus_level" not in ex.KNOWLEDGE_LEVELS
