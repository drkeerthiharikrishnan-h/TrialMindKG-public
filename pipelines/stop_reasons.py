"""
Stop-reason extractor (Nemotron via NIM): free text about why a trial stopped / failed -> one category,
with the exact supporting quote.

    python pipelines/stop_reasons.py             # all trials -> outputs/stop_reasons.csv
    python pipelines/stop_reasons.py evaluate    # agreement with your ground-truth workbook (when filled)

Texts per trial (each extracted separately, labelled by source):
  registry  - ClinicalTrials.gov 'why stopped' (only present for stopped trials)
  curated   - 'what failed' from the curated dataset row of the program
Guards (CLAUDE.md rules 1 and 3):
  - evidence_quote must appear word for word in the input text, otherwise the row is REJECTED
  - an empty / uninformative text gives 'Not stated in text' (never a guess)
  - output is a proposal for review; it is not written into the graph by this module

Functions: categorize(nim, text, source) -> dict;  quote_ok(quote, text) -> bool;  run(nim) -> rows;
evaluate(rows, workbook) -> agreement report.
"""
import csv, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nim as nimmod
from nim import register_offline

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "stop_reasons.csv"
CATEGORIES = ["Efficacy: primary endpoint missed", "Futility at interim analysis", "Safety",
              "Benefit-risk unfavourable", "Strategic / business decision", "Enrollment / operational",
              "Other", "Not stated in text"]
SCHEMA = {"type": "object", "required": ["category", "evidence_quote"],
          "properties": {"category": {"type": "string", "enum": CATEGORIES},
                         "evidence_quote": {"type": "string"}, "note": {"type": "string"}}}
SYSTEM = ("You classify why a clinical trial stopped or failed, using ONLY the text given. "
          "Categories: " + "; ".join(CATEGORIES) + ". "
          "Futility = an interim analysis concluded the trial was unlikely to succeed. "
          "Efficacy = the final primary analysis missed. Benefit-risk = harms judged to outweigh benefit. "
          "Strategic / business = sponsor portfolio or commercial decision. "
          "If the text does not say why, answer 'Not stated in text'. "
          "evidence_quote must be copied word for word from the text (shortest span that justifies the category). "
          "Do not use outside knowledge.")


def _norm_ws(s):
    return " ".join((s or "").split()).lower()


def quote_ok(quote, text):
    q = _norm_ws(quote)
    return bool(q) and q in _norm_ws(text)


# ---- offline stand-in: transparent keyword rules (NOT a model; labelled offline-stand-in)
RULES = [("Futility at interim analysis", r"futil|unlikely to (meet|succeed|achieve)|interim analysis"),
         ("Benefit-risk unfavourable", r"benefit[–-]risk|risk[–-]benefit|unfavou?rable"),
         ("Safety", r"safety|death|mortality|adverse|toxicit|clinical hold"),
         ("Strategic / business decision", r"business|strategic|portfolio|commercial|sponsor decision"),
         ("Enrollment / operational", r"enrol|recruit|accrual"),
         ("Efficacy: primary endpoint missed", r"primary endpoint|did not meet|missed|not met|no (clinical )?benefit|"
                                               r"no improvement|did not (slow|reduce|improve)|negative")]


@register_offline("extractor")
def _offline(messages, schema):
    text = messages[-1]["content"].split("TEXT:", 1)[-1].strip()
    for sent in re.split(r"(?<=[.;:])\s+", text):
        for cat, pat in RULES:
            m = re.search(pat, sent, re.I)
            if m:
                return json.dumps({"category": cat, "evidence_quote": sent.strip(), "note": f"keyword '{m.group(0)}'"})
    return json.dumps({"category": "Not stated in text", "evidence_quote": "", "note": "no rule matched"})


def categorize(nim, text, source):
    """-> dict(category, evidence_quote, note, status, backend, model, snapshot_key)."""
    if not (text or "").strip():
        return dict(category="Not stated in text", evidence_quote="", note="empty text", status="ok",
                    backend="none", model="", snapshot_key="")
    r = nim.chat("extractor", [{"role": "system", "content": SYSTEM},
                               {"role": "user", "content": f"SOURCE: {source}\nTEXT: {text}"}], schema=SCHEMA)
    d = r.data
    ok = d["category"] == "Not stated in text" or quote_ok(d["evidence_quote"], text)
    return dict(category=d["category"], evidence_quote=d["evidence_quote"], note=d.get("note", ""),
                status="ok" if ok else "REJECTED: quote not found in text", backend=r.backend, model=r.model,
                snapshot_key=r.key)


def trial_texts():
    """[(program, nct, source, text)] for every trial: registry why_stopped + curated what_failed."""
    from kg_query import KGIndex
    kg = KGIndex()
    out = []
    for pid in sorted(n for n, v in kg.nodes.items() if v["category"] == "tmkg:ClinicalTrialProgram"):
        for te in kg.out(pid, "tmkg:has_trial"):
            t = kg.nodes[te["object"]]
            out.append((pid, te["object"][4:], "registry why_stopped", t.get("why_stopped", "")))
            out.append((pid, te["object"][4:], "curated what_failed", kg.nodes[pid].get("what_failed", "")))
    return out


def run(nim, path=OUT):
    rows = []
    for pid, nct, source, text in trial_texts():
        rows.append(dict(program=pid, trial=nct, source=source, text=text, **categorize(nim, text, source)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, list(rows[0]), lineterminator="\n")
        w.writeheader(); w.writerows(rows)
    return rows


def evaluate(rows, workbook=ROOT / "data" / "curated" / "ground_truth_template.xlsx"):
    """Agreement of the curated-text category with the reviewer's 'Failure category' (Trial outcomes sheet).
    'Enrollment / operational' and 'Not stated in text' count as 'Other' (the workbook has no such option)."""
    from openpyxl import load_workbook
    ws = load_workbook(workbook, data_only=True)["Trial outcomes"]
    head = [c.value for c in ws[1]]
    ti, fi = head.index("Trial NCT"), head.index("Failure category")
    truth = {r[ti]: r[fi] for r in ws.iter_rows(min_row=2, values_only=True) if r[ti] and r[fi]}
    if not truth:
        return dict(n=0, note="no reviewer answers yet in the workbook")
    m = lambda c: "Other" if c in ("Enrollment / operational", "Not stated in text") else c
    pred = {r["trial"]: m(r["category"]) for r in rows if r["source"] == "curated what_failed"}
    pairs = [(truth[t], pred.get(t)) for t in truth]
    return dict(n=len(pairs), agreement=round(sum(a == b for a, b in pairs) / len(pairs), 3),
                disagreements=[(t, truth[t], pred.get(t)) for t in truth if truth[t] != pred.get(t)])


if __name__ == "__main__":
    n = nimmod.NIM()
    rows = run(n)
    print(f"{len(rows)} texts classified (backend {n.backend}); rejected: "
          f"{sum(r['status'] != 'ok' for r in rows)} -> {OUT}")
    if sys.argv[1:] == ["evaluate"]:
        print(json.dumps(evaluate(rows), indent=2))
