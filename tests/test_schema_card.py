"""Tests for pipelines/schema_card.py and pipelines/maintain.py: the schema card takes terms from the BioLink model
and counts from the layer summaries (with the file named), flags non-BioLink terms; the maintain diff reports what a
rebuild changed. Small fixture files; the BioLink test of the committed card needs the model file (skipped without)."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "pipelines")
import maintain  # noqa: E402
import schema_card as sc  # noqa: E402

SUMMARY = """# pharos graph (source version pharos319 (Pharos 3.19.2 / TCRD 6.13.5))

1,000 nodes, 2,500 edges.

## Nodes by class

| class | nodes |
|---|---|
| biolink:Gene | 600 |
| biolink:Protein | 400 |

## Edges by predicate and primary knowledge source

| predicate | primary knowledge source | edges | source rows |
|---|---|---|---|
| biolink:has_gene_product | infores:pharos | 400 | 400 |
| biolink:gene_associated_with_condition | infores:ctd | 2,000 | 2,100 |
| biolink:made_up_predicate | infores:x | 100 | 100 |

## Source rows not in the graph (identifier could not be standardised)

| edge spec | reason | rows |
|---|---|---|
| gene_disease | no subject identifier | 12 |
"""
BIOLINK = dict(version="4.4.5",
               classes={"Gene": dict(iri=sc.VOCAB + "Gene", exact_mappings=["SO:0000704"]),
                        "Protein": dict(iri=sc.VOCAB + "Protein", exact_mappings=["PR:000000001"])},
               slots={"has_gene_product": dict(iri=sc.VOCAB + "has_gene_product", exact_mappings=["RO:0002205"]),
                      "gene_associated_with_condition": dict(iri=sc.VOCAB + "gene_associated_with_condition",
                                                             exact_mappings=[])})


def test_parse_summary():
    s = sc.parse_summary(SUMMARY)
    assert s["title"] == "pharos" and s["source_version"].startswith("pharos319") and (s["n_nodes"], s["n_edges"]) == (1000, 2500)
    assert s["nodes_by_class"] == {"biolink:Gene": 600, "biolink:Protein": 400}
    assert s["edges"][1] == dict(predicate="biolink:gene_associated_with_condition", source="infores:ctd", edges=2000, rows=2100)
    assert len(s["edges"]) == 3                                  # the unmapped-rows table is not read as edges


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "pharos.yaml").write_text("name: pharos\n")
    (tmp_path / "sources" / "opentargets_layer.yaml").write_text("name: opentargets\n")    # no summary: skipped
    (tmp_path / "outputs" / "pharos").mkdir(parents=True)
    (tmp_path / "outputs" / "pharos" / "summary.md").write_text(SUMMARY)
    return tmp_path


def test_card_takes_terms_from_biolink_and_flags_the_rest(repo):
    card = sc.build_card(repo, BIOLINK)
    assert [l["layer"] for l in card["layers"]] == ["pharos"] and card["layers"][0]["file"] == "outputs/pharos/summary.md"
    prot = next(c for c in card["classes"] if c["name"] == "Protein")
    assert prot["exact_mappings"] == ["PR:000000001"] and prot["iri"].endswith("/Protein") and prot["nodes"] == {"pharos": 400}
    gad = next(p for p in card["predicates"] if p["name"] == "gene_associated_with_condition")
    assert gad["edges"] == {"pharos": 2000} and gad["sources"] == {"pharos": ["infores:ctd"]}
    assert card["problems"] == ["predicate biolink:made_up_predicate is not a BioLink predicate in model 4.4.5"]
    md = sc.card_markdown(card)
    assert "## Problems" in md and "`PR:000000001`" in md and "NOT BIOLINK" in md and "| EFO | biolink:DiseaseOrPhenotypicFeature |" in md


def test_committed_card_is_all_biolink():
    path = Path(sc.ROOT) / sc.BIOLINK_YAML
    if not path.exists():
        pytest.skip("BioLink model file not downloaded (scripts/download_external_data.sh)")
    card = sc.build_card(sc.ROOT, sc.load_biolink(path))
    assert card["problems"] == [] and any(c["name"] == "Protein" and "PR:000000001" in c["exact_mappings"]
                                          for c in card["classes"])


def test_summary_diff_and_report():
    old = sc.parse_summary(SUMMARY)
    new = sc.parse_summary(SUMMARY.replace("| biolink:Gene | 600 |", "| biolink:Gene | 650 |")
                           .replace("2,500 edges", "2,450 edges")
                           .replace("| biolink:made_up_predicate | infores:x | 100 | 100 |\n", "")
                           .replace("| biolink:has_gene_product | infores:pharos | 400 | 400 |",
                                    "| biolink:has_gene_product | infores:pharos | 450 | 450 |"))
    d = maintain.summary_diff(old, new)
    assert d["classes"] == [dict(key="biolink:Gene", old=600, new=650, change=50)]
    assert {r["key"]: r["new"] for r in d["predicates"]} == {"biolink:has_gene_product | infores:pharos": 450,
                                                            "biolink:made_up_predicate | infores:x": None}
    md = maintain.diff_markdown("pharos", d)
    assert "1 node classes and 2 predicate/source rows changed; edges 2,500 -> 2,450 (-50 (-2.0%))" in md
    assert "| biolink:made_up_predicate | infores:x | 100 | - | gone |" in md
    assert maintain.diff_markdown("pharos", maintain.summary_diff(old, old)).splitlines()[2].startswith("No change in pharos")


def test_maintain_diff_against_git_head(repo):
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
    run("init", "-q"); run("-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x")
    p = repo / "outputs" / "pharos" / "summary.md"
    p.write_text(SUMMARY.replace("| biolink:Protein | 400 |", "| biolink:Protein | 410 |"))
    assert maintain.committed_text(repo, "outputs/pharos/summary.md") == SUMMARY
    assert maintain.main(["diff", "--root", str(repo)]) == 0
    out = (repo / "outputs" / "maintain" / "diff_pharos.md").read_text()
    assert "| biolink:Protein | 400 | 410 | +10 (+2.5%) |" in out
    assert maintain.committed_text(repo, "outputs/none.md") is None


def test_numbers_page_shows_the_models_ontology_terms(tmp_path):
    from fixtures.pharos_fixture import build
    from pharos_app import PharosGraph
    import app as appmod
    root = build(tmp_path / "r")
    y = root / sc.BIOLINK_YAML
    y.parent.mkdir(parents=True)
    y.write_text("version: 4.4.5\nclasses:\n  protein:\n    exact_mappings: [PR:000000001]\n  gene: {}\nslots: {}\n")
    r = appmod.results_page(root, PharosGraph(root=root))
    by = {c["name"]: c for c in r["schema"]["classes"]}
    assert by["Protein"]["terms"] == ["PR:000000001"] and by["Gene"]["terms"] == [] and "SCHEMA_CARD" in r["schema"]["note"]


# ------------------------------------------------------------------ PK5: the brief's IRIs, the trial layer, why these classes
def test_card_opens_with_the_briefs_three_iris_in_full(repo):
    """Finding ES-01: the first section of the card gives the three IRIs as full, linked IRIs with what carries each and
    how many, and the Gene row says its SO:0000704 mapping is the class default that the node-level type overrides."""
    (repo / "sources" / "pharos.yaml").write_text(
        "name: pharos\n"
        "nodes:\n"
        "  - {table: targets, category: 'biolink:Protein', constants: {type: 'PR:000000001'}}\n"
        "  - {table: gene_ids, category: 'biolink:Gene', constants: {type: 'SO:0001217'}}\n"
        "edges:\n"
        "  - {name: gene_has_product, table: gene_ids, predicate: 'biolink:has_gene_product',\n"
        "     constants: {original_predicate: 'SO:translates_to'}}\n")
    card = sc.build_card(repo, BIOLINK)
    rows = {r["curie"]: r for r in card["brief_iris"]}
    assert rows["SO:0001217"]["count"] is None and rows["PR:000000001"]["count"] == 400   # PK13: Gene counted at export and rows["SO:translates_to"]["count"] == 400
    md = sc.card_markdown(card)
    first = md.split("## ", 2)[1]
    assert first.startswith("The brief's three IRIs")
    for iri, n in (("http://purl.obolibrary.org/obo/SO_0001217", "protein-coding by Open Targets biotype"),
                   ("http://purl.obolibrary.org/obo/PR_000000001", "400 Protein nodes"),
                   ("http://purl.obolibrary.org/obo/so#translates_to", "400 has_gene_product edges")):
        assert f"[{iri}]({iri})" in first and n in first, iri
    assert "Why the gene -> protein edge is `biolink:has_gene_product`" in first and "no transcript nodes" in first
    gene_row = next(l for l in md.splitlines() if l.startswith("| Gene |"))
    assert "`SO:0000704`" in gene_row and "more specific type `SO:0001217`" in gene_row
    protein_row = next(l for l in md.splitlines() if l.startswith("| Protein |"))
    assert "more specific" not in protein_row                    # the class default already is the brief's term


def test_committed_card_has_the_iris_the_reasons_and_the_trial_layer():
    """Findings ES-01 and ES-11 on the committed card: full IRIs first, 'Why these classes', and every tmkg: term of the
    built trial layer with a BioLink parent that is in the model file; no claim that every term is BioLink."""
    md = (Path(sc.ROOT) / "docs" / "SCHEMA_CARD.md").read_text()
    for iri in ("http://purl.obolibrary.org/obo/SO_0001217", "http://purl.obolibrary.org/obo/PR_000000001",
                "http://purl.obolibrary.org/obo/so#translates_to"):
        assert iri in md, iri
    assert md.index("## The brief's three IRIs") < md.index("## Layers and the files") < md.index("## Why these classes") \
        < md.index("## The trial layer: its own terms and their BioLink parents")
    assert "of the Pharos core and the enrichment layers below is a BioLink Model term" in md
    for reason in ("As few as the identifiers allow", "What was merged", "classes for the Gene Ontology",
                   "DiseaseOrPhenotypicFeature next to Disease", "two interaction predicates"):
        assert reason in md, reason
    import json
    card = json.loads((Path(sc.ROOT) / "outputs" / "schema_card.json").read_text())
    assert sc.card_markdown(card) == md                                   # the card is what the generator writes
    assert all(r["in_card"] and (r["count"] or r["class"] == "Gene") for r in card["brief_iris"])   # Gene: after the next export


def test_trial_layer_terms_have_real_biolink_parents():
    """Finding ES-11: counted from the committed trial graph; every tmkg: term names a parent that is a BioLink term."""
    path = sc.biolink_yaml(sc.ROOT)
    if not path.exists():
        pytest.skip("BioLink model file not available")
    import kg_schema
    tl = sc.trial_layer(sc.ROOT, sc.load_biolink(path))
    assert tl["n_nodes"] == sum(c["n"] for c in tl["classes"]) and tl["n_edges"] == sum(p["n"] for p in tl["predicates"])
    own = [x for x in tl["classes"] + tl["predicates"] if not x["is_biolink"]]
    assert own and all(x["curie"].startswith("tmkg:") for x in own)
    assert all(x["parent"] == kg_schema.BIOLINK_PARENT[x["curie"]] and x["parent_ok"] and x["parent_iri"] for x in own), \
        [x["curie"] for x in own if not x["parent_ok"]]
    assert all(x["parent_ok"] for x in tl["classes"] + tl["predicates"] if x["is_biolink"])
    assert tl["n_own_classes"] == sum(not c["is_biolink"] for c in tl["classes"]) > 0
    assert sum(tl["parents"].values()) == tl["n_own_predicates"] and "biolink:related_to" in tl["parents"]
    md = "\n".join(sc.trial_layer_markdown(tl))
    assert "| `tmkg:has_outcome_measure` | `biolink:has_output` | https://w3id.org/biolink/vocab/has_output |" in md
    assert sc.trial_layer_markdown(None) == []
    sub = (Path(sc.ROOT) / "docs" / "SUBMISSION.md").read_text()
    assert f"adds {tl['n_own_classes']} classes and {tl['n_own_predicates']} predicates of its own" in sub
    import json                                                                   # FIX2 F8 (G2-ES-04): the counts of the card, not typed
    card = json.loads((Path(sc.ROOT) / "outputs" / "schema_card.json").read_text())
    assert (f"The Pharos core and the enrichment layers use {len(card['classes'])} node classes and "
            f"{len(card['predicates'])} predicates") in sub


def test_design_numbers_come_from_the_enriched_graph_summary(tmp_path):
    assert sc.design_numbers(tmp_path) == dict(mixin_nodes=None, retyped_rows=None)
    (tmp_path / "outputs" / "g2").mkdir(parents=True)
    (tmp_path / "outputs" / "g2" / "summary.md").write_text(
        "# G2\n\n10 nodes, 20 edges; 0 edges removed by pruning; 1,234 node rows re-typed to one class per id prefix.\n\n"
        "- DiseaseOrPhenotypicFeature: 9,628 nodes\n")
    assert sc.design_numbers(tmp_path) == dict(mixin_nodes=9628, retyped_rows=1234)
    got = sc.design_numbers(sc.ROOT)
    md = (Path(sc.ROOT) / "docs" / "SCHEMA_CARD.md").read_text()
    assert f"{got['mixin_nodes']:,} EFO terms" in md and f"{got['retyped_rows']:,} node rows are re-typed" in md


def test_brief_iris_without_a_summary_reads_the_layer_summary(tmp_path):
    """FIX2 F8 (G2-ES-06): /api/results calls brief_iris without a summary and printed count null for so#translates_to."""
    import shutil
    rows = sc.brief_iris(Path(sc.ROOT) / "sources" / "pharos.yaml")
    edge = next(r for r in rows if r["curie"] == "SO:translates_to")
    if (Path(sc.ROOT) / "outputs" / "pharos" / "summary.md").is_file():
        summ = sc.parse_summary((Path(sc.ROOT) / "outputs" / "pharos" / "summary.md").read_text(encoding="utf-8"))
        want = sum(x["edges"] for x in summ["edges"] if x["predicate"] == "biolink:has_gene_product")
        assert edge["count"] == want and edge["count_what"] == "has_gene_product edges"
        assert next(r for r in rows if r["curie"] == "PR:000000001")["count"] == summ["nodes_by_class"]["biolink:Protein"]
    (tmp_path / "sources").mkdir()
    shutil.copy(Path(sc.ROOT) / "sources" / "pharos.yaml", tmp_path / "sources" / "pharos.yaml")
    bare = next(r for r in sc.brief_iris(tmp_path / "sources" / "pharos.yaml") if r["curie"] == "SO:translates_to")
    assert bare["count"] is None                                           # no summary next to the card: nothing invented


def test_a_biolink_class_without_exact_mapping_says_so_in_its_cell():
    """G1-ES-07 (owner decision 14 Nov): an empty ontology-terms cell of a BioLink class names the reason; the committed
    card carries the sentence on exactly the classes the committed JSON leaves unmapped."""
    import json
    root = Path(sc.__file__).resolve().parents[1]
    card = json.loads((root / "outputs" / "schema_card.json").read_text(encoding="utf-8"))
    unmapped = {c["name"] for c in card["classes"] if c["is_biolink"] and not c["exact_mappings"]}
    rows = {ln.split("|")[1].strip(): ln for ln in (root / "docs" / "SCHEMA_CARD.md").read_text(encoding="utf-8").splitlines()
            if ln.startswith("| ") and "w3id.org/biolink/vocab/" in ln and ln.count("|") > 5}
    assert unmapped, "the committed card has no unmapped class; this test needs one"
    for name in unmapped:
        assert sc.NO_EXACT_MAPPING in rows[name]
    assert not [n for n, ln in rows.items() if n not in unmapped and sc.NO_EXACT_MAPPING in ln]
