"""Tests for pipelines/raw_docs.py renderers and the committed raw/ folder."""
import csv
import raw_docs as rd
import seed_kg as sk
from snapshot import Snapshot

TULIP1 = Snapshot(mode="offline").get("ctgov", "https://clinicaltrials.gov/api/v2/studies/NCT02446912").body


def test_slug_and_abstract():
    assert rd.slug("Solanezumab — EXPEDITION3") == "Solanezumab_EXPEDITION3"
    assert rd.abstract_text("<h4>Background</h4>Text <i>x</i>") == "**Background** Text x"


def test_registry_summary_keeps_registry_numbers():
    md = rd.registry_summary_md(TULIP1, [("Study Protocol", "https://x/Prot_000.pdf", "documents/Prot_000.pdf")])
    assert "p = 0.412" in md and "| participants analysed | 93 | 180 | 184 |" in md
    assert "documents/Prot_000.pdf" in md and "https://clinicaltrials.gov/study/NCT02446912" in md


def test_fulltext_md():
    xml = (b"<article><front><article-meta><title-group><article-title>T</article-title></title-group>"
           b"<abstract><p>A</p></abstract></article-meta></front><body><sec><title>Methods</title><p>M</p>"
           b"</sec></body></article>")
    md = rd.fulltext_md(xml)
    assert md.startswith("# T") and "## Methods" in md and "M\n" in md


def test_publication_md_without_record():
    assert "(no Europe PMC record)" in rd.publication_md("1", None, ["x"])


def test_raw_index_covers_every_program_and_trial():
    rows = list(csv.DictReader(open(sk.ROOT / "raw" / "index.csv", encoding="utf-8")))
    assert len({r["program"] for r in rows}) == 34
    summaries = {r["trial"] for r in rows if r["doc_type"] == "registry summary (readable)"}
    assert len(summaries) == 45
    for r in rows:                                   # every file the index calls present really exists
        if r["status"].startswith("ok") and not r["local_path"].endswith(".pdf"):
            assert (sk.ROOT / "raw" / r["local_path"]).exists(), r["local_path"]


def test_categorize_url():
    assert rd.categorize_url("https://www.fda.gov/media/1/download") == "FDA documents"
    assert rd.categorize_url("https://www.nejm.org/doi/x") == "Trial publications"
    assert rd.categorize_url("https://www.roche.com/media/releases/x") == "Press releases"
    assert rd.categorize_url("https://www.repository.cam.ac.uk/items/x") == "Derived data / code repositories"


def test_status_of():
    assert rd.status_of({"status": "ok"}) == rd.DOWNLOADED
    assert rd.status_of({"status": "not reachable from this computer (ProxyError)"}) == rd.TO_FETCH
    assert rd.status_of({"status": "HTTP 403 (may need a browser or subscription)"}) == rd.BLOCKED
