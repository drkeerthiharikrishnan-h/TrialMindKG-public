"""
The words a reader gets for a predicted link (packet PK6, review finding ES-13): how strong a model score is, and which
benchmark number belongs to the model that made the predictions. Its own router, included by pipelines/app.py with one
line, so parallel branches do not meet in app.py. Read-only; every number is read from a committed file.

  GET /api/pharos/prediction_scale   the strength bands of the predictions file and the model's benchmark line:
      {n, cut: {strong, moderate}, bands: [{word, rule}], model, graph, benchmark: {mean, ci_low, ci_high, wins, n} | None,
       sentence, files}

Functions
  score_bands(scores)              -> {n, strong, moderate}: the score at the 90th and the 50th percentile of all scored rows
  band(score, cut)                 -> "strong" | "moderate" | "weak" (None when there is no score or no scale)
  benchmark_line(text, graph)      -> the meta-path row of one graph code in compare_cs9_metapath.md, or None
  prediction_scale(root)           -> the endpoint's answer (cached per root)
"""
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fastapi import APIRouter

ROOT = Path(__file__).resolve().parents[1]
SCORES = "outputs/linkpred/predictions_novelty.tsv.gz"          # one row per prediction of the shipped file, with its score
SETTINGS = "outputs/linkpred/predictions_metapath_settings.json"  # which graph and model wrote the predictions
COMPARE = "outputs/linkpred/compare_cs9_metapath.md"             # the meta-path model on the shipped graphs vs Pharos alone
STRONG_Q, MODERATE_Q = 0.90, 0.50
router = APIRouter()


def score_bands(scores):
    """Takes an iterable of model scores (numbers; blanks and text are skipped). Returns {n, strong, moderate}: the number of
    scores and the scores at the 90th (strong) and 50th (moderate) percentile, rounded to 2 places; None values when empty."""
    import numpy as np
    xs = []
    for s in scores:
        try:
            v = float(s)
        except (TypeError, ValueError):
            continue
        if v == v:
            xs.append(v)
    if not xs:
        return dict(n=0, strong=None, moderate=None)
    a = np.asarray(xs)
    return dict(n=len(xs), strong=round(float(np.quantile(a, STRONG_Q)), 2), moderate=round(float(np.quantile(a, MODERATE_Q)), 2))


def band(score, cut):
    """Takes a score and the cut {strong, moderate}. Returns 'strong' (at or above the 90th percentile), 'moderate' (at or
    above the median), 'weak' (below it), or None when the score or the cut is missing."""
    try:
        v = float(score)
    except (TypeError, ValueError):
        return None
    if not cut or cut.get("strong") is None or cut.get("moderate") is None:
        return None
    return "strong" if v >= cut["strong"] else "moderate" if v >= cut["moderate"] else "weak"


ROW = re.compile(r"^\|\s*(G\dR?)@(\S+)\s*\|\s*(\d+)\s*\|\s*([+-]\d+\.\d+)\s*\|\s*([+-]\d+\.\d+) to ([+-]\d+\.\d+)\s*\|\s*(\d+)\s*\|\s*(\w+)\s*\|", re.M)


def benchmark_line(text, graph):
    """Takes the text of compare_cs9_metapath.md and a graph code (e.g. 'G3'). Returns the row of the shipped-graph run of
    that graph (tag containing 'cs9'; else the first row of the graph) as {mean, ci_low, ci_high, n, wins, same_pairs},
    or None when the file has no row for it."""
    rows = [m for m in ROW.finditer(text or "") if m.group(1) == graph]
    rows.sort(key=lambda m: "cs9" not in m.group(2))
    if not rows:
        return None
    m = rows[0]
    return dict(mean=float(m.group(4)), ci_low=float(m.group(5)), ci_high=float(m.group(6)), n=int(m.group(3)),
                wins=int(m.group(7)), same_pairs=m.group(8) == "yes")


@lru_cache(maxsize=4)
def prediction_scale(root=ROOT):
    """Takes the repo root. Returns the strength bands of the predictions (score_bands over SCORES), the model and graph in
    words (SETTINGS; graph words from numbers_page.WORDS) and the model's benchmark line (benchmark_line over COMPARE),
    with one sentence built from what is there. A missing file leaves its part None and out of the sentence."""
    import pandas as pd
    import numbers_page
    root = Path(root)
    cut = dict(n=0, strong=None, moderate=None)
    try:
        cut = score_bands(pd.read_csv(root / SCORES, sep="\t", usecols=["score"])["score"])
    except (OSError, ValueError, KeyError):
        pass
    graph = model = None
    try:
        st = json.loads((root / SETTINGS).read_text())["settings"]
        graph, model = st.get("graph"), st.get("model")
    except (OSError, ValueError, KeyError):
        pass
    bench = None
    try:
        bench = benchmark_line((root / COMPARE).read_text(), graph) if graph else None
    except OSError:
        pass
    model_words = {"metapath": "the meta-path model (a model of the named paths between a gene and a disease)",
                   "gnn": "the R-GCN graph neural network"}.get(model, "the link-prediction model")
    graph_words = numbers_page.WORDS["graphs"].get(graph) if graph else None
    parts = [f"Predictions come from {model_words}" + (f", trained on {graph_words}" if graph_words else "") + "."]
    if bench:
        parts.append(f"On the held-out curated gene-disease pairs that model scores {bench['mean']:+.3f} AUROC over Pharos alone "
                     f"(95% CI {bench['ci_low']:+.3f} to {bench['ci_high']:+.3f}, {bench['wins']} of {bench['n']} folds higher).")
    if cut["n"]:
        parts.append(f"Strength of a prediction: strong = in the top tenth of the {cut['n']:,} scored predictions (score "
                     f"{cut['strong']:.2f} or more), moderate = in the top half ({cut['moderate']:.2f} or more), weak = the "
                     f"lower half. A score ranks pairs; it is not a probability.")
    bands = [dict(word="strong", rule=f"score {cut['strong']:.2f} or more (top tenth)"),
             dict(word="moderate", rule=f"score {cut['moderate']:.2f} or more (top half)"),
             dict(word="weak", rule="lower half")] if cut["n"] else []
    return dict(n=cut["n"], cut=dict(strong=cut["strong"], moderate=cut["moderate"]), bands=bands, model=model,
                model_words=model_words, graph=graph, graph_words=graph_words, benchmark=bench, sentence=" ".join(parts),
                files=[f for f in (SCORES, SETTINGS, COMPARE) if (root / f).exists()])


@router.get("/api/pharos/prediction_scale")
def api_prediction_scale():
    return prediction_scale(ROOT)
