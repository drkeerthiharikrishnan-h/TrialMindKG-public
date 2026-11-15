"""pipelines/prune_calibration.py on a toy verdict table and toy Open Targets pairs."""
import sys
sys.path.insert(0, "pipelines")

import pandas as pd

from prune_calibration import corroboration_table, markdown


def _toy():
    v = pd.DataFrame({
        "subject": [f"NCBIGene:{i}" for i in range(8)], "predicate": ["biolink:gene_associated_with_condition"] * 8,
        "object": ["MONDO:0000001"] * 8,
        "source": ["CTD", "CTD", "DisGeNET", "DisGeNET", "Expression Atlas", "Expression Atlas", "TIN-X", "TIN-X"],
        "confidence": [0.9, 0.8, 0.7, 0.6, 0.5, 0.2, 0.6, 0.1],
        "verdict": ["keep", "keep", "keep", "keep", "keep", "prune", "keep", "prune"]})
    ot = pd.DataFrame({"subject": ["NCBIGene:0", "NCBIGene:1", "NCBIGene:2", "NCBIGene:6"],
                       "object": ["MONDO:0000001"] * 4, "ot_score": [0.9, 0.3, 0.05, 0.5]})
    return v, ot


def test_corroboration_rates_by_verdict_decile_and_source():
    v, ot = _toy()
    t = corroboration_table(v, ot, threshold=0.2, deciles=2)
    assert t["n_non_text"] == 6 and t["n_text"] == 2
    bv = t["by_verdict"]
    assert bv.loc["keep", "n"] == 5 and abs(bv.loc["keep", "in_ot"] - 3 / 5) < 1e-9 and abs(bv.loc["keep", "ot_strong"] - 2 / 5) < 1e-9
    assert bv.loc["prune", "in_ot"] == 0.0
    bd = t["by_decile"]
    assert list(bd.index) == [1, 2] and bd.loc[2, "in_ot"] == 1.0 and bd.loc[1, "in_ot"] == 0.0   # top half: genes 0,1,2
    assert t["by_source"].loc["Expression Atlas", "pruned"] == 0.5
    assert t["tinx_by_verdict"].loc["keep", "in_ot"] == 1.0 and t["tinx_by_verdict"].loc["prune", "in_ot"] == 0.0
    ds = t["by_decile_source"]                 # decile 1 = confidences 0.2, 0.5, 0.6; decile 2 = 0.7, 0.8, 0.9
    assert ds["n"].to_dict() == {(1, "DisGeNET"): 1, (1, "Expression Atlas"): 2, (2, "CTD"): 2, (2, "DisGeNET"): 1}
    assert ds.loc[(2, "CTD"), "ot_strong"] == 1.0 and ds.loc[(2, "DisGeNET"), "ot_strong"] == 0.0
    assert ds["n"].sum() == t["n_non_text"] and (ds.groupby("decile")["n"].sum() == bd["n"]).all()


def test_markdown_is_computed_from_the_tables():
    v, ot = _toy()
    t = corroboration_table(v, ot, threshold=0.2, deciles=2)
    text = markdown(t, 0.2, len(ot))
    assert "Least-confident decile: in OT 0.0%" in text and "most-confident decile: 100.0%" in text
    assert "| Expression Atlas | 2 |" in text and "## Text mining (TIN-X, JensenLab Text Mining) by verdict" in text
    assert "6 non-text-mining + 2 text-mining (TIN-X, JensenLab Text Mining:" in text
    assert "## Sources within each confidence decile" in text and "| CTD | 0 | 2 / 100.0% |" in text
    assert "| Expression Atlas | 2 / 0.0% | 0 |" in text


def test_every_text_mining_source_is_kept_out_of_the_non_text_tables():
    """JensenLab Text Mining carries knowledge level text_co_occurrence in the Pharos layer, like TIN-X: its rows count
    as text mining (not in the verdict, decile or source tables), as in scripts/prune_report_from_verdicts.py."""
    v, ot = _toy()
    extra = pd.DataFrame({"subject": ["NCBIGene:8", "NCBIGene:9"], "predicate": [v["predicate"][0]] * 2,
                          "object": ["MONDO:0000001"] * 2, "source": ["JensenLab Text Mining"] * 2,
                          "confidence": [0.65, 0.15], "verdict": ["keep", "prune"]})
    base = corroboration_table(v, ot, threshold=0.2, deciles=2)
    t = corroboration_table(pd.concat([v, extra], ignore_index=True), ot, threshold=0.2, deciles=2)
    assert t["n_non_text"] == base["n_non_text"] == 6 and t["n_text"] == 4
    assert "JensenLab Text Mining" not in t["by_source"].index and "TIN-X" not in t["by_source"].index
    assert t["by_verdict"]["n"].to_dict() == base["by_verdict"]["n"].to_dict() == {"keep": 5, "prune": 1}
    assert t["by_decile"]["n"].sum() == 6
    assert t["tinx_by_verdict"]["n"].to_dict() == {"keep": 2, "prune": 2}
