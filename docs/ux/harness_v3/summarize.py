"""Collects the journey outputs (J*.json, R*.json) next to this file into _summary.json: per journey the script errors and,
per snapshot, the DOM facts that show what the screen held (card order, edges by verdict, hover card text, answer panels,
bottom line, side panel). Run after journeys.sh."""
import glob
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
summ = {}
for f in sorted(HERE.glob("[JR]*.json")):
    d = json.loads(f.read_text())
    name = f.stem
    if "harness_error" in d:
        summ[name] = dict(harness_error=d["harness_error"][:300])
        continue
    rows = []
    for s in d["snapshots"]:
        r = dict(snap=s["name"], persona=s.get("persona"))
        c = s.get("card")
        if c:
            r.update(card=c.get("title", "").split(" ")[0], order=c.get("order"), kinds_first=c.get("kinds", [])[:2], rows=c.get("rows"),
                     strength_chips=c.get("strength_chips"), lines=c.get("cg_lines_by_verdict"), hidden=c.get("cg_hidden"), dimmed=c.get("cg_dimmed"),
                     predictions=c.get("plist"), paths=c.get("pathboxes"), hops=c.get("hops"), hops_kept=c.get("hops_kept"),
                     paths_greyed=c.get("paths_thin"), slider_info=c.get("slider_info"), first_chain=c.get("first_chain"), weakest=c.get("weakest"))
        hc = s.get("hovercard")
        if hc and not hc["hidden"]:
            r["hovercard"] = ("pinned: " if hc["pinned"] else "") + hc["text"][:240]
        for k in ("answer", "report"):
            a = s.get(k)
            if a:
                r[k] = dict(user=a["user"], context=a["context_fields"], stages=a["stages"], pinned_bottom_line=a["pinned_bottom_line"],
                            headline=a["headline"][:180], confidence=a["confidence"], confidence_reason=a["confidence_reason"],
                            points=a["points"][:4], rows=a["rows"], row_evidence_chips=a["evidence_chips"], checks=a["checks"][:4], abstain=a["abstain"][:160])
                if k == "report":
                    r[k].update(sections=a["sections"], claims=a["claims"], claim_evidence_chips=a.get("claim_evidence_chips"), subgraphs=a.get("subgraphs"))
        dr = s.get("drawer")
        if dr and dr["open"]:
            r["side_panel"] = dict(title=dr["title"], lead=dr["lead"][:100], raw=dr["raw"], headings=dr["headings"][:8])
        if s.get("echo", {}).get("question"):
            r["echo"] = s["echo"]
        if s.get("start", {}).get("visible"):
            r.update(example_tabs=s["start"]["example_tabs"], example_cards=s["start"]["example_cards"])
        r["headings"] = s["headings"][:14]
        rows.append(r)
    summ[name] = dict(script_errors=d["script_errors"], errors=d["errors"], api_calls=d["api_calls"], snapshots=rows)
out = dict(note="jsdom 24.1.3 + bridge.py (FastAPI TestClient against pipelines/app.py, local trial graph, card index v2, offline model "
                "stand-in); no port opened, hosted server not used. 8 Nov 2026.",
           total_script_errors=sum(v.get("script_errors", 0) for v in summ.values()), journeys=summ)
(HERE / "_summary.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
print("journeys", len(summ), "total script errors", out["total_script_errors"])
