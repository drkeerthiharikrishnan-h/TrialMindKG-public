"""
Findings from the other associated documents (P10 step C, last stage): FDA approval letters / labels / reviews, EMA
assessment reports, sponsor pages and curated source PDFs (raw_text/<program>/regulatory|web|sources), proposed by
Nemotron with a word-for-word quote and confirmed by the owner (same engine as protocol_findings.py).

    python pipelines/document_findings.py   # -> data/curated/document_findings_candidates.csv (PROPOSED)

Finding types (DOC_FINDINGS): regulatory_decision, efficacy_conclusion, failure_explanation, next_step.
Owner step: confirmed rows -> data/curated/document_findings.csv ('Confirmed by owner YYYY-MM-DD: ...');
seed_kg.add_document_findings() adds Program -tmkg:has_document_finding-> tmkg:DocumentFinding.

Functions: documents() -> [dict(program, kind, doc, path)];  propose(nim, path) -> rows;  load_confirmed(path) -> rows
"""
import csv, gzip, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protocol_findings as pf
import rag_baseline

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "data" / "curated" / "document_findings_candidates.csv"
CONFIRMED = ROOT / "data" / "curated" / "document_findings.csv"
KINDS = ("regulatory", "web", "sources")
DOC_FINDINGS = {
    "regulatory_decision": ("approved approval refuse withdrawn indication accelerated approval marketing authorisation",
                            "the regulatory decision stated (approval, refusal, withdrawal), its date and indication"),
    "efficacy_conclusion": ("efficacy primary endpoint study demonstrated did not demonstrate substantial evidence results",
                            "the document's conclusion on efficacy, naming the trial(s) it refers to"),
    "failure_explanation": ("failed did not meet futility discontinued lack of efficacy reason because",
                            "the reason(s) the document gives for a failed or discontinued trial or program"),
    "next_step": ("decided discontinue further development confirmatory trial postmarketing requirement next",
                  "what was decided next (discontinuation, new trial, post-marketing requirement)"),
}


def documents():
    out = []
    for r in csv.DictReader(open(ROOT / "raw_text" / "index.csv", encoding="utf-8")):
        parts = r["pdf"].split("/")
        kind = parts[1] if len(parts) > 2 and parts[1] in KINDS else ("registry" if "/other_registries/" in r["pdf"]
                                                                        else None)
        if r["status"] == "ok" and kind and not r["pdf"].lower().endswith(".csv"):   # csv = lists our pipeline wrote
            out.append(dict(program=parts[0][:3], kind=kind, doc=parts[-1], path=ROOT / "raw_text" / r["text_file"]))
    return sorted(out, key=lambda d: (d["program"], d["kind"], d["doc"]))


def propose(nim, path=CANDIDATES):
    rows = []
    for d in documents():
        text = gzip.decompress(d["path"].read_bytes()).decode("utf-8", "replace")
        chs = rag_baseline.chunks([(str(d["path"].relative_to(ROOT)), text)])
        for finding, (words, _) in DOC_FINDINGS.items():
            passages = rag_baseline.bm25(words, chs, k=pf.KEEP)
            if not passages:
                continue
            x = pf.recheck(pf.extract(nim, finding, passages, findings=DOC_FINDINGS), text)
            if x["found"]:
                rows.append(dict(program=d["program"], kind=d["kind"], document=d["doc"], finding=finding,
                                 value=x["value"], quote=x["quote"], status=x["status"], backend=x["backend"],
                                 snapshot_key=x["snapshot_key"],
                                 note="PROPOSED - confirm by copying to data/curated/document_findings.csv"))
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["program", "kind", "document", "finding", "value", "quote", "status", "backend", "snapshot_key", "note"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return rows


def load_confirmed(path=CONFIRMED):
    if not Path(path).exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if re.fullmatch(r"P\d{2}", r.get("program") or "")
                and r.get("finding") in DOC_FINDINGS and str(r.get("justification", "")).startswith("Confirmed by owner")]


if __name__ == "__main__":
    import nim as nimmod
    rows = propose(nimmod.NIM())
    print(f"{len(rows)} proposals ({sum(r['status'] == 'ok' for r in rows)} with a verified quote) -> {CANDIDATES}")
