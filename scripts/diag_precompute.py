#!/usr/bin/env python3
"""Diagnose why scripts/precompute_examples.py skips the trial / query examples (CS-7, 9 Nov 2026).
    .venv/bin/python scripts/diag_precompute.py [x01] [x08]
Runs each example id through the app in-process exactly as precompute does and prints, per example: elapsed time,
run.backend, run.cached, the count of model calls by (role, backend), the first stand-in call's role and snapshot key,
and the NIM settings in effect (backend, fallback, snapshot mode). Read-only; stores nothing."""
import collections, os, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines")); sys.path.insert(0, str(ROOT / "scripts"))


def main(ids):
    from fastapi.testclient import TestClient
    import app as appmod, stored_answers, precompute_examples as pe
    client = TestClient(appmod.app)
    targets = {t["id"]: t for t in pe.plan(appmod)}
    nim = getattr(appmod, "NIM_CLIENT", None) or getattr(appmod, "nim", None)
    print("env: TMKG_SNAPSHOT_MODE=%s TMKG_LLM_BACKEND=%s TMKG_NIM_FALLBACK=%s key_set=%s" % (
        os.environ.get("TMKG_SNAPSHOT_MODE"), os.environ.get("TMKG_LLM_BACKEND"), os.environ.get("TMKG_NIM_FALLBACK"),
        bool(os.environ.get("NVIDIA_API_KEY"))))
    if nim is not None:
        print("nim object: backend=%s fallback=%s" % (getattr(nim, "backend", "?"), getattr(nim, "fallback", "?")))
    for i in ids:
        t = targets.get(i)
        if t is None:
            print(i, "not a target"); continue
        t0 = time.time(); ans, backend, model = pe.run(client, t); dt = time.time() - t0
        run_ = ans.get("run") or {}
        calls = run_.get("model_calls") or []
        by = collections.Counter((c.get("role"), c.get("backend")) for c in calls)
        first = next((c for c in calls if c.get("backend") == "offline-stand-in"), None)
        print("\n== %s kind=%s endpoint=%s elapsed=%.1fs" % (i, t["kind"], t["endpoint"], dt))
        print("   backend=%s model=%s run.cached=%s fallback_from=%s hosted()=%s" % (
            backend, model, run_.get("cached", ans.get("cached")), ans.get("fallback_from") or run_.get("fallback_from"), pe.hosted(ans, t["kind"])))
        print("   model calls by (role, backend):", dict(by) or "(none reported)")
        if first:
            print("   first stand-in call:", {k: first.get(k) for k in ("role", "model", "snapshot_key")})
        if t["kind"] != "trials":
            import json
            print("   model_fallback_from:", ans.get("model_fallback_from"), "| fallback_from:", ans.get("fallback_from"))
            for k, a in enumerate(ans.get("attempts") or []):
                print("   attempt %d:" % k, json.dumps({kk: vv for kk, vv in a.items() if kk != "cypher"}, default=str)[:400])
            if nim is not None and getattr(nim, "calls", None):
                print("   nim.calls tail:", [c for c in list(nim.calls)[-3:]])
            if nim is not None and getattr(nim, "last_error", None):
                print("   nim.last_error:", str(nim.last_error)[:400])
        else:
            bad = [c for c in calls if c.get("backend") != "nim"]
            print("   non-hosted calls:", bad[:3])


if __name__ == "__main__":
    main(sys.argv[1:] or ["x01", "x08"])
