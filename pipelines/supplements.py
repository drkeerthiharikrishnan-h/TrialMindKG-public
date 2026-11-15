"""
Supplementary files of the trial publications, fetched from the Europe PMC REST API (the europepmc.org website blocks
scripts; the API does not) and turned into text for the publication extractor.

    python pipelines/supplements.py      # -> raw_text/<program>/supplementary/<PMCID>/<file>.txt.gz + supplementary_index.csv

Per article (PMCID from raw/index.csv 'Supplementary files' / 'Supplementary data tables' rows):
  1. GET .../rest/<PMCID>/supplementaryFiles through Snapshot.download -> data/snapshots_raw/supplementary/<PMCID>.zip
     (git-ignored like PDFs; the text output is committed)
  2. read the zip entry by entry (the API streams it without a central directory, so local headers are walked)
  3. text from pdf (pypdf), docx / pptx (XML text), xlsx (openpyxl), csv / txt; images, video and old binary Office
     formats (.doc / .xls) are listed as 'not text' - figure captions are already in the full-text records
Nothing is summarised: the text is the file's own text.

Functions: articles() -> {pmcid: program};  entries(data) -> [(name, bytes)];  to_text(name, data) -> str | None;
           run(snap) -> index rows
"""
import csv, gzip, io, re, struct, sys, time, zlib, zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[1]
API = "https://www.ebi.ac.uk/europepmc/webservices/rest/{}/supplementaryFiles"
CACHE = ROOT / "data" / "snapshots_raw" / "supplementary"
OUT = ROOT / "raw_text"
INDEX = OUT / "supplementary_index.csv"
NOT_TEXT = (".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".mp4", ".avi", ".mov", ".doc", ".xls", ".ppt", ".zip")


def articles():
    out = {}
    for r in csv.DictReader(open(ROOT / "raw" / "index.csv", encoding="utf-8")):
        if r["category"] in ("Supplementary files", "Supplementary data tables (xlsx/csv)"):
            m = re.search(r"(PMC\d+)", r["local_path"] or "")
            if m:
                out.setdefault(m.group(1), r["program"])
    return dict(sorted(out.items()))


def entries(data):
    """Files in a zip, also when it was streamed without a central directory -> [(name, bytes)]."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        return [(n, z.read(n)) for n in z.namelist() if not n.endswith("/")]
    except zipfile.BadZipFile:
        pass
    out, i = [], 0
    while True:
        i = data.find(b"PK\x03\x04", i)
        if i < 0:
            return out
        flags, method, size, nlen, elen = struct.unpack("<xxxxxxHH xxxx xxxx I xxxx HH", data[i:i + 30])
        name = data[i + 30:i + 30 + nlen].decode("utf-8", "replace")
        start = i + 30 + nlen + elen
        try:
            if method == 8:
                d = zlib.decompressobj(-15)
                body = d.decompress(data[start:start + size] if size else data[start:])
                end = start + (size or len(data[start:]) - len(d.unused_data))
            else:
                body, end = data[start:start + size], start + size
        except zlib.error:
            body, end = b"", start
        if not name.endswith("/"):
            out.append((name, body))
        i = max(end, i + 4)


def _xml_text(xml):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", xml.decode("utf-8", "replace"))).strip()


def to_text(name, data):
    """Text of one supplementary file, or None when it is not a text format."""
    low = name.lower()
    if low.endswith(NOT_TEXT) or not data:
        return None
    if low.endswith(".pdf"):
        from pypdf import PdfReader
        try:
            return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
        except Exception:
            return None
    if low.endswith((".docx", ".pptx")):
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
            parts = [n for n in sorted(z.namelist()) if re.match(r"(word/document|ppt/slides/slide\d+)\.xml$", n)]
            return "\n".join(_xml_text(z.read(n)) for n in parts)
        except zipfile.BadZipFile:
            return None
    if low.endswith(".xlsx"):
        from openpyxl import load_workbook
        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception:
            return None
        lines = []
        for ws in wb.worksheets:
            lines.append(f"## sheet {ws.title}")
            for row in ws.iter_rows(values_only=True):
                if any(v is not None for v in row):
                    lines.append("\t".join("" if v is None else str(v) for v in row))
        return "\n".join(lines)
    if low.endswith((".csv", ".txt", ".tsv", ".xml", ".html", ".htm")):
        text = data.decode("utf-8", "replace")
        return _xml_text(data) if low.endswith((".xml", ".html", ".htm")) else text
    return None


def run(snap):
    rows = []
    for pmc, prog in articles().items():
        for wait in (0, 60, 180, 600):                  # the API answers 5xx when busy: wait and ask again
            time.sleep(wait)
            res = snap.download(API.format(pmc), CACHE / f"{pmc}.zip")
            if res["status"].startswith("ok") or not re.search(r"HTTP 5|not reachable", res["status"]):
                break
        if not res["status"].startswith("ok"):
            rows.append(dict(program=prog, pmcid=pmc, file="", status=res["status"], chars="", text_file=""))
            continue
        folder = next(iter(sorted((ROOT / "raw").glob(f"{prog}_*"))), None)
        for name, data in entries((CACHE / f"{pmc}.zip").read_bytes()):
            text = to_text(name, data)
            if text is None or len(text.strip()) < 50:
                rows.append(dict(program=prog, pmcid=pmc, file=name, status="not text (image / video / binary)"
                                 if text is None else "empty", chars=0, text_file=""))
                continue
            rel = Path(folder.name if folder else prog) / "supplementary" / pmc / (Path(name).name + ".txt.gz")
            (OUT / rel).parent.mkdir(parents=True, exist_ok=True)
            (OUT / rel).write_bytes(gzip.compress(text.encode("utf-8"), mtime=0))
            rows.append(dict(program=prog, pmcid=pmc, file=name, status="ok", chars=len(text), text_file=str(rel)))
    with open(INDEX, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, ["program", "pmcid", "file", "status", "chars", "text_file"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return rows


if __name__ == "__main__":
    from snapshot import Snapshot
    from collections import Counter
    rows = run(Snapshot(ROOT / "data" / "snapshots_raw"))
    print(Counter(r["status"].split(" (")[0] for r in rows), f"-> {INDEX}")
