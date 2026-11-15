"""Tests for pruning v1 (pipelines/kgbuilder/prune.py) on a small synthetic graph; no network."""
import numpy as np
import pandas as pd
from kgbuilder import prune

CFG = {"score_columns": {"zscore": "higher"}, "generic_objects": ["MONDO:0000001"],
       "pair_groups": {"gc": ["biolink:gene_associated_with_condition", "biolink:genetically_associated_with"]}}


def toy(n_genes=60, n_dis=40, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for g in range(n_genes):
        for d in rng.choice(n_dis, 4, replace=False):
            for src, kl, ag in (("infores:ctd", "knowledge_assertion", "manual_agent"),
                                ("infores:diseases", "text_co_occurrence", "text_mining_agent")):
                if src == "infores:ctd" or rng.random() < 0.5:
                    rows.append(dict(subject=f"NCBIGene:{g}", predicate="biolink:gene_associated_with_condition",
                                     object=f"MONDO:{d + 10:07d}", source=src, primary_knowledge_source=src,
                                     knowledge_level=kl, agent_type=ag, n_records="1", zscore=str(rng.random())))
    e = pd.DataFrame(rows)
    e["id"] = ["e%d" % i for i in range(len(e))]
    nodes = pd.DataFrame({"id": [f"NCBIGene:{g}" for g in range(n_genes)] + [f"MONDO:{d + 10:07d}" for d in range(n_dis)],
                          "category": ["biolink:Gene"] * n_genes + ["biolink:Disease"] * n_dis})
    return e, nodes


def test_corroboration_counts_other_sources_and_pair_groups():
    e = pd.DataFrame({"subject": ["G", "G", "G"], "object": ["D", "D", "D"], "primary_knowledge_source": ["a", "b", "c"],
                      "predicate": ["biolink:gene_associated_with_condition"] * 2 + ["biolink:genetically_associated_with"]})
    c, n = prune.corroboration(e, CFG["pair_groups"])
    assert n.tolist() == [3, 3, 3] and np.allclose(c, 2 / 3)


def test_source_score_percentile_and_default():
    e = pd.DataFrame({"predicate": ["p"] * 3, "source": ["s"] * 3, "zscore": ["1", "3", None]})
    s = prune.source_score(e, {"zscore": "higher"})
    assert s.tolist() == [0.5, 1.0, 0.5]


def test_flags_generic_object_and_activity():
    e = pd.DataFrame({"subject": ["A", "B"], "object": ["MONDO:0000001", "X"], "activity": [None, "20"]})
    assert prune.flags(e, CFG).tolist() == [["generic_object"], ["implausible_activity"]]


def test_inject_errors_keeps_class_and_avoids_existing_pairs():
    e, nodes = toy()
    inj = prune.inject_errors(e, nodes, 50, np.random.default_rng(0))
    assert inj["object"].str.startswith("MONDO:").all() and inj["injected"].all()
    real = set(e["subject"] + "|" + e["object"])
    assert not (inj["subject"] + "|" + inj["object"]).isin(real).any()


def test_score_gives_one_verdict_per_edge_and_catches_some_errors():
    e, nodes = toy()
    v, m = prune.score(e, nodes, CFG, seed=0, n_injected=200)
    assert len(v) == len(e) and set(v["verdict"]) <= {"keep", "prune"} and v["id"].tolist() == e["id"].tolist()
    assert v["confidence"].between(0, 1).all() and v["reasons"].str.contains("sources=").all()
    assert m["recall"] > 0 and 0 <= m["pruned_real_fraction"] < 1
    md = prune.report_markdown("toy", m)
    assert "Pruning v1" in md and "real toy edges" in md and "Pharos" not in md   # named after the layer, not Pharos


def test_plausibility_is_leave_one_out_and_separates_communities():
    # two communities: genes 0-19 link to diseases A*, genes 20-39 to diseases B*
    rows = [(f"G{g}", f"{'A' if g < 20 else 'B'}{d}") for g in range(40) for d in range(5)]
    rows.append(("LONE", "A0"))                                   # a subject with a single object
    e = pd.DataFrame(rows, columns=["subject", "object"])
    inj = pd.DataFrame([("G0", "B1"), ("G25", "A2")], columns=["subject", "object"])
    allx = pd.concat([e, inj], ignore_index=True)
    is_inj = np.array([False] * len(e) + [True] * len(inj))
    p, no_ctx = prune.plausibility(allx, is_inj, k=4)
    assert p[:-3].mean() > 0.8 and p[-2:].max() < 0.2               # real edges fit; cross-community errors do not
    assert no_ctx[len(e) - 1] == 1 and no_ctx[:-3].sum() == 0       # LONE->A0: no other evidence


# --- pruning v1.1 (docs/BACKUP_PLAN.md B2) ---

def test_v1_settings_unchanged_without_new_keys():
    e, nodes = toy()
    v1, m1 = prune.score(e, nodes, CFG, seed=0, n_injected=200)
    v2, m2 = prune.score(e, nodes, {**CFG, "trust_weight": 0.2, "svd_k": 32, "plausibility_by": "all"},
                         seed=0, n_injected=200)
    assert v1["confidence"].tolist() == v2["confidence"].tolist() and m1["auprc_errors"] == m2["auprc_errors"]
    assert m1["settings"] == {"version": "v1", "trust_weight": 0.2, "svd_k": 32, "plausibility_by": "all",
                              "protect_curated_corroborated": False}
    assert m1["auprc_learned"] == m1["auprc_plausibility_only"]
    assert set(m1["auprc_by_trust_weight"]) == {"0.0", "0.1", "0.2"}
    assert m1["auprc_by_trust_weight"]["0.2"] == m1["auprc_errors"]


def test_trust_weight_zero_ranks_by_learned_signals_only():
    e, nodes = toy()
    _, m = prune.score(e, nodes, {**CFG, "trust_weight": 0.0}, seed=0, n_injected=200)
    assert m["auprc_errors"] == m["auprc_learned"] and m["settings"]["trust_weight"] == 0.0


def test_protection_keeps_curated_corroborated_edges():
    e, nodes = toy()
    # every pair gets a second curated source, so every curated edge is corroborated
    extra = e[e["primary_knowledge_source"] == "infores:ctd"].copy()
    extra["primary_knowledge_source"] = extra["source"] = "infores:uniprot"
    extra["id"] = "x" + extra["id"]
    e2 = pd.concat([e, extra], ignore_index=True)
    v, m = prune.score(e2, nodes, {**CFG, "protect_curated_corroborated": True}, seed=0, n_injected=200)
    curated = (e2["knowledge_level"] == "knowledge_assertion") & (e2["agent_type"] == "manual_agent")
    assert (v.loc[curated.to_numpy(), "verdict"] == "keep").all()
    assert v.loc[curated.to_numpy(), "reasons"].str.contains("protected").all()
    assert m["n_protected"] == int(curated.sum()) and m["settings"]["protect_curated_corroborated"]
    md = prune.report_markdown("toy", m)
    assert "curated + corroborated protected: yes" in md and "| source trust |" in md


def test_grouped_plausibility_scores_each_predicate_group_on_its_own():
    rows = [(f"G{g}", "biolink:gene_associated_with_condition", f"{'A' if g < 20 else 'B'}{d}")
            for g in range(40) for d in range(5)]
    rows += [(f"G{g}", "biolink:has_phenotype", f"P{(g + d) % 7}") for g in range(40) for d in range(3)]
    rows += [("G1", "biolink:capable_of", "M1"), ("G2", "biolink:capable_of", "M1")]      # too small for an SVD
    e = pd.DataFrame(rows, columns=["subject", "predicate", "object"])
    inj = pd.DataFrame([("G0", "biolink:gene_associated_with_condition", "B1")], columns=e.columns)
    allx = pd.concat([e, inj], ignore_index=True)
    is_inj = np.array([False] * len(e) + [True])
    p, nc = prune.plausibility_grouped(allx, is_inj, {}, k=4)
    gd = (allx["predicate"] == "biolink:gene_associated_with_condition").to_numpy() & ~is_inj
    assert p[gd].mean() > 0.8 and p[-1] < 0.2
    small = (allx["predicate"] == "biolink:capable_of").to_numpy()
    assert (p[small] == 0).all() and (nc[small] == 1).all()
    v, m = prune.score(toy()[0], toy()[1], {**CFG, "plausibility_by": "predicate_group", "svd_k": 8},
                       seed=0, n_injected=200)
    assert m["settings"]["plausibility_by"] == "predicate_group" and len(v) == len(toy()[0])


def test_prune_compare_variants_and_table():
    import sys
    sys.path.insert(0, "pipelines")
    import prune_compare
    vs = prune_compare.variants({**CFG, "trust_weight": 0.0, "version": "v1.1"})
    assert [l for l, _ in vs][0].startswith("v1") and "trust_weight" not in vs[0][1] and "version" not in vs[0][1]
    assert vs[-1][1]["plausibility_by"] == "predicate_group" and vs[1][1]["trust_weight"] == 0.0
    e, nodes = toy()
    rows = prune_compare.compare(e, nodes, CFG, seed=1, n_injected=200, log=lambda m: None)
    assert len(rows) == 5 and all(0 <= r["auprc_errors"] <= 1 for r in rows)
    md = prune_compare.markdown("toy", rows, 1)
    assert "Highest combined AUPRC" in md and md.count("\n| ") == 6


MOCK_RULE = [{"source": "mock", "predicate": "biolink:physically_interacts_with", "self_pair": True,
              "reason": "pharos_placeholder_self_interaction"}]


def test_hard_prune_rule_prunes_pharos_mock_self_pairs_whatever_the_confidence():
    """Owner decision 6 (9 Nov): Pharos PPI rows with PPI Data Source = mock (placeholder self-pairs, no score) -> prune."""
    e, nodes = toy()
    prots = [f"UniProtKB:P{i:05d}" for i in range(6)]
    ppi = pd.DataFrame([dict(id=f"m{i}", subject=p, predicate="biolink:physically_interacts_with", object=p, source="mock",
                             primary_knowledge_source="infores:string", knowledge_level="knowledge_assertion",
                             agent_type="manual_agent", n_records="1") for i, p in enumerate(prots)] +
                       [dict(id="real", subject=prots[0], predicate="biolink:physically_interacts_with", object=prots[1],
                             source="mock", primary_knowledge_source="infores:string", knowledge_level="knowledge_assertion",
                             agent_type="manual_agent", n_records="1"),        # mock but not a self-pair: no rule applies
                        dict(id="selfstring", subject=prots[2], predicate="biolink:physically_interacts_with", object=prots[2],
                             source="STRINGDB", primary_knowledge_source="infores:string", knowledge_level="knowledge_assertion",
                             agent_type="manual_agent", n_records="1")])        # a self-pair from another source: no rule
    e2 = pd.concat([e, ppi], ignore_index=True)
    nodes2 = pd.concat([nodes, pd.DataFrame({"id": prots, "category": "biolink:Protein"})], ignore_index=True)
    cfg = {**CFG, "hard_prune": MOCK_RULE, "protect_curated_corroborated": True}
    v, m = prune.score(e2, nodes2, cfg, seed=0, n_injected=200)
    v = v.set_index("id")
    mocks = [f"m{i}" for i in range(6)]
    assert (v.loc[mocks, "verdict"] == "prune").all()
    assert v.loc[mocks, "reasons"].str.contains("pharos_placeholder_self_interaction").all()
    assert not v.loc[["real", "selfstring"], "reasons"].str.contains("pharos_placeholder").any()
    assert m["n_hard_pruned"] == 6
    mask, reasons = prune.hard_prune_mask(e2, MOCK_RULE)
    assert int(mask.sum()) == 6 and prune.hard_prune_mask(e2, None)[0].sum() == 0
    # without the rule the verdicts of the same rows follow the confidence alone
    v0, m0 = prune.score(e2, nodes2, {**CFG, "protect_curated_corroborated": True}, seed=0, n_injected=200)
    assert m0["n_hard_pruned"] == 0


def test_the_pharos_card_declares_the_mock_rule():
    import yaml
    from pathlib import Path
    card = yaml.safe_load((Path(__file__).resolve().parents[1] / "sources" / "pharos.yaml").read_text())
    assert card["prune"]["hard_prune"] == MOCK_RULE
