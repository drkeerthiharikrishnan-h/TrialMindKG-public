"""
PDF (and web page / Office file) -> plain text for every document downloaded into raw/ (run on the owner's Mac
after raw_docs.py). Web pages (.html / .htm), .docx / .pptx, .xlsx and .csv / .txt are converted with
supplements.to_text (one 'page'); images and video are not text.

    python pipelines/pdf_text.py            # writes raw_text/<same path>.txt.gz + raw_text/index.csv

Why: the PDFs stay on the Mac (git-ignored, large, some licensed), but their TEXT is small enough to commit, so the
cloud agents (stop-reason extractor, finding extractor, specialists) can read protocols, SAPs and FDA reviews.
- deterministic: pinned pypdf version, pages in order, gzip without timestamps -> same PDF = same bytes
- a page marker '=== page N ===' precedes each page, so an extracted quote can be traced to its page
- PDFs with (almost) no text layer are scanned images: listed as NEEDS_OCR (to be read later with NVIDIA
  Nemotron Parse), never guessed
- a PDF whose text is already extracted and unchanged (same sha256) is skipped

Functions: pdf_to_text(path) -> (text, n_pages); run(raw, out) -> index rows
"""
import csv, gzip, hashlib, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
RAW, OUT = ROOT / "raw", ROOT / "raw_text"
MIN_CHARS_PER_PAGE = 40          # below this on average the PDF is treated as a scanned image


def pdf_to_text(path):
    """-> (text with '=== page N ===' markers, number of pages)."""
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    parts = []
    for i, page in enumerate(reader.pages, 1):
        try:
            t = page.extract_text() or ""
        except Exception as err:                      # one broken page must not lose the rest of the document
            t = f"[page could not be read: {type(err).__name__}]"
        parts.append(f"=== page {i} ===\n{t.strip()}\n")
    return "\n".join(parts), len(reader.pages)


TEXT_SUFFIXES = (".pdf", ".html", ".htm", ".docx", ".pptx", ".xlsx", ".csv", ".txt")


def other_to_text(path):
    """Web page / Office / table file -> (text with one '=== page 1 ===' marker, 1); empty text when unreadable."""
    import supplements
    text = supplements.to_text(path.name, Path(path).read_bytes()) or ""
    return f"=== page 1 ===\n{text.strip()}\n", 1


def run(raw=RAW, out=OUT):
    raw, out = Path(raw), Path(out)
    old = {}
    if (out / "index.csv").exists():
        old = {r["pdf"]: r for r in csv.DictReader(open(out / "index.csv", encoding="utf-8"))}
    rows = []
    pdfs = sorted(p for p in raw.rglob("*") if p.suffix.lower() in TEXT_SUFFIXES and p.is_file()
                  and not p.name.endswith(".md"))
    for i, pdf in enumerate(pdfs, 1):
        rel = pdf.relative_to(raw).as_posix()
        sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
        dest = out / (rel + ".txt.gz")
        prev = old.get(rel)
        if prev and prev["pdf_sha256"] == sha and dest.exists():
            rows.append(prev)
            continue
        print(f"{i}/{len(pdfs)} {rel}", flush=True)
        try:
            text, pages = pdf_to_text(pdf) if pdf.suffix.lower() == ".pdf" else other_to_text(pdf)
        except Exception as err:
            rows.append(dict(pdf=rel, pdf_sha256=sha, pages="", chars="", text_file="",
                             status=f"ERROR: {type(err).__name__}: {str(err)[:120]}"))
            continue
        chars = len(text) - len(f"=== page  ===\n\n") * pages
        status = "ok" if pages and chars / pages >= MIN_CHARS_PER_PAGE else "NEEDS_OCR (scanned pages, no text layer)"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(gzip.compress(text.encode("utf-8"), mtime=0))
        rows.append(dict(pdf=rel, pdf_sha256=sha, pages=pages, chars=chars,
                         text_file=dest.relative_to(out).as_posix(), status=status))
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "index.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, ["pdf", "pdf_sha256", "pages", "chars", "text_file", "status"], lineterminator="\n")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["pdf"]))
    return rows


if __name__ == "__main__":
    rows = run()
    ok = sum(r["status"] == "ok" for r in rows)
    ocr = sum(r["status"].startswith("NEEDS_OCR") for r in rows)
    print(f"{len(rows)} PDFs: {ok} text extracted, {ocr} need OCR, {len(rows) - ok - ocr} errors -> {OUT}/index.csv")
