"""Tests for scripts/audit_spotcheck.py on small fixtures (no network, no model, no data/)."""
import gzip
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import audit_spotcheck as a  # noqa: E402
import edge_evidence as ee  # noqa: E402


class Fake:
    """Fetcher stand-in for gene-to-protein rows: UniProt P<n> cross-references NCBI Gene <n> for even n."""

    def json(self, source, url, params=None, headers=None):
        n = int(url.rsplit("/P", 1)[-1].split(".")[0])
        return {"entryType": "UniProtKB reviewed (Swiss-Prot)",
                "uniProtKBCrossReferences": [{"database": "GeneID", "id": str(n if n % 2 == 0 else 999)}]}

    text = post = json


def _rows(n=6):
    return [{"row": i, "subject_name": f"G{i}", "subject_id": f"NCBIGene:{i}", "predicate": "has_gene_product",
             "object_name": f"P{i}", "object_id": f"UniProtKB:P{i}", "source": "Pharos target-gene mapping (infores:pharos)",
             "knowledge_level": "knowledge_assertion", "source_statistic": "", "references": ""} for i in range(1, n + 1)]


def _sheet(tmp_path, n=6, extra=None):
    df = pd.DataFrame(_rows(n)).assign(owner_judgement="", notes="", **(extra or {}))
    p = tmp_path / "sheet.xlsx"
    with pd.ExcelWriter(p, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="spot_check")
    return p


@pytest.fixture
def lookups(tmp_path):
    (tmp_path / "data" / "reference").mkdir(parents=True)
    return ee.Lookups(tmp_path)


@pytest.fixture
def work(tmp_path, lookups):
    w = tmp_path / "work"
    a.collect_rows(_rows(), w, Fake(), lookups, workers=1, today="2026-11-12")
    return w


def _labels(tmp_path, rows):
    """rows: {row: (label_A, label_B, label_C)} -> labels file path."""
    recs = [{"row": r, "rater": k, "model": f"m{k}", "label": l, "label_raw": l, "items": "UP1" if l == "supported" else "",
             "problems": "", "rationale": f"because {k} read the dossier of row {r}", "reply_key": "k"}
            for r, trio in rows.items() for k, l in zip(a.RATERS, trio)]
    p = tmp_path / "labels.tsv"
    pd.DataFrame(recs)[a.LABEL_COLS].to_csv(p, sep="\t", index=False)
    return p


def test_read_rows_reads_blind_columns_only(tmp_path):
    rows = a.read_rows(_sheet(tmp_path, extra={"verdict": "prune", "confidence": "0.1"}))
    assert len(rows) == 6 and set(rows[0]) == {"row"} | set(a.BLIND_FIELDS) and rows[0]["row"] == 1
    assert rows[0]["references"] == ""                                               # empty cells come back as ''


def test_owner_rows_are_seeded_and_need_no_key():
    r = a.owner_rows()
    assert len(r) == 40 == len(set(r)) and r == sorted(r) and 1 <= r[0] and r[-1] <= 200
    assert r == a.owner_rows(200, 40, a.OWNER_SEED) and r != a.owner_rows(seed=1)
    assert a.owner_rows(5, 40) == [1, 2, 3, 4, 5]


def test_collect_rows_stores_one_dossier_per_row_and_resumes(tmp_path, lookups):
    w = tmp_path / "w"
    assert a.collect_rows(_rows(), w, Fake(), lookups, workers=2, today="2026-11-12", edge_ids={1: "e1"}) == {
        "built": 6, "kept": 0, "failed_sources": 0}
    assert a.dossier_path(w, 3).name == "row_0003.json" and len(list((w / "dossiers").glob("*.json"))) == 6
    assert a.collect_rows(_rows(), w, Fake(), lookups) == {"built": 0, "kept": 6, "failed_sources": 0}
    d = a.load_dossiers(w)
    assert sorted(d) == [1, 2, 3, 4, 5, 6] and d[1]["edge_id"] == "e1" and d[2]["group"] == "gene_product"
    assert "verdict" not in json.dumps(d)
    broken = json.loads(a.dossier_path(w, 2).read_text())
    broken["sources"][0]["status"] = "failed"
    a.dossier_path(w, 2).write_text(json.dumps(broken))
    assert a.needs_collection(a.dossier_path(w, 2)) and not a.needs_collection(a.dossier_path(w, 2), retry_failed=False)
    assert a.needs_collection(a.dossier_path(w, 99)) and not a.needs_collection(a.dossier_path(w, 1))
    assert a.collect_rows(_rows(), w, Fake(), lookups, workers=1)["built"] == 1          # only the failed one again
    assert a.collect_rows(_rows(), w, Fake(), lookups, workers=1, rebuild=True)["built"] == 6


def test_export_and_summary(tmp_path, work):
    d = a.load_dossiers(work)
    p1, p2 = tmp_path / "a.jsonl.gz", tmp_path / "b.jsonl.gz"
    assert a.export_dossiers(d, p1) == 6 and a.export_dossiers(d, p2) == 6
    assert p1.read_bytes() == p2.read_bytes()                                         # byte-identical for identical content
    lines = gzip.decompress(p1.read_bytes()).decode().splitlines()
    assert [json.loads(l)["row"] for l in lines] == [1, 2, 3, 4, 5, 6]
    s = a.dossier_summary(d)
    assert s["groups"] == {"gene_product": 6} and s["status"] == {("UniProt", "answered"): 6}


def test_rater_models_takes_the_first_candidate_that_answers():
    nim = type("N", (), {"cfg": {"spotcheck_raters": {"judge_a": "nv/a", "judge_b": "nv/b", "rater_c_candidates": ["x/1", "y/2", "z/3"]}}})()
    assert a.rater_models(nim, probe=lambda m: m != "x/1") == {"A": "nv/a", "B": "nv/b", "C": "y/2"}
    with pytest.raises(RuntimeError):
        a.rater_models(nim, probe=lambda m: False)


def test_default_probe_waits_as_long_as_the_real_rater_calls(monkeypatch):
    """The probe's own Snapshot must use post_timeout=RATER_POST_TIMEOUT (600 s), not the Snapshot default of 180 s."""
    import snapshot
    seen = []
    real = snapshot.Snapshot

    def spy(*args, **kw):
        seen.append(kw.get("post_timeout", "default"))
        return real(*args, **kw)

    monkeypatch.setattr(snapshot, "Snapshot", spy)
    nim = type("N", (), {"cfg": {"spotcheck_raters": {"judge_a": "nv/a", "judge_b": "nv/b", "rater_c_candidates": ["x/1"]},
                                 "roles": {}}})()          # chat() is missing: the probe answers False after building its Snapshot
    with pytest.raises(RuntimeError):
        a.rater_models(nim)
    assert a.RATER_POST_TIMEOUT == 600 and seen == [600]


def test_config_names_three_raters_from_two_families():
    import yaml
    cfg = yaml.safe_load((a.ROOT / "config" / "nim_models.yaml").read_text())["spotcheck_raters"]
    fam = lambda m: m.split("/")[0]
    assert fam(cfg["judge_a"]) == fam(cfg["judge_b"]) == "nvidia" and cfg["judge_a"] != cfg["judge_b"]
    assert cfg["rater_c_candidates"] and all(fam(m) != "nvidia" for m in cfg["rater_c_candidates"])


def test_judge_rows_appends_resumes_and_leaves_failures(tmp_path, work):
    d = a.load_dossiers(work)
    path = tmp_path / "labels.tsv"
    calls = []

    def fake_judge(dossier, nim, model):
        calls.append((dossier["row"], model))
        if model == "mC" and dossier["row"] == 3:
            raise RuntimeError("503")
        return {"label": "supported", "label_raw": "supported", "rationale": "ok\tfine", "items": ["UP1"], "problems": [], "model": model, "key": "k"}

    models = {"A": "mA", "B": "mB", "C": "mC"}
    r = a.judge_rows(d, None, models, path, workers=1, judge_fn=fake_judge)
    assert r["done"] == 17 and r["kept"] == 0 and [(e["row"], e["rater"]) for e in r["errors"]] == [(3, "C")]
    lab = a.read_labels(path)
    assert list(lab.columns) == a.LABEL_COLS and len(lab) == 17 and lab["rationale"].iloc[0] == "ok\tfine"
    calls.clear()
    r2 = a.judge_rows(d, None, models, path, workers=2, judge_fn=lambda *x: fake_judge(x[0], None, "ok") | {"model": x[2]})
    assert r2["done"] == 1 and r2["kept"] == 17 and len(a.read_labels(path)) == 18       # only the missing one
    assert a.judge_rows(d, None, models, path, judge_fn=fake_judge)["done"] == 0 and len(a.read_labels(tmp_path / "none.tsv")) == 0


def test_judge_rows_asks_again_when_a_reply_was_unusable_and_saves_in_chunks(tmp_path, work):
    d = a.load_dossiers(work)
    path = tmp_path / "labels.tsv"
    state = {"first": True, "writes": 0}

    def fake_judge(dossier, nim, model):
        if model == "mC" and state["first"]:
            return {"label": "cannot tell", "label_raw": "", "rationale": "reply not usable", "items": [], "problems": ["no valid label in the reply"], "model": model}
        return {"label": "unsupported", "label_raw": "unsupported", "rationale": "nothing", "items": [], "problems": [], "model": model}

    models = {"A": "mA", "B": "mB", "C": "mC"}
    assert a.judge_rows(d, None, models, path, workers=1, judge_fn=fake_judge, chunk=5)["done"] == 18      # written in four chunks
    assert (a.read_labels(path)["label_raw"] == "").sum() == 6
    state["first"] = False
    r = a.judge_rows(d, None, models, path, workers=1, judge_fn=fake_judge)
    assert r == {"done": 6, "kept": 12, "errors": []}                                   # only the six unusable ones again
    lab = a.read_labels(path)
    assert len(lab) == 18 and (lab["label_raw"] == "").sum() == 0 and set(lab[lab["rater"] == "C"]["label"]) == {"unsupported"}
    assert a.judge_rows(d, None, models, path, workers=1, judge_fn=fake_judge, retry_unusable=False)["done"] == 0


def test_merge_labels_keeps_what_another_process_wrote(tmp_path, work):
    path = tmp_path / "labels.tsv"
    row = lambda r, k, l: {"row": r, "rater": k, "model": "m" + k, "label": l, "label_raw": l, "items": "", "problems": "", "rationale": "x", "reply_key": ""}
    a.merge_labels(path, [row(1, "A", "supported"), row(2, "A", "unsupported")])
    a.merge_labels(path, [row(1, "B", "supported")])                     # as if written by the process of another rater
    out = a.merge_labels(path, [row(1, "A", "cannot tell"), row(3, "A", "supported")])   # replaces (1, A), keeps the rest
    assert [(r, k, l) for r, k, l in zip(out["row"], out["rater"], out["label"])] == [
        (1, "A", "cannot tell"), (1, "B", "supported"), (2, "A", "unsupported"), (3, "A", "supported")]
    assert len(a.read_labels(path)) == 4 and not path.with_suffix(".tsv.tmp").exists()
    d = a.load_dossiers(work)
    fake = lambda dossier, nim, model: {"label": "unsupported", "label_raw": "unsupported", "rationale": "n", "items": [], "problems": [], "model": model}
    a.judge_rows(d, None, {"A": "mA"}, tmp_path / "l2.tsv", workers=1, judge_fn=fake)
    a.judge_rows(d, None, {"B": "mB"}, tmp_path / "l2.tsv", workers=1, judge_fn=fake)   # a second rater's run adds, never overwrites
    assert sorted(set(a.read_labels(tmp_path / "l2.tsv")["rater"])) == ["A", "B"] and len(a.read_labels(tmp_path / "l2.tsv")) == 12


def test_apply_rater_options_adds_to_model_options():
    nim = type("N", (), {"cfg": {"spotcheck_raters": {"options": {"fam/c": {"max_tokens": 8192}}},
                                  "model_options": {"fam/c": {"x": 1}, "nv/a": {"chat_template_kwargs": {"enable_thinking": False}}}}})()
    assert a.apply_rater_options(nim) == {"fam/c": {"max_tokens": 8192}}
    assert nim.cfg["model_options"] == {"fam/c": {"x": 1, "max_tokens": 8192}, "nv/a": {"chat_template_kwargs": {"enable_thinking": False}}}
    bare = type("N", (), {"cfg": {}})()
    assert a.apply_rater_options(bare) == {} and bare.cfg["model_options"] == {}


def test_judge_rows_keeps_a_deterministic_label(tmp_path, work):
    d = {1: a.load_dossiers(work)[1]}
    path = tmp_path / "labels.tsv"
    models = {"A": "mA", "B": "mB", "C": "mC"}
    assert a.judge_rows(d, None, models, path, workers=1)["done"] == 3                    # the real judge: no model call for this group
    lab = a.read_labels(path)
    assert set(lab["model"]) == {"deterministic (UniProt cross-reference)"} and set(lab["label"]) == {"unsupported"}
    assert a.judge_rows(d, None, models, path, workers=1)["done"] == 0                    # not asked again


def test_proposals(tmp_path):
    lab = a.read_labels(_labels(tmp_path, {1: ("supported",) * 3, 2: ("supported", "supported", "cannot tell"),
                                           3: ("supported", "unsupported", "cannot tell")}))
    p = a.proposals(lab).set_index("row")
    assert p.loc[1, "proposal"] == "supported" and not p.loc[1, "flagged"]
    assert p.loc[2, "proposal"] == "" and p.loc[2, "flagged"] and p.loc[2, "majority"] == "supported"
    assert p.loc[3, "flagged"] and p.loc[3, "majority"] == ""
    assert len(a.proposals(lab[lab["rater"] != "C"])) == 0                               # incomplete rows are left out


def test_owner_sheet_has_dossiers_and_no_proposals(tmp_path, work):
    d = a.load_dossiers(work)
    p = a.write_owner_sheet(d, [2, 5], tmp_path / "owner.xlsx")
    df = pd.read_excel(p, sheet_name="spot_check", dtype=str)
    assert list(df["row"]) == ["2", "5"] and df["owner_judgement"].isna().all()
    assert df.loc[0, "dossier"].startswith("CLAIM") and "[UP1]" in df.loc[0, "dossier"] and "uniprot.org" in df.loc[0, "links"]
    banned = ("proposal", "label", "rater", "majority", "verdict", "confidence", "reasons", "edge_id")
    assert not [c for c in df.columns if any(b in c.lower() for b in banned)]
    rules = pd.read_excel(p, sheet_name="rules", dtype=str)
    assert "Protein-protein rows" in set(rules["kind of row"]) and "Gene-to-protein rows" in set(rules["kind of row"])
    readme = dict(pd.read_excel(p, sheet_name="README", dtype=str).values)
    assert "20261112" in readme["rows"] and "No machine proposal" in readme["what is not here"]


def test_adjudication_sheet_lists_flagged_rows_outside_the_owner_rows(tmp_path, work):
    d = a.load_dossiers(work)
    lab = a.read_labels(_labels(tmp_path, {1: ("supported",) * 3, 2: ("supported", "supported", "cannot tell"),
                                           3: ("supported", "unsupported", "cannot tell"), 4: ("unsupported", "unsupported", "supported")}))
    p = a.write_adjudication_sheet(d, lab, [4], tmp_path / "adj.xlsx")
    df = pd.read_excel(p, sheet_name="spot_check", dtype=str)
    assert list(df["row"]) == ["2", "3"] and list(df["majority"]) == ["supported", "(none)"]
    assert df.loc[0, "rater_C"].startswith("cannot tell: because C read") and df["owner_judgement"].isna().all()
    assert "verdict" not in " ".join(df.columns)


def test_publish_writes_one_record_per_edge_id(tmp_path, work):
    key = tmp_path / "key.tsv"
    pd.DataFrame({"row": [1, 2, 3], "verdict": ["prune", "keep", "keep"], "edge_id": ["ea", "eb", "ec"]}).to_csv(key, sep="\t", index=False)
    ids = a.edge_ids_from_key(key)
    assert ids == {1: "ea", 2: "eb", 3: "ec"}
    lab = a.read_labels(_labels(tmp_path, {1: ("supported",) * 3, 2: ("supported", "supported", "cannot tell")}))
    out = tmp_path / "edges"
    assert a.publish(a.load_dossiers(work), lab, ids, out) == 2                           # row 3 has no raters, rows 4-6 no edge id
    rec = json.loads((out / "eb.json").read_text())
    assert rec["edge_id"] == "eb" and rec["judgement"]["label"] == "supported" and rec["judgement"]["raters_agreeing"] == 2
    assert "verdict" not in json.dumps(rec) and "keep" not in json.dumps(rec)
    assert json.loads((out / "index.json").read_text()) == {"ea": "supported", "eb": "supported"}
    unanswered = lab.copy()
    unanswered.loc[(unanswered["row"] == 2) & (unanswered["rater"] == "C"), "label_raw"] = ""
    assert a.publish(a.load_dossiers(work), unanswered, ids, tmp_path / "un") == 1          # row 2 now has two real ratings only
    two = lab[lab["rater"] != "C"]
    assert a.publish(a.load_dossiers(work), two, ids, tmp_path / "two") == 0 and a.publish(a.load_dossiers(work), two, ids, tmp_path / "two", min_raters=2) == 2
    assert json.loads((tmp_path / "two" / "ea.json").read_text())["judgement"]["raters"] == 2
    (out / "index.json").write_text(json.dumps({"old": "unsupported"}))
    a.publish(a.load_dossiers(work), lab, ids, out)
    assert json.loads((out / "index.json").read_text())["old"] == "unsupported"           # earlier sets stay in the index


def test_cited_edges_become_blind_rows(tmp_path):
    ex = tmp_path / "examples"
    ex.mkdir()
    (ex / "x01.json").write_text(json.dumps({"id": "x01", "answer": {
        "summary": {"refs": {"edges": {"e1": {"id": "e1", "confidence": 0.2}}}},
        "sections": [{"graph": {"edges": [{"id": "e2"}, {"id": "e1"}]}, "claims": [{"evidence": [{"id": "e3"}, "e4"]}]}]}}))
    overlay = tmp_path / "overlay.json"
    overlay.write_text(json.dumps({"results": {"pharos_demo_1": {"result": {"evidence": ["e1", {"id": "e5"}]}}}}))
    cited = a.cited_edge_ids(ex, overlay)
    assert sorted(cited) == ["e1", "e2", "e3", "e4", "e5"]
    assert cited["e1"] == ["stored answer x01", "tested question pharos_demo_1"] and cited["e5"] == ["tested question pharos_demo_1"]
    assert sorted(a.cited_edge_ids(ex)) == ["e1", "e2", "e3", "e4"]

    def explain(eid):
        if eid == "e2":
            return None                                                  # a registry link: not in the index
        if eid == "e3":
            raise KeyError(eid)
        return {"id": eid, "verdict": {"verdict": "prune", "confidence": 0.11, "reasons": ["low_plausibility"]},
                "subject": {"id": "NCBIGene:5336", "name": "phospholipase", "symbol": "PLCG2"}, "object": {"id": "MONDO:0004975", "name": "Alzheimer disease"},
                "provenance": {"subject": "NCBIGene:5336", "predicate": "biolink:gene_associated_with_condition", "object": "MONDO:0004975",
                               "source": "DisGeNET", "primary_knowledge_source": "infores:disgenet", "knowledge_level": "not_provided", "n_records": "2"},
                "properties": {"disgenet_score": "0.3", "pmids": "111|222"}}

    row = a.blind_row(explain("e1"))
    assert row == {"subject_name": "PLCG2", "subject_id": "NCBIGene:5336", "predicate": "gene_associated_with_condition",
                   "object_name": "Alzheimer disease", "object_id": "MONDO:0004975", "source": "DisGeNET (infores:disgenet)",
                   "knowledge_level": "not_provided", "source_statistic": "disgenet_score=0.3; n_records=2", "references": "PMID:111 | PMID:222"}
    assert a.blind_row(None) is None and a.blind_row({"id": "x"}) is None
    sheet, key = tmp_path / "cited.xlsx", tmp_path / "cited_ids.tsv"
    assert a.write_cited_set(cited, explain, sheet, key) == {"cited": 5, "in_index": 3, "not_in_index": 2}
    rows = a.read_rows(sheet)
    assert [r["row"] for r in rows] == [1, 2, 3] and rows[0]["subject_name"] == "PLCG2"
    blob = sheet.read_bytes() + key.read_bytes()
    assert b"prune" not in key.read_bytes() and "low_plausibility" not in pd.read_excel(sheet, dtype=str).to_csv()
    assert a.edge_ids_from_key(key) == {1: "e1", 2: "e4", 3: "e5"} and len(blob) > 0


def test_draw_audit_sample_on_a_small_layer(tmp_path, monkeypatch):
    kg = tmp_path / "data" / "kg" / "pharos"
    kg.mkdir(parents=True)
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "pharos.yaml").write_text("prune:\n  pair_groups:\n    gene_condition: [biolink:gene_associated_with_condition]\n")
    n = 400
    v = pd.DataFrame({"id": [f"e{i}" for i in range(n)], "subject": [f"UniProtKB:S{i}" for i in range(n)],
                      "predicate": ["biolink:physically_interacts_with" if i % 2 else "biolink:gene_associated_with_condition" for i in range(n)],
                      "object": [f"UniProtKB:O{i}" for i in range(n)], "source": "Src", "primary_knowledge_source": "infores:x",
                      "confidence": "0.5", "verdict": ["prune" if i % 4 == 0 else "keep" for i in range(n)], "reasons": "r"})
    for name, df in (("edge_verdicts.tsv.gz", v), ("edges.tsv.gz", v[["id"]].assign(knowledge_level="knowledge_assertion", pmids="")),
                     ("nodes.tsv.gz", pd.DataFrame({"id": ["UniProtKB:S1"], "name": ["s one"]}))):
        with gzip.open(kg / name, "wt") as f:
            df.to_csv(f, sep="\t", index=False)
    monkeypatch.setattr(a, "ROOT", tmp_path)
    old_key = tmp_path / "old_key.tsv"
    pd.DataFrame({"row": [1, 2], "edge_id": ["e0", "nope"]}).to_csv(old_key, sep="\t", index=False)
    res = a.draw_audit_sample(n=100, seed=7, sheet=tmp_path / "s.xlsx", key=tmp_path / "k.tsv", exclude_key=old_key)
    assert res == {"verdict_rows": 400, "prune": 100, "keep": 100, "shared_with_preregistered": 1}
    blind = pd.read_excel(tmp_path / "s.xlsx", sheet_name="spot_check", dtype=str)
    assert len(blind) == 200 and "verdict" not in blind.columns and "confidence" not in blind.columns
    assert set(pd.read_csv(tmp_path / "k.tsv", sep="\t")["verdict"]) == {"prune", "keep"}
    assert len(a.read_rows(tmp_path / "s.xlsx")) == 200


def test_main_owner40_and_publish(tmp_path, work, monkeypatch, capsys):
    sheet = _sheet(tmp_path)
    key = tmp_path / "key.tsv"
    pd.DataFrame({"row": range(1, 7), "edge_id": [f"e{i}" for i in range(1, 7)]}).to_csv(key, sep="\t", index=False)
    labels = _labels(tmp_path, {i: ("supported",) * 3 for i in range(1, 7)})
    monkeypatch.setattr(a, "SETS", {"spotcheck": dict(sheet=sheet, key=key, work=work, dossiers=tmp_path / "d.jsonl.gz", labels=labels)})
    monkeypatch.setattr(a, "OUT", tmp_path)
    monkeypatch.setattr(a, "ROOT", tmp_path)
    monkeypatch.setattr(a, "AUDIT", tmp_path / "audit")
    assert a.main(["owner40"]) == 0 and (tmp_path / "spotcheck_owner40.xlsx").exists()
    assert "6 rows" in capsys.readouterr().out                                         # 40 asked, the sheet has 6
    assert a.main(["adjudicate"]) == 0 and (tmp_path / "spotcheck_adjudicate.xlsx").exists()
    assert a.main(["publish"]) == 0 and len(list((tmp_path / "audit" / "edges").glob("e*.json"))) == 6
