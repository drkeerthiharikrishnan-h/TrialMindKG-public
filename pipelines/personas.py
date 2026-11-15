"""
Personas and plain wording for the web app (docs/USABILITY_PLAN.md, P1).

A persona is the user's job role, chosen by a click, never guessed (the pattern comes from the owner's FACET
repository: one YAML file per persona under personas/; adding a role = adding a file). The persona changes the
start tab, the example questions, the order of the entity card's sections and the wording level. The graph and
the endpoints are the same for everyone.

load_personas(folder)      -> list of persona dicts (sorted by file name), validated: every required key present
wording()                  -> the tables the page uses to turn graph terms into words: predicate -> verb phrase,
                              knowledge level -> plain words, category -> plain noun, evidence kinds with colours
evidence_kind(edge_row)    -> which kind of evidence an edge is ("human genetics", "curated", "text mining", ...)
confidence_word(verdict, confidence, predicted=False) -> "kept" / "pruned" / "predicted" / "unscored"
predicate_phrase(predicate) -> "is associated with" (falls back to the predicate's local name with spaces)
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PERSONAS_DIR = ROOT / "personas"
REQUIRED = ("name", "short", "emoji", "color", "description", "welcome", "start_tab", "wording", "show_ids",
            "card_sections", "questions")
TABS = ("pharos", "trials", "explore", "numbers", "build", "card")
SECTIONS = ("about", "known", "predicted", "trials")

# predicate (local part) -> verb phrase; anything missing falls back to the local name with spaces
PREDICATES = {
    "gene_associated_with_condition": "is associated with",
    "genetically_associated_with": "is genetically associated with",
    "directly_physically_interacts_with": "binds",
    "physically_interacts_with": "interacts with",
    "has_gene_product": "encodes",
    "actively_involved_in": "takes part in",
    "located_in": "is located in",
    "capable_of": "has the activity",
    "has_phenotype": "shows the phenotype",
    "treats_or_applied_or_studied_to_treat": "is used or studied to treat",
    "subclass_of": "is a kind of",
    "part_of": "is part of",
    "associated_with_response_to": "affects the response to",
    "has_participant": "involves",
    "related_to": "is related to",
}
# BioLink knowledge level -> plain words (the raw term stays available for the technical wording level)
KNOWLEDGE_LEVELS = {
    "knowledge_assertion": "curated statement",
    "statistical_association": "statistical association",
    "text_co_occurrence": "text mining only",
    "prediction": "predicted",
    "not_provided": "unspecified",
    "observation": "observation",
}
CATEGORIES = {
    "Gene": "gene", "Protein": "protein", "Disease": "disease", "SmallMolecule": "compound", "Drug": "drug",
    "ChemicalEntity": "chemical", "BiologicalProcess": "biological process", "MolecularActivity": "molecular activity",
    "CellularComponent": "cellular component", "Pathway": "pathway", "PhenotypicFeature": "phenotype",
    "DiseaseOrPhenotypicFeature": "disease or phenotype", "ClinicalTrial": "clinical trial",
    "ClinicalTrialProgram": "trial program", "Publication": "publication",
}
# evidence kinds, in the order the card shows them, with the colour the page uses
EVIDENCE_KINDS = [
    ("human genetics", "GWAS and genetic association (TIGA / GWAS Catalog)", "#0b7285"),
    ("curated", "a curated database statement (DisGeNET, CTD, Monarch, UniProt, DrugCentral, Open Targets)", "#1e7b4f"),
    ("clinical", "a trial or drug-indication record", "#d9480f"),
    ("binding or activity", "measured ligand activity or a protein interaction (ChEMBL, Pharos ligands, PPI)", "#6f42c1"),
    ("expression or statistics", "a statistical signal (Expression Atlas, IMPC phenotypes)", "#9a6700"),
    ("annotation", "an ontology or annotation link (Gene Ontology, pathways, hierarchies, gene to protein)", "#868e96"),
    ("text mining", "text co-occurrence only (JensenLab text mining, TIN-X); kept as context, never as proof", "#b42318"),
]
_BINDING = {"directly_physically_interacts_with", "physically_interacts_with"}
_ANNOTATION = {"actively_involved_in", "located_in", "capable_of", "subclass_of", "part_of", "has_gene_product",
               "has_participant"}
_CLINICAL = {"treats_or_applied_or_studied_to_treat", "associated_with_response_to"}


def _local(term):
    return str(term or "").split(":")[-1]


def predicate_phrase(predicate):
    """Takes a predicate ('biolink:gene_associated_with_condition'). Returns a verb phrase ('is associated with')."""
    loc = _local(predicate)
    return PREDICATES.get(loc, loc.replace("_", " "))


def evidence_kind(edge):
    """Takes an edge row (predicate, knowledge_level, source). Returns the evidence kind name (EVIDENCE_KINDS)."""
    pred, level = _local(edge.get("predicate")), str(edge.get("knowledge_level") or "")
    source = str(edge.get("source") or edge.get("primary_knowledge_source") or "").lower()
    if level == "text_co_occurrence" or "text mining" in source or "tin-x" in source:
        return "text mining"
    if pred == "genetically_associated_with" or "gwas" in source or "tiga" in source:
        return "human genetics"
    if pred in _BINDING:
        return "binding or activity"
    if pred in _CLINICAL or "indication" in source or "clinicaltrials" in source:
        return "clinical"
    if pred in _ANNOTATION:
        return "annotation"
    if level == "statistical_association":
        return "expression or statistics"
    return "curated"


def confidence_word(verdict=None, confidence=None, predicted=False):
    """One word for a row: 'predicted' (from the GNN), 'kept' / 'pruned' (pruning verdict) or 'unscored'."""
    if predicted:
        return "predicted"
    if verdict in ("keep", "prune"):
        return "kept" if verdict == "keep" else "pruned"
    return "unscored"


def wording():
    """The word tables the page fetches once (/api/wording), so Python and the page use the same words."""
    return dict(predicates=PREDICATES, knowledge_levels=KNOWLEDGE_LEVELS, categories=CATEGORIES,
                evidence_kinds=[dict(kind=k, meaning=m, color=c) for k, m, c in EVIDENCE_KINDS],
                confidence_words={"kept": "passed the pruning check", "pruned": "removed by the pruning check (kept as context)",
                                  "predicted": "a new link proposed by the link-prediction model, not in any source",
                                  "unscored": "no pruning verdict for this layer"})


def load_personas(folder=PERSONAS_DIR):
    """Reads personas/*.yaml. Returns a list of dicts (id = file stem) sorted by their `order` key (then file
    name); raises ValueError when a
    file misses a required key or names an unknown tab or card section, so a broken persona fails the tests."""
    out = []
    for path in sorted(Path(folder).glob("*.yaml")):          # file-name order, then the optional `order` key
        cfg = yaml.safe_load(path.read_text()) or {}
        missing = [k for k in REQUIRED if k not in cfg]
        if missing:
            raise ValueError(f"{path.name}: missing {missing}")
        if cfg["start_tab"] not in TABS or any(q.get("tab") not in TABS for q in cfg["questions"]):
            raise ValueError(f"{path.name}: unknown tab (allowed: {TABS})")
        if sorted(cfg["card_sections"]) != sorted(SECTIONS):
            raise ValueError(f"{path.name}: card_sections must be a permutation of {SECTIONS}")
        if cfg["show_ids"] not in ("always", "hover", "never") or cfg["wording"] not in ("plain", "mechanistic", "technical"):
            raise ValueError(f"{path.name}: show_ids must be always/hover/never, wording plain/mechanistic/technical")
        out.append(dict(id=path.stem, **cfg))
    return sorted(out, key=lambda p: (p.get("order", 99), p["id"]))
