"""Web app: report, evidence card, trial view, PDF export and status (offline stand-ins, no network)."""
import pytest
from fastapi.testclient import TestClient
import app as appmod
from kg_query import KGIndex
from nim import NIM


@pytest.fixture(autouse=True)
def no_pharos_graph_or_index(monkeypatch):
    """These tests assume "no Pharos graph": they must not depend on what is built in the working copy (on the Mac a real
    data/kg/pharos_index/card_index.sqlite exists and the app would find it). The index is switched off and the app's cached graph
    and index are reset before and after every test."""
    monkeypatch.setenv("TMKG_PHAROS_INDEX", "off")
    monkeypatch.delenv("TMKG_PHAROS_ROOT", raising=False)
    appmod._pharos.clear()
    yield
    appmod._pharos.clear()


@pytest.fixture(scope="module")
def client():
    appmod._state.update(kg=KGIndex(), nim=NIM(backend="offline"))
    return TestClient(appmod.app)


def test_home_page_and_status(client):
    assert "TrialMindKG" in client.get("/").text
    s = client.get("/api/status").json()
    assert s["backend"] == "offline" and s["counts"]["edges"] > 0 and "provenance_completeness" in s["quality"]


def test_ask_returns_report_with_evidence_cards(client):
    r = client.post("/api/ask", json={"question": "Why did TULIP-1 miss its primary endpoint, and what changed later?"}).json()
    claims = [c for s in r["sections"] for c in s["claims"]]
    assert claims and r["markdown"].startswith("# TrialMindKG")
    card = client.get("/api/evidence/" + claims[0]["evidence"][0]["id"]).json()
    assert card["biolink"]["primary_knowledge_source"] and card["source"] and card["statement"]
    assert client.get("/api/evidence/e0000000000000000").status_code == 404


def test_pdf_export_and_trial_view(client):
    pdf = client.get("/api/report.pdf", params={"q": "Did a genotype-defined population rescue dalcetrapib in dal-GenE?"})
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    t = client.get("/api/trial/NCT02446912").json()
    assert t["outcomes"] and t["drugs"]


def test_query_endpoint(client):
    r = client.post("/api/query", json={"question": "What was the primary outcome of TULIP-1?"}).json()
    assert r["problems"] == [] and r["evidence"]


def test_graph_endpoints_and_report_subgraphs(client):
    hits = client.get("/api/search", params={"q": "TULIP"}).json()
    assert hits and hits[0]["id"] == "P19"
    nb = client.get("/api/neighbourhood/P19").json()
    assert nb["center"] == "P19" and any(e["predicate"] == "tmkg:has_trial" for e in nb["edges"])
    ids = {n["id"] for n in nb["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in nb["edges"])
    r = client.post("/api/ask", json={"question": "Why did TULIP-1 miss its primary endpoint?"}).json()
    g = r["sections"][0]["graph"]
    assert g["nodes"] and len(g["edges"]) == r["sections"][0]["subgraph"]["edges"]
    page = client.get("/").text
    assert "Explore the trial graph" in page and "function layout" in page and "cdnjs" not in page


def test_graph_path_flow_links_build_and_subgraph_export():
    import app, orchestrator, zipfile, io
    from nim import NIM
    kg = app.state()[0]
    rep = orchestrator.ask("Why did TULIP-1 miss its primary endpoint, and what changed later?", kg=kg,
                           nim=NIM(backend="offline"))
    assert "flow_links" in rep and all(l["source"]["role"] != l["target"]["role"] for l in rep["flow_links"])
    drug = kg.out("P19", "tmkg:has_intervention")[0]["object"]
    far = kg.out(drug, "biolink:interacts_with")[0]["id"]           # anifrolumab -> its target: two hops from P19
    path = app.graph_path(kg, far, ["P19"])
    assert [p["id"] for p in path] == [kg.out("P19", "tmkg:has_intervention")[0]["id"], far]
    assert app.graph_path(kg, kg.out("P19", "tmkg:has_intervention")[0]["id"], ["P19"]) == []
    st = app.status(kg)
    assert st["validation_failures"] == [] and st["coverage_by_type"]
    z = zipfile.ZipFile(io.BytesIO(app.report_subgraph_zip(kg, rep)))
    assert "neptune/opencypher/edges.csv" in z.namelist() and "README.txt" in z.namelist()


def test_ask_stream_sends_steps_then_report():
    from fastapi.testclient import TestClient
    import app
    with TestClient(app.app) as c:
        r = c.get("/api/ask_stream", params={"q": "Why did TULIP-1 miss its primary endpoint?"})
        text = r.text
    assert text.index("event: step") < text.index("event: report") and "report_node" in text


def test_personas_wording_and_trials_for(client):
    ps = client.get("/api/personas").json()["personas"]
    assert len(ps) == 5 and all(p["questions"] and p["start_tab"] for p in ps)
    w = client.get("/api/wording").json()
    assert w["predicates"]["gene_associated_with_condition"] == "is associated with" and w["evidence_kinds"]
    t = client.get("/api/trials_for?names=anifrolumab").json()["trials"]
    assert t and any("TULIP" in x["label"] for x in t) and t[0]["via"]
    assert client.get("/api/trials_for?ids=nothing:here").json()["trials"] == []
    home = client.get("/").text
    assert "Ask the graph" in home and "Or start as a role" in home and "Pharos graph" in home


def test_results_page_without_the_pharos_graph(client, tmp_path):
    appmod._pharos.pop("g", None)                       # another test module may have loaded the fixture graph
    r = client.get("/api/results").json()
    assert r["schema"] is None and r["verdicts"] is None and "runs" in r["benchmark"]
    assert all("@frozen_" in x["file"] for x in r["benchmark"]["runs"])          # smoke runs never shown
    assert r["calibration"] is None or r["calibration"]["sentences"]
    assert any(f["name"] == "kg_edges.csv" for f in r["files"]) and all(f["bytes"] > 0 for f in r["files"])
    assert client.get("/api/file?name=kg_edges.csv").status_code == 200
    assert client.get("/api/file?name=../CLAUDE.md").status_code == 404        # only the allow-list is served
    assert client.get("/api/file?name=compare.md").status_code in (200, 404)   # present only after a run
    assert client.get("/api/file?name=G0_B_kfold.json").status_code == 404     # smoke result: not on the list
    assert client.get("/api/file?name=prune_calibration.json").status_code == 200


def test_results_page_falls_back_to_the_image_copy_when_the_data_root_has_no_results(client, tmp_path, monkeypatch):
    """CC-32 (review C1): on the hosted app TMKG_PHAROS_ROOT holds the graph but no outputs/linkpred; the Numbers page
    then reads the repo's committed result files shipped in the image instead of showing an empty benchmark."""
    appmod._pharos.pop("g", None)
    monkeypatch.setenv("TMKG_PHAROS_ROOT", str(tmp_path))
    committed = sorted(p.name for p in (appmod.ROOT / "outputs" / "linkpred").glob("*@frozen_*.json"))
    r = client.get("/api/results").json()
    assert sorted(x["file"] for x in r["benchmark"]["runs"]) == committed
    assert set(committed) <= set(r["fallback_files"])
    if committed:
        assert client.get("/api/file?name=" + committed[0]).status_code == 200
    assert client.get("/api/file?name=kg_edges.csv").status_code == 200
    assert client.get("/api/file?name=G0_B_kfold.json").status_code == 404


def test_results_page_carries_the_experiment_rows_and_the_page_labels_them(client):
    appmod._pharos.pop("g", None)
    r = client.get("/api/results").json()
    ids = [e["id"] for e in r["experiments"]]
    assert ids[:5] == ["E003", "E004", "E005", "E006", "E008"] and {"E008-G3", "E008-MONDO", "E009"} <= set(ids)
    release = [appmod.ROOT / "outputs" / "linkpred" / f"{g}_B_kfold@diag_gnnpath_1m_release.json" for g in ("G2", "G3")]
    assert ("D" in ids) == any(p.exists() for p in release)              # D hidden until a release file is committed
    assert all(e["status"] in ("frozen protocol", "EXPLORATORY") for e in r["experiments"])
    home = client.get("/").text
    assert "Further link-prediction results" in home and "EXPLORATORY" in home


def test_a_built_card_index_in_the_working_copy_does_not_change_these_tests():
    """The isolation fixture holds: the index is off for the app even when the default index file exists."""
    import pharos_index
    assert appmod.card_index() is None and "g" not in appmod._pharos
    assert pharos_index.index_path(appmod.ROOT) is not None
