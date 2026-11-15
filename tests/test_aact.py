"""AACT loader: round trip CT.gov record -> AACT-style flat files (AACT spellings) -> study() -> nothing lost.
This proves the mapping logic; `python pipelines/aact.py check-schema` and `compare` prove it on the real AACT."""
import csv
import pytest
import aact, seed_kg as sk
from snapshot import Snapshot, MissingFromSnapshot

TRIALS = ["NCT02446912", "NCT02105961", "NCT02525939"]     # TULIP-1, METREO, dal-GenE (results + AE posted)


def words(x):
    return None if x is None else x.replace("_", " ").capitalize()


MONTH = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
         "November", "December"]


def month_end(d):
    """AACT's *_date column: a month-only date gets the last day of the month filled in."""
    import calendar
    if d and len(d) == 7:
        y, m = int(d[:4]), int(d[5:])
        return f"{d}-{calendar.monthrange(y, m)[1]:02d}"
    return d


def spelled(d):
    """AACT's *_month_year column: 'July 2013' / 'July 15, 2013'."""
    if not d:
        return None
    y, m = d[:4], MONTH[int(d[5:7]) - 1]
    return f"{m} {int(d[8:])}, {y}" if len(d) == 10 else f"{m} {y}"


def to_aact(nct, s, T):
    """Reverse mapping in AACT's own spelling (title-case words, 'Phase 2/Phase 3', numbers as numbers)."""
    p = s["protocolSection"]
    idm, st, ds, ipd = p["identificationModule"], p["statusModule"], p.get("designModule", {}), p.get("ipdSharingStatementModule", {})
    T["studies"].append(dict(nct_id=nct, acronym=idm.get("acronym"), brief_title=idm.get("briefTitle"),
                             official_title=idm.get("officialTitle"), overall_status=words(st.get("overallStatus")),
                             why_stopped=st.get("whyStopped"),
                             phase="/".join(x.replace("PHASE", "Phase ") for x in ds.get("phases", [])),
                             enrollment=ds.get("enrollmentInfo", {}).get("count"),
                             start_date=month_end(st.get("startDateStruct", {}).get("date")),
                             primary_completion_date=month_end(st.get("primaryCompletionDateStruct", {}).get("date")),
                             start_month_year=spelled(st.get("startDateStruct", {}).get("date")),
                             primary_completion_month_year=spelled(st.get("primaryCompletionDateStruct", {}).get("date")),
                             results_first_posted_date=st.get("resultsFirstPostDateStruct", {}).get("date"),
                             plan_to_share_ipd=words(ipd.get("ipdSharing")), plan_to_share_ipd_description=ipd.get("description"),
                             ipd_time_frame=ipd.get("timeFrame"), ipd_access_criteria=ipd.get("accessCriteria"),
                             ipd_url=ipd.get("url")))
    T["id_information"] += [dict(nct_id=nct, id_type="secondary_id", id_value=i["id"]) for i in idm.get("secondaryIdInfos", [])]
    T["sponsors"].append(dict(nct_id=nct, lead_or_collaborator="lead",
                              name=p["sponsorCollaboratorsModule"]["leadSponsor"]["name"]))
    T["conditions"] += [dict(nct_id=nct, name=c) for c in p.get("conditionsModule", {}).get("conditions", [])]
    T["browse_conditions"] += [dict(nct_id=nct, mesh_term=m["term"], mesh_type="mesh-list")
                               for m in s.get("derivedSection", {}).get("conditionBrowseModule", {}).get("meshes", [])]
    gid = {}
    for g in p["armsInterventionsModule"].get("armGroups", []):
        gid[g["label"]] = len(T["design_groups"]) + 1
        T["design_groups"].append(dict(id=gid[g["label"]], nct_id=nct, group_type=words(g.get("type")), title=g["label"]))
    for i in p["armsInterventionsModule"].get("interventions", []):
        iid = len(T["interventions"]) + 1
        T["interventions"].append(dict(id=iid, nct_id=nct, intervention_type=words(i["type"]), name=i["name"]))
        T["intervention_other_names"] += [dict(id=len(T["intervention_other_names"]) + k + 1, nct_id=nct,
                                               intervention_id=iid, name=o) for k, o in enumerate(i.get("otherNames", []))]
        T["design_group_interventions"] += [dict(nct_id=nct, design_group_id=gid[l], intervention_id=iid)
                                            for l in i.get("armGroupLabels", [])]
    for f, k in (("primaryOutcomes", "primary"), ("secondaryOutcomes", "secondary"), ("otherOutcomes", "other")):
        T["design_outcomes"] += [dict(nct_id=nct, outcome_type=k, measure=o["measure"], time_frame=o.get("timeFrame"))
                                 for o in p.get("outcomesModule", {}).get(f, [])]
    T["study_references"] += [dict(nct_id=nct, pmid=r.get("pmid"), reference_type=(r.get("type") or "").lower(),
                                   citation=r.get("citation")) for r in p.get("referencesModule", {}).get("references", [])]
    T["ipd_information_types"] += [dict(nct_id=nct, name=words(x)) for x in ipd.get("infoTypes", []) or []]
    res = s.get("resultsSection", {})
    for m in res.get("outcomeMeasuresModule", {}).get("outcomeMeasures", []):
        oid = len(T["outcomes"]) + 1
        T["outcomes"].append(dict(id=oid, nct_id=nct, outcome_type=words(m["type"]).replace("Post hoc", "Post-Hoc"),
                                  title=m["title"], time_frame=m.get("timeFrame"), param_type=words(m.get("paramType")),
                                  units=m.get("unitOfMeasure")))
        T["result_groups"] += [dict(id=len(T["result_groups"]) + 1, nct_id=nct, ctgov_group_code=g["id"],
                                    result_type="Outcome", title=g.get("title"), outcome_id=oid) for g in m.get("groups", [])]
        T["outcome_counts"] += [dict(nct_id=nct, outcome_id=oid, ctgov_group_code=c["groupId"], scope="Measure",
                                     count=c.get("value")) for d in m.get("denoms", []) for c in d.get("counts", [])]
        T["outcome_measurements"] += [dict(nct_id=nct, outcome_id=oid, ctgov_group_code=v.get("groupId"),
                                           classification=c.get("title"), category=cat.get("title"),
                                           param_value=v.get("value"), dispersion_value=v.get("spread"),
                                           dispersion_lower_limit=v.get("lowerLimit"),
                                           dispersion_upper_limit=v.get("upperLimit"))
                                      for c in m.get("classes", []) for cat in c.get("categories", [])
                                      for v in cat.get("measurements", [])]
        for a in m.get("analyses", []):
            aid = len(T["outcome_analyses"]) + 1
            pv = a.get("pValue")
            mod = pv[0] if pv and pv[0] in "<>=" else None
            T["outcome_analyses"].append(dict(id=aid, nct_id=nct, outcome_id=oid, non_inferiority_type=words(a.get("nonInferiorityType")),
                                              param_type=a.get("paramType"), param_value=a.get("paramValue"),
                                              ci_percent=a.get("ciPctValue"), ci_lower_limit=a.get("ciLowerLimit"),
                                              ci_upper_limit=a.get("ciUpperLimit"), p_value_modifier=mod,
                                              p_value=pv[1:] if mod else pv, method=a.get("statisticalMethod")))
            T["outcome_analysis_groups"] += [dict(nct_id=nct, outcome_analysis_id=aid, ctgov_group_code=g)
                                             for g in a.get("groupIds", [])]
    ae = res.get("adverseEventsModule")
    if ae:
        for g in ae.get("eventGroups", []):
            T["result_groups"].append(dict(id=len(T["result_groups"]) + 1, nct_id=nct, ctgov_group_code=g["id"],
                                           result_type="Reported Event", title=g.get("title"), outcome_id=None))
            for k in ("serious", "other", "deaths"):
                if g.get(f"{k}NumAtRisk") is not None:
                    T["reported_event_totals"].append(dict(nct_id=nct, ctgov_group_code=g["id"], event_type=k.capitalize(),
                                                           subjects_affected=g.get(f"{k}NumAffected"),
                                                           subjects_at_risk=g.get(f"{k}NumAtRisk")))
            T["reported_events"].append(dict(nct_id=nct, ctgov_group_code=g["id"], time_frame=ae.get("timeFrame")))


@pytest.fixture(scope="module")
def flat(tmp_path_factory):
    d = tmp_path_factory.mktemp("aact")
    ct = Snapshot(mode="offline")
    T = {t: [] for t in aact.TABLES}
    records = {n: sk.fetch(f"{sk.CT}/{n}", None, ct).body for n in TRIALS}
    for n, s in records.items():
        to_aact(n, s, T)
    for t, rows in T.items():                       # AACT row ids follow insertion (= registry) order
        for i, r in enumerate(rows, 1):
            r.setdefault("id", i)
    for t, cols in aact.TABLES.items():
        with open(d / f"{t}.txt", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, cols, delimiter="|")
            w.writeheader()
            w.writerows([{c: ("" if r.get(c) is None else r.get(c)) for c in cols} for r in T[t]])
    return d, records


def test_round_trip_loses_nothing_the_pipeline_uses(flat, tmp_path):
    d, records = flat
    src = aact.Source(Snapshot(tmp_path, mode="live"), flatfiles=d)
    meshes = {m["term"]: m["id"] for s in records.values()
              for m in s.get("derivedSection", {}).get("conditionBrowseModule", {}).get("meshes", [])}
    for n, ct in records.items():
        aa, resp = aact.study(src, n, mesh=meshes.get)
        assert aact.compare(ct, aa) == [], n
        assert resp.source == "aact" and resp.key


def test_records_are_frozen_and_replay_offline(flat, tmp_path):
    d, records = flat
    aact.study(aact.Source(Snapshot(tmp_path, mode="live"), flatfiles=d), TRIALS[0])
    again, _ = aact.study(aact.Source(Snapshot(tmp_path, mode="offline"), flatfiles=tmp_path / "nowhere"), TRIALS[0])
    assert again["protocolSection"]["identificationModule"]["nctId"] == TRIALS[0]
    with pytest.raises(MissingFromSnapshot):
        aact.study(aact.Source(Snapshot(tmp_path, mode="offline"), flatfiles=d), TRIALS[1])


def test_search_matches_intervention_and_other_names(flat, tmp_path):
    d, _ = flat
    r = aact.search_studies(aact.Source(Snapshot(tmp_path, mode="live"), flatfiles=d), "anifrolumab")
    assert [s["protocolSection"]["identificationModule"]["nctId"] for s in r.body["studies"]] == ["NCT02446912"]


def test_enum_spellings():
    assert aact.enum("Active, not recruiting") == "ACTIVE_NOT_RECRUITING" == aact.enum("ACTIVE_NOT_RECRUITING")
    assert aact.phases("Phase 2/Phase 3") == ["PHASE2", "PHASE3"] == aact.phases("PHASE2/PHASE3")
    assert aact.phases("Early Phase 1") == ["EARLY_PHASE1"] and aact.phases("Not Applicable") == ["NA"]


def test_builder_switch_and_aact_provenance(flat, tmp_path, monkeypatch):
    import provenance
    from kg_schema import KG
    d, records = flat
    monkeypatch.setattr(sk, "TRIAL_SOURCE", "aact")
    monkeypatch.setenv("AACT_FLATFILES", str(d))
    monkeypatch.setattr(aact, "mesh_id", lambda snap, term: None)
    monkeypatch.setattr(sk, "chembl_synonyms", lambda name, snap=None: [name, "RO4607381"])   # no network in tests
    monkeypatch.setattr(sk, "confirmed_registry_names", lambda name, snap=None: [])
    monkeypatch.setattr(sk, "AACT_SRC", None)
    snap = Snapshot(tmp_path, mode="live")
    rec = sk.trial_record(TRIALS[0], snap)
    assert rec.source == "aact" and rec.body["protocolSection"]["identificationModule"]["nctId"] == TRIALS[0]
    hits = sk.resolve_trial("dal-GenE", "dalcetrapib", snap)          # same matching rules as with the API
    assert [(h["nct"], h["rule"]) for h in hits] == [("NCT02525939", "acronym_exact")]
    kg = KG()
    kg.add_node("P19", "tmkg:ClinicalTrialProgram")
    kg.add_node(f"NCT:{TRIALS[0]}", "biolink:ClinicalTrial")
    eid = kg.add_edge("P19", "tmkg:has_trial", f"NCT:{TRIALS[0]}", "ClinicalTrials.gov", TRIALS[0], "registry",
                      "2026-01-01", via="AACT")
    assert provenance.annotate(kg, Snapshot(mode="offline")) == []
    assert kg.edges[eid]["aggregator_knowledge_source"].startswith('["infores:aact"')


def test_flatfile_schema_check_prefetch_and_nested_folder(flat, tmp_path, monkeypatch):
    d, records = flat
    src = aact.Source(Snapshot(tmp_path / "a", mode="live"), flatfiles=d)
    assert aact.check_schema(src) == []
    direct, _ = aact.study(src, TRIALS[1])
    src2 = aact.Source(Snapshot(tmp_path / "b", mode="live"), flatfiles=d.parent)    # export one folder deeper
    assert src2.flat == d
    src2.prefetch(TRIALS)
    monkeypatch.setattr(aact, "_read", lambda path: (_ for _ in ()).throw(AssertionError("file re-read")))
    cached, _ = aact.study(src2, TRIALS[1])
    assert cached == direct
    head = (d / "studies.txt").read_text().splitlines()[0]
    (d / "studies.txt").write_text((d / "studies.txt").read_text().replace(head, head.replace("why_stopped", "stop_reason")))
    monkeypatch.undo()
    assert ("studies", "why_stopped") in aact.check_schema(aact.Source(Snapshot(tmp_path / "c"), flatfiles=d))
    (d / "studies.txt").write_text((d / "studies.txt").read_text().replace("stop_reason", "why_stopped", 1))


def test_flatfiles_setting_read_from_env_file(flat, tmp_path, monkeypatch):
    import nim
    d, _ = flat
    env = tmp_path / ".env"
    env.write_text(f"AACT_FLATFILES={d}\n")
    monkeypatch.delenv("AACT_FLATFILES", raising=False)
    monkeypatch.setattr(nim, "ROOT", tmp_path)
    monkeypatch.setattr(nim.load_env, "__defaults__", (env,))
    assert aact.Source(Snapshot(tmp_path / "s")).flat == d


def test_number_text_and_date_normalisation():
    assert aact.same_number("0.80") == aact.same_number("0.8") and aact.same_number("<.0001") == "<0.0001"
    assert aact.same_number("-0.00") == aact.same_number("0.0") and aact.same_number("n/a") == "n/a"
    assert aact.same_text('A "quoted"\n\nB') == aact.same_text("A quoted~B")
    assert aact.month_year("July 2013") == "2013-07" and aact.month_year("July 5, 2013") == "2013-07-05"
    assert aact.month_year("2013-07") == "2013-07" and aact.month_year("sometime") is None


def test_missing_month_year_columns_are_optional(flat, tmp_path):
    d, _ = flat
    src = aact.Source(Snapshot(tmp_path), flatfiles=d)
    assert aact.check_schema(src) == [] and aact.check_schema(src, optional=True) == []
    text = (d / "studies.txt").read_text()
    (d / "studies.txt").write_text(text.replace("start_month_year", "start_my", 1))
    src2 = aact.Source(Snapshot(tmp_path / "x"), flatfiles=d)
    assert aact.check_schema(src2) == [] and aact.check_schema(src2, optional=True) == [("studies", "start_month_year")]
    (d / "studies.txt").write_text(text)


def test_other_names_keep_registry_order_and_batch_search(flat, tmp_path):
    d, records = flat
    rows = [dict(id=str(i), nct_id="NCT1", intervention_id="1", name=n) for i, n in ((10, "b"), (9, "z"), (100, "a"))]
    assert [r["name"] for r in aact._sorted("intervention_other_names", rows)] == ["z", "b", "a"]   # 9 < 10 < 100
    src = aact.Source(Snapshot(tmp_path / "s", mode="live"), flatfiles=d)
    one = src.search_raw(["anifrolumab"])
    aact.prefetch_search(src, ["Anifrolumab", "Mepolizumab"])
    assert src._search["anifrolumab"] == one and src.search_raw(["Mepolizumab"]) == ["NCT02105961"]


def test_search_by_synonym_ignores_punctuation_and_short_names(flat, tmp_path):
    d, _ = flat
    src = aact.Source(Snapshot(tmp_path, mode="live"), flatfiles=d)
    assert src.search_raw(["ANIFRO-LUMAB"]) == ["NCT02446912"]           # punctuation ignored
    assert src.search_raw(["ab"]) == []                                  # too short to search on
    assert aact.search_keys(["AOC 1001", "AOC-1001", "IL5"]) == ["aoc1001"]


def test_confirmed_registry_names_come_from_drug_overrides(tmp_path, monkeypatch):
    f = tmp_path / "drug_overrides.csv"
    f.write_text("registry_name,chembl_id,justification\nTQJ230,CHEMBL4298202,test\nOTHER,CHEMBL1,test\n")
    monkeypatch.setattr(sk, "DRUG_OVERRIDES_FILE", f)
    monkeypatch.setattr(sk, "resolve_drug", lambda name, snap=None, **k: {"chembl_id": "CHEMBL4298202"})
    assert sk.confirmed_registry_names("Pelacarsen") == ["TQJ230"]


def test_trial_matching_tolerates_empty_fields(monkeypatch):
    from snapshot import Response
    body = {"studies": [{"protocolSection": {"identificationModule": {"nctId": "NCT02525939", "acronym": None,
            "briefTitle": "dal-GenE study", "officialTitle": None, "secondaryIdInfos": None}}}]}
    monkeypatch.setattr(sk, "trial_search", lambda a, d, snap=None: Response(body, "2026-10-05T00:00:00Z", "k", "aact"))
    assert [h["nct"] for h in sk.resolve_trial("dal-GenE", "dalcetrapib")] == ["NCT02525939"]


def test_compare_catches_reordering(flat):
    import copy
    _, records = flat
    ct = records["NCT02446912"]
    swapped = copy.deepcopy(ct)
    om = swapped["resultsSection"]["outcomeMeasuresModule"]["outcomeMeasures"]
    sec = [i for i, m in enumerate(om) if m["type"] == "SECONDARY"][:2]
    om[sec[0]], om[sec[1]] = om[sec[1]], om[sec[0]]               # same outcomes, other order -> other S1/S2 ids
    assert any(d["field"] == "outcomes" for d in aact.compare(ct, swapped))
    assert aact.compare(ct, copy.deepcopy(ct)) == []
