"""PK3 (findings U6 and AC-05): the Numbers tab's first table and its pruning story, read from committed files only.

pipelines/numbers_page.py lead(), spot_check(), ppi_check(), injected_errors(), pruning_evidence() and WORDS. Fixture files
in a temporary folder for the rules; one check on the repository's own committed files for the numbers the plan quotes.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
import numbers_page as npg  # noqa: E402


def result(values, digest="d1", n_test=100):
    """A gnnpath result file: one seed, one fold per value of auroc_gnnpath, n_test held-out pairs per fold."""
    folds = [dict(auroc_gnnpath=v, n_test=n_test) for v in values]
    return {"settings": {"test_set": {"digest": digest}}, "per_seed": [{"folds": folds}],
            "summary": {"auroc_gnnpath": {"mean": sum(values) / len(values), "ci95": 0.0}}}


def write_lead(lp, g0, g2, g3, digest3="d1"):
    lp.mkdir(parents=True, exist_ok=True)
    for (label, name), res in zip(npg.LEAD_ROWS, (result(g0), result(g2), result(g3, digest=digest3))):
        if res is not None:
            (lp / name).write_text(json.dumps(res))


def test_lead_has_three_rows_in_user_words_with_paired_intervals(tmp_path):
    lp = tmp_path / "outputs" / "linkpred"
    write_lead(lp, [0.60, 0.61, 0.62, 0.60, 0.61], [0.70, 0.72, 0.71, 0.70, 0.73], [0.60, 0.62, 0.61, 0.61, 0.60])
    L = npg.lead(lp)
    assert [r["label"] for r in L["rows"]] == ["Pharos alone", "Agent-built (Pharos + public sources)", "Agent-built and pruned"]
    assert L["pairs"] == 500 and L["folds"] == 5 and L["same_pairs"] is True and L["model"] == "GNN + paths"
    assert L["rows"][0]["delta"] is None and L["rows"][0]["auroc"] == pytest.approx(0.608)
    g2, g3 = L["rows"][1]["delta"], L["rows"][2]["delta"]
    assert g2["mean"] == pytest.approx(0.104) and g2["ci_low"] > 0 and g2["wins"] == 5
    assert g3["ci_low"] < 0 < g3["ci_high"]
    # the words follow the intervals (rule 7)
    assert "Agent-built (Pharos + public sources): +0.104 (higher;" in L["sentence"]
    assert "Agent-built and pruned: " in L["sentence"] and "not clearly different" in L["sentence"]
    assert "the same 500 held-out curated gene-disease pairs, 5 folds" in L["sentence"]
    for code in ("G0", "G2", "G3", "@", "cs9", "diag_"):
        assert code not in L["sentence"] and all(code not in r["label"] for r in L["rows"])


def test_lead_reports_a_different_test_set_and_a_missing_file(tmp_path):
    lp = tmp_path / "outputs" / "linkpred"
    write_lead(lp, [0.6] * 5, [0.7] * 5, [0.65] * 5, digest3="other")
    assert npg.lead(lp)["same_pairs"] is False
    (lp / npg.LEAD_ROWS[2][1]).unlink()
    L = npg.lead(lp)
    assert L["rows"][2]["file"] is None and L["rows"][2]["delta"] is None and len(L["files"]) == 2
    (lp / npg.LEAD_ROWS[0][1]).unlink()
    assert npg.lead(lp) is None                                             # no base: no table


def test_pruning_evidence_order_and_sentences_from_files(tmp_path):
    ph = tmp_path / "outputs" / "pharos"
    ph.mkdir(parents=True)
    (ph / "prune_report.md").write_text(
        "- edges scored: **1,000**; verdict 'prune': **10** (**1.00%**); x\n\n## What the injected-error test says (quoted)\n\n"
        "| variant | AUPRC (combined) | AUPRC (learned) | AUPRC (plausibility) | precision | recall | r | e |\n|---|---|---|---|---|---|---|---|\n"
        "| v1 | 0.050 | 0.040 | 0.030 | 0.090 | 0.200 | 0.1 | 0.1 |\n")
    (ph / "spotcheck_rule.md").write_text("`RULE: prune_unsupported_min=30; keep_unsupported_max=5`\n- Population: x. Sample: 50 pruned + 50\n  kept\n"
                                          "Sample: 50 pruned + 50 kept, same method\n")
    (ph / "ppi_corroboration.md").write_text("# t\n\n**Headline: 2.0% of pruned rows and 3.0% of kept rows have a record** (kept rows 1.5 times as often).\n")
    ev = npg.pruning_evidence(tmp_path)
    assert list(ev) == ["verdicts", "spot_check", "ppi_check", "injected", "audit_sample"]           # AC-05 order; calibration is its own key
    assert ev["verdicts"]["scored"] == 1000 and "990 kept and 10 marked prune (1.00%)" in ev["verdicts"]["sentence"]
    sc = ev["spot_check"]
    assert (sc["rule_pruned_min"], sc["rule_kept_max"], sc["n_pruned"], sc["n_kept"], sc["result"]) == (30, 5, 50, 50, None)
    assert "not finished" in sc["sentence"] and "at least 30 of the 50 pruned" in sc["sentence"]
    (ph / "spotcheck_blind_result.md").write_text("Result: **FAIL** (12 of 50)\n")
    sc = npg.pruning_evidence(tmp_path)["spot_check"]
    assert sc["result"] == "FAIL" and sc["sentence"].endswith(": FAIL.") and "spotcheck_blind_result.md" in sc["files"]
    assert ev["ppi_check"]["sentence"].startswith("2.0% of pruned rows and 3.0% of kept rows")
    inj = ev["injected"]
    assert (inj["auprc"], inj["precision"], inj["recall"]) == (0.05, 0.09, 0.2) and inj["sentence"].startswith("Stated limit")


def test_pruning_evidence_parts_are_none_without_files(tmp_path):
    ev = npg.pruning_evidence(tmp_path)
    assert ev == {"verdicts": None, "spot_check": None, "ppi_check": None, "injected": None, "audit_sample": None}


def test_committed_files_give_the_final_graph_numbers():
    """U6: the final-graph run (+0.1235 / +0.1219 over Pharos alone, 39,513 pairs) is what the first table shows."""
    n = npg.numbers(ROOT)
    L = n["benchmark"]["lead"]
    assert L["pairs"] == 39513 and L["folds"] == 5 and L["same_pairs"]
    assert f"{L['rows'][1]['delta']['mean']:+.4f}" == "+0.1235" and f"{L['rows'][2]['delta']['mean']:+.4f}" == "+0.1219"
    assert all(npg.source_file(ROOT, f) is not None for f in L["files"])          # every lead number links to its file
    ev = n["benchmark"]["pruning"]
    assert ev["verdicts"]["pruned"] == 256639 and ev["injected"]["auprc"] == 0.005 and ev["spot_check"]["n_pruned"] == 100
    for name in ("prune_report.md", "spotcheck_rule.md", "ppi_corroboration.md"):
        assert npg.source_file(ROOT, name) is not None
    assert set(n["benchmark"]["words"]["graphs"]) >= {"G0", "G2", "G3"}
    tags = {r["tag"] for r in n["benchmark"]["runs"]} | {r["tag"] for e in n["experiments"] for r in e["rows"]}
    assert tags <= set(npg.WORDS["tags"]), tags - set(npg.WORDS["tags"])          # every run on the page has a plain name
