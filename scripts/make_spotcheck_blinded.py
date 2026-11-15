"""Blinded 200-edge spot check of the shipped Pharos pruner verdicts (FIX_PLAN_2026-11-08 packet CS-4, finding M5).

    .venv/bin/python scripts/make_spotcheck_blinded.py [--layer pharos] [--n-prune 100] [--n-keep 100] [--seed 20261108]

Reads  data/kg/<layer>/edge_verdicts.tsv.gz   (prune_kg.py verdicts; streamed in chunks, never loaded whole)
       data/kg/<layer>/edges.tsv.gz           (knowledge level, the source's own statistic, references; streamed)
       data/kg/<layer>/nodes.tsv.gz           (human-readable names)
       sources/<layer>.yaml                   (prune.pair_groups: the predicate groups the pruner uses)
Writes outputs/<layer>/spotcheck_blind.xlsx       sheet 'spot_check' (NO verdict, confidence or reasons) + tab 'README'
       outputs/<layer>/spotcheck_blind_key.tsv    row -> verdict, confidence, reasons (closed: do not open while rating)

Sampling: a stratum is (verdict, predicate group); predicate group = the card's pair group (gene_condition joins
gene_associated_with_condition and genetically_associated_with) or the predicate itself. One pass over the verdict
file keeps, per stratum, the n rows with the smallest uniform random key (bottom-k reservoir sampling: a uniform
sample without replacement, independent of file order; keys drawn from numpy default_rng(seed) in file order).
Each verdict's n rows are allocated across its groups proportionally to the group sizes, at least 1 per group
(largest remainder), and the allocated count is taken from the stratum's reservoir (smallest keys first). Rows are
shuffled with the same seed, so pruned and kept edges are interleaved. Score with scripts/score_spotcheck.py.
"""
import argparse
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SEED = 20261108
VERDICT_COLS = ["id", "subject", "predicate", "object", "source", "primary_knowledge_source", "confidence", "verdict",
                "reasons"]
# the source's own statistic, in the order shown; activity is shown with its activity_type
STAT_COLS = ["activity", "disgenet_score", "jensenlab_zscore", "jensenlab_confidence", "monarch_s2o",
             "expression_atlas_log2fc", "mean_rank_score", "median_p_value", "median_odds_ratio", "p_value",
             "effect_size", "study_count", "snp_count", "stringdb_score", "bioplex_probability", "tinx_importance",
             "tinx_disease_novelty", "evidence_code", "action"]
DETAIL_COLS = ["id", "knowledge_level", "agent_type", "n_records", "activity_type", "ligand_name", "drug_name",
               "pmids"] + STAT_COLS
JUDGEMENTS = ["supported", "unsupported", "cannot tell"]
SHEET_COLS = ["row", "subject_name", "subject_id", "predicate", "object_name", "object_id", "source",
              "knowledge_level", "source_statistic", "references", "owner_judgement", "notes"]
KEY_COLS = ["row", "verdict", "confidence", "reasons", "edge_id", "predicate_group", "stratum_size", "stratum_sampled"]


def predicate_group(predicate, pair_groups):
    """Group name of a predicate: its pair group from the card's prune.pair_groups, else the predicate itself
    (without the biolink: prefix)."""
    for g, preds in (pair_groups or {}).items():
        if predicate in preds:
            return g
    return str(predicate).replace("biolink:", "")


def load_pair_groups(card):
    """prune.pair_groups from a source card (dict group -> list of predicates); {} when absent."""
    import yaml
    with open(card) as f:
        return (yaml.safe_load(f).get("prune") or {}).get("pair_groups") or {}


def stream_reservoirs(path, pair_groups, k, seed=SEED, chunksize=2_000_000):
    """One pass over a verdict file. Returns (reservoir, sizes, n_rows): reservoir = DataFrame of at most k rows per
    (verdict, predicate_group) stratum with the smallest random key `_key`; sizes = {(verdict, group): row count}."""
    rng = np.random.default_rng(seed)
    res, sizes, n_rows = None, {}, 0
    for ch in pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False, usecols=VERDICT_COLS, chunksize=chunksize):
        n_rows += len(ch)
        ch["_key"] = rng.random(len(ch))
        gmap = {p: predicate_group(p, pair_groups) for p in ch["predicate"].unique()}
        ch["predicate_group"] = ch["predicate"].map(gmap)
        for s, c in ch.groupby(["verdict", "predicate_group"]).size().items():
            sizes[s] = sizes.get(s, 0) + int(c)
        cand = ch.sort_values("_key").groupby(["verdict", "predicate_group"], sort=False).head(k)
        res = cand if res is None else pd.concat([res, cand], ignore_index=True)
        res = res.sort_values("_key").groupby(["verdict", "predicate_group"], sort=False).head(k).reset_index(drop=True)
    if res is None:
        res = pd.DataFrame(columns=VERDICT_COLS + ["_key", "predicate_group"])
    return res, sizes, n_rows


def allocate(sizes, n):
    """{group: size} -> {group: count}: proportional to size, at least 1 per non-empty group, largest remainder,
    never more than the group has, summing to min(n, total) (more than n only if there are more groups than n)."""
    sizes = {g: s for g, s in sizes.items() if s > 0}
    total = sum(sizes.values())
    n = min(n, total)
    if not sizes or n <= 0:
        return {}
    exact = {g: n * s / total for g, s in sizes.items()}
    alloc = {g: min(sizes[g], max(1, int(np.floor(e)))) for g, e in exact.items()}
    order = sorted(sizes, key=lambda g: (-(exact[g] - np.floor(exact[g])), -sizes[g], g))
    while sum(alloc.values()) < n:
        grown = False
        for g in order:
            if sum(alloc.values()) >= n:
                break
            if alloc[g] < sizes[g]:
                alloc[g] += 1
                grown = True
        if not grown:
            break
    while sum(alloc.values()) > n:
        g = max((g for g in alloc if alloc[g] > 1), key=lambda g: (alloc[g], g), default=None)
        if g is None:
            break
        alloc[g] -= 1
    return alloc


def draw(reservoir, sizes, n_by_verdict):
    """Take each verdict's allocation from the reservoir (smallest keys first). Adds stratum_size, stratum_sampled."""
    parts = []
    for verdict, n in n_by_verdict.items():
        alloc = allocate({g: s for (v, g), s in sizes.items() if v == verdict}, n)
        for g, c in sorted(alloc.items()):
            r = reservoir[(reservoir["verdict"] == verdict) & (reservoir["predicate_group"] == g)].nsmallest(c, "_key")
            parts.append(r.assign(stratum_size=sizes[(verdict, g)], stratum_sampled=len(r)))
    return pd.concat(parts, ignore_index=True) if parts else reservoir.iloc[0:0]


def edge_details(path, ids, chunksize=1_000_000):
    """Stream an edges file and return the DETAIL_COLS present for the given edge ids (stops once all are found)."""
    ids, found = set(ids), []
    header = pd.read_csv(path, sep="\t", nrows=0).columns
    cols = [c for c in DETAIL_COLS if c in header]
    for ch in pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False, usecols=cols, chunksize=chunksize):
        hit = ch[ch["id"].isin(ids)]
        if len(hit):
            found.append(hit)
            ids -= set(hit["id"])
        if not ids:
            break
    out = pd.concat(found, ignore_index=True) if found else pd.DataFrame(columns=cols)
    return out.drop_duplicates("id").set_index("id")


def stat_string(rec):
    """'name=value; ...' for the non-empty statistic columns of one edge (dict-like); activity carries its type."""
    parts = []
    for c in STAT_COLS:
        v = str(rec.get(c, "") or "").strip()
        if not v:
            continue
        if c == "activity" and str(rec.get("activity_type", "") or "").strip():
            v = f"{v} ({rec['activity_type']})"
        parts.append(f"{c}={v}")
    n = str(rec.get("n_records", "") or "").strip()
    if n and n != "1":
        parts.append(f"n_records={n}")
    return "; ".join(parts)


def references(rec, k=5):
    """First k PMIDs of an edge as 'PMID:x | PMID:y (+m more)'; '' when none."""
    p = [x for x in str(rec.get("pmids", "") or "").split("|") if x.strip()]
    s = " | ".join(f"PMID:{x.strip()}" for x in p[:k])
    return s + (f" (+{len(p) - k} more)" if len(p) > k else "")


def node_names(path):
    """{node id: name}; gene symbol preferred when present."""
    df = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
    name = df["name"] if "name" in df.columns else pd.Series("", index=df.index)
    if "symbol" in df.columns:
        name = df["symbol"].where(df["symbol"] != "", name)
    return dict(zip(df["id"], name))


def build_sheet(sample, details, names, seed=SEED):
    """(sheet, key) DataFrames sharing the 'row' column. The sheet carries no verdict, confidence or reasons."""
    s = sample.sample(frac=1, random_state=seed).reset_index(drop=True)
    d = details.reindex(s["id"]).fillna("").reset_index(drop=True)
    recs = d.to_dict("records")
    compound = [r.get("ligand_name", "") or r.get("drug_name", "") for r in recs]

    def nm(ids, fallback):
        return [names.get(i, "") or fb or "(no name in nodes.tsv.gz)" for i, fb in zip(ids, fallback)]
    sheet = pd.DataFrame({
        "row": range(1, len(s) + 1),
        "subject_name": nm(s["subject"], compound),
        "subject_id": s["subject"],
        "predicate": s["predicate"].str.replace("biolink:", "", regex=False),
        "object_name": nm(s["object"], [""] * len(s)),
        "object_id": s["object"],
        "source": [f"{a} ({b})" if b else a for a, b in zip(s["source"], s["primary_knowledge_source"])],
        "knowledge_level": [r.get("knowledge_level", "") for r in recs],
        "source_statistic": [stat_string(r) for r in recs],
        "references": [references(r) for r in recs],
        "owner_judgement": "",
        "notes": "",
    })
    key = pd.DataFrame({"row": sheet["row"], "verdict": s["verdict"], "confidence": s["confidence"],
                        "reasons": s["reasons"], "edge_id": s["id"], "predicate_group": s["predicate_group"],
                        "stratum_size": s["stratum_size"], "stratum_sampled": s["stratum_sampled"]})
    return sheet[SHEET_COLS], key[KEY_COLS]


def write_blind_xlsx(sheet, readme, path):
    """Sheet 'spot_check' (owner_judgement has a drop-down: supported / unsupported / cannot tell) + tab 'README'."""
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        sheet.to_excel(w, index=False, sheet_name="spot_check")
        pd.DataFrame(readme, columns=["item", "value"]).to_excel(w, index=False, sheet_name="README")
        ws = w.sheets["spot_check"]
        for i, col in enumerate(sheet.columns, 1):
            width = max(len(str(col)), sheet[col].astype(str).str.len().max() if len(sheet) else 0)
            ws.column_dimensions[get_column_letter(i)].width = min(width + 2, 50)
        j = get_column_letter(list(sheet.columns).index("owner_judgement") + 1)
        dv = DataValidation(type="list", formula1='"' + ",".join(JUDGEMENTS) + '"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"{j}2:{j}{len(sheet) + 1}")
        ws.freeze_panes = "B2"
        w.sheets["README"].column_dimensions["A"].width = 22
        w.sheets["README"].column_dimensions["B"].width = 120
    return path


def readme_rows(seed, inputs, n_rows, n_prune, n_keep, pair_groups, key_name):
    """README tab rows: seed, inputs (size, mtime), method, instructions. No per-row or per-stratum verdict info."""
    rows = [("purpose", "Blinded spot check of the shipped pruner verdicts (FIX_PLAN_2026-11-08 CS-4, review finding M5)."),
            ("generated", dt.datetime.now().strftime("%Y-%m-%d %H:%M") + " by scripts/make_spotcheck_blinded.py"),
            ("seed", str(seed))]
    for p in inputs:
        st = Path(p).stat()
        rows.append(("input", f"{Path(p).relative_to(ROOT)} ({st.st_size:,} bytes, modified "
                              f"{dt.datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M})"))
    rows += [("verdict rows read", f"{n_rows:,}"),
             ("sample", f"{n_prune + n_keep} edges: {n_prune} the pruner prunes + {n_keep} it keeps, shuffled together"),
             ("strata", "verdict x predicate group; groups = predicates, except pair groups from the card: "
                        + "; ".join(f"{g} = {', '.join(p)}" for g, p in pair_groups.items())),
             ("method", "bottom-k reservoir sampling per stratum in one streamed pass (uniform keys from numpy "
                        "default_rng(seed) in file order); proportional allocation within each verdict, >= 1 per group"),
             ("how to rate", "owner_judgement per row: 'supported' (the claim is right / backed by the evidence), "
                             "'unsupported' (wrong, or not backed by its evidence), 'cannot tell'. Notes free text."),
             ("columns", "source = source name (primary knowledge source); source_statistic = the source's own "
                         "statistic(s) on the edge; references = first PMIDs on the edge"),
             ("rule", "pre-registered in outputs/pharos/spotcheck_rule.md and loop/ledger.tsv (id CS-4-spotcheck); "
                      "numbers may change only BEFORE this sheet is opened"),
             ("key", f"{key_name} holds verdict, confidence and reasons per row. Do not open it until every row is rated."),
             ("score", ".venv/bin/python scripts/score_spotcheck.py")]
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--layer", default="pharos")
    ap.add_argument("--n-prune", type=int, default=100)
    ap.add_argument("--n-keep", type=int, default=100)
    ap.add_argument("--seed", type=int, default=SEED)
    a = ap.parse_args(argv)
    kg, out = ROOT / "data" / "kg" / a.layer, ROOT / "outputs" / a.layer
    vpath, epath, npath, card = kg / "edge_verdicts.tsv.gz", kg / "edges.tsv.gz", kg / "nodes.tsv.gz", ROOT / "sources" / f"{a.layer}.yaml"
    pair_groups = load_pair_groups(card)
    reservoir, sizes, n_rows = stream_reservoirs(vpath, pair_groups, max(a.n_prune, a.n_keep), seed=a.seed)
    sample = draw(reservoir, sizes, {"prune": a.n_prune, "keep": a.n_keep})
    details = edge_details(epath, sample["id"])
    sheet, key = build_sheet(sample, details, node_names(npath), seed=a.seed)
    xlsx, keyf = out / "spotcheck_blind.xlsx", out / "spotcheck_blind_key.tsv"
    n_p, n_k = int((key["verdict"] == "prune").sum()), int((key["verdict"] == "keep").sum())
    write_blind_xlsx(sheet, readme_rows(a.seed, [vpath, epath, npath], n_rows, n_p, n_k, pair_groups, keyf.name), xlsx)
    key.to_csv(keyf, sep="\t", index=False)
    groups = {g for _, g in sizes}
    # aggregate counts only: per-row or per-stratum verdicts stay in the key
    print(f"verdict rows read {n_rows:,}; prune {sum(s for (v, _), s in sizes.items() if v == 'prune'):,}; keep "
          f"{sum(s for (v, _), s in sizes.items() if v == 'keep'):,}; predicate groups {len(groups)}")
    print(f"sheet {len(sheet)} rows ({n_p} prune + {n_k} keep) -> {xlsx.relative_to(ROOT)}; key -> {keyf.relative_to(ROOT)}")
    print(f"edges with details found {details.index.isin(sample['id']).sum()} of {len(sample)}; "
          f"empty subject names {(sheet['subject_name'] == '(no name in nodes.tsv.gz)').sum()}, "
          f"empty object names {(sheet['object_name'] == '(no name in nodes.tsv.gz)').sum()}; "
          f"rows with a statistic {(sheet['source_statistic'] != '').sum()}")


if __name__ == "__main__":
    sys.exit(main())
