"""Tests for pipelines/edge_evidence.py with fake fetchers (no network, no model, no data/)."""
import json
import time

import pytest

import edge_evidence as ee


class Fake:
    """Fetcher stand-in: answers by the first key that is a substring of 'url params'; a key mapped to an Exception
    raises it; an unknown request returns None (a stored 404)."""

    def __init__(self, table):
        self.table, self.calls = table, []

    def _get(self, url, params):
        probe = url + " " + json.dumps(params or {}, sort_keys=True)
        self.calls.append(probe)
        for k, v in self.table.items():
            if k in probe:
                if isinstance(v, Exception):
                    raise v
                return v
        return None

    def json(self, source, url, params=None, headers=None):
        return self._get(url, params)

    def text(self, source, url, params=None):
        return self._get(url, params)

    def post(self, source, url, body):
        return self._get(url, body)


def mitab(a, b, ac="EBI-1", itype='psi-mi:"MI:0915"(physical association)', pmid="111", expansion="-", negative="false"):
    c = ["-"] * 42
    c[0], c[1] = f"uniprotkb:{a}", f"uniprotkb:{b}"
    c[6], c[7], c[8] = 'psi-mi:"MI:0018"(two hybrid)', "Van et al. (1996)", f"pubmed:{pmid}|mint:M-1"
    c[11], c[13], c[14], c[15], c[35] = itype, f"intact:{ac}", "intact-miscore:0.62", expansion, negative
    return "\t".join(c)


EDGE = dict(subject_name="HRAS", subject_id="UniProtKB:P01112", predicate="physically_interacts_with", object_name="RAF1",
            object_id="UniProtKB:P04049", source="STRINGDB (infores:string)", knowledge_level="knowledge_assertion",
            source_statistic="stringdb_score=0.9", references="")


@pytest.fixture
def lookups(tmp_path):
    ref = tmp_path / "data" / "reference"
    ref.mkdir(parents=True)
    (ref / "hgnc_gene_ids.tsv").write_text("hgnc_id\tsymbol\tensembl_id\tncbigene_id\tuniprot_id\tlocus_group\n"
                                           "HGNC:5173\tHRAS\tENSG00000174775\tNCBIGene:3265\tP01112\tprotein-coding gene\n"
                                           "HGNC:9829\tRAF1\tENSG00000132155\tNCBIGene:5894\tP04049\tprotein-coding gene\n")
    (ref / "mondo_terms.tsv").write_text("# source: test\nmondo_id\tlabel\texact_synonyms\n"
                                         "MONDO:0005233\tnon-small cell lung carcinoma\tNSCLC|non-small cell lung cancer|NSC|a very long synonym for the lung disease\n")
    (ref / "mondo_efo_exact.tsv").write_text("mondo_id\tefo_id\nMONDO:0005233\tEFO:0003060\n")
    with open(ref / "mondo_terms.tsv", "a") as f:
        f.write("MONDO:0005061\tlung adenocarcinoma\t\nMONDO:0000002\tacinar lung adenocarcinoma\t\nMONDO:0000003\tlung squamous cell carcinoma\t\n")
    onto = tmp_path / "data" / "ontologies" / "mondo"
    onto.mkdir(parents=True)
    import gzip
    with gzip.open(onto / "relations.tsv.gz", "wt") as f:
        f.write("subject\trelation\tobject\tsubject_namespace\tobject_namespace\n"
                "MONDO:0005061\tis_a\tMONDO:0005233\tmondo\tmondo\nMONDO:0000003\tis_a\tMONDO:0005233\tmondo\tmondo\n"
                "MONDO:0000002\tis_a\tMONDO:0005061\tmondo\tmondo\nMONDO:0000002\tpart_of\tMONDO:0009999\tmondo\tmondo\n")
    return ee.Lookups(tmp_path)


# ---------------------------------------------------------------------------------------------- small helpers
def test_ids_and_records():
    assert ee.prefix_of("UniProtKB:P01112") == "UniProtKB" and ee.prefix_of("x") == ""
    assert ee.bare("UniProtKB:P01112-2") == "P01112"
    assert ee.local_id("CHEMBL.COMPOUND:CHEMBL939") == "CHEMBL939" and ee.local_id("GO:0005886") == "GO:0005886"
    r = ee.source_record("IntAct", "answered", [ee.make_item("a  b\nc", "u", "context", cls="x")], note="n", total=1)
    assert r["items"][0] == {"text": "a b c", "url": "u", "kind": "context", "cls": "x"} and r["examined"] is None


def test_name_pattern_symbols_are_case_sensitive_long_names_are_not():
    pat = ee.name_pattern(["SET", "Nischarin"])
    assert pat.search("the SET protein") and not pat.search("a set of genes") and not pat.search("ASSETS")
    assert pat.search("loss of nischarin in cells")
    assert ee.name_pattern([]) is None and ee.name_pattern(["", "x"]) is None


def test_text_helpers():
    assert ee.clean_abstract("<h4>Background</h4> A <i>b</i>  c") == "Background A b c"
    assert ee.split_sentences("HRAS binds RAF1. It is a GTPase! Is it? (Yes.)") == ["HRAS binds RAF1.", "It is a GTPase!", "Is it?", "(Yes.)"]
    assert ee.pmids_in("PMID:8670882 | PMID:12345 (+2 more)") == ["8670882", "12345"] and ee.pmids_in(None) == []
    assert ee.cut_words("a b c d", 2) == "a b ..." and ee.cut_words("a b", 2) == "a b"


def test_list_gap_and_pick_sentences_prefer_direct_statements():
    a, b = ee.name_pattern(["HRAS"]), ee.name_pattern(["RAF1"])
    assert ee.list_gap("HRAS binds RAF1.", a, b) == 0
    assert ee.list_gap("Variants in BRAF, HRAS, KRAS, NRAS, RAF1 and SOS1.", a, b) == 3
    assert ee.list_gap("Only HRAS here.", a, b) == 99
    text = "Variants in BRAF, HRAS, KRAS, NRAS, RAF1 were found. We show that HRAS binds RAF1 directly. Nothing else."
    sents, both, gap = ee.pick_sentences(text, a, b, k=1)
    assert both and gap == 0 and sents == ["We show that HRAS binds RAF1 directly."]
    sents, both, gap = ee.pick_sentences("HRAS is a GTPase. RAF1 is a kinase. Other.", a, b)
    assert not both and gap == 99 and sents == ["HRAS is a GTPase.", "RAF1 is a kinase."]
    assert ee.pick_sentences("x" * 500 + " HRAS RAF1.", a, b, width=20)[0][0].endswith("...")


def test_lookups(lookups):
    assert lookups.gene("UniProtKB:P01112-2") == {"symbol": "HRAS", "ensembl": "ENSG00000174775", "uniprot": "P01112",
                                                 "ncbigene": "3265", "hgnc": "5173"}
    assert lookups.gene("NCBIGene:5894")["symbol"] == "RAF1" and lookups.gene("HGNC:5173")["symbol"] == "HRAS"
    assert lookups.gene("UniProtKB:Q00000") == {} and lookups.gene("MONDO:1") == {}
    assert lookups.disease_names("MONDO:0005233", "non-small cell lung carcinoma") == [
        "non-small cell lung carcinoma", "NSCLC", "non-small cell lung cancer", "a very long synonym for the lung disease"]
    assert lookups.disease_names("EFO:1", "asthma") == ["asthma"]
    assert lookups.narrower("MONDO:0005233") == ["lung squamous cell carcinoma", "lung adenocarcinoma", "acinar lung adenocarcinoma"]
    assert lookups.narrower("MONDO:0005233", k=1) == ["lung squamous cell carcinoma"] and lookups.narrower("MONDO:0000002") == []
    assert lookups.open_targets_ids("MONDO:0005233") == ["MONDO_0005233", "EFO_0003060"]
    assert lookups.open_targets_ids("ORDO:98") == ["Orphanet_98"] and lookups.open_targets_ids("UMLS:C1") == []


def test_protein_names():
    body = {"genes": [{"geneName": {"value": "NISCH"}, "synonyms": [{"value": "IRAS"}, {"value": "ab"}]}],
            "proteinDescription": {"recommendedName": {"fullName": {"value": "Nischarin"}, "shortNames": [{"value": "I1R"}]}},
            "uniProtKBCrossReferences": [{"database": "Ensembl", "properties": [{"key": "GeneId", "value": "ENSG00000010322.16"}]}]}
    r = ee.protein_names("UniProtKB:Q9Y2I1", Fake({"Q9Y2I1": body}))
    assert r["names"] == ["NISCH", "Nischarin", "I1R", "IRAS"] and r["ensembl"] == "ENSG00000010322"
    assert ee.protein_names("Q0", Fake({}))["status"] == "not found"
    assert ee.protein_names("Q0", Fake({"Q0": RuntimeError("down")}))["status"] == "failed"   # never raises


# ---------------------------------------------------------------------------------------------- collectors
def test_mitab_parsing_and_classes():
    assert ee._mi('psi-mi:"MI:0915"(physical association)') == ("MI:0915", "physical association")
    assert [ee.interaction_class(t) for t in ("physical association", "phosphorylation reaction", "colocalization",
                                              "synthetic genetic interaction")] == ["physical", "physical", "colocalization", "genetic"]
    text = "\n".join([mitab("P01112", "P04049"), mitab("P04049-2", "P01112", ac="EBI-2"), mitab("P01112", "P04049"),
                      mitab("P01112", "Q99999", ac="EBI-3"), "short\tline"])
    recs = ee.parse_mitab(text, "P01112", "P04049")
    assert [r["ac"] for r in recs] == ["EBI-1", "EBI-2"] and recs[0]["pmid"] == "111" and recs[0]["miscore"] == "0.62"
    assert recs[0]["method"] == "two hybrid" and not recs[0]["expanded"] and not recs[0]["negative"]


def test_collect_intact_orders_and_marks_same_experiment():
    text = "\n".join([mitab("P01112", "P04049", ac="EBI-9", itype='psi-mi:"MI:0403"(colocalization)'),
                      mitab("P01112", "P04049", ac="EBI-8", negative="true"),
                      mitab("P01112", "P04049", ac="EBI-7", pmid="26186194", expansion='psi-mi:"MI:1060"(spoke expansion)'),
                      mitab("P01112", "P04049", ac="EBI-6")])
    f = Fake({"id:P01112 AND id:P04049": text})
    r = ee.collect_intact("UniProtKB:P01112", "UniProtKB:P04049", f, own_sources=["BioPlex (infores:bioplex)"])
    assert r["status"] == "answered" and r["total"] == 4 and "2 physical-type, 1 colocalization, 0 genetic, 1 negative" in r["note"]
    assert [i["cls"] for i in r["items"]] == ["physical", "physical", "colocalization", "physical"]     # negative last
    bioplex = next(i for i in r["items"] if i["pmid"] == "26186194")
    assert bioplex["kind"] == "context" and "same experiment" in bioplex["text"] and "spoke expansion" in bioplex["text"]
    assert "NEGATIVE record" in r["items"][-1]["text"]
    plain = ee.collect_intact("P01112", "P04049", f, own_sources=["STRINGDB"])
    assert all(i["kind"] == "evidence" for i in plain["items"])                       # not a BioPlex row: evidence
    self_pair = Fake({"idA:P01112 AND idB:P01112": mitab("P01112", "P01112")})
    assert ee.collect_intact("P01112", "P01112", self_pair)["total"] == 1
    assert ee.collect_intact("P01112", "P04049", Fake({}))["total"] == 0              # empty answer: answered, nothing
    assert ee.collect_intact("P01112", "P04049", Fake({"id:": RuntimeError("x")}))["status"] == "failed"


def test_collect_complex_portal():
    el = lambda ac, ids, predicted=False: {"complexAC": ac, "complexName": "n " + ac, "organismName": "Homo sapiens; 9606",
                                           "predictedComplex": predicted,
                                           "interactors": [{"identifier": i, "stochiometry": f"minValue: {n}, maxValue: {n}"} for i, n in ids]}
    f = Fake({"complex-ws/search/P01112": {"elements": [el("CPX-1", [("P01112", 2), ("Q07889", 1)]), el("CPX-2", [("P01112", 1), ("P04049", 1)]),
                                                         el("CPX-3", [("P01112", 1), ("P04049-1", 1)], predicted=True)]}})
    r = ee.collect_complex_portal("P01112", "P04049", f)
    assert [(i["kind"], "CPX-2" in i["text"], "CPX-3" in i["text"]) for i in r["items"]] == [("evidence", True, False), ("context", False, True)]
    assert r["total"] == 2 and r["examined"] == 3
    self_r = ee.collect_complex_portal("P01112", "P01112", f)
    assert len(self_r["items"]) == 1 and "two or more copies" in self_r["items"][0]["text"]
    assert ee.collect_complex_portal("P1", "P2", Fake({}))["total"] == 0


def test_collect_reactome_shared_and_own_source():
    hs = lambda st, name, sp="Homo sapiens", **k: {"stId": st, "displayName": name, "speciesName": sp, **k}
    f = Fake({"complexes/UniProt/P01112": [hs("R-HSA-1", "RAS:RAF1"), hs("R-HSA-2", "other")],
              "complexes/UniProt/P04049": [hs("R-HSA-1", "RAS:RAF1"), hs("R-MMU-9", "mouse", "Mus musculus")],
              "mapping/UniProt/P01112/reactions": [hs("R-HSA-5", "RAF1 binds RAS", isInferred=True)],
              "mapping/UniProt/P04049/reactions": [hs("R-HSA-5", "RAF1 binds RAS", isInferred=True)]})
    r = ee.collect_reactome("P01112", "P04049", f)
    assert [i["what"] for i in r["items"]] == ["complex", "reaction"] and r["total"] == 2
    assert all(i["kind"] == "evidence" for i in r["items"]) and "inferred from another species" in r["items"][1]["text"]
    own = ee.collect_reactome("P01112", "P04049", f, own_sources=["Reactome,STRINGDB (infores:string)"])
    assert all(i["kind"] == "context" for i in own["items"]) and "own source" in own["items"][0]["text"]
    assert ee.collect_reactome("P1", "P1", f)["status"] == "not applicable"
    assert ee.collect_reactome("P1", "P2", Fake({}))["total"] == 0                    # 404 for both proteins: nothing shared


def test_collect_comention_ranks_direct_sentences_first():
    assert ee.epmc_query(["HRAS", 'p21 "ras"'], ["RAF1"]) == '(TITLE_ABS:"HRAS" OR TITLE_ABS:"p21  ras ") AND (TITLE_ABS:"RAF1")'
    res = [{"source": "MED", "pmid": "1", "pubYear": "2020", "title": "Panel.", "abstractText": "Variants in BRAF, HRAS, KRAS, NRAS, RAF1."},
           {"source": "MED", "pmid": "2", "pubYear": "2015", "title": "Binding.", "abstractText": "<b>HRAS</b> binds RAF1 directly."},
           {"source": "PPR", "id": "PPR9", "title": "Separate.", "abstractText": "HRAS is a GTPase. RAF1 is a kinase."}]
    f = Fake({"TITLE_ABS": {"hitCount": 40, "resultList": {"result": res}}})
    r = ee.collect_comention(["HRAS"], ["RAF1"], f, max_shown=2)
    assert r["total"] == 40 and r["examined"] == 3 and "2 of those name both in one sentence" in r["note"]
    assert [i["pmid"] for i in r["items"]] == ["2", "1"] and r["items"][0]["both_in_sentence"]
    assert "HRAS binds RAF1 directly." in r["items"][0]["text"] and "<b>" not in r["items"][0]["text"]
    all3 = ee.collect_comention(["HRAS"], ["RAF1"], f)
    assert "Europe PMC PPR PPR9" in all3["items"][2]["text"] and "No sentence names both" in all3["items"][2]["text"]
    assert ee.collect_comention([], ["RAF1"], f)["status"] == "not found"
    wide = ee.collect_comention(["HRAS"], ["lung cancer"], f, narrower_b=["RAF1", "lung adenocarcinoma"])
    assert wide["query"].endswith('(TITLE_ABS:"lung cancer" OR TITLE_ABS:"RAF1" OR TITLE_ABS:"lung adenocarcinoma")')
    assert "together with lung cancer or one of 2 more specific terms" in wide["note"] and wide["items"][0]["both_in_sentence"]
    assert ee.collect_comention(["A1"], ["B1"], Fake({"TITLE_ABS": {"hitCount": 0, "resultList": {"result": []}}}))["total"] == 0


def test_collect_references():
    f = Fake({"EXT_ID:111": {"resultList": {"result": [{"pubYear": "1996", "title": "T.", "abstractText": "HRAS binds RAF1. Other."}]}},
              "EXT_ID:222": {"resultList": {"result": []}}})
    r = ee.collect_references(["111", "222"], ["HRAS"], ["RAF1"], f)
    assert r["total"] == 2 and r["examined"] == 1 and "1 not found" in r["note"]
    assert r["items"][0]["kind"] == "evidence" and "cited by the row" in r["items"][0]["text"] and r["items"][0]["pmid"] == "111"
    assert ee.collect_references([], ["a"], ["b"], f)["status"] == "not applicable"


def test_collect_open_targets():
    row = {"score": 0.92, "datatypeScores": [{"id": "literature", "score": 0.99}, {"id": "genetic_association", "score": 0.87}]}
    ok = {"data": {"disease": {"id": "MONDO_1", "name": "d"}, "target": {"approvedSymbol": "EGFR", "associatedDiseases": {"rows": [row]}}}}
    f = Fake({'"efo": "MONDO_1"': ok, '"efo": "MONDO_0"': {"data": {"disease": None, "target": {"approvedSymbol": "EGFR"}}}})
    r = ee.collect_open_targets("ENSG1", ["MONDO_0", "MONDO_1"], f)                  # first id unknown: the second is tried
    assert r["status"] == "answered" and "overall score 0.92" in r["note"]
    assert [(i["datatype"], i["kind"]) for i in r["items"]] == [("literature", "context"), ("genetic_association", "evidence")]
    assert ee.collect_open_targets("ENSG1", ["MONDO_0"], f)["status"] == "not found"
    own = ee.collect_open_targets("ENSG1", ["MONDO_1"], f, own_sources=["Open Targets 26.09 overall direct (infores:opentargets)"])
    assert all(i["kind"] == "context" for i in own["items"]) and "own source" in own["items"][1]["text"]
    assert ee.collect_open_targets("", ["MONDO_1"], f)["status"] == "not found"
    assert ee.collect_open_targets("ENSG1", [], f)["status"] == "not found"
    none = {"data": {"disease": {"id": "MONDO_2", "name": "d2"}, "target": {"approvedSymbol": "EGFR", "associatedDiseases": {"rows": []}}}}
    r0 = ee.collect_open_targets("ENSG1", ["MONDO_2"], Fake({"MONDO_2": none}))
    assert r0["status"] == "answered" and r0["items"] == [] and "no association" in r0["note"]
    assert ee.collect_open_targets("ENSG1", ["M"], Fake({"M": {"data": {"target": None}}}))["status"] == "not found"


def test_collect_ot_literature_reads_more_specific_terms():
    row = lambda pmid, dis, sents, score=0.5: {"disease": {"id": dis, "name": "name of " + dis}, "literature": [pmid], "score": score,
                                              "textMiningSentences": [{"text": t, "section": sec} for sec, t in sents]}
    rows = [row("1", "MONDO_9", [("results", "NISCH was down-regulated in lung adenocarcinoma tissue.")], 0.9),
            row("2", "MONDO_1", [("other", "A long sentence from the discussion about NISCH."), ("abstract", "NISCH loss drives NSCLC.")], 0.1),
            row("2", "MONDO_1", [("abstract", "duplicate paper")]), row("3", "MONDO_1", [("abstract", "非英语句子完全没有英文字母")])]
    f = Fake({'"ind": true': {"data": {"disease": {"id": "MONDO_1", "name": "NSCLC", "evidences": {"count": 37, "rows": rows}}}}})
    r = ee.collect_ot_literature("ENSG1", ["MONDO_1"], f)
    assert r["status"] == "answered" and r["total"] == 37 and r["examined"] == 4
    assert [i["pmid"] for i in r["items"]] == ["2", "1"]                              # abstract sentence first; non-English and duplicate dropped
    assert "the claimed term" in r["items"][0]["text"] and "NISCH loss drives NSCLC." in r["items"][0]["text"]
    assert r["items"][1]["specific"] and "a more specific term under the claimed one" in r["items"][1]["text"]
    assert all(i["kind"] == "evidence" for i in r["items"])
    direct_only = Fake({'"ind": true': RuntimeError("502"), '"ind": false': {"data": {"disease": {"id": "MONDO_1", "name": "NSCLC", "evidences": {"count": 1, "rows": rows[1:2]}}}}})
    d = ee.collect_ot_literature("ENSG1", ["MONDO_1"], direct_only)
    assert d["status"] == "failed" and len(d["items"]) == 1 and "did not answer" in d["note"]   # hierarchy not covered: not 'answered'
    assert ee.collect_ot_literature("ENSG1", ["MONDO_0"], Fake({"MONDO_0": {"data": {"disease": None}}}))["status"] == "not found"
    assert ee.collect_ot_literature("", ["MONDO_1"], f)["status"] == "not found" and ee.collect_ot_literature("E", [], f)["status"] == "not found"
    assert ee._english("NISCH loss drives NSCLC.") and not ee._english("非英语句子 EGFR") and not ee._english("123")


def test_open_targets_disease_by_name_needs_an_exact_name():
    f = Fake({"lung carcinoma": {"data": {"search": {"hits": [{"id": "MONDO_7", "name": "Lung Cancer"}, {"id": "MONDO_8", "name": "lung carcinoma"}]}}}})
    assert ee.open_targets_disease_by_name(["lung carcinoma"], f) == "MONDO_8"
    assert ee.open_targets_disease_by_name(["lung carcinoma of the left lobe"], f) == ""
    assert ee.open_targets_disease_by_name(["x"], Fake({"x": RuntimeError("down")})) == "" and ee.open_targets_disease_by_name([], f) == ""


def test_gene_disease_falls_back_to_the_exact_name(lookups):
    assoc = {"data": {"disease": {"id": "MONDO_8", "name": "odd disease"}, "target": {"approvedSymbol": "HRAS", "associatedDiseases": {"rows": []}}}}
    f = Fake({"search(queryString": {"data": {"search": {"hits": [{"id": "MONDO_8", "name": "odd disease"}]}}},
              '"efo": "MONDO_8"': assoc, '"efo": "MONDO_5"': {"data": {"disease": None, "target": {"approvedSymbol": "HRAS"}}}})
    edge = dict(EDGE, predicate="gene_associated_with_condition", object_id="MONDO:5", object_name="odd disease")
    ot = ee.collect_for_edge(edge, f, lookups)[0]
    assert ot["status"] == "answered" and "matched to Open Targets by its exact name: MONDO_8" in ot["note"]


def test_collect_quickgo_sorts_manual_first_and_flags_not():
    a = lambda go, ev, ref, q="located_in": {"goId": go, "goName": "pm", "goEvidence": ev, "reference": ref, "qualifier": q, "assignedBy": "X"}
    res = [a("GO:0005886", "IEA", "GO_REF:1"), a("GO:0005886", "IDA", "PMID:1"), a("GO:0005886", "IDA", "PMID:1"),
           a("GO:0099999", "IMP", "PMID:2"), a("GO:0005886", "IDA", "PMID:3", "NOT|located_in")]
    r = ee.collect_quickgo("UniProtKB:P00533", "GO:0005886", Fake({"QuickGO": {"numberOfHits": 5, "results": res}}))
    assert r["total"] == 4 and "2 manual, 1 electronic or no-data, 1 NOT" in r["note"]
    assert [i["evidence_code"] for i in r["items"]] == ["IDA", "IMP", "IEA", "IDA"] and r["items"][-1]["negated"]
    assert "the same term" in r["items"][0]["text"] and "a more specific term" in r["items"][1]["text"]
    assert ee.collect_quickgo("P1", "GO:1", Fake({}))["total"] == 0
    gone = Fake({"annotation/search": RuntimeError("HTTP 500"), "terms/GO:0055114": {"results": [{"id": "GO:0055114", "isObsolete": True}]}})
    r = ee.collect_quickgo("P1", "GO:0055114", gone)
    assert r["status"] == "not found" and "obsolete in the current Gene Ontology" in r["note"]      # not a source failure
    unknown = Fake({"annotation/search": RuntimeError("HTTP 500"), "terms/GO:0000000": {"results": []}})
    assert ee.collect_quickgo("P1", "GO:0000000", unknown)["status"] == "not found"
    live = Fake({"annotation/search": RuntimeError("HTTP 500"), "terms/GO:0005886": {"results": [{"id": "GO:0005886", "isObsolete": False}]}})
    assert ee.collect_quickgo("P1", "GO:0005886", live)["status"] == "failed"                         # a live term: the source did fail


def test_collect_pathways():
    f = Fake({"participants/R-HSA-1": [{"databaseName": "UniProt", "identifier": "P00533"}, {"databaseName": "ChEBI", "identifier": "1"}]})
    r = ee.collect_reactome_pathway("UniProtKB:P00533", "REACT:R-HSA-1", f)
    assert len(r["items"]) == 1 and "1 participant proteins" in r["items"][0]["text"]
    assert ee.collect_reactome_pathway("P1", "REACT:R-HSA-1", f)["items"] == []
    assert ee.collect_reactome_pathway("P1", "REACT:R-HSA-404", f)["status"] == "not found"
    w = Fake({"WP437": "Label\tType\tIdentifier\tComment\tEnsembl\tNCBI gene\tHGNC\tUniProt\n"
                       "COX2\tGeneProduct\tncbigene:4513\t\"\"\tensembl:ENSG00000198712\tncbigene:4513\thgnc.symbol:MT-CO2\tuniprot:P00403;uniprot:U5Z487\n"
                       "EGFR\tGeneProduct\tncbigene:1956\t\"\"\tensembl:ENSG00000146648\tncbigene:1956\thgnc.symbol:EGFR\tuniprot:P00533\n"})
    assert len(ee.collect_wikipathways({"symbol": "EGFR", "ncbigene": "1956"}, "WIKIPATHWAYS:WP437", w)["items"]) == 1
    assert len(ee.collect_wikipathways({"uniprot": "U5Z487"}, "WIKIPATHWAYS:WP437", w)["items"]) == 1         # second accession of a cell
    assert len(ee.collect_wikipathways({"symbol": "MT-CO2"}, "WIKIPATHWAYS:WP437", w)["items"]) == 1
    assert ee.collect_wikipathways({"symbol": "COX2"}, "WIKIPATHWAYS:WP437", w)["items"] == []              # the label column is not an identifier
    assert ee.collect_wikipathways({"symbol": "TP53"}, "WIKIPATHWAYS:WP437", w)["items"] == []
    assert ee.collect_wikipathways({"symbol": "TP53"}, "WIKIPATHWAYS:WP0", w)["status"] == "not found"


def test_collect_chembl():
    assert ee.collect_chembl("PUBCHEM.COMPOUND:5", "P00533", Fake({}))["status"] == "not found"
    assert ee.collect_chembl("CHEMBL.COMPOUND:CHEMBL939", "P00533", Fake({}))["total"] == 0
    act = lambda i, t, p, **k: {"activity_id": i, "target_chembl_id": t, "pchembl_value": p, "standard_type": "IC50", "standard_relation": "=",
                               "standard_value": "5", "standard_units": "nM", "target_pref_name": "EGFR", "assay_chembl_id": "CHEMBL1", "assay_type": "B", **k}
    f = Fake({"target.json": {"targets": [{"target_chembl_id": "CHEMBL203", "target_type": "SINGLE PROTEIN"},
                                          {"target_chembl_id": "CHEMBL9", "target_type": "PROTEIN FAMILY"}]},
              "activity.json": {"page_meta": {"total_count": 3},
                                "activities": [act(1, "CHEMBL9", "9.0"), act(2, "CHEMBL203", "4.0"), act(3, "CHEMBL203", "8.3", data_validity_comment="Outside typical range")]}})
    r = ee.collect_chembl("CHEMBL.COMPOUND:CHEMBL939", "UniProtKB:P00533", f)
    assert [i["pchembl"] for i in r["items"]] == ["8.3", "4.0", "9.0"] and [i["single_protein"] for i in r["items"]] == [True, True, False]
    assert "Outside typical range" in r["items"][0]["text"] and "1 on the single protein with pChEMBL 5 or more" in r["note"]


def test_collect_impc():
    gene = {"response": {"docs": [{"marker_symbol": "Havcr2", "mgi_accession_id": "MGI:1", "phenotyping_data_available": True}]}}
    call = lambda mp, up=(): {"mp_term_id": mp, "mp_term_name": "n", "intermediate_mp_term_id": list(up), "p_value": 1e-9, "zygosity": "homozygote", "sex": "male"}
    f = Fake({"gene/select": gene, "genotype-phenotype/select": {"response": {"docs": [call("MP:0000002", ["MP:0000001"]), call("MP:0000001"),
                                                                                    call("MP:0000000"), call("MP:0000009")]}},
              "mp/select": {"response": {"docs": [{"intermediate_mp_id": ["MP:0000000"]}]}}})
    r = ee.collect_impc("HAVCR2", "MP:0000001", f)
    assert r["status"] == "answered" and r["total"] == 2 and r["examined"] == 4
    assert [i["kind"] for i in r["items"]] == ["evidence", "evidence", "context"] and "a broader term" in r["items"][2]["text"]
    assert ee.collect_impc("HAVCR2", "HP:0000001", f)["status"] == "not applicable"
    assert ee.collect_impc("", "MP:0000001", f)["status"] == "not found"
    assert ee.collect_impc("X", "MP:0000001", Fake({"gene/select": {"response": {"docs": []}}}))["status"] == "not found"


def test_collect_uniprot_gene_and_string():
    body = {"entryType": "UniProtKB reviewed (Swiss-Prot)", "uniProtKBCrossReferences": [
        {"database": "GeneID", "id": "1956"}, {"database": "HGNC", "id": "HGNC:3236"},
        {"database": "Ensembl", "properties": [{"key": "GeneId", "value": "ENSG00000146648.21"}]}]}
    f = Fake({"P00533": body, "P11111": {"entryType": "Inactive"}})
    assert len(ee.collect_uniprot_gene("UniProtKB:P00533", "NCBIGene:1956", f)["items"]) == 1
    assert len(ee.collect_uniprot_gene("P00533", "HGNC:3236", f)["items"]) == 1
    assert len(ee.collect_uniprot_gene("P00533", "ENSEMBL:ENSG00000146648", f)["items"]) == 1
    miss = ee.collect_uniprot_gene("P00533", "NCBIGene:7", f)
    assert miss["status"] == "answered" and miss["items"] == []
    assert ee.collect_uniprot_gene("P11111", "NCBIGene:1", f)["status"] == "not found"       # obsolete entry
    assert ee.collect_uniprot_gene("P22222", "NCBIGene:1", f)["status"] == "not found"       # no entry
    assert ee.collect_uniprot_gene("P00533", "MONDO:1", f)["status"] == "not found"
    s = ee.collect_string("EGFR", "GRB2", Fake({"string-db": [{"preferredName_A": "EGFR", "preferredName_B": "GRB2", "score": 0.999, "escore": 0.9}]}))
    assert s["items"][0]["kind"] == "context" and "combined score 0.999" in s["items"][0]["text"]
    assert ee.collect_string("A", "B", Fake({"string-db": []}))["items"] == []
    assert ee.collect_string("A", "A", Fake({}))["status"] == "not applicable"


# ---------------------------------------------------------------------------------------------- dossier
def test_edge_group():
    g = lambda p, s, o: ee.edge_group({"predicate": p, "subject_id": s, "object_id": o})
    assert g("biolink:physically_interacts_with", "UniProtKB:A", "UniProtKB:B") == "protein_protein"
    assert g("gene_associated_with_condition", "UniProtKB:A", "MONDO:1") == "gene_disease"
    assert g("genetically_associated_with", "UniProtKB:A", "EFO:1") == "gene_disease"
    assert g("located_in", "UniProtKB:A", "GO:1") == "gene_ontology" and g("actively_involved_in", "UniProtKB:A", "GO:1") == "gene_ontology"
    assert g("actively_involved_in", "UniProtKB:A", "REACT:R-HSA-1") == "pathway"
    assert g("directly_physically_interacts_with", "CHEMBL.COMPOUND:CHEMBL1", "UniProtKB:B") == "ligand"
    assert g("has_phenotype", "NCBIGene:1", "MP:1") == "phenotype" and g("has_gene_product", "NCBIGene:1", "UniProtKB:B") == "gene_product"
    assert g("expressed_in", "UniProtKB:A", "UBERON:1") == "other"


def _ppi_fake():
    up = lambda sym: {"genes": [{"geneName": {"value": sym}}], "proteinDescription": {}}
    return Fake({"rest.uniprot.org/uniprotkb/P01112": up("HRAS"), "rest.uniprot.org/uniprotkb/P04049": up("RAF1"),
                 "id:P01112 AND id:P04049": mitab("P01112", "P04049"), "complex-ws": {"elements": []},
                 "TITLE_ABS": {"hitCount": 0, "resultList": {"result": []}},
                 "string-db": [{"preferredName_A": "HRAS", "preferredName_B": "RAF1", "score": 0.9}]})


def test_build_dossier_is_blind_and_numbers_items(lookups):
    edge = dict(EDGE, verdict="prune", confidence="0.03", reasons="low_plausibility", edge_id_secret="e123")
    d = ee.build_dossier(edge, _ppi_fake(), lookups, today="2026-11-12")
    blob = json.dumps(d)
    assert set(d["edge"]) == set(ee.BLIND_FIELDS)
    assert "prune" not in blob and "0.03" not in blob and "low_plausibility" not in blob and "e123" not in blob
    assert d["group"] == "protein_protein" and d["collected"] == "2026-11-12" and d["schema"] == ee.SCHEMA_VERSION
    assert d["required"] == ["IntAct", "Complex Portal", "Reactome", "Europe PMC co-mention search"]
    assert [i["id"] for i in ee.dossier_items(d)] == ["IA1", "ST1"]
    assert [s["source"] for s in d["sources"]] == ["IntAct", "Complex Portal", "Reactome", "Europe PMC co-mention search",
                                                   "The row's own references", "STRING"]


def test_dossier_text_separates_context_and_hides_edge_id(lookups):
    d = ee.build_dossier(EDGE, _ppi_fake(), lookups, today="2026-11-12", edge_id="abc123def")
    text = ee.dossier_text(d)
    assert d["edge_id"] == "abc123def" and "abc123def" not in text
    assert text.startswith("CLAIM\nSubject: HRAS (UniProtKB:P01112)") and "- IntAct [required source]: answered." in text
    head, _, ctx = text.partition("CONTEXT ITEMS")
    assert "[IA1]" in head and "[ST1]" not in head and "[ST1]" in ctx
    assert "stringdb_score=0.9" in ee.claim_text(d["edge"])


def test_collect_for_edge_other_groups(lookups):
    go = ee.collect_for_edge(dict(EDGE, predicate="located_in", object_id="GO:0005886"), Fake({}), lookups)
    assert [s["source"] for s in go] == ["QuickGO"]
    kegg = ee.collect_for_edge(dict(EDGE, predicate="actively_involved_in", object_id="KEGG.PATHWAY:hsa1"), Fake({}), lookups)
    assert kegg[0]["source"] == "KEGG" and kegg[0]["status"] == "not applicable"
    gd = ee.collect_for_edge(dict(EDGE, predicate="gene_associated_with_condition", object_id="MONDO:0005233", object_name="x"), Fake({}), lookups)
    assert [s["source"] for s in gd] == ["Open Targets", "Open Targets literature sentences", "Europe PMC co-mention search",
                                         "The row's own references"]
    gp = ee.collect_for_edge(dict(EDGE, predicate="has_gene_product", subject_id="NCBIGene:3265", object_id="UniProtKB:P01112"), Fake({}), lookups)
    assert [s["source"] for s in gp] == ["UniProt"]
    ids = ee.number_items([{"source": "IntAct", "items": [{}, {}]}, {"source": "Nope", "items": [{}]}])
    assert ids == ["IA1", "IA2", "XX1"]


# ---------------------------------------------------------------------------------------------- raters
def _dossier(lookups):
    return ee.build_dossier(EDGE, _ppi_fake(), lookups, today="2026-11-12")


def test_judge_messages_carry_rules_and_no_verdict(lookups):
    msgs = ee.judge_messages(_dossier(lookups))
    assert msgs[0]["role"] == "system" and "interact physically" in msgs[0]["content"] and "Use only the dossier" in msgs[0]["content"]
    assert msgs[1]["content"].startswith("CLAIM")
    assert set(ee.RULES_GROUP) == set(ee.REQUIRED) - {"gene_product"}


def test_check_label_corrections(lookups):
    d = _dossier(lookups)
    ok = ee.check_label({"label": "supported", "rationale": "IA1 is a physical association record.", "items": ["IA1"]}, d)
    assert ok["label"] == "supported" and ok["problems"] == []
    assert ee.check_label({"label": "unsupported", "rationale": "Nothing found.", "items": []}, d)["label"] == "unsupported"
    bad_item = ee.check_label({"label": "supported", "rationale": "x", "items": ["IA1", "IA7"]}, d)
    assert bad_item["label"] == "cannot tell" and bad_item["label_raw"] == "supported" and "IA7" in bad_item["problems"][0]
    assert ee.check_label({"label": "unsupported", "rationale": "PM3 shows nothing", "items": []}, d)["label"] == "cannot tell"
    outside = ee.check_label({"label": "supported", "rationale": "Known from PMID 7777777 and EBI-999.", "items": ["IA1"]}, d)
    assert outside["label"] == "cannot tell" and "EBI-999" in outside["problems"][0] and "PubMed 7777777" in outside["problems"][0]
    inside = ee.check_label({"label": "supported", "rationale": "EBI-1, PubMed 111.", "items": ["[IA1]"]}, d)
    assert inside["label"] == "supported" and inside["items"] == ["IA1"]                      # records that ARE in the dossier
    ctx = ee.check_label({"label": "supported", "rationale": "STRING score is high.", "items": ["ST1"]}, d)
    assert ctx["label"] == "cannot tell" and "without citing an evidence item" in ctx["problems"][0]
    assert ee.check_label({"label": "supported", "rationale": "x", "items": []}, d)["label"] == "cannot tell"
    for junk in (None, {}, {"label": "maybe"}):
        j = ee.check_label(junk, d)
        assert j["label"] == "cannot tell" and j["label_raw"] == ""


def test_deterministic_label(lookups):
    mk = lambda status, items: {"group": "gene_product", "sources": [{"source": "UniProt", "status": status, "items": items, "note": "n"}]}
    assert ee.deterministic_label(mk("answered", [{"id": "UP1"}]))["label"] == "supported"
    assert ee.deterministic_label(mk("answered", []))["label"] == "unsupported"
    assert ee.deterministic_label(mk("not found", []))["label"] == "cannot tell"
    assert ee.deterministic_label(_dossier(lookups)) is None


class FakeNim:
    def __init__(self, data=None, error=None):
        self.cfg, self.data, self.error, self.seen = {"roles": {}}, data, error, []

    def chat(self, role, messages, schema=None):
        self.seen.append((role, messages, schema))
        if self.error:
            raise self.error
        return type("R", (), {"data": self.data, "key": "k1"})()


def test_judge_uses_the_model_client_and_never_sends_a_verdict(lookups):
    d = ee.build_dossier(dict(EDGE, verdict="prune", reasons="secret_reason"), _ppi_fake(), lookups)
    nim = FakeNim({"label": "supported", "rationale": "IA1.", "items": ["IA1"]})
    j = ee.judge(d, nim, "family/model-x")
    assert j["label"] == "supported" and j["model"] == "family/model-x" and j["key"] == "k1"
    role, messages, schema = nim.seen[0]
    assert nim.cfg["roles"][role] == "family/model-x" and schema == ee.JUDGE_SCHEMA
    assert "prune" not in json.dumps(messages) and "secret_reason" not in json.dumps(messages)
    bad = ee.judge(d, FakeNim(error=ValueError("reply does not match schema")), "m")
    assert bad["label"] == "cannot tell" and bad["label_raw"] == ""
    with pytest.raises(RuntimeError):
        ee.judge(d, FakeNim(error=RuntimeError("503")), "m")                                 # transport failure: retried later
    gp = {"group": "gene_product", "sources": [{"source": "UniProt", "status": "answered", "items": [{"id": "UP1"}], "note": ""}]}
    assert ee.judge(gp, None, "m")["model"].startswith("deterministic")                      # no model call at all


def test_combine():
    assert ee.combine(["supported"] * 3) == {"proposal": "supported", "flagged": False, "majority": "supported"}
    assert ee.combine(["supported", "supported", "cannot tell"]) == {"proposal": "", "flagged": True, "majority": "supported"}
    assert ee.combine(["supported", "unsupported", "cannot tell"]) == {"proposal": "", "flagged": True, "majority": ""}
    assert ee.combine([]) == {"proposal": "", "flagged": True, "majority": ""}


def test_audit_record(lookups):
    d = ee.build_dossier(EDGE, _ppi_fake(), lookups, today="2026-11-12", edge_id="e1")
    js = [{"label": "supported", "rationale": "IA1 is a record.", "items": ["IA1"]}] * 2 + [{"label": "cannot tell", "rationale": "x", "items": []}]
    rec = ee.audit_record(d, js)
    assert rec["edge_id"] == "e1" and rec["checked"] == "2026-11-12" and rec["statement"] == ee.STATEMENT
    assert rec["judgement"] == {"label": "supported", "rationale": "IA1 is a record.", "raters": 3, "raters_agreeing": 2, "items": ["IA1"]}
    assert rec["sources"][0]["source"] == "IntAct" and rec["sources"][0]["items"][0]["id"] == "IA1"
    blob = json.dumps(rec)
    assert "verdict" not in blob and "confidence" not in blob
    assert ee.audit_record(d, [])["judgement"]["label"] == "cannot tell"
    split = ee.audit_record(d, [{"label": "supported", "rationale": "IA1.", "items": ["IA1"]}, {"label": "unsupported", "rationale": "No.", "items": []}])
    assert split["judgement"] == {"label": "cannot tell", "rationale": "The raters did not agree (supported, unsupported), so the check is undecided.",
                                  "raters": 2, "raters_agreeing": 0, "items": []}


# ---------------------------------------------------------------------------------------------- fetchers
def test_fetcher_caches(tmp_path, monkeypatch):
    f = ee.Fetcher(directory=tmp_path, sleep=0)
    calls = []
    monkeypatch.setattr(f.snap, "_http", lambda method, *a, **k: calls.append(method) or ({"text": "abc"} if method == "GET_TEXT" else {"v": 1}))
    assert f.json("s", "http://x/a", {"q": 1}) == {"v": 1} and f.json("s", "http://x/a", {"q": 1}) == {"v": 1}
    assert f.text("s", "http://x/t") == "abc" and f.post("s", "http://x/p", {"b": 2}) == {"v": 1}
    assert calls == ["GET", "GET_TEXT", "POST"]                                               # the repeat came from the cache


def test_fast_fetcher_timeout_and_deadline(monkeypatch):
    class R:
        def __init__(self, code, body):
            self.status_code, self._b, self.text = code, body, "txt"

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(str(self.status_code))

        def json(self):
            return self._b
    seen = {}
    monkeypatch.setattr(ee.requests, "get", lambda url, **k: seen.update(k) or R(404 if "missing" in url else 200, {"a": 1}))
    monkeypatch.setattr(ee.requests, "post", lambda url, **k: R(500, None))
    f = ee.FastFetcher(timeout=2.5)
    assert f.json("s", "http://x") == {"a": 1} and seen["timeout"] == 2.5
    assert f.json("s", "http://x/missing") is None and f.text("s", "http://x") == "txt"
    with pytest.raises(RuntimeError):
        f.post("s", "http://x", {})
    with pytest.raises(TimeoutError):
        ee.FastFetcher(deadline=time.monotonic() - 1).json("s", "http://x")
