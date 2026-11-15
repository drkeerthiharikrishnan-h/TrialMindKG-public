"""Score a built layer with the CANDIDATE pruner settings (prune_compare's "all three": trust weight 0, SVD 64 dimensions,
plausibility per predicate group) WITHOUT touching the shipped v1 verdicts or the frozen graph.

    .venv/bin/python scripts/prune_v2_candidate.py sources/pharos.yaml [--seed 0]

Reads  data/kg/<name>/{nodes,edges}.tsv.gz (the built layer, same as prune_kg.py)
Writes data/kg/<name>/edge_verdicts_v2.tsv.gz        (candidate verdicts; prune_kg.py writes edge_verdicts.tsv.gz = v1, untouched)
       outputs/<name>/prune_report_v2.md, prune_metrics_v2.json
Track 2 of docs/archive/PLAN_FINAL_WEEK_2026-11-07.md (owner decision 7 Nov): v2 is a candidate until the owner's spot check compares it
with v1 on real edges. Nothing downstream (G3, index, exports) reads the _v2 files.
"""
import gzip
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import pandas as pd  # noqa: E402
import yaml  # noqa: E402

from kgbuilder import prune  # noqa: E402

V2 = {"trust_weight": 0.0, "svd_k": 64, "plausibility_by": "predicate_group"}


def main(card_path, seed=0):
    card = yaml.safe_load(Path(card_path).read_text())
    kg = ROOT / "data" / "kg" / card["name"]
    t0 = time.time()
    edges = pd.read_csv(kg / "edges.tsv.gz", sep="\t", dtype=str, keep_default_na=False)
    nodes = pd.read_csv(kg / "nodes.tsv.gz", sep="\t", dtype=str, keep_default_na=False)
    print(f"{card['name']}: {len(edges):,} edges, {len(nodes):,} nodes loaded in {time.time() - t0:.0f}s", flush=True)
    cfg = {**(card.get("prune") or {}), **V2}
    verdicts, metrics = prune.score(edges, nodes, cfg, seed=seed)
    metrics["candidate_settings"] = V2
    with gzip.GzipFile(kg / "edge_verdicts_v2.tsv.gz", "wb", mtime=0) as gz:
        verdicts.to_csv(gz, sep="\t", index=False)
    out = ROOT / "outputs" / card["name"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "prune_report_v2.md").write_text("# CANDIDATE pruner v2 (trust 0, SVD 64, plausibility per predicate group) - not shipped\n\n"
                                            + prune.report_markdown(card["name"] + " (v2 candidate)", metrics))
    (out / "prune_metrics_v2.json").write_text(json.dumps(metrics, indent=1, sort_keys=True, default=str) + "\n")
    vc = verdicts["verdict"].value_counts()
    print(f"v2 verdicts: {dict(vc)}; share pruned {vc.get('prune', 0) / len(verdicts):.4f}; "
          f"{(time.time() - t0) / 60:.1f} min -> {kg / 'edge_verdicts_v2.tsv.gz'}", flush=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    seed = int(args[args.index("--seed") + 1]) if "--seed" in args else 0
    main(args[0], seed)
