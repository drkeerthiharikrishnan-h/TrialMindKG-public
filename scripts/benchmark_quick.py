#!/usr/bin/env python3
"""
The benchmark in a minute: every paired comparison the headline rests on, recomputed from the committed result files with
pipelines/linkpred/compare.py (no torch, no data/), then the headline itself.

    python3 scripts/benchmark_quick.py

  rows(root) -> [(name, metric, variant file, base file, n, mean, ci_low, ci_high, wins)]  (missing files: None values)
Exit code 0 when every comparison was computed, 1 when a result file is missing.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
import headline  # noqa: E402


def rows(root=ROOT):
    out = []
    for name, d in headline.comparisons(root).items():
        v, b, m = headline.COMPARISONS[name]
        out.append((name, m, v, b) + ((d["n"], d["mean"], d["ci_low"], d["ci_high"], d["wins"]) if d else (None,) * 5))
    return out


def main():
    rs = rows()
    print(f"{'comparison':18} {'metric':14} {'mean':>8} {'95% CI':>20} {'folds':>6}  variant - base")
    for name, m, v, b, n, mean, lo, hi, wins in rs:
        if n is None:
            print(f"{name:18} {m:14} {'missing':>8}{'':21}{'':7}  {v} - {b}")
        else:
            print(f"{name:18} {m:14} {mean:+8.4f} {lo:+9.4f} to {hi:+8.4f} {wins:>3}/{n}  {v} - {b}")
    print("\n" + headline.headline(ROOT)["text"])
    return 1 if any(r[4] is None for r in rs) else 0


if __name__ == "__main__":
    sys.exit(main())
