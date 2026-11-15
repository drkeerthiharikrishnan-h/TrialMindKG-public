"""
Generate a spot-check sheet for the owner: 100 pruned + 100 kept edges, stratified by predicate.

    python pipelines/make_spotcheck.py [edge_verdicts.tsv.gz] [-o data/curated/spotcheck.xlsx]

Default input: data/kg/pharos/edge_verdicts.tsv.gz, the file prune_kg.py writes for the Pharos layer.
That file is git-ignored (1.65 M rows), so this script runs on the machine that ran the pruner (the Mac);
only the sheet it writes (data/curated/spotcheck.xlsx, 200 rows) is committed.

The sheet has columns:
  edge_id, subject, predicate, object, source, confidence, verdict, reasons,
  owner_agrees (blank), owner_notes (blank)

The owner fills in owner_agrees (Y/N) and owner_notes, then the system scores precision/recall
of the pruning.

Functions
---------
load_verdicts(path) -> DataFrame
sample_for_review(edges, n_prune=100, n_keep=100, seed=42) -> DataFrame
write_xlsx(df, path)
score_review(path) -> dict
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VERDICTS = ROOT / "data" / "kg" / "pharos" / "edge_verdicts.tsv.gz"   # written by prune_kg.py
DEFAULT_SHEET = ROOT / "data" / "curated" / "spotcheck.xlsx"


def load_verdicts(path=DEFAULT_VERDICTS):
    """Read a prune_kg.py verdict file (gzipped TSV, one row per edge).

    Parameters
    ----------
    path : str or Path   edge_verdicts.tsv.gz as written by prune_kg.py (data/kg/<layer>/).

    Returns
    -------
    DataFrame with every column as text except confidence (float). Raises FileNotFoundError with the
    command that produces the file when it is missing (it is git-ignored: run on the machine that pruned).
    """
    import gzip
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. prune_kg.py writes it on the machine that built the layer (it is git-ignored): "
            "run `python3 pipelines/prune_kg.py sources/pharos.yaml` there, then this script, and commit the sheet.")
    with gzip.open(path, "rt") as f:
        edges = pd.read_csv(f, sep="\t", dtype=str, keep_default_na=False)
    edges["confidence"] = pd.to_numeric(edges["confidence"], errors="coerce")
    return edges


def sample_for_review(edges, n_prune=100, n_keep=100, seed=42):
    """Stratified sample of edges for owner review.

    Parameters
    ----------
    edges : DataFrame   Must have columns: id, subject, predicate, object, source, confidence,
                        verdict ('keep' or 'prune'), reasons.
    n_prune, n_keep : int   Target counts (fewer if the graph has fewer edges of that verdict).
    seed : int

    Returns
    -------
    DataFrame with review columns added.
    """
    rng = np.random.RandomState(seed)
    samples = []
    for verdict, n_target in [("prune", n_prune), ("keep", n_keep)]:
        pool = edges[edges["verdict"] == verdict]
        if len(pool) == 0:
            continue
        # Stratify by predicate: proportional to count, at least 1 per predicate if possible
        preds = pool["predicate"].value_counts()
        n_actual = min(n_target, len(pool))
        alloc = (preds / preds.sum() * n_actual).round().astype(int).clip(lower=1)
        # Adjust to hit n_actual exactly
        while alloc.sum() > n_actual:
            biggest = alloc.idxmax()
            alloc[biggest] -= 1
        while alloc.sum() < n_actual and (alloc < preds).any():
            room = preds - alloc
            biggest = room.idxmax()
            alloc[biggest] += 1
        for pred, count in alloc.items():
            pred_pool = pool[pool["predicate"] == pred]
            take = min(count, len(pred_pool))
            idx = rng.choice(pred_pool.index, size=take, replace=False)
            samples.append(pool.loc[idx])

    if not samples:
        return pd.DataFrame()
    out = pd.concat(samples).sort_values(["verdict", "predicate", "confidence"])
    # Add review columns
    out = out.copy()
    out["owner_agrees"] = ""
    out["owner_notes"] = ""
    # Keep only the columns the owner needs
    cols = ["id", "subject", "predicate", "object", "source", "confidence", "verdict",
            "reasons", "owner_agrees", "owner_notes"]
    return out[[c for c in cols if c in out.columns]]


def write_xlsx(df, path):
    """Write the spot-check sheet as an Excel file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="spot_check")
        ws = w.sheets["spot_check"]
        # Auto-width for readability
        for i, col in enumerate(df.columns, 1):
            max_len = max(len(str(col)), df[col].astype(str).str.len().max() if len(df) else 0)
            ws.column_dimensions[chr(64 + i) if i <= 26 else "A" + chr(64 + i - 26)].width = min(max_len + 2, 50)
    return path


def score_review(path):
    """Score the owner's filled-in spot-check sheet.

    Returns
    -------
    dict with keys: n_reviewed, prune_precision (owner agrees with prune decisions),
    keep_precision (owner agrees with keep decisions), notes on disagreements.
    """
    df = pd.read_excel(path, sheet_name="spot_check", dtype=str)
    df["owner_agrees"] = df["owner_agrees"].str.strip().str.upper()
    reviewed = df[df["owner_agrees"].isin(["Y", "N"])]
    if len(reviewed) == 0:
        return {"n_reviewed": 0, "message": "No rows reviewed yet."}
    pruned = reviewed[reviewed["verdict"] == "prune"]
    kept = reviewed[reviewed["verdict"] == "keep"]
    prune_correct = (pruned["owner_agrees"] == "Y").sum() if len(pruned) else 0
    keep_correct = (kept["owner_agrees"] == "Y").sum() if len(kept) else 0
    disagree = reviewed[reviewed["owner_agrees"] == "N"]
    return {
        "n_reviewed": len(reviewed),
        "prune_precision": round(prune_correct / len(pruned), 3) if len(pruned) else None,
        "keep_precision": round(keep_correct / len(kept), 3) if len(kept) else None,
        "n_prune_reviewed": len(pruned),
        "n_keep_reviewed": len(kept),
        "disagreements": disagree[["id", "verdict", "reasons", "owner_notes"]].to_dict("records"),
    }


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    out = DEFAULT_SHEET
    if "-o" in args:
        i = args.index("-o")
        out = Path(args[i + 1])
        del args[i:i + 2]
    path = Path(args[0]) if args else DEFAULT_VERDICTS
    edges = load_verdicts(path)
    sample = sample_for_review(edges)
    write_xlsx(sample, out)
    n_prune = (sample["verdict"] == "prune").sum()
    n_keep = (sample["verdict"] == "keep").sum()
    print(f"Wrote {out}: {n_prune} pruned + {n_keep} kept edges for review")
