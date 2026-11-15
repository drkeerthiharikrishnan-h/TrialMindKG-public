"""
Numbers page data (BUILD_ROLES.md section 2, Claude Code item 1; docs/STRATEGY_REVIEW_RESPONSE_2026-11-02.md
sections 3 and 7): the numbers a judge sees, read ONLY from committed result files, each with the file it came from.

What it reads (nothing else; smoke runs and the stale prune_metrics.json never appear):
- outputs/linkpred/<graph>_<task>_<split>@frozen_<x>.json   runs of the frozen benchmark (run.py --tag frozen_<x>)
- the paired comparison of every frozen tag against G0 of the same tag (linkpred.compare.paired_delta and verdict,
  computed here from the same files with the same functions as compare.md)
- outputs/pharos/prune_calibration.json                     pruning confidence vs Open Targets (prune_calibration.py)

  frozen_runs(out_dir)              -> list of run rows (graph, tag, AUROC, baselines, settings, file, deviations)
  protocol_deviations(settings)     -> list of plain-language differences from the frozen protocol (section 3)
  frozen_comparisons(out_dir)       -> list of {tag, task, split, base, rows (compare.paired_delta + verdict)}
  calibration(path)                 -> the calibration file with sentences computed from its numbers, or None
  experiments(out_dir)              -> E003 (frozen protocol, cap-matched) and the EXPLORATORY E004-E006 / E008 rows,
                                       each number from its committed JSON, plus the overlap-control table (E005)
  lead(out_dir)                     -> the Numbers tab's first table (PK3, U6): Pharos alone / agent-built / agent-built
                                       and pruned, the same model on the same test pairs, each difference with its interval
  pruning_evidence(roots)           -> the pruning story in the order of finding AC-05: a verdict on every edge, the
                                       corroboration gradient, the spot check, the injected-error test as a stated limit
  WORDS                             -> plain names for the graph codes and run tags the judges' run tables carry
  numbers(root, fallback_root)      -> {benchmark: {runs, comparisons, protocol, tags, lead, pruning, words}, calibration,
                                       experiments, sources, fallback_files, neptune (routes_neptune.numbers_row: the sentence on
                                       the whole graph loaded in Amazon Neptune, from outputs/neptune/live_parity.json, or None)}
  source_file(root, name, fallback_root) -> Path of a file the page may link to (allow-list), or None
  epochs_caveat(label, res, base_label, base) -> sentence when two compared runs trained a different number of epochs

Fallback (CC-32): on the hosted app the data root (TMKG_PHAROS_ROOT) holds the graph but may lack the result files;
the image carries the repo's own committed outputs/ (aws/Dockerfile COPY outputs). A file absent under the data root is
then read from fallback_root (the app passes this repo's root); a file present in both is read from the data root.

Wording follows the numbers (CLAUDE.md rule 7): every sentence here is built from the values in the files.
No torch, no data/ access: runs in the cloud on the committed outputs.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
LINKPRED = Path("outputs") / "linkpred"
CALIBRATION = Path("outputs") / "pharos" / "prune_calibration.json"
FROZEN_RE = re.compile(r"^(G\d)_([A-Z])_([a-z_]+)@(frozen_[A-Za-z0-9_]+)\.json$")
BASE_GRAPH = "G0"

# The frozen protocol (review response section 3). Only the graph files, the number of seeds and, as the documented
# memory fallback, a 1M cap may differ between frozen runs; anything else is reported as a deviation on the page.
FROZEN_PROTOCOL = {
    "epochs": 300, "hidden": 64,
    "wiring": {"test_levels": ["knowledge_assertion"], "drop_levels": ["text_co_occurrence"], "positives": "assertions",
               "neg_match": "degree", "neg_pool": "training", "reverse_edges": True, "learn_embeddings": True,
               "num_bases": 30},
    "max_edges_per_type": (2_000_000, 1_000_000),
}
PROTOCOL_TEXT = ("Task gene -> disease (Pharos gene_associated_with_condition). Test items: the curated Pharos pairs "
                 "(knowledge_assertion), identical in every graph. 5 folds by pair hash; held-out pairs removed from "
                 "every gene-disease edge type. One negative per test pair from a disease of the same degree bin, so "
                 "popularity cannot score. Metric: AUROC; the comparison is each graph minus G0, paired over folds, "
                 "95% t interval. Training graph: full Pharos without TIN-X text co-occurrence, every edge type "
                 "capped at the number of edges shown next to each result (the protocol cap is 2M; in run A, G2 and G3 "
                 "ran at 1M after the 2M run exhausted the Mac's memory, G0 at 2M), 300 epochs.")
RESULT_RE = re.compile(r"^(G\d[A-Z]?)_([A-Z])_([a-z_]+)@([A-Za-z0-9_]+)\.json$")     # any tagged result file (G2R: E009)
BASE_CAPMATCHED = ("G0", "frozen_a_g0_1m")           # G0 at the same 1M cap as G2/G3 of run A (ledger E003)
EXPERIMENTS = [
    # Which committed files an experiment reads. Ids and titles follow loop/ledger.tsv; nothing here is a result.
    dict(id="E003", exploratory=False, title="Cap-matched comparison (frozen protocol)",
         question="Do G2 and G3 beat Pharos alone when every graph is capped at the same 1M edges per type?",
         base=BASE_CAPMATCHED, variants=[("G2", "frozen_a"), ("G3", "frozen_a")],
         metrics=[("auroc", "GNN AUROC"), ("auroc_metapath", "meta-path AUROC"), ("metapath_coverage", "pairs with a path"),
                  ("auroc_cn", "common-neighbour AUROC")], note_file="compare_capmatched.md"),
    dict(id="E004", exploratory=True, title="Rebuilt submission graphs (Open Targets score >= 0.1)",
         question="Does the meta-path gain replicate on the rebuilt G2 and G3, and is it larger than the common-neighbour gain?",
         base=BASE_CAPMATCHED, variants=[("G2", "diag_rebuilt_1m"), ("G3", "diag_rebuilt_1m")],
         metrics=[("auroc_metapath", "meta-path AUROC"), ("metapath_coverage", "pairs with a path"),
                  ("auroc_cn", "common-neighbour AUROC")], note_file="diag_rebuilt.md"),
    dict(id="E005", exploratory=True, title="Overlap control: drop one enrichment layer at a time", overlap=True,
         question="Which enrichment layer carries the meta-path gain?",
         base=BASE_CAPMATCHED, reference=("G2", "diag_rebuilt_1m"),
         variants=[("G2", "diag_rebuilt_1m"), ("G2", "diag_e005_noot"), ("G2", "diag_e005_nochembl37"),
                   ("G2", "diag_e005_nomondo"), ("G2", "diag_e005_nohpo"), ("G2", "diag_e005_nogo")],
         metrics=[("auroc_metapath", "meta-path AUROC"), ("metapath_coverage", "pairs with a path"),
                  ("auroc_cn", "common-neighbour AUROC")], note_file="diag_e005.md"),
    dict(id="E006", exploratory=True, title="Graph embedding (RotatE) under the frozen protocol",
         question="Does a RotatE embedding score held-out pairs better on the enriched graph than on Pharos alone?",
         # CS-3 (8 Nov review m2): diag_kge_rotate_gpu (dim 512) is not registered: its folds reached unequal epochs
         # (kge_epochs_done G0 141-149, G2 118-119, G3 270-300; docs/EXPERIMENTS_LINKPRED.md section 2 note), so its
         # comparisons mix graph and training. Only the equal-epoch dim-256 run is shown.
         same_tag_base="G0", variants=[("G2", "diag_kge_rotate_d256"), ("G3", "diag_kge_rotate_d256")],
         metrics=[("auroc", "RotatE AUROC")]),
    dict(id="E008", exploratory=True, title="GNN + path features (gnnpath)",
         question="Does giving the R-GCN the pair's path counts show the enrichment gain the plain R-GCN misses?",
         same_tag_base="G0", variants=[("G2", "diag_gnnpath_1m")],
         metrics=[("auroc_gnnpath", "GNN + path AUROC"), ("auroc_gnnhead", "GNN head only"),
                  ("auroc_pathhead", "path head only"), ("auroc", "plain R-GCN (same run)")]),
    # CC-24: specs with `pairs` compare each variant with its OWN base: [(variant (graph, tag), base (graph, tag))]
    dict(id="E008-G3", exploratory=True, title="GNN + path features on G3 (GPU), and the same-device check",
         question="Does the gnnpath gain hold on the pruned G3, and does the GPU give G0 the same number as the Mac?",
         pairs=[(("G3", "diag_gnnpath_1m_gpu"), ("G0", "diag_gnnpath_1m_gpu")),
                (("G0", "diag_gnnpath_1m_gpu"), ("G0", "diag_gnnpath_1m"))],
         metrics=[("auroc_gnnpath", "GNN + path AUROC")]),
    dict(id="E008-MONDO", exploratory=True, title="MONDO control: G2 without the MONDO disease hierarchy (gnnpath)",
         question="How much of the gnnpath gain of G2 over Pharos alone does the MONDO disease hierarchy carry?",
         pairs=[(("G2", "diag_gnnpath_1m_nomondo"), ("G0", "diag_gnnpath_1m")),
                (("G2", "diag_gnnpath_1m_nomondo"), ("G2", "diag_gnnpath_1m"))],
         metrics=[("auroc_gnnpath", "GNN + path AUROC"), ("auroc_pathhead", "path head only"),
                  ("auroc_gnnhead", "GNN head only")],
         share=dict(metric="auroc_gnnpath", layer="MONDO disease hierarchy"), note_file="diag_e008_nomondo.md"),
    dict(id="E009", exploratory=True, title="Random-pruning control (RotatE)",
         question="Is pruned G3 better than G2 with the same number of edges removed at random (G2R, seed 0)?",
         pairs=[(("G3", "diag_kge_rotate_d256_e009"), ("G2R", "diag_kge_rotate_d256_e009")),
                (("G2R", "diag_kge_rotate_d256_e009"), ("G2", "diag_kge_rotate_d256"))],
         metrics=[("auroc", "RotatE AUROC")], graph_labels={"G2R": "same number of edges removed at random (seed 0)"}),
    dict(id="D", exploratory=True, title="Release-graph confirmation run (gnnpath)", hide_if_missing=True,
         question="Does the gnnpath gain over Pharos alone hold on the shipped graph?",
         # Base = the G0 file named in the D pre-registration (loop/ledger.tsv row D): the same-device H100 run.
         pairs=[(("G2", "diag_gnnpath_1m_release"), ("G0", "diag_gnnpath_1m_gpu")),
                (("G3", "diag_gnnpath_1m_release"), ("G0", "diag_gnnpath_1m_gpu"))],
         metrics=[("auroc_gnnpath", "GNN + path AUROC")]),
    dict(id="D-metapath", exploratory=True, title="Release-graph confirmation run (meta-path model)", hide_if_missing=True,
         question="Does the meta-path gain over Pharos alone hold on the shipped graph?",
         # Base per loop/ledger.tsv row "D meta-path": G0_B_kfold@frozen_a_g0_1m (same base as E003/E004).
         pairs=[(("G2", "diag_release_metapath"), BASE_CAPMATCHED),
                (("G3", "diag_release_metapath"), BASE_CAPMATCHED)],
         metrics=[("auroc_metapath", "meta-path AUROC"), ("metapath_coverage", "pairs with a path")]),
]
OTHER_FILES = {                       # name -> relative path; the non-frozen files the page links to
    "compare.md": LINKPRED / "compare.md",
    "report.md": LINKPRED / "report.md",
    "compare_capmatched.md": LINKPRED / "compare_capmatched.md",
    "diag_rebuilt.md": LINKPRED / "diag_rebuilt.md",
    "diag_e005.md": LINKPRED / "diag_e005.md",
    "diag_e008_nomondo.md": LINKPRED / "diag_e008_nomondo.md",
    "prune_calibration.json": CALIBRATION,
    "prune_calibration.md": CALIBRATION.with_suffix(".md"),
    # PK3 (AC-05): the pruning files the Numbers tab cites
    "prune_report.md": Path("outputs") / "pharos" / "prune_report.md",
    "spotcheck_rule.md": Path("outputs") / "pharos" / "spotcheck_rule.md",
    "spotcheck_blind_result.md": Path("outputs") / "pharos" / "spotcheck_blind_result.md",
    "ppi_corroboration.md": Path("outputs") / "pharos" / "ppi_corroboration.md",
    "audit_sample_result.md": Path("outputs") / "pharos" / "audit_sample_result.md",   # S08: the 500 + 500 audit table
    "compare_cs9_gnnpath.md": LINKPRED / "compare_cs9_gnnpath.md",
}


def _dirs(out_dir):
    """Takes a results folder or a list of them. Returns the list of Paths, first one wins."""
    if isinstance(out_dir, (list, tuple)):
        return [Path(d) for d in out_dir]
    return [Path(out_dir)]


def _path(out_dir, name):
    """Takes a results folder (or list, first wins) and a file name. Returns the first existing Path, else the path in
    the first folder (which does not exist)."""
    dirs = _dirs(out_dir)
    for d in dirs:
        if (d / name).is_file():
            return d / name
    return dirs[0] / name


def _glob(out_dir, pattern):
    """Takes a results folder (or list) and a glob pattern. Returns {name: Path} over all folders, first folder wins."""
    found = {}
    for d in _dirs(out_dir):
        if d.is_dir():
            for p in d.glob(pattern):
                found.setdefault(p.name, p)
    return dict(sorted(found.items()))


def _kge_epochs(res):
    v = ((res or {}).get("summary") or {}).get("kge_epochs_done")
    return float(v["mean"]) if isinstance(v, dict) and v.get("mean") is not None else None


def epochs_caveat(label, res, base_label, base):
    """Takes two compared runs (label, result dict) of an embedding model. Returns a caveat sentence when both record
    kge_epochs_done (mean training epochs per fold; kge.py stops at an epoch or a time limit) and the values differ,
    else None. The sentence is built from the two numbers: the difference then mixes the graph with training length."""
    a, b = _kge_epochs(res), _kge_epochs(base)
    if a is None or b is None or abs(a - b) < 1e-9:
        return None
    return (f"Caveat: {label} trained {a:g} epochs per fold on average and {base_label} {b:g}, so this difference "
            f"mixes the graph with training length.")


def _metric(summary, key):
    """Takes a run summary and a metric key. Returns {'mean', 'ci95'} or None."""
    v = summary.get(key)
    return {"mean": float(v["mean"]), "ci95": float(v.get("ci95", 0.0))} if isinstance(v, dict) and "mean" in v else None


def protocol_deviations(settings):
    """Takes a result file's 'settings'. Returns a list of sentences, one per setting that differs from
    FROZEN_PROTOCOL ([] when the run follows it). Missing settings count as deviations."""
    out = []
    for k in ("epochs", "hidden"):
        if settings.get(k) != FROZEN_PROTOCOL[k]:
            out.append(f"{k} {settings.get(k)} (protocol {FROZEN_PROTOCOL[k]})")
    wiring = settings.get("wiring") or {}
    for k, want in FROZEN_PROTOCOL["wiring"].items():
        got = wiring.get(k)
        if isinstance(want, list):
            got = sorted(got or [])
        if got != want:
            out.append(f"{k} {got} (protocol {want})")
    if settings.get("max_edges_per_type") not in FROZEN_PROTOCOL["max_edges_per_type"]:
        out.append(f"max_edges_per_type {settings.get('max_edges_per_type')} (protocol 2,000,000; 1,000,000 allowed "
                   "as the memory fallback)")
    if settings.get("sample_fraction"):
        out.append(f"sample_fraction {settings['sample_fraction']} (protocol: whole graph)")
    return out


def _n_folds(result):
    return sum(len(s.get("folds", [])) for s in result.get("per_seed", []))


def frozen_runs(out_dir):
    """Takes the linkpred results folder. Returns one row per '*@frozen_*.json' file, sorted by tag, task, split,
    graph: graph, tag, task, split, file (name), auroc / auprc / degree baseline / meta-path model / untrained
    ({'mean','ci95'} or None), metapath_coverage, strata [{name, auroc, n}], folds, seeds, max_edges, test_digest,
    deviations (protocol_deviations). out_dir may be a list of folders (first wins). Files that do not parse are returned with 'error' instead of numbers."""
    rows = []
    for path in _glob(out_dir, "*@frozen_*.json").values():
        m = FROZEN_RE.match(path.name)
        if not m:
            continue
        graph, task, split, tag = m.groups()
        row = dict(graph=graph, task=task, split=split, tag=tag, label=f"{graph}@{tag}", file=path.name)
        try:
            res = json.loads(path.read_text())
            summary, settings = res["summary"], res.get("settings", {})
        except (ValueError, KeyError, TypeError) as err:
            rows.append(dict(row, error=f"unreadable result file: {err}"))
            continue
        strata = []
        for k in sorted(summary):
            if k.startswith("stratum_") and not k.endswith("_n") and _metric(summary, k):
                n = _metric(summary, k + "_n")
                strata.append(dict(name=k[len("stratum_"):], auroc=_metric(summary, k), n=n["mean"] if n else None))
        row.update(auroc=_metric(summary, "auroc"), auprc=_metric(summary, "auprc"),
                   degree_baseline=_metric(summary, "auroc_degree_dst"), metapath=_metric(summary, "auroc_metapath"),
                   untrained=_metric(summary, "auroc_untrained"), metapath_coverage=_metric(summary, "metapath_coverage"),
                   strata=strata, folds=_n_folds(res), seeds=settings.get("seeds"),
                   max_edges=settings.get("max_edges_per_type"), epochs=settings.get("epochs"),
                   test_digest=(settings.get("test_set") or {}).get("digest"),
                   deviations=protocol_deviations(settings))
        rows.append(row)
    rows.sort(key=lambda r: (r["tag"], r["task"], r["split"], r["graph"]))
    return rows


def frozen_comparisons(out_dir, base_graph=BASE_GRAPH):
    """Takes the linkpred results folder. Returns, per (tag, task, split) whose base run '<base_graph>@<tag>' exists
    and parses, {tag, task, split, base, rows, metapath_rows}: each other readable run of the same tag, task and
    split paired with the base fold by fold (linkpred.compare.paired_delta: n, mean, ci_low, ci_high, wins,
    digest_match) plus compare.verdict's sentence; metapath_rows do the same for the meta-path model (metric
    auroc_metapath) where the files hold it. Unreadable files are skipped (frozen_runs reports them)."""
    from linkpred import compare
    loaded = {}
    for r in frozen_runs(out_dir):
        if "error" not in r:
            loaded[(r["tag"], r["task"], r["split"], r["graph"])] = json.loads(_path(out_dir, r["file"]).read_text())
    out = []
    for tag, task, split in sorted({k[:3] for k in loaded}):
        base = loaded.get((tag, task, split, base_graph))
        if base is None:
            continue
        base_label = f"{base_graph}@{tag}"
        rows, mp_rows = [], []
        for (t, k, s, graph), res in sorted(loaded.items()):
            if (t, k, s) != (tag, task, split) or graph == base_graph:
                continue
            label = f"{graph}@{tag}"
            for metric, dest in (("auroc", rows), ("auroc_metapath", mp_rows)):
                d = compare.paired_delta(res, base, metric)
                if metric == "auroc" or d["n"]:
                    dest.append(dict(variant=label, **d, verdict=compare.verdict(d, label, base_label, metric)))
        out.append(dict(tag=tag, task=task, split=split, base=base_label, rows=rows, metapath_rows=mp_rows))
    return out


def _pct(x):
    return f"{100 * x:.1f}%"


def calibration(path):
    """Takes the path of prune_calibration.json. Returns None when it does not exist, else the file's tables plus
    'sentences' computed from them: the lowest vs highest confidence decile (share in Open Targets at any score and
    at >= threshold), how many of the decile-to-decile steps rise, and kept vs pruned."""
    path = Path(path)
    if not path.exists():
        return None
    cal = json.loads(path.read_text())
    dec = sorted(cal.get("by_decile", []), key=lambda d: d["decile"])
    thr = cal.get("ot_threshold")
    sentences = []
    if len(dec) >= 2:
        lo, hi = dec[0], dec[-1]
        sentences.append(
            f"Least-confident tenth of the {cal.get('n_non_text', sum(d['n'] for d in dec)):,} non-text-mining Pharos "
            f"gene-disease edges: {_pct(lo['ot_strong'])} also in Open Targets at score >= {thr} "
            f"({_pct(lo['in_ot'])} at any score); most-confident tenth: {_pct(hi['ot_strong'])} ({_pct(hi['in_ot'])}).")
        ups = sum(b["in_ot"] > a["in_ot"] for a, b in zip(dec, dec[1:]))
        ups_s = sum(b["ot_strong"] > a["ot_strong"] for a, b in zip(dec, dec[1:]))
        sentences.append(f"From one tenth to the next, the share in Open Targets rises in {ups} of {len(dec) - 1} "
                         f"steps (at score >= {thr}: {ups_s} of {len(dec) - 1}).")
    v = {r["verdict"]: r for r in cal.get("by_verdict", [])}
    if "keep" in v and "prune" in v:
        k, p = v["keep"], v["prune"]
        rel = "less often" if p["in_ot"] < k["in_ot"] else ("more often" if p["in_ot"] > k["in_ot"] else "as often")
        # FIX2 F3 (G2-AC-01): the share with its n, "also appear in", and how little of the pruning it covers
        txt = (f"Pruned gene-disease edges that are not text-mined are {rel} found in Open Targets than kept ones: "
               f"{round(p['n'] * p['in_ot']):,} of the {p['n']:,} pruned ({_pct(p['in_ot'])}) also appear there, "
               f"against {_pct(k['in_ot'])} of the {k['n']:,} kept.")
        tx = {r.get("verdict"): r for r in cal.get("tinx_by_verdict", []) if isinstance(r, dict)}.get("prune")
        if tx and tx.get("n"):                       # computed: how many pruned gene-disease edges the check leaves out
            txt += (f" A further {tx['n']:,} pruned gene-disease edges are text-mined"
                    + (", so this check covers few of them." if tx["n"] > p["n"] else " and are not in this check."))
        sentences.append(txt)
    sentences.append("The Pharos pruner never saw Open Targets, so this is held-out agreement, not a gold set; "
                     "Open Targets also holds literature evidence, so part of the agreement is shared provenance.")
    return dict(cal, by_decile=dec, sentences=sentences, file=CALIBRATION.name)


def _result_name(graph, tag, task="B", split="kfold"):
    return f"{graph}_{task}_{split}@{tag}.json"


def _load(out_dir, graph, tag):
    path = _path(out_dir, _result_name(graph, tag))
    try:
        return path.name, json.loads(path.read_text())
    except (OSError, ValueError):
        return path.name, None


def _mean(res, metric):
    v = (res.get("summary") or {}).get(metric)
    return float(v["mean"]) if isinstance(v, dict) and "mean" in v else None


def experiment_files():
    """Names of every result file the EXPERIMENTS specs read (the allow-list for source_file)."""
    names = set()
    for e in EXPERIMENTS:
        pairs = [e["base"]] if "base" in e else []
        pairs += [(e["same_tag_base"], t) for _, t in e["variants"]] if "same_tag_base" in e else []
        pairs += e.get("variants", [])
        pairs += [x for v, b in e.get("pairs", []) for x in (v, b)]
        names |= {_result_name(g, t) for g, t in pairs}
    return names


def _dropped(res):
    w = (res.get("settings") or {}).get("wiring") or {}
    return sorted(w.get("drop_layers") or [])


def experiments(out_dir):
    """Takes the linkpred results folder. Returns one entry per EXPERIMENTS spec, in order:
    {id, title, question, exploratory, status ('frozen protocol' | 'EXPLORATORY'), available, missing (file names not
    on this machine), base (label, file), rows, sentences, note_file, overlap}. Each row: graph, tag, label, file,
    dropped_layers (read from the file's settings), means {metric: mean}, deltas {metric: linkpred.compare.paired_delta
    + its verdict sentence} against the base (the same tag's G0 for specs with same_tag_base), and, for the
    overlap spec, vs_reference {metric: delta} against the all-layers run and gain_left (share of the reference
    meta-path gain that remains, computed). A file that is absent is listed under missing; a spec with no variant file
    has available False and shows no numbers."""
    from linkpred import compare
    out = []
    for e in EXPERIMENTS:
        entry = dict(id=e["id"], title=e["title"], question=e["question"], exploratory=e["exploratory"],
                     status="EXPLORATORY" if e["exploratory"] else "frozen protocol", metrics=[list(m) for m in e["metrics"]],
                     note_file=e.get("note_file"), rows=[], missing=[], sentences=[], overlap=bool(e.get("overlap")))
        fixed_base = None
        if "base" in e:
            bname, bres = _load(out_dir, *e["base"])
            fixed_base = bres
            entry["base"] = dict(label=f"{e['base'][0]}@{e['base'][1]}", file=bname if bres else None)
            if bres is None:
                entry["missing"].append(bname)
        ref = None
        if e.get("reference"):
            _, ref = _load(out_dir, *e["reference"])
        if "pairs" in e:
            _pair_rows(out_dir, e, entry)
            if e.get("hide_if_missing") and not entry["rows"]:
                continue                                              # e.g. D: hidden until its file exists
        for graph, tag in e.get("variants", []):
            name, res = _load(out_dir, graph, tag)
            if res is None:
                entry["missing"].append(name)
                continue
            base = fixed_base
            if "same_tag_base" in e:
                bname, base = _load(out_dir, e["same_tag_base"], tag)
                if base is None:
                    entry["missing"].append(bname)
            label = f"{graph}@{tag}"
            row = dict(graph=graph, tag=tag, label=label, file=name, dropped_layers=_dropped(res),
                       means={m: _mean(res, m) for m, _ in e["metrics"]}, deltas={})
            if base is not None:
                bl = f"{e['same_tag_base']}@{tag}" if "same_tag_base" in e else entry["base"]["label"]
                for m, _ in e["metrics"]:
                    d = compare.paired_delta(res, base, m)
                    if d["n"]:
                        row["deltas"][m] = dict(d, verdict=compare.verdict(d, label, bl, m))
                row["caveat"] = epochs_caveat(label, res, bl, base)
            if ref is not None and res is not ref:
                row["vs_reference"] = {m: d for m, _ in e["metrics"]
                                       if (d := compare.paired_delta(res, ref, m))["n"]}
                gain_all = row_gain = None
                mp = "auroc_metapath"
                if mp in row["deltas"] and fixed_base is not None:
                    gain_all = compare.paired_delta(ref, fixed_base, mp)["mean"]
                    row_gain = row["deltas"][mp]["mean"]
                if gain_all:
                    row["gain_left"] = row_gain / gain_all
            entry["rows"].append(row)
        entry["available"] = bool(entry["rows"])
        if entry["available"]:
            for r in entry["rows"]:
                for m, name in e["metrics"]:
                    d = r["deltas"].get(m)
                    if d:
                        entry["sentences"].append(f"{name}: {d['verdict']}")
            if entry["overlap"]:
                for r in entry["rows"]:
                    if r.get("gain_left") is not None and r["dropped_layers"]:
                        entry["sentences"].append(
                            f"With {', '.join(r['dropped_layers'])} removed, {_pct(r['gain_left'])} of the all-layers "
                            f"meta-path gain over Pharos alone remains.")
            entry["sentences"] += [r["caveat"] for r in entry["rows"] if r.get("caveat")]
        if e.get("share") and entry["available"]:
            entry["sentences"] += _share_sentences(e, entry)
        if entry["exploratory"] and entry["available"]:
            entry["sentences"].append("EXPLORATORY: not part of the frozen protocol; read next to the frozen R-GCN result, "
                                      "never instead of it.")
        out.append(entry)
    return out


def _pair_rows(out_dir, e, entry):
    """Takes the results folder, a spec with `pairs` and its entry. Appends one row per (variant, base) pair: the
    variant's means and its paired deltas against that pair's base (linkpred.compare.paired_delta + verdict). A missing
    variant or base file is listed under entry['missing'] and its row has no deltas (no variant: no row)."""
    from linkpred import compare
    gl = e.get("graph_labels") or {}
    for (vg, vt), (bg, bt) in e["pairs"]:
        name, res = _load(out_dir, vg, vt)
        if res is None:
            entry["missing"].append(name) if name not in entry["missing"] else None
            continue
        bname, base = _load(out_dir, bg, bt)
        if base is None and bname not in entry["missing"]:
            entry["missing"].append(bname)
        label, bl = f"{vg}@{vt}", f"{bg}@{bt}"
        row = dict(graph=vg, tag=vt, label=f"{label} vs {bl}", base_label=bl, base_file=bname if base else None,
                   file=name, dropped_layers=_dropped(res), means={m: _mean(res, m) for m, _ in e["metrics"]},
                   deltas={}, graph_notes={g: gl[g] for g in (vg, bg) if g in gl})
        if base is not None:
            for m, _ in e["metrics"]:
                d = compare.paired_delta(res, base, m)
                if d["n"]:
                    row["deltas"][m] = dict(d, verdict=compare.verdict(d, label, bl, m))
            row["caveat"] = epochs_caveat(label, res, bl, base)
        entry["rows"].append(row)
    for g, text in gl.items():
        if any(g in r["graph_notes"] for r in entry["rows"]):
            entry["sentences"].append(f"{g} = {text}.")


def _share_sentences(e, entry):
    """Takes a `share` spec (rows: without-layer vs G0, then without-layer vs the full graph) and its entry. Returns the
    sentence on the layer's share of the full gain, computed from the two paired deltas: share = -d2 / (d1 - d2), where
    d1 = without - G0 and d2 = without - full (so d1 - d2 = full - G0). [] when either delta or the gain is missing."""
    m, layer = e["share"]["metric"], e["share"]["layer"]
    if len(entry["rows"]) < 2:
        return []
    d1, d2 = (entry["rows"][0]["deltas"].get(m), entry["rows"][1]["deltas"].get(m))
    if not d1 or not d2 or d1["mean"] is None or d2["mean"] is None:
        return []
    gain = d1["mean"] - d2["mean"]
    if gain <= 0:
        return [f"The full graph does not beat Pharos alone on {m} in these files, so no share of a gain is computed."]
    share = -d2["mean"] / gain
    if share <= 0:
        return [f"Removing the {layer} does not lower {m} ({d2['mean']:+.4f}); it carries none of the "
                f"{gain:+.4f} gain."]
    return [f"The {layer} carries about {round(100 * min(share, 1.0))}% of the gain over Pharos alone on {m} "
            f"(full graph {gain:+.4f} over G0; without it {d1['mean']:+.4f}; computed from the two paired deltas)."]


# ---------------------------------------------------------------------------------------------- PK3 (U6, AC-05)
# The Numbers tab opens with ONE table in user words. Its three rows are the files of the headline (pipelines/headline.py
# HISTORY[0], the shipped graph): the same model (GNN + paths), the same held-out pairs, the same folds.
LEAD_ROWS = [("Pharos alone", "G0_B_kfold@diag_gnnpath_1m_gpu.json"),
             ("Agent-built (Pharos + public sources)", "G2_B_kfold@diag_gnnpath_1m_cs9.json"),
             ("Agent-built and pruned", "G3_B_kfold@diag_gnnpath_1m_cs9.json")]
LEAD_METRIC, LEAD_MODEL = "auroc_gnnpath", "GNN + paths"
PRUNE_REPORT = Path("outputs") / "pharos" / "prune_report.md"
SPOTCHECK_RULE = Path("outputs") / "pharos" / "spotcheck_rule.md"
SPOTCHECK_RESULT = Path("outputs") / "pharos" / "spotcheck_blind_result.md"
PPI_CHECK = Path("outputs") / "pharos" / "ppi_corroboration.md"

# Plain names for what the judges' run tables are labelled with (graph codes, run tags). Words only, no numbers; a tag that
# is not listed is shown as "another run" by the page.
WORDS = {
    "graphs": {"G0": "Pharos alone", "G1": "Pharos and its ontologies", "G2": "the agent-built graph", "G3": "the agent-built graph after pruning",
               "G4": "the pruned graph with the trial layer", "G2R": "the agent-built graph with as many edges removed at random"},
    "metrics": {"auroc": "AUROC", "auroc_metapath": "meta-path AUROC", "auroc_gnnpath": "GNN + paths AUROC", "auroc_cn": "common-neighbour AUROC",
                "metapath_coverage": "share of pairs with a path", "auroc_gnnhead": "GNN-head AUROC", "auroc_pathhead": "path-head AUROC"},
    "tags": {"frozen_a": "frozen benchmark", "frozen_a_g0_1m": "frozen benchmark at the 1M cap",
             "diag_gnnpath_1m": "path-aware run, 4 November build", "diag_gnnpath_1m_gpu": "path-aware run on the GPU",
             "diag_gnnpath_1m_release": "path-aware run, 7 November build", "diag_gnnpath_1m_cs9": "path-aware run, shipped graph",
             "diag_gnnpath_1m_nomondo": "path-aware run without the MONDO hierarchy",
             "diag_rebuilt_1m": "meta-path run, rebuilt graphs", "diag_release_metapath": "meta-path run, 7 November build",
             "diag_e005_noot": "meta-path run without Open Targets", "diag_e005_nochembl37": "meta-path run without ChEMBL",
             "diag_e005_nomondo": "meta-path run without MONDO", "diag_e005_nohpo": "meta-path run without HPO",
             "diag_e005_nogo": "meta-path run without GO", "diag_kge_rotate_d256": "RotatE run",
             "diag_kge_rotate_gpu": "RotatE run (512 dimensions)", "diag_kge_rotate_d256_e009": "RotatE random-pruning control"},
}


def _n_pairs(res):
    """Takes a parsed result. Returns the number of held-out test pairs summed over its folds (n_test per fold), or None."""
    n = [f.get("n_test") for s in res.get("per_seed", []) for f in s.get("folds", [])]
    return int(round(sum(n))) if n and all(isinstance(x, (int, float)) for x in n) else None


def lead(out_dir):
    """Takes the linkpred results folder (or list, first wins). Returns None when the Pharos-alone file is missing, else
    {model, metric, rows, pairs, folds, same_pairs, files, sentence}: rows = [{label, file, auroc (mean over folds),
    delta (linkpred.compare.paired_delta against Pharos alone; None for the base or a missing file), folds ([{fold, auroc}]
    per fold, linkpred.compare.fold_values)}]. pairs = held-out
    test pairs summed over the folds of the base file; same_pairs = every row was scored on the base's test set (digest).
    The sentence is built from the numbers (CLAUDE.md rule 7)."""
    from linkpred import compare
    loaded = []
    for label, name in LEAD_ROWS:
        try:
            loaded.append((label, name, json.loads(_path(out_dir, name).read_text())))
        except (OSError, ValueError):
            loaded.append((label, name, None))
    base = loaded[0][2]
    if base is None:
        return None
    rows, same = [], True
    for i, (label, name, res) in enumerate(loaded):
        row = dict(label=label, file=name if res is not None else None, auroc=_mean(res, LEAD_METRIC) if res else None, delta=None,
                   # FIX1 F10 (G1-P5-01): the run's AUROC per fold, so the first screen's number can be followed to its folds
                   folds=[dict(fold=i + 1, auroc=round(v, 4)) for i, (_, v) in enumerate(sorted(
                       compare.fold_values(res, LEAD_METRIC).items(), key=lambda kv: str(kv[0])))] if res else [])
        if res is not None and i:
            d = compare.paired_delta(res, base, LEAD_METRIC)
            row["delta"] = d if d["n"] else None
            same = same and bool(d["digest_match"])
        rows.append(row)
    folds = _n_folds(base)
    pairs = _n_pairs(base)
    parts = []
    for r in rows[1:]:
        d = r["delta"]
        if not d or d["ci_low"] is None:
            continue
        word = "higher" if d["ci_low"] > 0 else "lower" if d["ci_high"] < 0 else "not clearly different"
        parts.append(f"{r['label']}: {d['mean']:+.3f} ({word}; 95% interval {d['ci_low']:+.3f} to {d['ci_high']:+.3f}, "
                     f"higher in {d['wins']} of {d['n']} folds)")
    sentence = (f"Same model ({LEAD_MODEL}), the same {pairs:,} held-out curated gene-disease pairs, {folds} folds; "
                f"differences from Pharos alone are paired over the folds. " if pairs else "") + "; ".join(parts) + ("." if parts else "")
    return dict(model=LEAD_MODEL, metric=LEAD_METRIC, rows=rows, pairs=pairs, folds=folds, same_pairs=same,
                files=[r["file"] for r in rows if r["file"]], sentence=sentence)


def _read(roots, rel):
    p, _ = _first(roots, rel)
    return (p.read_text(encoding="utf-8"), rel.name) if p is not None else (None, rel.name)


def spot_check(roots):
    """Takes the roots. Returns None when the pre-registered rule is not there, else {rule_pruned_min, rule_kept_max,
    n_pruned, n_kept, result (None until the scored result file exists, else 'PASS' / 'FAIL'), files, sentence}. Every number
    is read from outputs/pharos/spotcheck_rule.md (the RULE: line and the sample size) and, once it exists, from the result
    file written by scripts/score_spotcheck.py."""
    text, name = _read(roots, SPOTCHECK_RULE)
    if text is None:
        return None
    m = re.search(r"RULE: prune_unsupported_min=(\d+); keep_unsupported_max=(\d+)", text)
    s = re.search(r"Sample: (\d+) pruned \+ (\d+) kept", text)
    if not m:
        return None
    pmin, kmax = int(m.group(1)), int(m.group(2))
    npr, nk = (int(s.group(1)), int(s.group(2))) if s else (None, None)
    res_text, res_name = _read(roots, SPOTCHECK_RESULT)
    result = None
    counts = {}
    if res_text:
        # Only the scorer's own result line counts ("- Result: **PASS**" / "- Result: **FAIL**"). The rule line it writes
        # first ("Rule (pre-registered): PASS if ...") must never be read as the result (gate 1, G1-AC-01).
        r = re.search(r"Result: \*\*(PASS|FAIL)\*\*", res_text)
        result = r.group(1) if r else None
        counts = spot_check_counts(res_text)
    size = f"{npr + nk} links ({npr} pruned, {nk} kept)" if npr else "the sample"
    rule = (f"pass if at least {pmin} of the {npr or ''} pruned links are judged unsupported and at most {kmax} of the "
            f"{nk or ''} kept links are").replace("  ", " ")
    if result:
        sentence = f"Blinded spot check of {size}, rule fixed before anyone rated a row ({rule}): {result}."
        if "pruned_unsupported" in counts and "kept_unsupported" in counts:          # FIX2 F2: what the result rests on
            sentence += (f" {counts['pruned_unsupported']} of the {counts['pruned_rated']} pruned and {counts['kept_unsupported']} "
                         f"of the {counts['kept_rated']} kept links were judged unsupported.")
        if "owner_agree" in counts:
            confirmed = counts["owner_agree"] >= counts.get("owner_needed", counts["owner_rows"] + 1)
            raters = counts.get("raters")
            sentence += (f" {_count_word(raters).capitalize() + ' a' if raters else 'A'}utomated raters labelled the rows; on the {counts['owner_rows']} rows the scientist rated "
                         f"blind, she agreed with them on {counts['owner_agree']}, so the raters' labels are "
                         f"{'confirmed' if confirmed else 'not confirmed'} by a human.")
    else:
        sentence = (f"Blinded spot check of {size}: the rule was fixed before anyone rated a row ({rule}); the rating is "
                    "not finished, so no result is shown yet.")
    return dict(rule_pruned_min=pmin, rule_kept_max=kmax, n_pruned=npr, n_kept=nk, result=result, **counts,
                files=[name] + ([res_name] if res_text else []), sentence=sentence)


def spot_check_counts(text):
    """Takes the text of outputs/pharos/spotcheck_blind_result.md. Returns the counts it states (keys only for the lines
    found): raters (how many are named on the Raters line), pruned_unsupported / pruned_rated and kept_unsupported / kept_rated ("Pruned edges judged unsupported: **71 of
    100**"), owner_agree / owner_rows (the owner's blind labels against the raters' majority, "7 of 39 agree") and
    owner_needed (the pre-registered level, "(32 of 40 agreeing rows)")."""
    out = {}
    for key, word in (("pruned", "Pruned"), ("kept", "Kept")):
        m = re.search(rf"{word} edges judged unsupported: \*\*(\d+) of (\d+)\*\*", text)
        if m:
            out[f"{key}_unsupported"], out[f"{key}_rated"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"Owner's blind labels against the machine label[^:]*: (\d+) of (\d+) agree", text)
    if m:
        out["owner_agree"], out["owner_rows"] = int(m.group(1)), int(m.group(2))
        n = re.search(r"pre-registered level \((\d+) of (\d+) agreeing rows\)", text)
        if n:
            out["owner_needed"] = int(n.group(1))
    r = re.search(r"^Raters: (.+)$", text, re.M)
    if r:
        out["raters"] = len(re.findall(r"\b[A-Z] = `", r.group(1)))
    return out


def _count_word(n):
    """Takes a small whole number. Returns it as a word for one to ten ('three'), else the digits."""
    words = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
    return words[n] if isinstance(n, int) and 0 <= n < len(words) else str(n)


def ppi_check(roots):
    """Takes the roots. Returns {sentence, file} with the bold headline line of outputs/pharos/ppi_corroboration.md (the
    independent IntAct check of pruned versus kept protein-protein rows; no model involved), or None."""
    text, name = _read(roots, PPI_CHECK)
    if not text:
        return None
    m = re.search(r"\*\*Headline: (.+?)\*\*(.*)", text)
    if not m:
        return None
    return dict(sentence=(m.group(1) + m.group(2)).strip().rstrip("."), file=name)


AUDIT_RESULT = Path("outputs") / "pharos" / "audit_sample_result.md"


def audit_sample(roots):
    """Takes the roots. Returns {pruned_n, pruned_unsupported, kept_n, kept_unsupported, rows_all_agree, rows, sentence,
    file} read from outputs/pharos/audit_sample_result.md (scripts/score_spotcheck.py --audit: the separate 500 + 500
    sample, machine raters only, no pass rule, never mixed into the spot check), or None when the file or its verdict
    table is missing."""
    text, name = _read(roots, AUDIT_RESULT)
    if not text:
        return None
    cells = {}
    for v in ("prune", "keep"):
        m = re.search(rf"^\| {v} \| (\d+) \| (\d+) = ([\d.]+)% \(([\d.]+)% to ([\d.]+)%\) \|", text, re.M)
        if not m:
            return None
        cells[v] = (int(m.group(1)), int(m.group(2)), m.group(3), m.group(4), m.group(5))
    a = re.search(r"^\| all three \| (\d+) \| (\d+) ", text, re.M)
    (pn, pu, ps, plo, phi), (kn, ku, ks, klo, khi) = cells["prune"], cells["keep"]
    sentence = (f"A separate audit of {pn + kn:,} further links drawn at random ({pn:,} pruned, {kn:,} kept), each checked "
                "by three automated raters that never saw the pruner's verdict: "
                f"{pu:,} of the {pn:,} pruned links ({ps}%, 95% interval {plo}% to {phi}%) and {ku:,} of the {kn:,} kept "
                f"links ({ks}%, {klo}% to {khi}%) were judged unsupported")
    if a:
        sentence += f"; all three raters agreed on {int(a.group(2)):,} of {int(a.group(1)):,} links"
    sentence += ". This audit has no pass rule and is reported apart from the spot check."
    spot_text, _ = _read(roots, SPOTCHECK_RESULT)
    mix = source_mix_sentence(per_source(spot_text or ""), per_source(text))
    if mix:
        sentence += " " + mix
    return dict(pruned_n=pn, pruned_unsupported=pu, kept_n=kn, kept_unsupported=ku,
                rows_all_agree=int(a.group(2)) if a else None, rows=int(a.group(1)) if a else pn + kn,
                sentence=sentence, file=name)


STRING_SOURCE, TEXT_MINED_SOURCE = "STRINGDB (infores:string)", "TIN-X (infores:pharos)"


def per_source(text):
    """Takes the text of a scored result file (spot check or audit). Returns {(verdict, source): (rows, unsupported)} from
    its 'Per source' table, or {} when there is none. Values are (rows, unsupported, supported)."""
    out = {}
    for m in re.finditer(r"^\| (prune|keep) \| ([^|]+?) \| (\d+) \| (\d+) \| [\d.]+% \([^|]*\) \| (\d+) \|", text or "", re.M):
        out[(m.group(1), m.group(2).strip())] = (int(m.group(3)), int(m.group(4)), int(m.group(5)))
    return out


def source_mix_sentence(spot, audit, close=10.0):
    """Takes per_source() of the spot check and of the audit. Returns the sentence of FIX2 F3 (G2-AC-04): within the STRING
    protein-protein rows, pruned and kept rows judged unsupported, per sample, and how many text-mined disease rows each
    sample holds; '' when a sample lacks the STRING rows. 'similar' is said only when every pruned-kept gap is under
    `close` percentage points; "little evidence for any STRING row" only when under a quarter of the STRING rows of every
    sample and verdict were judged supported."""
    parts, gaps = [], []
    for label, t in (("spot check", spot), ("audit", audit)):
        pr, kp = t.get(("prune", STRING_SOURCE)), t.get(("keep", STRING_SOURCE))
        if not pr or not kp or not pr[0] or not kp[0]:
            continue
        gaps.append(abs(100.0 * pr[1] / pr[0] - 100.0 * kp[1] / kp[0]))
        parts.append(f"{label} {pr[1]:,} of {pr[0]:,} pruned against {kp[1]:,} of {kp[0]:,} kept")
    if not parts:
        return ""
    how = "at similar rates" if all(g < close for g in gaps) else "at different rates"
    txt = f"Within protein-protein rows from STRING, pruned and kept rows are judged unsupported {how} ({'; '.join(parts)})"
    tm = [(label, t.get(("prune", TEXT_MINED_SOURCE), (0, 0))[0], t.get(("keep", TEXT_MINED_SOURCE), (0, 0))[0])
          for label, t in (("spot check", spot), ("audit", audit)) if t]
    if tm and all(k > p for _, p, k in tm):
        txt += ("; most of the overall difference comes from the kept sample holding many more text-mined disease links ("
                + "; ".join(f"{label} {k:,} kept against {p:,} pruned" for label, p, k in tm) + ")")
    shares = [t[("prune" if v == "prune" else "keep", STRING_SOURCE)] for t in (spot, audit) for v in ("prune", "keep")
              if (v, STRING_SOURCE) in t]
    if shares and all(len(x) > 2 and x[0] and x[2] / x[0] < 0.25 for x in shares):   # computed: under a quarter supported
        txt += (". The raters find little evidence for any STRING row, so this check says little about protein-protein "
                "pruning; the IntAct comparison is the model-free check for those")
    return txt + "."


def injected_errors(roots):
    """Takes the roots. Returns {auprc, precision, recall, sentence, file} read from the quoted injected-error table of
    outputs/pharos/prune_report.md (first data row of 'What the injected-error test says'), or None."""
    text, name = _read(roots, PRUNE_REPORT)
    if not text or "injected-error test" not in text:
        return None
    sec = text.split("injected-error test", 1)[1]
    m = re.search(r"^\| [^|]+\| ([\d.]+) \| [\d.]+ \| [\d.]+ \| ([\d.]+) \| ([\d.]+) \|", sec, re.M)
    if not m:
        return None
    a, p, r = (float(x) for x in m.groups())
    return dict(auprc=a, precision=p, recall=r, file=name,
                sentence=(f"Stated limit: on deliberately injected wrong edges the pruner is weak (AUPRC {a:.3f}, precision "
                          f"{p:.3f}, recall {r:.3f}). Those injected errors keep the original source and score, so the test "
                          "only sees implausible graph structure; it cannot see a wrong edge from a trusted source."))


def pruning_evidence(root=ROOT, fallback_root=None):
    """Takes the data root and the optional fallback root. Returns the pruning story in the order of review finding AC-05:
    {verdicts: {scored, pruned, pruned_share, all_links, all_removed, sentence, files} or None, spot_check, ppi_check,
    injected (the stated limit)}; the calibration gradient itself is numbers()['calibration']. Each part is None when its
    file is missing. The verdict numbers are headline.prune_numbers' (the same files as the first screen)."""
    roots = _roots(root, fallback_root)
    import headline
    p = {}
    for r in roots:
        q = headline.prune_numbers(r)
        for k, v in q.items():
            if p.get(k) is None:
                p[k] = v
    verdicts = None
    if p.get("scored") and p.get("pruned") is not None:
        s = (f"Every one of the {p['scored']:,} Pharos links has a keep-or-prune verdict, a confidence and its reasons: "
             f"{p['scored'] - p['pruned']:,} kept and {p['pruned']:,} marked prune ({p['pruned_share']:.2f}%). Pruned links "
             "are shown greyed, never deleted.")
        if p.get("g2_edges") and p.get("g3_removed") is not None:
            s += (f" Across all layers the pruned graph has {p['g3_removed']:,} fewer links "
                  f"({100.0 * p['g3_removed'] / p['g2_edges']:.2f}% of {p['g2_edges']:,}).")
        verdicts = dict(scored=p["scored"], pruned=p["pruned"], pruned_share=p["pruned_share"], all_links=p.get("g2_edges"),
                        all_removed=p.get("g3_removed"), sentence=s,
                        files=[PRUNE_REPORT.name])
    return dict(verdicts=verdicts, spot_check=spot_check(roots), ppi_check=ppi_check(roots), injected=injected_errors(roots),
                audit_sample=audit_sample(roots))


def _roots(root, fallback_root):
    roots = [Path(root)]
    if fallback_root is not None and Path(fallback_root).resolve() != roots[0].resolve():
        roots.append(Path(fallback_root))
    return roots


def report_files():
    """FIX1 F7 (gate 1, G1-AC-02): the non-frozen report files whose committed copy (in the image) is read BEFORE the data
    root's copy. The data root is synced from storage and held a calibration file from an older build (prune n 505) under a
    headline computed from the committed one (prune n 280); these files are written with the verdicts and committed with
    them, so the copy the image was built with is the current one."""
    return {CALIBRATION, CALIBRATION.with_suffix(".md"), PRUNE_REPORT, PPI_CHECK}


def _ordered(roots, rel):
    """Takes the roots (data root first, then the image) and a relative path. Returns the roots in the order to read `rel`:
    image first for report_files(), else as given."""
    return list(reversed(roots)) if Path(rel) in report_files() and len(roots) > 1 else roots


def _first(roots, rel):
    """Takes the roots (first wins) and a relative path. Returns (Path, index of the root it was found in) or (None, None)."""
    order = _ordered(roots, rel)
    for r in order:
        if (r / rel).is_file():
            return r / rel, roots.index(r)
    return None, None


def numbers(root=ROOT, fallback_root=None):
    """Takes the data root and an optional fallback root (this repo's committed outputs/, shipped in the image).
    Returns {benchmark: {runs, comparisons, protocol, tags}, calibration, experiments, sources, fallback_files}:
    sources maps each file name the page cites to its relative path (only files that exist in either root);
    fallback_files lists the names that were read from fallback_root: because the data root lacks them, or (FIX1 F7) because
    they are report_files(), whose image copy is read first."""
    roots = _roots(root, fallback_root)
    dirs = [r / LINKPRED for r in roots]
    any_dir = any(d.is_dir() for d in dirs)
    runs = frozen_runs(dirs) if any_dir else []
    comps = frozen_comparisons(dirs) if runs else []
    cal_path, cal_i = _first(roots, CALIBRATION)
    cal = calibration(cal_path) if cal_path else None
    exps = experiments(dirs) if any_dir else []
    names = {r["file"]: LINKPRED / r["file"] for r in runs}
    names.update({f: LINKPRED / f for f in experiment_files()})
    names.update(OTHER_FILES)
    names.update({f: LINKPRED / f for _, f in LEAD_ROWS})
    sources, fallback = {}, []
    for n, rel in names.items():
        p, i = _first(roots, rel)
        if p is not None:
            sources[n] = str(rel)
            if i:
                fallback.append(n)
    return dict(benchmark=dict(runs=runs, comparisons=comps, protocol=PROTOCOL_TEXT,
                               tags=sorted({r["tag"] for r in runs}), lead=lead(dirs) if any_dir else None,
                               pruning=pruning_evidence(root, fallback_root), words=WORDS),
                calibration=cal, experiments=exps, sources=sources, fallback_files=sorted(fallback),
                neptune=__import__("routes_neptune").numbers_row(root, fallback_root))   # PK15: the live-load sentence or None


def source_file(root, name, fallback_root=None):
    """Takes the data root, a file name from numbers()['sources'] and the optional fallback root. Returns its Path when
    the name is a frozen result file, an experiment file or one of OTHER_FILES and it exists under root (else under
    fallback_root); None otherwise (nothing outside the allow-list is served)."""
    if FROZEN_RE.match(name or "") or name in experiment_files() or name in {f for _, f in LEAD_ROWS}:
        rel = LINKPRED / name
    elif name in OTHER_FILES:
        rel = OTHER_FILES[name]
    else:
        return None
    return _first(_roots(root, fallback_root), rel)[0]


if __name__ == "__main__":
    print(json.dumps(numbers(), indent=1, default=str)[:4000])
