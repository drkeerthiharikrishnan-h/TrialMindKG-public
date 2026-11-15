"""Rewrite a layer's build summary (outputs/<layer>/summary.md) from the built files on disk, without a rebuild (CS-2).

build_kg.py writes summary.md with kgbuilder.engine.summary_markdown(build) at build time. When the committed summary
is from an older build (pharos: the 25 Oct 1,650,119-edge cloud build; the shipped layer has 26,462,915 edges), this
script feeds the SAME function the layer's nodes.tsv.gz / edges.tsv.gz and the committed unmapped.tsv. Sections that
only a build knows (multi-value records, identifier routes, build problems) are not recomputed; the Problems section
says so instead of claiming "None".

    python scripts/summary_from_layer.py sources/pharos.yaml [--out outputs/pharos/summary.md]

  layer_build(card, kg_dir, unmapped_path, chunksize) -> an object with the attributes summary_markdown reads
"""
import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
from pandas.api.types import union_categoricals

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
from kgbuilder import engine  # noqa: E402

NOTE = ("summary regenerated from data/kg/{name}/nodes.tsv.gz and edges.tsv.gz by scripts/summary_from_layer.py "
        "(no rebuild); the build-time sections (multi-value records, identifier routes, build problems) are not "
        "recomputed here")


def layer_build(card, kg_dir, unmapped_path=None, chunksize=2_000_000):
    """Takes the source card dict, the layer folder and the unmapped.tsv path. Returns a namespace with card, nodes
    [category], edges [id (placeholder), predicate, primary_knowledge_source (categorical), n_records], unmapped,
    multi_value [], routes [], problems [NOTE]. Edges are read in chunks (three columns only)."""
    kg_dir = Path(kg_dir)
    nodes = pd.read_csv(kg_dir / "nodes.tsv.gz", sep="\t", dtype=str, usecols=["category"], keep_default_na=False)
    preds, pks, recs = [], [], []
    for c in pd.read_csv(kg_dir / "edges.tsv.gz", sep="\t", dtype=str, keep_default_na=False, chunksize=chunksize,
                         usecols=["predicate", "primary_knowledge_source", "n_records"]):
        preds.append(c["predicate"].astype("category"))
        pks.append(c["primary_knowledge_source"].astype("category"))
        recs.append(pd.to_numeric(c["n_records"], errors="coerce").fillna(0).astype(np.int64).to_numpy())
    n = sum(len(r) for r in recs)
    edges = pd.DataFrame({"id": np.zeros(n, dtype=np.int8),
                          "predicate": pd.Categorical(union_categoricals(preds)) if preds else pd.Categorical([]),
                          "primary_knowledge_source": pd.Categorical(union_categoricals(pks)) if pks else pd.Categorical([]),
                          "n_records": np.concatenate(recs) if recs else np.zeros(0, dtype=np.int64)})
    unmapped = (pd.read_csv(unmapped_path, sep="\t") if unmapped_path and Path(unmapped_path).exists()
                else pd.DataFrame(columns=["edge_spec", "reason", "rows"]))
    return SimpleNamespace(card=card, nodes=nodes, edges=edges, unmapped=unmapped, multi_value=[], routes=[],
                           problems=[NOTE.format(name=card["name"])])


def summary_text(b):
    """engine.summary_markdown with observed=True grouping (categorical columns) - same table as a build writes."""
    edges = b.edges
    b2 = SimpleNamespace(**{**vars(b), "edges": edges.assign(predicate=edges["predicate"].astype(str),
                                                             primary_knowledge_source=edges["primary_knowledge_source"].astype(str))})
    return engine.summary_markdown(b2)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("card")
    p.add_argument("--kg-dir", default=None, help="default data/kg/<name>")
    p.add_argument("--unmapped", default=None, help="default outputs/<name>/unmapped.tsv")
    p.add_argument("--out", default=None, help="default outputs/<name>/summary.md")
    a = p.parse_args(argv)
    card = engine.load_card(a.card)
    name = card["name"]
    b = layer_build(card, a.kg_dir or ROOT / "data" / "kg" / name, a.unmapped or ROOT / "outputs" / name / "unmapped.tsv")
    out = Path(a.out or ROOT / "outputs" / name / "summary.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(summary_text(b))
    print(f"{len(b.nodes):,} nodes, {len(b.edges):,} edges -> {out}")


if __name__ == "__main__":
    sys.exit(main())
