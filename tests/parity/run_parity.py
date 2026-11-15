"""
Run the 25-question parity suite against real backends (Claude Science, with NVIDIA_API_KEY in .env):

    python tests/parity/run_parity.py                 offline stand-in, then hosted NIM if a key is set; prints the agreement
    python tests/parity/run_parity.py --warm          hosted run that stores every answer in data/snapshots_llm: afterwards the
                                                      pinned questions are answered from that cache without the API
    python tests/parity/run_parity.py --replay        hosted backend in TMKG_SNAPSHOT_MODE=offline: only cached answers; a
                                                      question not warmed fails visibly (it does NOT fall back to the stand-in)
    python tests/parity/run_parity.py --offline-only  just the stand-in (no network, what the unit tests do)

Writes outputs/eval/parity_<labels>.json (rows) and .md (the table). Run `bin/agent-cli nim-check` first: a model id that
answers 404 for the account fails every question. Not a unit test: it needs the key (the unit tests stub the endpoint).
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipelines"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import parity_suite as ps  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--offline-only", action="store_true")
    ap.add_argument("--warm", action="store_true", help="hosted run; every answer is stored in data/snapshots_llm")
    ap.add_argument("--replay", action="store_true", help="hosted backend, cached answers only (no network)")
    ap.add_argument("--out", default=str(ROOT / "outputs" / "eval"))
    a = ap.parse_args(argv)
    if a.replay:
        os.environ["TMKG_SNAPSHOT_MODE"] = "offline"
    import nim as nimmod
    from kg_query import KGIndex
    kg = KGIndex()
    runs = {"offline stand-in": ps.run_suite(kg, nimmod.NIM(backend="offline"))}
    hosted = None
    if not a.offline_only:
        probe = nimmod.NIM()
        if probe.backend != "nim":
            print("no NVIDIA_API_KEY / NIM_BASE_URL: hosted run skipped")
        else:
            hosted = "hosted (replay)" if a.replay else "hosted"
            runs[hosted] = ps.run_suite(kg, nimmod.NIM(backend="nim", fallback="none"))     # a failure must show, not fall back
    labels = list(runs)
    agree = ps.agreement(runs[labels[0]], runs[labels[1]]) if len(labels) > 1 else None
    text = ps.report_markdown(runs, agree)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = "parity_" + "_vs_".join(l.split()[0] for l in labels)
    (out / f"{stem}.md").write_text(text)
    (out / f"{stem}.json").write_text(json.dumps({"runs": runs, "agreement": agree}, indent=1, sort_keys=True))
    print(text)
    print(f"written: {out / (stem + '.md')}")
    return 0 if all(r["passed"] for rows in runs.values() for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
