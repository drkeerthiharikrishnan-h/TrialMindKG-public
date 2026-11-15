"""
Build a knowledge graph from one source card with the generic builder (pipelines/kgbuilder/engine.py).

Usage:   python pipelines/build_kg.py sources/pharos.yaml
Writes:  data/kg/<name>/nodes.tsv.gz, edges.tsv.gz (KGX TSV; git-ignored, large)
         outputs/<name>/summary.md, outputs/<name>/unmapped.tsv (committed: counts, so changes show in git)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kgbuilder import engine  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main(card_path):
    card = engine.load_card(card_path)
    b = engine.build(card)
    nodes_path, edges_path = engine.export(b, ROOT / "data" / "kg" / card["name"])
    out = ROOT / "outputs" / card["name"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.md").write_text(engine.summary_markdown(b))
    b.unmapped.to_csv(out / "unmapped.tsv", sep="\t", index=False)
    print(f"{len(b.nodes):,} nodes, {len(b.edges):,} edges -> {nodes_path.parent}")
    print(f"summary: {out / 'summary.md'}")
    for p in b.problems:
        print("PROBLEM:", p)
    return b


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
