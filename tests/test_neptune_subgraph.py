"""CC-9: pipelines/neptune_subgraph.py on a fixture graph (no real data, no network): kinase selection, Neptune
openCypher CSV format, count manifest checked by aws/neptune_load.sh, and local-vs-loaded parity of the questions."""
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, "pipelines")
import neptune_subgraph as ns  # noqa: E402
import querier  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")

N = [  # id, category, name, idg_family
    ("UniProtKB:K1", "biolink:Protein", "ABL1", "Kinase"),
    ("UniProtKB:K2", "biolink:Protein", "EGFR", "kinase"),
    ("UniProtKB:G1", "biolink:Protein", "GPCR one", "GPCR"),
    ("MONDO:1", "biolink:Disease", "leukemia, \"chronic\"", ""),
    ("MONDO:2", "biolink:Disease", "lung cancer\nnote", ""),
    ("MONDO:3", "biolink:Disease", "far away", ""),
    ("CHEMBL:1", "biolink:SmallMolecule", "imatinib", ""),
    ("CHEMBL:2", "biolink:SmallMolecule", "gefitinib", ""),
]
E = [  # id, subject, predicate, object, verdict, confidence
    ("e1", "UniProtKB:K1", "biolink:gene_associated_with_condition", "MONDO:1", "keep", "0.9"),
    ("e2", "UniProtKB:K2", "biolink:gene_associated_with_condition", "MONDO:2", "keep", "0.8"),
    ("e3", "UniProtKB:K2", "biolink:gene_associated_with_condition", "MONDO:1", "prune", "0.1"),
    ("e4", "CHEMBL:1", "biolink:interacts_with", "UniProtKB:K1", "", ""),                  # layer never pruned: kept
    ("e5", "CHEMBL:2", "biolink:interacts_with", "UniProtKB:K2", "keep", "0.7"),
    ("e6", "UniProtKB:G1", "biolink:gene_associated_with_condition", "MONDO:3", "keep", "0.9"),   # no kinase end
    ("e7", "MONDO:1", "biolink:related_to", "MONDO:3", "keep", "0.5"),                      # neighbour-neighbour: 2 hops
    ("e8", "UniProtKB:K1", "biolink:interacts_with", "UniProtKB:K2", "keep", "0.6"),        # seed-seed
    ("e9", "UniProtKB:K1", "biolink:interacts_with", "UniProtKB:GHOST", "keep", "0.6"),     # far end has no node row
]


@pytest.fixture
def tables():
    nodes = pd.DataFrame(N, columns=["id", "category", "name", "idg_family"]).assign(provided_by="infores:pharos")
    edges = pd.DataFrame(E, columns=["id", "subject", "predicate", "object", "verdict", "confidence"]).assign(
        primary_knowledge_source="infores:x", knowledge_level="knowledge_assertion", agent_type="manual_agent",
        source="s", source_record="r", layer="L0", reasons="")
    return nodes, edges


def test_selection_is_the_one_hop_neighbourhood_over_non_pruned_edges(tables):
    nodes, edges = tables
    sn, se, rep = ns.select_subgraph(nodes, edges, "Kinase")
    assert list(se["id"]) == ["e1", "e2", "e4", "e5", "e8"]                 # e3 pruned, e6/e7 not touching, e9 no node
    assert set(sn["id"]) == {"UniProtKB:K1", "UniProtKB:K2", "MONDO:1", "MONDO:2", "CHEMBL:1", "CHEMBL:2"}
    assert (rep["seeds"], rep["edges_pruned_excluded"], rep["edges_missing_endpoint"]) == (2, 1, 1)
    assert rep["nodes"] == len(sn) and rep["edges"] == len(se)
    assert "UniProtKB:G1" not in set(sn["id"]) and "MONDO:3" not in set(sn["id"])
    # every kept edge has both endpoints in the node table
    assert set(se["subject"]) | set(se["object"]) <= set(sn["id"])


def test_other_family_and_missing_family_column(tables):
    nodes, edges = tables
    sn, se, rep = ns.select_subgraph(nodes, edges, "GPCR")
    assert list(se["id"]) == ["e6"] and rep["seeds"] == 1
    with pytest.raises(ValueError):
        ns.select_subgraph(nodes.drop(columns=["idg_family"]), edges)


def _write(tables, tmp_path):
    sn, se, rep = ns.select_subgraph(*tables)
    npath, epath = ns.write_opencypher(sn, se, tmp_path / "x" / "opencypher")
    return sn, se, rep, npath, epath


def test_csv_is_valid_neptune_opencypher_format(tables, tmp_path):
    sn, se, rep, npath, epath = _write(tables, tmp_path)
    assert ns.validate_opencypher(npath, epath) == []
    nh = npath.read_text().splitlines()[0].split(",")
    eh = epath.read_text().splitlines()[0].split(",")
    assert nh[:3] == [":ID", ":LABEL", "id:String"] and eh[:5] == [":ID", ":START_ID", ":END_ID", ":TYPE", "id:String"]
    assert "confidence:Double" in eh and "verdict:String" in eh and "primary_knowledge_source:String" in eh
    assert "\r" not in npath.read_text() and "lung cancer\\nnote" in npath.read_text()      # newline escaped, quote kept


def test_validator_catches_each_load_problem(tables, tmp_path):
    sn, se, rep, npath, epath = _write(tables, tmp_path)
    good_n, good_e = npath.read_text(), epath.read_text()

    def bad(n=good_n, e=good_e):
        npath.write_text(n), epath.write_text(e)
        return " | ".join(ns.validate_opencypher(npath, epath))

    assert "is not a node" in bad(e=good_e.replace("CHEMBL:1,UniProtKB:K1", "CHEMBL:9,UniProtKB:K1", 1))
    assert "duplicate :ID" in bad(n=good_n + good_n.splitlines()[1] + "\n")
    assert "not a number" in bad(e=good_e.replace(",0.9,", ",high,", 1))
    assert "header must start" in bad(n=good_n.replace(":LABEL", "label", 1))
    assert "unknown type" in bad(e=good_e.replace("confidence:Double", "confidence:Float"))
    assert "empty :LABEL" in bad(n=good_n.replace(",Disease,", ",,", 1))
    assert bad() == ""


def test_output_is_byte_deterministic(tables, tmp_path):
    a = _write(tables, tmp_path / "a")
    b = _write(tables, tmp_path / "b")
    assert a[3].read_bytes() == b[3].read_bytes() and a[4].read_bytes() == b[4].read_bytes()


def test_manifest_counts_hashes_and_location(tables, tmp_path):
    sn, se, rep, npath, epath = _write(tables, tmp_path)
    man = ns.write_manifest(npath.parent, rep, sn, se, "GT")
    on_disk = json.loads((npath.parent.parent / "manifest.json").read_text())
    assert on_disk == man and not (npath.parent / "manifest.json").exists()      # not in the folder the loader reads
    nrows = len(npath.read_text().strip().splitlines()) - 1
    erows = len(epath.read_text().strip().splitlines()) - 1
    assert man["counts"] == {"nodes": nrows, "edges": erows} == {"nodes": len(sn), "edges": len(se)}
    import hashlib
    assert man["files"]["nodes.csv"] == hashlib.sha256(npath.read_bytes()).hexdigest()


def test_parity_questions_are_built_from_the_data_and_return_the_same_edges_after_the_csv_round_trip(tables, tmp_path):
    sn, se, rep, npath, epath = _write(tables, tmp_path)
    qs = ns.parity_questions(sn, se)
    assert qs and len(qs) <= 4 and {q["id"] for q in qs} == {f"K0{i + 1}" for i in range(len(qs))}
    local = ns.index_from_tables(sn, se)
    loaded = ns.load_into_index(npath, epath)
    seen_evidence = set()
    for q in qs:
        card = querier.schema_card(local)
        assert querier.validate(q["cypher"], card) == [], q
        a, b = querier.run_local(q["cypher"], local), querier.run_local(q["cypher"], loaded)
        assert a["evidence"] and a["evidence"] == b["evidence"] and a["rows"] == b["rows"], q["id"]
        seen_evidence |= set(a["evidence"])
    assert seen_evidence <= set(se["id"]) and "e3" not in seen_evidence             # a pruned edge is never an answer


def test_manifest_parity_digest_matches_the_loaded_csv(tables, tmp_path):
    sn, se, rep, npath, epath = _write(tables, tmp_path)
    man = ns.write_manifest(npath.parent, rep, sn, se, "GT")
    loaded = ns.load_into_index(npath, epath)
    for qid, entry in man["parity"].items():
        assert ns.evidence_digest(querier.run_local(entry["cypher"], loaded)) == \
            {k: entry[k] for k in ("n_rows", "n_evidence", "evidence_sha256")}, qid


# ---- aws/neptune_load.sh with the kinase manifest and source folder (stubbed endpoint, as tests/test_aws_kit.py) ----
CURL_STUB = (ROOT / "tests" / "test_aws_kit.py").read_text().split("CURL_STUB = r'''")[1].split("'''")[0]


@pytest.mark.skipif(BASH is None, reason="bash not found")
@pytest.mark.parametrize("delta,code", [(0, 0), (1, 1)])
def test_neptune_load_checks_the_kinase_manifest_and_loads_the_kinase_folder(tables, tmp_path, delta, code):
    sn, se, rep, npath, epath = _write(tables, tmp_path)
    man = ns.write_manifest(npath.parent, rep, sn, se, "GT")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "curl").write_text(CURL_STUB.replace("print(json.dumps({\"payload\": {\"loadId\": \"L1\"}}))",
                                                     "open(os.environ['STUB_LOG'], 'a').write(sys.argv[-1] + '\\n'); print(json.dumps({\"payload\": {\"loadId\": \"L1\"}}))"))
    (bindir / "curl").chmod(0o755)
    cfg = tmp_path / "stub.env"
    cfg.write_text("TMKG_BUCKET=b\nNEPTUNE_ENDPOINT=n.example\nNEPTUNE_LOAD_ROLE_ARN=arn:aws:iam::1:role/r\n")
    log = tmp_path / "loader.log"
    env = {**os.environ, "TMKG_AWS_CONFIG": str(cfg), "TMKG_S3_VERSION": "abc123", "STUB_LOG": str(log),
           "TMKG_NEPTUNE_SRC": "neptune_kinase/opencypher", "TMKG_MANIFEST": str(npath.parent.parent / "manifest.json"),
           "STUB_NODES": str(man["counts"]["nodes"] + delta), "STUB_EDGES": str(man["counts"]["edges"]),
           "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}
    r = subprocess.run([BASH, str(ROOT / "aws" / "neptune_load.sh")], capture_output=True, text=True, env=env, cwd=ROOT)
    assert r.returncode == code, r.stderr + r.stdout
    assert "outputs/neptune_kinase/opencypher/" in log.read_text()
    assert ("Counts match the manifest" in r.stdout) == (code == 0) and ("COUNT MISMATCH" in r.stderr) == (code == 1)


def test_build_end_to_end_from_layer_files(tmp_path, monkeypatch):
    """neptune_subgraph.build() on two tiny layers written like the real ones: pruned edges are assembled, marked and then
    left out of the subgraph; the manifest and CSVs are written and valid. Needs the BioLink model for the assembly."""
    import gzip
    import make_graphs as mg
    from kgbuilder.biolink import DEFAULT as BIOLINK_YAML
    if not BIOLINK_YAML.exists():
        pytest.skip("biolink-model.yaml not downloaded")
    monkeypatch.setitem(mg.RECIPES, "GK", {"layers": ["pharos"], "prune": True})
    edge = dict(source="s", source_record="r", primary_knowledge_source="infores:x", aggregator_knowledge_source="infores:y",
                knowledge_level="knowledge_assertion", agent_type="manual_agent", source_version="v1", n_records="1",
                layer="L0")
    d = tmp_path / "data" / "kg" / "pharos"
    d.mkdir(parents=True)
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "pharos.yaml").write_text("name: pharos\n")
    nodes = pd.DataFrame([("NCBIGene:1", "biolink:Gene", "A", "infores:pharos", "Kinase"),
                          ("NCBIGene:2", "biolink:Gene", "B", "infores:pharos", "GPCR"),
                          ("MONDO:5", "biolink:Disease", "d", "infores:pharos", ""),
                          ("MONDO:6", "biolink:Disease", "e", "infores:pharos", "")],
                         columns=["id", "category", "name", "provided_by", "idg_family"])
    edges = pd.DataFrame([{**edge, "id": "e1", "subject": "NCBIGene:1", "object": "MONDO:5",
                           "predicate": "biolink:gene_associated_with_condition"},
                          {**edge, "id": "e2", "subject": "NCBIGene:1", "object": "MONDO:6",
                           "predicate": "biolink:gene_associated_with_condition"},
                          {**edge, "id": "e3", "subject": "NCBIGene:2", "object": "MONDO:5",
                           "predicate": "biolink:gene_associated_with_condition"}])
    ver = pd.DataFrame([("e1", "keep", "0.9", "ok"), ("e2", "prune", "0.1", "generic"), ("e3", "keep", "0.8", "ok")],
                       columns=["id", "verdict", "confidence", "reasons"])
    for name, df in (("nodes", nodes), ("edges", edges), ("edge_verdicts", ver)):
        with gzip.open(d / f"{name}.tsv.gz", "wt") as f:
            df.to_csv(f, sep="\t", index=False)
    man, problems = ns.build("GK", "Kinase", root=tmp_path)
    assert problems == [] and man["counts"] == {"nodes": 2, "edges": 1}
    assert man["selection"]["edges_pruned_excluded"] == 1 and man["selection"]["seeds"] == 1
    out = tmp_path / "outputs" / "neptune_kinase"
    assert (out / "opencypher" / "nodes.csv").exists() and (out / "manifest.json").exists()
    assert "e1" in (out / "opencypher" / "edges.csv").read_text() and "e2" not in (out / "opencypher" / "edges.csv").read_text()
