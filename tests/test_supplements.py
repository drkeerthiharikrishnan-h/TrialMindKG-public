"""Tests for pipelines/supplements.py (no network)."""
import io, zipfile
import supplements as s


def _docx(text):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("word/document.xml", f"<w:document><w:t>{text}</w:t></w:document>")
    return b.getvalue()


def test_entries_and_text_from_normal_and_streamed_zips():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("s1.docx", _docx("Supplementary Table 1 baseline characteristics"))
        z.writestr("fig1.jpg", b"\xff\xd8 image")
        z.writestr("t.csv", "a,b\n1,2\n")
    data = b.getvalue()
    names = [n for n, _ in s.entries(data)]
    assert names == ["s1.docx", "fig1.jpg", "t.csv"]
    cut = data[:data.find(b"PK\x01\x02")]                     # no central directory, as the API streams it
    got = dict(s.entries(cut))
    assert "Supplementary Table 1" in s.to_text("s1.docx", got["s1.docx"])
    assert s.to_text("fig1.jpg", got["fig1.jpg"]) is None and s.to_text("t.csv", got["t.csv"]) == "a,b\n1,2\n"


def test_articles_come_from_the_raw_index():
    a = s.articles()
    assert len(a) > 300 and all(k.startswith("PMC") and v.startswith("P") for k, v in a.items())
