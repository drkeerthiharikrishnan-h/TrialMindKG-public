"""
Real-data check of the pruning confidence against an INDEPENDENT source (docs/STRATEGY_REVIEW_HANDOFF.md follow-up,
2026-11-02): do Pharos gene-disease edges that pruning v1 trusts less also get less support from Open Targets 26.09?
The Pharos pruner (prune_kg.py sources/pharos.yaml) sees only Pharos-internal signals, so Open Targets corroboration
is held-out evidence. It is NOT a gold set (Open Targets also contains literature evidence), so the table is read as
a calibration curve, not as precision.

    python pipelines/prune_calibration.py [--ot-threshold 0.2] [--deciles 10]

Reads:  data/kg/pharos/edge_verdicts.tsv.gz (gene_associated_with_condition rows; the text-mining sources reported
        separately: TEXT_SOURCES = the sources whose edges carry knowledge level text_co_occurrence in the layer),
        data/kg/opentargets/edges.tsv.gz (gene_associated_with_condition rows with ot_score)
Writes: outputs/pharos/prune_calibration.md (committed; small) and outputs/pharos/prune_calibration.json

  corroboration_table(verdicts, ot, threshold, deciles) -> dict of DataFrames: by_verdict, by_decile, by_source,
                                                           by_decile_source, tinx_by_verdict
  markdown(tables, threshold, n_pairs) -> text
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
GD = "biolink:gene_associated_with_condition"
# The two Pharos gene-disease sources whose edges carry knowledge level text_co_occurrence (checked source by source
# against data/kg/pharos/edges.tsv.gz on 2026-11-11: every source has exactly one knowledge level). Same split as
# scripts/prune_report_from_verdicts.py, which reads the knowledge level, so the two reports give the same counts.
TEXT_SOURCES = ("TIN-X", "JensenLab Text Mining")


def load_verdicts(path, chunksize=2_000_000):
    """Gene-disease verdict rows of a layer's edge_verdicts.tsv.gz (streamed; the Pharos file has 26M rows)."""
    cols = ["subject", "predicate", "object", "source", "confidence", "verdict"]
    parts = [c[c["predicate"] == GD] for c in pd.read_csv(path, sep="\t", usecols=cols, chunksize=chunksize)]
    return pd.concat(parts, ignore_index=True)


def load_ot_pairs(path):
    """(subject, object, ot_score) of Open Targets gene-disease edges, one row per pair (max score)."""
    e = pd.read_csv(path, sep="\t", usecols=["subject", "predicate", "object", "ot_score"], low_memory=False)
    e = e[e["predicate"] == GD].copy()
    e["ot_score"] = pd.to_numeric(e["ot_score"], errors="coerce")
    return e.groupby(["subject", "object"], as_index=False)["ot_score"].max()


def corroboration_table(verdicts, ot, threshold=0.2, deciles=10):
    """Takes gene-disease verdict rows and Open Targets pairs. Returns the corroboration rates (share of edges whose
    (subject, object) pair Open Targets also asserts, at any score and at score >= threshold) by verdict, by
    confidence decile (1 = least confident) and by source, for the non-text-mining edges, plus the
    text-mining sources (TEXT_SOURCES) by verdict under the key 'tinx_by_verdict' (name kept for readers of the JSON)."""
    m = verdicts.merge(ot, on=["subject", "object"], how="left")
    m["in_ot"] = m["ot_score"].notna()
    m["ot_strong"] = m["ot_score"].ge(threshold)
    m["pruned"] = m["verdict"].eq("prune")
    text = m["source"].isin(TEXT_SOURCES)
    nt = m[~text].copy()
    agg = dict(n=("in_ot", "size"), in_ot=("in_ot", "mean"), ot_strong=("ot_strong", "mean"))
    by_verdict = nt.groupby("verdict").agg(**agg)
    nt["decile"] = pd.qcut(nt["confidence"].rank(method="first"), min(deciles, max(len(nt), 1)), labels=False) + 1
    by_decile = nt.groupby("decile").agg(conf_min=("confidence", "min"), conf_max=("confidence", "max"), **agg)
    by_source = nt.groupby("source").agg(**agg, pruned=("pruned", "mean")).sort_values("n", ascending=False)
    by_decile_source = nt.groupby(["decile", "source"]).agg(**agg)   # which sources fall in which decile
    tinx = m[text].groupby("verdict").agg(**agg) if text.any() else pd.DataFrame(columns=list(agg))
    return {"by_verdict": by_verdict, "by_decile": by_decile, "by_source": by_source,
            "by_decile_source": by_decile_source, "tinx_by_verdict": tinx,
            "n_non_text": int(len(nt)), "n_text": int(text.sum())}


def _pct(x):
    return f"{100 * x:.1f}%"


def markdown(t, threshold, n_ot_pairs):
    """Report text computed from the tables (no prewritten conclusions beyond what the numbers say)."""
    d = t["by_decile"]
    lo, hi = d.iloc[0], d.iloc[-1]
    lines = [f"# Pruning confidence vs Open Targets corroboration (Pharos gene-disease edges)", "",
             f"Open Targets 26.09 gene-disease pairs: {n_ot_pairs:,}. Pharos gene-disease edges checked: "
             f"{t['n_non_text']:,} non-text-mining + {t['n_text']:,} text-mining ({', '.join(TEXT_SOURCES)}: the sources "
             f"whose edges carry knowledge level text_co_occurrence). 'in OT' = the pair is asserted by Open "
             f"Targets at any score; 'OT >= {threshold}' = at overall association score >= {threshold}. The Pharos "
             f"pruner never saw Open Targets, so this is held-out evidence; it is a calibration curve, not a gold set.", "",
             f"Least-confident decile: in OT {_pct(lo['in_ot'])}, OT >= {threshold} {_pct(lo['ot_strong'])}; "
             f"most-confident decile: {_pct(hi['in_ot'])} and {_pct(hi['ot_strong'])}.", "",
             "## By verdict (non-text-mining)", "", "| verdict | edges | in OT | OT >= %s |" % threshold, "|---|---|---|---|"]
    for v, r in t["by_verdict"].iterrows():
        lines.append(f"| {v} | {int(r['n']):,} | {_pct(r['in_ot'])} | {_pct(r['ot_strong'])} |")
    lines += ["", "## By confidence decile (1 = least confident, non-text-mining)", "",
              "| decile | confidence range | edges | in OT | OT >= %s |" % threshold, "|---|---|---|---|---|"]
    for k, r in d.iterrows():
        lines.append(f"| {k} | {r['conf_min']:.3f} - {r['conf_max']:.3f} | {int(r['n']):,} | {_pct(r['in_ot'])} | {_pct(r['ot_strong'])} |")
    lines += ["", "## By source (non-text-mining)", "", "| source | edges | in OT | OT >= %s | pruned |" % threshold,
              "|---|---|---|---|---|"]
    for s, r in t["by_source"].iterrows():
        lines.append(f"| {s} | {int(r['n']):,} | {_pct(r['in_ot'])} | {_pct(r['ot_strong'])} | {_pct(r['pruned'])} |")
    ds = t.get("by_decile_source")
    if ds is not None and len(ds):
        dl = list(d.index)
        lines += ["", "## Sources within each confidence decile (non-text-mining)", "",
                  f"Each cell: edges of the source in the decile / share of them with OT >= {threshold}. A decile's rate "
                  "above depends on which sources fall in it.", "",
                  "| source | " + " | ".join(str(k) for k in dl) + " |", "|---|" + "---|" * len(dl)]
        for src in t["by_source"].index:
            cells = []
            for k in dl:
                if (k, src) in ds.index:
                    r = ds.loc[(k, src)]
                    cells.append(f"{int(r['n']):,} / {_pct(r['ot_strong'])}")
                else:
                    cells.append("0")
            lines.append(f"| {src} | " + " | ".join(cells) + " |")
    if len(t["tinx_by_verdict"]):
        lines += ["", f"## Text mining ({', '.join(TEXT_SOURCES)}) by verdict", "", "| verdict | edges | in OT | OT >= %s |" % threshold, "|---|---|---|---|"]
        for v, r in t["tinx_by_verdict"].iterrows():
            lines.append(f"| {v} | {int(r['n']):,} | {_pct(r['in_ot'])} | {_pct(r['ot_strong'])} |")
    lines += ["", "Caveat: Open Targets includes literature-derived evidence, so agreement is partly shared provenance; "
              f"the OT >= {threshold} column reduces that. Expression Atlas rows are differential-expression hits, "
              "not curated associations, whatever their verdict."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--ot-threshold", type=float, default=0.2)
    p.add_argument("--deciles", type=int, default=10)
    a = p.parse_args(argv)
    v = load_verdicts(ROOT / "data" / "kg" / "pharos" / "edge_verdicts.tsv.gz")
    ot = load_ot_pairs(ROOT / "data" / "kg" / "opentargets" / "edges.tsv.gz")
    t = corroboration_table(v, ot, a.ot_threshold, a.deciles)
    out = ROOT / "outputs" / "pharos"
    out.mkdir(parents=True, exist_ok=True)
    (out / "prune_calibration.md").write_text(markdown(t, a.ot_threshold, len(ot)))
    (out / "prune_calibration.json").write_text(json.dumps(
        {k: (val.reset_index().to_dict(orient="records") if isinstance(val, pd.DataFrame) else val) for k, val in t.items()}
        | {"ot_threshold": a.ot_threshold, "n_ot_pairs": int(len(ot))}, indent=1, default=float))
    print((out / "prune_calibration.md").read_text())


if __name__ == "__main__":
    main()
