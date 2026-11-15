"""
Graph schema, graph container and validation gate.

Everything that may appear in the graph is declared here:
  CATEGORIES  node type -> allowed id pattern                         (main graph, profile 'seed')
  PREDICATES  edge type -> (allowed subject types, allowed object types)
  ADC_*       the same for the TUB-040 ADC demo graph (profile 'adc')
  EDGE_FIELDS provenance fields every edge must have (CLAUDE.md rule 2)
  BIOLINK_PARENT  every tmkg: category/predicate -> the BioLink class/predicate it specialises (KGX export,
                  second Neptune/Neo4j label). biolink: terms map to themselves.

KG       - in-memory graph. Same input always gives the same ids and the same sorted output.
edge_id  - stable id of an edge, a hash of (subject, predicate, object, source, source_record).
biolink_annotate - sets biolink_category / biolink_predicate (+ original_predicate) on nodes and edges.
mapping_confidence - how sure we are that the entities of an edge were matched to the right identifiers.
validate - returns a list of problems; the build refuses to write a graph with any problem.

`biolink:` terms below were checked against biolink-model.yaml 4.4.6 (CLAUDE.md rule 6);
tests/test_kg_schema.py re-checks them whenever data/external/biolink/ is present.
"""
import hashlib, json, re

CATEGORIES = {
    "tmkg:ClinicalTrialProgram":     r"P\d{2}",
    "biolink:ClinicalTrial":         r"NCT:NCT\d{8}",
    "biolink:Drug":                  r"CHEMBL:CHEMBL\d+",
    "biolink:Protein":               r"CHEMBL:CHEMBL\d+",
    "biolink:MacromolecularComplex": r"CHEMBL:CHEMBL\d+",
    "biolink:ProteinFamily":         r"CHEMBL:CHEMBL\d+",
    "biolink:RNAProduct":            r"CHEMBL:CHEMBL\d+",
    "biolink:SmallMolecule":         r"CHEMBL:CHEMBL\d+",
    "biolink:Gene":                  r"ENSEMBL:ENSG\d{11}",
    "biolink:Disease":               r"(MESH:[CD]\d{6,9}|MONDO:\d{7}|EFO:\d{7})",
    "biolink:Pathway":               r"REACT:R-HSA-\d+",
    "biolink:BiologicalProcess":     r"GO:\d{7}",
    "biolink:PhenotypicFeature":     r"HP:\d{7}",
    "biolink:Publication":           r"(PMID:\d+|PMC:PMC\d+|PPR:PPR\d+)",
    "tmkg:OutcomeMeasure":           r"OUTCOME:NCT\d{8}:(P|S|O)\d+",
    "tmkg:DevelopmentEvent":         r"DEV:(post_failure|repurposing|data_access):\d+",
    "biolink:StudyPopulation":       r"POP:NCT\d{8}",                    # enrolled population of one trial
    "tmkg:StudyArm":                 r"ARM:NCT\d{8}:A\d+",                # registry arm group (biolink:Cohort)
    "tmkg:Endpoint":                 r"ENDPOINT:NCT\d{8}:(P|S|O)\d+",     # registered (protocol) outcome measure
    "tmkg:AdverseEventReport":       r"AEREPORT:NCT\d{8}:(SERIOUS|OTHER)", # posted adverse-event table of one trial
    "tmkg:Biomarker":                r"BIOMARKER:[A-Z0-9_]+",              # owner-confirmed (data/curated/biomarkers.csv)
    "tmkg:RegulatoryApplication":    r"FDA:(NDA|BLA)\d+",                  # Drugs@FDA application with its documents
    "tmkg:SpontaneousReportSummary": r"FAERS:CHEMBL\d+",                   # FAERS totals for one drug (not incidence)
    "tmkg:ProtocolFinding":          r"PROTOCOL:NCT\d{8}:[a-z_]+:\d+",     # owner-confirmed protocol / SAP fact
    "tmkg:PublicationFinding":       r"PUBFINDING:[A-Za-z0-9_]+:[a-z_]+:\d+",  # owner-confirmed result stated in a paper
    "tmkg:DocumentFinding":          r"DOCFINDING:P\d{2}:[a-z_]+:\d+",      # owner-confirmed fact from FDA / EMA / web docs
}

_TARGET = {"biolink:Protein", "biolink:MacromolecularComplex", "biolink:ProteinFamily", "biolink:RNAProduct",
           "biolink:SmallMolecule", "biolink:Gene"}
PREDICATES = {
    "tmkg:has_intervention":          ({"tmkg:ClinicalTrialProgram"}, {"biolink:Drug"}),
    "tmkg:has_trial":                 ({"tmkg:ClinicalTrialProgram"}, {"biolink:ClinicalTrial"}),
    "tmkg:has_source_publication":    ({"tmkg:ClinicalTrialProgram", "tmkg:DevelopmentEvent"}, {"biolink:Publication"}),
    "tmkg:has_post_failure_analysis": ({"tmkg:ClinicalTrialProgram"}, {"tmkg:DevelopmentEvent"}),
    "tmkg:has_repurposing_pivot":     ({"tmkg:ClinicalTrialProgram"}, {"tmkg:DevelopmentEvent"}),
    "tmkg:has_data_access_route":     ({"biolink:ClinicalTrial"}, {"tmkg:DevelopmentEvent"}),
    "tmkg:tests_intervention":        ({"biolink:ClinicalTrial"}, {"biolink:Drug"}),
    "tmkg:has_comparator_drug":       ({"biolink:ClinicalTrial"}, {"biolink:Drug"}),
    "tmkg:has_background_drug":       ({"biolink:ClinicalTrial"}, {"biolink:Drug"}),
    "tmkg:studies_condition":         ({"biolink:ClinicalTrial"}, {"biolink:Disease"}),
    "tmkg:has_publication":           ({"biolink:ClinicalTrial"}, {"biolink:Publication"}),
    "tmkg:has_outcome_measure":       ({"biolink:ClinicalTrial"}, {"tmkg:OutcomeMeasure"}),
    "biolink:interacts_with":         ({"biolink:Drug"}, _TARGET),
    "biolink:gene_product_of":        ({"biolink:Protein", "biolink:RNAProduct"}, {"biolink:Gene"}),
    "tmkg:has_component_gene":        ({"biolink:MacromolecularComplex"}, {"biolink:Gene"}),
    "tmkg:has_member_gene":           ({"biolink:ProteinFamily"}, {"biolink:Gene"}),
    "biolink:exact_match":            ({"biolink:Disease"}, {"biolink:Disease"}),
    "biolink:associated_with":        ({"biolink:Gene"}, {"biolink:Disease"}),
    "biolink:participates_in":        ({"biolink:Gene"}, {"biolink:Pathway", "biolink:BiologicalProcess"}),
    "biolink:gene_associated_with_condition": ({"biolink:Gene"}, {"biolink:Disease"}),   # Open Targets genetics
    "biolink:has_phenotype":          ({"biolink:Disease"}, {"biolink:PhenotypicFeature"}),   # HPO annotations
    "tmkg:has_study_population":      ({"biolink:ClinicalTrial"}, {"biolink:StudyPopulation"}),
    "tmkg:has_arm":                   ({"biolink:ClinicalTrial"}, {"tmkg:StudyArm"}),
    "tmkg:arm_receives":              ({"tmkg:StudyArm"}, {"biolink:Drug"}),
    "tmkg:has_registered_endpoint":   ({"biolink:ClinicalTrial"}, {"tmkg:Endpoint"}),
    "tmkg:endpoint_result":           ({"tmkg:Endpoint"}, {"tmkg:OutcomeMeasure"}),
    "tmkg:has_adverse_event_report":  ({"biolink:ClinicalTrial"}, {"tmkg:AdverseEventReport"}),
    "biolink:associated_with_response_to": ({"biolink:Gene"}, {"biolink:Drug"}),   # PharmGKB / ClinPGx
    "tmkg:analysed_in_subgroup":      ({"tmkg:OutcomeMeasure", "tmkg:Endpoint"}, {"tmkg:Biomarker"}),
    "tmkg:biomarker_gene":            ({"tmkg:Biomarker"}, {"biolink:Gene"}),
    "tmkg:has_regulatory_application": ({"biolink:Drug"}, {"tmkg:RegulatoryApplication"}),
    "tmkg:has_faers_summary":         ({"biolink:Drug"}, {"tmkg:SpontaneousReportSummary"}),
    "tmkg:has_protocol_finding":      ({"biolink:ClinicalTrial"}, {"tmkg:ProtocolFinding"}),
    "tmkg:reports_finding":           ({"biolink:Publication"}, {"tmkg:PublicationFinding"}),
    "tmkg:has_document_finding":      ({"tmkg:ClinicalTrialProgram"}, {"tmkg:DocumentFinding"}),
    # derived (seed_kg.derive_clinical_trials_for): registry-confirmed tested drug x studied condition
    "biolink:in_clinical_trials_for": ({"biolink:Drug"}, {"biolink:Disease"}),
}

# ---- second profile: the TUB-040 ADC demo graph (external evidence joined with an internal study)
ADC_CATEGORIES = {
    "biolink:Protein":            r"UniProtKB:[A-Z0-9]+",
    "biolink:Disease":            r"MONDO:\d{7}",
    "biolink:AnatomicalEntity":   r"UBERON:\d{7}",
    "biolink:Drug":               r"(CHEMBL:CHEMBL\d+|tmkg:[A-Za-z0-9_-]+)",
    "biolink:ClinicalTrial":      r"NCT:NCT\d{8}",
    "biolink:PhenotypicFeature":  r"AE:[A-Za-z0-9 /()-]+",
    "tmkg:LiteratureFinding":     r"LIT\d+",
    "tmkg:InternalStudy":         r"INT:[A-Z0-9-]+",
    "tmkg:BiomarkerStratum":      r"INT:TPS_[<>=0-9-]+",
    "tmkg:BiomarkerDefinition":   r"EXT:[A-Za-z0-9_]+",
}
ADC_PREDICATES = {
    "biolink:genetically_associated_with": ({"biolink:Protein"}, {"biolink:Disease"}),
    "biolink:expressed_in":       ({"biolink:Protein"}, {"biolink:AnatomicalEntity"}),
    "biolink:interacts_with":     ({"biolink:Drug"}, {"biolink:Protein"}),
    "tmkg:tests_intervention":    ({"biolink:ClinicalTrial", "tmkg:InternalStudy"}, {"biolink:Drug"}),
    "tmkg:reported_with":         ({"biolink:Drug"}, {"biolink:PhenotypicFeature"}),
    "tmkg:about":                 ({"tmkg:LiteratureFinding"}, {"biolink:Drug"}),
    "tmkg:reports_on":            ({"tmkg:LiteratureFinding"}, {"biolink:ClinicalTrial"}),
    "tmkg:evaluates":             ({"tmkg:LiteratureFinding"}, {"tmkg:BiomarkerDefinition"}),
    "tmkg:internal_data_for":     ({"tmkg:InternalStudy"}, {"biolink:ClinicalTrial"}),
    "tmkg:has_stratum":           ({"tmkg:InternalStudy"}, {"tmkg:BiomarkerStratum"}),
    "tmkg:same_definition_as":    ({"tmkg:BiomarkerStratum"}, {"tmkg:BiomarkerDefinition"}),
    "tmkg:observed":              ({"tmkg:InternalStudy"}, {"biolink:PhenotypicFeature"}),
}
# tmkg term -> BioLink parent (checked in biolink-model.yaml 4.4.6; tests/test_kg_schema.py re-checks).
# Only the closest honest parent: has_output / has_input / has_part / has_member / mentions where the BioLink
# definition fits, otherwise the root predicate biolink:related_to (never a stronger claim than the source makes).
BIOLINK_PARENT = {
    "tmkg:ClinicalTrialProgram":     "biolink:Study",
    "tmkg:OutcomeMeasure":           "biolink:StudyResult",
    "tmkg:DevelopmentEvent":         "biolink:InformationContentEntity",
    "tmkg:LiteratureFinding":        "biolink:InformationContentEntity",
    "tmkg:InternalStudy":            "biolink:Study",
    "tmkg:BiomarkerStratum":         "biolink:Cohort",
    "tmkg:BiomarkerDefinition":      "biolink:InformationContentEntity",
    "tmkg:StudyArm":                 "biolink:Cohort",
    "tmkg:Endpoint":                 "biolink:StudyVariable",
    "tmkg:AdverseEventReport":       "biolink:StudyResult",
    "tmkg:Biomarker":                "biolink:ClinicalAttribute",
    "tmkg:RegulatoryApplication":    "biolink:InformationContentEntity",
    "tmkg:SpontaneousReportSummary": "biolink:InformationContentEntity",
    "tmkg:has_regulatory_application": "biolink:related_to",
    "tmkg:has_faers_summary":        "biolink:related_to",
    "tmkg:ProtocolFinding":          "biolink:InformationContentEntity",
    "tmkg:has_protocol_finding":     "biolink:related_to",
    "tmkg:PublicationFinding":       "biolink:InformationContentEntity",
    "tmkg:reports_finding":          "biolink:related_to",
    "tmkg:DocumentFinding":          "biolink:InformationContentEntity",
    "tmkg:has_document_finding":     "biolink:related_to",
    "tmkg:analysed_in_subgroup":     "biolink:related_to",
    "tmkg:biomarker_gene":           "biolink:related_to",
    "tmkg:has_adverse_event_report": "biolink:related_to",
    "tmkg:has_arm":                  "biolink:has_part",
    "tmkg:has_study_population":     "biolink:related_to",
    "tmkg:arm_receives":             "biolink:related_to",
    "tmkg:has_registered_endpoint":  "biolink:related_to",
    "tmkg:endpoint_result":          "biolink:related_to",
    "tmkg:has_outcome_measure":      "biolink:has_output",
    "tmkg:has_publication":          "biolink:has_output",
    "tmkg:has_intervention":         "biolink:has_input",
    "tmkg:tests_intervention":       "biolink:has_input",
    "tmkg:has_comparator_drug":      "biolink:has_input",
    "tmkg:has_background_drug":      "biolink:has_input",
    "tmkg:has_component_gene":       "biolink:has_part",
    "tmkg:has_member_gene":          "biolink:has_member",
    "tmkg:has_stratum":              "biolink:has_part",
    "tmkg:about":                    "biolink:mentions",
    "tmkg:reports_on":               "biolink:mentions",
    "tmkg:evaluates":                "biolink:mentions",
    "tmkg:has_trial":                "biolink:related_to",
    "tmkg:studies_condition":        "biolink:related_to",
    "tmkg:has_source_publication":   "biolink:related_to",
    "tmkg:has_post_failure_analysis": "biolink:related_to",
    "tmkg:has_repurposing_pivot":    "biolink:related_to",
    "tmkg:has_data_access_route":    "biolink:related_to",
    "tmkg:reported_with":            "biolink:related_to",
    "tmkg:internal_data_for":        "biolink:related_to",
    "tmkg:same_definition_as":       "biolink:related_to",
    "tmkg:observed":                 "biolink:related_to",
}

PROFILES = {"seed": (CATEGORIES, PREDICATES), "adc": (ADC_CATEGORIES, ADC_PREDICATES)}

EDGE_FIELDS = ("id", "subject", "predicate", "object", "source", "source_record",
               "evidence_type", "retrieved", "primary_knowledge_source", "knowledge_level", "agent_type",
               "mapping_confidence")
# BioLink 4.4.6 enums (biolink-model.yaml: KnowledgeLevelEnum, AgentTypeEnum); filled by provenance.annotate()
KNOWLEDGE_LEVELS = {"knowledge_assertion", "logical_entailment", "prediction", "statistical_association",
                    "text_co_occurrence", "observation", "not_provided"}
AGENT_TYPES = {"manual_agent", "automated_agent", "data_analysis_pipeline", "computational_model", "text_mining_agent",
               "image_processing_agent", "manual_validation_of_automated_agent", "not_provided"}
MAPPING_CONFIDENCE = ("source_identifier", "high", "medium", "low")   # best first
EVIDENCE_TYPES = {"curated", "curator_confirmed", "registry", "database", "computed", "literature",
                  "internal", "synthetic"}    # synthetic = generated demo data, never sponsor data (rule 4)
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}Z)?$")


def local(term):
    """'biolink:Drug' -> 'Drug' (used as Neptune/Neo4j label)."""
    return term.split(":", 1)[1]


def biolink_term(term):
    """tmkg or biolink term -> BioLink term ('tmkg:has_trial' -> 'biolink:related_to'; biolink: unchanged)."""
    return term if term.startswith("biolink:") else BIOLINK_PARENT[term]


# match rules (match_rule / chembl_match on the edge) -> confidence that the entity identifiers are right
_RULE_CONFIDENCE = {"acronym_exact": "high", "secondary_id_exact": "high", "acronym_contains": "medium",
                    "title_word": "medium", "exact_name_or_synonym": "high", "curator_override": "high",
                    "unnamed_top_hit+curator_confirmed": "high", "unnamed_top_hit_NEEDS_REVIEW": "low"}


def mapping_confidence(e):
    """Edge dict -> one of MAPPING_CONFIDENCE.
    source_identifier  both ends are identifiers given by the source itself (NCT, ChEMBL, Ensembl, MeSH, PMID ...)
    high               curator confirmed, or exact registry id / acronym / ChEMBL name-or-synonym match
    medium             exact text match without an identifier (acronym inside a title, condition name = MONDO
                       label), or a trial-drug link stated only by the curated program table
    low                unconfirmed fallback (e.g. a ChEMBL record without any name)
    Derived edges carry the lowest confidence of the edges they were derived from (set by the deriver)."""
    if e.get("mapping_confidence") in MAPPING_CONFIDENCE:
        return e["mapping_confidence"]
    if e.get("evidence_type") == "curator_confirmed":
        return "high"
    found = [_RULE_CONFIDENCE[k] for r in (e.get("match_rule"), e.get("chembl_match")) if r
             for k in _RULE_CONFIDENCE if str(r) == k or str(r).startswith(k + ":") or str(r).startswith(k + "+")]
    if found:
        return max(found, key=MAPPING_CONFIDENCE.index)
    if e.get("evidence_type") == "computed" or e.get("in_registry_arms") is False:
        return "medium"
    return "source_identifier"


def weakest(confidences):
    """Lowest confidence in a list (used for derived edges)."""
    return max(confidences, key=MAPPING_CONFIDENCE.index)


def biolink_annotate(kg):
    """In place: node biolink_category, edge biolink_predicate + original_predicate (the tmkg term, when the
    graph's own predicate is not a BioLink term) + mapping_confidence. Returns kg."""
    for n in kg.nodes.values():
        n["biolink_category"] = biolink_term(n["category"])
    for e in kg.edges.values():
        e["biolink_predicate"] = biolink_term(e["predicate"])
        if not e["predicate"].startswith("biolink:"):
            e["original_predicate"] = e["predicate"]
        e["mapping_confidence"] = mapping_confidence(e)
    return kg


def edge_id(subject, predicate, obj, source, source_record):
    """Stable edge id: 'e' + 16 hex characters of sha256 over the defining fields."""
    s = "\x1f".join([subject, predicate, obj, source, str(source_record)])
    return "e" + hashlib.sha256(s.encode()).hexdigest()[:16]


def _clean(v):
    """Store lists/dicts as canonical JSON strings; drop None/empty values."""
    if isinstance(v, (list, dict, tuple)):
        return json.dumps(v, sort_keys=True, ensure_ascii=False) if v else None
    if isinstance(v, float) and v != v:       # NaN
        return None
    return v


class KG:
    """Nodes and edges keyed by id. Adding the same edge twice keeps one copy."""

    def __init__(self):
        self.nodes, self.edges = {}, {}

    def add_node(self, nid, category, **props):
        """First definition of a node wins; later calls may only fill in missing properties."""
        n = self.nodes.setdefault(nid, {"id": nid, "category": category})
        for k, v in props.items():
            v = _clean(v)
            if v is not None and n.get(k) is None:
                n[k] = v
        return nid

    def add_edge(self, subject, predicate, obj, source, source_record, evidence_type, retrieved, **props):
        eid = edge_id(subject, predicate, obj, source, source_record)
        if eid not in self.edges:
            e = dict(id=eid, subject=subject, predicate=predicate, object=obj, source=source,
                     source_record=str(source_record), evidence_type=evidence_type, retrieved=retrieved)
            e.update({k: c for k, v in props.items() if (c := _clean(v)) is not None})
            self.edges[eid] = e
        return eid

    def sorted_nodes(self):
        return [self.nodes[k] for k in sorted(self.nodes)]

    def sorted_edges(self):
        return sorted(self.edges.values(), key=lambda e: (e["subject"], e["predicate"], e["object"], e["id"]))


GLUED_ID = re.compile(r"^([A-Za-z]{2,})\d+(?:\1\d+)+$")


def glued_id(nid):
    """Takes a node id ('CHEMBL.COMPOUND:CHEMBL1026CHEMBL588'). True when its local part is two or more accessions of the
    same shape run together with no separator (CHEMBL1026CHEMBL588, ENSG00000184845ENSG00000012048): a list of ids that was
    joined instead of exploded. 'CHEMBL1026', 'NCT01739348:OTHER' and 'P35354' are not."""
    return bool(GLUED_ID.match(nid.split(":", 1)[-1]))


def validate(kg, profile="seed"):
    """Returns a list of human-readable problems (empty list = graph is valid).
    profile: 'seed' (main graph) or 'adc' (TUB-040 demo graph), see PROFILES."""
    CATEGORIES, PREDICATES = PROFILES[profile]
    problems = []
    for nid, n in kg.nodes.items():
        cat = n.get("category")
        if cat not in CATEGORIES:
            problems.append(f"node {nid}: unknown category {cat}")
        elif not re.fullmatch(CATEGORIES[cat], nid):
            problems.append(f"node {nid}: id does not match pattern for {cat}")
        if glued_id(nid):
            problems.append(f"node {nid}: several identifiers joined without a separator")
    labels = {}
    for t in list(CATEGORIES) + list(PREDICATES):
        labels.setdefault(local(t), set()).add(t)
    problems += [f"label clash: {sorted(v)}" for v in labels.values() if len(v) > 1]
    for eid, e in kg.edges.items():
        for f in EDGE_FIELDS:
            if e.get(f) in (None, ""):
                problems.append(f"edge {eid}: missing {f}")
        p = e.get("predicate")
        if p not in PREDICATES:
            problems.append(f"edge {eid}: unknown predicate {p}")
            continue
        s, o = kg.nodes.get(e["subject"]), kg.nodes.get(e["object"])
        if s is None or o is None:
            problems.append(f"edge {eid}: dangling ({e['subject']} -> {e['object']})")
            continue
        dom, rng = PREDICATES[p]
        if s["category"] not in dom or o["category"] not in rng:
            problems.append(f"edge {eid}: {p} not allowed {s['category']} -> {o['category']}")
        if e.get("evidence_type") not in EVIDENCE_TYPES:
            problems.append(f"edge {eid}: unknown evidence_type {e.get('evidence_type')}")
        if e.get("knowledge_level") and e["knowledge_level"] not in KNOWLEDGE_LEVELS:
            problems.append(f"edge {eid}: knowledge_level {e['knowledge_level']!r} is not a BioLink value")
        if e.get("agent_type") and e["agent_type"] not in AGENT_TYPES:
            problems.append(f"edge {eid}: agent_type {e['agent_type']!r} is not a BioLink value")
        if e.get("mapping_confidence") and e["mapping_confidence"] not in MAPPING_CONFIDENCE:
            problems.append(f"edge {eid}: mapping_confidence {e['mapping_confidence']!r} is not one of {MAPPING_CONFIDENCE}")
        if e.get("biolink_predicate") and e["biolink_predicate"] != biolink_term(p):
            problems.append(f"edge {eid}: biolink_predicate {e['biolink_predicate']} does not match {p}")
        if e.get("retrieved") and not DATE.match(str(e["retrieved"])):
            problems.append(f"edge {eid}: retrieved {e['retrieved']!r} is not an ISO date")
        if edge_id(e["subject"], p, e["object"], e["source"], e["source_record"]) != eid:
            problems.append(f"edge {eid}: id does not match its content")
    return problems
