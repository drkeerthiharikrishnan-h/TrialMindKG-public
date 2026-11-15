"""Tests for scripts/ppi_corroboration.py on small fixtures (no data/, no network)."""
import io
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ppi_corroboration as c  # noqa: E402


def line(a, b, itype="physical association", pmid="111", expansion="-", negative="false"):
    cells = ["-"] * 42
    cells[0], cells[1] = f"uniprotkb:{a}", f"uniprotkb:{b}"
    cells[8], cells[11], cells[15], cells[35] = f"pubmed:{pmid}|imex:IM-1", f'psi-mi:"MI:0915"({itype})', expansion, negative
    return "\t".join(cells) + "\n"


LINES = ["#ID(s) interactor A\tID(s) interactor B\n",
         line("P2", "P1-2"),                                                   # physical, independent, binary; isoform stripped
         line("P1", "P3", itype="colocalization"),                             # any only
         line("P1", "P4", pmid="26186194"),                                    # physical, BioPlex paper: not independent
         line("P1", "P5", expansion='psi-mi:"MI:1060"(spoke expansion)'),      # independent, not binary
         line("P1", "P6", negative="true"),                                    # negative: skipped
         line("P7", "P7"),                                                     # self: skipped
         "intact:EBI-1\tuniprotkb:P1\t" + "\t".join(["-"] * 40) + "\n",        # not two proteins
         line("P3", "P1")]                                                     # second evidence for P1|P3: now physical too


def _verdicts():
    rows = [("P1", "P2", "STRINGDB", "0.9", "keep"), ("P2", "P1", "STRINGDB", "0.8", "keep"), ("P1", "P3", "STRINGDB", "0.2", "prune"),
            ("P1", "P4", "BioPlex", "0.7", "keep"), ("P1", "P4", "STRINGDB", "0.6", "keep"), ("P1", "P5", "BioPlex,STRINGDB", "0.5", "keep"),
            ("P1", "P6", "STRINGDB", "0.1", "prune"), ("P8", "P9", "STRINGDB", "0.05", "prune"), ("P7", "P7", "mock", "0.0", "prune")]
    df = pd.DataFrame(rows, columns=["subject", "object", "source", "confidence", "verdict"])
    df["subject"], df["object"] = "UniProtKB:" + df["subject"], "UniProtKB:" + df["object"]
    df["predicate"] = c.PREDICATE
    other = pd.DataFrame([{"subject": "UniProtKB:P1", "object": "MONDO:1", "source": "TIN-X", "confidence": "0.4", "verdict": "keep",
                           "predicate": "biolink:gene_associated_with_condition"}])
    return pd.concat([df, other], ignore_index=True)


def test_record_mask():
    assert c.record_mask('psi-mi:"MI:0403"(colocalization)', "pubmed:1", "-") == c.ANY
    assert c.record_mask('psi-mi:"MI:0208"(genetic interaction)', "pubmed:1", "-") == c.ANY
    assert c.record_mask('psi-mi:"MI:0915"(physical association)', "pubmed:28514442", "-") == c.ANY | c.PHYSICAL
    assert c.record_mask('psi-mi:"MI:0914"(association)', "pubmed:1", 'psi-mi:"MI:1060"(spoke expansion)') == c.ANY | c.PHYSICAL | c.INDEPENDENT
    assert c.record_mask('psi-mi:"MI:0407"(direct interaction)', "pubmed:1", "-") == 15


def test_intact_pairs():
    pairs, n = c.intact_pairs(LINES)
    assert pairs == {"P1|P2": 15, "P1|P3": 15, "P1|P4": c.ANY | c.PHYSICAL, "P1|P5": c.ANY | c.PHYSICAL | c.INDEPENDENT}
    assert n == {"lines": 8, "used": 5, "negative": 1, "not_two_proteins": 1, "self": 1}


def test_open_intact_streams_a_zip_member(tmp_path):
    z = tmp_path / "human.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("human.txt", "".join(LINES))
        f.writestr("human_negative.txt", "x")
    with c.open_intact(z) as fh:
        assert c.intact_pairs(fh)[1]["used"] == 5


def test_pair_keys_are_unordered_and_strip_isoforms():
    k = c.pair_keys(pd.Series(["UniProtKB:P2", "UniProtKB:P1-3"]), pd.Series(["UniProtKB:P1", "UniProtKB:P2"]))
    assert list(k) == ["P1|P2", "P1|P2"]


def test_tally_rows_and_summary():
    pairs, n = c.intact_pairs(LINES)
    v = _verdicts()
    counts, conf, corr, pruned, n_self, n_pairs = c.tally_rows([v.iloc[:5], v.iloc[5:]], pairs)
    assert n_self == {"prune": 1} and n_pairs == 6 and len(conf) == 8 == len(corr) == len(pruned)
    assert int(counts["rows"].sum()) == 8                                      # the self pair and the disease row are out
    by = c.by_verdict(counts)
    assert by["keep"] == {"rows": 5, "any": 5, "physical": 5, "independent": 3, "binary": 2, "corroborated": 4}
    assert by["prune"] == {"rows": 3, "any": 1, "physical": 1, "independent": 1, "binary": 1, "corroborated": 1}
    # the BioPlex-sourced P1-P4 row is NOT corroborated by the BioPlex paper's record; the STRING row for the same pair is
    bio = counts[(counts["source"] == "BioPlex")].iloc[0]
    assert bio["physical"] == 1 and bio["corroborated"] == 0
    s = c.summarise(counts, conf, corr, pruned, n_self, n_pairs, n, len(pairs), ["input a"])
    assert s["rows"] == 8 and s["by_verdict"]["keep"]["corroborated_share"] == 0.8
    assert abs(s["by_verdict"]["prune"]["corroborated_share"] - 1 / 3) < 1e-9 and s["corroborated_rows_pruned_share"] == 0.2
    assert json.loads(json.dumps(s))["intact"]["pairs"] == 4                    # JSON-able
    text = "\n".join(c.report(s))
    assert "33.33% of pruned rows and 80.00% of kept rows" in text and "kept rows 2.4 times as often" in text
    assert s["within_source"] == [{"source": "STRINGDB", "prune_rows": 3, "keep_rows": 3, "prune_share": 1 / 3, "keep_share": 1.0}]
    assert "higher than the pruned share in 1 of the 1 sources" in text and s["bioplex_rows"] == {"keep": 2}
    assert "Counting ANY IntAct record" not in text                                    # only said when pruned > kept on that count
    assert "| keep | BioPlex | 1 | 0 | 0.00% |" in text and "1 rows (1 prune)" in text and "- input a" in text
    assert "G0" not in text and "outputs/" not in text


def test_tally_rows_with_nothing_to_count():
    counts, conf, corr, pruned, n_self, n_pairs = c.tally_rows([_verdicts().iloc[9:]], {})
    assert len(counts) == 0 and len(conf) == 0 and n_pairs == 0 and c.decile_table(conf, corr, pruned) == []


def test_wilson_and_deciles():
    lo, hi = c.wilson(5, 10)
    assert 0.23 < lo < 0.24 and 0.76 < hi < 0.77 and all(x != x for x in c.wilson(0, 0))
    conf = np.array([0.1 * i for i in range(20)] + [np.nan], dtype="float32")
    corr = np.array([i >= 10 for i in range(20)] + [True])
    d = c.decile_table(conf, corr, np.array([i < 4 for i in range(21)]))
    assert len(d) == 10 and [x["rows"] for x in d] == [2] * 10 and sum(x["corroborated"] for x in d) == 10
    assert d[0]["share"] == 0.0 and d[-1]["share"] == 1.0 and d[0]["pruned"] == 2 and d[0]["lo"] <= d[0]["hi"] <= d[1]["lo"]


def _summary(all_p, all_k, within, shares):
    return {"by_verdict": {"prune": {"rows": 100, "corroborated_share": all_p}, "keep": {"rows": 900, "corroborated_share": all_k}},
            "within_source": [{"source": src, "prune_share": p, "keep_share": k} for src, p, k in within],
            "deciles": [{"decile": i + 1, "share": v} for i, v in enumerate(shares)]}


def test_within_source_needs_both_verdicts():
    counts = pd.DataFrame([("prune", "STRINGDB", 100, 1), ("keep", "STRINGDB", 1000, 30), ("keep", "Reactome", 50, 5),
                           ("prune", "BioPlex", 10, 1), ("keep", "BioPlex", 10, 2)], columns=["verdict", "source", "rows", "corroborated"])
    w = c.within_source(counts)
    assert [r["source"] for r in w] == ["STRINGDB", "BioPlex"]                              # largest first; Reactome has no pruned rows
    assert w[0] == {"source": "STRINGDB", "prune_rows": 100, "keep_rows": 1000, "prune_share": 0.01, "keep_share": 0.03}


def test_figure_titles_claim_only_what_every_category_shows(tmp_path):
    good = _summary(0.01, 0.05, [("STRINGDB", 0.01, 0.02), ("BioPlex,STRINGDB", 0.07, 0.3)], [0.0, 0.01, 0.02])
    assert c.figure_series(good) == [("all rows", 0.01, 0.05), ("STRING", 0.01, 0.02), ("BioPlex\n+ STRING", 0.07, 0.3)]
    left, right = c.figure_titles(good)
    assert left == "Kept rows have an independent IntAct record more often than pruned rows, in every source"
    assert right == "The share with a record rises with every tenth of pruner confidence"
    one_source_reversed = _summary(0.01, 0.05, [("STRINGDB", 0.03, 0.02)], [0.02, 0.01, 0.03])
    left, right = c.figure_titles(one_source_reversed)                                      # one category contradicts: no claim
    assert left == "Independent IntAct records among pruned and kept interaction rows" and right == "IntAct records by tenth of pruner confidence"
    assert c.figure_titles(_summary(0.01, 0.05, [], [0.01, 0.01, 0.02]))[1].startswith("IntAct records")   # a flat step is not 'rises'
    out = tmp_path / "f.png"
    drawn = c.figure(good, out)
    assert out.stat().st_size > 5000 and drawn["bars"][0] == ("all rows", 1.0, 5.0) and drawn["line"] == [0.0, 1.0, 2.0]
    assert drawn["titles"] == c.figure_titles(good) and drawn["overlaps"] == 0


def test_text_overlaps_counts_colliding_labels():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    ax.set_axis_off()
    fig.text(0.5, 0.5, "one label")
    fig.canvas.draw()
    assert c.text_overlaps(fig) == 0
    fig.text(0.5, 0.5, "another label on top")
    fig.canvas.draw()
    assert c.text_overlaps(fig) == 1
    plt.close(fig)


def test_main_writes_report_json_and_figure(tmp_path, monkeypatch, capsys):
    kg = tmp_path / "data" / "kg" / "pharos"
    kg.mkdir(parents=True)
    (tmp_path / "outputs" / "pharos").mkdir(parents=True)
    _verdicts().to_csv(kg / "edge_verdicts.tsv.gz", sep="\t", index=False)
    z = tmp_path / "human.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("human.txt", "".join(LINES))
    monkeypatch.setattr(c, "ROOT", tmp_path)
    assert c.main(["--intact", str(z), "--figure", str(tmp_path / "docs" / "figures" / "x.png")]) == 0
    s = json.loads((tmp_path / "outputs" / "pharos" / "ppi_corroboration.json").read_text())
    assert s["rows"] == 8 and len(s["deciles"]) == 8 and (tmp_path / "docs" / "figures" / "x.png").exists()
    assert "Headline" in (tmp_path / "outputs" / "pharos" / "ppi_corroboration.md").read_text()
    assert "figure -> docs/figures/x.png" in capsys.readouterr().out
    md = tmp_path / "outputs" / "pharos" / "ppi_corroboration.md"
    first = md.read_text()
    md.write_text("stale")
    assert c.main(["--from-json", "--figure", str(tmp_path / "docs" / "figures" / "y.png")]) == 0      # no IntAct file needed
    assert md.read_text() == first and (tmp_path / "docs" / "figures" / "y.png").exists()
