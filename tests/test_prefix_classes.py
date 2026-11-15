"""CC-27: one BioLink class per identifier prefix in EVERY product (assembled graphs, card index + live app graph, KGX
export, schema card); OBA measurement traits are phenotypic features, not diseases. Fixtures only."""
import gzip
import sys

import pandas as pd
import pytest

sys.path.insert(0, "pipelines")
import make_graphs as mg  # noqa: E402
import schema_card  # noqa: E402
from pharos_app import PharosGraph  # noqa: E402
from pharos_index import CardIndex, build_index  # noqa: E402

from fixtures.pharos_fixture import build, write  # noqa: E402

OBA, HP, MONDO = "OBA:2040165", "HP:0001250", "MONDO:0004975"
WANT = {OBA: "biolink:PhenotypicFeature", HP: "biolink:PhenotypicFeature", MONDO: "biolink:Disease"}


def layer_nodes():
    """An Open Targets-like layer that types the OBA trait and the HP term as diseases."""
    return pd.DataFrame([dict(id=OBA, category="biolink:Disease", name="body mass index measurement"),
                         dict(id=HP, category="biolink:Disease", name="Seizure"),
                         dict(id=MONDO, category="biolink:Disease", name="Alzheimer disease")])


def test_prefix_table_types_oba_as_phenotypic_feature():
    assert mg.PREFIX_CATEGORY["OBA"] == "biolink:PhenotypicFeature"
    out, n = mg.harmonise_categories(layer_nodes().assign(layer="opentargets_layer"))
    assert dict(zip(out["id"], out["category"])) == WANT and n == 2


def test_make_graphs_combine_retypes_oba_and_hp_and_keeps_mondo():
    nodes, _ = mg.combine([(layer_nodes().assign(layer="opentargets_layer"),
                            pd.DataFrame(columns=["id", "subject", "object"]))], rep := {})
    assert dict(zip(nodes["id"], nodes["category"])) == WANT and rep["retyped"] == 2


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    root = build(tmp_path_factory.mktemp("repo"))
    d = root / "data" / "kg" / "opentargets"
    ot = pd.read_csv(d / "nodes.tsv.gz", sep="\t", dtype=str, keep_default_na=False)
    write(d, "nodes", pd.concat([ot, layer_nodes()], ignore_index=True))
    return root


def test_card_index_and_live_graph_use_the_harmonised_class(repo):
    stats = build_index(repo, log=lambda *_: None)
    idx, live = CardIndex(stats["path"]), PharosGraph(root=repo)
    for nid, cat in WANT.items():
        row = idx.con.execute("SELECT category FROM nodes WHERE id = ?", (nid,)).fetchone()
        assert row["category"] == cat, nid
        assert live.nodes[nid]["category"] == cat, nid
        assert idx.card(nid)["about"] == live.card(nid)["about"]               # the card's "about" line agrees
    hit = [r for r in idx.search("body mass index") if r["id"] == OBA][0]
    assert hit["category"] == "biolink:PhenotypicFeature"                       # search facet shows the harmonised class
    assert idx.graph_status()["nodes_by_category"] == live.status()["nodes_by_category"]


def test_kgx_export_nodes_file_uses_the_harmonised_class(tmp_path, monkeypatch):
    import export_graph_kgx as xk
    monkeypatch.setitem(mg.RECIPES, "GT", {"layers": ["pharos", "otl"], "prune": False})
    for name, rows in (("pharos", [dict(id="NCBIGene:1", category="biolink:Gene", name="A"),
                                   dict(id=MONDO, category="biolink:Disease", name="AD")]),
                       ("otl", layer_nodes().to_dict("records"))):
        (tmp_path / "sources").mkdir(exist_ok=True)
        (tmp_path / "sources" / f"{name}.yaml").write_text(f"name: {name}\n")
        write(tmp_path / "data" / "kg" / name, "nodes", pd.DataFrame(rows).assign(provided_by="infores:x"))
        write(tmp_path / "data" / "kg" / name, "edges", pd.DataFrame(
            [dict(id=f"e_{name}", subject="NCBIGene:1" if name == "pharos" else MONDO,
                  predicate="biolink:gene_associated_with_condition" if name == "pharos" else "biolink:has_phenotype",
                  object=MONDO if name == "pharos" else HP, source="s", source_record="r",
                  primary_knowledge_source="infores:x", knowledge_level="knowledge_assertion", agent_type="manual_agent",
                  layer="L0", source_version="v", n_records="1")]))
    nodes, edges, _ = xk.assemble_kgx("GT", tmp_path)
    xk.write_kgx(nodes, edges, tmp_path / "out")
    with gzip.open(tmp_path / "out" / "nodes.tsv.gz", "rt") as f:
        written = pd.read_csv(f, sep="\t", dtype=str)
    got = dict(zip(written["id"], written["category"]))
    assert {k: got[k] for k in WANT} == WANT
    with gzip.open(tmp_path / "out" / "edges.tsv.gz", "rt") as f:
        assert sorted(pd.read_csv(f, sep="\t", dtype=str)["id"]) == ["e_otl", "e_pharos"]      # edge ids untouched


def test_schema_card_states_the_rule_from_the_prefix_table():
    s = schema_card.prefix_rule_sentence(mg.PREFIX_CATEGORY)
    assert s.startswith("One class per identifier prefix in every product")
    assert "HP/MP/OBA terms are phenotypic features" in s and "MONDO" in s
    assert "EFO terms without an exact MONDO class" in s and "biolink:DiseaseOrPhenotypicFeature" in s
    md = schema_card.card_markdown(dict(biolink_version="4.4.5", layers=[], classes=[], predicates=[], problems=[],
                                        prefixes=[dict(prefix=k, **{"class": v}) for k, v in sorted(mg.PREFIX_CATEGORY.items())]))
    assert s in md and "| OBA | biolink:PhenotypicFeature |" in md
