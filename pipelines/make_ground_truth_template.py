"""
Ground-truth workbook for the owner to fill in: data/curated/ground_truth_template.xlsx

    python pipelines/make_ground_truth_template.py

Grey columns = what the pipeline found (from outputs/, read-only reference).
Yellow columns = what the reviewer confirms from the documents in raw/.
A formula column flags where the reviewer's answer differs from the pipeline's.

Sheets: Read me, Trials, Drugs, Conditions, Trial outcomes, Golden questions, Lists.
Functions: trial_rows(), drug_rows(), condition_rows(), outcome_rows() -> lists of dicts (tested);
build(path) writes the workbook.
"""
import csv, json, sys
from pathlib import Path
import pandas as pd
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seed_kg as sk
from kg_query import KGIndex

ROOT = sk.ROOT
OUT_FILE = ROOT / "data" / "curated" / "ground_truth_template.xlsx"
N_GOLDEN_BLANK = 30

LISTS = {
    "verdict": ["Confirmed", "Corrected", "Not in registry", "Unsure"],
    "met": ["Yes", "No", "Partly", "Not reported"],
    "failure_category": ["Efficacy: primary endpoint missed", "Futility at interim analysis", "Safety",
                         "Benefit-risk unfavourable", "Strategic / business decision", "Other"],
    "confidence": ["High", "Medium", "Low"],
}

FONT = "Arial"
HEAD = PatternFill("solid", fgColor="1F3864")
PIPE = PatternFill("solid", fgColor="EDEDED")
FILL = PatternFill("solid", fgColor="FFF2CC")
THIN = Border(*(Side(style="thin", color="BFBFBF"),) * 4)


# ---------------------------------------------------------------- rows from the graph (pipeline view)
def _folder(pid, kg):
    return f"raw/{pid}_{__import__('raw_docs').slug(kg.nodes[pid].get('name'))}/"


def trial_rows(kg, report):
    progs = {p["program_id"]: p for p in sk.parse_inventory(open(sk.DATASET).read())}
    rows = []
    for r in report[report.entity_type == "trial"].sort_values(["program", "query"]).itertuples():
        ncts = [n for n in str(r.identifier).split(";") if n and n != "nan"]
        rows.append(dict(program=r.program, program_name=progs[r.program]["label"], trial_name=r.query,
                         pipeline_nct="; ".join(ncts), match_rule=r.rule,
                         registry_title="; ".join(kg.name(f"NCT:{n}") or "" for n in ncts),
                         raw_folder=_folder(r.program, kg) + (f"trials/{ncts[0]}/" if ncts else "")))
    return rows


def drug_rows(kg, report):
    rows = []
    for r in report[report.entity_type == "drug"].sort_values(["program", "query"]).itertuples():
        did = f"CHEMBL:{r.identifier}" if isinstance(r.identifier, str) and r.identifier else ""
        targets = kg.drug_targets(did)["rows"] if did else []
        rows.append(dict(program=r.program, drug_name=r.query, pipeline_chembl=r.identifier if did else "",
                         chembl_name=kg.name(did) or "", match_rule=r.rule,
                         pipeline_targets="; ".join(f"{t['target_name']} ({', '.join(t['gene_symbols'])})" for t in targets)
                         or "none found", raw_folder=_folder(r.program, kg)))
    return rows


def condition_rows(kg):
    rows = []
    for pid in sorted(n for n, v in kg.nodes.items() if v["category"] == "tmkg:ClinicalTrialProgram"):
        for te in kg.out(pid, "tmkg:has_trial"):
            t = te["object"]
            dis = [(e["object"], kg.name(e["object"])) for e in kg.out(t, "tmkg:studies_condition")]
            mondo = [(x["object"], kg.name(x["object"])) for d, _ in dis for x in kg.out(d, "biolink:exact_match")]
            rows.append(dict(program=pid, trial=t[4:], registry_conditions="; ".join(json.loads(kg.nodes[t].get("conditions_text", "[]"))),
                             pipeline_disease="; ".join(f"{i} {n}" for i, n in dis + mondo) or "none mapped"))
    return rows


def outcome_rows(kg):
    rows = []
    for pid in sorted(n for n, v in kg.nodes.items() if v["category"] == "tmkg:ClinicalTrialProgram"):
        p = kg.nodes[pid]
        for te in kg.out(pid, "tmkg:has_trial"):
            t = kg.nodes[te["object"]]
            prim = kg.trial_outcomes(te["object"][4:], "PRIMARY")["rows"]
            p1 = prim[0] if prim else {}
            pvals = [a.get("p_value") for a in p1.get("analyses", []) if a.get("p_value")]
            rows.append(dict(program=pid, trial=te["object"][4:], acronym=t.get("acronym") or "",
                             registry_status=t.get("status"), why_stopped=t.get("why_stopped") or "",
                             curated_what_failed=p.get("what_failed"),
                             registry_primary_outcome=p1.get("title") or "", registry_data=p1.get("data") or "",
                             registry_p_values="; ".join(pvals),
                             raw_folder=_folder(pid, kg) + f"trials/{te['object'][4:]}/registry_summary.md"))
    return rows


# ---------------------------------------------------------------- workbook
def _sheet(wb, title, pipe_cols, fill_cols, rows, widths, validations=(), check=None, notes=None):
    """pipe_cols: [(key, header)] grey reference; fill_cols: [(header, comment)] yellow input;
    check: (header, formula template with {r} for the row, using column letters) appended at the end."""
    ws = wb.create_sheet(title)
    heads = [h for _, h in pipe_cols] + [h for h, _ in fill_cols] + ([check[0]] if check else [])
    ws.append(heads)
    for c, h in enumerate(heads, 1):
        cell = ws.cell(1, c)
        cell.font = Font(name=FONT, bold=True, color="FFFFFF")
        cell.fill = HEAD
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for c, (h, com) in enumerate(fill_cols, len(pipe_cols) + 1):
        if com:
            ws.cell(1, c).comment = Comment(com, "TrialMindKG")
    n = max(len(rows), 1)
    for i, row in enumerate(rows, 2):
        for c, (k, _) in enumerate(pipe_cols, 1):
            ws.cell(i, c, row.get(k, ""))
    for i in range(2, n + 2):
        for c in range(1, len(heads) + 1):
            cell = ws.cell(i, c)
            cell.font = Font(name=FONT, size=10)
            cell.border = THIN
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if c <= len(pipe_cols):
                cell.fill = PIPE
            elif c <= len(pipe_cols) + len(fill_cols):
                cell.fill = FILL
        if check:
            ws.cell(i, len(heads), check[1].format(r=i))
    for c, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = ws.cell(2, len(pipe_cols) + 1)
    ws.auto_filter.ref = f"A1:{get_column_letter(len(heads))}{n + 1}"
    ws.row_dimensions[1].height = 42
    for header, list_name in validations:
        col = get_column_letter(heads.index(header) + 1)
        dv = DataValidation(type="list", formula1=f"=Lists!${list_name}", allow_blank=True)
        dv.error, dv.errorTitle = "Choose a value from the list", "Not in list"
        ws.add_data_validation(dv)
        dv.add(f"{col}2:{col}{n + 1}")
    return ws


def build(path=OUT_FILE):
    kg = KGIndex()
    report = pd.read_csv(sk.OUT / "resolution_report.csv")
    wb = Workbook()
    readme = wb.active
    readme.title = "Read me"

    # Lists sheet (dropdown sources)
    ls = wb.create_sheet("Lists")
    refs = {}
    for c, (name, vals) in enumerate(LISTS.items(), 1):
        ls.cell(1, c, name).font = Font(name=FONT, bold=True)
        for r, v in enumerate(vals, 2):
            ls.cell(r, c, v).font = Font(name=FONT)
        col = get_column_letter(c)
        refs[name] = f"{col}$2:${col}${len(vals) + 1}"

    common = [("Verdict", "Confirmed = pipeline is right; Corrected = you entered a different answer; "
                          "Not in registry = no registry record exists; Unsure = needs a second look"),
              ("Evidence (PMID / URL / file in raw/)", "Where you found the answer, e.g. PMID:31387606 or raw/P02_.../registry_summary.md"),
              ("Page / table / section", "Optional: where in the document"),
              ("Notes", None), ("Reviewer initials", None), ("Review date (YYYY-MM-DD)", None)]

    t = trial_rows(kg, report)
    _sheet(wb, "Trials",
           [("program", "Program"), ("program_name", "Program (curated)"), ("trial_name", "Trial name (curated)"),
            ("pipeline_nct", "Pipeline NCT"), ("match_rule", "Pipeline match rule"),
            ("registry_title", "Registry title (pipeline NCT)"), ("raw_folder", "Documents in")],
           [("Confirmed NCT", "The NCT number that really is this trial, e.g. NCT02446912. Several: separate with ;")] + common,
           t, [8, 30, 18, 16, 20, 45, 34, 16, 14, 30, 16, 30, 10, 13],
           validations=[("Verdict", refs["verdict"])],
           check=("Agrees with pipeline?", '=IF(H{r}="","",IF(TRIM(H{r})=TRIM(D{r}),"yes","DIFFERENT"))'))

    d = drug_rows(kg, report)
    _sheet(wb, "Drugs",
           [("program", "Program"), ("drug_name", "Drug (curated)"), ("pipeline_chembl", "Pipeline ChEMBL id"),
            ("chembl_name", "ChEMBL name"), ("match_rule", "Pipeline match rule"),
            ("pipeline_targets", "Pipeline mechanism targets"), ("raw_folder", "Documents in")],
           [("Confirmed ChEMBL id", "e.g. CHEMBL1743072; leave empty if the drug has no ChEMBL record"),
            ("Confirmed target(s) / mechanism", "Target and action as stated in the documents, e.g. 'APP / amyloid-beta binding antibody'")]
           + common,
           d, [8, 24, 16, 24, 26, 45, 34, 16, 34, 14, 30, 16, 30, 10, 13],
           validations=[("Verdict", refs["verdict"])],
           check=("Agrees with pipeline?", '=IF(H{r}="","",IF(TRIM(H{r})=TRIM(C{r}),"yes","DIFFERENT"))'))

    c = condition_rows(kg)
    _sheet(wb, "Conditions",
           [("program", "Program"), ("trial", "Trial NCT"), ("registry_conditions", "Registry condition text"),
            ("pipeline_disease", "Pipeline disease ids (MeSH / MONDO)")],
           [("Confirmed MONDO id", "e.g. MONDO:0004975 (look up at https://www.ebi.ac.uk/ols4/ontologies/mondo)")] + common,
           c, [8, 14, 34, 50, 18, 14, 30, 16, 30, 10, 13],
           validations=[("Verdict", refs["verdict"])],
           check=("Agrees with pipeline?", '=IF(E{r}="","",IF(ISNUMBER(SEARCH(TRIM(E{r}),D{r})),"yes","DIFFERENT"))'))

    o = outcome_rows(kg)
    _sheet(wb, "Trial outcomes",
           [("program", "Program"), ("trial", "Trial NCT"), ("acronym", "Acronym"), ("registry_status", "Registry status"),
            ("why_stopped", "Registry: why stopped"), ("curated_what_failed", "Curated: what failed"),
            ("registry_primary_outcome", "Registry primary outcome (P1)"), ("registry_data", "Registry data type"),
            ("registry_p_values", "Registry p-value(s) for P1"), ("raw_folder", "Read")],
           [("Primary endpoint met?", "As reported in the primary publication or topline release"),
            ("Primary result as published (effect, 95% CI)", "Copy the numbers exactly as published, e.g. 'difference -4.2% (95% CI -14.2 to 5.8)'"),
            ("p-value as published", "Exactly as written, e.g. 0.41 or <0.001"),
            ("Failure category", "Main reason the program failed"),
            ("Key reason (one sentence, your words)", None),
            ("Confidence", "High = primary paper/registry results; Medium = press release; Low = secondary source")] + common[1:],
           o, [8, 13, 14, 14, 26, 34, 45, 10, 14, 34, 12, 30, 14, 26, 36, 11, 30, 16, 30, 10, 13],
           validations=[("Primary endpoint met?", refs["met"]), ("Failure category", refs["failure_category"]),
                        ("Confidence", refs["confidence"])])

    g = list(csv.DictReader(open(ROOT / "data" / "curated" / "golden_questions.csv", encoding="utf-8")))
    g += [dict()] * N_GOLDEN_BLANK
    _sheet(wb, "Golden questions",
           [("qid", "qid"), ("function", "Query function"), ("params", "Params (JSON)"), ("field", "Field"),
            ("expected", "Expected value"), ("source_of_truth", "Source of truth")],
           [("Plain-language question", "The question in your words, e.g. 'Which trial is TULIP-1?'"),
            ("Confirmed by you? (Yes/No)", None), ("Notes", None)],
           g, [7, 20, 34, 16, 22, 50, 40, 14, 30])

    # Read me
    readme.column_dimensions["A"].width = 120
    text = [
        ("TrialMindKG ground truth - what to do", True),
        ("", False),
        ("Purpose: record the correct answers, checked by you against the source documents, so the pipeline can be tested against them.", False),
        ("Documents: raw/<program folder>/ (start with 00_program.md, then trials/<NCT>/registry_summary.md, then publications/). Missing PDFs: see raw/README.md.", False),
        ("", False),
        ("Colours:  GREY columns = what the pipeline found (reference, do not edit).  YELLOW columns = you fill these in.", False),
        ("The last column on Trials, Drugs and Conditions is a formula: it shows DIFFERENT where your answer and the pipeline disagree.", False),
        ("Hover over a yellow header for guidance. Verdict, met?, category and confidence cells have drop-down lists.", False),
        ("", False),
        ("Order of work: 1) Trials  2) Drugs  3) Conditions  4) Trial outcomes  5) Golden questions (add your own Q&A).", False),
        ("Rule: copy numbers exactly as published; if a value is not reported, write 'not reported' - do not estimate.", False),
        ("", False),
        ("Example of a completed Trials row (format only):", True),
        ("Confirmed NCT: NCT02446912 | Verdict: Confirmed | Evidence: raw/P19_Anifrolumab_TULIP-1/trials/NCT02446912/registry_summary.md | "
         "Page/table: Outcome P1 | Notes: sister record NCT02446899 is TULIP-2 (from curated_overrides.csv) | Reviewer: your initials | Date: 2026-10-08", False),
        ("Example of a completed Trial outcomes row (format only):", True),
        ("Primary endpoint met?: No | Result: mean difference -4.2 (95% CI -14.2 to 5.8), anifrolumab 300 mg vs placebo | p: 0.412 | "
         "Category: Efficacy: primary endpoint missed | Key reason: SRI(4) response at week 52 did not differ from placebo | Confidence: High "
         "(values from the TULIP-1 registry results, raw/P19_Anifrolumab_TULIP-1/trials/NCT02446912/registry_summary.md)", False),
        ("", False),
        ("When done: save, commit, and ask Claude to convert it into data/curated/ground_truth.csv and tests/test_resolution.py.", False),
        (f"Pipeline reference values come from outputs/ (graph snapshot {kg.version.get('snapshot', '')[:12]}).", False),
    ]
    for i, (s, bold) in enumerate(text, 1):
        cell = readme.cell(i, 1, s)
        cell.font = Font(name=FONT, bold=bold, size=12 if i == 1 else 10)
        cell.alignment = Alignment(wrap_text=True)
    wb.move_sheet("Lists", offset=len(wb.sheetnames))
    wb.calculation.fullCalcOnLoad = True          # Excel computes the check column when the file opens
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return dict(trials=len(t), drugs=len(d), conditions=len(c), outcomes=len(o), golden=len(g) - N_GOLDEN_BLANK)


if __name__ == "__main__":
    print(build(), "->", OUT_FILE)
