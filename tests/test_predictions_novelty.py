"""Tests for scripts/predictions_novelty.py (CS-5) on a hand-made fixture: known / novel / reverse / ancestor flags."""
import gzip
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import predictions_novelty as pn  # noqa: E402

PRED_HEAD = "graph\ttask\tsubject\tobject\tscore\trank\tby\tmodel\tbest_shape\tn_shapes\tpaths\n"
PREDS = [("G:1", "MONDO:3", 5.0), ("G:1", "MONDO:1", 4.0), ("G:1", "MONDO:9", 3.0),
         ("G:2", "MONDO:9", 2.0), ("E:2", "MONDO:5", 1.0), ("G:3", "MONDO:7", 0.5)]
VERD_HEAD = "id\tsubject\tpredicate\tobject\tverdict\n"
VERDICTS = [("v1", "G:3", "biolink:related_to", "MONDO:7", "prune"),                       # G:3-MONDO:7 pruned only
            ("v2", "G:1", "biolink:related_to", "MONDO:3", "keep")]
EDGE_HEAD = "id\tsubject\tpredicate\tobject\tlayer\tverdict\tknowledge_level\n"
EDGES = [("e1", "G:1", "biolink:gene_associated_with_condition", "MONDO:3", "L0", "keep", "text_co_occurrence"),   # G:1-MONDO:3 known
         ("e2", "G:1", "biolink:related_to", "MONDO:4", "L1", "prune", "knowledge_assertion"),                     # MONDO:4 is_a 2 is_a 1
         ("e3", "MONDO:9", "biolink:related_to", "G:2", "L1", "keep", "knowledge_assertion"),                      # reverse only
         ("e4", "G:2", "biolink:has_gene_product", "P:1", "L0", "keep", "knowledge_assertion"),
         ("e5", "E:2", "biolink:has_gene_product", "P:1", "L2", "keep", "knowledge_assertion"),                    # E:2 ~ G:2
         ("e6", "G:2", "biolink:gene_associated_with_condition", "MONDO:5", "L0", "keep", "knowledge_assertion")]
MONDO_HEAD = "id\tsubject\tpredicate\tobject\n"
MONDO = [("m1", "MONDO:4", "biolink:subclass_of", "MONDO:2"), ("m2", "MONDO:2", "biolink:subclass_of", "MONDO:1"),
         ("m3", "MONDO:1", "biolink:subclass_of", "MONDO:4")]                              # cycle tolerated


def _gz(path, head, rows):
    with gzip.open(path, "wt") as fh:
        fh.write(head + "".join("\t".join(map(str, r)) + "\n" for r in rows))
    return str(path)


def _fixture(tmp_path):
    preds = _gz(tmp_path / "p.tsv.gz", PRED_HEAD,
                [("G3", "B", s, o, sc, i + 1, "subject", "metapath", "x", 1, "[]") for i, (s, o, sc) in enumerate(PREDS)])
    return (preds, _gz(tmp_path / "e.tsv.gz", EDGE_HEAD, EDGES), _gz(tmp_path / "m.tsv.gz", MONDO_HEAD, MONDO),
            _gz(tmp_path / "v.tsv.gz", VERD_HEAD, VERDICTS))


def test_flags_known_novel_reverse_and_ancestor(tmp_path):
    preds, edges, mondo, verd = _fixture(tmp_path)
    rows = pn.read_predictions(preds)
    assert "paths" not in rows[0] and rows[0]["score"] == "5.0"
    scan = pn.scan_edges(edges, {r["subject"] for r in rows}, {(r["subject"], r["object"]) for r in rows})
    assert scan["n_edges"] == 6 and scan["verdicts"] == {"keep": 5, "prune": 1}
    parents = pn.mondo_parents(mondo)
    assert pn.ancestors("MONDO:4", parents, {}) == {"MONDO:2", "MONDO:1"}
    out = {(r["subject"], r["object"]): r for r in pn.flag(rows, scan, parents)}
    assert out[("G:1", "MONDO:3")]["known_in_any_layer"] == "yes" and out[("G:1", "MONDO:3")]["known_layers"] == "L0"
    assert out[("G:1", "MONDO:1")]["known_in_any_layer"] == "no"
    assert out[("G:1", "MONDO:1")]["ancestor_of_known"] == "yes"        # via pruned edge to MONDO:4
    assert out[("G:1", "MONDO:9")]["ancestor_of_known"] == "no"
    assert out[("G:2", "MONDO:9")]["known_in_any_layer"] == "no" and out[("G:2", "MONDO:9")]["stated_reverse"] == "yes"
    assert pn.equivalent_genes(scan["products"]) == {"G:2": {"E:2"}, "E:2": {"G:2"}}
    assert out[("G:1", "MONDO:3")]["known_levels"] == "text_co_occurrence"
    pairs = {(r["subject"], r["object"]) for r in rows}
    pruned = pn.pruned_pairs([verd], pairs, {r["subject"] for r in rows})
    assert pruned == ({("G:3", "MONDO:7")}, {"G:3": {"MONDO:7"}})
    out2 = {(r["subject"], r["object"]): r for r in pn.flag(rows, scan, parents, pruned)}
    assert out2[("G:3", "MONDO:7")]["known_in_any_layer"] == "yes" and out2[("G:3", "MONDO:7")]["pruned_only"] == "yes"
    assert out2[("G:1", "MONDO:3")]["pruned_only"] == "no"


def test_node_counts_dedupe_and_card_top(tmp_path):
    preds, edges, mondo, verd = _fixture(tmp_path)
    rows = pn.read_predictions(preds)
    scan = pn.scan_edges(edges, {r["subject"] for r in rows}, {(r["subject"], r["object"]) for r in rows})
    flagged = pn.flag(rows, scan, pn.mondo_parents(mondo))
    c = pn.node_counts(flagged + flagged[:1], "G:1", top_n=2)                  # duplicate row is collapsed
    assert c["rows"] == 4 and c["pairs"] == 3 and c["novel"] == 2 and c["novel_not_ancestor"] == 1
    assert c["top"] == 2 and c["top_novel"] == 1 and c["top_novel_not_ancestor"] == 0
    assert pn.node_counts(flagged, "MONDO:9")["pairs"] == 2


def test_main_writes_both_files(tmp_path):
    preds, edges, mondo, verd = _fixture(tmp_path)
    out, md = tmp_path / "n.tsv.gz", tmp_path / "n.md"
    pn.main(["--predictions", preds, "--edges", edges, "--mondo", mondo, "--verdicts", verd, "--out", str(out),
             "--md", str(md)])
    lines = gzip.open(out, "rt").read().splitlines()
    assert len(lines) == 7 and lines[0].endswith("known_in_any_layer\tknown_layers\tknown_levels\tpruned_only\t"
                                                 "stated_reverse\tancestor_of_known")
    text = md.read_text()
    assert "| **novel** (in no layer) | **4** (66.7%)" in text and "Known only through a pruned layer edge: 1 rows" in text
    assert "novel and not an ancestor of a known disease | **3**" in text
    assert "(e.g. an ENSEMBL and an NCBIGene id of one gene): 1 (still counted as novel" in text   # E:2 ~ G:2 -> MONDO:5
    assert ("proposes 3 gene-disease links that no source in the graph states yet (of 6 scored predictions: 2 restate a "
            "pair the graph already holds, 1 of them only as a text-mining co-mention, and 1 name a broader") in text
