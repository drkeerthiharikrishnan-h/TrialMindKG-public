"""CC-33 (review findings M6, m8): the headline is computed from the committed result files, its verbs follow the intervals,
the README block, the Numbers page and the landing JSON carry the same text, and scripts/benchmark_quick.py prints the deltas."""
import importlib.util
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
import headline as H  # noqa: E402


def test_every_number_comes_from_a_committed_file():
    h = H.headline(ROOT)
    c, p = h["numbers"]["comparisons"], h["numbers"]["pruning"]
    assert all(c.values()) and all(v is not None for v in p.values())
    assert f"{c['paths_g2']['mean']:+.3f}" in h["text"] and f"{p['pruned']:,}" in h["text"] and f"{p['g3_removed']:,}" in h["text"]
    assert "not biological accuracy" in h["text"] and "not proven error removal" in h["text"]
    assert all((ROOT / f).is_file() for f in h["files"])


def test_verbs_follow_the_intervals(tmp_path):
    lp = tmp_path / "outputs" / "linkpred"
    lp.mkdir(parents=True)
    def res(vals, metric):
        return {"settings": {"test_set": {"digest": "d"}}, "per_seed": [{"folds": [{metric: v} for v in vals]}],
                "summary": {metric: {"mean": sum(vals) / len(vals), "ci95": 0}}}
    for name, (v, b, m) in H.COMPARISONS.items():
        (lp / b).write_text(json.dumps(res([0.60, 0.61, 0.59, 0.60, 0.62], m)))
    for name, (v, b, m) in H.COMPARISONS.items():
        up = name in ("paths_g2", "paths_g3", "paths_g2_e008", "paths_nomondo", "rgcn_g2")
        (lp / v).write_text(json.dumps(res([0.70, 0.71, 0.69, 0.70, 0.72] if up else [0.60, 0.62, 0.58, 0.61, 0.60], m)))
    h = H.headline(tmp_path)
    assert "scores higher on the enriched graph that is shipped" in h["text"]
    assert "the plain R-GCN of the frozen protocol gains" in h["text"] and "a RotatE embedding does not gain" in h["text"]
    assert "does not help" not in h["text"]                       # a gaining R-GCN changes the lead
    assert not any("Pruning marks" in s for s in h["sentences"])    # no prune report in this root: the sentence is left out


def test_readme_block_matches_the_generator():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    block = text.split(H.START, 1)[1].split(H.END, 1)[0].strip("\n")
    assert block == H.readme_block(H.headline(ROOT)).rstrip("\n"), "run: python pipelines/headline.py --readme"
    assert H.render_readme(text, H.readme_block(H.headline(ROOT))) == text


def test_every_generated_block_is_up_to_date():
    """PK5: README (first sentence, agent paragraph, run log, headline), the judge's card, the agent-loop page and the
    history table in the experiments page are all what headline.py writes."""
    for rel, want in H.expected(ROOT).items():
        assert (ROOT / rel).read_text(encoding="utf-8") == want, f"{rel}: run python pipelines/headline.py --readme"
    assert set(H.expected(ROOT)) == set(H.BLOCKS)


def test_the_headline_is_the_shipped_graph_and_one_value_everywhere():
    """PK5 (finding ES-05): the quoted gain is the shipped graph's (9 November build) against Pharos alone on the same GPU;
    README, the judge's card and the experiments page quote that value and no earlier build's as the result."""
    c = H.comparisons(ROOT)
    assert H.COMPARISONS["paths_g2"][:2] == ("G2_B_kfold@diag_gnnpath_1m_cs9.json", "G0_B_kfold@diag_gnnpath_1m_gpu.json")
    assert H.COMPARISONS["paths_g3"][:2] == ("G3_B_kfold@diag_gnnpath_1m_cs9.json", "G0_B_kfold@diag_gnnpath_1m_gpu.json")
    assert H.COMPARISONS["paths_pruning"][:2] == ("G3_B_kfold@diag_gnnpath_1m_cs9.json", "G2_B_kfold@diag_gnnpath_1m_cs9.json")
    g2, g3 = f"{c['paths_g2']['mean']:+.3f}", f"{c['paths_g3']['mean']:+.3f}"
    assert (g2, g3) == ("+0.124", "+0.122")                                 # the values the ledger row "D (CS-9 graphs)" reports
    h = H.headline(ROOT)
    assert g2 in h["lead"] and g3 in h["lead"] and h["lead"].endswith(".")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.index(h["lead"]) < readme.index("## ")                    # the result opens the README, before any section
    sub = (ROOT / "docs" / "SUBMISSION.md").read_text(encoding="utf-8")
    card = sub.split("## 2.", 1)[0]
    assert f"**{g2} AUROC**" in card and f"**{g3}**" in card
    for stale in ("+0.121", "+0.119 "):                                     # the 7 November values, once quoted as the result
        assert stale not in readme and stale not in card.replace("(+0.122 / +0.119)", ""), stale
    exp = (ROOT / "docs" / "EXPERIMENTS_LINKPRED.md").read_text(encoding="utf-8")
    assert "The submission quotes D:" not in exp and exp.count("The submission quotes these numbers") == 1


def test_controls_name_the_build_they_ran_on():
    """The MONDO control, the plain R-GCN and RotatE ran on the 4 November build: the sentences that use them say so."""
    h = H.headline(ROOT)
    mondo = next(s for s in h["sentences"] if "MONDO" in s)
    assert "on the 4 November build, where this control was run" in mondo and "not repeated on the shipped graph" in mondo
    plain = next(s for s in h["sentences"] if "plain R-GCN" in s)
    assert "(both measured on the 4 November build)" in plain
    assert all(d["build"] in H.BUILD_LABEL for d in h["numbers"]["comparisons"].values())
    assert set(H.BUILD) == set(H.COMPARISONS)


def test_history_table_has_the_three_builds_and_marks_one_as_quoted():
    rows = H.history(ROOT)
    assert [r["build"] for r in rows] == ["shipped", "release", "nov4"] and [r["shipped"] for r in rows] == [True, False, False]
    assert all(r["g2"] and r["g3"] and r["g2"]["n"] == 5 for r in rows)
    got = [f"{r['g2']['mean']:+.4f}/{r['g3']['mean']:+.4f}" for r in rows]
    assert got[:2] == ["+0.1235/+0.1219", "+0.1216/+0.1186"]                 # ledger rows "D (CS-9 graphs)" and "D GNN+paths"
    md = H.history_markdown(rows)
    assert md.count("**yes**") == 1 and md.count("no, history only") == 2
    assert md in (ROOT / "docs" / "EXPERIMENTS_LINKPRED.md").read_text(encoding="utf-8")
    assert "not run" in H.history_markdown([dict(rows[0], g3=None)])


def test_render_block_needs_its_markers():
    import pytest
    assert H.render_block("a\n<!-- x -->\nold\n<!-- /x -->\nb", "new", "<!-- x -->", "<!-- /x -->") == "a\n<!-- x -->\nnew\n<!-- /x -->\nb"
    with pytest.raises(ValueError):
        H.render_block("no markers", "new", "<!-- x -->", "<!-- /x -->")


def test_numbers_page_and_landing_carry_the_headline():
    from fastapi.testclient import TestClient
    import app as appmod
    from kg_query import KGIndex
    from nim import NIM
    appmod._state.update(kg=KGIndex(), nim=NIM(backend="offline"))
    c = TestClient(appmod.app)
    want = H.headline(ROOT)["text"]
    assert c.get("/api/results").json()["headline"]["text"] == want == c.get("/api/landing").json()["headline"]["text"]


def test_benchmark_quick_prints_every_comparison(capsys):
    spec = importlib.util.spec_from_file_location("bq", ROOT / "scripts" / "benchmark_quick.py")
    bq = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bq)
    rows = bq.rows()
    assert [r[0] for r in rows] == list(H.COMPARISONS) and all(r[4] == 5 for r in rows)
    assert bq.main() == 0
    out = capsys.readouterr().out
    assert "paths_g2" in out and H.headline(ROOT)["text"] in out


def test_stale_quotes_are_gone():
    assert "AUPRC 0.116" not in (ROOT / "docs" / "USABILITY_PLAN.md").read_text()
    assert "older smaller cloud build" not in (ROOT / "CLAUDE.md").read_text()
    lic = (ROOT / "DATA_LICENSES.md").read_text()
    assert "covers the code only" in lic and "DATA_LICENSES.md" in (ROOT / "README.md").read_text()
    assert "whole graph" not in json.loads((ROOT / "downloads.json").read_text())["files"][0]["title"]


def test_run_log_and_mondo_share_follow_the_intervals(tmp_path):
    """Rule 7 in the run log: the verb beside a number comes from its interval, and no MONDO share is stated when the full
    gain is not clearly above 0 or the control gain is outside 0..full."""
    lp = tmp_path / "outputs" / "linkpred"
    lp.mkdir(parents=True)
    def res(vals, metric):
        return {"settings": {"test_set": {"digest": "d"}}, "per_seed": [{"folds": [{metric: v} for v in vals]}],
                "summary": {metric: {"mean": sum(vals) / len(vals), "ci95": 0}}}
    base, up = [0.60, 0.61, 0.59, 0.60, 0.62], [0.70, 0.71, 0.69, 0.70, 0.72]
    for name, (v, b, m) in H.COMPARISONS.items():
        (lp / b).write_text(json.dumps(res(base, m)))
    for name, (v, b, m) in H.COMPARISONS.items():                       # the plain R-GCN gains; without MONDO the gain is larger
        (lp / v).write_text(json.dumps(res([x + 0.05 for x in up] if name == "paths_nomondo" else up, m)))
    rows = {r["ledger"]: r["agent"] for r in H.run_log(tmp_path)}
    assert "the plain graph network scored higher (" in rows["E001"]
    assert "the enriched graph scored higher than Pharos alone" in rows["E008 control"]
    assert not any("MONDO" in s for s in H.headline(tmp_path)["sentences"])
    real = {r["ledger"]: r["agent"] for r in H.run_log(ROOT)}
    assert "scored lower (" in real["E001"]
    assert H.run_log_markdown([dict(date="2026-03-09", ledger="x", agent="a", decision="d")]).splitlines()[2].startswith("| 9 Mar |")


def test_open_targets_check_carries_its_n():
    """FIX2 F3 (G2-AC-01): the Open Targets cross-check says how many links it rests on and what part of the pruning it covers."""
    p = dict(nontext_gd_pruned_in_ot=0.7, nontext_gd_kept_in_ot=17.9, nontext_gd_pruned_n=280, nontext_gd_pruned_in_ot_n=2,
             nontext_gd_kept_n=296694, pruned=256639, ppi_pruned=238679, text_share_of_pruned=4.1)
    s = H.ot_check_sentence(p)
    assert s == ("Of the 280 pruned gene-disease edges that are not text-mined, 2 (0.7%) also appear in Open Targets (which the "
                 "pruner never sees), against 17.9% of the 296,694 kept ones; those 280 are 0.11% of the 256,639 pruned edges, "
                 "most of which are protein-protein or text-mined, so this check covers few of them.")
    assert "confirmed" not in s
    bare = H.ot_check_sentence(dict(p, nontext_gd_pruned_n=None))                     # no calibration file: the shares only
    assert bare.startswith("Pruned gene-disease edges that are not text-mined also appear in Open Targets") and "280" not in bare
    q = H.prune_numbers(ROOT)
    assert q["nontext_gd_pruned_n"] and f"Of the {q['nontext_gd_pruned_n']:,} pruned gene-disease edges" in H.headline(ROOT)["text"]


def test_open_targets_coverage_words_follow_the_numbers():
    p = dict(nontext_gd_pruned_in_ot=0.7, nontext_gd_kept_in_ot=17.9, nontext_gd_pruned_n=280, nontext_gd_pruned_in_ot_n=2,
             nontext_gd_kept_n=296694, pruned=400, ppi_pruned=10, text_share_of_pruned=1.0)
    s = H.ot_check_sentence(p)                          # 280 of 400: most pruned edges ARE in the check here
    assert "70.00% of the 400 pruned edges." in s and "most of which" not in s and "covers few" not in s
