"""Tests for scripts/summary_from_layer.py (CS-2): the build summary rewritten from built files, same function."""
import gzip
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import summary_from_layer as sl  # noqa: E402
from schema_card import parse_summary  # noqa: E402  (pipelines/ is on the path via conftest)


def _gz(path, text):
    with gzip.open(path, "wt") as fh:
        fh.write(text)


def test_summary_from_built_files_parses_like_a_build_summary(tmp_path):
    kg = tmp_path / "kg"
    kg.mkdir()
    _gz(kg / "nodes.tsv.gz", "id\tcategory\tname\nG:1\tbiolink:Gene\ta\nG:2\tbiolink:Gene\tb\nD:1\tbiolink:Disease\tc\n")
    _gz(kg / "edges.tsv.gz", "id\tsubject\tpredicate\tobject\tprimary_knowledge_source\tn_records\n"
        "e1\tG:1\tbiolink:gene_associated_with_condition\tD:1\tinfores:pharos\t2\n"
        "e2\tG:2\tbiolink:gene_associated_with_condition\tD:1\tinfores:pharos\t1\n"
        "e3\tG:1\tbiolink:interacts_with\tG:2\tinfores:string\t5\n")
    um = tmp_path / "unmapped.tsv"
    um.write_text("edge_spec\treason\trows\ngene_disease\tno subject identifier\t7\n")
    b = sl.layer_build({"name": "toy", "version": "v9"}, kg, um, chunksize=2)      # chunks of 2 rows
    text = sl.summary_text(b)
    assert "3 nodes, 3 edges." in text and "# toy graph (source version v9)" in text
    assert "| biolink:gene_associated_with_condition | infores:pharos | 2 | 3 |" in text
    assert "| biolink:interacts_with | infores:string | 1 | 5 |" in text
    assert "| gene_disease | no subject identifier | 7 |" in text and "not recomputed here" in text
    s = parse_summary(text)
    assert s["n_nodes"] == 3 and s["n_edges"] == 3 and s["source_version"] == "v9"
