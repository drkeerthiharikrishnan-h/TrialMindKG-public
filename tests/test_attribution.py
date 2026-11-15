"""CC-40: the data attribution footer (pipelines/attribution.py), its README block, the app endpoint and the pages."""
import gzip
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
import attribution as A  # noqa: E402


def test_versions_come_from_committed_files():
    v = A.versions(ROOT)
    assert v["pharos_version"] == "Pharos 3.19.2 / TCRD 6.13.5"        # sources/pharos.yaml
    assert v["ot_version"] == "26.09" and v["chembl_version"] == "37"
    assert v["ctgov_date"] == "2026-10-05"                               # outputs/run_manifest.json
    assert v["gtex_date"] == "2026-10-05"                                # data/snapshots_adc_demo/gtex fetched_at
    assert v["pharos_date"] == "2026-10-25"
    # HPO: the release date when data/ontologies/hpo/version.txt is present (the Mac: releases/2026-09-01), else the manifest's
    # hp.json download date (the cloud, no data)
    assert v["hpo_version"] == "2026-09-01" or "downloaded 2026-10-25" in v["hpo_version"]


def test_missing_files_say_not_recorded(tmp_path):
    (tmp_path / "data" / "curated").mkdir(parents=True)
    (tmp_path / A.ROWS).write_text("source,what,licence,attribution,url,conditions,checked\n"
                                   "GTEx,x,cite,obtained on {gtex_date},https://gtexportal.org/home/license,,\n")
    assert set(A.versions(tmp_path).values()) == {A.MISSING}
    f = A.footer(tmp_path)
    assert f["items"][0]["text"] == f"obtained on {A.MISSING}"


def test_hpo_release_from_prepare_report_wins(tmp_path):
    p = tmp_path / "outputs" / "hpo_hierarchy" / "prepare.md"
    p.parent.mkdir(parents=True)
    p.write_text("# hpo\n\nRelease (version IRI in the file): http://purl.obolibrary.org/obo/hp/releases/2026-09-01/hp.json\n")
    assert A.versions(tmp_path)["hpo_version"] == "2026-09-01"


def test_gtex_date_range(tmp_path):
    d = tmp_path / "data" / "snapshots_adc_demo" / "gtex"
    d.mkdir(parents=True)
    for i, day in enumerate(("2026-10-05", "2026-10-07")):
        with gzip.open(d / f"{i}.json.gz", "wt") as f:
            json.dump(dict(fetched_at=f"{day}T00:00:00Z", body={}), f)
    assert A.versions(tmp_path)["gtex_date"] == "2026-10-05 to 2026-10-07"


def test_footer_has_every_required_notice():
    f = A.footer(ROOT)
    by = {i["source"]: i for i in f["items"]}
    assert "This service/product uses the Human Phenotype Ontology (version " in by["Human Phenotype Ontology"]["text"]
    assert by["STRING"]["licence"] == "CC BY 4.0" and by["JensenLab DISEASES"]["licence"] == "CC BY 4.0"
    assert by["openFDA (FAERS, Drugs@FDA)"]["licence"] == "CC0 1.0"
    assert "non-commercial" in by["CTD"]["conditions"] and "ctdbase.org" in by["CTD"]["text"]
    assert "CC BY-NC-SA 4.0" in by["DisGeNET"]["licence"] and "non-commercial" in by["DisGeNET"]["conditions"]
    assert "obtained from the GTEx Portal on 2026-10-05" in by["GTEx"]["text"]
    assert "data processed 2026-10-05" in by["ClinicalTrials.gov"]["text"]
    assert "no explicit licence" in by["TIN-X"]["licence"]
    assert all(i["url"].startswith("https://") and "{" not in i["text"] for i in f["items"])
    assert "code only" in f["notice"] and A.footer_text(f).count("\n- ") == len(f["items"])


def test_committed_json_and_readme_match_the_generator():
    f = A.footer(ROOT)
    assert A.load_footer(ROOT) == json.loads(json.dumps(f)), "run: python pipelines/attribution.py --write"
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert A.render_readme(text, A.readme_block(f)) == text, "run: python pipelines/attribution.py --write"
    with pytest.raises(ValueError):
        A.render_readme("no markers", "x")


def test_app_serves_the_footer_and_the_licence_file():
    from fastapi.testclient import TestClient
    import app as appmod
    c = TestClient(appmod.app)
    r = c.get("/api/attribution")
    assert r.status_code == 200 and r.json()["items"] == A.load_footer(ROOT)["items"]
    r = c.get("/DATA_LICENSES.md")
    assert r.status_code == 200 and r.text.startswith("# Data licences")


@pytest.mark.parametrize("page", ["app_page.html", "app_page_v3.html"])
def test_pages_render_the_footer(page):
    s = (ROOT / "pipelines" / page).read_text(encoding="utf-8")
    assert 'id="attribution"' in s and 'fetch("/api/attribution")' in s and "loadAttribution();" in s
    assert s.index('id="attribution"') > s.index("</main>")


def test_hpo_release_falls_back_to_the_committed_footer(tmp_path):
    """A checkout without data/ontologies/hpo/version.txt (the cloud) reads the release the committed footer recorded, so
    `attribution.py --write` reproduces the committed file; only a release date counts, never a "downloaded" fallback."""
    out = tmp_path / A.OUT
    out.parent.mkdir(parents=True)
    out.write_text(json.dumps({"versions": {"hpo_version": "2026-09-01"}}))
    assert A._hpo_version(tmp_path) == "2026-09-01"
    out.write_text(json.dumps({"versions": {"hpo_version": "of the hp.json file downloaded 2026-10-25"}}))
    assert A._hpo_version(tmp_path) == ""
    p = tmp_path / "data" / "ontologies" / "hpo" / "version.txt"
    p.parent.mkdir(parents=True)
    p.write_text("http://purl.obolibrary.org/obo/hp/releases/2026-11-01/hp.json\n")
    assert A._hpo_version(tmp_path) == "2026-11-01"                      # the Mac's file still wins
