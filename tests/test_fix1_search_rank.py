"""FIX1 F9 (gate 1, G1-P1-03): search puts the disease above a same-named phenotype.

Live, "Alzheimer disease" listed HP:0002511 (phenotype, 5 links) first, a KEGG pathway, then MONDO:0004975 (disease, 19,928
links); a clinician opened the near-empty phenotype card. Same labels are now ordered Disease > Gene > Protein > Phenotype >
others, then by link count, and each suggestion carries its link count for the page's type tag."""
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
sys.path.insert(0, str(ROOT / "tests"))
import pharos_app  # noqa: E402
from fixtures.pharos_fixture import build  # noqa: E402
from pharos_index import build_index  # noqa: E402

LIVE = [dict(id="HP:0002511", label="Alzheimer disease", category="biolink:PhenotypicFeature"),
        dict(id="KEGG.PATHWAY:hsa05010", label="Alzheimer disease", category="biolink:Pathway"),
        dict(id="MONDO:0004975", label="Alzheimer disease", category="biolink:Disease")]
LINKS = {"HP:0002511": 5, "KEGG.PATHWAY:hsa05010": 120, "MONDO:0004975": 19928}


def test_the_disease_comes_first_then_the_phenotype_then_the_pathway():
    out = pharos_app.rank_same_label(LIVE, LINKS)
    assert [h["id"] for h in out] == ["MONDO:0004975", "HP:0002511", "KEGG.PATHWAY:hsa05010"]
    assert out[0]["links"] == 19928


def test_other_labels_keep_their_place_and_link_count_breaks_ties():
    hits = [dict(id="X:1", label="Alzheimer disease 2", category="biolink:Disease"), *LIVE,
            dict(id="MONDO:9", label="alzheimer disease", category="biolink:Disease")]
    out = pharos_app.rank_same_label(hits, dict(LINKS, **{"MONDO:9": 3}))
    assert out[0]["id"] == "X:1"                                   # its own label: not moved
    assert [h["id"] for h in out[1:]] == ["MONDO:0004975", "MONDO:9", "HP:0002511", "KEGG.PATHWAY:hsa05010"]
    assert pharos_app.rank_same_label([], {}) == []
    assert "links" not in pharos_app.rank_same_label(LIVE[:1])[0]  # no count known: none shown


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    return build(tmp_path_factory.mktemp("repo"))


@pytest.mark.parametrize("use_index", [False, True])
def test_the_search_route_carries_link_counts(repo, use_index, monkeypatch):
    import app as appmod
    monkeypatch.setenv("TMKG_PHAROS_ROOT", str(repo))
    if use_index:
        monkeypatch.setenv("TMKG_PHAROS_INDEX", str(build_index(repo, chunk=3, log=lambda *_: None)["path"]))
    else:
        monkeypatch.setenv("TMKG_PHAROS_INDEX", "off")
    appmod._pharos.clear()
    try:
        hits = TestClient(appmod.app).get("/api/pharos/search", params={"q": "PTGS2"}).json()
        assert hits and all(isinstance(h.get("links"), int) for h in hits)
        assert next(h for h in hits if h["id"] == "NCBIGene:5743")["links"] > 0
    finally:
        appmod._pharos.clear()


def test_the_page_shows_type_and_link_count():
    page = (ROOT / "pipelines" / "app_page_v3.html").read_text(encoding="utf-8")
    assert "const tagOf = h => esc(noun(h.category)) + (h.links != null" in page
