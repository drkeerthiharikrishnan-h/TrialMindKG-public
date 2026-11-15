"""CC-17: pipelines/neptune_export.py writes the WHOLE assembled graph as Neptune openCypher CSVs by streaming. Fixtures only (the
pharos fixture, an Open Targets layer, a trial layer under outputs/kgx and a tiny ChEMBL names file): streaming == in-memory,
pruned edges out, per-field merge, ChEMBL names, trial id mapping, file split at the row cap, validator, manifest counts,
parity digests, and the load / create scripts in dry-run."""
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, "pipelines")
import neptune_export as nx  # noqa: E402
import neptune_subgraph as ns  # noqa: E402
import make_graphs as mg  # noqa: E402
import querier  # noqa: E402

from fixtures.pharos_fixture import build, edge, write, GENE, PROT, DIS1, DIS2, CMP, BP  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
IMA, HP = "CHEMBL.COMPOUND:CHEMBL941", "HP:0001250"
SYN = ("# source: ChEMBL chembl_37 (fixture)\nchembl_id\tparent_chembl_id\tpref_name\tmax_phase\tmolecule_type\tsynonyms\n"
       "CHEMBL941\tCHEMBL941\tIMATINIB\t4.0\tSmall molecule\tGleevec::TRADE_NAME\n")


def _tsv(path, df):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, sep="\t", index=False)


@pytest.fixture
def repo(tmp_path):
    root = build(tmp_path / "repo")
    kg = root / "data" / "kg"
    # pharos layer: an unnamed phenotype, an unnamed drug, a protein-disease edge (so a target has a disease)
    nodes = pd.read_csv(kg / "pharos" / "nodes.tsv.gz", sep="\t", dtype=str, keep_default_na=False)
    extra = pd.DataFrame([dict(id=HP, category="biolink:PhenotypicFeature", name=""),
                          dict(id=IMA, category="biolink:SmallMolecule", name="")])
    write(kg / "pharos", "nodes", pd.concat([nodes, extra], ignore_index=True).fillna(""))
    edges = pd.read_csv(kg / "pharos" / "edges.tsv.gz", sep="\t", dtype=str, keep_default_na=False)
    new = pd.DataFrame([edge(20, PROT, "biolink:genetically_associated_with", DIS1, "TIGA (GWAS Catalog)", "pharos", "pharos319"),
                        edge(21, IMA, "biolink:directly_physically_interacts_with", PROT, "CHEMBL", "pharos", "pharos319"),
                        edge(22, GENE, "biolink:gene_associated_with_condition", "MONDO:9999999", "CTD", "pharos", "pharos319")])
    write(kg / "pharos", "edges", pd.concat([edges, new], ignore_index=True).fillna(""))   # e22: its object is not a node
    # opentargets layer: names the phenotype, repeats an edge id of pharos (a duplicate), has no verdict file
    ot_nodes = pd.DataFrame([dict(id=HP, category="biolink:PhenotypicFeature", name="Seizure")])
    write(kg / "opentargets", "nodes", ot_nodes)
    ote = pd.read_csv(kg / "opentargets" / "edges.tsv.gz", sep="\t", dtype=str, keep_default_na=False)
    dup = pd.DataFrame([edge(1, GENE, "biolink:gene_associated_with_condition", DIS1, "DisGeNET", "opentargets_layer", "26.09")])
    dup["id"] = f"e{1:016x}"                                                                  # the id of pharos edge 1
    write(kg / "opentargets", "edges", pd.concat([ote, dup], ignore_index=True).fillna(""))
    # ChEMBL names
    p = root / "data" / "reference" / "chembl_synonyms.tsv.gz"
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wt") as f:
        f.write(SYN)
    # trial layer (outputs/kgx): a program, an arm, the drug CHEMBL:CHEMBL521 (= CMP once renamed) and one only the trial knows
    tn = pd.DataFrame([dict(id="PROGRAM:P1", category="biolink:ClinicalTrial", tmkg_category="tmkg:ClinicalTrialProgram", name="Prog 1"),
                       dict(id="ARM:NCT1:A1", category="biolink:StudyResult", tmkg_category="tmkg:StudyArm", name="Arm"),
                       dict(id="CHEMBL:CHEMBL521", category="biolink:SmallMolecule", tmkg_category="", name="ibuprofen"),
                       dict(id="CHEMBL:CHEMBL999", category="biolink:SmallMolecule", tmkg_category="", name="only in trial")]).fillna("")
    _tsv(root / "outputs" / "kgx" / "nodes.tsv", tn)
    te = pd.DataFrame([dict(id="t1", subject="PROGRAM:P1", predicate="biolink:related_to", object="ARM:NCT1:A1",
                            original_predicate="tmkg:has_arm", primary_knowledge_source="infores:clinicaltrials-gov",
                            knowledge_level="knowledge_assertion", agent_type="manual_agent"),
                       dict(id="t2", subject="ARM:NCT1:A1", predicate="biolink:related_to", object="CHEMBL:CHEMBL521",
                            original_predicate="tmkg:arm_receives", primary_knowledge_source="infores:clinicaltrials-gov",
                            knowledge_level="knowledge_assertion", agent_type="manual_agent"),
                       dict(id="t3", subject="ARM:NCT1:A1", predicate="biolink:related_to", object="CHEMBL:CHEMBL999",
                            original_predicate="tmkg:arm_receives", primary_knowledge_source="infores:clinicaltrials-gov",
                            knowledge_level="knowledge_assertion", agent_type="manual_agent")]).fillna("")
    _tsv(root / "outputs" / "kgx" / "edges.tsv", te)
    return root


def run(repo, out, **kw):
    kw.setdefault("log", lambda *_: None)
    return nx.export(repo, out, "G4", **kw)


def read_all(oc):
    """(nodes frame, edges frame) from the written files, all columns."""
    def one(prefix):
        fs = sorted(Path(oc).glob(f"{prefix}_*.csv.gz"))
        return pd.concat([pd.read_csv(f, dtype=str, keep_default_na=False) for f in fs], ignore_index=True)
    return one("nodes"), one("edges")


def test_streaming_equals_in_memory_and_chunk_size_does_not_matter(repo, tmp_path):
    small = run(repo, tmp_path / "a", chunk=3, rows_per_file=1_000_000)
    big = run(repo, tmp_path / "b", chunk=10_000_000, rows_per_file=1_000_000)
    na, ea = read_all(tmp_path / "a" / "opencypher")
    nb, eb = read_all(tmp_path / "b" / "opencypher")
    assert na.equals(nb) and ea.equals(eb) and small["counts"] == big["counts"]
    assert {f["sha256"] for f in small["files"]["edges"]} == {f["sha256"] for f in big["files"]["edges"]}
    # independent in-memory reference: export_graph_kgx assembles the same recipe with pandas (pruned edges dropped)
    import export_graph_kgx as xk
    rn, re_, rep = xk.assemble_kgx("G4", repo, include_pruned=False, allow_missing=True)
    assert set(ea[":ID"]) == set(re_["id"]) and set(na[":ID"]) == set(rn["id"])
    assert len(ea) == len(re_) == small["counts"]["edges"]


def test_pruned_duplicate_and_dangling_edges_are_left_out_and_counted(repo, tmp_path):
    man = run(repo, tmp_path / "o")
    _, e = read_all(tmp_path / "o" / "opencypher")
    d = man["dropped"]
    assert f"e{3:016x}" not in set(e[":ID"]) and d["pruned"] == 1 and d["pruned_by_layer"]["pharos"] == 1   # the text-mining edge
    assert d["duplicate_edge_ids"] == 1                                    # the opentargets copy of pharos edge 1: first one wins
    assert e[e[":ID"] == f"e{1:016x}"]["layer:String"].tolist() == ["pharos"]
    assert d["edge_end_not_a_node"] == 1 and f"e{22:016x}" not in set(e[":ID"])
    assert set(e["verdict:String"]) == {"keep", ""} and e[e["layer:String"] == "opentargets_layer"]["verdict:String"].eq("").all()   # never pruned: empty
    assert man["edges_read"] == sum(man["kept_by_layer"].values()) + d["pruned"] + d["duplicate_edge_ids"] + d["edge_end_not_a_node"]


def test_node_fields_merge_across_layers_and_chembl_names_apply(repo, tmp_path):
    run(repo, tmp_path / "o")
    n, _ = read_all(tmp_path / "o" / "opencypher")
    n = n.set_index(":ID")
    assert n.loc[HP, "name:String"] == "Seizure" and n.loc[HP, ":LABEL"] == "PhenotypicFeature"      # pharos empty, opentargets names it
    assert n.loc[IMA, "name:String"] == "IMATINIB" and n.loc[CMP, "name:String"] == "IBUPROFEN"      # ChEMBL name; a layer's name stays
    assert n.loc["NCBIGene:5743", "name:String"] == "PTGS2" and n.loc[PROT, "tdl:String"] == "Tclin"
    assert n.index.is_unique and (n[":LABEL"] != "").all()


def test_trial_layer_is_appended_and_its_drug_ids_are_mapped(repo, tmp_path):
    man = run(repo, tmp_path / "o")
    n, e = read_all(tmp_path / "o" / "opencypher")
    ids = set(n[":ID"])
    assert "CHEMBL:CHEMBL521" not in ids and "CHEMBL.COMPOUND:CHEMBL999" in ids and CMP in ids          # renamed; merged with Pharos
    assert n.set_index(":ID").loc["PROGRAM:P1", ":LABEL"] == "ClinicalTrialProgram"                  # the trial's own class
    t = e[e["layer:String"] == "trialmindkg"].set_index(":ID")
    assert t.loc["t2", ":END_ID"] == CMP and t.loc["t2", ":TYPE"] == "arm_receives" and t.loc["t2", "biolink_predicate:String"] == "biolink:related_to"
    assert man["trial"]["ids_renamed"] == 2 and man["trial"]["same_as_edges"] == 0
    # one database: a trial drug reaches a Pharos protein
    assert PROT in set(e[e[":START_ID"] == CMP][":END_ID"])


def test_same_as_edge_when_the_original_trial_id_is_also_a_node_of_another_layer(repo, tmp_path):
    kg = repo / "data" / "kg" / "pharos"
    nodes = pd.read_csv(kg / "nodes.tsv.gz", sep="\t", dtype=str, keep_default_na=False)
    write(kg, "nodes", pd.concat([nodes, pd.DataFrame([dict(id="CHEMBL:CHEMBL521", category="biolink:SmallMolecule", name="dup")])],
                                 ignore_index=True).fillna(""))
    man = run(repo, tmp_path / "o")
    _, e = read_all(tmp_path / "o" / "opencypher")
    s = e[e[":TYPE"] == "same_as"]
    assert man["trial"]["same_as_edges"] == 1 and s[":START_ID"].tolist() == ["CHEMBL:CHEMBL521"] and s[":END_ID"].tolist() == [CMP]


def test_files_split_at_the_row_cap_and_every_row_is_kept(repo, tmp_path):
    man = run(repo, tmp_path / "o", chunk=3, rows_per_file=4)
    oc = tmp_path / "o" / "opencypher"
    for kind in ("nodes", "edges"):
        files = sorted(oc.glob(f"{kind}_*.csv.gz"))
        assert [f.name for f in files] == [f"{kind}_{i:03d}.csv.gz" for i in range(len(files))] and len(files) >= 3
        rows = [len(list(nx._rows(f))) - 1 for f in files]
        assert max(rows) <= 4 and all(r == 4 for r in rows[:-1]) and sum(rows) == man["counts"][kind]
        assert [f["rows"] for f in man["files"][kind]] == rows
    assert nx.validate_export(oc, max_rows=4) == []


def test_manifest_counts_hashes_and_totals_are_the_files(repo, tmp_path):
    man = run(repo, tmp_path / "o", rows_per_file=5)
    on_disk = json.loads((tmp_path / "o" / "manifest.json").read_text())
    assert on_disk == man and not list((tmp_path / "o" / "opencypher").glob("*.json"))      # the loader would choke on a .json
    n, e = read_all(tmp_path / "o" / "opencypher")
    assert man["counts"] == {"nodes": len(n), "edges": len(e)}
    assert sum(man["totals"]["labels"].values()) == len(n) and sum(man["totals"]["types"].values()) == len(e)
    assert man["totals"]["labels"] == n[":LABEL"].value_counts().to_dict() and man["totals"]["types"] == e[":TYPE"].value_counts().sort_index().to_dict()
    for kind in ("nodes", "edges"):
        for f in man["files"][kind]:
            assert f["sha256"] == hashlib.sha256((tmp_path / "o" / "opencypher" / f["file"]).read_bytes()).hexdigest()
    assert man["layers"]["used"] == ["pharos", "opentargets_layer", "trialmindkg"] and "chembl37" in man["layers"]["missing"]
    assert man["named_from_chembl"] >= 1 and all(i["bytes"] > 0 for i in man["inputs"])
    assert any(i["path"].endswith("chembl_synonyms.tsv.gz") for i in man["inputs"])


def test_the_export_is_byte_deterministic_and_a_rerun_replaces_old_files(repo, tmp_path):
    a = run(repo, tmp_path / "o", rows_per_file=3)
    b = run(repo, tmp_path / "o", rows_per_file=100)                    # fewer files: the old numbered ones must be gone
    assert len(list((tmp_path / "o" / "opencypher").glob("edges_*.csv.gz"))) == len(b["files"]["edges"]) < len(a["files"]["edges"])
    c = run(repo, tmp_path / "c", rows_per_file=100)
    assert [f["sha256"] for f in b["files"]["edges"]] == [f["sha256"] for f in c["files"]["edges"]]
    assert [f["sha256"] for f in b["files"]["nodes"]] == [f["sha256"] for f in c["files"]["nodes"]]
    raw = (tmp_path / "o" / "opencypher" / b["files"]["nodes"][0]["file"]).read_bytes()
    assert raw[4:8] == b"\0\0\0\0"                                      # gzip mtime field is zero


def test_the_validator_passes_and_catches_each_problem(repo, tmp_path):
    run(repo, tmp_path / "o")
    oc = tmp_path / "o" / "opencypher"
    assert nx.validate_export(oc) == []
    assert nx.validate_export(tmp_path / "empty") and "no nodes_" in nx.validate_export(tmp_path / "empty")[0]

    def rewrite(name, fn):
        f = oc / name
        text = gzip.decompress(f.read_bytes()).decode().splitlines()
        f.write_bytes(gzip.compress(("\n".join(fn(text)) + "\n").encode()))

    good = {p.name: p.read_bytes() for p in oc.glob("*.csv.gz")}

    def reset():
        for k, v in good.items():
            (oc / k).write_bytes(v)

    rewrite("edges_000.csv.gz", lambda t: [t[0]] + [t[1].replace(t[1].split(",")[2], "NOPE:1", 1)] + t[2:])
    assert any("is not a node" in p for p in nx.validate_export(oc)); reset()
    rewrite("edges_000.csv.gz", lambda t: t + [t[1]])
    assert any("duplicate :ID" in p for p in nx.validate_export(oc)); reset()
    rewrite("nodes_000.csv.gz", lambda t: t + [t[1]])
    assert any("duplicate :ID" in p for p in nx.validate_export(oc)); reset()
    rewrite("nodes_000.csv.gz", lambda t: [t[0].replace(":LABEL", "label")] + t[1:])
    assert any("not the node header" in p for p in nx.validate_export(oc)); reset()
    rewrite("edges_000.csv.gz", lambda t: [t[0]] + [t[1] + ",extra"] + t[2:])
    assert any("cells, header has" in p for p in nx.validate_export(oc)); reset()
    assert nx.validate_export(oc, max_rows=2) and "over 2" in " ".join(nx.validate_export(oc, max_rows=2))


def test_parity_questions_are_chosen_by_rule_not_for_kinases_and_digests_are_reproducible(repo, tmp_path):
    a = run(repo, tmp_path / "a", chunk=3)
    b = run(repo, tmp_path / "b", chunk=10_000_000, rows_per_file=5)
    assert a["parity"] == b["parity"] and len(a["parity"]) >= 3                     # same on different chunk / file sizes
    assert all("idg_family" not in q["cypher"] and "Kinase" not in q["cypher"] for q in a["parity"].values())
    kinds = " ".join(q["question"] for q in a["parity"].values())
    assert "trial drug" in kinds and "Disease nodes" in kinds and "Two hops" in kinds
    assert all(CMP in q["anchors"] or PROT in q["anchors"] for q in a["parity"].values())
    # the digests are what the WRITTEN files answer: load every file back (the fixture is tiny) and run the same openCypher
    n, e = read_all(tmp_path / "b" / "opencypher")
    idx = ns._Index({r[":ID"]: dict(id=r[":ID"], category=f"neptune:{r[':LABEL']}") for _, r in n.iterrows()},
                    {r[":ID"]: dict(id=r[":ID"], subject=r[":START_ID"], object=r[":END_ID"], predicate=f"neptune:{r[':TYPE']}")
                     for _, r in e.iterrows()})
    for qid, q in b["parity"].items():
        res = querier.run_local(q["cypher"], idx)
        assert res["evidence"], qid
        assert ns.evidence_digest(res) == {k: q[k] for k in ("n_rows", "n_evidence", "evidence_sha256")}, qid
    assert not any(f"e{3:016x}" in json.dumps(q) for q in b["parity"].values())      # a pruned edge is never in an answer


def test_without_parity_the_manifest_has_none_and_one_pass_is_made(repo, tmp_path):
    man = run(repo, tmp_path / "o", parity=False)
    assert man["parity"] == {}


def test_edges_are_never_held_all_at_once(repo):
    info = {}
    nodes, ninfo = nx.merged_nodes(repo, mg.RECIPES["G4"]["layers"])
    sizes = [len(c) for c in nx.kept_edge_chunks(repo, mg.RECIPES["G4"]["layers"], set(nodes["id"]), chunk=3, info=info)]
    assert max(sizes) <= 3 and len(sizes) >= 4 and sum(sizes) == info["kept"]


def test_graph_recipe_and_missing_layers_are_reported(repo, tmp_path):
    man = nx.export(repo, tmp_path / "o", "G3", log=lambda *_: None)                  # G3 = no trial layer
    n, _ = read_all(tmp_path / "o" / "opencypher")
    assert man["layers"]["used"] == ["pharos", "opentargets_layer"] and "PROGRAM:P1" not in set(n[":ID"])
    assert man["trial"]["ids_renamed"] == 0


# ---- aws scripts ----
def _cfg(tmp_path):
    cfg = tmp_path / "stub.env"
    cfg.write_text("TMKG_BUCKET=b\nNEPTUNE_ENDPOINT=n.example\nNEPTUNE_LOAD_ROLE_ARN=arn:aws:iam::1:role/r\n")
    return cfg


def _sh(script, args, tmp_path, env=None):
    e = {**os.environ, "TMKG_AWS_CONFIG": str(_cfg(tmp_path)), **(env or {})}
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    marker = tmp_path / "called"
    for name in ("aws", "curl", "awscurl"):
        f = bindir / name
        f.write_text(f'#!/bin/sh\necho "{name} $*" >> "{marker}"\nexit 99\n')
        f.chmod(0o755)
    e["PATH"] = f"{bindir}{os.pathsep}{e['PATH']}"
    r = subprocess.run([BASH, str(ROOT / "aws" / f"{script}.sh"), *args], capture_output=True, text=True, env=e, cwd=ROOT)
    return r, marker


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_load_script_full_dry_run_reads_the_prefix_with_many_files_at_high_parallelism(tmp_path):
    """PK14 A3 (14 Nov): the --full default is HIGH (AWS documents a deadlock risk for openCypher loads at HIGH and
    OVERSUBSCRIBE; a deadlocked job is resubmitted one step lower), OVERSUBSCRIBE only when asked for."""
    r, marker = _sh("neptune_load", ["--dry-run", "--full"], tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 0 and not marker.exists(), out
    assert "outputs/neptune/full/opencypher/" in out and '"parallelism":"HIGH"' in out
    assert "outputs/neptune/full/manifest.json" in out and "nodes_*.csv.gz" in out
    plain, _ = _sh("neptune_load", ["--dry-run"], tmp_path)
    assert '"parallelism":"MEDIUM"' in plain.stdout and "outputs/neptune/opencypher/" in plain.stdout
    custom, _ = _sh("neptune_load", ["--dry-run", "--full"], tmp_path, env={"NEPTUNE_PARALLELISM": "OVERSUBSCRIBE"})
    assert '"parallelism":"OVERSUBSCRIBE"' in custom.stdout


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_create_script_takes_io_optimized_storage_and_ncu_limits(tmp_path):
    base, m0 = _sh("neptune_create", ["--dry-run"], tmp_path)
    assert base.returncode == 0 and "--storage-type" not in base.stdout and "MinCapacity=1,MaxCapacity=4" in base.stdout
    big, m1 = _sh("neptune_create", ["--dry-run", "--io-optimized"], tmp_path, env={"MIN_NCU": "2", "MAX_NCU": "64"})
    assert big.returncode == 0 and "--storage-type iopt1" in big.stdout and "MinCapacity=2,MaxCapacity=64" in big.stdout
    scale, _ = _sh("neptune_create", ["--dry-run", "--scale"], tmp_path, env={"MIN_NCU": "1", "MAX_NCU": "8"})
    assert scale.returncode == 0 and "modify-db-cluster" in scale.stdout and "MaxCapacity=8" in scale.stdout
    assert "create-db-cluster" not in scale.stdout and "create-security-group" not in scale.stdout


def test_readme_is_written_from_the_manifest_with_the_plain_note(repo, tmp_path):
    man = run(repo, tmp_path / "o", rows_per_file=5)
    text = (tmp_path / "o" / "README.md").read_text()
    assert text == nx.readme_markdown(man) and not (tmp_path / "o" / "opencypher" / "README.md").exists()   # not in the loader's folder
    assert nx.NOT_HOSTED_REASON in text and "## Not loaded anywhere" in text and "deliverable" in text
    assert f"{man['counts']['nodes']:,} nodes and {man['counts']['edges']:,} edges" in text
    for kind in ("nodes", "edges"):
        for f in man["files"][kind]:
            assert f"| {f['file']} | {f['rows']:,} |" in text and f["sha256"][:16] in text
    for qid, q in man["parity"].items():
        assert q["cypher"] in text and q["evidence_sha256"] in text and f"**{qid}**" in text
    d = man["dropped"]
    assert f"{d['pruned']:,} edges with verdict `prune`" in text and "format` set to `opencypher`" in text
    assert "MATCH (n) RETURN count(n)" in text and "manifest.json" in text


def test_readme_is_deterministic_and_has_no_date(repo, tmp_path):
    a = run(repo, tmp_path / "a")
    run(repo, tmp_path / "b")
    assert (tmp_path / "a" / "README.md").read_bytes() == (tmp_path / "b" / "README.md").read_bytes()
    assert not re.search(r"20\d\d-\d\d-\d\d", (tmp_path / "a" / "README.md").read_text())
    nodate = nx.readme_markdown({**a, "parity": {}})
    assert "## Questions to check a load" not in nodate


def test_readme_says_nothing_the_manifest_does_not(repo, tmp_path):
    man = run(repo, tmp_path / "o")
    text = nx.readme_markdown({**man, "layers": {"used": ["pharos"], "missing": ["x", "y"]}})
    assert "Layers used: pharos; not built: x, y." in text
    assert "not built: none" in nx.readme_markdown({**man, "layers": {"used": ["pharos"], "missing": []}})
