"""Pruning report from the SHIPPED verdict file (CS-2, review findings M3/M4): no re-scoring.

pipelines/prune_kg.py has no report-only mode (it re-scores and rewrites edge_verdicts.tsv.gz). This script reads the
verdict file the graphs were built from, plus the layer's edges file (same row order; only `id` and `knowledge_level`
are used) and the Open Targets layer (held-out corroboration), and writes:

    outputs/<layer>/prune_report.md      real-layer pruned share overall / by predicate / by knowledge level / by
                                         source, confidence distribution, three thresholds with pruned share and Open
                                         Targets corroboration at each, and the injected-error numbers quoted from
                                         prune_compare.md (v1 row; the shipped run's own injected-set numbers were
                                         never written to a file)
    outputs/<layer>/prune_metrics.json   the same numbers

    python scripts/prune_report_from_verdicts.py [--layer pharos] [--ot data/kg/opentargets/edges.tsv.gz]

Thresholds: (1) shipped = the verdict column as written; (2) and (3) = the confidence values below which 5% and 10% of
the layer's edges lie, applying the shipped run's own rule (exempt predicates never pruned; edges whose reasons say
'protected' never pruned).

Functions:
  stream_verdicts(verdict_path, edges_path, ot, exempt)  -> arrays + counters (one pass, both files in step)
  threshold_rows(a, cuts)                                 -> one dict per threshold
  confidence_summary(conf)                                -> quantiles + histogram
  quote_compare(path, row_label)                          -> the prune_compare.md row as {column: value}
  report_markdown(layer, m)                               -> markdown text
"""
import argparse
from array import array
import gzip
import io
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
GD = "biolink:gene_associated_with_condition"
TEXT_LEVEL = "text_co_occurrence"
OT_STRONG = 0.2
QUANTILE_CUTS = (0.05, 0.10)


def _open(path):
    path = Path(path)
    return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", newline="") if path.suffix == ".gz" \
        else open(path, encoding="utf-8", newline="")


def load_ot_pairs(path):
    """(subject, object) -> max ot_score of the Open Targets gene-disease edges (streamed)."""
    out = {}
    with _open(path) as fh:
        h = fh.readline().rstrip("\r\n").split("\t")
        i_s, i_p, i_o, i_sc = h.index("subject"), h.index("predicate"), h.index("object"), h.index("ot_score")
        for line in fh:
            f = line.rstrip("\r\n").split("\t")
            if f[i_p] != GD:
                continue
            try:
                sc = float(f[i_sc])
            except (ValueError, IndexError):
                sc = float("nan")
            k = (f[i_s], f[i_o])
            prev = out.get(k)
            out[k] = sc if prev is None or (sc == sc and (prev != prev or sc > prev)) else prev
    return out


def stream_verdicts(verdict_path, edges_path, ot, exempt=()):
    """One pass over the verdict file and the layer's edges file in step (prune.score writes verdicts in edge order;
    a row whose ids differ raises ValueError). Returns dict of numpy arrays (conf, pruned, eligible, gd, text, ot_any,
    ot_strong, pred code, level code, source code) and the code tables."""
    conf = array("d")
    pruned, eligible, gd, text, ot_any, ot_strong = (array("b") for _ in range(6))
    codes = {"predicate": {}, "knowledge_level": {}, "source": {}}
    pc, lc, sc = array("i"), array("i"), array("i")
    n_protected = 0
    with _open(verdict_path) as vf, _open(edges_path) as ef:
        vh = vf.readline().rstrip("\r\n").split("\t")
        eh = ef.readline().rstrip("\r\n").split("\t")
        vi = {c: vh.index(c) for c in ("id", "subject", "predicate", "object", "source", "confidence", "verdict",
                                       "reasons")}
        ei_id, ei_lv = eh.index("id"), eh.index("knowledge_level")
        vneed, eneed = max(vi.values()) + 1, max(ei_id, ei_lv) + 1
        for n, (vl, el) in enumerate(zip(vf, ef)):
            v = vl.rstrip("\r\n").split("\t", vneed)
            e = el.rstrip("\r\n").split("\t", eneed)
            if v[vi["id"]] != e[ei_id]:
                raise ValueError(f"row {n + 2}: verdict id {v[vi['id']]} != edge id {e[ei_id]}")
            p, lvl, src = v[vi["predicate"]], e[ei_lv], v[vi["source"]]
            is_prot = "protected" in v[vi["reasons"]]
            n_protected += is_prot
            conf.append(float(v[vi["confidence"]]))
            pruned.append(v[vi["verdict"]] == "prune")
            eligible.append(p not in exempt and not is_prot)
            is_gd = p == GD
            gd.append(is_gd)
            text.append(lvl == TEXT_LEVEL)
            sco = ot.get((v[vi["subject"]], v[vi["object"]])) if is_gd else None
            ot_any.append(sco is not None)
            ot_strong.append(sco is not None and sco == sco and sco >= OT_STRONG)
            pc.append(codes["predicate"].setdefault(p, len(codes["predicate"])))
            lc.append(codes["knowledge_level"].setdefault(lvl, len(codes["knowledge_level"])))
            sc.append(codes["source"].setdefault(src, len(codes["source"])))
        if vf.readline() or ef.readline():
            raise ValueError("verdict and edge files have different row counts")
    b = lambda x: np.frombuffer(x, dtype=np.int8).astype(bool)
    i = lambda x: np.frombuffer(x, dtype=np.int32).copy() if len(x) else np.zeros(0, dtype=np.int32)
    return dict(conf=np.frombuffer(conf, dtype=np.float64).copy(), pruned=b(pruned), eligible=b(eligible), gd=b(gd),
                text=b(text), ot_any=b(ot_any), ot_strong=b(ot_strong), pred=i(pc), level=i(lc), source=i(sc),
                codes=codes,
                n_protected=int(n_protected))


def _share(mask, sub):
    n = int(sub.sum())
    return dict(n=n, pruned=int((mask & sub).sum()), share=float((mask & sub).sum() / n) if n else None)


def _ot(mask, a, sel):
    """Open Targets corroboration of the gene-disease edges in sel, split into pruned (mask) and kept."""
    out = {}
    for name, m in (("pruned", sel & mask), ("kept", sel & ~mask)):
        n = int(m.sum())
        out[name] = dict(n=n, in_ot=float(a["ot_any"][m].mean()) if n else None,
                         ot_strong=float(a["ot_strong"][m].mean()) if n else None)
    return out


def threshold_rows(a, cuts=QUANTILE_CUTS):
    """Takes stream_verdicts arrays. Returns [shipped, quantile cuts...]: label, threshold, pruned share overall,
    pruned share of gene-disease edges, share of pruned edges that are text co-occurrence, and Open Targets
    corroboration (pruned vs kept) for non-text and text gene-disease edges."""
    elig_kept = a["eligible"] & ~a["pruned"]
    t_ship = dict(max_pruned=float(a["conf"][a["pruned"]].max()) if a["pruned"].any() else None,
                  min_eligible_kept=float(a["conf"][elig_kept].min()) if elig_kept.any() else None)
    variants = [("shipped verdicts (best-F1 cut on injected errors)", t_ship, a["pruned"])]
    for q in cuts:
        t = float(np.quantile(a["conf"], q))
        variants.append((f"confidence < {q:.0%} quantile of all edges", t, (a["conf"] < t) & a["eligible"]))
    rows = []
    allm = np.ones_like(a["pruned"])
    for label, t, mask in variants:
        n_pr = int(mask.sum())
        rows.append(dict(label=label, threshold=t, pruned=n_pr, pruned_share=float(mask.mean()),
                         gd=_share(mask, a["gd"]), text_share_of_pruned=float(a["text"][mask].mean()) if n_pr else None,
                         ot_non_text=_ot(mask, a, a["gd"] & ~a["text"]), ot_text=_ot(mask, a, a["gd"] & a["text"]),
                         all=_share(mask, allm)["pruned"]))
    return rows


def confidence_summary(conf, bins=10):
    qs = [0.0, 0.01, 0.05, 0.10, 0.25, 0.5, 0.75, 0.9, 0.99, 1.0]
    hist, edges = np.histogram(conf, bins=bins, range=(0.0, 1.0))
    return dict(quantiles={f"{q:g}": float(np.quantile(conf, q)) for q in qs}, mean=float(conf.mean()),
                histogram=[dict(lo=float(edges[i]), hi=float(edges[i + 1]), n=int(hist[i])) for i in range(bins)])


def _group(a, key):
    names = {v: k for k, v in a["codes"][key].items()}
    arr = {"predicate": a["pred"], "knowledge_level": a["level"], "source": a["source"]}[key]
    n = np.bincount(arr, minlength=len(names))
    p = np.bincount(arr, weights=a["pruned"], minlength=len(names))
    out = {names[i]: dict(n=int(n[i]), pruned=int(p[i]), share=float(p[i] / n[i])) for i in range(len(names)) if n[i]}
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["n"]))


def quote_compare(path, row_label="v1"):
    """Takes prune_compare.md and a row label prefix. Returns {column header: cell} of the first table row whose first
    cell starts with row_label, or None when the file or row is absent."""
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return None
    head = None
    for ln in lines:
        if ln.startswith("| variant"):
            head = [c.strip() for c in ln.strip().strip("|").split("|")]
        elif head and ln.startswith("| " + row_label):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            return dict(zip(head, cells))
    return None


def metrics(a, compare_row=None, compare_file=None):
    return dict(n_edges=int(len(a["conf"])), n_pruned=int(a["pruned"].sum()), pruned_share=float(a["pruned"].mean()),
                n_protected=a["n_protected"], n_eligible=int(a["eligible"].sum()),
                by_predicate=_group(a, "predicate"), by_knowledge_level=_group(a, "knowledge_level"),
                by_source=_group(a, "source"), confidence=confidence_summary(a["conf"]),
                confidence_pruned=confidence_summary(a["conf"][a["pruned"]]) if a["pruned"].any() else None,
                thresholds=threshold_rows(a), ot_strong_score=OT_STRONG,
                injected_errors=dict(source=compare_file, row=compare_row))


def _p(x, d=1):
    return "-" if x is None else f"{100 * x:.{d}f}%"


def report_markdown(layer, m, verdict_file, ot_file):
    lines = [f"# Pruning report - {layer} (shipped verdicts, real layer)", "",
             f"Computed from `{verdict_file}` (the verdicts the graphs were built from; no re-scoring) with "
             f"`scripts/prune_report_from_verdicts.py`. Knowledge levels from the layer's edges file (same row order, "
             f"ids checked row by row). Held-out corroboration: Open Targets gene-disease pairs from `{ot_file}` (the "
             "pruner never saw Open Targets). Replaces the 25 Oct report of a 1,650,119-edge build.", "",
             f"- edges scored: **{m['n_edges']:,}**; verdict 'prune': **{m['n_pruned']:,}** "
             f"(**{_p(m['pruned_share'], 2)}**); edges marked protected in the reasons: {m['n_protected']:,}",
             f"- confidence: mean {m['confidence']['mean']:.3f}; quantiles "
             + ", ".join(f"q{k} {v:.3f}" for k, v in m["confidence"]["quantiles"].items()), ""]
    inj = m["injected_errors"]["row"]
    if inj:
        lines += ["## What the injected-error test says (quoted, not recomputed)", "",
                  f"The shipped run's own injected-set numbers were not written to a file. The same v1 settings on "
                  f"this layer, on a separate injected set (seed 1), from `{m['injected_errors']['source']}`:", "",
                  "| " + " | ".join(inj) + " |", "|" + "---|" * len(inj), "| " + " | ".join(inj.values()) + " |", "",
                  "Injected errors are type-preserving object swaps that keep the original edge's source and score, so "
                  "this test measures graph implausibility and missing corroboration only; it cannot see a wrong edge "
                  "from a trusted source, and real edges include unknown errors. The Open Targets columns below are "
                  "the real-graph check.", ""]
    lines += ["## Three thresholds: pruned share and Open Targets corroboration", "",
              f"Gene-disease edges only for the corroboration columns ('in OT' = pair asserted by Open Targets at any "
              f"score; 'OT >= {m['ot_strong_score']}' = at overall score >= {m['ot_strong_score']}).", "",
              "| threshold | confidence cut | edges pruned | share of all edges | share of gene-disease edges | "
              "pruned that are text co-occurrence | non-text G-D pruned: n / in OT / OT >= 0.2 | non-text G-D kept: "
              "in OT / OT >= 0.2 | TIN-X pruned: n / in OT | TIN-X kept: in OT |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in m["thresholds"]:
        t = r["threshold"]
        if isinstance(t, dict):
            cut = (f"{t['max_pruned']:.4f} (largest pruned = smallest eligible kept at the file's 4 decimals)"
                   if f"{t['max_pruned']:.4f}" == f"{t['min_eligible_kept']:.4f}"
                   else f"between {t['max_pruned']:.4f} and {t['min_eligible_kept']:.4f}")
        else:
            cut = f"< {t:.4f}"
        nt, tx = r["ot_non_text"], r["ot_text"]
        lines.append(f"| {r['label']} | {cut} | {r['pruned']:,} | {_p(r['pruned_share'], 2)} | {_p(r['gd']['share'], 2)} | "
                     f"{_p(r['text_share_of_pruned'])} | {nt['pruned']['n']:,} / {_p(nt['pruned']['in_ot'])} / "
                     f"{_p(nt['pruned']['ot_strong'])} | {_p(nt['kept']['in_ot'])} / {_p(nt['kept']['ot_strong'])} | "
                     f"{tx['pruned']['n']:,} / {_p(tx['pruned']['in_ot'])} | {_p(tx['kept']['in_ot'])} |")
    lines += ["", "The alternative cuts apply the shipped run's rule (exempt predicates and protected edges are never "
              "pruned); they are reported, not shipped.", "",
              "## By knowledge level", "", "| knowledge level | edges | pruned | share pruned |", "|---|---|---|---|"]
    lines += [f"| {k or '(empty)'} | {v['n']:,} | {v['pruned']:,} | {_p(v['share'], 2)} |" for k, v in m["by_knowledge_level"].items()]
    lines += ["", "## By predicate", "", "| predicate | edges | pruned | share pruned |", "|---|---|---|---|"]
    lines += [f"| {k} | {v['n']:,} | {v['pruned']:,} | {_p(v['share'], 2)} |" for k, v in m["by_predicate"].items()]
    lines += ["", "## By source", "", "| source | edges | pruned | share pruned |", "|---|---|---|---|"]
    lines += [f"| {k} | {v['n']:,} | {v['pruned']:,} | {_p(v['share'], 2)} |" for k, v in m["by_source"].items()]
    lines += ["", "## Confidence distribution (all edges; pruned edges in the last column)", "",
              "| confidence | edges | pruned edges |", "|---|---|---|"]
    hp = {(h["lo"], h["hi"]): h["n"] for h in (m["confidence_pruned"] or {}).get("histogram", [])}
    lines += [f"| {h['lo']:.1f} - {h['hi']:.1f} | {h['n']:,} | {hp.get((h['lo'], h['hi']), 0):,} |"
              for h in m["confidence"]["histogram"]]
    lines += ["", "What this pruner is: confidence re-weighting (learned graph plausibility + corroboration, plus source "
              "trust) with a conservative default cut. What it demonstrably removes is uncorroborated, graph-implausible "
              "edges; whether they are wrong is for the blinded spot check (`outputs/pharos/spotcheck_rule.md`) and the "
              "organisers' gold set to decide.", ""]
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--layer", default="pharos")
    p.add_argument("--verdicts", default=None, help="default data/kg/<layer>/edge_verdicts.tsv.gz")
    p.add_argument("--edges", default=None, help="default data/kg/<layer>/edges.tsv.gz")
    p.add_argument("--ot", default="data/kg/opentargets/edges.tsv.gz")
    p.add_argument("--compare", default=None, help="default outputs/<layer>/prune_compare.md")
    p.add_argument("--exempt", default="biolink:has_gene_product", help="never-pruned predicates (comma list)")
    p.add_argument("--out-dir", default=None, help="default outputs/<layer>")
    args = p.parse_args(argv)
    vpath = args.verdicts or f"data/kg/{args.layer}/edge_verdicts.tsv.gz"
    epath = args.edges or f"data/kg/{args.layer}/edges.tsv.gz"
    cpath = args.compare or f"outputs/{args.layer}/prune_compare.md"
    out = Path(args.out_dir or f"outputs/{args.layer}")
    ot = load_ot_pairs(args.ot)
    print(f"Open Targets gene-disease pairs {len(ot):,}", flush=True)
    a = stream_verdicts(vpath, epath, ot, exempt=set(filter(None, args.exempt.split(","))))
    m = metrics(a, quote_compare(cpath), cpath)
    m["inputs"] = dict(verdicts=vpath, edges=epath, ot=args.ot, n_ot_pairs=len(ot))
    out.mkdir(parents=True, exist_ok=True)
    (out / "prune_report.md").write_text(report_markdown(args.layer, m, vpath, args.ot))
    (out / "prune_metrics.json").write_text(json.dumps(m, indent=1, sort_keys=True) + "\n")
    print(json.dumps(dict(n_edges=m["n_edges"], n_pruned=m["n_pruned"], pruned_share=m["pruned_share"],
                          thresholds=[(r["label"], r["pruned"], r["pruned_share"]) for r in m["thresholds"]])))


if __name__ == "__main__":
    sys.exit(main())
