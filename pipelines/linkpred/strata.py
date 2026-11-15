"""
Degree strata for the link-prediction benchmark (docs/ENRICHMENT_DIAGNOSIS_PLAN.md, wave C):

  degree_bins(ref_keys, side)        node id -> degree bin ('1-5', '6-20', '21-100', '101-500', '>500'), where the degree is the
                                     node's number of REFERENCE (G0) test-eligible pairs: the same in every graph.
  matched_candidates(...)            C3: the test negative for a pair is drawn from candidates in the SAME degree bin
                                     as the true object, so popularity cannot separate positives from negatives.
  match_bins(ref_keys)               the bins used for that matching: powers of two (1, 2-3, 4-7, ... ). The four
                                     reporting bins were too coarse for it: on 2026-11-02 84% of the test pairs sat in
                                     '>100', where degrees run from 101 to thousands, and popularity still scored 0.61.
  stratified_aurocs(pos, neg, labels) C4: AUROC within each stratum (each test pair is scored against its own
                                     negative), so a loss on popular diseases cannot hide a gain on rare ones.

Pure python / numpy, unit-tested in the cloud; run.py uses them with --neg-match degree and --strata.
"""
from collections import Counter

import numpy as np

from linkpred.evaluate import auroc

BIN_EDGES = (5, 20, 100, 500)            # upper bounds of the reporting bins; above the last one: '>500'


def bin_label(degree, edges=BIN_EDGES):
    """Takes a degree (>= 1). Returns its bin label: '1-5', '6-20', '21-100', '101-500' or '>500' for the default edges."""
    low = 1
    for hi in edges:
        if degree <= hi:
            return f"{low}-{hi}"
        low = hi + 1
    return f">{edges[-1]}"


def degree_bins(ref_keys, side=1, edges=BIN_EDGES):
    """Takes the reference pair keys ('subject<TAB>object') and a side (0 = subject / gene, 1 = object / disease).
    Returns {node id: bin label} from each node's number of reference pairs."""
    deg = Counter(k.split("\t")[side] for k in ref_keys)
    return {n: bin_label(d, edges) for n, d in deg.items()}


def match_bins(ref_keys, side=1):
    """Takes the reference pair keys and a side. Returns {node id: 'log2_<k>'} with k = floor(log2(degree)):
    1 / 2-3 / 4-7 / 8-15 / ... reference pairs, so a negative's popularity is within a factor of two of the true
    object's."""
    deg = Counter(k.split("\t")[side] for k in ref_keys)
    return {n: f"log2_{int(np.floor(np.log2(d)))}" for n, d in deg.items()}


def matched_candidates(candidates, bin_of, min_size=2):
    """Takes the sorted candidate object ids and {object id: bin}. Returns a function obj -> the candidates to draw
    its negative from: those in obj's bin, or all candidates when that bin holds fewer than min_size (a bin of one
    can only offer the true object itself)."""
    by_bin = {}
    for c in candidates:
        by_bin.setdefault(bin_of.get(c), []).append(c)

    def pick(obj):
        pool = by_bin.get(bin_of.get(obj), [])
        return pool if len(pool) >= min_size else candidates
    return pick


def stratified_aurocs(pos_scores, neg_scores, labels, prefix="auroc", min_pairs=5):
    """Takes the test pairs' scores, their negatives' scores (same order, one negative per pair) and one stratum
    label per test pair. Returns {'<prefix>_<label>': AUROC within the stratum, '<prefix>_<label>_n': pairs} for
    every stratum with at least min_pairs pairs (fewer would give a meaningless AUROC; their count is still
    reported)."""
    pos, neg, labels = np.asarray(pos_scores, float), np.asarray(neg_scores, float), np.asarray(labels, dtype=object)
    out = {}
    for lab in sorted(set(labels), key=str):
        m = labels == lab
        n = int(m.sum())
        out[f"{prefix}_{lab}_n"] = n
        if n >= min_pairs:
            out[f"{prefix}_{lab}"] = auroc(np.r_[np.ones(n), np.zeros(n)], np.r_[pos[m], neg[m]])
    return out
