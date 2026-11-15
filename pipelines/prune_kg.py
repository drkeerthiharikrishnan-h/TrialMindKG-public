"""
Score every edge of a built graph with pruning v1 (pipelines/kgbuilder/prune.py).

Usage:   python pipelines/prune_kg.py sources/pharos.yaml      (after python pipelines/build_kg.py <card>)
Writes:  data/kg/<name>/edge_verdicts.tsv.gz  (one row per edge: confidence, keep/prune, reasons; git-ignored)
         outputs/<name>/prune_report.md, outputs/<name>/prune_metrics.json  (committed)
"""
import gzip, json, sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kgbuilder import engine, prune  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main(card_path, seed=0):
    card = engine.load_card(card_path)
    kg = ROOT / "data" / "kg" / card["name"]
    edges = pd.read_csv(kg / "edges.tsv.gz", sep="\t", dtype=str, low_memory=False)
    nodes = pd.read_csv(kg / "nodes.tsv.gz", sep="\t", dtype=str, usecols=["id", "category"])
    verdicts, metrics = prune.score(edges, nodes, card.get("prune") or {}, seed=seed)
    with gzip.GzipFile(kg / "edge_verdicts.tsv.gz", "wb", mtime=0) as gz:
        gz.write(verdicts.to_csv(sep="\t", index=False).encode())
    out = ROOT / "outputs" / card["name"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "prune_report.md").write_text(prune.report_markdown(card["name"], metrics))
    (out / "prune_metrics.json").write_text(json.dumps(metrics, indent=1, sort_keys=True) + "\n")
    print(prune.report_markdown(card["name"], metrics))
    return verdicts, metrics


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
