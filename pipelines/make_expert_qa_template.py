"""
Writes data/curated/expert_qa.xlsx: the owner's expert question-and-answer sheet, used later as a test set for the
solution (pruning labels, multi-hop answers, failure explanations, knowledge timelines).

The sheet holds question STEMS only (no answers, no facts): the owner writes the answers and evidence from their own
expertise and sources, before seeing what the system answers. Re-running keeps any answers already typed in.

Usage:  python pipelines/make_expert_qa_template.py
"""
from pathlib import Path
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "curated" / "expert_qa.xlsx"

# program id, drug / trial, main target gene symbol(s), disease (as in the curated program table)
PROGRAMS = [
    ("P13", "Tofersen - VALOR", "SOD1", "SOD1-associated ALS"),
    ("P17", "Magrolimab - ENHANCE", "CD47", "High-risk MDS / AML"),
    ("P06", "Tominersen - GENERATION HD1", "HTT", "Huntington's disease"),
    ("P02", "Verubecestat - EPOCH / APECS", "BACE1", "Alzheimer's disease"),
    ("P14", "Epacadostat + pembrolizumab - ECHO-301", "IDO1", "Advanced melanoma"),
    ("P18", "Tiragolumab + atezolizumab - SKYSCRAPER-01", "TIGIT", "PD-L1-high NSCLC"),
    ("P25", "Dalcetrapib - dal-GenE", "CETP (ADCY9 genotype)", "After acute coronary syndrome"),
    ("P33", "Pelacarsen - Lp(a)HORIZON", "LPA", "Elevated Lp(a), cardiovascular disease"),
    ("P26", "Asundexian - OCEANIC-AF", "F11", "Stroke prevention in atrial fibrillation"),
    ("P21", "Selonsertib - STELLAR-3/4", "MAP3K5 (ASK1)", "MASH with advanced fibrosis"),
]

# kind -> question stems ({t} target, {d} disease, {drug} drug/trial); what each kind tests
STEMS = {
    "edge_judgement": ["Is {t} genuinely linked to {d}? Keep or prune, and what kind of evidence supports it "
                       "(human genetics, functional, clinical, text-mining only)?",
                       "Name 2-3 diseases that databases link to {t} but you consider weak or wrong, and why."],
    "multihop": ["Which other drugs act on {t}, and in which diseases have they been tested?",
                 "Which genes in the same pathway or complex as {t} are also linked to {d}?"],
    "failure": ["Why did {drug} fail: target biology, molecule, dose/exposure, population, endpoint or trial design? "
                "What is the evidence?",
                "Given what is known now, which patients or indication (if any) would you test {t} in next?"],
    "timeline": ["What was known about {t} in {d} before the trial started, and what is known now (with years)?",
                 "Is there contradicting evidence for {t} in {d} (failed trials, negative studies, retractions)? "
                 "Give years."],
}
KIND_TESTS = {"edge_judgement": "pruning (keep/prune labels for Pharos edges)",
              "multihop": "usability: graph questions with several hops",
              "failure": "differentiator: failure diagnosis and what next",
              "timeline": "evidence timeline on edges (what we knew / know now)"}
COLUMNS = ["qid", "program", "drug / trial", "target", "disease", "kind", "tests", "question", "expert_answer",
           "must_mention", "must_not_say", "evidence (PMID / NCT / URL)", "confidence", "notes"]
README = [
    "Expert Q&A - your answers become the test set for the solution.",
    "",
    "1. Answer from your own expertise and sources. Do not use TrialMindKG's answers (that would leak).",
    "2. expert_answer: 1-5 sentences. must_mention: key facts a good answer must contain, separated by ';'.",
    "3. must_not_say: claims that would be wrong or overstated (e.g. 'proven causal', a wrong population).",
    "4. evidence: PMIDs, NCT ids or URLs. confidence: high / medium / low. Leave a row empty if unsure.",
    "5. Add your own questions at the bottom (new qid, same columns). About 20-30 minutes per program.",
    "6. Save the file in place (data/curated/expert_qa.xlsx) and tell Claude; nothing else to run.",
]


def rows():
    """Question rows (qid, program, ...); answers empty. Returns a list of lists matching COLUMNS."""
    out, n = [], 0
    for pid, drug, target, disease in PROGRAMS:
        t = target.split(" (")[0]
        for kind, stems in STEMS.items():
            for stem in stems:
                n += 1
                out.append([f"Q{n:03d}", pid, drug, target, disease, kind, KIND_TESTS[kind],
                            stem.format(t=t, d=disease, drug=drug)] + [None] * 6)
    return out


def previous_answers(path):
    """{qid: {column: value}} for answer columns already filled in an existing workbook."""
    if not path.exists():
        return {}
    ws = openpyxl.load_workbook(path)["Questions"]
    head = [c.value for c in ws[1]]
    keep = COLUMNS[8:]
    out = {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        rec = dict(zip(head, r))
        if rec.get("qid"):
            out[rec["qid"]] = {k: rec.get(k) for k in keep if rec.get(k) not in (None, "")}
            if not str(rec["qid"]).startswith("Q") or rec["qid"] not in {x[0] for x in rows()}:
                out[rec["qid"]]["_row"] = [rec.get(c) for c in COLUMNS]     # owner-added question
    return out


def write(path=OUT):
    old = previous_answers(path)
    wb = openpyxl.Workbook()
    rd = wb.active
    rd.title = "Read me"
    for line in README:
        rd.append([line])
    rd["A1"].font = Font(bold=True, size=13)
    rd.column_dimensions["A"].width = 110
    ws = wb.create_sheet("Questions")
    ws.append(COLUMNS)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DDEBF7")
    all_rows = rows()
    known = {r[0] for r in all_rows}
    all_rows += [v["_row"] for q, v in old.items() if q not in known and "_row" in v]
    for r in all_rows:
        prev = old.get(r[0], {})
        r = [prev.get(c, v) if i >= 8 else v for i, (c, v) in enumerate(zip(COLUMNS, r))]
        ws.append(r)
    widths = [7, 8, 30, 16, 26, 14, 30, 70, 60, 40, 30, 28, 11, 30]
    for col, w in zip("ABCDEFGHIJKLMN", widths):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    dv = DataValidation(type="list", formula1='"high,medium,low"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"M2:M{ws.max_row + 200}")
    ws.freeze_panes = "I2"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path, ws.max_row - 1


if __name__ == "__main__":
    p, n = write()
    print(f"{n} questions -> {p}")
