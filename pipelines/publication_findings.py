"""
Publication findings (P10 step C, publications): what the trial's own papers report, proposed by Nemotron with a
word-for-word quote and confirmed by the owner before entering the graph (same engine as protocol_findings.py).

    python pipelines/publication_findings.py   # -> data/curated/publication_findings_candidates.csv (PROPOSED)

Papers read: 'Trial publications' in raw/index.csv (Europe PMC records citing the trial, reviews excluded) that name
the trial outside the full-text body (NCT in the abstract / metadata, or linked from the registry or the curated
table) - the likely primary and secondary results papers. Text = abstract record + open-access full text if present.
Finding types (PUB_FINDINGS): primary_result, subgroup_finding, failure_explanation (the authors' own words).
Owner step: confirmed rows -> data/curated/publication_findings.csv ('Confirmed by owner YYYY-MM-DD: ...');
seed_kg.add_publication_findings() adds Publication -tmkg:reports_finding-> tmkg:PublicationFinding.

Functions: documents() -> [dict(program, pmid, paths)];  propose(nim, path) -> rows;  load_confirmed(path) -> rows
"""
import csv, gzip, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protocol_findings as pf
import rag_baseline

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "data" / "curated" / "publication_findings_candidates.csv"
CONFIRMED = ROOT / "data" / "curated" / "publication_findings.csv"
PUB_FINDINGS = {
    "primary_result": ("primary endpoint primary outcome met did not meet difference placebo hazard ratio p value",
                       "the trial's primary-endpoint result (effect size, confidence interval, p value as stated)"),
    "subgroup_finding": ("subgroup post hoc prespecified exploratory analysis patients with biomarker interaction",
                         "a subgroup or post hoc result, saying whether the paper calls it prespecified or post hoc"),
    "failure_explanation": ("failed did not reach may explain possible reasons limitation futility lack of efficacy",
                            "the reason(s) the authors give for the negative or failed result, in their own words"),
}


CATEGORIES = ("Trial publications", "Conference abstracts", "Preprints")


def supplement_texts():
    """{PMCID: [text file paths]} from raw_text/supplementary_index.csv (pipelines/supplements.py)."""
    idx = ROOT / "raw_text" / "supplementary_index.csv"
    out = {}
    if idx.exists():
        for r in csv.DictReader(open(idx, encoding="utf-8")):
            if r["status"] == "ok":
                out.setdefault(r["pmcid"], []).append(ROOT / "raw_text" / r["text_file"])
    return out


def documents(all_records=True):
    """Every trial publication, conference abstract and preprint record in raw/ (all_records=False: only the 165
    papers naming the trial outside the full-text body, the first batch). Text = record (abstract) + open-access
    full text + the article's supplementary files as text. -> [dict(program, record, pmid, paths)]"""
    rows = list(csv.DictReader(open(ROOT / "raw" / "index.csv", encoding="utf-8")))
    supp = supplement_texts()
    out = []
    for r in rows:
        notes = (r["notes"] or "").strip()
        m = re.search(r"((PMID|PMC|PPR)_[A-Z]*\d+)\.md$", r["local_path"] or "")
        if r["category"] not in CATEGORIES or not m:
            continue
        if not all_records and not (notes and notes != "cites trial NCT (in full text)" and m.group(2) == "PMID"):
            continue
        base = ROOT / "raw" / r["local_path"]
        paths = [p for p in (base, base.with_name(base.stem + "_fulltext.md")) if p.exists()]
        if not paths:
            continue
        pmc = re.findall(r"(PMC\d+)", paths[0].read_text(encoding="utf-8", errors="replace"))
        pmid = m.group(1)[5:] if m.group(2) == "PMID" else ""
        out.append(dict(program=r["program"], record=m.group(1), pmid=pmid, paths=paths, part="article"))
        sp = [p for c in sorted(set(pmc))[:1] for p in supp.get(c, [])]   # supplements: read as their own document,
        if sp:                                                              # so an article is never re-read when they
            out.append(dict(program=r["program"], record=m.group(1), pmid=pmid, paths=sp, part="supplement"))  # arrive
    seen, uniq = set(), []
    for d in sorted(out, key=lambda d: (d["record"], d["part"], d["program"])):
        if (d["record"], d["part"]) not in seen:
            seen.add((d["record"], d["part"]))
            uniq.append(d)
    return uniq


def read_text(path):
    """Text of a .md record or a gzip-compressed supplement text file (raw_text/.../*.txt.gz)."""
    data = Path(path).read_bytes()
    return (gzip.decompress(data) if str(path).endswith(".gz") else data).decode("utf-8", "replace")


def propose(nim, path=CANDIDATES):
    rows = []
    for d in documents():
        texts = [(str(p.relative_to(ROOT)), read_text(p)) for p in d["paths"]]
        chs = rag_baseline.chunks(texts)
        for finding, (words, _) in PUB_FINDINGS.items():
            passages = rag_baseline.bm25(words, chs, k=pf.KEEP)
            if not passages:
                continue
            x = pf.recheck(pf.extract(nim, finding, passages, findings=PUB_FINDINGS), "\n".join(t for _, t in texts))
            if x["found"]:
                rows.append(dict(program=d["program"], record=d["record"], part=d["part"], pmid=d["pmid"], finding=finding, value=x["value"],
                                 quote=x["quote"], status=x["status"], backend=x["backend"],
                                 snapshot_key=x["snapshot_key"],
                                 note="PROPOSED - confirm by copying to data/curated/publication_findings.csv"))
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["program", "record", "part", "pmid", "finding", "value", "quote", "status", "backend", "snapshot_key", "note"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return rows


def load_confirmed(path=CONFIRMED):
    if not Path(path).exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if (r.get("record") or r.get("pmid")) and r.get("finding") in PUB_FINDINGS
                and str(r.get("justification", "")).startswith("Confirmed by owner")]


if __name__ == "__main__":
    import nim as nimmod
    rows = propose(nimmod.NIM())
    print(f"{len(rows)} proposals ({sum(r['status'] == 'ok' for r in rows)} with a verified quote) -> {CANDIDATES}")
