"""Blinded spot-check sheet comparing the shipped pruner (v1) with the candidate (v2) on the edges where they DISAGREE.

    .venv/bin/python scripts/make_spotcheck_v1_vs_v2.py [pharos] [--n 100] [--seed 42]

Reads  data/kg/<layer>/edge_verdicts.tsv.gz (v1) and edge_verdicts_v2.tsv.gz (v2, from scripts/prune_v2_candidate.py).
Writes outputs/<layer>/spotcheck_v1_vs_v2.xlsx      the sheet the owner fills: NO verdicts shown (blinded); columns
                                                      edge_is_correct (Y / N / ?) and notes
       outputs/<layer>/spotcheck_v1_vs_v2_key.tsv    row id -> v1 verdict, v2 verdict, confidences (do not open while judging)
Sample: n/2 edges v1 keeps and v2 prunes, n/2 edges v1 prunes and v2 keeps, stratified by predicate like make_spotcheck,
TIN-X text-mining rows excluded (they are judged separately). Score afterwards with --score:
    .venv/bin/python scripts/make_spotcheck_v1_vs_v2.py pharos --score
which joins the filled sheet to the key and reports, per pruner, how often its verdict matched the owner's judgement.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
from make_spotcheck import write_xlsx  # noqa: E402

COLS = ["id", "subject", "predicate", "object", "source", "verdict", "confidence", "reasons"]


def load(layer, suffix):
    p = ROOT / "data" / "kg" / layer / f"edge_verdicts{suffix}.tsv.gz"
    df = pd.read_csv(p, sep="\t", dtype=str, keep_default_na=False)
    keep = [c for c in COLS if c in df.columns]
    return df[keep].set_index("id")


def disagreements(v1, v2):
    both = v1.join(v2[["verdict", "confidence"]], rsuffix="_v2", how="inner")
    both = both[both["verdict"] != both["verdict_v2"]]
    if "source" in both.columns:
        both = both[~both["source"].str.contains("tinx|text mining|jensenlab text", case=False, regex=True)]
    return both


def node_names(layer):
    """{node id: name} from the layer's nodes file (symbol for genes when present)."""
    p = ROOT / "data" / "kg" / layer / "nodes.tsv.gz"
    df = pd.read_csv(p, sep="\t", dtype=str, keep_default_na=False)
    name = df["name"] if "name" in df.columns else pd.Series("", index=df.index)
    if "symbol" in df.columns:
        name = df["symbol"].where(df["symbol"] != "", name)
    return dict(zip(df["id"], name))


def stratified(pool, n, rng):
    if len(pool) <= n:
        return pool
    preds = pool["predicate"].value_counts()
    alloc = (preds / preds.sum() * n).round().astype(int).clip(lower=1)
    while alloc.sum() > n:
        alloc[alloc.idxmax()] -= 1
    parts = [pool[pool["predicate"] == p].sample(min(k, (pool["predicate"] == p).sum()), random_state=rng) for p, k in alloc.items()]
    return pd.concat(parts)


def build(layer, n, seed):
    v1, v2 = load(layer, ""), load(layer, "_v2")
    d = disagreements(v1, v2)
    a = d[(d["verdict"] == "keep") & (d["verdict_v2"] == "prune")]
    b = d[(d["verdict"] == "prune") & (d["verdict_v2"] == "keep")]
    rng = np.random.RandomState(seed)
    s = pd.concat([stratified(a, n // 2, rng), stratified(b, n - n // 2, rng)])
    s = s.sample(frac=1, random_state=seed)                      # shuffle so the two groups are not blocks
    key = s[["verdict", "confidence", "verdict_v2", "confidence_v2"]].rename(columns={"verdict": "v1_verdict", "confidence": "v1_confidence",
                                                                                       "verdict_v2": "v2_verdict", "confidence_v2": "v2_confidence"})
    names = node_names(layer)
    sheet = s[[c for c in ["subject", "predicate", "object", "source"] if c in s.columns]].copy()
    sheet.insert(1, "subject_name", sheet["subject"].map(names).fillna(""))
    sheet.insert(4, "object_name", sheet["object"].map(names).fillna(""))
    sheet["predicate"] = sheet["predicate"].str.replace("biolink:", "", regex=False)
    key = key.join(s[["reasons"]]) if "reasons" in s.columns else key          # the pruner's reasons stay out of the blinded sheet
    sheet.insert(0, "row", range(1, len(sheet) + 1))
    sheet["edge_is_correct"] = ""
    sheet["notes"] = ""
    sheet = sheet.reset_index().rename(columns={"id": "edge_id"})
    out = ROOT / "outputs" / layer
    out.mkdir(parents=True, exist_ok=True)
    write_xlsx(sheet, out / "spotcheck_v1_vs_v2.xlsx")
    key.reset_index().rename(columns={"id": "edge_id"}).to_csv(out / "spotcheck_v1_vs_v2_key.tsv", sep="\t", index=False)
    print(f"{layer}: v1/v2 disagree on {len(d):,} non-text-mining edges (v1 keep & v2 prune {len(a):,}; v1 prune & v2 keep {len(b):,}); "
          f"sheet {len(sheet)} rows -> {out / 'spotcheck_v1_vs_v2.xlsx'}; key -> spotcheck_v1_vs_v2_key.tsv")


def score(layer):
    out = ROOT / "outputs" / layer
    sheet = pd.read_excel(out / "spotcheck_v1_vs_v2.xlsx", sheet_name="spot_check", dtype=str)
    key = pd.read_csv(out / "spotcheck_v1_vs_v2_key.tsv", sep="\t", dtype=str)
    df = sheet.merge(key, on="edge_id")
    df["j"] = df["edge_is_correct"].fillna("").str.strip().str.upper()
    judged = df[df["j"].isin(["Y", "N"])]
    lines = [f"# Spot check v1 vs v2 - {layer}", "", f"Rows judged: {len(judged)} of {len(df)} (Y = the edge is a correct claim, N = wrong).", ""]
    for tag in ("v1", "v2"):
        want = np.where(judged["j"] == "Y", "keep", "prune")
        agree = (judged[f"{tag}_verdict"].values == want)
        lines.append(f"- **{tag}** verdict matches the owner's judgement on **{agree.sum()} of {len(judged)}** rows ({agree.mean():.1%}).")
    n_wrong = (judged["j"] == "N").sum()
    lines += ["", f"Of the judged edges, {n_wrong} are wrong claims (owner N) and {len(judged) - n_wrong} correct (owner Y).",
              "On every row exactly one pruner is right (they disagree by construction), so the two percentages sum to 100%."]
    (out / "spotcheck_v1_vs_v2_result.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    args = sys.argv[1:]
    layer = next((a for a in args if not a.startswith("--") and not a.isdigit()), "pharos")
    if "--score" in args:
        score(layer)
    else:
        n = int(args[args.index("--n") + 1]) if "--n" in args else 100
        seed = int(args[args.index("--seed") + 1]) if "--seed" in args else 42
        build(layer, n, seed)
