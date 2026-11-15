"""
Independent evidence for one graph edge: collectors, the evidence dossier, the rater prompt and the label checks.

Used by scripts/audit_spotcheck.py (the pruner spot check and the audit sample; the rules are pre-registered in
outputs/pharos/spotcheck_rule.md, Amendment 1 addition A) and by the app's "independent check" on an edge.
None of the sources below is read by the pruner: it reads only the Pharos tables.

    fetch = Fetcher()                              # cached under data/audit/snapshots, replayable
    d = build_dossier(edge, fetch, Lookups())      # edge = the blind columns of one row
    print(dossier_text(d))                         # what a rater (model or human) reads
    j = judge(d, nim, "nvidia/nemotron-...")       # {label, label_raw, rationale, items, problems, model}

Blindness: build_dossier copies only BLIND_FIELDS from the edge, so a verdict, confidence or reasons column never
reaches a dossier, a prompt or a stored record.

Every collector takes plain identifiers and a `fetch` object (methods json / text / post; see Fetcher and
FastFetcher), returns one source record and never raises:
    {source, status, items, url, note, total, examined}
    status   'answered' | 'failed' (the source did not answer) | 'not found' (an identifier is unknown to the
             source) | 'not applicable'
    items    [{text, url, kind}]; kind 'evidence' can support a claim, kind 'context' is shown but cannot
"""
import datetime
import json
import re
from pathlib import Path

import requests

from snapshot import Snapshot

ROOT = Path(__file__).resolve().parents[1]
AUDIT_DIR = ROOT / "data" / "audit"
SCHEMA_VERSION = 1
LABELS = ["supported", "unsupported", "cannot tell"]
BLIND_FIELDS = ["subject_name", "subject_id", "predicate", "object_name", "object_id", "source", "knowledge_level",
                "source_statistic", "references"]
STATEMENT = "checked by an auditing agent against sources the pruner does not read"

# The three BioPlex papers as IntAct cites them (titles checked in Europe PMC on 2026-11-12): an IntAct record
# from one of them is the same experiment as a BioPlex-sourced row, so it is context there, not evidence.
BIOPLEX_PMIDS = {"26186194", "28514442", "33961781"}
ITEM_PREFIX = {"IntAct": "IA", "Complex Portal": "CP", "Reactome": "RX", "Europe PMC co-mention search": "PM",
               "The row's own references": "RF", "Open Targets": "OT",
               "Open Targets literature sentences": "OL", "QuickGO": "QG", "ChEMBL": "CH", "IMPC": "IM",
               "UniProt": "UP", "WikiPathways": "WP", "STRING": "ST"}
OT_TYPE_NAMES = {"genetic_association": "genetic association", "genetic_literature": "curated genetic literature",
                 "somatic_mutation": "somatic mutation", "clinical": "clinical (drugs)", "known_drug": "known drug",
                 "affected_pathway": "affected pathway", "rna_expression": "RNA expression",
                 "animal_model": "animal model", "literature": "literature (text mining)"}
COMPOUND_PREFIXES = {"CHEMBL.COMPOUND", "PUBCHEM.COMPOUND", "GTOPDB", "DrugCentral"}
PATHWAY_PREFIXES = {"REACT", "WIKIPATHWAYS", "KEGG.PATHWAY"}
DISEASE_PREFIXES = {"MONDO", "EFO", "DOID", "OMIM", "ORDO", "Orphanet", "UMLS", "HP"}


# ------------------------------------------------------------------------------------------------ fetching
class Fetcher:
    """Cached HTTP for the collectors (pipelines/snapshot.py): every answer is stored under `directory`, so a
    dossier can be rebuilt offline and each item traced to the raw answer. Methods return the parsed body
    (None for a stored 404) and raise when the source does not answer after the snapshot's retries."""

    def __init__(self, directory=None, mode=None, sleep=0.1):
        self.snap = Snapshot(directory or AUDIT_DIR / "snapshots", mode=mode, sleep=sleep)

    def json(self, source, url, params=None, headers=None):
        return self.snap.get(source, url, params=params, headers=headers).body

    def text(self, source, url, params=None):
        body = self.snap.get_text(source, url, params=params).body
        return None if body is None else body.get("text", "")

    def post(self, source, url, body):
        return self.snap.post(source, url, body).body


class FastFetcher:
    """Uncached HTTP with a short timeout and no retry, for an on-demand check under a time budget. Same methods
    as Fetcher. `deadline` (time.monotonic() value) makes every later call raise TimeoutError."""

    def __init__(self, timeout=4.0, deadline=None):
        self.timeout, self.deadline = timeout, deadline

    def _check(self):
        import time
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise TimeoutError("time budget for the independent check is used up")

    def _done(self, r, text=False):
        if r.status_code in (400, 404):
            return None
        r.raise_for_status()
        return r.text if text else r.json()

    def json(self, source, url, params=None, headers=None):
        self._check()
        return self._done(requests.get(url, params=params, headers=headers, timeout=self.timeout))

    def text(self, source, url, params=None):
        self._check()
        return self._done(requests.get(url, params=params, timeout=self.timeout), text=True)

    def post(self, source, url, body):
        self._check()
        return self._done(requests.post(url, json=body, timeout=self.timeout))


def source_record(source, status, items=(), url="", note="", total=None, examined=None):
    """One source's part of a dossier."""
    return {"source": source, "status": status, "items": list(items), "url": url, "note": note, "total": total,
            "examined": examined}


def make_item(text, url="", kind="evidence", **fields):
    """One dossier item. kind: 'evidence' or 'context'."""
    return {"text": " ".join(str(text).split()), "url": url, "kind": kind, **fields}


def _safe(source):
    """Decorator: a collector that raises returns a 'failed' source record instead."""
    def deco(fn):
        def wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as err:                       # the source did not answer, or answered in a new shape
                return source_record(source, "failed", note=f"{type(err).__name__}: {str(err)[:160]}")
        wrapped.__name__, wrapped.__doc__ = fn.__name__, fn.__doc__
        return wrapped
    return deco


def prefix_of(node_id):
    return str(node_id).split(":", 1)[0] if ":" in str(node_id) else ""


def bare(node_id):
    """'UniProtKB:P01112-2' -> 'P01112' (prefix and isoform suffix removed)."""
    return str(node_id).split(":", 1)[-1].split("-")[0].strip()


def local_id(node_id):
    """'CHEMBL.COMPOUND:CHEMBL939' -> 'CHEMBL939'; 'GO:0005886' stays 'GO:0005886' for GO, MP, HP, MONDO, EFO."""
    s = str(node_id)
    return s if prefix_of(s) in ("GO", "MP", "HP", "MONDO", "EFO") else s.split(":", 1)[-1]


# ------------------------------------------------------------------------------------------------ names and text
def name_pattern(names):
    """Regex that finds any of the names in running text. Short symbols (6 characters or fewer with a capital or a
    digit, such as HRAS) match case-sensitively as whole tokens; longer names match in any case."""
    parts = []
    for n in sorted({n.strip() for n in names if n and len(n.strip()) >= 2}, key=len, reverse=True):
        body = re.escape(n).replace(r"\ ", r"[\s-]+")
        symbol = len(n) <= 6 and (any(c.isupper() for c in n) or any(c.isdigit() for c in n))
        parts.append(f"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])" if symbol else f"(?i:(?<![A-Za-z0-9]){body}(?![A-Za-z0-9]))")
    return re.compile("|".join(parts)) if parts else None


def clean_abstract(text):
    """Abstract text without markup."""
    return " ".join(re.sub(r"<[^>]+>", " ", text or "").split())


def split_sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])", text or "") if s.strip()]


def list_gap(sentence, pat_a, pat_b):
    """Fewest commas or semicolons between a mention of each entity in one sentence (0 = named side by side or in
    one clause; a long list puts several between them). 99 when the sentence does not name both."""
    ma, mb = [m.span() for m in pat_a.finditer(sentence)], [m.span() for m in pat_b.finditer(sentence)]
    gaps = [len(re.findall(r"[,;]", sentence[min(x[1], y[1]):max(x[0], y[0])])) for x in ma for y in mb]
    return min(gaps) if gaps else 99


def pick_sentences(text, pat_a, pat_b, k=2, width=420):
    """(sentences, both, gap) from one abstract: sentences that name both entities first (most direct first, see
    list_gap), else the first sentence naming each; `both` says whether one sentence names both; gap = the smallest
    list_gap of the abstract. Each sentence is cut at `width` characters."""
    sents = split_sentences(text)
    hit_a = [bool(pat_a and pat_a.search(s)) for s in sents]
    hit_b = [bool(pat_b and pat_b.search(s)) for s in sents]
    both = sorted((i for i in range(len(sents)) if hit_a[i] and hit_b[i]), key=lambda i: (list_gap(sents[i], pat_a, pat_b), i))
    chosen = both[:k]
    gap = list_gap(sents[chosen[0]], pat_a, pat_b) if chosen else 99
    if not chosen:
        chosen = sorted({i for i in ([hit_a.index(True)] if True in hit_a else []) + ([hit_b.index(True)] if True in hit_b else [])})
    return [sents[i][:width] + ("..." if len(sents[i]) > width else "") for i in chosen], bool(both), gap


def pmids_in(text):
    """PubMed ids in a references cell ('8670882|12345' or 'PMID:8670882, 12345'), in order, without repeats."""
    out = []
    for m in re.findall(r"(?<![\d.])(\d{4,9})(?![\d.])", str(text or "")):
        if m not in out:
            out.append(m)
    return out


class Lookups:
    """Local reference tables (data/reference): gene ids and disease names. Nothing here comes from the verdicts."""

    def __init__(self, root=ROOT):
        import csv
        ref = Path(root) / "data" / "reference"
        self.by_uniprot, self.by_ncbigene, self.by_hgnc, self.by_ensembl = {}, {}, {}, {}
        f = ref / "hgnc_gene_ids.tsv"
        if f.exists():
            with f.open() as fh:
                for r in csv.DictReader((l for l in fh if not l.startswith("#")), delimiter="\t"):
                    for u in filter(None, re.split(r"[|,; ]+", r.get("uniprot_id") or "")):
                        self.by_uniprot.setdefault(u, r)
                    if r.get("ncbigene_id"):
                        self.by_ncbigene[r["ncbigene_id"].split(":")[-1]] = r
                    if r.get("hgnc_id"):
                        self.by_hgnc[r["hgnc_id"].split(":")[-1]] = r
                    if r.get("ensembl_id"):
                        self.by_ensembl[r["ensembl_id"]] = r
        self.mondo, self.mondo_efo = {}, {}
        f = ref / "mondo_terms.tsv"
        if f.exists():
            with f.open() as fh:
                for r in csv.DictReader((l for l in fh if not l.startswith("#")), delimiter="\t"):
                    self.mondo[r["mondo_id"]] = (r.get("label") or "", [s for s in (r.get("exact_synonyms") or "").split("|") if s])
        f = ref / "mondo_efo_exact.tsv"
        if f.exists():
            with f.open() as fh:
                for line in fh:
                    cells = line.rstrip("\n").split("\t")
                    m = [c for c in cells if c.startswith("MONDO:")]
                    e = [c for c in cells if c.startswith("EFO:")]
                    if m and e:
                        self.mondo_efo.setdefault(m[0], e[0])
        self.children = {}
        f = Path(root) / "data" / "ontologies" / "mondo" / "relations.tsv.gz"       # absent in the app image: no narrower terms there
        if f.exists():
            import gzip
            with gzip.open(f, "rt") as fh:
                for r in csv.DictReader(fh, delimiter="\t"):
                    if r.get("relation") == "is_a":
                        self.children.setdefault(r["object"], []).append(r["subject"])

    def narrower(self, node_id, k=30):
        """Labels of up to k more specific MONDO terms under a disease, nearest first (children before grandchildren)."""
        out, seen, queue = [], {str(node_id)}, [str(node_id)]
        while queue and len(out) < k:
            for c in sorted(self.children.get(queue.pop(0), [])):
                if c not in seen:
                    seen.add(c); queue.append(c)
                    label = self.mondo.get(c, ("", []))[0]
                    if label and len(out) < k:
                        out.append(label)
        return out

    def gene(self, node_id):
        """{symbol, ensembl, uniprot, ncbigene, hgnc} for a UniProtKB, NCBIGene, HGNC or ENSEMBL id ({} if unknown)."""
        pre, loc = prefix_of(node_id), bare(node_id) if prefix_of(node_id) == "UniProtKB" else str(node_id).split(":", 1)[-1]
        r = {"UniProtKB": self.by_uniprot, "NCBIGene": self.by_ncbigene, "HGNC": self.by_hgnc,
             "ENSEMBL": self.by_ensembl}.get(pre, {}).get(loc)
        if not r:
            return {}
        return {"symbol": r.get("symbol", ""), "ensembl": r.get("ensembl_id", ""),
                "uniprot": re.split(r"[|,; ]+", r.get("uniprot_id") or "")[0],
                "ncbigene": (r.get("ncbigene_id") or "").split(":")[-1], "hgnc": (r.get("hgnc_id") or "").split(":")[-1]}

    def disease_names(self, node_id, name=""):
        """Search names of a disease or trait: its label, then for MONDO up to three exact synonyms (shortest first,
        four characters or more). Narrower terms are separate: narrower()."""
        names = [name] if name else []
        label, syn = self.mondo.get(str(node_id), ("", []))
        if label and label not in names:
            names.append(label)
        have = {n.lower() for n in names}
        for s in sorted((s for s in syn if len(s) >= 4), key=lambda s: (len(s), s)):
            if len(names) >= 4:
                break
            if s.lower() not in have:
                names.append(s); have.add(s.lower())
        return names

    def open_targets_ids(self, node_id):
        """Candidate Open Targets disease ids for a node id, best first ('MONDO:0005233' -> ['MONDO_0005233', ...])."""
        s = str(node_id)
        out = [s.replace(":", "_")] if prefix_of(s) in ("MONDO", "EFO", "HP", "Orphanet", "OTAR", "MP", "GO", "DOID") else []
        if prefix_of(s) == "ORDO":
            out.append("Orphanet_" + s.split(":")[-1])
        if s in self.mondo_efo:
            out.append(self.mondo_efo[s].replace(":", "_"))
        return out


@_safe("UniProt")
def protein_names(acc, fetch):
    """Names of a protein for literature search, from its UniProt entry: source record whose `names` field lists the
    gene symbol, the recommended protein name and its short names, and gene synonyms of four characters or more
    (at most five names). `ensembl` holds the Ensembl gene id."""
    acc = bare(acc)
    url = f"https://rest.uniprot.org/uniprotkb/{acc}.json"
    body = fetch.json("uniprot", url, {"fields": "accession,gene_names,protein_name,xref_ensembl"})
    if not body:
        return {**source_record("UniProt", "not found", url=url, note=f"no UniProt entry {acc}"), "names": [], "ensembl": ""}
    names = []
    genes = body.get("genes") or [{}]
    if (genes[0].get("geneName") or {}).get("value"):
        names.append(genes[0]["geneName"]["value"])
    rec = (body.get("proteinDescription") or {}).get("recommendedName") or {}
    if (rec.get("fullName") or {}).get("value"):
        names.append(rec["fullName"]["value"])
    names += [s["value"] for s in rec.get("shortNames") or [] if len(s.get("value", "")) >= 3]
    names += [s["value"] for s in genes[0].get("synonyms") or [] if len(s.get("value", "")) >= 4]
    seen, out = set(), []
    for n in names:
        if n.lower() not in seen:
            seen.add(n.lower()); out.append(n)
    ens = ""
    for x in body.get("uniProtKBCrossReferences") or []:
        if x.get("database") == "Ensembl":
            ens = next((p["value"].split(".")[0] for p in x.get("properties") or [] if p.get("key") == "GeneId"), "")
            if ens:
                break
    return {**source_record("UniProt", "answered", url=url), "names": out[:5], "ensembl": ens}


# ------------------------------------------------------------------------------------------------ collectors
PSICQUIC = "https://www.ebi.ac.uk/Tools/webservices/psicquic/intact/webservices/current/search/query/"


def _mi(cell):
    """('MI:0915', 'physical association') from a MITAB cell such as psi-mi:"MI:0915"(physical association)."""
    m = re.search(r'"?(MI:\d+)"?\(([^)]*)\)', cell or "")
    return (m.group(1), m.group(2)) if m else ("", (cell or "").strip("-"))


def interaction_class(type_name):
    """'colocalization', 'genetic' or 'physical' for an IntAct interaction type name. Types under association
    (physical association, direct interaction and its children such as phosphorylation reaction) count as physical."""
    t = (type_name or "").lower()
    if "colocali" in t:
        return "colocalization"
    if "genetic" in t or t.startswith("synthetic") or "phenotypic" in t or "epistasis" in t:
        return "genetic"
    return "physical"


def parse_mitab(text, a, b):
    """IntAct records (MITAB 2.7 lines) in which the two interactors are exactly the proteins a and b (isoform
    suffixes ignored). Returns [{ac, type_mi, type, cls, method, pmid, author, miscore, expanded, negative}],
    one per interaction accession."""
    out, seen = [], set()
    for line in (text or "").splitlines():
        c = line.split("\t")
        if len(c) < 15:
            continue
        ids = []
        for cell in (c[0], c[1]):
            m = re.search(r"uniprotkb:([A-Z0-9]+)", cell)
            ids.append(m.group(1) if m else "")
        if sorted(ids) != sorted([a, b]):
            continue
        ac = next(iter(re.findall(r"intact:(EBI-\d+)", c[13])), c[13])
        if ac in seen:
            continue
        seen.add(ac)
        type_mi, type_name = _mi(c[11])
        score = re.search(r"intact-miscore:([\d.]+)", c[14])
        out.append({"ac": ac, "type_mi": type_mi, "type": type_name, "cls": interaction_class(type_name),
                    "method": _mi(c[6])[1], "pmid": next(iter(re.findall(r"pubmed:(\d+)", c[8])), ""),
                    "author": c[7].strip("-"), "miscore": score.group(1) if score else "",
                    "expanded": len(c) > 15 and "expansion" in c[15].lower(),
                    "negative": len(c) > 35 and c[35].strip().lower() == "true"})
    return out


@_safe("IntAct")
def collect_intact(a, b, fetch, own_sources=(), max_items=8):
    """IntAct experimental interaction records for a protein pair (a == b: self-interaction). own_sources: the
    row's source names; with BioPlex among them a record from a BioPlex paper is context."""
    a, b = bare(a), bare(b)
    query = f"idA:{a} AND idB:{a}" if a == b else f"id:{a} AND id:{b}"
    url = PSICQUIC + query
    text = fetch.text("intact", url, {"format": "tab27"})
    recs = parse_mitab(text or "", a, b)
    own_bioplex = any("bioplex" in s.lower() for s in own_sources)
    order = {"physical": 0, "colocalization": 1, "genetic": 2}
    recs.sort(key=lambda r: (r["negative"], order[r["cls"]], r["expanded"], -float(r["miscore"] or 0), r["ac"]))
    items = []
    for r in recs[:max_items]:
        same = own_bioplex and r["pmid"] in BIOPLEX_PMIDS
        t = (f"IntAct {r['ac']}: {r['type']} ({r['type_mi']}), detected by {r['method']}; {r['author']}, "
             f"PubMed {r['pmid'] or 'not given'}; IntAct MI score {r['miscore'] or 'not given'}")
        if r["expanded"]:
            t += "; pair taken from a co-complex purification (spoke expansion)"
        if r["negative"]:
            t += "; NEGATIVE record: the two were reported not to interact"
        if same:
            t += "; same experiment as this row's own source (BioPlex)"
        items.append(make_item(t, f"https://www.ebi.ac.uk/intact/details/interaction/{r['ac']}",
                               "context" if same else "evidence", cls=r["cls"], pmid=r["pmid"], negative=r["negative"]))
    counts = {k: sum(r["cls"] == k and not r["negative"] for r in recs) for k in order}
    note = (f"{len(recs)} records for this pair" + (f": {counts['physical']} physical-type, {counts['colocalization']} "
            f"colocalization, {counts['genetic']} genetic, {sum(r['negative'] for r in recs)} negative" if recs else ""))
    return source_record("IntAct", "answered", items, "https://www.ebi.ac.uk/intact/search?query=" + query.replace(" ", "%20"),
                         note, total=len(recs), examined=len(recs))


@_safe("Complex Portal")
def collect_complex_portal(a, b, fetch, max_items=5):
    """Curated complexes (EBI Complex Portal) in which both proteins take part (a == b: two or more copies)."""
    a, b = bare(a), bare(b)
    url = f"https://www.ebi.ac.uk/intact/complex-ws/search/{a}"
    body = fetch.json("complexportal", url, {"format": "json", "number": 200}) or {}
    items = []
    for e in body.get("elements") or []:
        parts = {bare(i.get("identifier", "")): i for i in e.get("interactors") or []}
        if a not in parts or b not in parts:
            continue
        if a == b:
            m = re.search(r"minValue:\s*(\d+)", parts[a].get("stochiometry") or "")
            if not m or int(m.group(1)) < 2:
                continue
        predicted = bool(e.get("predictedComplex"))
        t = (f"Complex Portal {e.get('complexAC')}: {e.get('complexName')} ({(e.get('organismName') or '').split(';')[0]}); "
             + ("the complex holds two or more copies of the protein" if a == b else "both proteins are participants")
             + ("; computationally predicted complex, not curated" if predicted else ""))
        items.append(make_item(t, f"https://www.ebi.ac.uk/complexportal/complex/{e.get('complexAC')}",
                               "context" if predicted else "evidence"))
    return source_record("Complex Portal", "answered", items[:max_items], f"https://www.ebi.ac.uk/complexportal/complex/search?query={a}",
                         f"{len(items)} shared complexes", total=len(items), examined=len(body.get("elements") or []))


REACTOME = "https://reactome.org/ContentService/data/"


@_safe("Reactome")
def collect_reactome(a, b, fetch, own_sources=(), max_items=5):
    """Human Reactome complexes and reactions that contain both proteins. With Reactome among the row's own
    sources the items are context."""
    a, b = bare(a), bare(b)
    if a == b:
        return source_record("Reactome", "not applicable", note="self-interaction is not read from Reactome")
    kind = "context" if any("reactome" in s.lower() for s in own_sources) else "evidence"

    def shared(path):
        sets = []
        for acc in (a, b):
            rows = fetch.json("reactome", REACTOME + path.format(acc=acc)) or []
            sets.append({r["stId"]: r for r in rows if r.get("speciesName") == "Homo sapiens" and r.get("stId")})
        return [sets[0][k] for k in sorted(set(sets[0]) & set(sets[1]))]

    cx, rx = shared("complexes/UniProt/{acc}"), shared("mapping/UniProt/{acc}/reactions")
    suffix = "; Reactome is this row's own source" if kind == "context" else ""
    items = [make_item(f"Reactome complex {r['stId']}: {r.get('displayName')}; both proteins are components{suffix}",
                       f"https://reactome.org/content/detail/{r['stId']}", kind, what="complex") for r in cx[:max_items]]
    items += [make_item(f"Reactome reaction {r['stId']}: {r.get('displayName')}; both proteins take part"
                        + ("; inferred from another species" if r.get("isInferred") else "") + suffix,
                        f"https://reactome.org/content/detail/{r['stId']}", kind, what="reaction") for r in rx[:max_items]]
    return source_record("Reactome", "answered", items, f"https://reactome.org/content/query?q={a}",
                         f"{len(cx)} shared complexes, {len(rx)} shared reactions", total=len(cx) + len(rx),
                         examined=len(cx) + len(rx))


EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def epmc_query(names_a, names_b):
    """Europe PMC query: abstracts or titles that name one of names_a and one of names_b."""
    def group(names):
        return "(" + " OR ".join('TITLE_ABS:"' + n.replace('"', " ") + '"' for n in names) + ")"
    return group(names_a) + " AND " + group(names_b)


def _paper(r):
    src, pid = r.get("source") or "MED", r.get("pmid") or r.get("id") or ""
    label = f"PubMed {pid}" if src == "MED" else f"Europe PMC {src} {pid}"
    return label, f"https://europepmc.org/abstract/{src}/{pid}", pid if src == "MED" else ""


@_safe("Europe PMC co-mention search")
def collect_comention(names_a, names_b, fetch, max_examined=25, max_shown=8, narrower_b=()):
    """Abstracts that name both entities (Europe PMC, relevance order): the first `max_examined` are read, at most
    `max_shown` are shown: those with both names in one sentence first, the most direct sentences (fewest list
    separators between the two names) before list co-mentions. total = abstracts found, examined = read.
    narrower_b: names of more specific terms under entity b (a disease), searched as well."""
    names_a, names_b = [n for n in names_a if n], [n for n in names_b if n]
    shown_b, names_b = list(names_b), names_b + [n for n in narrower_b if n and n not in names_b]
    if not names_a or not names_b:
        return source_record("Europe PMC co-mention search", "not found", note="no name to search for one of the two")
    query = epmc_query(names_a, names_b)
    body = fetch.json("europepmc", EPMC, {"query": query, "format": "json", "resultType": "core", "pageSize": max_examined}) or {}
    results = (body.get("resultList") or {}).get("result") or []
    pat_a, pat_b = name_pattern(names_a), name_pattern(names_b)
    rows = []
    for rank, r in enumerate(results):
        text = clean_abstract(r.get("abstractText"))
        title = clean_abstract(r.get("title"))
        sents, both, gap = pick_sentences(title + " " + text if text else title, pat_a, pat_b)
        rows.append((not both, min(gap, 3), rank, r, title, sents, both))
    rows.sort(key=lambda x: x[:3])
    items = []
    for _, _, _, r, title, sents, both in rows[:max_shown]:
        label, url, pmid = _paper(r)
        t = (f"{label} ({r.get('pubYear') or 'year not given'}): \"{title}\" "
             + ("Sentences naming both: " if both else "No sentence names both. Sentences naming each: ")
             + " | ".join(f'"{s}"' for s in sents))
        items.append(make_item(t, url, "evidence", pmid=pmid, both_in_sentence=both))
    hits = int(body.get("hitCount") or 0)
    n_both = sum(1 for x in rows if x[6])
    more = len(names_b) - len(shown_b)
    note = (f"{hits} abstracts name both; {len(results)} examined; {n_both} of those name both in one sentence. "
            f"Searched titles and abstracts for {' / '.join(names_a)} together with {' / '.join(shown_b)}"
            + (f" or one of {more} more specific terms" if more else ""))
    return {**source_record("Europe PMC co-mention search", "answered", items, "https://europepmc.org/search?query="
                            + requests.utils.quote(query), note, total=hits, examined=len(results)), "query": query}


@_safe("The row's own references")
def collect_references(pmids, names_a, names_b, fetch, max_refs=5):
    """Title and the sentences naming the subject or the object from each paper the row cites (first `max_refs`)."""
    pmids = list(pmids)[:max_refs]
    if not pmids:
        return source_record("The row's own references", "not applicable", note="the row cites no paper")
    pat_a, pat_b = name_pattern(names_a), name_pattern(names_b)
    items, missing = [], 0
    for p in pmids:
        body = fetch.json("europepmc", EPMC, {"query": f"EXT_ID:{p} AND SRC:MED", "format": "json", "resultType": "core"}) or {}
        res = (body.get("resultList") or {}).get("result") or []
        if not res:
            missing += 1
            continue
        r = res[0]
        text, title = clean_abstract(r.get("abstractText")), clean_abstract(r.get("title"))
        sents, both, _ = pick_sentences(title + " " + text if text else title, pat_a, pat_b, k=3)
        if not sents:
            sents = split_sentences(text)[:1]
        t = (f"PubMed {p} ({r.get('pubYear') or 'year not given'}), cited by the row: \"{title}\" "
             + ("Sentences naming both: " if both else "No sentence names both. Sentences: ")
             + (" | ".join(f'"{s}"' for s in sents) if sents else "(no abstract text available)"))
        items.append(make_item(t, f"https://europepmc.org/abstract/MED/{p}", "evidence", pmid=p, both_in_sentence=both))
    return source_record("The row's own references", "answered", items, "",
                         f"{len(pmids)} cited papers looked up, {missing} not found in Europe PMC", total=len(pmids),
                         examined=len(pmids) - missing)


OT_URL = "https://api.platform.opentargets.org/api/v4/graphql"
OT_QUERY = ("query($ens:String!,$efo:String!){ disease(efoId:$efo){id name} target(ensemblId:$ens){ approvedSymbol "
            "associatedDiseases(Bs:[$efo], enableIndirect:true){ count rows{ disease{id name} score "
            "datatypeScores{id score} } } } }")


@_safe("Open Targets")
def collect_open_targets(ensembl_id, disease_ids, fetch, own_sources=()):
    """Open Targets association of a gene with a disease or trait, one item per data type. The association includes
    evidence on more specific disease terms. The literature data type is context; every other one is evidence,
    unless Open Targets is among the row's own sources: then every item is context."""
    own = any("open targets" in s.lower() or "opentargets" in s.lower() for s in own_sources)
    if not ensembl_id:
        return source_record("Open Targets", "not found", note="no Ensembl gene id for the gene")
    if not disease_ids:
        return source_record("Open Targets", "not found", note="the disease id has no Open Targets equivalent")
    for efo in disease_ids:
        body = fetch.post("opentargets", OT_URL, {"query": OT_QUERY, "variables": {"ens": ensembl_id, "efo": efo}}) or {}
        data = body.get("data") or {}
        if data.get("target") is None:
            return source_record("Open Targets", "not found", note=f"gene {ensembl_id} is not in Open Targets")
        if data.get("disease") is None:
            continue
        url = f"https://platform.opentargets.org/evidence/{ensembl_id}/{efo}"
        rows = (data["target"].get("associatedDiseases") or {}).get("rows") or []
        if not rows:
            return source_record("Open Targets", "answered", [], url,
                                 f"no association of {data['target'].get('approvedSymbol')} with {data['disease']['name']}", total=0, examined=0)
        row = rows[0]
        items = []
        for dt in sorted(row.get("datatypeScores") or [], key=lambda d: -d["score"]):
            lit = dt["id"] == "literature"
            items.append(make_item(
                f"Open Targets, {data['target'].get('approvedSymbol')} and {data['disease']['name']}: data type "
                f"{OT_TYPE_NAMES.get(dt['id'], dt['id'])} scores {dt['score']:.2f}"
                + (" (text mining: context only)" if lit else "") + ("; Open Targets is this row's own source" if own else ""),
                url, "context" if lit or own else "evidence", datatype=dt["id"], score=round(dt["score"], 4)))
        return source_record("Open Targets", "answered", items, url,
                             f"association found, overall score {row['score']:.2f}", total=len(items), examined=len(items))
    return source_record("Open Targets", "not found", note=f"disease id not in Open Targets (tried {', '.join(disease_ids)})")


OT_SEARCH = 'query($q:String!){ search(queryString:$q, entityNames:["disease"], page:{index:0,size:5}){ hits{ id name } } }'
OT_LIT_QUERY = ('query($efo:String!,$ens:[String!]!,$size:Int!,$ind:Boolean!){ disease(efoId:$efo){ id name '
                'evidences(ensemblIds:$ens, enableIndirect:$ind, datasourceIds:["europepmc"], size:$size){ count rows{ '
                'disease{id name} literature score textMiningSentences{ text section } } } } }')


def open_targets_disease_by_name(names, fetch):
    """Open Targets disease id whose name equals one of `names` exactly (case ignored), found with the platform's
    search; '' when there is none. Used when a node's own id is not an Open Targets id. Never raises."""
    want = {n.strip().lower() for n in names if n and n.strip()}
    try:
        for n in list(names)[:2]:
            body = fetch.post("opentargets", OT_URL, {"query": OT_SEARCH, "variables": {"q": n}}) or {}
            for h in ((body.get("data") or {}).get("search") or {}).get("hits") or []:
                if str(h.get("name", "")).strip().lower() in want:
                    return h["id"]
    except Exception:
        pass
    return ""


def _english(text):
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(c.isascii() for c in letters) / len(letters) >= 0.9


@_safe("Open Targets literature sentences")
def collect_ot_literature(ensembl_id, disease_ids, fetch, max_examined=25, max_shown=8, width=420):
    """Sentences in which Europe PMC text mining found the gene and the disease together, as Open Targets serves them,
    INCLUDING more specific disease terms under the claimed one (a label search misses those). One item per paper:
    its best sentence (title or abstract before other sections), with the disease the sentence is about. The
    sentences are evidence a rater reads; the literature score itself stays context (see collect_open_targets).
    total = evidence strings found, examined = read. If the query with more specific terms does not answer, the
    claimed term alone is tried and the record is marked 'failed' (the hierarchy was not covered)."""
    name = "Open Targets literature sentences"
    if not ensembl_id:
        return source_record(name, "not found", note="no Ensembl gene id for the gene")
    if not disease_ids:
        return source_record(name, "not found", note="the disease id has no Open Targets equivalent")
    section_rank = {"title": 0, "abstract": 0}
    for efo in disease_ids:
        status, extra = "answered", ""
        try:
            body = fetch.post("opentargets", OT_URL, {"query": OT_LIT_QUERY, "variables": {"ens": [ensembl_id], "efo": efo, "size": max_examined, "ind": True}}) or {}
        except Exception:
            body = fetch.post("opentargets", OT_URL, {"query": OT_LIT_QUERY, "variables": {"ens": [ensembl_id], "efo": efo, "size": max_examined, "ind": False}}) or {}
            status, extra = "failed", " The query that includes more specific disease terms did not answer; only the claimed term was searched."
        dis = (body.get("data") or {}).get("disease")
        if dis is None:
            continue
        ev = dis.get("evidences") or {}
        rows, seen, cand = ev.get("rows") or [], set(), []
        for order, r in enumerate(rows):
            pmid = str((r.get("literature") or [""])[0])
            if pmid in seen:
                continue
            sents = [x for x in r.get("textMiningSentences") or [] if x.get("text") and _english(x["text"])]
            if not sents:
                continue
            seen.add(pmid)
            best = min(sents, key=lambda x: (section_rank.get(x.get("section"), 1), len(x["text"])))
            cand.append((section_rank.get(best.get("section"), 1), -float(r.get("score") or 0), order, pmid, best, r.get("disease") or {}))
        cand.sort(key=lambda c: c[:3])
        items = []
        for _, _, _, pmid, best, d in cand[:max_shown]:
            same = d.get("id") == dis.get("id")
            text = " ".join(best["text"].split())
            items.append(make_item(
                f"PubMed {pmid or 'id not given'}, about {d.get('name')} "
                f"({'the claimed term' if same else 'a more specific term under the claimed one'}), {best.get('section') or 'text'} "
                f"sentence: \"{text[:width]}{'...' if len(text) > width else ''}\"",
                f"https://europepmc.org/abstract/MED/{pmid}" if pmid else "", "evidence", pmid=pmid, section=best.get("section"),
                specific=not same))
        total = int(ev.get("count") or 0)
        return source_record(name, status, items, f"https://platform.opentargets.org/evidence/{ensembl_id}/{efo}",
                             f"{total} text-mined co-mentions of the gene with {dis.get('name')} or a more specific term; "
                             f"{len(rows)} examined, {len(cand)} papers with an English sentence.{extra}", total=total, examined=len(rows))
    return source_record(name, "not found", note=f"disease id not in Open Targets (tried {', '.join(disease_ids)})")


QUICKGO = "https://www.ebi.ac.uk/QuickGO/services/annotation/search"


@_safe("QuickGO")
def collect_quickgo(acc, go_id, fetch, max_items=8):
    """GO annotations of a protein to a term or its more specific terms (QuickGO), with evidence code and reference."""
    acc = bare(acc)
    params = {"geneProductId": acc, "goId": go_id, "goUsage": "descendants", "goUsageRelationships": "is_a,part_of,occurs_in",
              "limit": 200, "includeFields": "goName"}
    page = f"https://www.ebi.ac.uk/QuickGO/annotations?geneProductId={acc}&goId={go_id}"
    try:
        body = fetch.json("quickgo", QUICKGO, params, headers={"Accept": "application/json"}) or {}
    except Exception:
        # QuickGO answers an error for a term that is no longer in the ontology: ask the ontology service which it is
        term = fetch.json("quickgo", f"https://www.ebi.ac.uk/QuickGO/services/ontology/go/terms/{go_id}",
                          headers={"Accept": "application/json"}) or {}
        hit = next((t for t in term.get("results") or [] if t.get("id") == go_id), None)
        if hit is None or hit.get("isObsolete"):
            return source_record("QuickGO", "not found", url=page,
                                 note=f"{go_id} is {'obsolete in' if hit else 'not in'} the current Gene Ontology, so its annotations cannot be looked up")
        raise
    seen, rows = set(), []
    for r in body.get("results") or []:
        k = (r.get("goId"), r.get("goEvidence"), r.get("reference"), r.get("qualifier"))
        if k not in seen:
            seen.add(k); rows.append(r)

    def manual(r):
        return r.get("goEvidence") not in ("IEA", "ND")

    def negated(r):
        return "NOT" in (r.get("qualifier") or "").upper().split("|")

    rows.sort(key=lambda r: (negated(r), not manual(r), r.get("goId") != go_id, str(r.get("reference"))))
    items = [make_item(
        f"QuickGO: {acc} annotated to {r.get('goId')} {r.get('goName') or ''} "
        f"({'the same term' if r.get('goId') == go_id else 'a more specific term'}), qualifier {r.get('qualifier')}, "
        f"evidence code {r.get('goEvidence')} ({'electronic' if r.get('goEvidence') == 'IEA' else 'no data' if r.get('goEvidence') == 'ND' else 'manual'}), "
        f"reference {r.get('reference')}, assigned by {r.get('assignedBy')}"
        + ("; NOT annotation: the protein was found not to have it" if negated(r) else ""),
        f"https://www.ebi.ac.uk/QuickGO/annotations?geneProductId={acc}&goId={go_id}", "evidence",
        evidence_code=r.get("goEvidence"), negated=negated(r)) for r in rows[:max_items]]
    note = (f"{len(rows)} distinct annotations to the term or more specific terms: "
            f"{sum(manual(r) and not negated(r) for r in rows)} manual, "
            f"{sum((not manual(r)) and not negated(r) for r in rows)} electronic or no-data, {sum(negated(r) for r in rows)} NOT")
    return source_record("QuickGO", "answered", items, f"https://www.ebi.ac.uk/QuickGO/annotations?geneProductId={acc}&goId={go_id}",
                         note, total=len(rows), examined=len(rows))


@_safe("Reactome")
def collect_reactome_pathway(acc, pathway_id, fetch):
    """Is the protein a participant of the Reactome pathway (current release)?"""
    acc, st = bare(acc), str(pathway_id).split(":", 1)[-1]
    url = f"https://reactome.org/content/detail/{st}"
    body = fetch.json("reactome", REACTOME + f"participants/{st}/referenceEntities")
    if body is None:
        return source_record("Reactome", "not found", url=url, note=f"pathway {st} is not in Reactome's current release")
    members = {r.get("identifier") for r in body if r.get("databaseName") == "UniProt"}
    items = ([make_item(f"Reactome pathway {st}: UniProt {acc} is listed among its {len(members)} participant proteins",
                        url, "evidence")] if acc in members else [])
    return source_record("Reactome", "answered", items, url, f"pathway {st} has {len(members)} participant proteins; "
                         f"{acc} is {'among' if items else 'not among'} them", total=len(items), examined=len(members))


@_safe("WikiPathways")
def collect_wikipathways(gene, pathway_id, fetch):
    """Is the gene listed in the WikiPathways pathway's participant table? gene: {symbol, ncbigene, ensembl, uniprot}."""
    wp = str(pathway_id).split(":", 1)[-1]
    url = f"https://www.wikipathways.org/wikipathways-assets/pathways/{wp}/{wp}-datanodes.tsv"
    text = fetch.text("wikipathways", url)
    page = f"https://www.wikipathways.org/pathways/{wp}.html"
    if text is None:
        return source_record("WikiPathways", "not found", url=page, note=f"pathway {wp} is not in WikiPathways' current release")
    rows = [l.split("\t") for l in text.splitlines()[1:] if l.strip()]
    wanted = {v for v in (gene.get("symbol"), gene.get("ncbigene"), gene.get("ensembl"), gene.get("uniprot")) if v}

    def tokens(r):                 # identifier columns only (not the free-text label); 'ncbigene:4846' also gives '4846'
        out = set()
        for cell in r[2:]:
            for c in re.split(r"[;,| ]+", cell.strip().strip('"')):
                out |= {c, c.split(":")[-1]}
        return out

    hit = next((r for r in rows if wanted & tokens(r)), None)
    items = ([make_item(f"WikiPathways {wp}: {gene.get('symbol') or sorted(wanted)[0]} is listed in the pathway's "
                        f"participant table ({len(rows)} entries)", page, "evidence")] if hit else [])
    return source_record("WikiPathways", "answered", items, page, f"pathway {wp} lists {len(rows)} participants; the gene is "
                         f"{'among' if items else 'not among'} them", total=len(items), examined=len(rows))


CHEMBL = "https://www.ebi.ac.uk/chembl/api/data/"


@_safe("ChEMBL")
def collect_chembl(compound_id, acc, fetch, max_items=8):
    """ChEMBL activity records of a compound against targets that contain the protein."""
    acc, cid = bare(acc), str(compound_id).split(":", 1)[-1]
    if not re.fullmatch(r"CHEMBL\d+", cid):
        return source_record("ChEMBL", "not found", note=f"{compound_id} is not a ChEMBL compound id")
    tb = fetch.json("chembl", CHEMBL + "target.json", {"target_components__accession": acc, "limit": 100}) or {}
    targets = {t["target_chembl_id"]: t for t in tb.get("targets") or []}
    page = f"https://www.ebi.ac.uk/chembl/compound_report_card/{cid}/"
    if not targets:
        return source_record("ChEMBL", "answered", [], page, f"{acc} is not a component of any ChEMBL target", total=0, examined=0)
    ab = fetch.json("chembl", CHEMBL + "activity.json", {"molecule_chembl_id": cid, "target_chembl_id__in": ",".join(sorted(targets)),
                                                         "limit": 200}) or {}
    acts = ab.get("activities") or []

    def p(a):
        try:
            return float(a.get("pchembl_value"))
        except (TypeError, ValueError):
            return -1.0

    def single(a):
        return (targets.get(a.get("target_chembl_id")) or {}).get("target_type") == "SINGLE PROTEIN"

    acts.sort(key=lambda a: (not single(a), -p(a), a.get("activity_id") or 0))
    items = []
    for a in acts[:max_items]:
        tt = (targets.get(a.get("target_chembl_id")) or {}).get("target_type") or "target type not given"
        value = " ".join(str(x) for x in (a.get("standard_type"), a.get("standard_relation"), a.get("standard_value"), a.get("standard_units")) if x)
        items.append(make_item(
            f"ChEMBL activity {a.get('activity_id')}: {a.get('molecule_pref_name') or cid} on {a.get('target_pref_name')} "
            f"({tt.lower()}): {value or 'no standard value'}; pChEMBL {a.get('pchembl_value') or 'not given'}; "
            f"assay {a.get('assay_chembl_id')} (type {a.get('assay_type')}); {a.get('document_journal') or 'source'} "
            f"{a.get('document_year') or ''}; data-validity warning: {a.get('data_validity_comment') or 'none'}; "
            f"comment: {a.get('activity_comment') or 'none'}",
            f"https://www.ebi.ac.uk/chembl/assay_report_card/{a.get('assay_chembl_id')}/", "evidence",
            pchembl=a.get("pchembl_value"), single_protein=single(a)))
    total = (ab.get("page_meta") or {}).get("total_count", len(acts))
    return source_record("ChEMBL", "answered", items, page,
                         f"{total} activity records of {cid} against targets containing {acc}; "
                         f"{sum(single(a) and p(a) >= 5 for a in acts)} on the single protein with pChEMBL 5 or more",
                         total=total, examined=len(acts))


IMPC = "https://www.ebi.ac.uk/mi/impc/solr/"


@_safe("IMPC")
def collect_impc(symbol, mp_id, fetch, max_items=6):
    """Significant phenotype calls of the mouse orthologue's knockout (IMPC) for a Mammalian Phenotype term: calls to
    the same or a more specific term are evidence; calls to a broader term are context."""
    if not symbol:
        return source_record("IMPC", "not found", note="no human gene symbol for the subject")
    if prefix_of(mp_id) != "MP":
        return source_record("IMPC", "not applicable", note=f"{mp_id} is not a Mammalian Phenotype term")
    gb = fetch.json("impc", IMPC + "gene/select", {"q": f'human_gene_symbol:"{symbol}"', "wt": "json", "rows": 5,
                                                   "fl": "marker_symbol,mgi_accession_id,phenotype_status,phenotyping_data_available"}) or {}
    genes = (gb.get("response") or {}).get("docs") or []
    if not genes:
        return source_record("IMPC", "not found", note=f"IMPC lists no mouse orthologue of {symbol}")
    g = genes[0]
    page = f"https://www.mousephenotype.org/data/genes/{g.get('mgi_accession_id')}"
    cb = fetch.json("impc", IMPC + "genotype-phenotype/select", {
        "q": f'marker_accession_id:"{g.get("mgi_accession_id")}"', "wt": "json", "rows": 500,
        "fl": "mp_term_id,mp_term_name,intermediate_mp_term_id,top_level_mp_term_id,p_value,zygosity,sex,procedure_name,parameter_name"}) or {}
    calls = (cb.get("response") or {}).get("docs") or []
    mb = fetch.json("impc", IMPC + "mp/select", {"q": f'mp_id:"{mp_id}"', "wt": "json", "rows": 1,
                                                 "fl": "mp_id,intermediate_mp_id,top_level_mp_id"}) or {}
    mdoc = ((mb.get("response") or {}).get("docs") or [{}])[0]
    broader = set(mdoc.get("intermediate_mp_id") or []) | set(mdoc.get("top_level_mp_id") or [])
    items, n_match = [], 0
    for c in calls:
        up = set(c.get("intermediate_mp_term_id") or []) | set(c.get("top_level_mp_term_id") or [])
        rel = ("the same term" if c.get("mp_term_id") == mp_id else "a more specific term than the claimed one" if mp_id in up
               else "a broader term than the claimed one" if c.get("mp_term_id") in broader else "")
        if not rel:
            continue
        ev = not rel.startswith("a broader")
        n_match += ev
        pv = c.get("p_value")
        items.append(make_item(
            f"IMPC, {g.get('marker_symbol')} knockout ({c.get('zygosity')}, {c.get('sex')}): significant call "
            f"{c.get('mp_term_id')} {c.get('mp_term_name')} ({rel}), p = {pv:.2g}" if isinstance(pv, (int, float)) else
            f"IMPC, {g.get('marker_symbol')} knockout ({c.get('zygosity')}, {c.get('sex')}): significant call "
            f"{c.get('mp_term_id')} {c.get('mp_term_name')} ({rel})", page, "evidence" if ev else "context"))
    items.sort(key=lambda i: i["kind"] != "evidence")
    has_data = bool(calls) or bool(g.get("phenotyping_data_available")) or "complete" in str(g.get("phenotype_status") or "").lower()
    note = (f"mouse orthologue {g.get('marker_symbol')}: {len(calls)} significant calls in all, {n_match} for the claimed "
            f"term or a more specific one; phenotyping data available: {'yes' if has_data else 'not stated'}")
    return source_record("IMPC", "answered" if has_data else "not found", items[:max_items], page, note, total=n_match, examined=len(calls))


@_safe("UniProt")
def collect_uniprot_gene(acc, gene_id, fetch):
    """Does the UniProt entry of the protein cross-reference the gene (NCBIGene, HGNC or ENSEMBL id)?"""
    acc = bare(acc)
    url = f"https://rest.uniprot.org/uniprotkb/{acc}.json"
    body = fetch.json("uniprot", url, {"fields": "accession,gene_names,xref_hgnc,xref_geneid,xref_ensembl"})
    page = f"https://www.uniprot.org/uniprotkb/{acc}/entry"
    if not body:
        return source_record("UniProt", "not found", url=page, note=f"no UniProt entry {acc}")
    if "inactive" in str(body.get("entryType") or "").lower():
        return source_record("UniProt", "not found", url=page, note=f"UniProt entry {acc} is obsolete")
    pre, loc = prefix_of(gene_id), str(gene_id).split(":", 1)[-1]
    xr = {"NCBIGene": set(), "HGNC": set(), "ENSEMBL": set()}
    for x in body.get("uniProtKBCrossReferences") or []:
        if x.get("database") == "GeneID":
            xr["NCBIGene"].add(str(x.get("id")))
        elif x.get("database") == "HGNC":
            xr["HGNC"].add(str(x.get("id")).split(":")[-1])
        elif x.get("database") == "Ensembl":
            xr["ENSEMBL"] |= {p["value"].split(".")[0] for p in x.get("properties") or [] if p.get("key") == "GeneId"}
    if pre not in xr:
        return source_record("UniProt", "not found", url=page, note=f"{gene_id}: not an NCBI Gene, HGNC or Ensembl gene id")
    ok = loc in xr[pre]
    items = [make_item(f"UniProt {acc} ({body.get('entryType')}) cross-references {gene_id}", page, "evidence")] if ok else []
    return source_record("UniProt", "answered", items, page,
                         f"UniProt {acc} lists {pre} ids: {', '.join(sorted(xr[pre])) or 'none'}", total=len(items), examined=len(xr[pre]))


@_safe("STRING")
def collect_string(name_a, name_b, fetch):
    """STRING's current channel scores for a protein pair. Context only: STRING is the source of most interaction rows."""
    if name_a == name_b:
        return source_record("STRING", "not applicable", note="self-interaction")
    body = fetch.json("string", "https://string-db.org/api/json/network",
                      {"identifiers": f"{name_a}\r{name_b}", "species": 9606, "required_score": 0}) or []
    url = f"https://string-db.org/network/9606/{name_a}"
    if not body:
        return source_record("STRING", "answered", [], url, "no link between the two in STRING's current release", total=0, examined=0)
    e = body[0]
    chan = [("experiments", "escore"), ("curated databases", "dscore"), ("text mining", "tscore"), ("co-expression", "ascore"),
            ("neighbourhood", "nscore"), ("gene fusion", "fscore"), ("co-occurrence across genomes", "pscore")]
    t = (f"STRING current release, {e.get('preferredName_A')} and {e.get('preferredName_B')}: combined score {e.get('score')}; "
         + ", ".join(f"{n} {e.get(k)}" for n, k in chan) + ". Context only: STRING is the same kind of source as the row")
    return source_record("STRING", "answered", [make_item(t, url, "context")], url, "link present in STRING's current release",
                         total=1, examined=1)


# ------------------------------------------------------------------------------------------------ the dossier
def edge_group(edge):
    """The decision-rule group of an edge: protein_protein, gene_disease, gene_ontology, pathway, ligand, phenotype,
    gene_product or other."""
    p = str(edge.get("predicate", "")).split(":")[-1]
    s, o = prefix_of(edge.get("subject_id", "")), prefix_of(edge.get("object_id", ""))
    if p == "has_gene_product":
        return "gene_product"
    if p == "physically_interacts_with" and s == "UniProtKB" and o == "UniProtKB":
        return "protein_protein"
    if p == "directly_physically_interacts_with" or s in COMPOUND_PREFIXES:
        return "ligand"
    if o == "GO":
        return "gene_ontology"
    if o in PATHWAY_PREFIXES:
        return "pathway"
    if p == "has_phenotype":
        return "phenotype"
    if p in ("gene_associated_with_condition", "genetically_associated_with") or o in DISEASE_PREFIXES:
        return "gene_disease"
    return "other"


REQUIRED = {"protein_protein": ["IntAct", "Complex Portal", "Reactome", "Europe PMC co-mention search"],
            "gene_disease": ["Open Targets", "Open Targets literature sentences", "Europe PMC co-mention search"],
            "gene_ontology": ["QuickGO"],
            "pathway": ["Reactome", "WikiPathways"], "ligand": ["ChEMBL"], "phenotype": ["IMPC"],
            "gene_product": ["UniProt"], "other": ["Europe PMC co-mention search"]}


def _gene_side(node_id, name, fetch, lookups):
    """(names for literature search, {symbol, ensembl, uniprot, ncbigene}) for a gene or protein node."""
    g = dict(lookups.gene(node_id))
    names = [n for n in (name, g.get("symbol")) if n]
    acc = bare(node_id) if prefix_of(node_id) == "UniProtKB" else g.get("uniprot", "")
    if acc:
        pn = protein_names(acc, fetch)
        names += pn.get("names") or []
        g.setdefault("uniprot", acc)
        if not g.get("ensembl"):
            g["ensembl"] = pn.get("ensembl", "")
        if not g.get("symbol") and pn.get("names"):
            g["symbol"] = pn["names"][0]
    seen, out = set(), []
    for n in names:
        if n and n.lower() not in seen and not n.startswith("(no name"):
            seen.add(n.lower()); out.append(n)
    return out[:5], g


def collect_for_edge(edge, fetch, lookups):
    """The source records for one edge, in the order a rater reads them (the group's collectors)."""
    group = edge_group(edge)
    s_id, o_id = edge.get("subject_id", ""), edge.get("object_id", "")
    s_name, o_name = edge.get("subject_name", ""), edge.get("object_name", "")
    own = [str(edge.get("source", ""))]
    refs = pmids_in(edge.get("references"))
    if group == "protein_protein":
        na, ga = _gene_side(s_id, s_name, fetch, lookups)
        nb, gb = _gene_side(o_id, o_name, fetch, lookups)
        out = [collect_intact(s_id, o_id, fetch, own), collect_complex_portal(s_id, o_id, fetch),
               collect_reactome(s_id, o_id, fetch, own)]
        if bare(s_id) == bare(o_id):
            out.append(source_record("Europe PMC co-mention search", "not applicable", note="self-interaction"))
        else:
            out.append(collect_comention(na, nb, fetch))
        out += [collect_references(refs, na, nb, fetch),
                collect_string(ga.get("symbol") or s_name, gb.get("symbol") or o_name, fetch)]
        return out
    if group == "gene_disease":
        na, ga = _gene_side(s_id, s_name, fetch, lookups)
        nb = lookups.disease_names(o_id, o_name)
        ids = lookups.open_targets_ids(o_id)
        ot = collect_open_targets(ga.get("ensembl", ""), ids, fetch, own)
        if ot["status"] == "not found" and ga.get("ensembl") and "disease id" in ot["note"]:
            by_name = open_targets_disease_by_name(nb, fetch)          # the node's id is not an Open Targets id: exact name
            if by_name:
                ids = [by_name]
                ot = collect_open_targets(ga["ensembl"], ids, fetch, own)
                ot["note"] += f" (disease matched to Open Targets by its exact name: {by_name})"
        return [ot, collect_ot_literature(ga.get("ensembl", ""), ids, fetch),
                collect_comention(na, nb, fetch, narrower_b=lookups.narrower(o_id)), collect_references(refs, na, nb, fetch)]
    if group == "gene_ontology":
        return [collect_quickgo(s_id, local_id(o_id), fetch)]
    if group == "pathway":
        pre = prefix_of(o_id)
        if pre == "REACT":
            return [collect_reactome_pathway(s_id, o_id, fetch)]
        if pre == "WIKIPATHWAYS":
            _, g = _gene_side(s_id, s_name, fetch, lookups)
            return [collect_wikipathways(g, o_id, fetch)]
        return [source_record("KEGG", "not applicable", note="KEGG is not queried (licensed for academic use only)")]
    if group == "ligand":
        return [collect_chembl(s_id, o_id, fetch)]
    if group == "phenotype":
        _, g = _gene_side(s_id, s_name, fetch, lookups)
        return [collect_impc(g.get("symbol") or s_name, local_id(o_id), fetch)]
    if group == "gene_product":
        return [collect_uniprot_gene(o_id, s_id, fetch)]
    na = _gene_side(s_id, s_name, fetch, lookups)[0] if prefix_of(s_id) in ("UniProtKB", "NCBIGene") else [s_name]
    nb = _gene_side(o_id, o_name, fetch, lookups)[0] if prefix_of(o_id) in ("UniProtKB", "NCBIGene") else [o_name]
    return [collect_comention(na, nb, fetch), collect_references(refs, na, nb, fetch)]


def number_items(sources):
    """Gives every item its id (source prefix + running number, e.g. IA1) in place and returns the id list."""
    ids, counts = [], {}
    for s in sources:
        pre = ITEM_PREFIX.get(s["source"], "XX")
        for it in s["items"]:
            counts[pre] = counts.get(pre, 0) + 1
            it["id"] = f"{pre}{counts[pre]}"
            ids.append(it["id"])
    return ids


def build_dossier(edge, fetch, lookups, today=None, edge_id=""):
    """The evidence dossier of one edge. Only BLIND_FIELDS of `edge` are read and stored. Returns
    {schema, edge_id, edge, group, required, collected, sources}."""
    blind = {k: ("" if edge.get(k) is None else str(edge.get(k))) for k in BLIND_FIELDS}
    sources = collect_for_edge(blind, fetch, lookups)
    number_items(sources)
    group = edge_group(blind)
    return {"schema": SCHEMA_VERSION, "edge_id": edge_id, "edge": blind, "group": group,
            "required": [s for s in REQUIRED[group] if any(x["source"] == s for x in sources)],
            "collected": today or datetime.date.today().isoformat(), "sources": sources}


def dossier_items(dossier):
    return [it for s in dossier["sources"] for it in s["items"]]


def claim_text(edge):
    return (f"Subject: {edge.get('subject_name')} ({edge.get('subject_id')})\n"
            f"Predicate: {str(edge.get('predicate')).split(':')[-1]}\n"
            f"Object: {edge.get('object_name')} ({edge.get('object_id')})\n"
            f"Source of the row: {edge.get('source')}; knowledge level: {edge.get('knowledge_level') or 'not given'}; "
            f"the source's own statistic: {edge.get('source_statistic') or 'none'}; references: {edge.get('references') or 'none'}")


def dossier_text(dossier):
    """The dossier as the text a rater reads: the claim, then per source its status line and its numbered items,
    evidence items first, context items in a separate block. No verdict, confidence, reasons or edge id."""
    lines = ["CLAIM", claim_text(dossier["edge"]), "", f"EVIDENCE DOSSIER (collected {dossier['collected']})"]
    context = []
    for s in dossier["sources"]:
        req = " [required source]" if s["source"] in dossier.get("required", []) else ""
        lines.append(f"- {s['source']}{req}: {s['status']}. {s.get('note') or ''}".rstrip())
        for it in s["items"]:
            (context if it["kind"] == "context" else lines).append(f"    [{it['id']}] {it['text']}")
    if context:
        lines += ["", "CONTEXT ITEMS (shown for understanding; they cannot be the basis of 'supported')"] + context
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------ rules and raters
RULES_COMMON = """You are an auditor. Decide whether ONE claim from a biomedical knowledge graph is backed by the evidence dossier below. Use only the dossier. Do not use anything you remember about these entities.

Labels:
- supported: at least one EVIDENCE item meets the condition for this kind of claim.
- unsupported: every required source answered and no evidence item meets the condition. Absence from a database is not proof that the claim is false; unsupported means "not backed by any source in the dossier".
- cannot tell: a required source failed or could not match the identifiers and nothing supports the claim; or the evidence is of the in-between kind described below; or a literature search found more abstracts than were examined and none of the examined ones decides.

Context items cannot be the basis of 'supported'. Cite only item ids that appear in the dossier. A 'supported' label must cite at least one evidence item. Do not name any paper, record or database entry that is not in the dossier.
Reply with: label; rationale (40 words or fewer); items (the item ids you relied on, possibly empty for 'unsupported')."""

RULES_GROUP = {
    "protein_protein": """Kind of claim: the two proteins interact physically (direct binding or membership of one complex).
supported if at least one of: (a) an IntAct experimental record for the pair of type association, physical association, direct interaction or a more specific type under these (for example phosphorylation reaction), not a negative record; (b) both proteins are participants of one curated Complex Portal complex; (c) both are components of one Reactome complex, or share a Reactome reaction whose name describes binding, complex formation or one protein modifying the other; (d) an abstract sentence states that the two proteins bind, form a complex, or that one directly modifies the other.
cannot tell if the only evidence is a functional link without physical contact: an IntAct record of type colocalization or genetic interaction; a shared Reactome reaction that does not describe binding or modification; an abstract that states only a regulatory, pathway or co-expression relation.
unsupported if all required sources answered and there is nothing: no IntAct record, no shared complex, no shared reaction, and no abstract that links the two proteins (none found, or the two are only named in a list).
If subject and object are the same protein, judge self-interaction: an IntAct record in which the protein is both partners, or a complex that holds two or more copies of it.""",
    "gene_disease": """Kind of claim: the gene or its product is associated with the disease or trait.
supported if at least one of: (a) an Open Targets data type other than literature scores 0.1 or more (for the predicate genetically_associated_with it must be genetic association, curated genetic literature or somatic mutation); (b) an abstract or a text-mined sentence states a relation between this gene and this disease, or a more specific disease under it: causal or risk variant, mutation or expression change found in patients, biomarker, drug target, or a functional role in a model of the disease. Being named together in a list, or in unrelated statements, is not a relation.
cannot tell if Open Targets holds only non-literature evidence below 0.1 and no sentence states a relation; or the sentences use the gene symbol or the disease name in another meaning.
unsupported if the required sources answered, no non-literature Open Targets data type reaches 0.1, and no abstract or sentence states a relation (none found; only list or unrelated co-mentions; or they state that no association was found).""",
    "gene_ontology": """Kind of claim: the protein has this Gene Ontology location, function or process.
supported if QuickGO holds an annotation of this protein to the same term or a more specific term, without the NOT qualifier, with a manual evidence code (any code other than IEA and ND).
cannot tell if the only such annotations are electronic (IEA) or no-data (ND).
unsupported if QuickGO answered and holds no annotation of the protein to the term or a more specific term, or only NOT annotations.""",
    "pathway": """Kind of claim: the protein takes part in this pathway.
supported if the pathway database the row names lists the protein (or its gene) as a participant of that pathway in its current release.
unsupported if that database answered, the pathway exists, and the protein is not a participant.
cannot tell for every KEGG row (KEGG is not queried), and when the pathway id is no longer in the database.""",
    "ligand": """Kind of claim: the compound acts on the protein.
supported if ChEMBL holds at least one activity record of this compound against this protein as a single-protein target with pChEMBL 5 or more, and no data-validity warning on that record.
cannot tell if the only records are against a complex or protein family that contains the protein, carry no pChEMBL value, or carry a data-validity warning; or the compound id is not a ChEMBL id.
unsupported if ChEMBL answered and holds no activity record for the pair, or every record is weaker than pChEMBL 5 or marked inactive.""",
    "phenotype": """Kind of claim: loss of the gene gives this phenotype (mouse knockout).
supported if IMPC lists a significant phenotype call for the orthologue's knockout with the same phenotype term or a more specific term.
cannot tell if no mouse orthologue is found in IMPC, the term is not a Mammalian Phenotype term, or the only significant call is to a broader term.
unsupported if IMPC answered, the orthologue has data, and there is no significant call for the term or a more specific term.""",
    "other": """Kind of claim: the relation named by the predicate holds between subject and object.
supported only if an abstract states that relation. unsupported if the search answered and nothing states it. Otherwise cannot tell.""",
}

JUDGE_SCHEMA = {"type": "object", "required": ["label", "rationale", "items"],
                "properties": {"label": {"type": "string", "enum": LABELS}, "rationale": {"type": "string"},
                               "items": {"type": "array", "items": {"type": "string"}}}}
_ITEM_ID = re.compile(r"\b(?:" + "|".join(sorted(set(ITEM_PREFIX.values()) | {"XX"})) + r")\d+\b")
_RECORD_ID = re.compile(r"\b(?:EBI-\d+|CPX-\d+|R-HSA-\d+|GO:\d{7}|CHEMBL\d+|MP:\d{7}|MONDO[:_]\d{7}|EFO[:_]\d{7})\b")
_PMID = re.compile(r"(?i)\b(?:PMID|PubMed(?:\s+id)?)\s*:?\s*(\d{4,9})\b")


def judge_messages(dossier):
    """The two messages a model rater receives: the rules for the edge's group, then the dossier text."""
    return [{"role": "system", "content": RULES_COMMON + "\n\n" + RULES_GROUP[dossier["group"]]},
            {"role": "user", "content": dossier_text(dossier)}]


def check_label(reply, dossier):
    """Applies the pre-registered corrections to a rater's reply {label, rationale, items}. Returns
    {label, label_raw, rationale, items, problems}: the label becomes 'cannot tell' when the reply cites an item id
    that is not in the dossier, names a PubMed id or database record that is not in the dossier, or says 'supported'
    without citing an evidence item. A reply that is not a valid label is 'cannot tell' with label_raw ''. An
    'unsupported' reply becomes 'cannot tell' (label_raw stays 'unsupported') when a required source of the dossier
    has status 'failed': the rules say so, and this rule does not depend on the rater following them."""
    reply = reply if isinstance(reply, dict) else {}
    raw = reply.get("label") if reply.get("label") in LABELS else ""
    rationale = " ".join(str(reply.get("rationale") or "").split())
    cited = [str(i).strip().strip("[]") for i in (reply.get("items") or []) if str(i).strip()]
    items = {it["id"]: it for it in dossier_items(dossier)}
    text = dossier_text(dossier)
    problems = []
    if not raw:
        problems.append("no valid label in the reply")
    unknown = sorted({i for i in cited if i not in items} | {m for m in _ITEM_ID.findall(rationale) if m not in items})
    if unknown:
        problems.append("cites an item that is not in the dossier: " + ", ".join(unknown))
    outside = sorted({m for m in _RECORD_ID.findall(rationale) if m not in text and m.replace("_", ":") not in text}
                     | {"PubMed " + m for m in _PMID.findall(rationale) if m not in text})
    if outside:
        problems.append("names a record that is not in the dossier: " + ", ".join(outside))
    if raw == "supported" and not any(items[i]["kind"] == "evidence" for i in cited if i in items):
        problems.append("'supported' without citing an evidence item")
    label = raw if raw and not problems else "cannot tell"
    failed = [s["source"] for s in dossier.get("sources") or []
              if s.get("source") in (dossier.get("required") or []) and s.get("status") == "failed"]
    if label == "unsupported" and failed:                  # absence cannot be read while a required source was silent
        label = "cannot tell"
        rationale = (f"{', '.join(failed)} did not answer, so finding nothing cannot be read as unsupported. "
                     + rationale).strip()
    return {"label": label, "label_raw": raw, "rationale": rationale, "items": cited, "problems": problems}


def deterministic_label(dossier):
    """The label for a group that needs no model (gene_product): from the UniProt record alone. None for other groups."""
    if dossier["group"] != "gene_product":
        return None
    s = dossier["sources"][0]
    if s["status"] != "answered":
        label, why = "cannot tell", f"UniProt could not decide: {s.get('note')}"
    elif s["items"]:
        label, why = "supported", "The UniProt entry of the protein cross-references the gene."
    else:
        label, why = "unsupported", f"The UniProt entry of the protein does not cross-reference the gene. {s.get('note')}"
    return {"label": label, "label_raw": label, "rationale": why, "items": [it["id"] for it in s["items"]], "problems": [],
            "model": "deterministic (UniProt cross-reference)"}


def judge(dossier, nim, model):
    """One verdict-blind rating of a dossier by `model` through the app's model client (pipelines/nim.py, structured
    output, the generation settings of config/nim_models.yaml). Returns check_label(...) plus {model, key}. A reply
    that does not match the schema is 'cannot tell'; a transport failure raises (nothing is stored, the row can be
    retried)."""
    det = deterministic_label(dossier)
    if det is not None:
        return det
    role = "audit:" + model
    nim.cfg["roles"][role] = model
    try:
        res = nim.chat(role, judge_messages(dossier), schema=JUDGE_SCHEMA)
        data, key = res.data, res.key
    except ValueError as err:                                # reply did not satisfy the schema
        data, key = {"rationale": f"reply not usable: {str(err)[:120]}"}, ""
    return {**check_label(data, dossier), "model": model, "key": key}


def combine(labels):
    """Takes the raters' labels (three). Returns {proposal, flagged, majority}: proposal = the label all give ('' when
    they differ); flagged = they do not all agree; majority = the label more than half give ('' when there is none)."""
    labels = list(labels)
    counts = {l: labels.count(l) for l in set(labels)}
    top = max(counts, key=lambda l: (counts[l], l)) if counts else ""
    unanimous = len(counts) == 1 and bool(labels)
    return {"proposal": top if unanimous else "", "flagged": not unanimous,
            "majority": top if counts and counts[top] * 2 > len(labels) else ""}


def cut_words(text, n=40):
    w = str(text or "").split()
    return " ".join(w[:n]) + (" ..." if len(w) > n else "")


def audit_record(dossier, judgements):
    """The stored record of an audited edge for the app's 'independent check': sources consulted with links, what
    each returned, the judgement with its rationale, the date. judgements: the raters' judge() results."""
    c = combine([j["label"] for j in judgements]) if judgements else {"proposal": "", "flagged": True, "majority": ""}
    label = c["proposal"] or c["majority"] or "cannot tell"
    agree = [j for j in judgements if j["label"] == label]
    if judgements and not (c["proposal"] or c["majority"]):          # no label has a majority: say so, cite nothing
        said = ", ".join(sorted(j["label"] for j in judgements))
        agree = []
        judgements = [dict(judgements[0], rationale=f"The raters did not agree ({said}), so the check is undecided.", items=[])] + list(judgements[1:])
    return {"schema": SCHEMA_VERSION, "edge_id": dossier.get("edge_id", ""), "checked": dossier["collected"], "group": dossier["group"],
            "statement": STATEMENT,
            "sources": [{"source": s["source"], "status": s["status"], "url": s["url"], "returned": s.get("note") or "",
                         "items": [{"id": it["id"], "text": it["text"], "url": it["url"], "kind": it["kind"]} for it in s["items"]]}
                        for s in dossier["sources"]],
            "judgement": {"label": label, "rationale": cut_words(agree[0]["rationale"] if agree else (judgements[0]["rationale"] if judgements else ""), 60),
                          "raters": len(judgements), "raters_agreeing": len(agree),
                          "items": sorted({i for j in agree for i in j["items"]})}}
