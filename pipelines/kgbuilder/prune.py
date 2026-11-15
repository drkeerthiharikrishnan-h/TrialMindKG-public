"""
Pruning v1: a keep/prune verdict, a confidence (0..1) and reasons for EVERY edge of a graph built by the engine.
Generic: what is source-specific (score columns, pair groups, generic terms) comes from the card's `prune:` section.

Signals per edge (each 0..1; documented in the verdict reasons):
  reliability     how the source asserted it (BioLink knowledge_level / agent_type: curated > statistical > text-mined)
  source_score    the source's own score (z-score, DisGeNET score, pActivity, TIGA rank ...) as a percentile within
                  its predicate x source group; 0.5 where the source gives none
  corroboration   number of OTHER primary sources asserting the same subject-object pair (pair groups let gene-disease
                  and gene-GWAS-trait edges corroborate each other)
  records         how many source rows were merged into the edge (repeated measurements)
Hard flags (reasons, lower the confidence): generic ontology root as object, implausible activity value, self-loop.
Hard prune rules (card `prune: hard_prune:`, owner decisions): rows matching a rule are pruned whatever their confidence
and whatever protection or exemption applies, with the rule's reason code (hard_prune_mask()).

  plausibility    does the edge fit the graph: leave-one-out similarity between the subject and the other subjects of
                  the same object, from a truncated SVD of the incidence matrix (see plausibility())
No labelled truth exists yet. Injected errors (copies of real edges with a degree-matched wrong object, keeping the
original's source and score) are the negatives; real edges the positives (positive-unlabelled learning). Only
plausibility and corroboration are learned from them; source trust is added as a fixed prior. The threshold is the
one with the best F1 on the held-out half of the injected set. Out-of-fold GNN scores and Nemotron literature
checks are added in later versions as further signals.

score(edges, cfg, rng) -> edges with signal columns, confidence, verdict, reasons
evaluate(...) -> metrics dict; report_markdown(metrics) -> text

Pruning v1.1 (card `prune:` keys; without them the v1 behaviour is unchanged):
  trust_weight   share of the confidence given to source trust (v1: 0.2). Injected errors copy the original edge's
                 source and score, so trust ALONE carries no information about them (its AUPRC sits near the base
                 rate); combined with plausibility and corroboration it can still help or hurt the ranking (the toy
                 test graph: helps; Pharos v1 on 2026-10-30: 0.147 without trust vs 0.116 with 0.2). Whether it helps
                 on REAL errors only the human spot check can show. The metrics report the AUPRC at trust weight 0,
                 0.1 and 0.2 side by side.
  svd_k          dimensions of the plausibility SVD (v1: 32).
  plausibility_by  'all' (v1: one subject x object matrix for every predicate) or 'predicate_group' (one matrix per
                 predicate, pair groups joined): a gene's diseases are compared with other genes' diseases, not
                 mixed with its phenotypes and GWAS traits.
  protect_curated_corroborated  true: an edge asserted by a curated source (knowledge_assertion by a manual agent)
                 AND by at least one other primary source is never pruned (reason 'protected').
pipelines/prune_compare.py scores the variants on a SEPARATE injected set (seed 1), so the card's choice is not
made on the injected errors the final metrics are measured on (seed 0).
"""
import numpy as np
import pandas as pd

RELIABILITY = {("knowledge_assertion", "manual_agent"): 0.9, "knowledge_assertion": 0.8,
               "statistical_association": 0.6, "observation": 0.6, "text_co_occurrence": 0.4,
               "prediction": 0.4, "not_provided": 0.5}
FEATURES = ["plausibility", "no_context", "corroboration", "reliability", "source_score", "records", "flagged"]
LEARNED = ["plausibility", "corroboration"]      # signals that can separate real edges from injected errors


def reliability(edges):
    kl, ag = edges["knowledge_level"].fillna("not_provided"), edges["agent_type"].fillna("not_provided")
    base = kl.map(lambda k: RELIABILITY.get(k, 0.5))
    curated = (kl == "knowledge_assertion") & (ag == "manual_agent")
    return base.where(~curated, RELIABILITY[("knowledge_assertion", "manual_agent")])


def source_score(edges, score_columns):
    """Percentile of the first available score column within each predicate x source group (0.5 if none)."""
    out = pd.Series(np.nan, index=edges.index)
    for col, direction in score_columns.items():
        if col not in edges:
            continue
        v = pd.to_numeric(edges[col], errors="coerce")
        if direction == "abs":
            v = v.abs()
        elif direction == "lower":
            v = -v
        todo = out.isna() & v.notna()
        if todo.any():
            grp = edges.loc[todo, "predicate"] + "|" + edges.loc[todo, "source"]
            out[todo] = v[todo].groupby(grp).rank(pct=True)
    return out.fillna(0.5)


def pair_keys(edges, pair_groups):
    """subject|group|object, where group joins predicates that state the same kind of relation."""
    group_of = {p: g for g, preds in (pair_groups or {}).items() for p in preds}
    grp = edges["predicate"].map(lambda p: group_of.get(p, p))
    return edges["subject"] + "|" + grp + "|" + edges["object"]


def corroboration(edges, pair_groups):
    """Distinct primary sources per pair minus this edge's own -> 0, 1/3, 2/3, 1 (3+ others)."""
    key = pair_keys(edges, pair_groups)
    n = pd.DataFrame({"k": key, "s": edges["primary_knowledge_source"]}).groupby("k")["s"].transform("nunique")
    return ((n - 1).clip(0, 3) / 3).astype(float), n


def flags(edges, cfg):
    """Hard consistency problems -> list of reason codes per edge."""
    reasons = pd.Series([[] for _ in range(len(edges))], index=edges.index)
    generic = set(cfg.get("generic_objects") or [])
    gen = edges["object"].isin(generic)
    lo, hi = cfg.get("activity_range", [2, 14])
    act = pd.to_numeric(edges.get("activity"), errors="coerce") if "activity" in edges else pd.Series(np.nan, index=edges.index)
    bad_act = act.notna() & ((act < lo) | (act > hi))
    loop = edges["subject"] == edges["object"]
    for mask, code in ((gen, "generic_object"), (bad_act, "implausible_activity"), (loop, "self_loop")):
        for i in edges.index[mask]:
            reasons[i] = reasons[i] + [code]
    return reasons


def hard_prune_mask(edges, rules):
    """Takes the edges and the card's hard_prune rules [{source, predicate, self_pair, reason}] (every key but reason
    optional; all given keys must hold). Returns (mask, reasons): mask True for an edge some rule matches, reasons = the
    matching rules' reason codes per edge (a list). No rule -> nothing matches."""
    mask = pd.Series(False, index=edges.index)
    reasons = pd.Series([[] for _ in range(len(edges))], index=edges.index)
    for rule in rules or []:
        m = pd.Series(True, index=edges.index)
        if "source" in rule:
            m &= edges["source"].astype(str) == str(rule["source"])
        if "predicate" in rule:
            m &= edges["predicate"].astype(str) == str(rule["predicate"])
        if rule.get("self_pair"):
            m &= edges["subject"] == edges["object"]
        for i in edges.index[m]:
            reasons[i] = reasons[i] + [str(rule["reason"])]
        mask |= m
    return mask, reasons


def features(edges, cfg):
    f = pd.DataFrame(index=edges.index)
    f["reliability"] = reliability(edges)
    f["source_score"] = source_score(edges, cfg.get("score_columns") or {})
    f["corroboration"], n_sources = corroboration(edges, cfg.get("pair_groups"))
    f["records"] = (np.log1p(pd.to_numeric(edges["n_records"], errors="coerce").fillna(1)) / np.log(11)).clip(0, 1)
    fl = flags(edges, cfg)
    f["flagged"] = fl.map(len).clip(0, 1).astype(float)
    return f, fl, n_sources


def inject_errors(edges, nodes, n, rng):
    """n type-preserving, degree-matched corruptions: a sampled real edge whose object is replaced by another object
    of the same predicate, drawn in proportion to how often it appears (so popular objects are as likely as in real
    data and popularity alone cannot expose the error), never an existing subject-predicate-object triple."""
    sample = edges.sample(n=min(n, len(edges)), random_state=int(rng.integers(1 << 31))).copy()
    existing = set(edges["subject"] + "|" + edges["predicate"] + "|" + edges["object"])
    new = pd.Series(index=sample.index, dtype=object)
    for pred, idx in sample.groupby("predicate").groups.items():
        deg = edges.loc[edges["predicate"] == pred, "object"].value_counts()
        objs, w = deg.index.to_numpy(), (deg.to_numpy() / deg.sum())
        cand = rng.choice(objs, size=len(idx), p=w)
        for _ in range(10):                      # resample the few that hit a real triple or the original object
            bad = [(s_ + "|" + pred + "|" + c in existing) or c == o
                   for s_, c, o in zip(sample.loc[idx, "subject"], cand, sample.loc[idx, "object"])]
            if not any(bad):
                break
            cand = np.where(bad, rng.choice(objs, size=len(idx), p=w), cand)
        new[idx] = cand
    sample["object"] = new.astype(str)
    keep = ~(sample["subject"] + "|" + sample["predicate"] + "|" + sample["object"]).isin(existing)
    sample = sample[keep]
    sample["id"] = "inj" + pd.Series(range(len(sample)), index=sample.index).astype(str)
    sample["injected"] = True
    return sample


def plausibility(allx, is_inj, k=32, seed=0):
    """Does the edge fit the graph? Each subject is embedded by its other neighbours (truncated SVD of the subject x
    object incidence matrix built from REAL edges, objects weighted 1/sqrt(degree)); the edge s->o scores the cosine
    between s and the average of the other subjects linked to o. The edge's own link is removed from both sides
    (leave-one-out), so a real edge gains nothing from being in the matrix.
    Returns (plausibility 0..1 = cosine clipped at 0, no_context 0/1 = 1 when the subject or object has no other link)."""
    from scipy.sparse import csr_matrix
    from scipy.sparse.linalg import svds
    real = allx.loc[~is_inj, ["subject", "object"]].drop_duplicates()
    subs = pd.Index(real["subject"].unique())
    objs = pd.Index(pd.unique(pd.concat([real["object"], allx.loc[is_inj, "object"]])))
    r, c = subs.get_indexer(real["subject"]), objs.get_indexer(real["object"])
    col_deg = np.bincount(c, minlength=len(objs)).astype(float)
    cw = 1 / np.sqrt(np.maximum(col_deg, 1))
    F = csr_matrix((cw[c].astype(np.float32), (r, c)), shape=(len(subs), len(objs)))
    kk = min(k, min(F.shape) - 1)
    _, _, vt = svds(F, k=kk, random_state=seed)
    V = vt.T                                     # objects x k
    U = F @ V                                    # subjects x k
    S = F.T.tocsr().astype(bool).astype(np.float32) @ U          # per object: sum of its subjects' vectors
    si, oi = subs.get_indexer(allx["subject"]), objs.get_indexer(allx["object"])
    out, no_ctx = np.zeros(len(allx)), np.ones(len(allx))
    ok = (si >= 0) & (oi >= 0)
    s_i, o_i, inj = si[ok], oi[ok], is_inj[ok]
    own = cw[o_i, None] * V[o_i]                 # contribution of feature o to any subject vector
    a = U[s_i] - np.where(inj[:, None], 0, own)  # subject without the edge's own object
    n_other = col_deg[o_i] - np.where(inj, 0, 1)
    c_sum = S[o_i] - np.where(inj[:, None], 0, U[s_i])
    cvec = c_sum / np.maximum(n_other, 1)[:, None] - own
    num = (a * cvec).sum(1)
    den = np.linalg.norm(a, axis=1) * np.linalg.norm(cvec, axis=1)
    row_deg = np.diff(F.indptr)                  # objects per subject
    s_other = row_deg[s_i] - np.where(inj, 0, 1)
    has = (s_other > 0) & (n_other > 0) & (den > 1e-12)   # counted, not inferred from floating-point norms
    out[ok] = np.where(has, np.clip(num / np.maximum(den, 1e-12), 0, 1), 0.0)
    no_ctx[ok] = (~has).astype(float)
    return out, no_ctx


def fit_logistic(X, y, l2=1.0, iters=800, lr=1.0):
    """L2 logistic regression by gradient descent (numpy only), classes weighted equally. Returns (weights, bias)."""
    w, b = np.zeros(X.shape[1]), 0.0
    sw = np.where(y == 1, 0.5 / max(y.sum(), 1), 0.5 / max((1 - y).sum(), 1))
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ w + b)))
        g = (p - y) * sw
        w -= lr * (X.T @ g + l2 * w / len(y))
        b -= lr * g.sum()
    return w, b


def predict(X, w, b):
    return 1 / (1 + np.exp(-(X @ w + b)))


def pr_metrics(y_true, score, threshold):
    """Precision/recall/F1 of 'prune' (= score below threshold) for the error class (y_true == 0)."""
    pruned = score < threshold
    err = y_true == 0
    tp, fp, fn = (pruned & err).sum(), (pruned & ~err).sum(), (~pruned & err).sum()
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return prec, rec, (2 * prec * rec / (prec + rec) if prec + rec else 0.0)


def auprc(y_err, s):
    """Area under the precision-recall curve for detecting errors (higher score = more likely an error)."""
    order = np.argsort(-s)
    y = y_err[order]
    tp = np.cumsum(y)
    prec = tp / np.arange(1, len(y) + 1)
    return float((prec * y).sum() / max(y.sum(), 1))


def plausibility_grouped(allx, is_inj, pair_groups, k=32, seed=0, min_size=3):
    """plausibility() run separately per predicate group (pair_groups join predicates; other predicates are their
    own group). Returns (plausibility, no_context) aligned with allx. A group whose matrix is too small for an SVD
    (fewer than min_size subjects or objects) gets plausibility 0 and no_context 1: it cannot be judged."""
    group_of = {p: g for g, preds in (pair_groups or {}).items() for p in preds}
    grp = allx["predicate"].map(lambda p: group_of.get(p, p)).to_numpy()
    out, no_ctx = np.zeros(len(allx)), np.ones(len(allx))
    for g in sorted(set(grp)):
        m = grp == g
        sub = allx.loc[m, ["subject", "object"]].reset_index(drop=True)
        real = sub.loc[~is_inj[m]]
        if real["subject"].nunique() < min_size or real["object"].nunique() < min_size:
            continue
        p, nc = plausibility(sub, is_inj[m], k=k, seed=seed)
        out[m], no_ctx[m] = p, nc
    return out, no_ctx


def score(edges, nodes, cfg, seed=0, n_injected=None):
    """Returns (verdicts DataFrame, metrics dict). Every input edge gets exactly one verdict row.
    confidence = 0.8 * learned plausibility (logistic regression: real edges vs injected errors, trained on half of
    the injected set) + 0.2 * source trust (mean of reliability and the source's own score; not learned), halved
    when a hard flag fires. The prune threshold is the best-F1 cut on the other half of the injected set."""
    rng = np.random.default_rng(seed)
    n_inj = n_injected or min(50_000, max(1_000, len(edges) // 30))
    inj = inject_errors(edges, nodes, n_inj, rng)
    allx = pd.concat([edges.assign(injected=False), inj], ignore_index=True)
    is_inj = allx["injected"].to_numpy(bool)
    f, fl, n_sources = features(allx, cfg)
    k = int(cfg.get("svd_k", 32))
    if cfg.get("plausibility_by", "all") == "predicate_group":
        f["plausibility"], f["no_context"] = plausibility_grouped(allx, is_inj, cfg.get("pair_groups"), k=k, seed=seed)
    else:
        f["plausibility"], f["no_context"] = plausibility(allx, is_inj, k=k, seed=seed)
    X = f[LEARNED].to_numpy(float)
    ctx = f["no_context"].to_numpy() == 0
    half = rng.random(len(allx)) < 0.5
    w, b = fit_logistic(X[half & ctx], (~is_inj[half & ctx]).astype(float))
    # an edge with no other link around it cannot be judged by plausibility: neutral 0.5, not "confident"
    learned = np.where(ctx, predict(X, w, b), 0.5)
    trust = (f["reliability"].to_numpy() + f["source_score"].to_numpy()) / 2
    tw = float(cfg.get("trust_weight", 0.2))
    flag = (1 - 0.5 * f["flagged"].to_numpy())
    combine = lambda t: ((1 - t) * learned + t * trust) * flag
    conf = combine(tw)
    ev = ~half
    y = (~is_inj[ev]).astype(int)
    grid = np.quantile(conf[ev], np.linspace(0.005, 0.5, 100))
    best = max(grid, key=lambda t: pr_metrics(y, conf[ev], t)[2])
    prec, rec, f1 = pr_metrics(y, conf[ev], best)
    real = ~is_inj
    metrics = {"n_edges": int(len(edges)), "n_injected": int(is_inj.sum()), "threshold": float(best),
               "precision": float(prec), "recall": float(rec), "f1": float(f1),
               "auprc_errors": auprc(1 - y, -conf[ev]), "auprc_plausibility_only": auprc(1 - y, -learned[ev]),
               "auprc_learned": auprc(1 - y, -learned[ev]),
               "auprc_plausibility": auprc(1 - y, -f["plausibility"].to_numpy()[ev]),
               "auprc_corroboration": auprc(1 - y, -f["corroboration"].to_numpy()[ev]),
               "auprc_trust": auprc(1 - y, -trust[ev]),
               "auprc_by_trust_weight": {str(t): auprc(1 - y, -combine(t)[ev]) for t in (0.0, 0.1, 0.2)},
               "settings": {"version": str(cfg.get("version", "v1")), "trust_weight": tw, "svd_k": k, "plausibility_by": cfg.get("plausibility_by", "all"),
                            "protect_curated_corroborated": bool(cfg.get("protect_curated_corroborated", False))},
               "error_base_rate": float((1 - y).mean()),
               "weights": dict(zip(LEARNED, map(float, w))),
               "pruned_real_fraction": None}
    v = edges[["id", "subject", "predicate", "object", "source", "primary_knowledge_source"]].copy()
    for c in FEATURES:
        v[c] = f.loc[real, c].round(3).to_numpy()
    v["n_sources"] = n_sources[real].to_numpy()
    v["confidence"] = conf[real].round(4)
    exempt = v["predicate"].isin(cfg.get("never_prune_predicates") or []).to_numpy()   # identity mappings
    protected = np.zeros(len(v), dtype=bool)
    if cfg.get("protect_curated_corroborated"):
        protected = (f.loc[real, "reliability"].to_numpy() >= RELIABILITY[("knowledge_assertion", "manual_agent")]) \
            & (v["n_sources"].to_numpy() >= 2)
    hard, hard_reasons = hard_prune_mask(edges, cfg.get("hard_prune"))
    hard = hard.to_numpy()
    v["verdict"] = np.where(((conf[real] < best) & ~exempt & ~protected) | hard, "prune", "keep")
    v["reasons"] = [reason_text(r, fl_i + hr + (["protected"] if pr and not h else []))
                    for r, fl_i, pr, hr, h in zip(v.itertuples(), fl[real], protected, hard_reasons, hard)]
    metrics["n_hard_pruned"] = int(hard.sum())
    metrics["n_protected"] = int(protected.sum())
    metrics["n_protected_below_threshold"] = int((protected & (conf[real] < best) & ~exempt).sum())
    metrics["pruned_real_fraction"] = float((v["verdict"] == "prune").mean())
    caught = pd.DataFrame({"predicate": allx.loc[is_inj, "predicate"].to_numpy(), "pruned": conf[is_inj] < best})
    metrics["recall_by_predicate"] = caught.groupby("predicate")["pruned"].mean().round(3).to_dict()
    metrics["pruned_by_predicate"] = v.groupby("predicate")["verdict"].apply(lambda s: (s == "prune").mean()).round(3).to_dict()
    return v, metrics


def reason_text(r, hard):
    """Short, computed reason codes (no prewritten conclusions)."""
    parts = [f"plausibility={r.plausibility}", f"sources={r.n_sources}", f"reliability={r.reliability}",
             f"source_score={r.source_score}"]
    return ";".join(parts + list(hard))


def report_markdown(name, m):
    lines = [f"# Pruning {m.get('settings', {}).get('version', 'v1')} - {name}", "",
             f"{m['n_edges']:,} edges scored; {m['n_injected']:,} injected type-preserving errors used for "
             f"training (half) and evaluation (other half).", "",
             f"- threshold (best F1 on held-out injected set): {m['threshold']:.4f}",
             f"- precision of 'prune' on the held-out set: {m['precision']:.3f}; recall of injected errors: "
             f"{m['recall']:.3f}; F1: {m['f1']:.3f}",
             f"- AUPRC for finding injected errors: {m['auprc_errors']:.3f} (learned signals = plausibility + "
             f"corroboration: {m.get('auprc_learned', m['auprc_plausibility_only']):.3f}; random = base rate "
             f"{m['error_base_rate']:.3f})",
             f"- share of real {name} edges with verdict 'prune': {m['pruned_real_fraction']:.3f}", "",
             "Learned weights (logistic regression, real vs injected): "
             + ", ".join(f"{k} {v:+.2f}" for k, v in m["weights"].items()), ""]
    if "auprc_trust" in m:
        st = m["settings"]
        lines += [f"Settings: trust weight {st['trust_weight']:g}, SVD dimensions {st['svd_k']}, plausibility "
                  f"by {st['plausibility_by']}, curated + corroborated protected: "
                  f"{'yes' if st['protect_curated_corroborated'] else 'no'}"
                  + (f" ({m['n_protected']:,} edges; {m['n_protected_below_threshold']:,} of them would otherwise be "
                     "pruned)" if st["protect_curated_corroborated"] else ""), "",
                  "AUPRC of each signal alone on the held-out injected errors (trust is expected near the base rate: "
                  "injected errors keep the original edge's source and score, so this test cannot see it):", "",
                  "| signal | AUPRC |", "|---|---|",
                  f"| plausibility | {m['auprc_plausibility']:.3f} |",
                  f"| corroboration | {m['auprc_corroboration']:.3f} |",
                  f"| source trust | {m['auprc_trust']:.3f} |",
                  f"| learned (plausibility + corroboration) | {m['auprc_learned']:.3f} |"]
        lines += [f"| combined, trust weight {t} | {v:.3f} |" for t, v in m["auprc_by_trust_weight"].items()]
        lines += [""]
    lines += [
             "## By predicate", "", "| predicate | injected errors caught | real edges pruned |", "|---|---|---|"]
    for p in sorted(m["pruned_by_predicate"]):
        lines.append(f"| {p} | {m['recall_by_predicate'].get(p, float('nan')):.3f} | {m['pruned_by_predicate'][p]:.3f} |")
    lines += ["", "Caveats: injected errors are degree-matched and keep the original edge's source and score, so only "
              f"plausibility and corroboration can expose them; real {name} edges include unknown errors (they are "
              "treated as 'real' when learning). v2 adds out-of-fold GNN scores, newer-release corroboration and "
              "literature checks. The organizers' gold set will give the real accuracy."]
    return "\n".join(lines) + "\n"
