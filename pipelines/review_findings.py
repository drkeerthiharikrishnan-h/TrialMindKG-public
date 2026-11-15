"""
Owner review of the Nemotron proposals in one workbook, then copy the accepted rows into the confirmed files.

    python pipelines/review_findings.py make     # -> data/curated/review_findings.xlsx (one sheet per kind)
    (open it, set Accept = yes / no per row, optionally edit Value or add a Note, save)
    python pipelines/review_findings.py apply    # accepted rows -> data/curated/{publication,protocol,document}_
                                                 #   findings.csv and biomarkers.csv, then rebuild the graph

Only rows whose quote was verified word for word (status ok) are offered. 'apply' writes the justification
'Confirmed by owner <today>: reviewed in review_findings.xlsx (<note>)' - the graph builder accepts nothing else
(CLAUDE.md rule 3). Rows already confirmed are kept; 'apply' never deletes a confirmed row.

Functions: KINDS (sheet -> candidates file, confirmed file, columns);  make(path) -> rows written;
           apply(path, today) -> {sheet: rows added}
"""
import csv, datetime, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[1]
CUR = ROOT / "data" / "curated"
BOOK = CUR / "review_findings.xlsx"
KINDS = {   # sheet: (candidates file, confirmed file, key columns copied, value column, quote column)
    "Publications": ("publication_findings_candidates.csv", "publication_findings.csv",
                     ["program", "record", "pmid", "finding"], "value", "quote"),
    "Protocols": ("protocol_findings_candidates.csv", "protocol_findings.csv",
                  ["nct", "document", "finding"], "value", "quote"),
    "Documents": ("document_findings_candidates.csv", "document_findings.csv",
                  ["program", "kind", "document", "finding"], "value", "quote"),
    "Biomarkers": ("biomarker_candidates.csv", "biomarkers.csv", ["node_id", "title"], "biomarker", "definition_quote"),
}


def _read(path):
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def previous_answers(path=BOOK):
    """{(sheet, key values..., quote): (Accept, Note, Value)} from an existing workbook, so a rebuild keeps the
    owner's work (rows are matched on their key columns and quote)."""
    from openpyxl import load_workbook
    if not Path(path).exists():
        return {}
    wb, out = load_workbook(path), {}
    for sheet in KINDS:
        if sheet not in wb.sheetnames:
            continue
        rows = list(wb[sheet].iter_rows(values_only=True))
        head = list(rows[0])
        for r in rows[1:]:
            d = dict(zip(head, r))
            if d.get("Accept") or d.get("Note"):
                key = (sheet,) + tuple(str(d.get(k) or "") for k in head if k not in ("Value", "Accept", "Note"))
                out[key] = (d.get("Accept"), d.get("Note"), d.get("Value"))
    return out


def make(path=BOOK):
    """Writes the review workbook; Accept / Note / edited Value already in an existing workbook are kept."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    old = previous_answers(path)
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = Workbook()
    ws = wb.active
    ws.title = "Read me"
    for line in ["Review of Nemotron proposals (every quote was checked word for word against the source).",
                 "Per row: Accept = yes or no. You may correct 'Value' and add a 'Note' (e.g. page number).",
                 "Then run: python pipelines/review_findings.py apply   (accepted rows enter the graph on rebuild)."]:
        ws.append([line])
    n = 0
    for sheet, (cand, _, keys, val, quote) in KINDS.items():
        rows = [r for r in _read(CUR / cand) if r.get("status") == "ok"]
        s = wb.create_sheet(sheet)
        head = keys + ["Value", "Quote", "Accept", "Note"]
        s.append(head)
        for c in s[1]:
            c.font = Font(bold=True)
        for r in rows:
            key = (sheet,) + tuple(str(r.get(k) or "") for k in keys) + (str(r.get(quote) or ""),)
            acc, note, value = old.get(key, (None, None, None))
            s.append([r.get(k) for k in keys] + [value or r.get(val), r.get(quote), acc, note])
        dv = DataValidation(type="list", formula1='"yes,no"', allow_blank=True)
        s.add_data_validation(dv)
        acc = len(keys) + 3
        col = s.cell(row=1, column=acc).column_letter
        dv.add(f"{col}2:{col}{max(2, len(rows) + 1)}")
        yellow = PatternFill("solid", fgColor="FFF2CC")
        for row in s.iter_rows(min_row=2, min_col=acc, max_col=acc + 1):
            for c in row:
                c.fill = yellow
        for i, w in enumerate([14] * len(keys) + [50, 80, 9, 30], 1):
            s.column_dimensions[s.cell(row=1, column=i).column_letter].width = w
        for row in s.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
        n += len(rows)
    wb.save(path)
    return n


def apply(path=BOOK, today=None):
    from openpyxl import load_workbook
    today = today or datetime.date.today().isoformat()
    wb = load_workbook(path)
    added = {}
    for sheet, (_, conf, keys, val, quote) in KINDS.items():
        if sheet not in wb.sheetnames:
            continue
        rows = list(wb[sheet].iter_rows(values_only=True))
        head = list(rows[0])
        confirmed_path = CUR / conf
        existing = _read(confirmed_path)
        cols = list(existing[0].keys()) if existing else None
        new = []
        for r in rows[1:]:
            d = dict(zip(head, r))
            if str(d.get("Accept") or "").strip().lower() != "yes":
                continue
            note = f" ({d['Note']})" if d.get("Note") else ""
            row = {k: d.get(k) or "" for k in keys}
            if sheet == "Biomarkers":
                row = dict(node_id=d["node_id"], biomarker=d["Value"], definition=d["Quote"], ensembl="")
            else:
                row.update(value=d["Value"], quote=d["Quote"])
            row["justification"] = f"Confirmed by owner {today}: reviewed in review_findings.xlsx{note}"
            if not any(all(str(e.get(k, "")) == str(row.get(k, "")) for k in row if k != "justification")
                       for e in existing):
                new.append(row)
        if new:
            with open(confirmed_path, encoding="utf-8") as f:
                cols = next(csv.reader(f))
            with open(confirmed_path, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, cols, lineterminator="\n", extrasaction="ignore")
                w.writerows(new)
        added[sheet] = len(new)
    return added


if __name__ == "__main__":
    if sys.argv[1:] == ["apply"]:
        print(apply(), "rows added; now run: python pipelines/seed_kg.py")
    else:
        print(f"{make()} proposals -> {BOOK}")
