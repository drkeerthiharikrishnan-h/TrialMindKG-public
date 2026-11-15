"""Tests for scripts/prune_report_from_verdicts.py (CS-2) on a hand-made verdict + edges + Open Targets fixture."""
import gzip
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prune_report_from_verdicts as pr  # noqa: E402

GD = "biolink:gene_associated_with_condition"
V_HEAD = "id\tsubject\tpredicate\tobject\tsource\tconfidence\tverdict\treasons\n"
# id, subject, predicate, object, source, confidence, verdict, reasons, knowledge_level
ROWS = [("e1", "G:1", GD, "D:1", "TIN-X", 0.10, "prune", "x", "text_co_occurrence"),
        ("e2", "G:2", GD, "D:2", "CTD", 0.20, "prune", "x", "knowledge_assertion"),
        ("e3", "G:3", GD, "D:3", "CTD", 0.30, "keep", "x;protected", "knowledge_assertion"),
        ("e4", "G:4", "biolink:has_gene_product", "P:4", "Pharos", 0.05, "keep", "x", "knowledge_assertion"),
        ("e5", "G:5", GD, "D:5", "CTD", 0.60, "keep", "x", "knowledge_assertion"),
        ("e6", "G:6", GD, "D:6", "TIN-X", 0.70, "keep", "x", "text_co_occurrence"),
        ("e7", "G:7", "biolink:interacts_with", "G:8", "STRING", 0.80, "keep", "x", "knowledge_assertion"),
        ("e8", "G:8", GD, "D:8", "CTD", 0.90, "keep", "x", "knowledge_assertion"),
        ("e9", "G:9", GD, "D:9", "CTD", 0.95, "keep", "x", "knowledge_assertion"),
        ("e10", "G:0", GD, "D:0", "CTD", 0.99, "keep", "x", "knowledge_assertion")]
OT_HEAD = "id\tsubject\tpredicate\tobject\tot_score\n"
OT = [("o1", "G:2", GD, "D:2", "0.1"), ("o2", "G:5", GD, "D:5", "0.5"), ("o3", "G:5", GD, "D:5", "0.3"),
      ("o4", "G:8", GD, "D:8", "0.25"), ("o5", "C:1", "biolink:treats", "D:1", "")]


def _gz(path, text):
    with gzip.open(path, "wt") as fh:
        fh.write(text)
    return str(path)


def _fixture(tmp_path, rows=ROWS):
    v = _gz(tmp_path / "v.tsv.gz", V_HEAD + "".join("\t".join(map(str, r[:8])) + "\n" for r in rows))
    e = _gz(tmp_path / "e.tsv.gz", "id\tknowledge_level\n" + "".join(f"{r[0]}\t{r[8]}\n" for r in rows))
    ot = _gz(tmp_path / "ot.tsv.gz", OT_HEAD + "".join("\t".join(r) + "\n" for r in OT))
    cmp = tmp_path / "prune_compare.md"
    cmp.write_text("# x\n\n| variant | AUPRC (combined) | precision |\n|---|---|---|\n| v1 (k 32) | 0.005 | 0.009 |\n"
                   "| trust weight 0 | 0.005 | 0.013 |\n")
    return v, e, ot, str(cmp)


def test_stream_thresholds_and_ot(tmp_path):
    v, e, ot_path, _ = _fixture(tmp_path)
    ot = pr.load_ot_pairs(ot_path)
    assert ot == {("G:2", "D:2"): 0.1, ("G:5", "D:5"): 0.5, ("G:8", "D:8"): 0.25}
    a = pr.stream_verdicts(v, e, ot, exempt={"biolink:has_gene_product"})
    assert a["n_protected"] == 1 and a["pruned"].sum() == 2 and a["eligible"].sum() == 8
    rows = pr.threshold_rows(a, cuts=(0.25, 0.5))
    ship = rows[0]
    assert ship["pruned"] == 2 and ship["threshold"] == {"max_pruned": 0.2, "min_eligible_kept": 0.6}
    assert ship["text_share_of_pruned"] == 0.5
    assert ship["ot_non_text"]["pruned"] == {"n": 1, "in_ot": 1.0, "ot_strong": 0.0}         # G:2 at 0.1
    assert ship["ot_non_text"]["kept"]["n"] == 5 and ship["ot_non_text"]["kept"]["ot_strong"] == pytest.approx(0.4)
    # 25% quantile of the 10 confidences; exempt e4 (0.05) and protected e3 (0.30) are never pruned
    q25 = rows[1]
    assert q25["threshold"] == pytest.approx(0.225) and q25["pruned"] == 2
    q50 = rows[2]          # cut 0.65: e1, e2, e5 pruned (e3 protected, e4 exempt)
    assert q50["pruned"] == 3 and q50["gd"] == {"n": 8, "pruned": 3, "share": 0.375}


def test_misaligned_files_raise(tmp_path):
    v, _, ot_path, _ = _fixture(tmp_path)
    bad = _gz(tmp_path / "bad.tsv.gz", "id\tknowledge_level\n" + "".join(f"x{r[0]}\t{r[8]}\n" for r in ROWS))
    with pytest.raises(ValueError):
        pr.stream_verdicts(v, bad, {})


def test_quote_compare_and_main(tmp_path):
    v, e, ot_path, cmp = _fixture(tmp_path)
    assert pr.quote_compare(cmp, "v1") == {"variant": "v1 (k 32)", "AUPRC (combined)": "0.005", "precision": "0.009"}
    assert pr.quote_compare(tmp_path / "missing.md") is None
    out = tmp_path / "out"
    pr.main(["--layer", "toy", "--verdicts", v, "--edges", e, "--ot", ot_path, "--compare", cmp, "--out-dir", str(out)])
    m = json.loads((out / "prune_metrics.json").read_text())
    assert m["n_edges"] == 10 and m["n_pruned"] == 2 and m["pruned_share"] == 0.2
    assert m["by_knowledge_level"]["text_co_occurrence"] == {"n": 2, "pruned": 1, "share": 0.5}
    assert m["by_predicate"][GD]["n"] == 8
    md = (out / "prune_report.md").read_text()
    assert "edges scored: **10**; verdict 'prune': **2** (**20.00%**)" in md
    assert "| v1 (k 32) | 0.005 | 0.009 |" in md and "between 0.2000 and 0.6000" in md
