"""Tests for the seed knowledge graph. Unit tests run offline; output tests need `python pipelines/seed_kg.py` first."""
import json
import pandas as pd, pytest
import seed_kg as sk

def test_plus_sign_normalised():
    assert sk.norm("evoke+") == sk.norm("EVOKE Plus")

def test_roman_numeral_expansion():
    assert sk.expand_trial_tokens(["GRADUATE I", "II"]) == ["GRADUATE I", "GRADUATE II"]

def test_dataset_has_34_programs():
    progs = sk.parse_inventory(open(sk.DATASET).read())
    assert len(progs) == 34

REPORT = sk.OUT / "resolution_report.csv"
needs_output = pytest.mark.skipif(not REPORT.exists(), reason="run python pipelines/seed_kg.py first")

@needs_output
def test_every_named_trial_resolved():
    r = pd.read_csv(REPORT); t = r[r.entity_type == "trial"]
    assert t.resolved.all(), t[~t.resolved]["query"].tolist()

@needs_output
def test_ground_truth_mappings():
    r = pd.read_csv(REPORT); r = r[r.entity_type == "trial"].set_index(["program", "query"])
    assert r.loc[("P19", "TULIP-1"), "identifier"] == "NCT02446912"
    assert r.loc[("P06", "GENERATION HD1"), "rule"] == "secondary_id_exact"
    assert r.loc[("P10", "evoke+"), "identifier"] == "NCT04777409"

@needs_output
def test_no_duplicate_edges():
    e = pd.read_csv(sk.OUT / "kg_edges.csv")
    assert not e.duplicated(subset=["subject", "object", "predicate", "source_record"]).any()

@needs_output
def test_neo4j_loader_covers_every_category_and_predicate():
    cy = (sk.OUT / "load_neo4j.cypher").read_text()
    n, e = pd.read_csv(sk.OUT / "kg_nodes.csv"), pd.read_csv(sk.OUT / "kg_edges.csv")
    missing = [c for c in set(n.category) | set(e.predicate) if f"'{c}'" not in cy]
    assert not missing, f"add these to kg_schema.py: {missing}"


def test_curated_as_of_is_language_independent():
    assert sk.curated_as_of("Evidence review as of 8 September 2026.") == "2026-09-08"


@needs_output
def test_unconfirmed_intervention_edges_are_curated():
    e = pd.read_csv(sk.OUT / "kg_edges.csv", low_memory=False)
    t = e[e.predicate == "tmkg:tests_intervention"]
    assert set(t[t.evidence_type == "curated"].in_registry_arms.astype(str)) <= {"False"}   # may be none left
    assert set(t[t.evidence_type == "registry"].in_registry_arms.astype(str)) == {"True"}


@needs_output
def test_every_mesh_disease_mapped_or_flagged():
    n, e = pd.read_csv(sk.OUT / "kg_nodes.csv", low_memory=False), pd.read_csv(sk.OUT / "kg_edges.csv", low_memory=False)
    r = pd.read_csv(REPORT)
    mesh = set(n[n.id.str.startswith("MESH:")].id)
    mapped = set(e[e.predicate == "biolink:exact_match"].subject)
    flagged = set(r[r.rule.isin(["mesh_without_MONDO_NEEDS_REVIEW", "curator_confirmed_no_MONDO_term"])]
                  ["query"].str.split(" ").str[0])                       # reviewed: no MONDO term exists
    assert mesh <= mapped | flagged


def test_research_phase():
    assert sk.research_phase("PHASE2,PHASE3") == "clinical_trial_phase_2_to_3"
    assert sk.research_phase("PHASE3") == "clinical_trial_phase_3"
    assert sk.research_phase("NA") == sk.research_phase(None) == "not_provided"


def test_derive_clinical_trials_for():
    """Registry-confirmed drug x condition -> one derived edge per (drug, disease), MeSH replaced by MONDO."""
    from kg_schema import KG
    b = sk.Builder.__new__(sk.Builder)
    b.kg = kg = KG()
    kg.add_node("NCT:NCT00000001", "biolink:ClinicalTrial", phase="PHASE2,PHASE3")
    kg.add_node("NCT:NCT00000002", "biolink:ClinicalTrial", phase="PHASE3")
    kg.add_node("CHEMBL:CHEMBL1", "biolink:Drug")
    kg.add_node("CHEMBL:CHEMBL2", "biolink:Drug")
    kg.add_node("MESH:D000001", "biolink:Disease")
    kg.add_node("MONDO:0000001", "biolink:Disease")
    for nct in ("NCT00000001", "NCT00000002"):
        kg.add_edge(f"NCT:{nct}", "tmkg:tests_intervention", "CHEMBL:CHEMBL1", "ClinicalTrials.gov", nct, "registry",
                    "2026-10-01", in_registry_arms=True, chembl_match="exact_name_or_synonym")
        kg.add_edge(f"NCT:{nct}", "tmkg:studies_condition", "MESH:D000001", "ClinicalTrials.gov", nct, "registry",
                    "2026-10-02")
    kg.add_edge("NCT:NCT00000001", "tmkg:tests_intervention", "CHEMBL:CHEMBL2", "curated", "row", "curated",
                "2026-10-01", in_registry_arms=False)                 # not in the registry arms -> no derived edge
    kg.add_edge("MESH:D000001", "biolink:exact_match", "MONDO:0000001", "MeSH term name + MONDO", "x", "computed",
                "2026-09-01", match_rule="MeSH term = MONDO exact label/synonym")
    b.derive_clinical_trials_for()
    d = [e for e in kg.edges.values() if e["predicate"] == "biolink:in_clinical_trials_for"]
    assert len(d) == 1
    e = d[0]
    assert (e["subject"], e["object"]) == ("CHEMBL:CHEMBL1", "MONDO:0000001")
    assert e["nct_ids"] == '["NCT00000001", "NCT00000002"]'
    assert e["max_research_phase"] == "clinical_trial_phase_3"
    assert e["mapping_confidence"] == "medium"                     # weakest input: the MeSH -> MONDO name match
    assert len(json.loads(e["derived_from"])) == 5
    assert e["retrieved"] == "2026-10-02"


@needs_output
def test_kgx_export_uses_biolink_terms():
    n = pd.read_csv(sk.OUT / "kgx" / "nodes.tsv", sep="\t", low_memory=False)
    e = pd.read_csv(sk.OUT / "kgx" / "edges.tsv", sep="\t", low_memory=False)
    assert n.category.str.startswith("biolink:").all() and e.predicate.str.startswith("biolink:").all()
    assert set(n[n.tmkg_category == "tmkg:OutcomeMeasure"].category) == {"biolink:StudyResult"}
    assert e.original_predicate.dropna().str.startswith("tmkg:").all()
    assert e.mapping_confidence.notna().all() and e.primary_knowledge_source.notna().all()
    assert not e.aggregator_knowledge_source.dropna().str.startswith("[").any()      # pipe-separated lists


@needs_output
def test_neptune_nodes_carry_biolink_parent_label():
    n = pd.read_csv(sk.OUT / "neptune" / "opencypher" / "nodes.csv", low_memory=False)
    assert "OutcomeMeasure;StudyResult" in set(n[":LABEL"]) and "Drug" in set(n[":LABEL"])


@needs_output
def test_genetic_evidence_edges_carry_their_original_source():
    e = pd.read_csv(sk.OUT / "kg_edges.csv", low_memory=False)
    g = e[e.predicate == "biolink:gene_associated_with_condition"]
    assert len(g) > 0
    prim = dict(zip(g.ot_datasource, g.primary_knowledge_source))
    assert prim.get("eva") == "infores:clinvar" and prim.get("cancer_gene_census", "infores:cosmic") == "infores:cosmic"
    assert set(g.evidence_origin) <= {"germline", "somatic"}
    land = e[e.association_scope == "target_top_diseases"]
    assert (land.ot_score.astype(float) >= sk.MIN_SCORE).all()
    assert land.object.str.match(r"(MONDO|EFO):").all()


@needs_output
def test_trial_design_nodes():
    n = pd.read_csv(sk.OUT / "kg_nodes.csv", low_memory=False).set_index("id")
    e = pd.read_csv(sk.OUT / "kg_edges.csv", low_memory=False)
    trials = set(n[n.category == "biolink:ClinicalTrial"].index)
    assert set(e[e.predicate == "tmkg:has_study_population"].subject) == trials
    arms = e[e.predicate == "tmkg:has_arm"]
    assert set(arms.object.str.rsplit(":", n=1).str[0].str.replace("ARM:", "NCT:")) == set(arms.subject)
    tulip = n.loc[[i for i in n.index if i.startswith("ENDPOINT:NCT02446912:")]]
    assert (tulip.kind == "PRIMARY").sum() == 1
    link = e[(e.predicate == "tmkg:endpoint_result") & (e.subject == "ENDPOINT:NCT02446912:P1")]
    assert list(link.object) == ["OUTCOME:NCT02446912:P1"]
    rec = e[e.predicate == "tmkg:arm_receives"]
    assert rec.object.isin(set(e[e.predicate.isin(["tmkg:tests_intervention", "tmkg:has_comparator_drug",
                                                    "tmkg:has_background_drug"])].object)).all()


@needs_output
def test_atc_and_go_annotations():
    import json as _j
    n = pd.read_csv(sk.OUT / "kg_nodes.csv", low_memory=False).set_index("id")
    cis = n.loc["CHEMBL:CHEMBL11359"]
    assert cis.atc_codes == "L01XA01" and "Platinum compounds" in _j.loads(cis.atc)[0]["level4"]
    e = pd.read_csv(sk.OUT / "kg_edges.csv", low_memory=False)
    go = e[(e.predicate == "biolink:participates_in") & e.object.str.startswith("GO:")]
    assert len(go) > 0 and not go.go_evidence.str.contains("IEA").any()
    assert set(go.primary_knowledge_source) == {"infores:goa"}


@needs_output
def test_raw_items_fda_faers_registries():
    n = pd.read_csv(sk.OUT / "kg_nodes.csv", low_memory=False).set_index("id")
    e = pd.read_csv(sk.OUT / "kg_edges.csv", low_memory=False)
    app = e[e.predicate == "tmkg:has_regulatory_application"]
    assert "FDA:BLA761123" in set(app.object)                      # anifrolumab (Saphnelo)
    assert set(app.primary_knowledge_source) == {"tmkg:fda-drugsatfda"}
    fa = n[n.category == "tmkg:SpontaneousReportSummary"]
    assert len(fa) and fa.caveat.str.contains("not incidence").all()
    assert set(e[e.predicate == "tmkg:has_faers_summary"].primary_knowledge_source) == {"infores:faers"}
    assert n[n.category == "biolink:ClinicalTrial"].other_registry_ids.notna().all()
