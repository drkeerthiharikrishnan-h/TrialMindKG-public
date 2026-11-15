"""
Protocol / SAP findings (P10 step C): design facts read from the posted protocol and statistical analysis plan PDFs
(raw_text/), proposed by Nemotron with a word-for-word quote, confirmed by the owner before they enter the graph.

    python pipelines/protocol_findings.py     # -> data/curated/protocol_findings_candidates.csv (PROPOSED)

For every protocol / SAP text of a trial and every finding type in FINDINGS:
  1. BM25 picks the KEEP passages most likely to state it (rag_baseline.bm25; no model call)
  2. the 'extractor' model returns dict(found, value, quote) from those passages only
  3. the quote must appear word for word in the passages, otherwise the row is REJECTED_quote (rules 1 and 3)
Owner step: copy accepted rows into data/curated/protocol_findings.csv with 'Confirmed by owner YYYY-MM-DD: ...';
seed_kg.add_protocol_findings() then adds Trial -tmkg:has_protocol_finding-> tmkg:ProtocolFinding.

Functions: documents() -> [dict(nct, doc, path)];  extract(nim, finding, passages) -> dict;
           propose(nim, path) -> rows;  load_confirmed(path) -> rows
"""
import csv, gzip, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nim import register_offline
from stop_reasons import quote_ok
import rag_baseline

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "data" / "curated" / "protocol_findings_candidates.csv"
CONFIRMED = ROOT / "data" / "curated" / "protocol_findings.csv"
KEEP = 4
FINDINGS = {   # finding type -> (search words for BM25, what the model must extract)
    "primary_endpoint": ("primary endpoint primary efficacy outcome measure defined as",
                         "the primary efficacy endpoint and its time point"),
    "sample_size": ("sample size power assumed treatment difference standard deviation dropout patients per arm",
                    "the sample-size assumptions: power, alpha, assumed effect, number of participants"),
    "interim_futility": ("interim analysis futility stopping boundary data monitoring committee",
                         "the interim analysis / futility rule (timing and boundary)"),
    "multiplicity": ("multiplicity hierarchical testing alpha family-wise error gatekeeping",
                     "how multiplicity across endpoints or doses is controlled"),
    "enrichment": ("inclusion criteria biomarker positive genotype eosinophil interferon screening enrichment",
                   "any biomarker / genotype / laboratory criterion used to select or stratify participants"),
}
SCHEMA = {"type": "object", "required": ["found", "value", "quote"], "properties": {
    "found": {"type": "boolean"}, "value": {"type": "string"}, "quote": {"type": "string"}}}
SYSTEM = ("You read passages from a clinical-trial protocol or statistical analysis plan. Report ONLY what the passages "
          "state about: {what}. value = a short plain summary (copy numbers exactly); quote = the shortest span copied "
          "word for word from the passages that supports it. If the passages do not state it, found = false.")


def documents():
    rows = list(csv.DictReader(open(ROOT / "raw_text" / "index.csv", encoding="utf-8")))
    out = []
    for r in rows:
        m = re.search(r"/trials/(NCT\d{8})/documents/([^/]+)\.pdf$", r["pdf"])
        if r["status"] == "ok" and m and not m.group(2).startswith("ICF"):
            out.append(dict(nct=m.group(1), doc=m.group(2), path=ROOT / "raw_text" / r["text_file"]))
    return sorted(out, key=lambda d: (d["nct"], d["doc"]))


def extract(nim, finding, passages, findings=None):
    """findings: {type: (search words, what to extract)}; default FINDINGS (protocol / SAP)."""
    what = (findings or FINDINGS)[finding][1]
    text = "\n\n".join(f"[{i + 1}] {p['text']}" for i, p in enumerate(passages))
    r = nim.chat("protocol", [{"role": "system", "content": SYSTEM.format(what=what)},
                               {"role": "user", "content": f"FINDING: {finding}\nPASSAGES:\n{text}"}], schema=SCHEMA)
    d = r.data or {}
    found = bool(d.get("found")) and bool(d.get("quote"))
    return dict(found=found, value=d.get("value", "") if found else "", quote=d.get("quote", "") if found else "",
                status=("ok" if quote_ok(d.get("quote"), text) else "REJECTED_quote") if found else "not_stated",
                backend=r.backend, snapshot_key=r.key)


def recheck(x, full_text):
    """A quote that spans two retrieved passages fails the passage check but is still word for word in the source
    document: accept it then (the rule is 'verbatim in the source', never 'close enough')."""
    if x["status"] == "REJECTED_quote" and quote_ok(x["quote"], full_text):
        return dict(x, status="ok")
    return x


def propose(nim, path=CANDIDATES):
    rows = []
    for d in documents():
        full = gzip.decompress(d["path"].read_bytes()).decode("utf-8", "replace")
        chs = rag_baseline.chunks([(str(d["path"]), full)])
        for finding, (words, _) in FINDINGS.items():
            passages = rag_baseline.bm25(words, chs, k=KEEP)
            if not passages:
                continue
            x = recheck(extract(nim, finding, passages), full)
            if x["found"]:
                rows.append(dict(nct=d["nct"], document=d["doc"], finding=finding, value=x["value"], quote=x["quote"],
                                 status=x["status"], backend=x["backend"], snapshot_key=x["snapshot_key"],
                                 note="PROPOSED - confirm by copying to data/curated/protocol_findings.csv"))
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["nct", "document", "finding", "value", "quote", "status", "backend", "snapshot_key", "note"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return rows


def load_confirmed(path=CONFIRMED):
    if not Path(path).exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("nct") and r.get("finding") in FINDINGS
                and str(r.get("justification", "")).startswith("Confirmed by owner")]


@register_offline("protocol")
def _offline(messages, schema):
    """Stand-in (NOT a model): reports the first sentence that contains a finding keyword, quoted verbatim."""
    user = messages[-1]["content"]
    finding = re.search(r"FINDING: (\S+)", user).group(1)
    key = {"primary_endpoint": r"primary (efficacy )?endpoint", "sample_size": r"sample size|power",
           "interim_futility": r"futility|interim", "multiplicity": r"multiplicity|hierarch",
           "enrichment": r"inclusion criteria", "primary_result": r"primary (endpoint|outcome)",
           "subgroup_finding": r"subgroup|post hoc", "failure_explanation": r"did not|failed|may explain",
           "regulatory_decision": r"approv|refus|withdr", "efficacy_conclusion": r"efficacy|demonstrat",
           "next_step": r"discontinu|confirmatory|postmarketing"}[finding]
    text = user.split("PASSAGES:\n", 1)[-1]
    for sent in re.split(r"(?<=[.;])\s+", re.sub(r"\[\d+\] ", "", text)):
        if re.search(key, sent, re.I) and 20 < len(sent) < 400:
            return json.dumps({"found": True, "value": sent[:120], "quote": sent})
    return json.dumps({"found": False, "value": "", "quote": ""})


if __name__ == "__main__":
    import nim as nimmod
    rows = propose(nimmod.NIM())
    print(f"{len(rows)} proposals ({sum(r['status'] == 'ok' for r in rows)} with a verified quote) -> {CANDIDATES}")
