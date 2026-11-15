"""
AACT (CTTI's PostgreSQL copy of ClinicalTrials.gov) as the trial source - same record shape as the CT.gov API v2,
so every parser in ctgov.py and the builder work unchanged.

    python pipelines/aact.py check-schema            # 1. every table/column this module reads exists in AACT
    python pipelines/aact.py compare                 # 2. AACT vs frozen CT.gov records for every trial in the graph
    python pipelines/aact.py freeze                  # 2b. freeze everything an AACT build needs (Mac), report matching
    TMKG_TRIAL_SOURCE=aact python pipelines/seed_kg.py   # 3. build from AACT (after 2 shows no differences)

Connection (never committed): AACT_USER / AACT_PASSWORD in .env for the public AACT database
(aact-db.ctti-clinicaltrials.org:5432, free account at aact.ctti-clinicaltrials.org), or AACT_FLATFILES=<folder>
holding the unzipped pipe-delimited export (<table>.txt; set in .env - it wins over the database). Each query result is frozen through Snapshot
(data/snapshots/aact/), so a build from AACT is as repeatable as one from the API.

AACT stores MeSH condition terms without MeSH ids; ids come from NLM's MeSH lookup (exact label, frozen).
Trial search: AACT has no ClinicalTrials.gov search engine, so candidates are studies whose intervention names or
other names contain the drug name or one of its ChEMBL synonyms (letters and digits compared); the builder's own
rules (acronym exact, secondary id, ...) then decide, exactly as with the API.

Functions
- TABLES                                -> {table: [columns read]}   (single place to fix a renamed column)
- Source(snap).rows(table, nct_ids)     -> [dict]  (postgres or flat files; frozen)
- Source(snap).record(nct)              -> (Response, {table: rows}) one frozen response per trial
- Source(snap).search_raw(names)        -> [nct_id] whose interventions contain any of the names
- study(src, nct, mesh)                 -> (CT.gov-v2-shaped dict for the fields the pipeline uses, Response)
- search_studies(src, drug, names)      -> Response, body {"studies": [...]} like GET /api/v2/studies?query.intr=
- compare(ct_study, aact_study)         -> [differences] on everything ctgov.py extracts
"""
import csv, json, os, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ctgov

ROOT = Path(__file__).resolve().parents[1]
MESH_LOOKUP = "https://id.nlm.nih.gov/mesh/lookup/descriptor"
TABLES = {   # every table: "id" first - AACT row ids follow the registry's own order
    "studies": ["id", "nct_id", "acronym", "brief_title", "official_title", "overall_status", "why_stopped", "phase",
                "enrollment", "start_date", "primary_completion_date", "start_month_year", "primary_completion_month_year",
                "results_first_posted_date", "plan_to_share_ipd",
                "plan_to_share_ipd_description", "ipd_time_frame", "ipd_access_criteria", "ipd_url"],
    "id_information": ["id", "nct_id", "id_type", "id_value"],
    "sponsors": ["id", "nct_id", "lead_or_collaborator", "name"],
    "conditions": ["id", "nct_id", "name"],
    "browse_conditions": ["id", "nct_id", "mesh_term", "mesh_type"],
    "design_groups": ["id", "nct_id", "group_type", "title"],
    "interventions": ["id", "nct_id", "intervention_type", "name"],
    "intervention_other_names": ["id", "nct_id", "intervention_id", "name"],   # id order = registry order
    "design_group_interventions": ["id", "nct_id", "design_group_id", "intervention_id"],
    "design_outcomes": ["id", "nct_id", "outcome_type", "measure", "time_frame"],
    "study_references": ["id", "nct_id", "pmid", "reference_type", "citation"],
    "ipd_information_types": ["id", "nct_id", "name"],
    "result_groups": ["id", "nct_id", "ctgov_group_code", "result_type", "title", "outcome_id"],
    "outcomes": ["id", "nct_id", "outcome_type", "title", "time_frame", "param_type", "units"],
    "outcome_counts": ["id", "nct_id", "outcome_id", "ctgov_group_code", "scope", "count"],
    "outcome_measurements": ["id", "nct_id", "outcome_id", "ctgov_group_code", "classification", "category",
                             "param_value", "dispersion_value", "dispersion_lower_limit", "dispersion_upper_limit"],
    "outcome_analyses": ["id", "nct_id", "outcome_id", "non_inferiority_type", "param_type", "param_value",
                         "ci_percent", "ci_lower_limit", "ci_upper_limit", "p_value_modifier", "p_value", "method"],
    "outcome_analysis_groups": ["id", "nct_id", "outcome_analysis_id", "ctgov_group_code"],
    "reported_event_totals": ["id", "nct_id", "ctgov_group_code", "event_type", "subjects_affected", "subjects_at_risk"],
    "reported_events": ["id", "nct_id", "ctgov_group_code", "time_frame"],
    "provided_documents": ["id", "nct_id", "document_type", "has_protocol", "has_icf", "has_sap", "document_date", "url"],
}


# Read when present. AACT's *_date columns fill a day the sponsor never gave ('2013-07' -> 2013-07-31); the
# *_month_year columns keep the registry's own precision, so they are preferred.
OPTIONAL = {("studies", "start_month_year"), ("studies", "primary_completion_month_year"),
            ("studies", "id")}       # studies has one row per trial (key nct_id), so no row id is needed
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"], 1)}


def month_year(v):
    """'July 2013' -> '2013-07'; 'July 15, 2013' -> '2013-07-15'; ISO text is kept as is; else None."""
    if not v:
        return None
    v = str(v).strip()
    if re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", v):
        return v
    m = re.fullmatch(r"([A-Za-z]+)\s+(?:(\d{1,2}),\s*)?(\d{4})", v)
    if not m or m.group(1).lower() not in MONTHS:
        return None
    mo, day, yr = MONTHS[m.group(1).lower()], m.group(2), m.group(3)
    return f"{yr}-{mo:02d}" + (f"-{int(day):02d}" if day else "")


def enum(x):
    """AACT spells values as on the website ('Placebo Comparator', 'Active, not recruiting'); the API v2 uses enums
    (PLACEBO_COMPARATOR, ACTIVE_NOT_RECRUITING). Either spelling -> the API spelling."""
    if x in (None, ""):
        return None
    return re.sub(r"[^A-Z0-9]+", "_", str(x).upper()).strip("_")


def phases(x):
    """'Phase 2/Phase 3' or 'PHASE2/PHASE3' -> ['PHASE2', 'PHASE3']; 'Not Applicable' / 'N/A' -> ['NA']."""
    if not x:
        return []
    out = []
    for p in str(x).split("/"):
        e = enum(p).replace("PHASE_", "PHASE")
        out.append("NA" if e in ("N_A", "NOT_APPLICABLE", "NA") else e)
    return out


def _s(v):
    """Numbers as the API writes them (strings, no trailing .0)."""
    if v in (None, ""):
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _int(v):
    return int(float(v)) if v not in (None, "") else None


class Source:
    """AACT rows for a set of trials, from PostgreSQL or the flat-file export, frozen through Snapshot."""

    def __init__(self, snap, conn=None, flatfiles=None):
        from nim import load_env
        load_env()                                   # AACT_* settings may live in .env
        self.snap = snap
        self._folder = flatfiles or os.environ.get("AACT_FLATFILES")
        self._flat = None
        self._conn = conn
        self._cache = {}                             # flat files: {table: {nct_id: [rows]}} filled by prefetch()

    @property
    def flat(self):
        """Folder of the flat-file export (found on first use, so replaying frozen records never needs it)."""
        if self._folder and self._flat is None:
            self._flat = find_flatfiles(self._folder)
        return self._flat

    def prefetch(self, nct_ids, tables=None):
        """Flat files only: read each table file ONCE for all trials (the big files are GBs)."""
        if not self.flat:
            return
        want = set(nct_ids)
        for t in tables or TABLES:
            got = {n: [] for n in want}
            for r in _read(self.flat / f"{t}.txt"):
                if r.get("nct_id") in want:
                    got[r["nct_id"]].append({c: (r.get(c) or None) for c in TABLES[t]})
            self._cache[t] = got

    def _pg(self):
        if self._conn is None:
            import psycopg2                       # pip install psycopg2-binary (only needed for the database route)
            from nim import load_env
            load_env()
            self._conn = psycopg2.connect(host=os.environ.get("AACT_HOST", "aact-db.ctti-clinicaltrials.org"),
                                          port=5432, dbname="aact", user=os.environ["AACT_USER"],
                                          password=os.environ["AACT_PASSWORD"])
        return self._conn

    def _query(self, table, nct_ids):
        cols = TABLES[table]
        if self.flat:
            cache = self._cache.get(table, {})
            if all(n in cache for n in nct_ids):
                return [r for n in nct_ids for r in cache[n]]
            want = set(nct_ids)
            return [{c: (r.get(c) or None) for c in cols} for r in _read(self.flat / f"{table}.txt")
                    if r.get("nct_id") in want]
        have = [c for c in cols if (table, c) in _columns(self)]
        cur = self._pg().cursor()
        cur.execute(f"SELECT {', '.join(have)} FROM ctgov.{table} WHERE nct_id = ANY(%s)", (list(nct_ids),))
        return [{c: None for c in cols} | dict(zip(have, [v.isoformat() if hasattr(v, "isoformat") else v for v in r]))
                for r in cur.fetchall()]

    def rows(self, table, nct_ids):
        """Rows of one table for several trials (used by the search); frozen per request."""
        req = {"table": table, "columns": TABLES[table], "nct_ids": sorted(nct_ids)}
        return _sorted(table, self.snap.call("aact", req, lambda: self._query(table, sorted(nct_ids))).body)

    def record(self, nct):
        """Every table for one trial as ONE frozen response (one snapshot key per trial, like the API)."""
        r = self.snap.call("aact", {"record": nct, "tables": TABLES},
                           lambda: {t: self._query(t, [nct]) for t in TABLES})
        return r, {t: _sorted(t, rows) for t, rows in r.body.items()}

    def search_raw(self, names):
        """nct_ids whose intervention name or other name contains any of `names` (compared on letters and digits
        only, so 'AOC 1001' matches 'AOC-1001'; names shorter than 5 characters are ignored as too unspecific).
        Not frozen on its own; search_studies() freezes the whole search."""
        keys = search_keys(names)
        cache = getattr(self, "_search", {})
        if keys and all(k in cache for k in keys):
            return sorted({n for k in keys for n in cache[k]})
        if self.flat:
            hits = set()
            for t in ("interventions", "intervention_other_names"):
                for r in _read(self.flat / f"{t}.txt"):
                    nm = _alnum(r.get("name"))
                    if any(k in nm for k in keys):
                        hits.add(r["nct_id"])
            return sorted(hits)
        cur = self._pg().cursor()
        hits = set()
        for k in keys:
            cur.execute("SELECT nct_id FROM ctgov.interventions WHERE regexp_replace(lower(name), '[^a-z0-9]', '', 'g') "
                        "LIKE %s UNION SELECT nct_id FROM ctgov.intervention_other_names WHERE "
                        "regexp_replace(lower(name), '[^a-z0-9]', '', 'g') LIKE %s", (f"%{k}%", f"%{k}%"))
            hits |= {r[0] for r in cur.fetchall()}
        return sorted(hits)


def _present(**kw):
    """Like dict(), but leaves out empty fields - as the ClinicalTrials.gov API does."""
    return {k: v for k, v in kw.items() if v not in (None, "", [])}


def _alnum(x):
    return re.sub(r"[^a-z0-9]", "", (x or "").lower())


def search_keys(names):
    return sorted({k for k in (_alnum(n) for n in names) if len(k) >= 5})


def prefetch_search(src, names):
    """Flat files: run the intervention-name search for many names in ONE pass over the two intervention files."""
    src._search = getattr(src, "_search", {})
    if not src.flat:
        return
    keys = search_keys(names)
    hits = {k: set() for k in keys}
    for t in ("interventions", "intervention_other_names"):
        for r in _read(src.flat / f"{t}.txt"):
            nm = _alnum(r.get("name"))
            for k in keys:
                if k in nm:
                    hits[k].add(r["nct_id"])
    src._search.update({k: sorted(v) for k, v in hits.items()})


def freeze_build(src, snap):
    """Mac step: freeze everything an AACT build needs (searches, trial records, MeSH ids) and report how AACT's
    trial matching compares with the committed graph. Does NOT write the graph (that is rebuilt from the frozen
    records). Returns rows dict(program, trial_query, ctgov_ncts, aact_ncts, same)."""
    import seed_kg as sk
    sk.TRIAL_SOURCE, sk.AACT_SRC = "aact", src
    progs = sk.parse_inventory(open(sk.DATASET).read())
    print("1/5 searching intervention names (each drug with its ChEMBL names)...")
    prefetch_search(src, [n for p in progs for d in p["drugs"]
                          for n in ([d] + sk.ALIASES[sk.norm(d)] if sk.norm(d) in sk.ALIASES
                                    else sk.chembl_synonyms(d, snap) + sk.confirmed_registry_names(d, snap))])
    ids = sorted({n for v in src._search.values() for n in v})
    print(f"2/5 reading study titles and ids for {len(ids)} candidate trials...")
    src.prefetch(ids, tables=["studies", "id_information"])
    found = set()
    for p in progs:
        for t in p["trials"]:
            for d in p["drugs"][:2]:
                hits = sk.resolve_trial(t, d, snap)
                found |= {h["nct"] for h in hits}
                if hits:
                    break
    found |= {ov["nct"] for ov in sk.load_overrides().values()}
    print(f"3/5 reading full records for {len(found)} matched trials...")
    src.prefetch(sorted(found))
    print("4/5 running the graph builder on AACT (nothing is written to outputs/)...")
    kg = sk.build(sk.DATASET, snap)[0]
    print("5/5 comparing every trial record with the ClinicalTrials.gov API copy...")
    write_equivalence(compare_all(src, snap, sorted(n[4:] for n in kg.nodes if n.startswith("NCT:"))))
    new = {}
    for e in kg.edges.values():
        if e["predicate"] == "tmkg:has_trial":
            new.setdefault(e["subject"], set()).add(e["object"])
    old = {}
    with open(ROOT / "outputs" / "kg_edges.csv", encoding="utf-8") as f:
        for e in csv.DictReader(f):
            if e["predicate"] == "tmkg:has_trial":
                old.setdefault(e["subject"], set()).add(e["object"])
    return [dict(program=p, ctgov=" ".join(sorted(old.get(p, []))), aact=" ".join(sorted(new.get(p, []))),
                 same=old.get(p, set()) == new.get(p, set())) for p in sorted(set(old) | set(new))]


def find_flatfiles(folder):
    """The folder holding studies.txt (the unzipped export may sit one or two folders deeper)."""
    root = Path(folder).expanduser()
    if (root / "studies.txt").exists():
        return root
    hits = sorted(root.glob("**/studies.txt"))
    if not hits:
        raise FileNotFoundError(f"no studies.txt under {root} - unzip the AACT pipe-delimited export there")
    return hits[0].parent


def _read(path):
    csv.field_size_limit(sys.maxsize)                # some AACT text fields are very long
    with open(path, encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f, delimiter="|")


def _sorted(table, rows):
    """Rows in a fixed order: by trial, then AACT row id (numerically: ids preserve the registry's own order)."""
    key = [c for c in ("nct_id", "id", "outcome_id", "ctgov_group_code", "name", "title") if c in TABLES[table]]
    num = lambda v: (0, int(v), "") if isinstance(v, int) or (isinstance(v, str) and v.isdigit()) else \
        (1, 0, json.dumps(v, default=str))
    return sorted(rows, key=lambda x: [num(x.get(c)) for c in key] + [(1, 0, json.dumps(x, sort_keys=True, default=str))])


def mesh_id(snap, term):
    """MeSH descriptor id for an exact MeSH heading (NLM lookup), or None."""
    r = snap.get("mesh", MESH_LOOKUP, {"label": term, "match": "exact", "limit": 5})
    ids = [x["resource"].rsplit("/", 1)[-1] for x in (r.body or []) if x.get("label") == term]
    return ids[0] if len(ids) == 1 else None


def _by(rows, key):
    out = {}
    for r in rows:
        out.setdefault(r[key], []).append(r)
    return out


def study(src, nct, mesh=None):
    """One trial as a CT.gov-v2-shaped dict (only the fields the pipeline reads). mesh(term) -> MeSH id.
    Returns (study or None, snapshot Response of the frozen AACT record)."""
    resp, T = src.record(nct)
    if not T["studies"]:
        return None, resp
    s = T["studies"][0]
    groups = {g["id"]: g for g in T["design_groups"]}
    arm_of = _by(T["design_group_interventions"], "intervention_id")
    other = _by(T["intervention_other_names"], "intervention_id")
    interventions = [dict(type=enum(i["intervention_type"]), name=i["name"],
                          otherNames=[o["name"] for o in other.get(i["id"], [])],
                          armGroupLabels=[groups[a["design_group_id"]]["title"] for a in arm_of.get(i["id"], [])
                                          if a["design_group_id"] in groups])
                     for i in T["interventions"]]
    om = {}
    for kind, field in (("PRIMARY", "primaryOutcomes"), ("SECONDARY", "secondaryOutcomes"), ("OTHER", "otherOutcomes")):
        om[field] = [dict(measure=o["measure"], timeFrame=o["time_frame"]) for o in T["design_outcomes"]
                     if enum(o["outcome_type"]) == kind]
    protocol = {
        "identificationModule": dict(nctId=nct, acronym=s["acronym"], briefTitle=s["brief_title"],
                                     officialTitle=s["official_title"],
                                     secondaryIdInfos=[dict(id=i["id_value"]) for i in T["id_information"]
                                                       if i["id_type"] != "nct_alias" and i["id_value"]]),
        "statusModule": dict(overallStatus=enum(s["overall_status"]), whyStopped=s["why_stopped"],
                             startDateStruct=dict(date=month_year(s.get("start_month_year")) or s["start_date"]),
                             primaryCompletionDateStruct=dict(date=month_year(s.get("primary_completion_month_year"))
                                                              or s["primary_completion_date"]),
                             resultsFirstPostDateStruct=dict(date=s["results_first_posted_date"])),
        "sponsorCollaboratorsModule": dict(leadSponsor=dict(name=next(
            (x["name"] for x in T["sponsors"] if enum(x["lead_or_collaborator"]) == "LEAD"), None))),
        "designModule": dict(phases=phases(s["phase"]), enrollmentInfo=dict(count=_int(s["enrollment"]))),
        "conditionsModule": dict(conditions=[c["name"] for c in T["conditions"]]),
        "ipdSharingStatementModule": dict(ipdSharing=enum(s["plan_to_share_ipd"]),
                                          description=s["plan_to_share_ipd_description"],
                                          infoTypes=[enum(x["name"]) for x in T["ipd_information_types"]] or None,
                                          timeFrame=s["ipd_time_frame"], accessCriteria=s["ipd_access_criteria"],
                                          url=s["ipd_url"]),
        "referencesModule": dict(references=[dict(pmid=_s(r["pmid"]), type=enum(r["reference_type"]),
                                                  citation=r["citation"]) for r in T["study_references"]]),
        "armsInterventionsModule": dict(armGroups=[dict(label=g["title"], type=enum(g["group_type"]))
                                                   for g in T["design_groups"]], interventions=interventions),
        "outcomesModule": om,
    }
    out = {"protocolSection": protocol, "hasResults": bool(T["outcomes"] or T["reported_event_totals"])}
    meshes = [m["mesh_term"] for m in T["browse_conditions"] if enum(m["mesh_type"]) in ("MESH_LIST", None)]
    if meshes:
        out["derivedSection"] = {"conditionBrowseModule": {"meshes": [dict(id=mesh(t) if mesh else None, term=t)
                                                                      for t in meshes]}}
    if T["outcomes"]:
        out["resultsSection"] = {"outcomeMeasuresModule": {"outcomeMeasures": _outcome_measures(T)}}
    if T["reported_event_totals"]:
        out.setdefault("resultsSection", {})["adverseEventsModule"] = _adverse_events(T)
    docs = [dict(typeAbbrev=d["document_type"], hasProtocol=d["has_protocol"], hasSap=d["has_sap"],
                 hasIcf=d["has_icf"], date=d["document_date"], url=d["url"]) for d in T["provided_documents"]]
    if docs:
        out["documentSection"] = {"largeDocumentModule": {"largeDocs": docs}}
    return out, resp


def _outcome_measures(T):
    titles = {(g["outcome_id"], g["ctgov_group_code"]): g["title"] for g in T["result_groups"]
              if enum(g["result_type"]) == "OUTCOME"}
    meas, counts = _by(T["outcome_measurements"], "outcome_id"), _by(T["outcome_counts"], "outcome_id")
    ana = _by(T["outcome_analyses"], "outcome_id")
    agroups = _by(T["outcome_analysis_groups"], "outcome_analysis_id")
    out = []
    for o in sorted(T["outcomes"], key=lambda o: _int(o["id"])):
        codes = sorted({m["ctgov_group_code"] for m in meas.get(o["id"], [])} |
                       {c["ctgov_group_code"] for c in counts.get(o["id"], [])})
        classes = {}
        for m in meas.get(o["id"], []):
            classes.setdefault(m["classification"], {}).setdefault(m["category"], []).append(dict(
                groupId=m["ctgov_group_code"], value=_s(m["param_value"]), spread=_s(m["dispersion_value"]),
                lowerLimit=_s(m["dispersion_lower_limit"]), upperLimit=_s(m["dispersion_upper_limit"])))
        out.append(dict(
            type=enum(o["outcome_type"]), title=o["title"], timeFrame=o["time_frame"], paramType=enum(o["param_type"]),
            unitOfMeasure=o["units"],
            groups=[dict(id=c, title=titles.get((o["id"], c))) for c in codes],
            denoms=[dict(counts=[dict(groupId=c["ctgov_group_code"], value=_s(c["count"]))
                                 for c in counts.get(o["id"], []) if enum(c["scope"]) in ("OVERALL", "MEASURE", None)])],
            classes=[dict(title=cl, categories=[dict(title=ca, measurements=v) for ca, v in cats.items()])
                     for cl, cats in classes.items()],
            analyses=[dict(groupIds=sorted(g["ctgov_group_code"] for g in agroups.get(a["id"], [])),
                           pValue=(a["p_value_modifier"] or "") + _s(a["p_value"]) if a["p_value"] is not None else None,
                           statisticalMethod=a["method"], paramType=a["param_type"], paramValue=_s(a["param_value"]),
                           ciPctValue=_s(a["ci_percent"]), ciLowerLimit=_s(a["ci_lower_limit"]),
                           ciUpperLimit=_s(a["ci_upper_limit"]), nonInferiorityType=enum(a["non_inferiority_type"]))
                      for a in ana.get(o["id"], [])]))
    return out


def _adverse_events(T):
    titles = {g["ctgov_group_code"]: g["title"] for g in T["result_groups"] if enum(g["result_type"]) == "REPORTED_EVENT"}
    groups = {}
    for r in T["reported_event_totals"]:
        g = groups.setdefault(r["ctgov_group_code"], dict(id=r["ctgov_group_code"], title=titles.get(r["ctgov_group_code"])))
        kind = {"SERIOUS": "serious", "OTHER": "other", "DEATHS": "deaths"}.get(enum(r["event_type"]))
        if kind:
            g[f"{kind}NumAffected"], g[f"{kind}NumAtRisk"] = _int(r["subjects_affected"]), _int(r["subjects_at_risk"])
    tf = sorted({r["time_frame"] for r in T["reported_events"] if r["time_frame"]})
    return dict(timeFrame=tf[0] if tf else None, eventGroups=[groups[k] for k in sorted(groups)])


def search_studies(src, drug, names=None):
    """Same shape as GET /api/v2/studies?query.intr=drug (protocol identification + enrolment only), searching
    interventions for `names` (default [drug]; the builder passes the drug's ChEMBL names).
    Frozen as ONE response per drug (key + fetched_at for the has_trial edge)."""
    names = sorted(set(names or [drug]))
    def run():
        ids = src.search_raw(names)
        if not ids:
            return {"studies": []}
        st, idi = _by(src._query("studies", ids), "nct_id"), _by(_sorted("id_information", src._query("id_information", ids)), "nct_id")
        out = []
        for n in ids:
            if n not in st:
                continue
            s = st[n][0]
            out.append({"protocolSection": {
                "identificationModule": _present(nctId=n, acronym=s["acronym"], briefTitle=s["brief_title"],
                                                 officialTitle=s["official_title"],
                                             secondaryIdInfos=[dict(id=i["id_value"]) for i in idi.get(n, [])
                                                               if i["id_type"] != "nct_alias" and i["id_value"]]),
                "designModule": dict(enrollmentInfo=dict(count=_int(s["enrollment"])))}})
        return {"studies": out}
    return src.snap.call("aact", {"search_intervention": drug, "names": names,
                                  "tables": ["interventions", "intervention_other_names", "studies", "id_information"]}, run)


# ------------------------------------------------------------------ equivalence check
NUM = re.compile(r"^([<>=≤≥]?)\s*(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE]-?\d+)?)$")


def same_number(x):
    """'0.80' and '0.8' (and '-0.00' and '0.0') are the same value: AACT stores analysis results as numbers, which
    drops trailing zeros. Non-numbers are returned unchanged."""
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        x = str(x)
    m = NUM.match(x.strip()) if isinstance(x, str) else None
    if not m:
        return x
    v = float(m.group(2))
    return m.group(1) + repr(v + 0.0 if v != 0 else 0.0)


def same_text(x):
    """AACT flat files turn line breaks into '~' and drop double quotes; same words otherwise."""
    if not isinstance(x, str):
        return x
    return " ".join(x.replace("~", " ").replace('"', "").split())


def _nums(d, keys):
    return {k: (same_number(v) if k in keys else v) for k, v in d.items()}


def extract(s):
    """Everything the pipeline takes from one record, in comparable form. Numbers are compared by value; lists the
    graph keeps in order (conditions, outcomes -> outcome ids P1/S1.., measurements, arms, AE groups) are compared
    IN ORDER, because the order decides ids and what the builder tries first."""
    f = ctgov.study_facts(s)
    for k in ("why_stopped", "ipd_description", "ipd_access_criteria", "ipd_time_frame"):
        f[k] = same_text(f[k])
    f["ipd_info_types"] = list(f["ipd_info_types"] or [])
    meshes = sorted((m.get("id"), m.get("term")) for m in (s or {}).get("derivedSection", {})
                    .get("conditionBrowseModule", {}).get("meshes", []))
    outs = [json.dumps([o["oid"], o["kind"], o["title"], o["time_frame"], o["source"], o.get("unit"), o.get("param_type"),
                        [[same_number(a[k]) for k in ("p_value", "param_value", "ci_lower", "ci_upper", "ci_pct")]
                         + [sorted(a["groups"] or []), a["method"]] for a in o.get("analyses", [])],
                        list((o.get("groups") or {}).items()),
                        [_nums(d, {"n"}) for d in o.get("denoms", [])],
                        [_nums(m, {"value", "spread", "lower", "upper"}) for m in o.get("measurements", [])]],
                       sort_keys=True) for o in ctgov.outcomes(s)]
    ae = ctgov.adverse_event_summary(s)
    return dict(facts=f, meshes=meshes, outcomes=outs,
                arms=[json.dumps(a, sort_keys=True) for a in ctgov.arm_interventions(s)],
                references=sorted(json.dumps(r, sort_keys=True) for r in ctgov.references(s)),
                ae=[json.dumps(_nums(g, {k for k in g if k.endswith(("affected", "at_risk"))}), sort_keys=True)
                    for g in ae.get("ae_groups", [])], ae_time_frame=ae.get("ae_time_frame"))


def compare(ct, aa):
    """-> [dict(field, ctgov, aact)] for every difference that would change the graph."""
    a, b = extract(ct), extract(aa)
    diffs = []
    for k in a["facts"]:
        if a["facts"][k] != b["facts"][k]:
            diffs.append(dict(field=f"facts.{k}", ctgov=a["facts"][k], aact=b["facts"][k]))
    for k in ("meshes", "outcomes", "arms", "references", "ae", "ae_time_frame"):
        if a[k] != b[k]:
            if isinstance(a[k], list) and isinstance(b[k], list) and sorted(a[k]) == sorted(b[k]):
                diffs.append(dict(field=k, ctgov="(same items, different order)", aact=""))
                continue
            only_a = [x for x in a[k] if x not in b[k]] if isinstance(a[k], list) else a[k]
            only_b = [x for x in b[k] if x not in a[k]] if isinstance(b[k], list) else b[k]
            diffs.append(dict(field=k, ctgov=only_a, aact=only_b))
    return diffs


def compare_all(src, snap, ncts):
    """AACT vs the frozen ClinicalTrials.gov API record for each trial -> rows dict(nct, field, ctgov, aact)."""
    import seed_kg as sk
    from snapshot import Snapshot
    ct_snap = Snapshot(mode="offline")
    rows = []
    for nct in ncts:
        ct = sk.fetch(f"{sk.CT}/{nct}", None, ct_snap).body
        aa, _ = study(src, nct, mesh=lambda t: mesh_id(snap, t))
        d = compare(ct, aa) if aa else [dict(field="record", ctgov="present", aact="missing")]
        rows += [dict(nct=nct, **x) for x in d] or [dict(nct=nct, field="(identical)", ctgov="", aact="")]
    return rows


def write_equivalence(rows, out=ROOT / "outputs" / "aact_equivalence.csv"):
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, ["nct", "field", "ctgov", "aact"], lineterminator="\n")
        w.writeheader()
        w.writerows([{k: (json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (list, dict)) else v)
                      for k, v in r.items()} for r in rows])
    same = sum(r["field"] == "(identical)" for r in rows)
    print(f"{same} trials identical, {len({r['nct'] for r in rows}) - same} with differences -> {out}")


def _columns(src):
    """{(table, column)} present in this AACT copy (database or flat-file headers); cached on the source."""
    if getattr(src, "_cols", None) is None:
        if src.flat:
            have = set()
            for t in TABLES:
                f = src.flat / f"{t}.txt"
                if f.exists():
                    with open(f, encoding="utf-8", newline="") as fh:
                        have |= {(t, c) for c in next(csv.reader(fh, delimiter="|"))}
                    have.add((t, "(file)"))
        else:
            cur = src._pg().cursor()
            cur.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'ctgov'")
            have = {(t, c) for t, c in cur.fetchall()}
            have |= {(t, "(file)") for t, _ in have}
        src._cols = have
    return src._cols


def check_schema(src, optional=False):
    """REQUIRED columns this module reads that are missing in AACT -> [(table, column)].
    optional=True lists the missing OPTIONAL ones instead (the loader then falls back, see OPTIONAL)."""
    have = _columns(src)
    out = []
    for t, cols in TABLES.items():
        if src.flat and (t, "(file)") not in have:
            out += [] if optional else [(t, "(whole file missing)")]
            continue
        out += [(t, c) for c in cols if (t, c) not in have and (((t, c) in OPTIONAL) == optional)]
    return out


if __name__ == "__main__":
    from snapshot import Snapshot
    from kg_query import KGIndex
    import seed_kg as sk
    snap = Snapshot()
    src = Source(snap)
    if sys.argv[1:] == ["check-schema"]:
        print("Checking", f"AACT files in {src.flat}" if src.flat else "the AACT database")
        missing = check_schema(src)
        print("AACT schema OK" if not missing else "Missing in AACT (edit TABLES):\n  " +
              "\n  ".join(f"{t}.{c}" for t, c in missing))
        for t, c in check_schema(src, optional=True):
            print(f"note: optional column {t}.{c} not found" +
                  (" - full dates are used instead" if "month_year" in c else " - not needed"))
    elif sys.argv[1:] == ["freeze"]:
        rows = freeze_build(src, snap)
        out = ROOT / "outputs" / "aact_trial_matching.csv"
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, list(rows[0]), lineterminator="\n")
            w.writeheader(); w.writerows(rows)
        diff = [r for r in rows if not r["same"]]
        print(f"{len(rows) - len(diff)} programs match the same trials in AACT, {len(diff)} differ -> {out}")
        for r in diff:
            print(f"  {r['program']}: ClinicalTrials.gov {r['ctgov'] or '-'} | AACT {r['aact'] or '-'}")
    elif sys.argv[1:] == ["compare"]:
        ncts = sorted(n[4:] for n, v in KGIndex().nodes.items() if v["category"] == "biolink:ClinicalTrial")
        if src.flat:
            print(f"Reading AACT files in {src.flat} once for {len(ncts)} trials (a few minutes)...")
            src.prefetch(ncts)
        write_equivalence(compare_all(src, snap, ncts))
    else:
        print(__doc__)
