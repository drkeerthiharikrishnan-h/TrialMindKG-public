"""
"What the agent did": a dated log of the build, in user words, read from committed files only.

    GET /api/agent-log  ->  {available, entries: [...], counts, records, wording}

Every entry is computed from a record that is committed to the repository; no entry is written by hand and no
number is typed here (CLAUDE.md rules 1 and 7). The records, and what each one gives:

  pull record            data/reference/pharos_manifest.tsv     what was pulled from Pharos, when, how many rows
  download record        data/external/DATA_MANIFEST.tsv        which reference files were downloaded, when
  build record of a layer  outputs/<layer>/summary.md           rows kept, rows merged, rows left out and why
  assembled graph record   outputs/g<n>/summary.md              layers put together, links left out by pruning
  pruning report         outputs/pharos/prune_report.md         links kept / marked prune, by evidence kind and source
  change record          outputs/maintain_diff.md, outputs/maintain/diff_<layer>.md   what changed since the build before
  experiment log         loop/ledger.tsv                        dates; what the agent recorded each day; the decisions a
                                                                scientist took (marked "Scientist decision")

Wording: the text a user sees holds no file path, run tag, graph code, packet id, layer file name or
model-operations word. internal_terms() is the check; the builder drops any quoted clause that fails it and
tests/test_agent_log.py runs it (and its own list) on the whole JSON. That is why a cite is a record title, not a
path: record_files() maps every cite back to its committed file for the test and for developers.

Dates: a build record carries no date, so an entry is dated by (1) the first experiment-log row that states the
build's link count, else (2) the last row that names the record, else (3) the download date written in the record;
`date_from` says which, in words. An entry with no dated record is listed last with date null.

Functions (each takes the repo root unless said otherwise)
  internal_terms(text)      -> the internal terms found in a text ([] = clean)
  ledger_rows(root)         -> [{line, date, id, hypothesis, preregistered_threshold, result_file, decision, judged_by}]
  shipped_records(root)     -> the layer and assembled-graph summaries the log reads (layers with a source card only)
  build(root)               -> entries with their private fields (_file: the committed file each one was read from)
  agent_log(root)           -> the endpoint's JSON (private fields removed)
  record_files(root)        -> {record title: committed file} for every cite in the log
"""
import csv
import datetime
import re
import sys
from pathlib import Path

from fastapi import APIRouter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema_card import parse_summary, layer_summaries  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
router = APIRouter()

LEDGER = "loop/ledger.tsv"
PHAROS_MANIFEST = "data/reference/pharos_manifest.tsv"
DATA_MANIFEST = "data/external/DATA_MANIFEST.tsv"
PRUNE_REPORT = "outputs/pharos/prune_report.md"
COMBINED_DIFF = "outputs/maintain_diff.md"

# Reader-facing names. A name that is missing here is shown with its underscores turned into spaces.
LAYER_NAMES = {"pharos": "Pharos", "dto": "Drug Target Ontology classes", "opentargets": "Open Targets",
               "opentargets_layer": "Open Targets", "chembl37": "ChEMBL", "go_hierarchy": "Gene Ontology hierarchy",
               "hpo_hierarchy": "Human Phenotype Ontology hierarchy", "mondo_hierarchy": "MONDO disease hierarchy"}
SOURCE_NAMES = {"biolink": "BioLink model", "mondo": "MONDO", "hpo": "Human Phenotype Ontology", "go": "Gene Ontology",
                "hgnc": "HGNC", "reactome": "Reactome", "gtex": "GTEx", "onsides": "OnSIDES", "optimuskg": "OptimusKG",
                "chembl": "ChEMBL", "drugcentral": "DrugCentral"}
LEVEL_WORDS = {"text_co_occurrence": "text mining (two names in the same text)",
               "knowledge_assertion": "stated by a source as knowledge",
               "statistical_association": "statistical association", "not_provided": "kind not given by the source"}
# First words of the experiment log's decision column -> what kind of entry it is, in user words.
STATUS_WORDS = [
    (("pre-registered", "planned", "amended"), "pass rules or plans written down before a result was seen"),
    (("accepted", "pass", "passed"), "results that passed their rule"),
    (("rejected", "primary not passed"), "results that did not pass their rule"),
    (("done", "reported", "measured", "mixed", "built", "merged", "smoke ok", "replicates"), "results reported"),
    (("decided",), "decisions"),
    (("correction",), "corrections of an earlier entry"),
    (("aborted", "stopped", "superseded", "not run", "cancelled"), "runs stopped or withdrawn"),
    (("pending", "running", "open", "in progress", "blocked", "unblocked"), "notes on work in progress"),
]
# What a scientist's decision was about: first match on the row's id and text (a label, not a conclusion).
DECISION_TOPICS = [
    (r"spot ?check", "how pruned and kept links are checked by hand, blind to the verdict"),
    (r"\bmesh\b", "which disease identifiers to leave out of an identifier mapping"),
    (r"class per id prefix|biolink class|classes\b", "how diseases and traits are classed"),
    (r"pruner", "which pruning settings are shipped"),
    (r"hosted|\baws\b", "where and how the demonstration is hosted"),
    (r"\bgpu\b|\bbrev\b|h100", "where a benchmark run is computed"),
]


def _topics(r):
    """Takes an experiment-log row. Returns what the decision was about: the topic its id names, else every topic
    its text names, in DECISION_TOPICS order ([] when none)."""
    by_id = [w for pat, w in DECISION_TOPICS if re.search(pat, r["id"].lower())]
    blob = (r["hypothesis"] + " " + r["judged_by"]).lower()
    return by_id[:1] or [w for pat, w in DECISION_TOPICS if re.search(pat, blob)]

# ---------------------------------------------------------------------------------------------------- wording
_INTERNAL = [
    ("file path", r"(?:^|[\s(`'\"])(?:~|outputs|data|pipelines|scripts|docs|loop|logs|sources|tests|bin|aws|raw|config|"
                  r"examples|harness)/\S+"),
    ("file name", r"\b[\w.-]+\.(?:py|md|tsv|csv|json|ya?ml|gz|xlsx|txt|sh|log|pt|sqlite|parquet|zst|html)\b"),
    ("run tag", r"@\w+|\b(?:frozen_[ab]\w*|diag_\w+|kfold)\b"),
    ("graph code", r"\bG[0-4]R?\b"),
    ("packet id", r"\b(?:CC|CS|PK|T2)-?R?\d+[A-Za-z]?\b|\bE\d{3}[a-z]?\b|\bM[1-9]\b|\bS0\d\b|\bU\d{1,2}\b|\bES-\d+\b"),
    ("code name", r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b"),
    ("prefixed term", r"\b(?:biolink|infores|tmkg):"),
    ("model-operations word", r"(?i)\b(?:gpu|h100|l40s|brev|ec2|s3|cloudfront|neptune|nohup|epochs?|seed|digest|"
                              r"checkpoint|docker|commit|branch|session|nim|stand-in|snapshot|claude|codex|mac|sandbox|"
                              r"pytest|svd|tag|cap|lambda|terminal|instance|bundle)\b"),
]


def internal_terms(text):
    """Takes a text. Returns the internal terms found in it as 'kind: term' strings ([] when the text is fit for a
    user): file paths and names, run tags, graph codes, packet ids, code names, prefixed terms, operations words."""
    found = []
    for kind, pat in _INTERNAL:
        found += [f"{kind}: {m.group(0).strip()}" for m in re.finditer(pat, text or "")]
    return found


def _n(v):
    return f"{v:,}"


def _pct(part, whole):
    return f"{100 * part / whole:.2f}%" if whole else "-"


def _date_words(iso):
    d = datetime.date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%b %Y')}"


def _name(layer):
    return LAYER_NAMES.get(layer, layer.replace("_", " "))


def _words(term):
    """'biolink:gene_associated_with_condition' -> 'gene associated with condition'."""
    return term.split(":", 1)[-1].replace("_", " ")


def _version_words(v):
    """Takes a build record's source version. Returns it in user words: a download date becomes 'the download of
    <date>'; a bracket that holds an internal term is dropped."""
    m = re.match(r"download (\d{4}-\d{2}-\d{2})", v or "")
    if m:
        return f"the download of {_date_words(m.group(1))}"
    v = re.sub(r"\s*\(([^()]*)\)", lambda b: "" if internal_terms(b.group(1)) else b.group(0), v or "").strip()
    return f"release {v}" if v and not internal_terms(v) else "the release on record"


def _read(root, rel):
    p = Path(root) / rel
    return p.read_text() if p.is_file() else None


def _table(text, heading):
    """Takes Markdown and the start of a '## ' heading (lower case). Returns the table under it as a list of cell
    lists (header and rule rows left out); [] when the section or table is missing."""
    rows, on, seen_header = [], False, False
    for line in (text or "").splitlines():
        if line.startswith("#"):
            on, seen_header = line.lstrip("# ").lower().startswith(heading), False
            continue
        if on and line.startswith("|"):
            if set(line.replace("|", "").strip()) <= set("-: "):
                continue
            if not seen_header:
                seen_header = True
                continue
            rows.append([c.strip() for c in line.strip("|").split("|")])
    return rows


def _int(s):
    try:
        return int(str(s).replace(",", "").strip())
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------- experiment log
def ledger_rows(root=ROOT):
    """Takes the repo root. Returns the experiment log's rows as dicts, each with `line` (its line in the file,
    header = 1); [] when the file is not there (the hosted image does not carry it)."""
    text = _read(root, LEDGER)
    if text is None:
        return []
    rows = []
    for i, r in enumerate(csv.DictReader(text.splitlines(), delimiter="\t"), start=2):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", (r.get("date") or "").strip()):
            rows.append({**{k: (v or "").strip() for k, v in r.items() if k}, "line": i})
    return rows


def _date_of(rows, count=None, rel=None, version=None):
    """Takes the experiment log rows and what is known about a record: its link count, its file, its source
    version. Returns (date, date_from words, line or None) by the three rules in the module docstring, or
    (None, words, None)."""
    if count is not None:
        hit = [r for r in rows if _n(count) in " ".join(str(v) for v in r.values())]
        if hit:
            return hit[0]["date"], "the first day the experiment log states this count", hit[0]["line"]
    if rel:
        hit = [r for r in rows if rel in r["hypothesis"] + " " + r["result_file"]]
        if hit:
            return hit[-1]["date"], "the last experiment-log entry that names this record", hit[-1]["line"]
    m = re.match(r"download (\d{4}-\d{2}-\d{2})", version or "")
    if m:
        return m.group(1), "the download date written in the record (the build used that download)", None
    return None, "no dated record of this step is available here", None


def _is_scientist(r):
    """A row is a scientist's decision when the scientist is named as its judge, or the row is headed as her decision."""
    return bool(re.search(r"\bOwner\b|\bowner chose\b", r["judged_by"])
                or re.match(r"\(DECISION, owner\b", r["hypothesis"]))


def _status(decision):
    d = decision.lower()
    for starts, words in STATUS_WORDS:
        if any(re.match(re.escape(s) + r"\b", d) for s in starts):
            return words
    return "other entries"


def _clean_quote(text, limit=260):
    """Takes free text from the experiment log (a pass rule). Returns the leading clauses that are fit for a user, joined, at
    most `limit` characters ('' when none is): the opening bracket (time, who) is cut, the text is split at
    sentence ends and semicolons, and a clause with an internal term (internal_terms) is dropped."""
    text = re.sub(r"^\s*(?:[A-Z][A-Z -]+\d*\s*)?\([^)]*\)\s*:?\s*", "", text or "")
    out = []
    for clause in re.split(r"(?<=[.;])\s+|;\s*", text):
        clause = clause.strip().rstrip(";").strip()
        if len(clause) < 12 or internal_terms(clause) or clause.count("(") != clause.count(")"):
            continue
        if sum(len(c) + 1 for c in out) + len(clause) > limit:
            break
        out.append(clause if clause.endswith(".") else clause + ".")
    return " ".join(out)


def _ledger_entries(rows):
    """Takes the experiment log rows. Returns one entry per scientist decision (marked) and one entry per day that
    counts what the agent recorded that day, by kind."""
    out = []
    for r in rows:
        if not _is_scientist(r):
            continue
        topic = "; ".join(_topics(r)) or None
        before = r["decision"].lower().startswith("pre-registered")
        text = ("The scientist fixed the pass rule before looking at the data" if before
                else "The scientist took a decision") + (f" on {topic}." if topic else ".")
        entry = dict(date=r["date"], step="Decision", by="scientist", marked="Scientist decision",
                     title=("Pass rule fixed before the data were seen" if before else "Decision") +
                           (f": {topic}" if topic else ""),
                     text=text, date_from="the date of the experiment-log entry",
                     _cites=[("Experiment log", LEDGER, f"row {r['line'] - 1}")])
        rule = _clean_quote(r["preregistered_threshold"]) if before else ""
        if rule:                             # the pass rule in the log's own words (clauses in shorthand left out)
            entry["rule"] = rule
        out.append(entry)
    by_day = {}
    for r in rows:
        if not _is_scientist(r):
            by_day.setdefault(r["date"], []).append(r)
    order = [w for _, w in STATUS_WORDS] + ["other entries"]
    for day, rs in sorted(by_day.items()):
        kinds = {}
        for r in rs:
            kinds[_status(r["decision"])] = kinds.get(_status(r["decision"]), 0) + 1
        parts = [f"{w}: {kinds[w]}" for w in order if w in kinds]
        out.append(dict(date=day, step="Log", by="agent", marked=None,
                        title="Entries added to the experiment log",
                        text=f"The agent added {len(rs)} entr{'y' if len(rs) == 1 else 'ies'} to the experiment log ("
                             + "; ".join(parts) + "). Each entry names the question, the rule it is judged by, "
                             "the result and who judged it.",
                        date_from="the date of the experiment-log entries",
                        _cites=[("Experiment log", LEDGER, f"rows {rs[0]['line'] - 1} to {rs[-1]['line'] - 1}"
                                 if len(rs) > 1 else f"row {rs[0]['line'] - 1}")]))
    return out


# ------------------------------------------------------------------------------------------------- what was pulled
def _pull_entries(root):
    """Takes the repo root. Returns one entry per day of the Pharos pull record and of the download record."""
    out = []
    text = _read(root, PHAROS_MANIFEST)
    rows = list(csv.DictReader(text.splitlines(), delimiter="\t")) if text else []
    for day in sorted({r["retrieved"][:10] for r in rows if r.get("retrieved")}):
        rs = [r for r in rows if r["retrieved"][:10] == day and _int(r.get("rows")) is not None]
        if not rs:
            continue
        big = max(rs, key=lambda r: _int(r["rows"]))
        versions = sorted({r["version"] for r in rs if r.get("version")})
        out.append(dict(date=day, step="Ingest", by="program", marked=None, title="Pharos pulled as delivered",
                        text=f"Pulled Pharos release {', '.join(versions)} as delivered; the pull record lists "
                             f"{len(rs)} tables: "
                             f"{_n(sum(_int(r['rows']) for r in rs))} rows in all; the largest table is "
                             f"\"{big['list']}\" with {_n(_int(big['rows']))} rows. No row was edited or filtered at "
                             f"this step, and a checksum of every table is on record.",
                        date_from="the pull date written in the pull record",
                        _cites=[("Pull record of Pharos", PHAROS_MANIFEST, f"{len(rs)} rows")]))
    text = _read(root, DATA_MANIFEST)
    rows = list(csv.DictReader(text.splitlines(), delimiter="\t")) if text else []
    for day in sorted({r["retrieved_utc"][:10] for r in rows if r.get("retrieved_utc")}):
        rs = [r for r in rows if r["retrieved_utc"][:10] == day]
        files = {r["file"] for r in rs}
        names = sorted({SOURCE_NAMES.get(r["source"], r["source"]) for r in rs}, key=str.lower)
        names = [n for n in names if not internal_terms(n)]
        out.append(dict(date=day, step="Ingest", by="program", marked=None, title="Reference files downloaded",
                        text=f"Downloaded {len(files)} reference file{'s' if len(files) != 1 else ''} from "
                             f"{len(names)} public source{'s' if len(names) != 1 else ''} ({', '.join(names)}). The "
                             f"address, size and checksum of every file are on record.",
                        date_from="the download date written in the download record",
                        _cites=[("Download record of reference files", DATA_MANIFEST, f"{len(rs)} rows")]))
    return out


# --------------------------------------------------------------------------------------------------- what was built
def _layer_entry(root, rel, ledger):
    """Takes the repo root, one layer's build record (path relative to root) and the experiment log rows. Returns
    the entry: entities and links built, source rows kept, merged and left out (with the reasons), or None."""
    text = _read(root, rel)
    s = parse_summary(text or "")
    if not text or s["n_edges"] is None or not s["title"]:
        return None
    name = _name(s["title"])
    rows_in, links = sum(e["rows"] for e in s["edges"]), sum(e["edges"] for e in s["edges"])
    left = {}
    for cells in _table(text, "source rows not in the graph"):
        if len(cells) == 3 and _int(cells[2]) is not None:
            left[cells[1]] = left.get(cells[1], 0) + _int(cells[2])
    t = (f"Built the {name} layer from {_version_words(s['source_version'])}: {_n(s['n_nodes'])} entities and "
         f"{_n(s['n_edges'])} links.")
    if s["edges"]:
        t += f" {_n(rows_in)} source rows were kept"
        t += (f"; {_n(rows_in - links)} of them repeated a link that was already there and were merged into it."
              if rows_in > links else ".")
    if left:
        why = "; ".join(f"{k}: {_n(v)}" for k, v in sorted(left.items(), key=lambda kv: -kv[1]))
        t += (f" {_n(sum(left.values()))} source rows were left out because their identifier could not be "
              f"standardised ({why}); they are counted, not dropped silently.")
    elif "## source rows not in the graph" in text.lower():
        t += " No source row was left out for an identifier problem."
    date, date_from, line = _date_of(ledger, s["n_edges"], rel, s["source_version"])
    cites = [(f"Build record of the {name} layer", rel, "totals and tables")]
    if line:
        cites.append(("Experiment log", LEDGER, f"row {line - 1}"))
    return dict(date=date, step="Ingest", by="program", marked=None, title=f"{name} layer built", text=t,
                date_from=date_from, _cites=cites)


def _graph_entry(root, rel, ledger):
    """Takes the repo root, one assembled graph's record and the experiment log rows. Returns the entry: which
    layers were put together, entities and links, links left out by pruning; or None."""
    text = _read(root, rel) or ""
    m = re.search(r"^# \S+ \(([^)]*)\)\s*$", text, re.M)
    c = re.search(r"^([\d,]+) nodes, ([\d,]+) edges; ([\d,]+) edges removed by pruning", text, re.M)
    if not (m and c):
        return None
    layers = [_name(x.strip()) for x in m.group(1).split("+")]
    layers = list(dict.fromkeys(layers))
    nodes, edges, removed = (_int(c.group(i)) for i in (1, 2, 3))
    retyped = re.search(r"([\d,]+) node rows re-typed", text)
    if len(layers) == 1:
        t = f"Assembled the {layers[0]} layer alone as the baseline graph: {_n(nodes)} entities and {_n(edges)} links."
        title = f"Baseline graph assembled ({layers[0]} alone)"
    else:
        t = (f"Assembled {len(layers)} layers into one graph ({', '.join(layers)}): {_n(nodes)} entities and "
             f"{_n(edges)} links.")
        title = "Enriched graph assembled" + (", pruned links left out" if removed else "")
    if removed:
        t += (f" {_n(removed)} links marked prune were left out of this version "
              f"({_pct(removed, edges + removed)} of {_n(edges + removed)}).")
    if retyped and _int(retyped.group(1)):
        t += f" {retyped.group(1)} entity rows were given one class per identifier prefix."
    date, date_from, line = _date_of(ledger, edges, rel)
    cites = [("Record of the " + ("baseline graph" if len(layers) == 1 else "enriched graph" +
              (" without pruned links" if removed else "")), rel, "first lines")]
    if line:
        cites.append(("Experiment log", LEDGER, f"row {line - 1}"))
    return dict(date=date, step="Limit" if removed else "Ingest", by="program", marked=None, title=title, text=t,
                date_from=date_from, _cites=cites)


# -------------------------------------------------------------------------------------------------- what was pruned
def _prune_entry(root, ledger):
    """Takes the repo root and the experiment log rows. Returns the pruning entry from the Pharos pruning report:
    links scored, marked prune and kept, the share pruned per kind of evidence, the sources with the most pruned
    links, and the check against Open Targets; or None when the report is missing or has no totals."""
    text = _read(root, PRUNE_REPORT)
    m = re.search(r"edges scored: \**([\d,]+)\**; verdict 'prune': \**([\d,]+)\**", text or "")
    if not m:
        return None
    scored, pruned = _int(m.group(1)), _int(m.group(2))
    t = (f"Gave every one of the {_n(scored)} Pharos links a confidence between 0 and 1: {_n(pruned)} "
         f"({_pct(pruned, scored)}) were marked prune and {_n(scored - pruned)} kept. Pruned links stay in the "
         f"record, marked, with their reasons.")
    prot = re.search(r"edges marked protected in the reasons: ([\d,]+)", text)
    if prot:
        t += f" {prot.group(1)} links were protected from pruning by rule."
    levels = [(c[0], _int(c[1]), _int(c[2])) for c in _table(text, "by knowledge level") if len(c) >= 3]
    levels = [x for x in levels if None not in x[1:]]
    if levels:
        t += " Why, by kind of evidence: " + "; ".join(
            f"{LEVEL_WORDS.get(k, k.replace('_', ' '))}: {_n(p)} of {_n(e)} pruned ({_pct(p, e)})"
            for k, e, p in sorted(levels, key=lambda x: -x[2])) + "."
    srcs = [(c[0], _int(c[1]), _int(c[2])) for c in _table(text, "by source") if len(c) >= 3]
    srcs = [x for x in srcs if None not in x[1:] and x[2] and not internal_terms(x[0])]
    if srcs:
        top = sorted(srcs, key=lambda x: -x[2])[:3]
        t += " Sources with the most pruned links: " + "; ".join(
            f"{k}: {_n(p)} of {_n(e)} ({_pct(p, e)})" for k, e, p in top) + "."
        whole = [k for k, e, p in srcs if p == e]
        if whole:
            t += f" Pruned in full: {', '.join(whole)}."
    for row in _table(text, "three thresholds"):                      # first row = the shipped verdicts
        pr = re.match(r"([\d,]+) / ([\d.]+%)", row[6]) if len(row) >= 8 else None
        kept = re.match(r"([\d.]+%)", row[7]) if len(row) >= 8 else None
        if pr and kept:
            t += (f" Checked against Open Targets, which the pruner never sees: of the {pr.group(1)} pruned "
                  f"gene-disease links that are not text-mined, {pr.group(2)} are also in Open Targets; of the kept "
                  f"ones, {kept.group(1)}.")
        break
    date, date_from, line = _date_of(ledger, scored, PRUNE_REPORT)
    cites = [("Pruning report of the Pharos layer", PRUNE_REPORT, "totals and tables")]
    if line:
        cites.append(("Experiment log", LEDGER, f"row {line - 1}"))
    return dict(date=date, step="Clean", by="program", marked=None, title="Every Pharos link scored: keep or prune",
                text=t, date_from=date_from, _cites=cites)


# ------------------------------------------------------------------------------------- what changed since last time
def _diff_entries(root, ledger):
    """Takes the repo root and the experiment log rows. Returns one entry per layer in the change records
    (the combined record when it exists, else the per-layer ones): links and entities before and after, how many
    groups of links are new, gone or resized, the largest ones, and whether a later build is on record."""
    files = [COMBINED_DIFF] if _read(root, COMBINED_DIFF) else sorted(
        str(p.relative_to(root)) for p in (Path(root) / "outputs" / "maintain").glob("diff_*.md"))
    out = []
    for rel in files:
        for part in re.split(r"(?m)^(?=# Rebuild diff: )", _read(root, rel) or ""):
            h = re.match(r"# Rebuild diff: (\S+)", part)
            if not h:
                continue
            layer, name = h.group(1), _name(h.group(1))
            tot = {c[0]: (_int(c[1]), _int(c[2])) for c in _table(part, "rebuild diff") if len(c) >= 3}
            if "edges" not in tot or None in tot["edges"]:
                continue
            (e0, e1), (n0, n1) = tot["edges"], tot.get("nodes", (None, None))
            groups = [c for c in _table(part, "edges by predicate") if len(c) >= 5]
            new = [(c[0], c[1], _int(c[3])) for c in groups if c[4] == "new"]
            gone = [c for c in groups if c[4] == "gone"]
            resized = len(groups) - len(new) - len(gone)
            if e0 == e1 and not groups:
                t = f"Compared the rebuilt {name} layer with the build before it: no change, {_n(e1)} links in both."
            else:
                t = (f"Compared the rebuilt {name} layer with the build before it: links went from {_n(e0)} to "
                     f"{_n(e1)} ({e1 - e0:+,})")
                t += f" and entities from {_n(n0)} to {_n(n1)} ({n1 - n0:+,})." if None not in (n0, n1) else "."
                bits = [f"{len(new)} new" if new else "", f"{len(gone)} gone" if gone else "",
                        f"{resized} changed in size" if resized else ""]
                t += f" Groups of links (one kind of link from one source): {', '.join(b for b in bits if b)}."
                if new:
                    t += " Largest new groups: " + "; ".join(
                        f"{_words(p)} from {_words(s)} ({_n(k)})" for p, s, k in
                        sorted(new, key=lambda x: -(x[2] or 0))[:3]) + "."
            cites = [(f"Change record of the {name} layer", rel, "totals and tables")]
            cur = parse_summary(_read(root, f"outputs/{layer}/summary.md") or "")["n_edges"]
            if cur is not None and cur != e1:
                t += (f" The build on record today has {_n(cur)} links, {_n(abs(cur - e1))} "
                      f"{'fewer' if cur < e1 else 'more'} than the later build in this comparison; no comparison "
                      f"with today's build is on record yet.")
                cites.append((f"Build record of the {name} layer", f"outputs/{layer}/summary.md", "totals"))
            date, date_from, line = _date_of(ledger, e1, rel)
            if line:
                cites.append(("Experiment log", LEDGER, f"row {line - 1}"))
            out.append(dict(date=date, step="Maintain", by="program", marked=None,
                            title=f"{name} layer compared with the build before it", text=t, date_from=date_from,
                            _cites=cites))
    return out


# ------------------------------------------------------------------------------------------------------- the log
_STEP_ORDER = ["Decision", "Maintain", "Limit", "Clean", "Ingest", "Log"]


def shipped_records(root=ROOT):
    """Takes the repo root. Returns the build records the log may read, as paths relative to root, sorted: the
    summary of every layer that has a source card (sources/*.yaml, the layers the graph is assembled from) and the
    summary of every assembled graph (outputs/g<n>/). Any other outputs/<folder>/summary.md on this machine (an
    example or a trial build that has no source card) is not a layer of the shipped graph and gives no entry."""
    root = Path(root)
    try:
        layers = [rel for _, rel, _ in layer_summaries(root)]
    except Exception:                   # an unreadable source card: no layer entries, never an error
        layers = []
    graphs = [str(p.relative_to(root)) for p in (root / "outputs").glob("g*/summary.md")
              if re.fullmatch(r"g\d+", p.parent.name)]
    return sorted(set(layers) | set(graphs))


def build(root=ROOT):
    """Takes the repo root. Returns every entry, newest first (entries with no date last), each with the public
    fields (date, date_words, date_from, step, by, marked, title, text, and rule when a decision fixed one) and
    the private `_cites`: [(record title, committed file, where in it)]. A record that is missing on this machine
    gives no entry; nothing raises."""
    root = Path(root)
    ledger = ledger_rows(root)
    entries = _pull_entries(root)
    for rel in shipped_records(root):
        e = _graph_entry(root, rel, ledger) if re.match(r"outputs/g\d+/", rel) else _layer_entry(root, rel, ledger)
        if e:
            entries.append(e)
    prune = _prune_entry(root, ledger)
    entries += ([prune] if prune else []) + _diff_entries(root, ledger) + _ledger_entries(ledger)
    for e in entries:
        e["date_words"] = _date_words(e["date"]) if e["date"] else None
    entries.sort(key=lambda e: (e["date"] is None, [-ord(ch) for ch in (e["date"] or "")],
                                _STEP_ORDER.index(e["step"]), e["title"]))
    return entries


def record_files(root=ROOT):
    """Takes the repo root. Returns {record title: committed file} for every record the log cites."""
    return {title: rel for e in build(root) for title, rel, _ in e["_cites"]}


def agent_log(root=ROOT):
    """Takes the repo root. Returns the endpoint's JSON: {available, entries: [{date, date_words, date_from, step,
    by ('program' | 'agent' | 'scientist'), marked ('Scientist decision' or null), title, text, rule?,
    cites: [{record, where}]}], counts: {entries, scientist_decisions, first_date, last_date}, records: the record
    titles cited, missing: what this machine cannot show and why, wording: how to read the entries}. No file path
    is in it; record_files() gives the file behind each record."""
    raw = build(root)
    entries = [{**{k: v for k, v in e.items() if not k.startswith("_")},
                "cites": [dict(record=t, where=w) for t, _, w in e["_cites"]]} for e in raw]
    dates = sorted(e["date"] for e in entries if e["date"])
    missing = []
    if not ledger_rows(root):
        missing.append("The experiment log is not on this machine, so the scientist's decisions and the dates of "
                       "some builds are not shown.")
    if _read(root, PRUNE_REPORT) is None:
        missing.append("The pruning report is not on this machine.")
    return dict(
        available=bool(entries), entries=entries,
        counts=dict(entries=len(entries), scientist_decisions=sum(e["by"] == "scientist" for e in entries),
                    first_date=dates[0] if dates else None, last_date=dates[-1] if dates else None),
        records=sorted({c["record"] for e in entries for c in e["cites"]}), missing=missing,
        wording=dict(
            program="A step done by a program that a person or the weekly check started; the same input gives the "
                    "same output.",
            agent="An entry the coding agent wrote into the experiment log while it ran the loop.",
            scientist="A decision taken by the scientist; marked \"Scientist decision\".",
            rule="A pass rule in the experiment log's own words; parts written in the project's internal "
                 "shorthand are left out.",
            numbers="Every number is read from the record cited under the entry; none is typed in."))


@router.get("/api/agent-log")
def api_agent_log():
    """The dated log of what the agent and the scientist did, in user words, from committed records only."""
    return agent_log(ROOT)
