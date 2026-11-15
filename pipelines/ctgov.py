"""
ClinicalTrials.gov full-record parsers (API v2). Pure functions: they take the JSON of one study
(GET https://clinicaltrials.gov/api/v2/studies/<NCT>) and return plain Python data.
Nothing is inferred beyond what the record states.

- study_facts(study)       -> dict of trial-level facts (status, why stopped, sponsor, dates, IPD statement)
- references(study)        -> [dict(pmid, type, citation)]  (type: RESULT / BACKGROUND / DERIVED)
- arm_interventions(study) -> [dict(name, type, other_names, arm_types)]
- intervention_role(arm_types) -> 'comparator' if used only in comparator / no-intervention arms, else 'background'
- adverse_event_summary(study) -> dict(ae_time_frame, ae_groups=[per arm: serious / death / other counts]) or {}
- outcomes(study)          -> [dict(oid, kind, title, time_frame, source, ...results)]
"""
import re

COMPARATOR_ARMS = {"ACTIVE_COMPARATOR", "PLACEBO_COMPARATOR", "SHAM_COMPARATOR", "NO_INTERVENTION"}
DRUG_TYPES = {"DRUG", "BIOLOGICAL", "GENETIC", "COMBINATION_PRODUCT"}
_KIND = {"PRIMARY": "P", "SECONDARY": "S", "OTHER_PRE_SPECIFIED": "O", "POST_HOC": "O", "OTHER": "O"}


def _p(study):
    return (study or {}).get("protocolSection", {})


def study_facts(study):
    p = _p(study)
    st, ipd = p.get("statusModule", {}), p.get("ipdSharingStatementModule", {})
    return dict(
        status=st.get("overallStatus"), why_stopped=st.get("whyStopped"),
        start=st.get("startDateStruct", {}).get("date"),
        primary_completion=st.get("primaryCompletionDateStruct", {}).get("date"),
        results_first_posted=st.get("resultsFirstPostDateStruct", {}).get("date"),
        lead_sponsor=p.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name"),
        phase=",".join(p.get("designModule", {}).get("phases", []) or []),
        enrollment=p.get("designModule", {}).get("enrollmentInfo", {}).get("count"),
        has_results=bool((study or {}).get("hasResults")),
        conditions=p.get("conditionsModule", {}).get("conditions", []),
        ipd_sharing=ipd.get("ipdSharing"), ipd_description=ipd.get("description"),
        ipd_info_types=ipd.get("infoTypes"), ipd_time_frame=ipd.get("timeFrame"),
        ipd_access_criteria=ipd.get("accessCriteria"), ipd_url=ipd.get("url"),
    )


def references(study):
    out = []
    for r in _p(study).get("referencesModule", {}).get("references", []):
        pmid = str(r.get("pmid") or "").strip()
        out.append(dict(pmid=pmid if pmid.isdigit() else None, type=r.get("type"),
                        citation=r.get("citation")))
    return out


def arm_interventions(study):
    arms = {a.get("label"): a.get("type") for a in
            _p(study).get("armsInterventionsModule", {}).get("armGroups", [])}
    out = []
    for i in _p(study).get("armsInterventionsModule", {}).get("interventions", []):
        out.append(dict(name=i.get("name"), type=i.get("type"), other_names=i.get("otherNames", []),
                        arm_types=sorted({arms.get(l) or "UNKNOWN" for l in i.get("armGroupLabels", [])}),
                        arm_labels=list(i.get("armGroupLabels", []))))
    return out


def arms(study):
    """Registry arm groups in registry order -> [dict(aid 'A1'.., label, type, description, dose)]; dose = the
    dose written in the label/description (parse_dose), never inferred."""
    out = []
    for n, a in enumerate(_p(study).get("armsInterventionsModule", {}).get("armGroups", []), 1):
        text = " ".join(x for x in (a.get("label"), a.get("description")) if x)
        out.append(dict(aid=f"A{n}", label=a.get("label"), type=a.get("type"), description=a.get("description"),
                        dose=parse_dose(text)))
    return out


def registered_endpoints(study):
    """Registered (protocol) outcome measures -> [dict(eid 'P1'/'S1'/'O1', kind, measure, time_frame)]."""
    om, out, counters = _p(study).get("outcomesModule", {}), [], {}
    for field, kind in (("primaryOutcomes", "PRIMARY"), ("secondaryOutcomes", "SECONDARY"),
                        ("otherOutcomes", "OTHER_PRE_SPECIFIED")):
        for m in om.get(field, []):
            k = _KIND[kind]
            counters[k] = counters.get(k, 0) + 1
            out.append(dict(eid=f"{k}{counters[k]}", kind=kind, measure=m.get("measure"), time_frame=m.get("timeFrame")))
    return out


def parse_dose(text):
    """Every dose written in the text -> [dict(value, unit, text)] in order of appearance; [] when none.
    'Anifrolumab 300 mg' -> [{'value': 300.0, 'unit': 'mg', 'text': '300 mg'}]. Descriptive doses ('higher dose')
    are not doses."""
    return [dict(value=float(m.group(1)), unit=m.group(3), text=m.group(0))
            for m in DOSE_PARSE.finditer(text or "")]


DOSE = re.compile(r"\b\d+(\.\d+)?\s*(mg/kg|mg/m2|mg|mcg|µg|ug|g|ml|iu)\b", re.I)
DOSE_PARSE = re.compile(r"(?<![\w.])(\d+(\.\d+)?)\s*(mg/kg|mg/m2|mg|mcg|µg|ug|g|ml|iu)(?!\w|/[a-z])", re.I)
FORMS = re.compile(r"\b(capsules?|tablets?|injection|infusion|oral solution|oral|subcutaneous|intravenous|iv|sc)\b", re.I)


def split_combo(name):
    """Split a registry intervention name into drugs on '+' or ' and ', but never inside brackets.
    'Paclitaxel + Carboplatin' -> ['Paclitaxel', 'Carboplatin'];
    'Verubecestat (Part I and Part II)' -> ['Verubecestat (Part I and Part II)']."""
    parts, depth, cur, s, i = [], 0, "", name or "", 0
    while i < len(s):
        c = s[i]
        depth += (c == "(") - (c == ")")
        if depth == 0 and c == "+":
            parts.append(cur); cur = ""; i += 1; continue
        if depth == 0 and s[i:i + 5].lower() == " and ":
            parts.append(cur); cur = ""; i += 5; continue
        cur += c; i += 1
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def name_candidates(part):
    """Names to try (in this order) for one registry drug name; each must still match ChEMBL exactly.
    'Aducanumab (BIIB037)' -> ['Aducanumab', 'BIIB037'];
    'Trospium Chloride Capsules' -> ['Trospium Chloride Capsules', 'Trospium Chloride'];
    'Verubecestat 12 mg (Parts 1 and 2)' -> ['Verubecestat', 'Parts 1 and 2']."""
    inner = [x.strip() for x in re.findall(r"\(([^()]*)\)", part)]
    outer = re.sub(r"\([^()]*\)", " ", part)
    cleaned = " ".join(FORMS.sub(" ", DOSE.sub(" ", outer)).split())
    out = []
    for c in [part.strip(), " ".join(outer.split()), cleaned] + inner:
        if c and c not in out:
            out.append(c)
    return out


def intervention_role(arm_types):
    return "comparator" if arm_types and set(arm_types) <= COMPARATOR_ARMS else "background"


def _num(x):
    return x if x not in ("", None, "NA") else None


def outcomes(study):
    """Posted results if the trial has them (source='results'), else the registered outcome
    list (source='protocol'). oid = P1, P2.. / S1.. / O1.. in registry order."""
    res = (study or {}).get("resultsSection", {}).get("outcomeMeasuresModule", {}).get("outcomeMeasures", [])
    counters, out = {}, []
    if res:
        for m in res:
            k = _KIND.get(m.get("type"), "O")
            counters[k] = counters.get(k, 0) + 1
            groups = {g["id"]: g.get("title") for g in m.get("groups", [])}
            meas = []
            for c in m.get("classes", []):
                for cat in c.get("categories", []):
                    for v in cat.get("measurements", []):
                        meas.append(dict(group=v.get("groupId"), cls=c.get("title"), category=cat.get("title"),
                                         value=_num(v.get("value")), spread=_num(v.get("spread")),
                                         lower=_num(v.get("lowerLimit")), upper=_num(v.get("upperLimit"))))
            denoms = [dict(group=c.get("groupId"), n=c.get("value"))
                      for d in m.get("denoms", []) for c in d.get("counts", [])]
            analyses = [dict(groups=a.get("groupIds"), p_value=a.get("pValue"), method=a.get("statisticalMethod"),
                             param_type=a.get("paramType"), param_value=a.get("paramValue"),
                             ci_pct=a.get("ciPctValue"), ci_lower=a.get("ciLowerLimit"),
                             ci_upper=a.get("ciUpperLimit"), non_inferiority=a.get("nonInferiorityType"))
                        for a in m.get("analyses", [])]
            out.append(dict(oid=f"{k}{counters[k]}", kind=m.get("type"), title=m.get("title"),
                            time_frame=m.get("timeFrame"), source="results",
                            param_type=m.get("paramType"), unit=m.get("unitOfMeasure"),
                            groups=groups, denoms=denoms, measurements=meas, analyses=analyses))
        return out
    om = _p(study).get("outcomesModule", {})
    for field, kind in (("primaryOutcomes", "PRIMARY"), ("secondaryOutcomes", "SECONDARY"),
                        ("otherOutcomes", "OTHER_PRE_SPECIFIED")):
        for m in om.get(field, []):
            k = _KIND[kind]
            counters[k] = counters.get(k, 0) + 1
            out.append(dict(oid=f"{k}{counters[k]}", kind=kind, title=m.get("measure"),
                            time_frame=m.get("timeFrame"), source="protocol"))
    return out


def adverse_event_summary(study):
    """Per-arm safety counts as posted (no events listed individually). {} if no posted AE results."""
    ae = (study or {}).get("resultsSection", {}).get("adverseEventsModule")
    if not ae:
        return {}
    groups = [dict(group=g.get("id"), title=g.get("title"),
                   serious_affected=g.get("seriousNumAffected"), serious_at_risk=g.get("seriousNumAtRisk"),
                   deaths_affected=g.get("deathsNumAffected"), deaths_at_risk=g.get("deathsNumAtRisk"),
                   other_affected=g.get("otherNumAffected"), other_at_risk=g.get("otherNumAtRisk"))
              for g in ae.get("eventGroups", [])]
    return dict(ae_time_frame=ae.get("timeFrame"), ae_groups=groups)


def eligibility(study):
    """Eligibility module -> dict(criteria, sex, minimum_age, maximum_age, healthy_volunteers, std_ages,
    study_population); keys left out when the registry has no value."""
    e = _p(study).get("eligibilityModule", {})
    out = dict(criteria=e.get("eligibilityCriteria"), sex=e.get("sex"), minimum_age=e.get("minimumAge"),
               maximum_age=e.get("maximumAge"), healthy_volunteers=e.get("healthyVolunteers"),
               std_ages=e.get("stdAges"), study_population=e.get("studyPopulation"))
    return {k: v for k, v in out.items() if v not in (None, "", [])}


def adverse_event_terms(study, kind):
    """kind 'serious' | 'other' -> [dict(term, organ_system, vocabulary, assessment, arms=[dict(group, title,
    affected, at_risk, events)])] in registry order. Counts exactly as posted (MedDRA term text, no codes)."""
    ae = (study or {}).get("resultsSection", {}).get("adverseEventsModule", {})
    titles = {g["id"]: g.get("title") for g in ae.get("eventGroups", [])}
    out = []
    for e in ae.get(f"{kind}Events", []):
        out.append(dict(term=e.get("term"), organ_system=e.get("organSystem"), vocabulary=e.get("sourceVocabulary"),
                        assessment=e.get("assessmentType"),
                        arms=[dict(group=s.get("groupId"), title=titles.get(s.get("groupId")), affected=s.get("numAffected"),
                                   at_risk=s.get("numAtRisk"), events=s.get("numEvents")) for s in e.get("stats", [])]))
    return out


def baseline(study):
    """Baseline characteristics -> dict(population, groups{id: title}, measures=[dict(title, param, unit,
    dispersion, values=[dict(group, class, category, value, spread)])]) or None when not posted."""
    b = (study or {}).get("resultsSection", {}).get("baselineCharacteristicsModule")
    if not b:
        return None
    measures = []
    for m in b.get("measures", []):
        vals = [dict(group=v.get("groupId"), cls=c.get("title"), category=cat.get("title"), value=_num(v.get("value")),
                     spread=_num(v.get("spread")))
                for c in m.get("classes", []) for cat in c.get("categories", []) for v in cat.get("measurements", [])]
        measures.append(dict(title=m.get("title"), param=m.get("paramType"), unit=m.get("unitOfMeasure"),
                             dispersion=m.get("dispersionType"), values=vals))
    return dict(population=b.get("populationDescription"), groups={g["id"]: g.get("title") for g in b.get("groups", [])},
                measures=measures)
