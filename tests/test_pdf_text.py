"""pdf_text: deterministic text extraction with page markers; scanned PDFs flagged, unchanged PDFs skipped."""
import csv, gzip
import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["pdf.fonttype"] = 42             # real text in the PDF (extractable)
from matplotlib.backends.backend_pdf import PdfPages
import matplotlib.pyplot as plt
import pdf_text


def make_pdf(path, pages):
    with PdfPages(path) as pdf:
        for text in pages:
            fig = plt.figure()
            if text:
                fig.text(0.1, 0.5, text)
            pdf.savefig(fig)
            plt.close(fig)


def test_extracts_text_with_page_markers_and_flags_scans(tmp_path):
    raw, out = tmp_path / "raw", tmp_path / "raw_text"
    (raw / "P19").mkdir(parents=True)
    make_pdf(raw / "P19" / "sap.pdf", ["Primary endpoint SRI-4 at week 52 was not met.",
                                       "Subgroup: IFN gene signature high, prespecified."])
    make_pdf(raw / "P19" / "scan.pdf", ["", ""])
    rows = {r["pdf"]: r for r in pdf_text.run(raw, out)}
    assert rows["P19/sap.pdf"]["status"] == "ok" and rows["P19/scan.pdf"]["status"].startswith("NEEDS_OCR")
    text = gzip.decompress((out / "P19/sap.pdf.txt.gz").read_bytes()).decode()
    assert "=== page 2 ===" in text and "SRI-4" in text and "prespecified" in text
    first = (out / "P19/sap.pdf.txt.gz").read_bytes()
    pdf_text.run(raw, out)                                     # second run: unchanged PDF skipped, same bytes
    assert (out / "P19/sap.pdf.txt.gz").read_bytes() == first
    assert len(list(csv.DictReader(open(out / "index.csv")))) == 2


def test_web_pages_and_office_files_become_text(tmp_path):
    import pdf_text
    raw, out = tmp_path / "raw", tmp_path / "out"
    (raw / "P01_x" / "sources").mkdir(parents=True)
    (raw / "P01_x" / "sources" / "01_press.html").write_text("<html><body><p>Topline results: the trial did not meet "
                                                             "its primary endpoint in the overall population.</p></body></html>")
    rows = pdf_text.run(raw, out)
    assert rows[0]["status"] == "ok" and "did not meet" in __import__("gzip").decompress(
        (out / rows[0]["text_file"]).read_bytes()).decode()
