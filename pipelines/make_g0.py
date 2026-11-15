"""
Build G0, the plain Pharos (L0) graph, as a PyG HeteroData file (EXECUTION_PLAN.md Phase 1a).

Usage:   python pipelines/make_g0.py            (after python pipelines/build_kg.py sources/pharos.yaml)
Reads:   data/kg/pharos/nodes.tsv.gz, edges.tsv.gz
Writes:  outputs/g0/heterodata.pt (git-ignored), outputs/g0/summary.md (committed)
The work is done by make_graphs.make("G0"); G1-G4 are built with python pipelines/make_graphs.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_graphs  # noqa: E402

if __name__ == "__main__":
    rep = make_graphs.make("G0")
    if not rep["written"]:
        sys.exit("G0 not written: run python pipelines/build_kg.py sources/pharos.yaml first")
    print(f"G0: {rep['n_nodes']:,} nodes, {rep['n_edges']:,} edges -> outputs/g0/heterodata.pt")
