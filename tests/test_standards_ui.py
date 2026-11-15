"""CC-34 (review M10, m4, m6): measured potency on binding rows and chips (pharos_app.potency), TIGA's -log10 p named as such,
the brief's three IRIs located from the source card (schema_card.brief_iris), and a BioLink model file the image can carry
(data/reference fallback)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
import answer_summary  # noqa: E402
import pharos_app  # noqa: E402
import schema_card  # noqa: E402
from pharos_app import PharosGraph  # noqa: E402
from pharos_index import CardIndex, build_index  # noqa: E402
from fixtures.pharos_fixture import build, PROT, CMP  # noqa: E402


def test_potency_reads_pharos_activity_and_chembl_pchembl():
    p = pharos_app.potency({"activity": "6.2", "activity_type": "IC50"})
    assert p["value"] == 6.2 and p["measure"] == "IC50" and p["word"] == "1 uM or stronger" and "pChEMBL" in p["unit"]
    q = pharos_app.potency({"pchembl_max": "5.0", "n_activities": "1"})
    assert q == dict(value=5.0, measure="best pChEMBL", n_activities=1, unit=pharos_app.POTENCY_UNIT, word="weaker than 1 uM")
    assert pharos_app.potency({"activity": ""}) is None and pharos_app.potency({}) is None


@pytest.fixture(scope="module")
def graphs(tmp_path_factory):
    root = build(tmp_path_factory.mktemp("st"))
    return PharosGraph(root=root), CardIndex(build_index(root, chunk=3, log=lambda *_: None)["path"])


def test_binding_rows_carry_potency_on_the_live_card_and_the_index(graphs):
    g, ci = graphs
    for c in (g.card(PROT), ci.card(PROT)):
        rows = [r for k in c["known"] for r in k["rows"] if r["subject"] == CMP]
        assert rows and rows[0]["potency"]["value"] == 6.2 and rows[0]["potency"]["measure"] == "IC50"
        other = [r for k in c["known"] for r in k["rows"] if r["subject"] != CMP]
        assert other and all(r["potency"] is None for r in other)
    assert g.card(PROT)["known"] == ci.card(PROT)["known"]
    f = answer_summary.edge_facts(g, [f"e{5:016x}"])
    assert f[f"e{5:016x}"]["potency"]["value"] == 6.2


def test_tiga_column_is_named_minus_log10_p():
    d = next(x for x in pharos_app.DEMO_QUESTIONS if x["id"] == "pharos_demo_5")
    assert "AS median_neg_log10_p" in d["cypher"] and "AS median_p_value" not in d["cypher"]
    v3 = (ROOT / "pipelines" / "app_page_v3.html").read_text()
    assert 'median_neg_log10_p: ["median -log10 p"' in v3 and "function potencyChip(p)" in v3 and "${potencyChip(r.potency)}" in v3


def measured_gene_count():
    """FIX1 F2: the SO:0001217 count of the first export report that measured gene types (the S05 rebuild committed them,
    outputs/export_overrides/), None while no export has run. The brief rows must carry that number, never one of their own."""
    got = schema_card._gene_report(schema_card.export_reports(ROOT))
    return got[1]["nodes"]["gene_types"].get("SO:0001217", 0) if got else None


def test_brief_iris_are_located_in_the_card_and_checked_in_the_graph():
    """CC-34 and PK5 (finding ES-01): each row carries the IRI in full, exactly as the organisers' brief prints it."""
    rows = {r["curie"]: r for r in schema_card.brief_iris(ROOT / "sources" / "pharos.yaml")}
    assert set(rows) == {"SO:0001217", "PR:000000001", "SO:translates_to"} and all(r["in_card"] for r in rows.values())
    assert rows["SO:0001217"]["iri"] == "http://purl.obolibrary.org/obo/SO_0001217"
    assert rows["PR:000000001"]["iri"] == "http://purl.obolibrary.org/obo/PR_000000001"
    assert rows["SO:translates_to"]["iri"] == "http://purl.obolibrary.org/obo/so#translates_to"
    brief = (ROOT / "docs" / "organizers" / "BRIEF.md").read_text()
    assert all(r["iri"] in brief and r["url"] == r["iri"] for r in rows.values())      # the brief's own strings
    assert "Gene node" in rows["SO:0001217"]["where"] and "Protein node" in rows["PR:000000001"]["where"]
    assert "biolink:has_gene_product" in rows["SO:translates_to"]["where"] and rows["PR:000000001"]["in_graph"] is None
    assert rows["SO:0001217"]["count"] == measured_gene_count()           # only an export report counts it (S05 committed one)
    with_graph = {r["curie"]: r for r in schema_card.brief_iris(ROOT / "sources" / "pharos.yaml",
                                                                {"Gene": ["type"], "Protein": ["name"]})}
    assert with_graph["SO:0001217"]["in_graph"] is True and with_graph["PR:000000001"]["in_graph"] is False
    for name in ("app_page.html", "app_page_v3.html"):
        assert "The brief's three IRIs: where they are." in (ROOT / "pipelines" / name).read_text()


def test_brief_iri_counts_come_from_the_pharos_build_summary():
    """Protein and gene -> protein counts from the Pharos build summary; the Gene row only from an export report (PK13)."""
    summary = schema_card.parse_summary((ROOT / "outputs" / "pharos" / "summary.md").read_text())
    rows = {r["curie"]: r for r in schema_card.brief_iris(ROOT / "sources" / "pharos.yaml", summary=summary)}
    assert rows["SO:0001217"]["count"] == measured_gene_count()   # PK13: genes carry their own biotype; counted at export, not here
    assert rows["PR:000000001"]["count"] == summary["nodes_by_class"]["biolink:Protein"] > 0
    assert rows["SO:translates_to"]["count"] == sum(e["edges"] for e in summary["edges"]
                                                    if e["predicate"] == "biolink:has_gene_product") > 0
    assert rows["SO:translates_to"]["count_what"] == "has_gene_product edges" and rows["SO:0001217"]["kind"] == "node"


def test_biolink_model_falls_back_to_the_committed_reference(tmp_path):
    (tmp_path / "data" / "reference").mkdir(parents=True)
    ref = tmp_path / "data" / "reference" / "biolink-model.yaml"
    ref.write_text("version: x\n")
    assert schema_card.biolink_yaml(tmp_path / "nowhere", tmp_path) == ref
    assert schema_card.biolink_yaml(tmp_path / "nowhere") == tmp_path / "nowhere" / schema_card.BIOLINK_YAML
