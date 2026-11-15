"""Tests for pipelines/doc_discovery.py (pure parsers; no network)."""
import doc_discovery as dd

JATS = b"""<article xmlns:xlink="http://www.w3.org/1999/xlink"><body>
<sec><title>Results</title><p>x</p>
<fig id="f1"><label>Figure 1</label><caption><p>Flow</p></caption><graphic xlink:href="fig1.jpg"/></fig></sec>
<sec sec-type="data-availability"><title>Data availability</title><p>Code at https://github.com/lab/repo and data on request.</p></sec>
</body><back><supplementary-material id="s1"><label>Table S1</label><caption><p>Raw</p></caption>
<media xlink:href="table_s1.xlsx" mimetype="application" mime-subtype="vnd.ms-excel"/></supplementary-material></back></article>"""


def test_parse_fulltext():
    info = dd.parse_fulltext(JATS)
    assert info["supplements"] == [dict(file="table_s1.xlsx", mimetype="application/vnd.ms-excel", label="Table S1", caption="Raw")]
    assert info["figures"][0]["file"] == "fig1.jpg" and info["figures"][0]["label"] == "Figure 1"
    assert "github.com/lab/repo" in info["data_availability"]


def test_supplement_kind():
    assert dd.supplement_kind("a.xlsx") == "data table"
    assert dd.supplement_kind("protocol.pdf").startswith("document")
    assert dd.supplement_kind("f.TIF") == "image"


def test_keys_and_categories():
    assert dd.epmc_key({"pmid": "123", "source": "MED", "id": "123"}) == "PMID_123"
    assert dd.epmc_key({"source": "PPR", "id": "PPR9"}) == "PPR_PPR9"
    assert dd.category_of({"source": "PPR"}) == "preprint"
    assert dd.category_of({"source": "MED", "pubType": "abstract"}) == "conference abstract"
    assert dd.category_of({"source": "MED", "pubTypeList": {"pubType": ["research-article", "Journal Article"]}}) == "publication"


def test_other_registries():
    study = {"protocolSection": {"identificationModule": {"secondaryIdInfos": [
        {"id": "2017-001364-38", "type": "EUDRACT_NUMBER"}, {"id": "WN29922", "type": "OTHER", "domain": "Roche"}]}}}
    regs = dd.other_registries(study)
    assert regs[0]["link"].endswith("query=2017-001364-38") and regs[1]["link"] == ""
