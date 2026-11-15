"""CC-30 (review finding M1): the card shows only predictions no layer states (novel), specific diseases before broad
categories (MONDO ancestors of a disease already linked, CS-5's flag), ranked 1..N; the predictions that restate a known pair
are a separate 'recovered' fold; the start page quotes outputs/linkpred/predictions_novelty.md instead of a hard-coded number."""
import gzip
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
import app as appmod  # noqa: E402
import pharos_app  # noqa: E402
from fixtures.pharos_fixture import build, GENE, DIS1, DIS2  # noqa: E402


def pred(o, score, rank, known=()):
    return dict(subject="G", object=o, other=o, other_label=o, score=score, rank=rank, known_in=list(known), paths=[])


def card(preds):
    edges = [dict(id=f"pred:G|{x['object']}", source="G", target=x["object"], predicted=True) for x in preds]
    edges.append(dict(id="e1", source="G", target="K", predicted=False))
    nodes = [dict(id=i) for i in ["G", "K"] + [x["object"] for x in preds]]
    return dict(about=dict(id="G"), summary=dict(predicted=len(preds)), predicted=preds, picture=dict(nodes=nodes, edges=edges))


def test_split_keeps_novel_links_ranks_them_and_moves_known_ones_out():
    c = card([pred("A", 0.9, 4, known=["pharos"]), pred("BROAD", 0.8, 5), pred("C", 0.7, 9), pred("D", 0.6, 2, known=["pharos"])])
    out = pharos_app.split_predictions(c, ancestors={("G", "BROAD")}, n=10)
    assert [x["object"] for x in out["predicted"]] == ["C", "BROAD"]            # specific first, then the broad category
    assert [x["rank"] for x in out["predicted"]] == [1, 2] and [x["model_rank"] for x in out["predicted"]] == [9, 5]
    assert [x["broad_category"] for x in out["predicted"]] == [False, True]
    assert [x["object"] for x in out["recovered_known"]] == ["A", "D"] and [x["rank"] for x in out["recovered_known"]] == [1, 2]
    assert out["prediction_counts"] == dict(considered=4, novel=2, broad_category=1, recovered_known=2)
    assert out["summary"]["predicted"] == 2 and out["summary"]["recovered_known"] == 2
    ids = {e["id"] for e in out["picture"]["edges"]}
    assert ids == {"e1", "pred:G|C", "pred:G|BROAD"} and {n["id"] for n in out["picture"]["nodes"]} == {"G", "K", "C", "BROAD"}
    assert c["predicted"][0]["rank"] == 4                                       # the input card is not changed
    assert len(pharos_app.split_predictions(c, n=1)["predicted"]) == 1


def test_all_known_says_none_is_new():
    out = pharos_app.split_predictions(card([pred("A", 0.9, 1, known=["pharos"])]))
    assert out["predicted"] == [] and "none is new" in out["prediction_note"]


def test_ancestor_pairs_read_the_flag(tmp_path):
    p = tmp_path / "n.tsv.gz"
    with gzip.open(p, "wt") as f:
        f.write("subject\tobject\tancestor_of_known\nG\tA\tyes\nG\tB\tno\n")
    assert pharos_app.ancestor_pairs(p) == {("G", "A")} and pharos_app.ancestor_pairs(tmp_path / "none") == set()


def test_novelty_summary_quotes_the_committed_file():
    s = pharos_app.novelty_summary(ROOT / pharos_app.NOVELTY_MD)
    assert s and s["novel_specific"] and s["all"] and s["novel"] <= s["all"]
    assert f"{s['novel_specific']:,}" in s["sentence"] and f"{s['all']:,}" in s["sentence"]
    assert pharos_app.novelty_summary(ROOT / "nope.md") is None


@pytest.fixture
def client(tmp_path, monkeypatch):
    root = build(tmp_path / "repo")
    monkeypatch.setenv("TMKG_PHAROS_ROOT", str(root))
    monkeypatch.setenv("TMKG_PHAROS_INDEX", "off")
    appmod._pharos.clear()
    yield TestClient(appmod.app)
    appmod._pharos.clear()


def test_the_card_endpoint_shows_novel_only(client):
    gene = client.get(f"/api/pharos/card/{GENE}").json()          # PTGS2 -> T2D: CTD already states the pair
    assert gene["predicted"] == [] and [x["object"] for x in gene["recovered_known"]] == [DIS2]
    assert not any(e.get("predicted") for e in gene["picture"]["edges"])
    sod1 = client.get("/api/pharos/card/NCBIGene:6647").json()     # SOD1 -> Alzheimer: no layer states it
    assert [x["object"] for x in sod1["predicted"]] == [DIS1] and sod1["predicted"][0]["rank"] == 1
    st = client.get("/api/pharos/status").json()
    assert st["novelty"]["sentence"] and st["novelty"]["file"] == "predictions_novelty.md"


def test_pages_never_call_a_known_pair_new():
    for name in ("app_page.html", "app_page_v3.html"):
        t = (ROOT / "pipelines" / name).read_text()
        assert "predicted new links" not in t and "new: predicted" not in t and "ph.novelty.sentence" in t, name
    v3 = (ROOT / "pipelines" / "app_page_v3.html").read_text()
    assert "The model also recovers" in v3 and "broad category of a disease already linked" in v3
