"""
raw/CATALOGUE.xlsx: what exists for each of the 34 failed programs, and what is still missing.

    python pipelines/make_catalogue.py        # after pipelines/raw_docs.py

Built only from raw/index.csv (every item found, with its status), so it always matches the folder.
Sheets
  Read me     - legend and how to fill the gaps
  Checklist   - one row per program, one column per kind of document; each cell = how many items are
                present / still to fetch / candidates, or "none found"; yellow columns for your review
  Items       - every item (file or link) with category, status, source URL, local path; yellow 'Checked' column
  Publications- the trial-citing papers, conference abstracts and preprints
  Datasets    - omics / data / code accessions and data-access statements
  Summary     - counts per category across programs (formulas over the Checklist sheet)
Functions: cell_text(counts) and checklist(rows) are pure and tested; build(path) writes the workbook.
"""
import csv, sys
from collections import Counter, defaultdict
from pathlib import Path
from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

sys.path.insert(0, str(Path(__file__).resolve().parent))
import raw_docs as rd

ROOT = rd.ROOT
OUT = ROOT / "raw" / "CATALOGUE.xlsx"
FONT = "Arial"
HEAD = PatternFill("solid", fgColor="1F3864")
FILL = PatternFill("solid", fgColor="FFF2CC")
GREEN = PatternFill("solid", fgColor="C6EFCE")
AMBER = PatternFill("solid", fgColor="FFEB9C")
RED = PatternFill("solid", fgColor="F4CCCC")
THIN = Border(*(Side(style="thin", color="BFBFBF"),) * 4)
PRESENT = {rd.DOWNLOADED, rd.WRITTEN}


def checklist(rows):
    """index rows -> {program: {category: Counter(present / to_fetch / candidate / link / blocked)}}.
    An item counts for every category it covers (e.g. one PDF that is protocol + SAP)."""
    out = defaultdict(lambda: defaultdict(Counter))
    for r in rows:
        kind = ("none" if r["status"] == rd.NONE_FOUND else "present" if r["status"] in PRESENT else "to_fetch" if r["status"] == rd.TO_FETCH else
                "candidate" if r["status"] == rd.CANDIDATE else "link" if r["status"] == rd.LINK_ONLY else "blocked")
        for c in (r.get("covers") or r["category"]).split(";"):
            out[r["program"]][c][kind] += 1
    return out


def cell_text(c):
    """Counter -> short checklist text. Starts with ✓ (present here), ◐ (exists, not yet downloaded),
    ? (web candidate to verify), → (link / request only) or ✗ (nothing found)."""
    if not c or not (c["present"] + c["to_fetch"] + c["blocked"] + c["candidate"] + c["link"]):
        return "✗ none found"
    parts = []
    if c["present"]:
        parts.append(f"✓ {c['present']}")
    if c["to_fetch"]:
        parts.append(f"◐ {c['to_fetch']} to download")
    if c["blocked"]:
        parts.append(f"◐ {c['blocked']} browser-only")
    if c["candidate"]:
        parts.append(f"? {c['candidate']} to verify")
    if c["link"]:
        parts.append(f"→ {c['link']} link/request")
    return "; ".join(parts)


def _style_header(ws, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(1, c)
        cell.font = Font(name=FONT, bold=True, color="FFFFFF", size=9)
        cell.fill = HEAD
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
    ws.row_dimensions[1].height = 60


def _table(wb, title, headers, rows, widths, fill_cols=(), validations=()):
    ws = wb.create_sheet(title)
    ws.append(headers)
    _style_header(ws, len(headers))
    for r in rows:
        ws.append(r)
    for i in range(2, len(rows) + 2):
        for c in range(1, len(headers) + 1):
            cell = ws.cell(i, c)
            cell.font = Font(name=FONT, size=9)
            cell.border = THIN
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if headers[c - 1] in fill_cols:
                cell.fill = FILL
    for c, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(rows) + 1}"
    for header, values in validations:
        col = get_column_letter(headers.index(header) + 1)
        dv = DataValidation(type="list", formula1='"' + ",".join(values) + '"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"{col}2:{col}{len(rows) + 1}")
    return ws


def build(path=OUT, index=None):
    index = Path(index or ROOT / "raw" / "index.csv")
    rows = list(csv.DictReader(open(index, encoding="utf-8")))
    progs = {p["program_id"]: p for p in __import__("seed_kg").parse_inventory(
        open(ROOT / "data" / "curated" / "failed_clinical_trials_2016_2026.md").read())}
    cl = checklist(rows)
    wb = Workbook()
    readme = wb.active
    readme.title = "Read me"

    # ---- Checklist
    cats = rd.CATEGORIES
    review = ["Reviewed by (initials)", "Review date", "Gaps to chase / notes"]
    headers = ["Program", "Drug / trial(s)", "Sponsor", "Year"] + cats + ["Categories with something present"] + review
    data = []
    for pid in sorted(progs):
        p = progs[pid]
        data.append([pid, p["label"], p["sponsor"], p["year"]] + [cell_text(cl[pid].get(c)) for c in cats]
                    + [None] + [None] * len(review))
    ws = _table(wb, "Checklist", headers, data, [6, 28, 16, 8] + [13] * len(cats) + [11, 10, 11, 30],
                fill_cols=review)
    first, last = get_column_letter(5), get_column_letter(4 + len(cats))
    cnt_col = get_column_letter(5 + len(cats))
    for i in range(2, len(data) + 2):
        ws[f"{cnt_col}{i}"] = f'=COUNTIF({first}{i}:{last}{i},"✓*")'
        ws[f"{cnt_col}{i}"].font = Font(name=FONT, size=9, bold=True)
    rng = f"{first}2:{last}{len(data) + 1}"
    ws.conditional_formatting.add(rng, FormulaRule(formula=[f'LEFT({first}2,1)="✓"'], fill=GREEN))
    ws.conditional_formatting.add(rng, FormulaRule(formula=[f'OR(LEFT({first}2,1)="◐",LEFT({first}2,1)="?",LEFT({first}2,1)="→")'], fill=AMBER))   # LEFT() compares literally (no wildcards)
    ws.conditional_formatting.add(rng, FormulaRule(formula=[f'LEFT({first}2,1)="✗"'], fill=RED))
    ws.freeze_panes = "E2"

    # ---- Summary (formulas over the Checklist)
    sm = wb.create_sheet("Summary")
    sm.append(["Category", "Programs with ✓ (present)", "Programs with something to fetch / verify",
               "Programs with nothing found", "Total programs"])
    _style_header(sm, 5)
    for j, c in enumerate(cats):
        col = get_column_letter(5 + j)
        r = j + 2
        crange = f"Checklist!{col}2:{col}{len(data) + 1}"
        sm.append([c, f'=COUNTIF({crange},"✓*")',
                   f'=COUNTIF({crange},"◐*")+COUNTIF({crange},"~?*")+COUNTIF({crange},"→*")',
                   f'=COUNTIF({crange},"✗*")', f"=COUNTA({crange})"])
        for k in range(1, 6):
            sm.cell(r, k).font = Font(name=FONT, size=10)
            sm.cell(r, k).border = THIN
    sm.column_dimensions["A"].width = 42
    for k in "BCDE":
        sm.column_dimensions[k].width = 18

    # ---- Items
    item_rows = [[r["program"], r["trial"], r["category"], r["doc_type"], r["title"], r["status"], r["source_url"],
                  r["local_path"], r["retrieved"], r["notes"], None, None] for r in rows]
    _table(wb, "Items", ["Program", "Trial", "Category", "Item type", "Title / description", "Status", "Source URL",
                         "Local path (raw/...)", "Retrieved", "Notes", "Checked? (Yes / No / Irrelevant)", "Your notes"],
           item_rows, [7, 12, 22, 20, 50, 26, 40, 40, 18, 30, 14, 30],
           fill_cols=("Checked? (Yes / No / Irrelevant)", "Your notes"),
           validations=[("Checked? (Yes / No / Irrelevant)", ["Yes", "No", "Irrelevant"])])

    # ---- Publications
    pub = [[r["program"], r["category"], r["title"], r["status"], r["source_url"], r["local_path"], r["notes"]]
           for r in rows if r["doc_type"] == "publication record + abstract"]
    _table(wb, "Publications", ["Program", "Type", "Title", "Status", "Europe PMC record", "Local file", "Linked from"],
           pub, [7, 18, 70, 18, 40, 45, 40])

    # ---- Datasets
    ds = [[r["program"], r["category"], r["doc_type"], r["title"], r["source_url"]] for r in rows
          if r["category"] in ("Omics / expression / genomic datasets", "Derived data / code repositories",
                               "Patient-level data access")]
    _table(wb, "Datasets", ["Program", "Category", "Item type", "Accession / statement", "Link"], ds, [7, 26, 28, 80, 45])

    # ---- Read me
    readme.column_dimensions["A"].width = 125
    st = Counter(r["status"] for r in rows)
    text = [("Source-document catalogue for the 34 failed programs", True), ("", False),
            ("Checklist sheet: one row per program, one column per kind of document. Each cell says what was found:", False),
            ("   ✓ n  = n items present in raw/ (downloaded, or written from source data such as registry records and abstracts)", False),
            ("   ◐ n to download = exists, not yet downloaded on this computer (run pipelines/raw_docs.py on your Mac)", False),
            ("   ◐ n browser-only = the site refused the script (paywall / bot block): open the URL in a browser and save it", False),
            ("   ? n to verify = found by web search; check it is the right document before relying on it", False),
            ("   → n link/request = a dataset accession, data-sharing statement or access route (not a downloadable file)", False),
            ("   ✗ none found = searched (registry, Europe PMC, Drugs@FDA, FAERS, web search) and nothing found - you may know more", False),
            ("Colours: green = present, amber = exists but needs action, red = none found. Yellow columns are yours to fill.", False),
            ("", False),
            ("Items sheet: every file or link, with its status and source; mark 'Checked?' as you review. Publications and Datasets", False),
            ("sheets are filtered views. Summary sheet counts, per category, how many programs have it (formulas).", False),
            ("", False),
            ("What was searched: ClinicalTrials.gov records and posted documents; EU Clinical Trials Register ids; Europe PMC", False),
            ("(papers, conference abstracts and preprints citing the trial NCT, plus every record mentioning it); open-access", False),
            ("full texts with their supplementary files, figures and data-availability statements; dataset accession numbers", False),
            ("text-mined by Europe PMC; Drugs@FDA application documents; FAERS report counts; the curated dataset's links;", False),
            ("and targeted web searches (press releases, company/conference presentations, FDA advisory committee and EMA", False),
            ("documents), listed in data/curated/web_candidates.csv.", False),
            ("Not collected: patient-level data (request routes are listed), raw omics files (links listed; often GB-sized),", False),
            ("investor decks that are not publicly linked, and documents behind logins (e.g. some medically.roche.com pages).", False),
            ("", False),
            ("Item status counts: " + "; ".join(f"{k}: {v}" for k, v in sorted(st.items())), False),
            ("Refresh after downloading on your Mac:  python pipelines/raw_docs.py   then   python pipelines/make_catalogue.py", False)]
    for i, (s, bold) in enumerate(text, 1):
        c = readme.cell(i, 1, s)
        c.font = Font(name=FONT, bold=bold, size=12 if i == 1 else 10)
    wb.move_sheet("Summary", offset=2 - wb.sheetnames.index("Summary"))   # 3rd sheet
    wb.calculation.fullCalcOnLoad = True
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return dict(programs=len(data), items=len(rows), categories=len(cats))


if __name__ == "__main__":
    print(build(), "->", OUT)
