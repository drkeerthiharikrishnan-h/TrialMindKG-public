"""
Review folder raw/: every document behind the 34 failed programs, arranged per program for a human
reviewer drafting the ground truth.

    python pipelines/raw_docs.py            # build/refresh raw/ (downloads what this computer can reach)

raw/
  README.md, index.csv                       what is there, where it came from, what is still missing
  P01_<drug - trial>/
    00_program.md                            the curated inventory row, linked trials, post-failure history
    trials/NCT.../registry_record.json       full ClinicalTrials.gov record (same response the graph uses)
    trials/NCT.../registry_summary.md        readable version: arms, outcomes + posted results, AEs, IPD, docs
    trials/NCT.../documents/*.pdf            protocol / SAP / consent PDFs posted on ClinicalTrials.gov
    publications/PMID_<id>.md                citation, links and abstract (Europe PMC)
    publications/PMID_<id>_fulltext.md       open-access full text (only when the article is open access)
    sources/NN_<host>.html|pdf               press releases / papers cited in the curated dataset

Nothing is summarised or interpreted: files are the source text, re-laid out. Documents that cannot be
fetched (paywall, site blocks scripts, network) are listed in index.csv with the reason; open their URL in
a browser and save the file into the same folder. PDFs are git-ignored (size); everything else is committed.

Functions (pure, tested): slug, abstract_text, registry_summary_md, publication_md, fulltext_md, program_md.
"""
import csv, json, re, sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ctgov, literature
from snapshot import Snapshot
from kg_query import KGIndex

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw"
CT_STUDY = "https://clinicaltrials.gov/api/v2/studies/"
INDEX_COLS = ["program", "trial", "category", "covers", "doc_type", "title", "source_url", "local_path",
              "status", "bytes", "sha256", "retrieved", "notes"]


# ---------------------------------------------------------------- pure renderers
def slug(text, n=50):
    """'Solanezumab — EXPEDITION3' -> 'Solanezumab_EXPEDITION3' (safe folder name)."""
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9+.-]+", "_", text or "")).strip("_")[:n]


def abstract_text(html):
    """Europe PMC abstracts carry light HTML (<h4>Background</h4>, <i>): keep headings, drop tags."""
    t = re.sub(r"<h4>(.*?)</h4>", r"\n\n**\1** ", html or "")
    return re.sub(r"<[^>]+>", "", t).strip()


def _cell(m):
    v = m.get("value")
    if v is None:
        return ""
    if m.get("lower") is not None or m.get("upper") is not None:
        return f"{v} [{m.get('lower')}, {m.get('upper')}]"
    return f"{v} ({m['spread']})" if m.get("spread") is not None else str(v)


def _outcome_md(o):
    out = [f"### {o['oid']} ({o['kind']}): {o['title']}", f"Time frame: {o.get('time_frame') or '-'}  ",
           f"Source: {'posted results' if o['source'] == 'results' else 'registered outcome (no posted results)'}"]
    if o["source"] != "results":
        return "\n".join(out)
    out.append(f"Measure: {o.get('param_type') or '-'}; unit: {o.get('unit') or '-'}\n")
    groups = o.get("groups") or {}
    gids = list(groups)
    if o.get("measurements") and gids:
        out.append("| row | " + " | ".join(groups[g] or g for g in gids) + " |")
        out.append("|---|" + "---|" * len(gids))
        rows = {}
        for m in o["measurements"]:
            rows.setdefault(" / ".join(x for x in (m.get("cls"), m.get("category")) if x) or "value", {})[m["group"]] = _cell(m)
        n = {d["group"]: d["n"] for d in o.get("denoms", [])}
        if n:
            out.append("| participants analysed | " + " | ".join(str(n.get(g, "")) for g in gids) + " |")
        for label, cells in rows.items():
            out.append(f"| {label} | " + " | ".join(cells.get(g, "") for g in gids) + " |")
    for a in o.get("analyses", []):
        names = " vs ".join(groups.get(g, g) for g in a.get("groups") or [])
        ci = f" ({a['ci_pct']}% CI {a.get('ci_lower')} to {a.get('ci_upper')})" if a.get("ci_pct") else ""
        ni = f"; {a['non_inferiority']}" if a.get("non_inferiority") else ""
        out.append(f"\n- Analysis ({names}): {a.get('param_type') or ''} {a.get('param_value') or ''}{ci}"
                   f"; p = {a.get('p_value') or 'not reported'}; method: {a.get('method') or '-'}{ni}")
    return "\n".join(out)


def registry_summary_md(study, documents=()):
    """Readable re-layout of one ClinicalTrials.gov record. documents: [(label, url, local path)]."""
    p = (study or {}).get("protocolSection", {})
    idm, f = p.get("identificationModule", {}), ctgov.study_facts(study)
    nct = idm.get("nctId")
    lines = [f"# {nct}: {idm.get('briefTitle')}", "",
             f"Registry page: https://clinicaltrials.gov/study/{nct}  ",
             "Source: ClinicalTrials.gov API v2 record (registry_record.json in this folder). Re-laid out, not edited.", "",
             "| Field | Value |", "|---|---|"]
    for k, v in [("Official title", idm.get("officialTitle")), ("Acronym", idm.get("acronym")),
                 ("Lead sponsor", f["lead_sponsor"]), ("Phase", f["phase"]), ("Overall status", f["status"]),
                 ("Why stopped", f["why_stopped"]), ("Start", f["start"]), ("Primary completion", f["primary_completion"]),
                 ("Enrollment", f["enrollment"]), ("Conditions", "; ".join(f["conditions"])),
                 ("Results posted", f["results_first_posted"] or ("yes" if f["has_results"] else "no"))]:
        lines.append(f"| {k} | {str(v if v not in (None, '') else '-').replace('|', '/')} |")
    lines += ["", "## Arms and interventions"]
    for a in p.get("armsInterventionsModule", {}).get("armGroups", []):
        lines.append(f"- **{a.get('label')}** ({a.get('type')}): {a.get('description') or ''} "
                     f"Interventions: {', '.join(a.get('interventionNames', []))}")
    desc = p.get("descriptionModule", {}).get("briefSummary")
    if desc:
        lines += ["", "## Brief summary", desc]
    lines += ["", "## Outcome measures"] + [_outcome_md(o) + "\n" for o in ctgov.outcomes(study)]
    ae = ctgov.adverse_event_summary(study)
    if ae:
        lines += ["## Adverse events (posted)", f"Time frame: {ae.get('ae_time_frame') or '-'}", "",
                  "| Group | Serious AE (affected / at risk) | Deaths (affected / at risk) | Other AE (affected / at risk) |",
                  "|---|---|---|---|"]
        for g in ae["ae_groups"]:
            lines.append(f"| {g['title']} | {g['serious_affected']} / {g['serious_at_risk']} | "
                         f"{g['deaths_affected']} / {g['deaths_at_risk']} | {g['other_affected']} / {g['other_at_risk']} |")
        lines.append("")
    lines += ["## Individual participant data (IPD) sharing statement",
              f"Plan: {f['ipd_sharing'] or 'not stated'}  ", f"Description: {f['ipd_description'] or '-'}  ",
              f"Access criteria: {f['ipd_access_criteria'] or '-'}  ", f"URL: {f['ipd_url'] or '-'}", "",
              "## References listed in the registry"]
    for r in ctgov.references(study):
        link = f" https://pubmed.ncbi.nlm.nih.gov/{r['pmid']}/" if r["pmid"] else ""
        lines.append(f"- [{r['type']}] {r['citation']}{link}")
    lines += ["", "## Documents posted on the registry"]
    lines += [f"- {lab}: {url} (local: {path})" for lab, url, path in documents] or ["- none"]
    return "\n".join(lines) + "\n"


def publication_md(key, meta, linked_from):
    """Citation, links and abstract for one record. key: 'PMID_<id>' or '<SRC>_<id>' (doc_discovery.epmc_key);
    meta: dict(title, authors, journal, year, doi, pmcid, open_access, abstract, category, fetched_at) or None."""
    m = meta or {}
    src, _, ident = key.partition("_")
    page = (f"https://pubmed.ncbi.nlm.nih.gov/{ident}/" if src == "PMID"
            else f"https://europepmc.org/article/{src}/{ident}")
    lines = [f"# {key.replace('_', ' ')}: {m.get('title') or '(no Europe PMC record)'}", "",
             f"- Type: {m.get('category') or 'publication'}",
             f"- Authors: {m.get('authors') or '-'}", f"- Journal / source: {m.get('journal') or '-'} ({m.get('year') or '-'})",
             f"- DOI: {('https://doi.org/' + m['doi']) if m.get('doi') else '-'}",
             f"- Record: {page}",
             f"- PMC: {('https://pmc.ncbi.nlm.nih.gov/articles/' + m['pmcid'] + '/') if m.get('pmcid') else '-'}",
             f"- Open access: {'yes (full text in ' + key + '_fulltext.md)' if m.get('open_access') and m.get('pmcid') else 'no'}",
             f"- Linked from: {'; '.join(linked_from)}",
             f"- Metadata: Europe PMC (retrieved {m.get('fetched_at') or '-'})", "", "## Abstract", "",
             abstract_text(m.get("abstract")) or "(no abstract in Europe PMC)"]
    return "\n".join(lines) + "\n"


def fulltext_md(xml_bytes):
    """JATS full-text XML (Europe PMC open access) -> readable markdown: title, abstract, sections,
    figure/table captions. Tables themselves are not reproduced (see the PDF/online article)."""
    root = ET.fromstring(xml_bytes)
    txt = lambda e: " ".join("".join(e.itertext()).split()) if e is not None else ""
    out = [f"# {txt(root.find('.//article-title'))}", "",
           "Open-access full text from Europe PMC (JATS XML), re-laid out without editing.", ""]
    ab = root.find(".//abstract")
    if ab is not None:
        out += ["## Abstract", txt(ab), ""]
    def sec(e, depth):
        title = e.find("title")
        if title is not None:
            out.append("#" * min(depth, 6) + " " + txt(title))
        for c in e:
            if c.tag == "p":
                out.append(txt(c) + "\n")
            elif c.tag == "sec":
                sec(c, depth + 1)
            elif c.tag in ("fig", "table-wrap"):
                out.append(f"*[{c.tag}] {txt(c.find('label'))} {txt(c.find('caption'))}*\n")
    body = root.find(".//body")
    if body is not None:
        for s in body:
            if s.tag == "sec":
                sec(s, 2)
            elif s.tag == "p":
                out.append(txt(s) + "\n")
    return "\n".join(out) + "\n"


def program_md(prog, trials, events):
    """prog: program node dict; trials: [(nct, title, match_rule)]; events: [DevelopmentEvent node dicts]."""
    lines = [f"# {prog['id']}: {prog.get('name')}", "",
             "Source: data/curated/failed_clinical_trials_2016_2026.md (curated inventory row).", "",
             "| Field | Value |", "|---|---|"]
    for k in ("year", "sponsor", "indication", "what_failed", "data_access", "source_url"):
        lines.append(f"| {k} | {str(prog.get(k) or '-').replace('|', '/')} |")
    lines += ["", "## Registry trials linked by the pipeline (confirm in the ground-truth workbook)"]
    lines += [f"- {n}: {t} (match rule: {r}) -> trials/{n}/" for n, t, r in trials] or ["- none"]
    if events:
        lines += ["", "## Curated history after the failure"]
        for e in events:
            lines.append(f"### {e['id']} ({e.get('kind')})")
            for k, v in e.items():
                if k not in ("id", "category", "kind", "name"):
                    lines.append(f"- {k}: {v}")
    return "\n".join(lines) + "\n"




# ---------------------------------------------------------------- checklist categories and statuses
# One column per category in raw/CATALOGUE.xlsx (order = column order).
CATEGORIES = [
    "Curated program summary", "Registry record", "Registry results posted", "Protocol",
    "Statistical analysis plan", "Informed consent form", "Other registries (EU etc.)", "Trial publications",
    "Open-access full text", "Supplementary files", "Supplementary data tables (xlsx/csv)", "Figures / images",
    "Conference abstracts", "Preprints", "Other literature mentioning the trial", "Press releases",
    "Lay summaries of results",
    "Company / investor presentations", "Conference presentations / posters", "FDA documents",
    "EMA / other regulator documents", "FAERS adverse-event reports", "Omics / expression / genomic datasets",
    "Derived data / code repositories", "Patient-level data access",
]
NONE_FOUND = "searched - none found"
DOWNLOADED, WRITTEN, TO_FETCH, BLOCKED, LINK_ONLY, CANDIDATE = (
    "downloaded", "written (from source data)", "to download on your computer",
    "could not download (paywall / site blocks scripts) - open in browser", "link only (dataset or access request)",
    "web-search candidate - verify, then download")


def status_of(res):
    """snapshot.download() result -> one of the statuses above."""
    st = res.get("status", "")
    if st.startswith("ok"):
        return DOWNLOADED
    if st.startswith("not reachable") or "offline" in st:
        return TO_FETCH
    return BLOCKED


def categorize_url(url):
    """Curated link -> checklist category, by site (deterministic, conservative)."""
    host = url.split("/")[2].lower() if "://" in url else ""
    if "fda.gov" in host:
        return "FDA documents"
    if "ema.europa.eu" in host:
        return "EMA / other regulator documents"
    if any(h in host for h in ("nejm.org", "nature.com", "sciencedirect.com", "wiley.com", "pubmed", "pmc.ncbi",
                               "thelancet.com", "jamanetwork.com", "bmj.com")):
        return "Trial publications"
    if any(h in host for h in ("a4studydata.org", "loni.usc.edu", "vivli.org", "yoda.yale.edu", "clinicalstudydatarequest")):
        return "Patient-level data access"
    if any(h in host for h in ("repository.cam.ac.uk", "zenodo.org", "figshare.com", "github.com", "datadryad.org")):
        return "Derived data / code repositories"
    if url.lower().endswith(".pdf") and any(k in url.lower() for k in ("presentation", "slides", "deck")):
        return "Company / investor presentations"
    return "Press releases"


def _ext(url):
    tail = url.lower().split("?")[0].rsplit("/", 1)[-1]
    for e in (".pdf", ".pptx", ".ppt", ".xlsx", ".csv", ".zip", ".docx"):
        if tail.endswith(e):
            return e
    return ".pdf" if "pdf" in tail else ".html"


WEB_CANDIDATES = ROOT / "data" / "curated" / "web_candidates.csv"


def _cites(rec, ncts):
    """Which of the trial's NCT numbers appear in a Europe PMC record (title/abstract/metadata)."""
    found = [n for n in ncts if n in json.dumps(rec)]
    return "cites " + "/".join(found) if found else "cites trial NCT (in full text)"


# ---------------------------------------------------------------- build
def build(raw=RAW, graph_snap=None, raw_snap=None, large=True):
    """Writes the review folder and returns the index rows.
    large=False: list supplementary files and figures without downloading them (they can total several GB)."""
    import doc_discovery as dd
    raw = Path(raw)
    kg = KGIndex()
    gsnap = graph_snap or Snapshot()                                  # same CT.gov records as the graph
    rsnap = raw_snap or Snapshot(ROOT / "data" / "snapshots_raw")     # literature, FDA, FAERS discovery
    prev = {}
    if (raw / "index.csv").exists():
        prev = {r["local_path"]: r for r in csv.DictReader(open(raw / "index.csv", encoding="utf-8"))}
    rows = []

    def record(pid, trial, category, doc_type, title, url, path, status, res=None, covers=None, notes=""):
        rel = str(Path(path).relative_to(raw)) if path else ""
        res = res or {}
        size = res.get("bytes") or (Path(path).stat().st_size if path and Path(path).exists() else "")
        when = res.get("fetched_at") or prev.get(rel, {}).get("retrieved") or ""
        rows.append(dict(program=pid, trial=trial, category=category, covers=covers or category, doc_type=doc_type,
                         title=(title or "")[:300], source_url=url or "", local_path=rel, status=status,
                         bytes=size, sha256=res.get("sha256") or "", retrieved=when, notes=notes))

    def fetch_file(pid, trial, category, doc_type, title, url, dest, covers=None, notes="", download=True):
        if not download:
            record(pid, trial, category, doc_type, title, url, dest, TO_FETCH, covers=covers,
                   notes=(notes + " " if notes else "") + "large file: run raw_docs.py without --no-large")
            return
        res = gsnap.download(url, dest)
        record(pid, trial, category, doc_type, title, url, dest, status_of(res), res, covers, notes)

    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    progs_md = {p["program_id"]: p for p in __import__("seed_kg").parse_inventory(
        open(ROOT / "data" / "curated" / "failed_clinical_trials_2016_2026.md").read())}
    web = list(csv.DictReader(open(WEB_CANDIDATES, encoding="utf-8"))) if WEB_CANDIDATES.exists() else []
    programs = sorted(n for n, v in kg.nodes.items() if v["category"] == "tmkg:ClinicalTrialProgram")

    for i, pid in enumerate(programs, 1):
        print(f"{i}/{len(programs)} {pid} {kg.nodes[pid].get('name', '')} ({len(rows)} items so far)", flush=True)
        prog, inv = kg.nodes[pid], progs_md[pid]
        folder = raw / f"{pid}_{slug(prog.get('name'))}"
        trials, events, urls, ncts, studies = [], [], [prog.get("source_url")], [], {}

        # ---- registry: record, summary, posted documents, other registries
        for te in kg.out(pid, "tmkg:has_trial"):
            nct = te["object"][4:]
            ncts.append(nct)
            trials.append((nct, kg.name(te["object"]), te.get("match_rule")))
            study = studies[nct] = gsnap.get("ctgov", CT_STUDY + nct).body
            tdir = folder / "trials" / nct
            write(tdir / "registry_record.json", json.dumps(study, indent=1, sort_keys=True, ensure_ascii=False))
            record(pid, nct, "Registry record", "registry record (JSON)", kg.name(te["object"]), CT_STUDY + nct,
                   tdir / "registry_record.json", WRITTEN)
            docs = []
            for d in study.get("documentSection", {}).get("largeDocumentModule", {}).get("largeDocs", []):
                url = f"https://cdn.clinicaltrials.gov/large-docs/{nct[-2:]}/{nct}/{d['filename']}"
                covers = ";".join(c for c, f in (("Protocol", "hasProtocol"), ("Statistical analysis plan", "hasSap"),
                                                 ("Informed consent form", "hasIcf")) if d.get(f)) or "Protocol"
                fetch_file(pid, nct, covers.split(";")[0], "registry document", d.get("label") or d.get("typeAbbrev"),
                           url, tdir / "documents" / d["filename"], covers=covers)
                docs.append((d.get("label") or d.get("typeAbbrev"), url, f"documents/{d['filename']}"))
            write(tdir / "registry_summary.md", registry_summary_md(study, docs))
            has_res = bool(study.get("hasResults"))
            record(pid, nct, "Registry results posted" if has_res else "Registry record", "registry summary (readable)",
                   kg.name(te["object"]), f"https://clinicaltrials.gov/study/{nct}", tdir / "registry_summary.md", WRITTEN,
                   covers="Registry record;Registry results posted" if has_res else "Registry record")
            for o in dd.other_registries(study):
                if o["link"]:
                    fetch_file(pid, nct, "Other registries (EU etc.)", f"registry entry ({o['type']})", o["id"], o["link"],
                               tdir / "other_registries" / f"{slug(o['type'])}_{slug(o['id'])}.html")
                elif o["type"] not in ("OTHER",) or o.get("domain"):
                    record(pid, nct, "Other registries (EU etc.)", f"secondary id ({o['type']})",
                           f"{o['id']} ({o.get('domain') or ''})", "", None, LINK_ONLY)
            f = ctgov.study_facts(study)
            if f["ipd_sharing"]:
                record(pid, nct, "Patient-level data access", "registry IPD sharing statement",
                       f"{f['ipd_sharing']}: {(f['ipd_description'] or '')[:200]}", f["ipd_url"] or
                       f"https://clinicaltrials.gov/study/{nct}", None, LINK_ONLY)
            for e in kg.out(te["object"], "tmkg:has_data_access_route"):
                events.append(kg.nodes[e["object"]])

        # ---- curated program summary and curated links
        for p in ("tmkg:has_post_failure_analysis", "tmkg:has_repurposing_pivot"):
            events += [kg.nodes[e["object"]] for e in kg.out(pid, p)]
        events = list({e["id"]: e for e in events}.values())
        for e in events:
            urls += json.loads(e.get("source_urls", "[]")) + json.loads(e.get("section_source_urls", "[]"))
        write(folder / "00_program.md", program_md(prog, trials, events))
        record(pid, "", "Curated program summary", "program summary (curated)", prog.get("name"), prog.get("source_url"),
               folder / "00_program.md", WRITTEN)
        if inv.get("data_access", "").strip().startswith(("R", "D")):
            record(pid, "", "Patient-level data access", "curated data-access code", inv["data_access"], "", None,
                   LINK_ONLY, notes="R = request route found; D = derived data/code (curated dataset)")
        seen = []
        for u in urls:
            if u and u not in seen and not re.search(r"pubmed\.ncbi|pmc\.ncbi|clinicaltrials\.gov", u):
                seen.append(u)
        for i, u in enumerate(seen, 1):
            fetch_file(pid, "", categorize_url(u), "curated source", u.split("/")[2], u,
                       folder / "sources" / f"{i:02d}_{slug(u.split('/')[2], 40)}{_ext(u)}")

        # ---- literature: trial publications (cite an NCT, no reviews) + graph-linked PMIDs
        lit = dd.trial_literature(rsnap, ncts)
        recs = {}
        for x in lit:
            k = dd.epmc_key(x)
            recs[k] = dict(title=x.get("title"), abstract=x.get("abstractText"), doi=x.get("doi"),
                           journal=((x.get("journalInfo") or {}).get("journal") or {}).get("title") or x.get("bookOrReportDetails", {}).get("publisher"),
                           year=x.get("pubYear"), pmcid=x.get("pmcid"), authors=x.get("authorString"),
                           open_access=x.get("isOpenAccess") == "Y", category=dd.category_of(x),
                           fetched_at=x["_fetched_at"], linked=[_cites(x, ncts)])
        linked = {}
        for e in kg.out(pid, "tmkg:has_source_publication"):
            linked.setdefault(e["object"][5:], []).append("curated inventory source")
        for te in kg.out(pid, "tmkg:has_trial"):
            for e in kg.out(te["object"], "tmkg:has_publication"):
                linked.setdefault(e["object"][5:], []).append(f"{te['object'][4:]} registry ({e.get('reference_type')})")
        for ev in events:
            for e in kg.out(ev["id"], "tmkg:has_source_publication"):
                linked.setdefault(e["object"][5:], []).append(f"curated {ev['id']}")
        missing = [p for p in linked if f"PMID_{p}" not in recs]
        for pm, m in literature.fetch_core(rsnap, missing).items():
            recs[f"PMID_{pm}"] = dict(m, category="publication", linked=[])
        for pm, where in linked.items():
            recs.setdefault(f"PMID_{pm}", dict(linked=[]))["linked"] = recs.get(f"PMID_{pm}", {}).get("linked", []) + where
        catmap = {"publication": "Trial publications", "conference abstract": "Conference abstracts",
                  "preprint": "Preprints"}
        pmcs = []
        for k in sorted(recs):
            m = recs[k]
            dest = folder / "publications" / f"{k}.md"
            write(dest, publication_md(k, m if m.get("title") else None, m.get("linked", [])))
            record(pid, "", catmap.get(m.get("category"), "Trial publications"), "publication record + abstract",
                   m.get("title"), f"https://europepmc.org/article/{k.replace('PMID_', 'MED/').replace('_', '/')}",
                   dest, WRITTEN if m.get("title") else BLOCKED, notes="; ".join(m.get("linked", [])))
            if not (m.get("open_access") and m.get("pmcid")):
                continue
            pmcs.append(m["pmcid"])
            cache = ROOT / "data" / "snapshots_raw" / "fulltext" / f"{m['pmcid']}.xml"
            res = rsnap.download(literature.fulltext_url(m["pmcid"]), cache)
            if not res["status"].startswith("ok"):
                record(pid, "", "Open-access full text", "open-access full text", m.get("title"),
                       literature.fulltext_url(m["pmcid"]), None, status_of(res))
                continue
            xml = cache.read_bytes()
            ft = folder / "publications" / f"{k}_fulltext.md"
            write(ft, fulltext_md(xml))
            record(pid, "", "Open-access full text", "open-access full text", m.get("title"),
                   literature.fulltext_url(m["pmcid"]), ft, WRITTEN)
            info = dd.parse_fulltext(xml)
            for s_ in info["supplements"]:
                kind = dd.supplement_kind(s_["file"])
                cat = "Supplementary data tables (xlsx/csv)" if kind == "data table" else (
                    "Figures / images" if kind == "image" else "Supplementary files")
                fetch_file(pid, "", cat, f"supplement: {kind}", f"{m['pmcid']} {s_['label']} {s_['caption']}".strip(),
                           f"https://europepmc.org/articles/{m['pmcid']}/bin/{s_['file']}",
                           folder / "supplementary" / m["pmcid"] / s_["file"], download=large,
                           covers=cat + (";Supplementary files" if cat != "Supplementary files" else ""))
            for g in info["figures"]:
                name = g["file"] if "." in g["file"] else g["file"] + ".jpg"
                fetch_file(pid, "", "Figures / images", "article figure", f"{m['pmcid']} {g['label']} {g['caption']}".strip(),
                           f"https://europepmc.org/articles/{m['pmcid']}/bin/{name}",
                           folder / "figures" / m["pmcid"] / name, download=large)
            if info["data_availability"]:
                da = info["data_availability"]
                record(pid, "", "Patient-level data access", "data availability statement (article)",
                       f"{k}: {da[:250]}", literature.fulltext_url(m["pmcid"]), None, LINK_ONLY)
                for u in sorted(set(re.findall(r"https?://(?:www\.)?(?:github\.com|zenodo\.org|doi\.org/10\.5281|"
                                               r"figshare\.com|datadryad\.org|synapse\.org|osf\.io)[^\s)\];,]*", da))):
                    record(pid, "", "Derived data / code repositories", "repository named in data statement", k, u,
                           None, LINK_ONLY)
        # all other mentions (incl. reviews): metadata list only
        acr = [t for t in progs_md[pid]["trials"]]
        men = dd.mentions(rsnap, ncts, acr, progs_md[pid]["drugs"])
        mpath = folder / "publications" / "all_literature_mentions.csv"
        mpath.parent.mkdir(parents=True, exist_ok=True)
        with open(mpath, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["key", "title", "journal", "year", "pub_type", "open_access", "doi", "in_trial_publications"])
            for x in sorted(men, key=dd.epmc_key):
                k = dd.epmc_key(x)
                w.writerow([k, x.get("title"), x.get("journalTitle"), x.get("pubYear"), x.get("pubType"),
                            x.get("isOpenAccess"), x.get("doi"), "yes" if k in recs else "no"])
        record(pid, "", "Other literature mentioning the trial", "literature list (CSV)",
               f"{len(men)} records cite an NCT or name acronym + drug (incl. reviews)",
               "https://europepmc.org/", mpath, WRITTEN)

        # ---- datasets (accession numbers text-mined from open-access articles)
        for pmc, accs in sorted(dd.accessions(rsnap, pmcs).items()):
            for a in accs:
                cat = ("Derived data / code repositories" if a["db"] in ("zenodo", "figshare", "dryad", "synapse", "doi")
                       else "Omics / expression / genomic datasets")
                record(pid, "", cat, f"dataset accession ({a['db']})", f"{a['accession']} (cited in {pmc})", a["uri"],
                       None, LINK_ONLY)

        # ---- FDA documents and FAERS, per investigational drug
        for de in kg.out(pid, "tmkg:has_intervention"):
            dname = kg.name(de["object"]) or ""
            r, apps = dd.fda_applications(rsnap, dname)
            if apps:
                lp = folder / "regulatory" / f"fda_{slug(dname)}_documents.csv"
                lp.parent.mkdir(parents=True, exist_ok=True)
                with open(lp, "w", newline="", encoding="utf-8") as fh:
                    w = csv.writer(fh, lineterminator="\n")
                    w.writerow(["application", "brand", "sponsor", "doc_type", "date", "url"])
                    for a in apps:
                        for d in a["docs"]:
                            w.writerow([a["application"], a["brand"], a["sponsor"], d["type"], d["date"], d["url"]])
                record(pid, "", "FDA documents", "Drugs@FDA document list (CSV)",
                       f"{dname}: {len(apps)} application(s), {sum(len(a['docs']) for a in apps)} documents",
                       "https://api.fda.gov/drug/drugsfda.json", lp, WRITTEN, {"fetched_at": r.fetched_at})
                for a in apps:      # download reviews + original approval letter + latest label; the rest stay in the CSV
                    labels = [d for d in a["docs"] if d["type"] == "Label"]
                    letters = [d for d in a["docs"] if d["type"] == "Letter"]
                    keep = [d for d in a["docs"] if d["type"] == "Review"] + letters[:1] + labels[-1:]
                    for d in keep:
                        name = d["url"].rsplit("/", 1)[-1]
                        fetch_file(pid, "", "FDA documents", f"FDA {d['type'].lower()} ({a['application']})",
                                   f"{a['brand']} {d['type']} {d['date']}", d["url"],
                                   folder / "regulatory" / "fda" / name)
            fa = dd.faers(rsnap, dname)
            fp = folder / "safety" / f"faers_{slug(dname)}.csv"
            fp.parent.mkdir(parents=True, exist_ok=True)
            with open(fp, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh, lineterminator="\n")
                w.writerow(["drug", "query_field", "total_reports", "reaction_meddra_pt", "report_count"])
                for term, n in fa["top_reactions"] or [("", "")]:
                    w.writerow([dname, fa["query_field"] or "none matched", fa["total"], term, n])
            record(pid, "", "FAERS adverse-event reports", "FAERS counts (CSV)",
                   f"{dname}: {fa['total']} reports (all indications; not trial-specific)",
                   "https://api.fda.gov/drug/event.json", fp, WRITTEN if fa["total"] else NONE_FOUND,
                   {"fetched_at": fa["fetched_at"]})

        # ---- web-search candidates (data/curated/web_candidates.csv)
        for i, c in enumerate([c for c in web if c["program"] == pid], 1):
            fetch_file(pid, "", c["category"], "web-search candidate", c["title"], c["url"],
                       folder / "web" / f"{i:02d}_{slug(c['url'].split('/')[2], 40)}{_ext(c['url'])}",
                       notes=f"query: {c['query']} (searched {c['searched_on']}) - verify relevance")
            if rows[-1]["status"] == DOWNLOADED:
                rows[-1]["notes"] += "; downloaded - verify relevance"
            elif rows[-1]["status"] == TO_FETCH:
                rows[-1]["status"] = CANDIDATE

    rows.sort(key=lambda r: (r["program"], r["category"], r["trial"], r["local_path"], r["source_url"]))
    with open(raw / "index.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, INDEX_COLS, lineterminator="\n")
        w.writeheader(); w.writerows(rows)
    write(raw / "README.md", readme(rows))
    return rows


def readme(rows):
    from collections import Counter
    st = Counter(r["status"] for r in rows)
    lines = ["# raw/ - source documents and data for the 34 failed programs", "",
             "One folder per program. Start with `00_program.md`, then `trials/<NCT>/registry_summary.md`, then",
             "`publications/`. The checklist of what exists and what is missing is **`CATALOGUE.xlsx`**;",
             "`index.csv` lists every item with its source URL, status, size, checksum and date.",
             "Files are source text re-laid out for reading - nothing is summarised or interpreted.", "",
             "| Status | Items |", "|---|---|"] + [f"| {k} | {v} |" for k, v in sorted(st.items())] + [
             "", "## Folder layout", "",
             "- `trials/<NCT>/` registry record + readable summary, `documents/` protocol / SAP / consent PDFs,",
             "  `other_registries/` EU Clinical Trials Register pages",
             "- `publications/` papers, conference abstracts and preprints that cite the trial (citation + abstract;",
             "  `_fulltext.md` when open access); `all_literature_mentions.csv` = every record mentioning it, incl. reviews",
             "- `supplementary/<PMCID>/` and `figures/<PMCID>/` files from open-access articles",
             "- `sources/` press releases and pages cited in the curated dataset; `web/` pages found by web search",
             "- `regulatory/` Drugs@FDA document list and letters / reviews / latest label",
             "- `safety/` FAERS report counts and top reactions (all indications, not trial-specific)", "",
             "## Getting the items marked 'to download on your computer'", "",
             "The build machine could not reach several sites. On your Mac:", "",
             "```bash", "cd <your TrialMindKG folder>        # e.g. cd ~/projects/TrialMindKG",
             "source .venv/bin/activate", "python pipelines/raw_docs.py      # add --no-large to skip supplements/figures (GBs)",
             "python pipelines/make_catalogue.py  # refresh the checklist", "```", "",
             "Items still failing after that (paywalls, sites that block scripts): open the URL from `index.csv` in a",
             "browser and save the file at the `local_path` shown. Binary files (PDF, images, spreadsheets, slides) stay",
             "on your computer (git-ignored); text files are committed.", "",
             "Not collected automatically: patient-level data (request routes are listed), raw omics files (accession",
             "links are listed; files are often GB-sized), JPM / investor decks that are not linked publicly.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    large = "--no-large" not in sys.argv
    rows = build(large=large)
    present = sum(r["status"] in (DOWNLOADED, WRITTEN) for r in rows)
    print(f"raw/: {len(rows)} items, {present} present; see raw/README.md, raw/index.csv")
    try:
        import make_catalogue
        print("catalogue:", make_catalogue.build())
    except ImportError:
        pass
