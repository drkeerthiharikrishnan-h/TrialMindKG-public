"""
TrialMindKG - seed knowledge graph builder.

    python pipelines/seed_kg.py                         # build (uses stored API responses, fetches missing ones)
    TMKG_SNAPSHOT_MODE=offline python pipelines/seed_kg.py   # rebuild from stored responses only
    TMKG_SNAPSHOT_MODE=refresh python pipelines/seed_kg.py   # fetch everything again (new data version)
    TMKG_SNAPSHOT_PRUNE=1 python pipelines/seed_kg.py        # also delete stored responses no longer used

Input : data/curated/failed_clinical_trials_2016_2026.md (34 programs) + curated override files
Steps : 1) parse the failure inventory table
        2) drugs: name -> ChEMBL molecule -> mechanism -> target; Open Targets fallback; else NEEDS_REVIEW
        3) trials: acronym -> ClinicalTrials.gov NCT (strict rules, curator overrides), then the full record:
           status/why stopped/IPD statement, conditions (MeSH, exact MONDO match), outcome measures and
           posted results, linked publications, comparator and background drugs (from arm types)
        4) targets -> genes (Open Targets id mapping) -> human pathways (Reactome)
        5) gene <-> trial indication association scores (Open Targets)
        6) publication metadata (Europe PMC) for registry-linked and curated-source PMIDs
        7) validate against kg_schema; export CSV, GraphML, Neptune bulk-load files, run manifest
Every API response comes from pipelines/snapshot.py, so the same snapshot + code = identical output.
Outputs (outputs/): kg_nodes.csv, kg_edges.csv, seed_kg.graphml, resolution_report.csv (review queue),
        neptune/, load_neo4j.cypher, run_manifest.json
"""
import os, re, json, datetime, hashlib
from pathlib import Path
import pandas as pd

from snapshot import Snapshot
import kg_schema
from kg_schema import KG, validate
import opentargets, ctgov, literature, reactome, mondo_ref, curated_tables, provenance

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"; OUT.mkdir(exist_ok=True)
DATASET = ROOT / "data" / "curated" / "failed_clinical_trials_2016_2026.md"
OVERRIDES_FILE = ROOT / "data" / "curated" / "curated_overrides.csv"
CONDITION_OVERRIDES_FILE = ROOT / "data" / "curated" / "condition_overrides.csv"
DRUG_OVERRIDES_FILE = ROOT / "data" / "curated" / "drug_overrides.csv"

CT = "https://clinicaltrials.gov/api/v2/studies"
CHEMBL = "https://www.ebi.ac.uk/chembl/api/data"
NOW = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")   # ADC demo headers only
SNAP = Snapshot()
_SOURCES = {"clinicaltrials.gov": "ctgov", "www.ebi.ac.uk/chembl": "chembl", "api.fda.gov": "openfda",
            "gtexportal.org": "gtex", "www.ebi.ac.uk/europepmc": "europepmc"}

# Curated aliases: development codes -> INN components (documented brand compositions)
ALIASES = {
    "amx0035": ["sodium phenylbutyrate", "ursodoxicoltaurine"],   # Relyvrio = PB + taurursodiol
}


# ---------------------------------------------------------------- helpers
def source_of(url):
    """Snapshot folder for a URL, e.g. 'ctgov'."""
    return next((v for k, v in _SOURCES.items() if k in url), "other")


def fetch(url, params=None, snap=None):
    """GET through the snapshot. Returns snapshot.Response (body, fetched_at, key)."""
    return (snap or SNAP).get(source_of(url), url, params)


def get(url, params=None):
    """GET through the snapshot; returns only the JSON body (used by run_adc_demo.py)."""
    return fetch(url, params).body


def norm(s):
    s = (s or "").lower().replace("+", "plus")      # evoke+ == "EVOKE Plus"
    return re.sub(r"[^a-z0-9]", "", s)

ROMAN = {"i", "ii", "iii", "iv", "v"}

def expand_trial_tokens(tokens):
    """'GRADUATE I / II' -> ['GRADUATE I', 'GRADUATE II'] (bare numeral inherits the prefix)."""
    out = []
    for t in tokens:
        if t.lower() in ROMAN and out:
            prefix = out[-1].rsplit(" ", 1)[0]
            out.append(f"{prefix} {t}")
        else:
            out.append(t)
    return out


MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"], 1)}

def curated_as_of(md):
    """'Evidence review as of 8 September 2026' -> '2026-09-08' (retrieved date of curated edges).
    Uses an explicit English month list, so the result does not depend on the computer's language."""
    d, mon, y = re.search(r"as of (\d{1,2}) (\w+) (\d{4})", md).groups()
    return f"{y}-{MONTHS[mon.lower()]:02d}-{int(d):02d}"


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_overrides():
    """(program_id, trial_query) -> dict(nct, justification, confidence) from curated_overrides.csv."""
    if not OVERRIDES_FILE.exists():
        return {}
    return {(r.program_id, r.trial_query): dict(nct=r.nct, justification=r.justification, confidence=r.confidence)
            for r in pd.read_csv(OVERRIDES_FILE).itertuples()}


def load_condition_overrides():
    """(nct, condition) -> dict(mondo_id, justification) from condition_overrides.csv.
    condition = the registry's free-text condition, or a MeSH id (e.g. MESH:D050171) with nct '*'
    to map that MeSH term for every trial. mondo_id 'NONE' = the curator decided there is no MONDO term."""
    if not CONDITION_OVERRIDES_FILE.exists():
        return {}
    return {(r.nct, r.condition): dict(mondo_id=r.mondo_id, justification=r.justification)
            for r in pd.read_csv(CONDITION_OVERRIDES_FILE).itertuples()}


def load_drug_overrides():
    """norm(registry name) -> dict(chembl_id, justification) from drug_overrides.csv (curator-confirmed
    resolver proposals; see pipelines/resolver.py)."""
    if not DRUG_OVERRIDES_FILE.exists():
        return {}
    return {norm(r.registry_name): dict(chembl_id=r.chembl_id, justification=r.justification)
            for r in pd.read_csv(DRUG_OVERRIDES_FILE).itertuples()}


def confirmed_registry_names(drug, snap=None):
    """Registry names a curator confirmed for this drug's ChEMBL molecule (drug_overrides.csv), e.g. TQJ230 for
    pelacarsen - sponsor codes ChEMBL does not list. [] if none or the drug does not resolve."""
    if not DRUG_OVERRIDES_FILE.exists():
        return []
    r = resolve_drug(drug, snap=snap)
    if not r:
        return []
    rows = pd.read_csv(DRUG_OVERRIDES_FILE)
    return sorted(str(x.registry_name) for x in rows.itertuples() if str(x.chembl_id).split(":")[-1] == r["chembl_id"])


def override_drug(name, snap=None, known=None):
    """Curator-confirmed ChEMBL record for a registry name, else None. chembl_id 'NONE' in the file = the curator
    decided the name is a drug class, not one molecule -> dict with chembl_id None (left unmapped on purpose)."""
    ov = load_drug_overrides().get(norm(name))
    if not ov:
        return None
    if str(ov["chembl_id"]).upper() == "NONE":
        return dict(chembl_id=None, match="curator_not_a_single_drug", justification=ov["justification"])
    node = (known or {}).get(f"CHEMBL:{ov['chembl_id']}")
    if node:                                     # already in the graph from a frozen ChEMBL response: reuse it
        parent = (node.get("chembl_parent") or "").split(":")[-1] or None
        return dict(chembl_id=ov["chembl_id"], pref_name=node.get("name") or name,
                    molecule_type=node.get("molecule_type"), max_phase=node.get("max_phase"), parent_chembl_id=parent,
                    match="curator_override", justification=ov["justification"], fetched_at=None,
                    key=node.get("chembl_snapshot_key"))
    r = fetch(f"{CHEMBL}/molecule/{ov['chembl_id']}.json", None, snap)
    m = r.body or {}
    return dict(chembl_id=ov["chembl_id"], pref_name=m.get("pref_name") or name, molecule_type=m.get("molecule_type"),
                max_phase=m.get("max_phase"), parent_chembl_id=(m.get("molecule_hierarchy") or {}).get("parent_chembl_id"),
                match="curator_override", justification=ov["justification"], fetched_at=r.fetched_at, key=r.key)


# ---------------------------------------------------------------- 1. parse
def parse_inventory(md):
    s, e = md.find("## Failure inventory"), md.find("## Trial-specific data-access evidence")
    rows = [l for l in md[s:e].splitlines() if l.startswith("|")][2:]
    progs = []
    for l in rows:
        c = [x.strip() for x in l.strip().strip("|").split("|")]
        m = re.match(r"\[(.+?)\]\((.+?)\)", c[2])
        label, src = (m.group(1), m.group(2)) if m else (c[2], "")
        drug_part, _, trial_part = label.partition(" — ")
        drugs = [d.strip() for d in re.split(r"\s*\+\s*|\s*±\s*|/", drug_part) if d.strip()]
        trials = expand_trial_tokens([t.strip() for t in re.split(r"\s*/\s*", trial_part) if t.strip()])
        progs.append(dict(program_id=f"P{int(c[0]):02d}", year=c[1], label=label, source_url=src,
                          drugs=drugs, trials=trials, sponsor=c[3], indication=c[4],
                          what_failed=c[5], data_access=c[6] if len(c) > 6 else ""))
    return progs


# ---------------------------------------------------------------- 2. trials
CT_FIELDS = ("NCTId,Acronym,BriefTitle,OfficialTitle,SecondaryId,OverallStatus,Phase,Condition,"
             "ConditionMeshTerm,ConditionMeshId,InterventionName,EnrollmentCount,HasResults,StartDate")
EUDRACT = re.compile(r"^\d{4}-\d{6}-\d{2}$")
RULE_ORDER = {"acronym_exact": 0, "secondary_id_exact": 1, "acronym_contains": 2, "title_word": 3}

# Trial registry source: aact (default since 2026-10-05: AACT flat files, 45/45 trial records and 34/34 programs
# identical to the API in pipelines/aact.py compare/freeze) | ctgov (ClinicalTrials.gov API v2, kept as fallback)
TRIAL_SOURCE = os.environ.get("TMKG_TRIAL_SOURCE", "aact")
AACT_SRC = None                                                  # shared aact.Source (keeps its read-once caches)


def _aact_source(snap):
    global AACT_SRC
    import aact
    if AACT_SRC is None or AACT_SRC.snap is not snap:
        AACT_SRC = aact.Source(snap)
    return AACT_SRC


def chembl_synonyms(name, snap=None):
    """All names ChEMBL gives the molecule that `name` matches exactly (pref name + synonyms, e.g. pelacarsen ->
    TQJ230, AKCEA-APO(a)-LRx), from the same frozen search resolve_drug uses; [name] if no exact match."""
    r = fetch(f"{CHEMBL}/molecule/search.json", {"q": name, "limit": 10}, snap)
    q = norm(name)
    for m in (r.body or {}).get("molecules", []):
        names = [m.get("pref_name") or ""] + [x.get("molecule_synonym", "") for x in m.get("molecule_synonyms", [])]
        if q and any(norm(n) == q for n in names):
            return sorted({n for n in names + [name] if n})
    return [name]


def trial_search(acronym, drug, snap=None):
    """Registry search for one trial name + drug -> Response with body {"studies": [...]}.
    AACT has no search engine: interventions are matched on the drug's ChEMBL names (explicit synonym expansion)."""
    if TRIAL_SOURCE == "aact":
        import aact
        names = ([drug] + ALIASES[norm(drug)] if norm(drug) in ALIASES      # curated combination, e.g. AMX0035
                 else chembl_synonyms(drug, snap) + confirmed_registry_names(drug, snap))
        return aact.search_studies(_aact_source(snap or SNAP), drug, names)
    return fetch(CT, {"query.intr": drug, "query.term": acronym, "pageSize": 50, "fields": CT_FIELDS}, snap)


def trial_record(nct, snap=None):
    """Full registry record of one trial -> Response with the CT.gov API v2 study as body."""
    if TRIAL_SOURCE == "aact":
        import aact
        from snapshot import Response
        snap = snap or SNAP
        body, resp = aact.study(_aact_source(snap), nct, mesh=lambda t: aact.mesh_id(snap, t))
        return Response(body, resp.fetched_at, resp.key, resp.source)
    return fetch(f"{CT}/{nct}", None, snap)


def resolve_trial(acronym, drug, snap=None):
    r = trial_search(acronym, drug, snap)
    hits = []
    for st in (r.body or {}).get("studies", []):
        p = st.get("protocolSection", {})
        idm = p.get("identificationModule", {})
        # 'or ""': a source may give an empty field as null (AACT) instead of leaving it out (API)
        acr, bt, ot = idm.get("acronym") or "", idm.get("briefTitle") or "", idm.get("officialTitle") or ""
        sec = [s.get("id") or "" for s in idm.get("secondaryIdInfos") or []]
        q = norm(acronym)
        rule = None
        if q and q == norm(acr):
            rule = "acronym_exact"
        elif q and any(q == norm(s) for s in sec):
            rule = "secondary_id_exact"
        elif q and len(q) >= 4 and q in norm(acr):
            rule = "acronym_contains"
        elif re.search(r"\b" + re.escape(acronym.lower()) + r"\b", (bt + " " + ot).lower()):
            rule = "title_word"
        if not rule:
            continue
        hits.append(dict(nct=idm.get("nctId"), acronym=acr, title=bt, rule=rule,
                         enrollment=p.get("designModule", {}).get("enrollmentInfo", {}).get("count"),
                         eudract=[s for s in sec if EUDRACT.match(s)],
                         fetched_at=r.fetched_at, key=r.key))
    hits.sort(key=lambda h: (RULE_ORDER[h["rule"]], h["nct"]))
    best = [h for h in hits if h["rule"] == hits[0]["rule"]] if hits else []
    # collapse sub-studies: records sharing a EudraCT number -> keep the largest enrolment as parent
    groups = {}
    for h in best:
        groups.setdefault(h["eudract"][0] if h["eudract"] else h["nct"], []).append(h)
    collapsed = []
    for g in groups.values():
        g.sort(key=lambda h: (-(h["enrollment"] or 0), h["nct"]))
        g[0]["substudies"] = [x["nct"] for x in g[1:]]
        collapsed.append(g[0])
    return sorted(collapsed, key=lambda h: h["nct"])


# ---------------------------------------------------------------- 3. drugs
# Open Targets: other indications of a target = its top TOP_DISEASES diseases with overall score >= MIN_SCORE
TOP_DISEASES, MIN_SCORE = 10, 0.5
GENETIC_SOURCE = {"eva": "ClinVar (EVA)", "eva_somatic": "ClinVar somatic (EVA)", "clingen": "ClinGen",
                  "gene2phenotype": "Gene2Phenotype", "orphanet": "Orphanet", "uniprot_variants": "UniProt variants",
                  "uniprot_literature": "UniProt literature", "gene_burden": "gene burden",
                  "cancer_gene_census": "COSMIC Cancer Gene Census", "intogen": "IntOGen"}

# BioLink ResearchPhaseEnum, lowest to highest (biolink-model.yaml 4.4.6)
RESEARCH_PHASES = ["not_provided", "pre_clinical_research_phase", "clinical_trial_phase", "clinical_trial_phase_1",
                   "clinical_trial_phase_1_to_2", "clinical_trial_phase_2", "clinical_trial_phase_2_to_3",
                   "clinical_trial_phase_3", "clinical_trial_phase_4"]
_PHASE = {"EARLY_PHASE1": "clinical_trial_phase", "PHASE1": "clinical_trial_phase_1",
          "PHASE1,PHASE2": "clinical_trial_phase_1_to_2", "PHASE2": "clinical_trial_phase_2",
          "PHASE2,PHASE3": "clinical_trial_phase_2_to_3", "PHASE3": "clinical_trial_phase_3",
          "PHASE4": "clinical_trial_phase_4"}


def research_phase(registry_phase):
    """Registry phase text ('PHASE2,PHASE3') -> BioLink ResearchPhaseEnum value; unknown/NA -> 'not_provided'."""
    return _PHASE.get((registry_phase or "").replace(" ", "").upper(), "not_provided")


def resolve_drug(name, allow_unnamed=True, snap=None):
    r = fetch(f"{CHEMBL}/molecule/search.json", {"q": name, "limit": 10}, snap)
    mols = (r.body or {}).get("molecules", [])
    q = norm(name)
    base = dict(fetched_at=r.fetched_at, key=r.key)
    for m in mols:
        names = [m.get("pref_name") or ""] + [s.get("molecule_synonym", "") for s in m.get("molecule_synonyms", [])]
        if q and any(norm(n) == q for n in names):
            return dict(chembl_id=m["molecule_chembl_id"], pref_name=m.get("pref_name"),
                        molecule_type=m.get("molecule_type"), max_phase=m.get("max_phase"),
                        parent_chembl_id=(m.get("molecule_hierarchy") or {}).get("parent_chembl_id"),
                        match="exact_name_or_synonym", **base)
    # fallback: ChEMBL full-text search returned a single record that carries no name at all
    # (e.g. newly registered compounds). Accept, but flag for curator review.
    if allow_unnamed and mols and not mols[0].get("pref_name") and not mols[0].get("molecule_synonyms"):
        m = mols[0]
        return dict(chembl_id=m["molecule_chembl_id"], pref_name=name, molecule_type=m.get("molecule_type"),
                    max_phase=m.get("max_phase"), match="unnamed_top_hit_NEEDS_REVIEW", **base)
    return None


def mechanisms(chembl_id, snap=None):
    r = fetch(f"{CHEMBL}/mechanism.json", {"molecule_chembl_id": chembl_id, "limit": 50}, snap)
    out = []
    for m in (r.body or {}).get("mechanisms", []):
        if not m.get("target_chembl_id"):
            continue
        t = fetch(f"{CHEMBL}/target/{m['target_chembl_id']}.json", None, snap).body or {}
        comps = t.get("target_components", [])
        out.append(dict(target_chembl_id=m["target_chembl_id"], action_type=m.get("action_type"),
                        moa=m.get("mechanism_of_action"), mec_id=m.get("mec_id"),
                        target_name=t.get("pref_name"), target_type=t.get("target_type"),
                        organism=t.get("organism"),
                        uniprot=sorted(c.get("accession") for c in comps if c.get("accession")),
                        refs=[x.get("ref_url") for x in m.get("mechanism_refs", [])][:3],
                        fetched_at=r.fetched_at, key=r.key))
    return sorted(out, key=lambda m: (m["target_chembl_id"], m["mec_id"] or 0))


# ChEMBL target_type -> graph category (biolink classes)
TARGET_CATEGORY = {"SINGLE PROTEIN": "biolink:Protein", "PROTEIN COMPLEX": "biolink:MacromolecularComplex",
                   "PROTEIN COMPLEX GROUP": "biolink:MacromolecularComplex", "PROTEIN FAMILY": "biolink:ProteinFamily",
                   "NUCLEIC-ACID": "biolink:RNAProduct", "SMALL MOLECULE": "biolink:SmallMolecule"}
GENE_LINK = {"biolink:Protein": "biolink:gene_product_of", "biolink:RNAProduct": "biolink:gene_product_of",
             "biolink:MacromolecularComplex": "tmkg:has_component_gene", "biolink:ProteinFamily": "tmkg:has_member_gene"}


# ---------------------------------------------------------------- 4. assemble
class Builder:
    """Holds the graph, the review report and the source versions while building."""

    def __init__(self, snap, md):
        self.snap, self.kg, self.report = snap, KG(), []
        self.md_name = f"curated:{DATASET.name}"
        self.curated_date = curated_as_of(md)
        self.mondo = mondo_ref.load()
        self.mondo_date = re.search(r"releases/(\d{4}-\d{2}-\d{2})", self.mondo["version"]).group(1)
        self.ot_release = opentargets.release(snap)
        chembl = fetch(f"{CHEMBL}/status.json", None, snap)
        self.chembl_version = chembl.body.get("chembl_db_version")
        self.pmids = set()                                  # publications to fetch metadata for
        self.program_drugs, self.program_trials = {}, {}
        self.trial_query_ncts = {}                          # (program, trial name) -> [NCT ids]
        self.intervention_drugs = {}                        # (nct, registry intervention name) -> {drug id: match}

    def flag(self, program, entity_type, query, resolved, identifier="", rule="", **extra):
        self.report.append(dict(program=program, entity_type=entity_type, query=query, resolved=bool(resolved),
                                identifier=identifier, rule=rule, **extra))

    # -- drugs and mechanisms
    def add_drug(self, r):
        did = f"CHEMBL:{r['chembl_id']}"
        self.kg.add_node(did, "biolink:Drug", name=r["pref_name"], molecule_type=r["molecule_type"],
                         max_phase=r["max_phase"], chembl_match=r["match"], chembl_snapshot_key=r["key"],
                         chembl_parent=f"CHEMBL:{r['parent_chembl_id']}" if r.get("parent_chembl_id") else None)
        for mm in mechanisms(r["chembl_id"], self.snap):
            tid = f"CHEMBL:{mm['target_chembl_id']}"
            cat = TARGET_CATEGORY.get(mm["target_type"])
            if cat is None:
                self.flag("", "target", tid, False, "", "unknown_ChEMBL_target_type_NEEDS_REVIEW",
                          detail=f"{mm['target_type']} ({mm['target_name']})")
                continue
            self.kg.add_node(tid, cat, name=mm["target_name"], target_type=mm["target_type"],
                             organism=mm["organism"], uniprot=mm["uniprot"])
            self.kg.add_edge(did, "biolink:interacts_with", tid, "ChEMBL", f"ChEMBL mechanism {mm['mec_id']}",
                             "curated", mm["fetched_at"], snapshot_key=mm["key"], moa=mm["moa"],
                             chembl_action_type=mm["action_type"], references=mm["refs"])
        return did

    def add_open_targets_mechanisms(self, pid, query_name, chembl_id, did):
        """Fallback when ChEMBL has no mechanism (see opentargets.py). No mechanism anywhere -> NEEDS_REVIEW."""
        r, moa = opentargets.drug_mechanisms(self.snap, chembl_id)
        self.flag(pid, "mechanism", query_name, moa, ";".join(m["symbol"] or m["ensembl"] for m in moa),
                  "open_targets_fallback" if moa else "no_mechanism_in_ChEMBL_or_OpenTargets_NEEDS_REVIEW")
        for m in moa:
            gid = f"ENSEMBL:{m['ensembl']}"
            self.kg.add_node(gid, "biolink:Gene", name=m["symbol"], target_name=m["target_name"], uniprot=m["uniprot"])
            self.kg.add_edge(did, "biolink:interacts_with", gid, "Open Targets Platform",
                             f"Open Targets {self.ot_release} drug {chembl_id} mechanismsOfAction", "curated",
                             r.fetched_at, snapshot_key=r.key, moa=m["moa"], chembl_action_type=m["action_type"],
                             references=[f"PMID:{p}" for p in m["pmids"]])

    # -- programs
    def add_program(self, p):
        pid = p["program_id"]
        self.kg.add_node(pid, "tmkg:ClinicalTrialProgram", name=p["label"], year=p["year"], sponsor=p["sponsor"],
                         indication=p["indication"], what_failed=p["what_failed"], data_access=p["data_access"],
                         source_url=p["source_url"])
        drug_ids = []
        for d in p["drugs"]:
            for dn in ALIASES.get(norm(d), [d]):
                r = resolve_drug(dn, snap=self.snap)
                ov = load_drug_overrides().get(norm(dn))
                if r and r["match"].startswith("unnamed") and ov and ov["chembl_id"] == r["chembl_id"]:
                    r["match"] = "unnamed_top_hit+curator_confirmed"   # ChEMBL record has no name; curator confirmed
                self.flag(pid, "drug", dn, r, r["chembl_id"] if r else "",
                          ((r or {}).get("match", "")) + ("+curated_alias" if dn != d else ""))
                if not r:
                    continue
                did = self.add_drug(r)
                if not any(e["subject"] == did and e["predicate"] == "biolink:interacts_with"
                           for e in self.kg.edges.values()):
                    self.add_open_targets_mechanisms(pid, dn, r["chembl_id"], did)
                self.kg.add_edge(pid, "tmkg:has_intervention", did, self.md_name, f"inventory row {pid}",
                                 "curated", self.curated_date, chembl_match=r["match"])
                drug_ids.append(did)
        self.program_drugs[pid] = drug_ids
        self.program_trials[pid] = []
        overrides = load_overrides()
        for t in p["trials"]:
            hits = resolve_trial(t, p["drugs"][0], self.snap)
            if not hits and len(p["drugs"]) > 1:
                hits = resolve_trial(t, p["drugs"][1], self.snap)
            ov = overrides.get((pid, t))
            if not hits and ov:                      # human-in-the-loop: curator-confirmed mapping
                hits = [dict(nct=ov["nct"], acronym=t, rule=f"curator_override:{ov['confidence']}",
                             justification=ov["justification"], substudies=[])]
            self.flag(pid, "trial", t, hits, ";".join(h["nct"] for h in hits),
                      hits[0]["rule"] if hits else "", n_candidates=len(hits))
            self.trial_query_ncts[(pid, t)] = [h["nct"] for h in hits]
            for h in hits:
                self.add_trial(pid, h, drug_ids)
        for pmid in self.source_pmids(p):
            self.kg.add_node(f"PMID:{pmid}", "biolink:Publication")
            self.kg.add_edge(pid, "tmkg:has_source_publication", f"PMID:{pmid}", self.md_name,
                             f"inventory row {pid}", "curated", self.curated_date, url=p["source_url"])
            self.pmids.add(pmid)

    def source_pmids(self, p):
        pmids = set(literature.pmids_from_text(p["source_url"]))
        pmids |= set(literature.pmcid_to_pmid(self.snap, literature.pmcids_from_text(p["source_url"])).values())
        return sorted(pmids, key=int)

    # -- trials (full registry record)
    def add_trial(self, pid, h, drug_ids):
        nct = h["nct"]
        rec = trial_record(nct, self.snap)
        s, f = rec.body or {}, ctgov.study_facts(rec.body)
        tid = f"NCT:{nct}"
        idm = s.get("protocolSection", {}).get("identificationModule", {})
        self.kg.add_node(tid, "biolink:ClinicalTrial", name=idm.get("briefTitle"), acronym=idm.get("acronym"),
                         **{k: v for k, v in f.items() if k != "conditions"}, conditions_text=f["conditions"],
                         registry_snapshot_key=rec.key, **ctgov.adverse_event_summary(s))
        self.program_trials[pid].append(tid)
        override = h["rule"].startswith("curator")
        self.kg.add_edge(pid, "tmkg:has_trial", tid, "ClinicalTrials.gov", nct,
                         "curator_confirmed" if override else "registry",
                         rec.fetched_at if override else h["fetched_at"],
                         snapshot_key=rec.key if override else h["key"], match_rule=h["rule"],
                         justification=h.get("justification"), substudies=h.get("substudies"))
        in_registry = self.add_arm_drugs(pid, tid, nct, s, rec, drug_ids)
        for did in drug_ids:
            if did in in_registry:              # drug named in this trial's registry arms
                self.kg.add_edge(tid, "tmkg:tests_intervention", did, "ClinicalTrials.gov", nct, "registry",
                                 rec.fetched_at, snapshot_key=rec.key, in_registry_arms=True,
                                 chembl_match=in_registry[did])
            else:                               # only the curated program links them
                self.kg.add_edge(tid, "tmkg:tests_intervention", did, self.md_name, f"inventory row {pid} | {nct}",
                                 "curated", self.curated_date, in_registry_arms=False)
        self.add_conditions(pid, tid, nct, s, f, rec)
        self.add_outcomes(tid, nct, s, rec)
        self.add_design(tid, nct, s, rec)                   # after outcomes: endpoints link to posted results
        for ref in ctgov.references(s):
            if ref["pmid"]:
                self.kg.add_node(f"PMID:{ref['pmid']}", "biolink:Publication")
                self.kg.add_edge(tid, "tmkg:has_publication", f"PMID:{ref['pmid']}", "ClinicalTrials.gov", nct,
                                 "registry", rec.fetched_at, snapshot_key=rec.key, reference_type=ref["type"])
                self.pmids.add(ref["pmid"])

    def add_conditions(self, pid, tid, nct, s, f, rec):
        meshes = s.get("derivedSection", {}).get("conditionBrowseModule", {}).get("meshes", [])
        for m in sorted(meshes, key=lambda m: m.get("id")):
            cid = f"MESH:{m['id']}"
            self.kg.add_node(cid, "biolink:Disease", name=m.get("term"))
            self.kg.add_edge(tid, "tmkg:studies_condition", cid, "ClinicalTrials.gov", nct, "registry",
                             rec.fetched_at, snapshot_key=rec.key)
            self.map_mesh(pid, cid, m.get("term"))
        if meshes:
            return
        overrides = load_condition_overrides()
        for text in f["conditions"]:
            exact = self.mondo["name_index"].get(mondo_ref.norm_name(text), [])
            ov = overrides.get((nct, text))
            if len(exact) == 1:
                self.add_mondo(exact[0])
                self.kg.add_edge(tid, "tmkg:studies_condition", exact[0], "ClinicalTrials.gov + MONDO", nct,
                                 "computed", rec.fetched_at, snapshot_key=rec.key, condition_text=text,
                                 match_rule=f"exact name/synonym in {self.mondo['version']}")
                self.flag(pid, "condition", text, True, exact[0], "mondo_exact_name")
            elif ov and str(ov["mondo_id"]).upper() == "NONE":
                self.flag(pid, "condition", text, True, "", "curator_confirmed_no_MONDO_term",
                          detail=f"{nct}; {ov['justification']}")
            elif ov:
                self.add_mondo(ov["mondo_id"])
                self.kg.add_edge(tid, "tmkg:studies_condition", ov["mondo_id"], "curated:condition_overrides.csv",
                                 f"{nct} | {text}", "curator_confirmed", rec.fetched_at, condition_text=text,
                                 justification=ov["justification"], snapshot_key=rec.key)
                self.flag(pid, "condition", text, True, ov["mondo_id"], "curator_override")
            else:
                self.flag(pid, "condition", text, False, "", "no_exact_MONDO_match_NEEDS_REVIEW",
                          detail=f"{nct}; candidates: {self.condition_candidates(text)}")

    def map_mesh(self, pid, cid, term):
        """MeSH disease -> MONDO, in this order: (1) MONDO xref marked equivalentTo; (2) the MeSH term name
        equals exactly one MONDO label/exact synonym; (3) curator override (nct '*', condition = MeSH id);
        otherwise NEEDS_REVIEW with candidate suggestions."""
        xrefs = self.mondo["mesh_to_mondo"].get(cid, [])
        for mondo in xrefs:
            self.add_mondo(mondo)
            self.kg.add_edge(cid, "biolink:exact_match", mondo, "MONDO",
                             f"{self.mondo['version']} xref {cid} (MONDO:equivalentTo)", "database", self.mondo_date)
        if xrefs:
            return
        exact = self.mondo["name_index"].get(mondo_ref.norm_name(term), [])
        ov = load_condition_overrides().get(("*", cid))
        if len(exact) == 1:
            self.add_mondo(exact[0])
            self.kg.add_edge(cid, "biolink:exact_match", exact[0], "MeSH term name + MONDO",
                             f"{self.mondo['version']} exact name '{term}'", "computed", self.mondo_date,
                             match_rule="MeSH term = MONDO exact label/synonym (no MONDO xref)")
            self.flag(pid, "condition", f"{cid} {term}", True, exact[0], "mesh_term_exact_MONDO_name")
        elif ov and str(ov["mondo_id"]).upper() == "NONE":
            self.flag(pid, "condition", f"{cid} {term}", True, "", "curator_confirmed_no_MONDO_term",
                      detail=ov["justification"])
        elif ov:
            self.add_mondo(ov["mondo_id"])
            self.kg.add_edge(cid, "biolink:exact_match", ov["mondo_id"], "curated:condition_overrides.csv",
                             f"* | {cid}", "curator_confirmed", self.curated_date, justification=ov["justification"])
            self.flag(pid, "condition", f"{cid} {term}", True, ov["mondo_id"], "curator_override")
        else:
            self.flag(pid, "condition", f"{cid} {term}", False, "", "mesh_without_MONDO_NEEDS_REVIEW",
                      detail=f"candidates: {self.condition_candidates(term)}")

    def condition_candidates(self, text):
        """Suggestions for the reviewer only (never added to the graph): MONDO names that appear as
        whole words inside the condition text, longest first, max 3."""
        words = mondo_ref.norm_name(text)
        found = sorted({(len(k), m) for k, ms in self.mondo["name_index"].items() if len(k) >= 6 and k in words
                        for m in ms}, reverse=True)[:3]
        return "; ".join(f"{m} ({self.mondo['terms'][m][0]})" for _, m in found)

    def add_mondo(self, mondo):
        label, syns = self.mondo["terms"].get(mondo, (None, []))
        self.kg.add_node(mondo, "biolink:Disease", name=label, mondo_version=self.mondo["version"])

    def add_outcomes(self, tid, nct, s, rec):
        for o in ctgov.outcomes(s):
            oid = f"OUTCOME:{nct}:{o['oid']}"
            props = {k: v for k, v in o.items() if k != "oid"}
            self.kg.add_node(oid, "tmkg:OutcomeMeasure", name=o["title"], **props)
            self.kg.add_edge(tid, "tmkg:has_outcome_measure", oid, "ClinicalTrials.gov", nct, "registry",
                             rec.fetched_at, snapshot_key=rec.key, outcome_kind=o["kind"])

    def add_design(self, tid, nct, s, rec):
        """Trial design from the registry record: the enrolled population, each arm (with any dose written in its
        label), which resolved drugs each arm receives, and the registered endpoints - linked to the posted result
        (OutcomeMeasure) only when the normalised titles are identical."""
        f = ctgov.study_facts(s)
        full = self.full_record(nct, s, rec)                # eligibility, descriptions, AE terms, baseline
        fs, fr = full if full else (s, rec)
        pop = f"POP:{nct}"
        el, bl = ctgov.eligibility(fs), ctgov.baseline(fs)
        import doc_discovery as dd
        self.kg.nodes[tid]["other_registry_ids"] = json.dumps(
            [dict(type=o["type"], id=o["id"]) for o in dd.other_registries(fs) if o.get("id")], ensure_ascii=False) or None
        self.kg.add_node(pop, "biolink:StudyPopulation", name=f"Participants of {nct}", enrollment=f.get("enrollment"),
                         conditions_text=f.get("conditions"), eligibility_criteria=el.get("criteria"), sex=el.get("sex"),
                         minimum_age=el.get("minimum_age"), maximum_age=el.get("maximum_age"),
                         healthy_volunteers=el.get("healthy_volunteers"), std_ages=el.get("std_ages"),
                         baseline_population=(bl or {}).get("population"),
                         baseline_groups=(bl or {}).get("groups"), baseline=(bl or {}).get("measures"))
        self.kg.add_edge(tid, "tmkg:has_study_population", pop, "ClinicalTrials.gov", nct, "registry", fr.fetched_at,
                         snapshot_key=fr.key)
        for kind in ("serious", "other"):
            terms = ctgov.adverse_event_terms(fs, kind)
            if not terms:
                continue
            rid = f"AEREPORT:{nct}:{kind.upper()}"
            ae = (fs.get("resultsSection") or {}).get("adverseEventsModule", {})
            self.kg.add_node(rid, "tmkg:AdverseEventReport", name=f"{kind} adverse events reported in {nct}",
                             event_type=kind, n_terms=len(terms), time_frame=ae.get("timeFrame"),
                             frequency_threshold=ae.get("frequencyThreshold"),
                             vocabularies=sorted({t["vocabulary"] for t in terms if t["vocabulary"]}),
                             organ_systems=sorted({t["organ_system"] for t in terms if t["organ_system"]}),
                             terms=terms)
            self.kg.add_edge(tid, "tmkg:has_adverse_event_report", rid, "ClinicalTrials.gov", nct, "registry",
                             fr.fetched_at, snapshot_key=fr.key, event_type=kind)
        desc = {a["label"]: a for a in ctgov.arms(fs)}
        arm_ids = {}
        for a in [dict(x, description=(desc.get(x["label"]) or {}).get("description") or x["description"],
                       dose=(desc.get(x["label"]) or {}).get("dose") or x["dose"]) for x in ctgov.arms(s)]:
            aid = f"ARM:{nct}:{a['aid']}"
            arm_ids[a["label"]] = aid
            d = a["dose"]
            self.kg.add_node(aid, "tmkg:StudyArm", name=a["label"], arm_type=a["type"], description=a["description"],
                             dose_text="; ".join(x["text"] for x in d) or None,
                             dose_value=d[0]["value"] if len(d) == 1 else None, dose_unit=d[0]["unit"] if len(d) == 1 else None)
            self.kg.add_edge(tid, "tmkg:has_arm", aid, "ClinicalTrials.gov", nct, "registry", rec.fetched_at,
                             snapshot_key=rec.key)
        for (n, name), dids in sorted(self.intervention_drugs.items()):
            if n != nct:
                continue
            iv = next((i for i in ctgov.arm_interventions(s) if i["name"] == name), None)
            for label in (iv or {}).get("arm_labels", []):
                for did in sorted(dids):
                    if label in arm_ids:
                        self.kg.add_edge(arm_ids[label], "tmkg:arm_receives", did, "ClinicalTrials.gov", nct, "registry",
                                         rec.fetched_at, snapshot_key=rec.key, registry_name=name,
                                         chembl_match=dids[did])
        results = {mondo_ref.norm_name(o["title"]): f"OUTCOME:{nct}:{o['oid']}" for o in ctgov.outcomes(s)
                   if o["source"] == "results"}
        for ep in ctgov.registered_endpoints(s):
            eid = f"ENDPOINT:{nct}:{ep['eid']}"
            self.kg.add_node(eid, "tmkg:Endpoint", name=ep["measure"], kind=ep["kind"], time_frame=ep["time_frame"])
            self.kg.add_edge(tid, "tmkg:has_registered_endpoint", eid, "ClinicalTrials.gov", nct, "registry",
                             rec.fetched_at, snapshot_key=rec.key, endpoint_kind=ep["kind"])
            res = results.get(mondo_ref.norm_name(ep["measure"]))
            if res and res in self.kg.nodes:
                self.kg.add_edge(eid, "tmkg:endpoint_result", res, "ClinicalTrials.gov", f"{nct} | {ep['eid']}",
                                 "computed", rec.fetched_at, snapshot_key=rec.key,
                                 match_rule="registered measure = posted result title (normalised, exact)")

    def full_record(self, nct, s, rec):
        """The registry record with every module (CT.gov API v2, frozen) when the trial source is AACT: AACT's frozen
        record holds only the tables in aact.TABLES. -> (study, Response) or None if not in the snapshot."""
        if TRIAL_SOURCE != "aact":
            return s, rec
        try:
            r = fetch(f"{CT}/{nct}", None, self.snap)
        except Exception:
            self.flag("", "registry_full_record", nct, False, "", "full_record_not_in_snapshot")
            return None
        return (r.body, r) if isinstance(r.body, dict) else None

    def add_arm_drugs(self, pid, tid, nct, s, rec, investigational):
        """Registry arm drugs -> comparator / background edges. Two passes: resolve every drug name first,
        then classify. Drugs are compared by ChEMBL parent molecule (salt forms = same drug).
        Returns {investigational drug id: ChEMBL match rule of its registry name} for drugs the registry arms name."""
        parent = lambda did: self.kg.nodes.get(did, {}).get("chembl_parent") or did
        inv_parents = {parent(d) for d in investigational}
        resolved = []                                   # (registry name, intervention, chembl result)
        for iv in ctgov.arm_interventions(s):
            if iv["type"] not in ctgov.DRUG_TYPES:
                continue
            for part in ctgov.split_combo(iv["name"]):
                if "placebo" in part.lower():
                    continue
                for name in ALIASES.get(norm(part), [part]):        # curated composition, e.g. AMX0035
                    r = override_drug(name, self.snap, self.kg.nodes)   # a curator's decision wins
                    if r and r["chembl_id"] is None:            # curator: a drug class, not one molecule
                        self.flag(pid, "trial_intervention", name, True, "", "curator_confirmed_not_a_single_drug",
                                  detail=f"{nct}; {r['justification']}")
                        continue
                    for n in ([] if r else ctgov.name_candidates(name) + iv["other_names"]):
                        r = resolve_drug(n, allow_unnamed=False, snap=self.snap)
                        if r:
                            break
                    if not r:
                        self.flag(pid, "trial_intervention", name, False, "", "no_exact_ChEMBL_match_NEEDS_REVIEW",
                                  detail=f"{nct}; arms: {','.join(iv['arm_types'])}")
                        continue
                    resolved.append((name, iv, r))
                    self.intervention_drugs.setdefault((nct, iv["name"]), {})
        par = lambda r: f"CHEMBL:{r.get('parent_chembl_id') or r['chembl_id']}"
        inv_in_registry = any(par(r) in inv_parents for _, _, r in resolved)
        found = {}
        for name, iv, r in resolved:
            if par(r) in inv_parents:                  # registry name / salt form of the investigational drug
                did = next(d for d in investigational if parent(d) == par(r))
                self.intervention_drugs[(nct, iv["name"])].setdefault(did, r["match"])
                found.setdefault(did, r["match"])
                names = sorted(set(json.loads(self.kg.nodes[did].get("registry_names") or "[]")) | {name})
                self.kg.nodes[did]["registry_names"] = json.dumps(names, ensure_ascii=False)
                continue
            role = ctgov.intervention_role(iv["arm_types"])
            if role == "background" and not inv_in_registry:
                self.flag(pid, "trial_intervention", name, False, f"CHEMBL:{r['chembl_id']}",
                          "investigational_drug_not_found_in_registry_arms_NEEDS_REVIEW",
                          detail=f"{nct}; possibly a code name of the investigational drug")
                continue
            did = self.add_drug(r)
            self.intervention_drugs[(nct, iv["name"])].setdefault(did, r["match"])
            pred = "tmkg:has_comparator_drug" if role == "comparator" else "tmkg:has_background_drug"
            self.kg.add_edge(tid, pred, did, "ClinicalTrials.gov", nct, "registry", rec.fetched_at,
                             snapshot_key=rec.key, registry_name=name, arm_types=iv["arm_types"],
                             chembl_match=r["match"])
        return found

    # -- targets -> genes -> pathways, gene <-> indication
    def add_genes(self):
        """ChEMBL target components -> Ensembl genes. Protein accessions (UniProt) are mapped by Open Targets
        (only unique hits); nucleic-acid targets already carry an Ensembl gene id in ChEMBL."""
        targets = sorted((n for n in self.kg.nodes.values() if n["category"] in GENE_LINK), key=lambda n: n["id"])
        accs = sorted({a for t in targets for a in json.loads(t.get("uniprot") or "[]") if not a.startswith("ENSG")})
        r, mapping = opentargets.uniprot_to_ensembl(self.snap, accs)
        for t in targets:
            pred = GENE_LINK[t["category"]]
            for a in json.loads(t.get("uniprot") or "[]"):
                if a.startswith("ENSG"):
                    tr = fetch(f"{CHEMBL}/target/{t['id'].split(':')[1]}.json", None, self.snap)
                    gid = self.kg.add_node(f"ENSEMBL:{a}", "biolink:Gene")
                    self.kg.add_edge(t["id"], pred, gid, "ChEMBL",
                                     f"{self.chembl_version} target {t['id'].split(':')[1]} component {a}",
                                     "database", tr.fetched_at, snapshot_key=tr.key)
                    continue
                if a not in mapping:
                    self.flag("", "target_gene", a, False, "", "uniprot_not_uniquely_mapped_NEEDS_REVIEW",
                              detail=t["id"])
                    continue
                ens, sym = mapping[a]
                gid = self.kg.add_node(f"ENSEMBL:{ens}", "biolink:Gene", name=sym)
                g = self.kg.nodes[gid]
                g["uniprot"] = json.dumps(sorted(set(json.loads(g.get("uniprot") or "[]")) | {a}))
                self.kg.add_edge(t["id"], pred, gid, "Open Targets Platform",
                                 f"Open Targets {self.ot_release} mapIds UniProt:{a}", "database", r.fetched_at,
                                 snapshot_key=r.key)

    def add_gene_symbols(self):
        """Genes that came from ChEMBL nucleic-acid targets have no symbol yet: ask Open Targets."""
        missing = sorted(n["id"].split(":")[1] for n in self.kg.nodes.values()
                         if n["category"] == "biolink:Gene" and not n.get("name"))
        r, mapping = opentargets.uniprot_to_ensembl(self.snap, missing)
        for ens, (hit, sym) in mapping.items():
            if hit == ens:
                self.kg.nodes[f"ENSEMBL:{ens}"]["name"] = sym

    def add_pathways(self):
        ver = reactome.version(self.snap)
        for g in sorted(n["id"] for n in self.kg.nodes.values() if n["category"] == "biolink:Gene"):
            accs = sorted(json.loads(self.kg.nodes[g].get("uniprot") or "[]"))
            queries = [("UniProt", a) for a in accs] or [("ENSEMBL", g.split(":")[1])]
            for db, acc in queries:
                r, pws = reactome.pathways(self.snap, acc, db)
                for pw in pws:
                    pid = f"REACT:{pw['id']}"
                    self.kg.add_node(pid, "biolink:Pathway", name=pw["name"], reactome_version=ver)
                    self.kg.add_edge(g, "biolink:participates_in", pid, "Reactome",
                                     f"Reactome v{ver} {db}:{acc}", "database", r.fetched_at, snapshot_key=r.key)
        return ver

    def genes_of(self, drug_id):
        out = set()
        for e in self.kg.edges.values():
            if e["subject"] == drug_id and e["predicate"] == "biolink:interacts_with":
                t = e["object"]
                if t.startswith("ENSEMBL:"):
                    out.add(t)
                out |= {x["object"] for x in self.kg.edges.values()
                        if x["subject"] == t and x["predicate"] in GENE_LINK.values()}
        return out

    def mondo_of(self, trial_id):
        out = set()
        for e in self.kg.edges.values():
            if e["subject"] == trial_id and e["predicate"] == "tmkg:studies_condition":
                if e["object"].startswith("MONDO:"):
                    out.add(e["object"])
                out |= {x["object"] for x in self.kg.edges.values()
                        if x["subject"] == e["object"] and x["predicate"] == "biolink:exact_match"}
        return out

    def add_indication_associations(self):
        """For every program: genes of its investigational drugs x MONDO diseases of its trials."""
        pairs = self.program_pairs()                        # disease -> {gene: [programs]}
        for d in sorted(pairs):
            ot_id = d.replace(":", "_")
            r, name, rows = opentargets.disease_target_scores(self.snap, ot_id, [g.split(":")[1] for g in pairs[d]])
            found = {x["ensembl"]: x for x in rows}
            for g in sorted(pairs[d]):
                x = found.get(g.split(":")[1])
                for pid in sorted(set(pairs[d][g])):
                    self.flag(pid, "target_indication", f"{g} | {d}", x is not None,
                              str(x["score"]) if x else "",
                              "open_targets_association" if x else
                              ("disease_not_in_open_targets" if name is None else "no_association_in_open_targets"),
                              detail=f"Open Targets {self.ot_release}; snapshot {r.key}")
                if x:
                    self.kg.add_edge(g, "biolink:associated_with", d, "Open Targets Platform",
                                     f"Open Targets {self.ot_release} association {g.split(':')[1]}-{ot_id}",
                                     "database", r.fetched_at, snapshot_key=r.key, ot_score=x["score"],
                                     ot_datatype_scores=x["datatypes"])

    def program_pairs(self):
        """{MONDO disease: {gene: [programs]}} - genes of each program's investigational drugs x its trials' diseases."""
        pairs = {}
        for pid, drugs in self.program_drugs.items():
            genes = set().union(*(self.genes_of(d) for d in drugs)) if drugs else set()
            diseases = set().union(*(self.mondo_of(t) for t in self.program_trials[pid])) if self.program_trials[pid] else set()
            for d in diseases:
                for g in genes:
                    pairs.setdefault(d, {}).setdefault(g, []).append(pid)
        return pairs

    def add_genetic_evidence(self):
        """Open Targets genetic evidence for each program's target genes in the program's own diseases: one
        biolink:gene_associated_with_condition edge per (gene, disease, datasource), e.g. ClinVar (EVA) germline
        variants with their clinical significance counts, COSMIC Cancer Gene Census / IntOGen mutated samples.
        Summaries only (counts, first 10 ClinVar records / rs ids / PMIDs); no variant is interpreted here."""
        pairs = self.program_pairs()
        for d in sorted(pairs):
            ot_id = d.replace(":", "_")
            genes = sorted(pairs[d])
            r, ev = opentargets.genetic_evidence(self.snap, ot_id, [g.split(":")[1] for g in genes])
            for (ens, ds), o in sorted(ev.items()):
                g = f"ENSEMBL:{ens}"
                if g not in self.kg.nodes:
                    continue
                label = GENETIC_SOURCE.get(ds, ds)
                path = sum(n for k, n in o["clinical_significance"].items() if k in ("pathogenic", "likely pathogenic"))
                self.kg.add_edge(g, "biolink:gene_associated_with_condition", d, f"Open Targets Platform: {label}",
                                 f"Open Targets {self.ot_release} evidence {ds} {ens}-{ot_id}", "database", r.fetched_at,
                                 snapshot_key=r.key, ot_datasource=ds,
                                 evidence_origin="somatic" if ds in opentargets.SOMATIC else "germline",
                                 n_evidence=o["n_evidence"], ot_evidence_score_max=o["max_score"],
                                 clinical_significance=o["clinical_significance"] or None,
                                 n_pathogenic_or_likely=path if o["clinical_significance"] else None,
                                 n_clinvar_records=o["n_clinvar_records"] or None, clinvar_records=o["clinvar_records"],
                                 n_variants=o["n_variants"] or None, variants=o["variants"],
                                 mutated_samples=o["mutated_samples"], variant_consequences=o["consequences"],
                                 direction_on_trait=o["direction"] or None, pmids=o["pmids"],
                                 truncated=o.get("truncated"))
            found = {f"ENSEMBL:{e}" for e, _ in ev}
            for g in genes:
                for pid in sorted(set(pairs[d][g])):
                    self.flag(pid, "genetic_evidence", f"{g} | {d}", g in found,
                              ";".join(sorted(ds for e, ds in ev if f"ENSEMBL:{e}" == g)),
                              "open_targets_genetic_evidence" if g in found else "no_genetic_evidence_in_open_targets",
                              detail=f"Open Targets {self.ot_release}; snapshot {r.key}")

    def add_target_disease_landscape(self, top=TOP_DISEASES, min_score=MIN_SCORE):
        """Other indications: for every target gene of a program drug, its top Open Targets diseases with an
        overall association score >= min_score (biolink:associated_with, association_scope 'target_top_diseases').
        Only MONDO / EFO disease ids are kept (others, e.g. HP phenotypes, are counted in the review report)."""
        genes = sorted(set().union(*(self.genes_of(d) for ds in self.program_drugs.values() for d in ds)))
        have = {(e["subject"], e["object"]) for e in self.kg.edges.values() if e["predicate"] == "biolink:associated_with"}
        for g in genes:
            r, rows = opentargets.target_diseases(self.snap, g.split(":")[1], top)
            kept = skipped = 0
            for rank, x in enumerate(rows, 1):
                if x["score"] < min_score:
                    break
                pre, _, num = x["disease"].partition("_")
                if pre not in ("MONDO", "EFO"):
                    skipped += 1
                    continue
                d = f"{pre}:{num}"
                if pre == "MONDO" and d in self.mondo["terms"]:
                    self.add_mondo(d)
                else:
                    self.kg.add_node(d, "biolink:Disease", name=x["name"], name_source=f"Open Targets {self.ot_release}")
                kept += 1
                if (g, d) in have:
                    continue
                self.kg.add_edge(g, "biolink:associated_with", d, "Open Targets Platform",
                                 f"Open Targets {self.ot_release} target {g.split(':')[1]} top diseases", "database",
                                 r.fetched_at, snapshot_key=r.key, ot_score=x["score"], ot_datatype_scores=x["datatypes"],
                                 association_scope="target_top_diseases", ot_rank=rank)
            self.flag("", "target_landscape", g, kept > 0, str(kept), "open_targets_top_diseases",
                      detail=f"top {top}, score >= {min_score}; {skipped} non-MONDO/EFO ids skipped; snapshot {r.key}")

    def add_biomarkers(self):
        """Owner-confirmed biomarker subgroups (data/curated/biomarkers.csv, proposed by pipelines/biomarkers.py):
        outcome / endpoint -tmkg:analysed_in_subgroup-> Biomarker (definition = exact title text), and
        Biomarker -tmkg:biomarker_gene-> Gene only when the confirmed Ensembl id is already a gene in the graph."""
        import biomarkers
        for r in biomarkers.load_confirmed():
            if r["node_id"] not in self.kg.nodes:
                self.flag("", "biomarker", f"{r['node_id']} | {r['biomarker']}", False, "", "node_not_in_graph_NEEDS_REVIEW")
                continue
            bid = f"BIOMARKER:{biomarkers.slug(r['biomarker'])}"
            self.kg.add_node(bid, "tmkg:Biomarker", name=r["biomarker"])
            self.kg.add_edge(r["node_id"], "tmkg:analysed_in_subgroup", bid, "curated:biomarkers.csv",
                             f"{r['node_id']} | {r['biomarker']}", "curator_confirmed", self.curated_date,
                             definition=r.get("definition"), justification=r["justification"])
            g = f"ENSEMBL:{r['ensembl']}" if r.get("ensembl") else None
            if g and g in self.kg.nodes:
                self.kg.add_edge(bid, "tmkg:biomarker_gene", g, "curated:biomarkers.csv", f"{bid} | {g}",
                                 "curator_confirmed", self.curated_date, justification=r["justification"])
            elif g:
                self.flag("", "biomarker", f"{bid} | {g}", False, "", "gene_not_in_graph_NEEDS_REVIEW")

    def add_pharmacogenomics(self):
        """PharmGKB / ClinPGx clinical annotations for every drug in the graph: Gene -biolink:associated_with_response_to->
        Drug, one edge per annotation that names this drug, with the PharmGKB level (1A..4), annotation types
        (Efficacy, Toxicity, Dosage, Metabolism/PK), variant and diseases. Gene symbols -> Ensembl via Open Targets
        (unique hits only); a gene not yet in the graph is added as a node."""
        import pharmgkb
        drugs = sorted(n for n, v in self.kg.nodes.items() if v["category"] == "biolink:Drug" and v.get("name"))
        found = []
        for d in drugs:
            name = self.kg.nodes[d]["name"]
            r, rows = pharmgkb.annotations(self.snap, name)
            rows = [a for a in rows if name.lower() in {c.lower() for c in a["chemicals"]}]
            found += [(d, r, a) for a in rows]
            self.flag("", "pharmacogenomics", name, bool(rows), str(len(rows)), "clinpgx_clinical_annotations",
                      detail=f"levels {sorted({a['level'] for a in rows})}; snapshot {r.key}")
        symbols = sorted({g for _, _, a in found for g in a["genes"]})
        mr, mapping = opentargets.symbols_to_ensembl(self.snap, symbols)
        for d, r, a in found:
            for sym in a["genes"]:
                ens = (mapping.get(sym) or (None, None))[0]
                if not ens:
                    self.flag("", "pharmacogenomics_gene", sym, False, "", "no_exact_symbol_in_open_targets_NEEDS_REVIEW")
                    continue
                g = f"ENSEMBL:{ens}"
                self.kg.add_node(g, "biolink:Gene", symbol=sym, name=sym)
                self.kg.add_edge(g, "biolink:associated_with_response_to", d, "ClinPGx (PharmGKB)", a["accession"],
                                 "database", r.fetched_at, snapshot_key=r.key, pgx_level=a["level"],
                                 pgx_types=a["types"], variant=a["variant"], diseases=a["diseases"],
                                 pgx_score=a["score"], annotation=a["name"], symbol_map_snapshot=mr.key if mr else None,
                                 url=f"https://www.clinpgx.org/clinicalAnnotation/{a['accession']}")

    def add_atc(self):
        """WHO ATC classification of every drug from its ChEMBL molecule record (the parent molecule's when a salt
        has none), with the class names of each level from ChEMBL's atc_class table: node properties atc_codes and
        atc (JSON list of dict(code, level1..level4 descriptions, who_name))."""
        for did in sorted(n for n, v in self.kg.nodes.items() if v["category"] == "biolink:Drug"):
            node, codes = self.kg.nodes[did], []
            for cid in [did, node.get("chembl_parent")]:
                if not cid or codes:
                    continue
                m = fetch(f"{CHEMBL}/molecule/{cid.split(':')[1]}.json", None, self.snap).body or {}
                codes = sorted(m.get("atc_classifications") or [])
            classes = []
            for code in codes:
                rows = (fetch(f"{CHEMBL}/atc_class.json", {"level5": code}, self.snap).body or {}).get("atc") or []
                for a in rows[:1]:
                    classes.append(dict(code=code, who_name=a.get("who_name"),
                                        **{f"level{i}": f"{a.get(f'level{i}')} {a.get(f'level{i}_description')}"
                                           for i in range(1, 5)}))
            if codes:
                node["atc_codes"] = "|".join(codes)
                node["atc"] = json.dumps(classes, ensure_ascii=False, sort_keys=True)
            self.flag("", "atc", node.get("name") or did, bool(codes), "|".join(codes), "chembl_atc_classification")

    def add_gene_ontology(self):
        """GO biological processes of each target gene of a program drug (Open Targets, experimental evidence only):
        Gene -biolink:participates_in-> biolink:BiologicalProcess (GO id), with the evidence codes."""
        genes = sorted(set().union(*(self.genes_of(d) for ds in self.program_drugs.values() for d in ds)))
        for g in genes:
            r, rows = opentargets.gene_ontology(self.snap, g.split(":")[1])
            for t in rows:
                self.kg.add_node(t["go"], "biolink:BiologicalProcess", name=t["label"])
                self.kg.add_edge(g, "biolink:participates_in", t["go"], "Open Targets Platform: Gene Ontology",
                                 f"Open Targets {self.ot_release} geneOntology {g.split(':')[1]} {t['go']}", "database",
                                 r.fetched_at, snapshot_key=r.key, go_evidence=t["evidence"], go_sources=t["sources"])

    def add_raw_items(self, rsnap):
        """P10 step A - structured raw items for every program's investigational drug, from the frozen discovery
        responses (data/snapshots_raw/, the same ones raw_docs.py used): Drugs@FDA applications with their posted
        documents, and FAERS report totals with the top reactions (spontaneous reports, all indications: counts
        are not incidence and say nothing about cause)."""
        import doc_discovery as dd
        drugs = sorted({e["object"] for e in self.kg.edges.values() if e["predicate"] == "tmkg:has_intervention"})
        for d in drugs:
            name = self.kg.nodes[d].get("name") or ""
            try:
                r, apps = dd.fda_applications(rsnap, name)
                fa = dd.faers(rsnap, name)
            except Exception:                       # not in the frozen discovery responses: listed, never guessed
                self.flag("", "raw_items", name, False, "", "not_in_discovery_snapshot")
                continue
            for a in apps:
                aid = f"FDA:{a['application']}"
                letters = [x["date"] for x in a["docs"] if x["type"] == "Letter" and x["date"]]
                self.kg.add_node(aid, "tmkg:RegulatoryApplication", name=f"{a['application']} {a['brand']}".strip(),
                                 brand=a["brand"], sponsor=a["sponsor"], n_documents=len(a["docs"]),
                                 first_letter_date=min(letters) if letters else None, documents=a["docs"])
                self.kg.add_edge(d, "tmkg:has_regulatory_application", aid, "openFDA Drugs@FDA", a["application"],
                                 "database", r.fetched_at, snapshot_key=r.key)
            if fa["total"]:
                fid = f"FAERS:{d.split(':')[1]}"
                self.kg.add_node(fid, "tmkg:SpontaneousReportSummary", name=f"FAERS reports naming {name}",
                                 total_reports=fa["total"], query_field=fa["query_field"],
                                 top_reactions=[dict(term=t, reports=n) for t, n in fa["top_reactions"]],
                                 caveat="spontaneous reports, all indications; counts are not incidence or causation")
                self.kg.add_edge(d, "tmkg:has_faers_summary", fid, "openFDA FAERS", f"{name} ({fa['query_field']})",
                                 "database", fa["fetched_at"], snapshot_key=fa["key"])
            self.flag("", "raw_items", name, True, f"{len(apps)} FDA applications; {fa['total']} FAERS reports",
                      "raw_items_from_discovery_snapshot")

    def add_protocol_findings(self):
        """Owner-confirmed protocol / SAP facts (data/curated/protocol_findings.csv, proposed by
        pipelines/protocol_findings.py with verbatim quotes): Trial -tmkg:has_protocol_finding-> ProtocolFinding."""
        import protocol_findings as pf
        seen = {}
        for r in pf.load_confirmed():
            tid = f"NCT:{r['nct']}"
            if tid not in self.kg.nodes:
                self.flag("", "protocol_finding", f"{r['nct']} {r['finding']}", False, "", "trial_not_in_graph_NEEDS_REVIEW")
                continue
            k = seen[(r["nct"], r["finding"])] = seen.get((r["nct"], r["finding"]), 0) + 1
            fid = f"PROTOCOL:{r['nct']}:{r['finding']}:{k}"
            self.kg.add_node(fid, "tmkg:ProtocolFinding", name=f"{r['finding'].replace('_', ' ')}: {r['value']}",
                             finding=r["finding"], value=r["value"], quote=r["quote"], document=r.get("document"))
            self.kg.add_edge(tid, "tmkg:has_protocol_finding", fid, "curated:protocol_findings.csv",
                             f"{r['nct']} | {r.get('document')} | {r['finding']} | {k}", "curator_confirmed",
                             self.curated_date, justification=r["justification"])

    def add_gwas(self):
        """GWAS Catalog: genome-wide significant associations (p <= 5e-8) mapped to a target gene of a program drug,
        kept ONLY when the trait is a MONDO disease or an EFO disease already in the graph; trait-level aggregates per
        (gene, disease): Gene -biolink:gene_associated_with_condition-> Disease with n associations, smallest p,
        studies, PMIDs and lead variants. Measurement traits (e.g. protein levels) are counted, not linked."""
        import gwas
        genes = sorted(set().union(*(self.genes_of(d) for ds in self.program_drugs.values() for d in ds)))
        diseases = {n for n, v in self.kg.nodes.items() if v["category"] == "biolink:Disease"}
        for g in genes:
            sym = self.kg.nodes[g].get("symbol")
            if not sym:
                continue
            rs, rows = gwas.associations(self.snap, sym)
            agg, other = {}, 0
            for a in rows:
                hit = [t for t in a["traits"] if t[0] in diseases or (t[0] or "").split(":")[0] == "MONDO"
                       and t[0] in self.mondo["terms"]]           # MONDO ids are diseases; EFO only if already a disease
                other += not hit
                for d, label in hit:
                    o = agg.setdefault(d, dict(n=0, p=1.0, studies=set(), pmids=set(), snps=set(), label=label))
                    o["n"] += 1
                    o["p"] = min(o["p"], a["p_value"])
                    o["studies"].add(a["study"])
                    o["pmids"].add(a["pmid"])
                    o["snps"] |= set(a["snps"])
            last = rs[-1]
            for d, o in sorted(agg.items()):
                if d not in self.kg.nodes:
                    self.add_mondo(d)
                self.kg.add_edge(g, "biolink:gene_associated_with_condition", d, "GWAS Catalog",
                                 f"GWAS Catalog mapped_gene {sym} trait {d}", "database", last.fetched_at,
                                 snapshot_key=last.key, ot_datasource="gwas_catalog", evidence_origin="germline",
                                 n_associations=o["n"], min_p_value=o["p"], studies=sorted(x for x in o["studies"] if x)[:10],
                                 pmids=sorted(x for x in o["pmids"] if x)[:10], variants=sorted(o["snps"])[:10])
            self.flag("", "gwas", sym, bool(agg), f"{len(agg)} disease traits; {other} other associations",
                      "gwas_catalog_genome_wide", detail=f"snapshot {last.key}")

    def add_publication_findings(self):
        """Owner-confirmed findings stated in trial papers (data/curated/publication_findings.csv, proposed by
        pipelines/publication_findings.py with verbatim quotes): Publication -tmkg:reports_finding-> PublicationFinding."""
        import publication_findings as pubf
        seen = {}
        for r in pubf.load_confirmed():
            rec = r.get("record") or f"PMID_{r['pmid']}"
            pre, num = rec.split("_", 1)
            pub = f"{pre}:{num}"                                      # PMID:123 / PMC:PMC456 / PPR:PPR789
            if pub not in self.kg.nodes:
                self.kg.add_node(pub, "biolink:Publication")
            k = seen[(rec, r["finding"])] = seen.get((rec, r["finding"]), 0) + 1
            fid = f"PUBFINDING:{rec}:{r['finding']}:{k}"
            self.kg.add_node(fid, "tmkg:PublicationFinding", name=f"{r['finding'].replace('_', ' ')}: {r['value']}",
                             finding=r["finding"], value=r["value"], quote=r["quote"], program=r.get("program"))
            self.kg.add_edge(pub, "tmkg:reports_finding", fid, "curated:publication_findings.csv",
                             f"{rec} | {r['finding']} | {k}", "curator_confirmed", self.curated_date,
                             justification=r["justification"])

    def add_document_findings(self):
        """Owner-confirmed facts from FDA / EMA / sponsor documents (data/curated/document_findings.csv, proposed by
        pipelines/document_findings.py with verbatim quotes): Program -tmkg:has_document_finding-> DocumentFinding."""
        import document_findings as docf
        seen = {}
        for r in docf.load_confirmed():
            if r["program"] not in self.kg.nodes:
                continue
            k = seen[(r["program"], r["finding"])] = seen.get((r["program"], r["finding"]), 0) + 1
            fid = f"DOCFINDING:{r['program']}:{r['finding']}:{k}"
            self.kg.add_node(fid, "tmkg:DocumentFinding", name=f"{r['finding'].replace('_', ' ')}: {r['value']}",
                             finding=r["finding"], value=r["value"], quote=r["quote"], document=r.get("document"),
                             document_kind=r.get("kind"))
            self.kg.add_edge(r["program"], "tmkg:has_document_finding", fid, "curated:document_findings.csv",
                             f"{r['program']} | {r.get('document')} | {r['finding']} | {k}", "curator_confirmed",
                             self.curated_date, justification=r["justification"])

    def add_phenotypes(self):
        """HPO phenotypes of the programs' own diseases (MONDO, via Open Targets HPO annotations, curated evidence):
        Disease -biolink:has_phenotype-> PhenotypicFeature (HP id), with evidence codes and frequency terms."""
        diseases = sorted(set().union(*(self.mondo_of(t) for ts in self.program_trials.values() for t in ts)))
        for d in diseases:
            r, rows = opentargets.disease_phenotypes(self.snap, d.replace(":", "_"))
            for p in rows:
                self.kg.add_node(p["hp"], "biolink:PhenotypicFeature", name=p["name"])
                self.kg.add_edge(d, "biolink:has_phenotype", p["hp"], "Open Targets Platform: HPO annotations",
                                 f"Open Targets {self.ot_release} phenotypes {d} {p['hp']}", "database", r.fetched_at,
                                 snapshot_key=r.key, hpo_evidence=p["evidence"], frequencies=p["frequencies"] or None)

    def add_curated_events(self, md, progs):
        """The three 'after failure' tables -> tmkg:DevelopmentEvent nodes. A row is linked only when its drug
        (or trial) name equals exactly one program's drug (or named trial) after norm(); otherwise it goes to
        the review queue. Text and links are copied from the curated file; nothing is added."""
        by_drug, by_trial = {}, {}
        for p in progs:
            for d in p["drugs"]:
                by_drug.setdefault(norm(d), set()).add(p["program_id"])
            for t in p["trials"]:
                by_trial.setdefault(norm(t), set()).add(p["program_id"])
        rows = (curated_tables.post_failure_rows(md) + curated_tables.repurposing_rows(md) +
                curated_tables.data_access_rows(md, expand_trial_tokens))
        pred = {"post_failure": "tmkg:has_post_failure_analysis", "repurposing": "tmkg:has_repurposing_pivot"}
        for r in rows:
            record = f"section '{r['section']}' row {r['row_number']}"
            links = []                                   # (subject, predicate)
            if r["kind"] == "data_access":
                for t in r["key"]:
                    pids = by_trial.get(norm(t), set())
                    if len(pids) != 1:
                        self.flag("", "curated_event", f"{r['event_id']} {t}", False, "",
                                  "trial_name_not_unique_in_inventory_NEEDS_REVIEW", detail=f"programs: {sorted(pids)}")
                        continue
                    pid = next(iter(pids))
                    q = next(x for x in self.trial_query_ncts if x[0] == pid and norm(x[1]) == norm(t))
                    links += [(f"NCT:{n}", "tmkg:has_data_access_route") for n in self.trial_query_ncts[q]]
            else:
                pids = by_drug.get(norm(r["key"][0]), set())
                if len(pids) != 1:
                    self.flag("", "curated_event", f"{r['event_id']} {r['name']}", False, "",
                              "drug_not_unique_in_inventory_NEEDS_REVIEW", detail=f"programs: {sorted(pids)}")
                    continue
                links.append((next(iter(pids)), pred[r["kind"]]))
            if not links:
                continue
            self.kg.add_node(r["event_id"], "tmkg:DevelopmentEvent", name=r["name"], kind=r["kind"],
                             **r["props"], source_urls=r["urls"], section_source_urls=r["section_urls"])
            for subj, p in sorted(set(links)):
                self.kg.add_edge(subj, p, r["event_id"], self.md_name, record, "curated", self.curated_date)
            pmids = set(literature.pmids_from_text(" ".join(r["urls"])))
            pmids |= set(literature.pmcid_to_pmid(self.snap, literature.pmcids_from_text(" ".join(r["urls"]))).values())
            for pmid in sorted(pmids, key=int):
                self.kg.add_node(f"PMID:{pmid}", "biolink:Publication")
                self.kg.add_edge(r["event_id"], "tmkg:has_source_publication", f"PMID:{pmid}", self.md_name,
                                 record, "curated", self.curated_date)
                self.pmids.add(pmid)

    def derive_clinical_trials_for(self):
        """Drug -biolink:in_clinical_trials_for-> Disease, derived (never curated by hand) from
        trial -tests_intervention-> drug where the registry arms name the drug (in_registry_arms) and
        trial -studies_condition-> disease. A MeSH disease is replaced by its MONDO equivalent(s) when the graph
        has a biolink:exact_match edge; otherwise the MeSH (or MONDO) term itself is used.
        Edge properties: nct_ids, clinical_trial_phases, max_research_phase (BioLink ResearchPhaseEnum),
        derived_from (the input edge ids), mapping_confidence = weakest input."""
        edges = sorted(self.kg.edges.values(), key=lambda e: e["id"])
        by = lambda pred: [e for e in edges if e["predicate"] == pred]
        mondo_of = {}
        for e in by("biolink:exact_match"):
            mondo_of.setdefault(e["subject"], []).append(e)
        pairs = {}
        for t in by("tmkg:tests_intervention"):
            if not t.get("in_registry_arms"):
                continue
            for c in (c for c in by("tmkg:studies_condition") if c["subject"] == t["subject"]):
                for x in mondo_of.get(c["object"]) or [None]:
                    disease = x["object"] if x else c["object"]
                    pairs.setdefault((t["object"], disease), []).append([t, c] + ([x] if x else []))
        for (drug, disease), paths in sorted(pairs.items()):
            used = sorted({e["id"]: e for p in paths for e in p}.values(), key=lambda e: e["id"])
            ncts = sorted({p[0]["subject"].split(":", 1)[1] for p in paths})
            phases = [research_phase(self.kg.nodes[f"NCT:{n}"].get("phase")) for n in ncts]
            self.kg.add_edge(drug, "biolink:in_clinical_trials_for", disease, "TrialMindKG derivation",
                             "tests_intervention x studies_condition: " + ";".join(ncts), "computed",
                             max(e["retrieved"] for e in used), nct_ids=ncts, clinical_trial_phases=phases,
                             max_research_phase=max(phases, key=RESEARCH_PHASES.index),
                             derived_from=[e["id"] for e in used],
                             mapping_confidence=kg_schema.weakest([kg_schema.mapping_confidence(e) for e in used]))

    def add_publication_metadata(self):
        meta = literature.fetch(self.snap, self.pmids)
        for pmid in sorted(self.pmids, key=int):
            m = meta.get(pmid)
            self.kg.add_node(f"PMID:{pmid}", "biolink:Publication")
            if m:
                n = self.kg.nodes[f"PMID:{pmid}"]
                for k in ("title", "journal", "year", "doi", "pmcid", "authors"):
                    if m.get(k):
                        n[k] = m[k]
                n["name"], n["metadata_source"] = m.get("title"), f"Europe PMC (snapshot {m['key']})"
            else:
                self.flag("", "publication", pmid, False, "", "no_Europe_PMC_record")


def build(md_path=DATASET, snap=None):
    """Builds the graph. Returns (KG, report DataFrame, programs, source versions dict)."""
    snap = snap or SNAP
    md = open(md_path).read()
    b = Builder(snap, md)
    progs = parse_inventory(md)
    for p in progs:
        b.add_program(p)
    b.add_curated_events(md, progs)
    b.add_genes()
    b.add_gene_symbols()
    reactome_version = b.add_pathways()
    b.add_indication_associations()
    b.add_genetic_evidence()
    b.add_biomarkers()
    b.add_pharmacogenomics()
    b.add_atc()
    b.add_gene_ontology()
    rsnap = Snapshot(ROOT / "data" / "snapshots_raw", mode="offline")            # frozen discovery responses only
    b.add_raw_items(rsnap)
    b.add_protocol_findings()
    b.add_publication_findings()
    b.add_document_findings()
    b.add_gwas()
    b.add_phenotypes()
    b.add_target_disease_landscape()
    b.add_publication_metadata()
    b.derive_clinical_trials_for()
    import babel                                     # P3: Babel equivalent ids on nodes; same-concept pairs reviewed
    n_map, n_ask, dups = babel.annotate(b.kg, snap)
    for d in dups:
        b.flag("", "babel_same_concept", f"{d['node_a']} = {d['node_b']}", d["already_linked"], d["babel_id"],
               "babel_same_concept_linked" if d["already_linked"] else "babel_same_concept_not_linked_NEEDS_REVIEW",
               detail=d["label"])
    aact_keys = {k for src, k in snap.used if src == "aact"}
    for e in b.kg.edges.values():                    # statements whose registry record came from AACT
        if e.get("snapshot_key") in aact_keys:
            e["via"] = "AACT"
    prov = provenance.annotate(b.kg, snap)          # BioLink knowledge source / level / agent type on every edge
    if prov:
        raise ValueError("provenance_map.csv does not cover the graph:\n  " + "\n  ".join(prov[:20]))
    kg_schema.biolink_annotate(b.kg)                # BioLink parent terms + mapping_confidence on every edge
    chembl = fetch(f"{CHEMBL}/status.json", None, snap).body or {}
    if TRIAL_SOURCE == "aact":
        days = sorted({e["retrieved"][:10] for e in b.kg.edges.values() if e.get("via") == "AACT"})
        registry = ("ClinicalTrials.gov via AACT",
                    f"AACT flat-file export, records frozen {days[0]} to {days[-1]}" if days else "AACT")
    else:
        ctv = fetch("https://clinicaltrials.gov/api/v2/version", None, snap).body or {}
        registry = ("ClinicalTrials.gov", f"API {ctv.get('apiVersion')} data {ctv.get('dataTimestamp')}")
    versions = {registry[0]: registry[1],
                "ChEMBL": f"{chembl.get('chembl_db_version')} ({chembl.get('chembl_release_date')})",
                "Open Targets Platform": b.ot_release, "Reactome": f"v{reactome_version}",
                "MONDO": b.mondo["version"], "Europe PMC": "live search (responses frozen in snapshot)",
                "curated dataset": f"{DATASET.name} as of {b.curated_date} sha256 {sha256_file(md_path)[:16]}",
                "infores registry": "biolink/information-resource-registry infores_catalog.yaml (frozen in snapshot)",
                "Babel node normalizer": f"RENCI Node Normalizer, {n_map} of {n_ask} nodes mapped (responses frozen)",
                "openFDA (Drugs@FDA, FAERS)": "discovery responses frozen in data/snapshots_raw, fingerprint "
                                              + __import__("export").snapshot_fingerprint(rsnap)[:16]}
    cols = ["program", "entity_type", "query", "resolved", "identifier", "rule", "n_candidates", "detail"]
    report = pd.DataFrame(b.report).reindex(columns=cols).drop_duplicates().sort_values(
        ["entity_type", "program", "query", "identifier"], kind="stable").reset_index(drop=True)
    return b.kg, report, progs, versions


if __name__ == "__main__":
    import sys
    from export import export_all
    G, rep, _, versions = build(sys.argv[1] if len(sys.argv) > 1 else DATASET)
    problems = validate(G)
    if problems:
        print("GRAPH FAILED VALIDATION - nothing written:\n  " + "\n  ".join(problems[:50]))
        sys.exit(1)
    if os.environ.get("TMKG_SNAPSHOT_PRUNE") == "1":
        print(f"Pruned {SNAP.prune()} stored responses this build no longer uses")
    elif SNAP.unused():
        print(f"Note: {len(SNAP.unused())} stored responses are unused (TMKG_SNAPSHOT_PRUNE=1 removes them)")
    manifest = export_all(G, rep, versions, SNAP, OUT)
    import contradictions                            # P5b: cross-source checks for review (outputs/contradiction_report.csv)
    from kg_query import KGIndex
    contradictions.run(KGIndex())
    c = manifest["counts"]
    print(f"Seed KG: {c['nodes']} nodes, {c['edges']} edges | review queue: {c['needs_review']} rows")
    print(f"Outputs written to {OUT} (manifest: run_manifest.json)")
