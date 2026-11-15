"""
Pharos side of the web app (EXECUTION_PLAN.md Phase 4): the Pharos graph with its enrichment layers, pruning
verdicts, GNN predictions and edge timelines, behind read-only functions that app.py exposes.

PharosGraph(root)       loads data/kg/<layer>/{nodes,edges,edge_verdicts}.tsv.gz for every layer that is built
                        (LAYERS order, Pharos first; the same files make_graphs.py assembles) and
                        outputs/linkpred/predictions_metapath.tsv.gz (named paths + edge ids) or, if absent,
                        outputs/linkpred/predictions.tsv.gz when linkpred/predict.py has written it.
                        Same interface as kg_query.KGIndex (nodes, edges, by_subject, by_object, out, inn, name,
                        version), so querier.run_local and orchestrator.graph_json work on it.
  .search(q, limit)     nodes whose id, name or symbol contains q
  .node_view(id, limit) the node and its edges grouped by predicate, each with layer, source, verdict, confidence
  .explain_edge(id)     provenance, pruning verdict with reasons, the same pair in other layers (corroboration),
                        and a dated timeline (what we knew, what we know now) computed from those rows
  .predicted_links(id, n, task)  top-n GNN predictions for a target or disease, each with up to 3 two-hop
                        supporting paths in the graph and the layers that already hold the pair
  .card(id, n_pred)     the entity card (one screen per target / disease / compound): about, known links grouped by
                        evidence kind with kept/pruned counts, predicted links with paths, a small picture
  .status()             layers, node and edge counts, verdict counts, predictions available
  .schema_card()        the querier's schema card computed from the tables
  .demo()               the fixed Pharos questions (DEMO_QUESTIONS) run on the graph with their checks
ask(graph, nim, question) -> querier.Querier over the Pharos graph; entities found by exact id / name match; a question
                        with a tested question's text gets that tested answer (tested_question, tested_answer)
entity_caveats(notes, cypher, label) -> the "read as a shorter name" notes without those the executed query resolved
display_result(answer)  -> the answer with its row values as the page prints them (decimals at 3 significant figures)

Memory: edges stay in one pandas table (categorical columns); edge dicts are built on demand, so the 16M-edge
Pharos graph fits a 16 GB laptop. TMKG_PHAROS_SKIP_PREDICATES (comma-separated predicates) leaves some out,
e.g. biolink:physically_interacts_with (11M PPI rows) on a smaller machine.
"""
import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_graphs  # noqa: E402
import timeline  # noqa: E402
from kg_schema import local  # noqa: E402
import personas  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LAYERS = [l for l in make_graphs.RECIPES["G4"]["layers"] if l != make_graphs.TRIAL_LAYER]
PREDICTIONS = ROOT / "outputs" / "linkpred" / "predictions.tsv.gz"
PREDICTIONS_METAPATH = ROOT / "outputs" / "linkpred" / "predictions_metapath.tsv.gz"
MODEL_WORDS = {"gnn": "R-GCN link prediction", "metapath": "meta-path model"}
KGX = ["id", "subject", "predicate", "object", "source", "source_record", "primary_knowledge_source",
       "aggregator_knowledge_source", "knowledge_level", "agent_type", "layer", "source_version", "n_records"]
# release month of a layer's source files, used to date timeline events (Pharos 3.19.2 source downloads: ~Feb 2024;
# Open Targets 26.09 = September 2026). Layers not listed are dated by a property of the edge (first_year) or not at all.
# PK6 (ES-14): the Drug Target Ontology table comes with Pharos 3.19.2 (sources/dto.yaml: version pharos319); ChEMBL 37 is
# dated 2026-05-01 by ChEMBL's own status response (outputs/run_manifest.json, sources: "ChEMBL_37 (2026-05-01)"); the GO, HPO
# and MONDO hierarchies were downloaded on 2026-10-25 (their source cards' version). A dated event says which of the two it is.
RELEASE_DATES = {"pharos": "2024-02", "opentargets_layer": "2026-09", "dto": "2024-02", "chembl37": "2026-05",
                 "go_hierarchy": "2026-10", "hpo_hierarchy": "2026-10", "mondo_hierarchy": "2026-10"}
DOWNLOADED = {"go_hierarchy", "hpo_hierarchy", "mondo_hierarchy"}      # dated by the download, not by a release
RELEASE_WORDS = {"pharos": "Pharos 3.19.2", "dto": "Pharos 3.19.2", "opentargets_layer": "Open Targets 26.09", "chembl37": "ChEMBL 37",
                 "go_hierarchy": "GO", "hpo_hierarchy": "HPO", "mondo_hierarchy": "MONDO"}


def release_words(layer, version=None):
    """Takes a layer name and its source_version. Returns the release in a reader's words: 'Pharos 3.19.2', 'Open Targets
    26.09', 'ChEMBL 37', 'GO, downloaded 2026-10-25'. Never a layer code or a file path; an unknown layer gives its version
    without any parenthesis, or ''."""
    v = re.sub(r"\s*\(.*?\)", "", str(version or "")).strip()
    name = RELEASE_WORDS.get(layer)
    if layer in DOWNLOADED:
        m = re.search(r"\d{4}-\d{2}-\d{2}", v)
        return f"{name}, downloaded {m.group(0)}" if m else name
    if name:
        return name
    return v if v and "/" not in v else ""


def year_of(x):
    """Takes a first_year cell ('2018', '2018.0', 2018.0, '', None). Returns the year as 'YYYY', or None (ES-14: the ChEMBL
    activity edges store first_year as a float, so '2018.0' must count)."""
    try:
        y = int(float(x))
    except (TypeError, ValueError):
        return None
    return str(y) if 1000 <= y <= 9999 else None
ID_PATTERNS = [r"MONDO:\d{7}", r"CHEMBL\d+", r"NCBIGene:\d+", r"GO:\d{7}", r"HP:\d{7}", r"EFO:\d{7}",
               r"UniProtKB:[A-Z0-9]{6,10}", r"(?<![A-Z0-9])[OPQ][0-9][A-Z0-9]{3}[0-9](?![A-Z0-9])"]

# The five fixed Pharos questions (Phase 4d): a question, the openCypher it stands for (the querier's subset:
# every row cites edge ids), what a correct answer must contain, and why the question matters to a drug developer.
# No answer is prewritten: demo() runs the query on the graph and checks the shape of what comes back.
# Ordering (CC-31a, owner decision 8 Nov): evidence first. The Cypher sorts on the BioLink knowledge level where it can (as
# text, knowledge_assertion comes before statistical_association and text_co_occurrence, so the same query runs on Neptune),
# and the local run then orders by querier.evidence_key before LIMIT and merges duplicate rows of one answer.
DEMO_QUESTIONS = [
    dict(id="pharos_demo_1",
         question="Which diseases is the gene PTGS2 associated with, and which source says so?",
         cypher="MATCH (g:Gene {name:'PTGS2'})-[r:gene_associated_with_condition]->(d:Disease) "
                "RETURN d.name AS disease, r.source AS source, r.knowledge_level AS knowledge_level, r.id AS evidence "
                "ORDER BY knowledge_level, disease LIMIT 50",
         expect=dict(min_rows=1, columns=["disease", "source", "evidence"]),
         why="One hop. Each row names the Pharos source (DisGeNET, CTD, text mining ...) and the edge id, so the "
             "reader can open the edge's provenance and pruning verdict."),
    dict(id="pharos_demo_2",
         question="Which compounds bind the protein encoded by PTGS2, and how strongly?",
         cypher="MATCH (g:Gene {name:'PTGS2'})-[r1:has_gene_product]->(p:Protein), "
                "(c:SmallMolecule)-[r2:directly_physically_interacts_with]->(p) WHERE r2.activity IS NOT NULL "
                "RETURN r2.ligand_name AS compound, r2.activity AS activity, r2.activity_type AS activity_type, "
                "r1.id AS e1, r2.id AS e2 ORDER BY activity DESC LIMIT 20",
         expect=dict(min_rows=1, columns=["compound", "activity", "e2"]),
         why="Two hops (gene -> protein -> ligand). Activities are pChEMBL-style values on Pharos edges (the "
             "WHERE keeps rows that carry one); ChEMBL 37 edges for the same pair appear in the explain view as "
             "corroboration."),
    dict(id="pharos_demo_3",
         question="Which genes are associated with both Alzheimer disease and type 2 diabetes mellitus?",
         cypher="MATCH (g:Gene)-[r1:gene_associated_with_condition]->(d1:Disease {id:'MONDO:0004975'}), "
                "(g)-[r2:gene_associated_with_condition]->(d2:Disease {id:'MONDO:0005148'}) "
                "RETURN g.name AS gene, r1.source AS alzheimer_source, r2.source AS diabetes_source, "
                "r1.knowledge_level AS alzheimer_level, r2.knowledge_level AS diabetes_level, "
                "r1.id AS e1, r2.id AS e2 ORDER BY alzheimer_level, diabetes_level, gene LIMIT 100",
         expect=dict(min_rows=1, columns=["gene", "e1", "e2"]),
         why="A multi-hop question a non-expert cannot answer from Pharos pages: two disease associations joined "
             "on the gene, each cited. MONDO ids are from data/reference/mondo_terms.tsv."),
    dict(id="pharos_demo_4",
         question="Which biological processes involve the proteins that ibuprofen binds?",
         cypher="MATCH (c:SmallMolecule {id:'CHEMBL.COMPOUND:CHEMBL521'})-[r1:directly_physically_interacts_with]->"
                "(p:Protein)-[r2:actively_involved_in]->(b:BiologicalProcess) "
                "RETURN p.name AS protein, b.name AS process, r1.id AS e1, r2.id AS e2 ORDER BY protein, process LIMIT 50",
         expect=dict(min_rows=1, columns=["protein", "process", "e2"]),
         why="Drug -> target -> GO process. CHEMBL521 is ibuprofen in data/reference/chembl_synonyms.tsv.gz. "
             "The GO hierarchy layer (L2) lets the app walk from a specific process to its parents."),
    dict(id="pharos_demo_5",
         question="Which GWAS traits are genetically associated with SOD1, and how strong is the evidence?",
         cypher="MATCH (g:Gene {name:'SOD1'})-[r:genetically_associated_with]->(t) "
                "RETURN t.name AS trait, r.study_count AS studies, r.median_p_value AS median_neg_log10_p, "
                "r.mean_rank_score AS rank_score, r.id AS evidence ORDER BY rank_score DESC LIMIT 20",
         expect=dict(min_rows=1, columns=["trait", "evidence"]),
         why="TIGA (GWAS Catalog) edges carry their own statistics (TIGA's median p-value is given as -log10 p); the pruning verdict uses them as the source's "
             "own score, and the leakage rule keeps a held-out gene-disease pair out of these edges too."),
]


def _read(path, usecols=None):
    return pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False, usecols=usecols)


def _clean(d):
    return {k: v for k, v in d.items() if v != "" and v is not None}


COMPOUND_PREFIX = "CHEMBL.COMPOUND:"
CHEMBL_NAMES = Path("data") / "reference" / "chembl_synonyms.tsv.gz"


def merge_node_frames(frames):
    """Takes the layers' node tables in layer order (Pharos first). Returns ONE table with one row per id, ids in order of
    first appearance, where each FIELD takes the first non-empty value over the layers (so a name that Pharos leaves empty
    is taken from a later layer, instead of the whole first record winning). Missing columns and empty strings count as empty."""
    both = pd.concat(frames, ignore_index=True).replace("", np.nan)
    return both.groupby("id", sort=False, as_index=False).first().fillna("")


def harmonised_nodes(layer_frames):
    """Takes [(layer name, node table)] in layer order (Pharos first). Returns merge_node_frames over them after
    make_graphs.harmonise_categories: ONE BioLink class per identifier prefix (MONDO = disease; HP / MP / OBA = phenotypic
    feature; EFO = disease or phenotypic feature; GO = the GO layer's class), the same rule the assembled graphs G0-G4,
    the KGX export and the Neptune files use, whatever class a layer gave the node."""
    frames = [f.assign(layer=layer) for layer, f in layer_frames if len(f)]
    if not frames:
        return merge_node_frames([f for _, f in layer_frames])
    allf, _ = make_graphs.harmonise_categories(pd.concat(frames, ignore_index=True).fillna(""))
    return merge_node_frames([allf.drop(columns="layer")])


def chembl_names(root):
    """Takes the repo root. Returns {names: {node id: name}, synonyms: {node id: [synonym, ...]}, source: the file's own
    '# source:' line, path: relative path} read from data/reference/chembl_synonyms.tsv.gz; all empty (path None) when the file
    is absent. Node id = 'CHEMBL.COMPOUND:' + chembl_id. The name is the molecule's pref_name, else its parent's; a molecule
    with neither has no entry (its id stays its label). Synonyms are the text before '::' of each '|'-separated item,
    without duplicates (case-insensitive) and without the name itself."""
    path = Path(root) / CHEMBL_NAMES
    if not path.exists():
        return dict(names={}, synonyms={}, source="", path=None)
    import gzip
    with gzip.open(path, "rt", encoding="utf-8") as f:
        first = f.readline()
        source = first[1:].strip() if first.startswith("#") else ""
        f.seek(0)
        df = pd.read_csv(f, sep="\t", dtype=str, keep_default_na=False, comment="#")
    pref = dict(zip(df["chembl_id"], df["pref_name"]))
    names, syns = {}, {}
    for r in df.itertuples(index=False):
        name = r.pref_name or pref.get(r.parent_chembl_id, "")
        nid = COMPOUND_PREFIX + r.chembl_id
        if name:
            names[nid] = name
        seen, out = {name.lower()}, []
        for item in (r.synonyms or "").split("|"):
            t = item.split("::")[0].strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
        if out:
            syns[nid] = out
    return dict(names=names, synonyms=syns, source=source, path=str(CHEMBL_NAMES))


def name_compounds(nodes, chembl):
    """Takes {node id: record} (merged layers; changed in place) and chembl_names(). A compound node whose record has no
    name gets the ChEMBL name (a name from a layer is never replaced; no name anywhere leaves the id as the label).
    Returns {node id: [synonyms]} for the nodes that are in the graph."""
    for nid, name in chembl["names"].items():
        n = nodes.get(nid)
        if n is not None and not n.get("name"):
            n["name"] = name
    return {nid: v for nid, v in chembl["synonyms"].items() if nid in nodes}


def search_match(q, node, synonyms):
    """Takes the lower-cased query, a node record and its synonyms. Returns None for no match, else 1 for an exact match
    (id, name, symbol or a synonym equal to the query) and 0 for a substring match: exact matches rank first."""
    fields = [str(node.get(k) or "").lower() for k in ("id", "name", "symbol")]
    syn = [x.lower() for x in synonyms]
    if q in fields or q in syn:
        return 1
    if any(q in f for f in fields) or any(q in x for x in syn):
        return 0
    return None


def close_matches(q, candidates, n=3, cutoff=0.75):
    """Takes a query, [(text, node id)] and how many to return. Returns [(score, node id, text)] of the texts most like the
    query (difflib similarity of the lower-cased strings, at least `cutoff`; a swap of two letters, PTSG2 for PTGS2, scores
    0.8), best first, one entry per node id. For "did you mean" when a search finds nothing (CC-31g, review finding m5)."""
    import difflib
    q = q.lower().strip()
    best = {}
    for text, nid in candidates:
        t = str(text).lower()
        m = difflib.SequenceMatcher(None, q, t)
        if m.real_quick_ratio() < cutoff or m.quick_ratio() < cutoff:
            continue
        r = m.ratio()
        if r >= cutoff and r > best.get(nid, (0,))[0]:
            best[nid] = (r, nid, text)
    return sorted(best.values(), key=lambda x: (-x[0], len(str(x[2])), x[1]))[:n]


def length_window(q):
    """The (min, max) length of a name worth comparing with q for "did you mean"."""
    k = max(2, len(q) // 4)
    return max(1, len(q) - k), len(q) + k


# Words that are not part of an entity name: the question's grammar and the generic class words (CC-31g substitution notes).
STOP = set("""a an the of in on to for with and or by at as is are was were be been which what who whom whose how why when
where does do did that this these those any all both either its their his her than then from into about over under between
among same other more most less least very can could should would may might will shall not no gene genes protein proteins
disease diseases condition conditions compound compounds drug drugs ligand ligands target targets trait traits process
processes pathway pathways phenotype phenotypes linked associated association associations involved involve involves
encoded encode encodes bind binds binding inhibit inhibits known evidence source sources strongly strong say says show
shows give gives list name names me us""".split())


def _words(text):
    return [re.sub(r"'s$", "", w) for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", text)]


def substitution_notes(graph, question, entities, search_limit=3):
    """Takes the graph, the question and the entity ids recognised in it (find_entities). Returns [{phrase, used, used_id,
    found: [{id, label}]}] for every recognised name that the question qualifies with more words on its left (content words,
    not STOP): 'Nipah virus infection' read as 'infection'. `found` = what the graph's search has for the longer phrase (at
    most search_limit), so the reader sees the substitution and the candidates; nothing is swapped in (rule 3)."""
    words = _words(question)
    low = [w.lower() for w in words]
    out, seen = [], set()
    recognised = {str(graph.label(e) if hasattr(graph, "label") else e).lower() for e in entities}
    for nid in entities:
        label = str(graph.label(nid) if hasattr(graph, "label") else nid)
        names = {label.lower()} | {str(graph.nodes[nid].get(k) or "").lower() for k in ("name", "symbol")} - {""}
        for name in sorted(names, key=len, reverse=True):
            nw = name.split()
            for i in range(len(low) - len(nw) + 1):
                if low[i:i + len(nw)] != nw:
                    continue
                j = i
                while j > 0 and low[j - 1] not in STOP and i - (j - 1) <= 3:
                    j -= 1
                if j < i:
                    phrase = " ".join(words[j:i + len(nw)])
                    if phrase.lower() in seen or phrase.lower() in recognised:
                        continue                         # the longer name is itself a recognised entity: nothing was shortened
                    seen.add(phrase.lower())
                    hits = [h for h in (graph.search(phrase, limit=search_limit) if hasattr(graph, "search") else [])
                            if h["id"] != nid]
                    out.append(dict(phrase=phrase, used=" ".join(words[i:i + len(nw)]), used_id=nid,
                                    found=[dict(id=h["id"], label=h["label"]) for h in hits]))
            if out and out[-1]["used_id"] == nid:
                break
    return out


def substitution_sentence(note, used_id=None):
    """One substitution note as the sentence the answer shows. `used_id` = the id the query really used for the shorter
    name (default: the note's own). When the graph does hold the longer name, the sentence says the rows are about the
    shorter one and names the node(s) with the longer name; 'is not a name in the graph' is written only when the search
    found no node with exactly that name."""
    uid = used_id or note["used_id"]
    exact = [f for f in note["found"] if str(f["label"]).lower() == note["phrase"].lower()]
    if exact:
        also = ", ".join("{} ({})".format(f["label"], f["id"]) for f in exact)
        return (f"'{note['phrase']}' was read as '{note['used']}' ({uid}), so the rows are about that, not about "
                f"{also}; name the identifier to ask about it.")
    s = f"'{note['phrase']}' is not a name in the graph; the question was read with '{note['used']}' ({uid})."
    if note["found"]:
        s += " The graph's search has: " + ", ".join(f"{f['label']} ({f['id']})" for f in note["found"]) + "."
    return s


# FIX1 F9 (gate 1, G1-P1-03): among search hits with the same label, the disease comes before a gene, a protein and a
# same-named phenotype ("Alzheimer disease": MONDO:0004975 with 19,928 links, not HP:0002511 with 5); then more links first.
SAME_LABEL_ORDER = ("biolink:Disease", "biolink:Gene", "biolink:Protein", "biolink:PhenotypicFeature")


def rank_same_label(hits, links=None):
    """Takes search hits [{id, label, category, ...}] in the search's order and optionally {id: link count}. Returns the hits
    with every group that shares a label (case ignored) re-ordered in place of its first member: by SAME_LABEL_ORDER (other
    classes after), then by link count (most first), then as found. Hits with a label of their own keep their position.
    Each hit gets `links` when a count is known."""
    links = links or {}
    out = [dict(h, **({"links": links[h["id"]]} if h.get("id") in links else {})) for h in hits]
    rank = {c: i for i, c in enumerate(SAME_LABEL_ORDER)}
    groups = {}
    for i, h in enumerate(out):
        groups.setdefault(str(h.get("label") or "").lower(), []).append(i)
    for idx in groups.values():
        if len(idx) < 2:
            continue
        ordered = sorted((out[i] for i in idx), key=lambda h: (rank.get(h.get("category"), len(rank)), -(h.get("links") or 0)))
        for i, h in zip(idx, ordered):
            out[i] = h
    return out


def query_anchors(cypher):
    """Takes the openCypher that ran. Returns (ids, names): the values of every {id:'...'} and {name:'...'} / {symbol:'...'}
    in it, i.e. the entities the executed query really used."""
    c = str(cypher or "")
    return (re.findall(r"\bid\s*:\s*'([^']+)'", c), re.findall(r"\b(?:name|symbol)\s*:\s*'([^']+)'", c))


def entity_caveats(notes, cypher, label=None):
    """Takes the substitution notes of a question (substitution_notes), the openCypher that ran and, optionally, a function
    id -> label (graph.label). Returns (notes kept, their sentences). A note is DROPPED when the executed query did resolve
    the longer name: the query uses an id whose label is that name (one of the note's own search hits, or any id whose
    label equals it), or uses the name itself. A note that stays names the id the query really used for the shorter name
    when that differs from the note's (the model may pick another node with the same label). With no query (nothing ran)
    every note is kept as it is."""
    ids, names = query_anchors(cypher)
    if not str(cypher or "").strip():
        return list(notes), [substitution_sentence(x) for x in notes]
    lab = (lambda i: str(label(i))) if label else (lambda i: i)
    low_names = {n.lower() for n in names}
    kept, sentences = [], []
    for note in notes:
        phrase = note["phrase"].lower()
        hit = {f["id"] for f in note["found"] if str(f["label"]).lower() == phrase}
        if phrase in low_names or any(i in hit or lab(i).lower() == phrase for i in ids):
            continue
        used = next((i for i in ids if i != note["used_id"] and lab(i).lower() == note["used"].lower()), None)
        if used is None and note["used_id"] not in ids:
            used = _resolved_id(note, ids, lab)            # FIX2 F6 (BB-1): the id the query really used for the phrase
        kept.append(note)
        sentences.append(substitution_sentence(note, used if note["used_id"] not in ids else None))
    return kept, sentences


def _resolved_id(note, ids, lab):
    """Takes a substitution note, the ids the executed query used and id -> label. Returns the id the query used for the
    note's phrase: the note's own id when the query used it, else the query id whose label is the longest part of the phrase
    ('lung carcinoma' for 'non-small cell lung carcinoma'), else None."""
    if note["used_id"] in ids:
        return note["used_id"]
    phrase = note["phrase"].lower()
    inside = [i for i in ids if lab(i) and lab(i).lower() != i.lower() and re.search(r"\b" + re.escape(lab(i).lower()) + r"\b", phrase)]
    return max(inside, key=lambda i: len(lab(i)), default=None)


def read_as(notes, cypher, label=None):
    """FIX2 F6 (BB-1). Takes the kept substitution notes (entity_caveats), the openCypher that ran and id -> label. Returns
    [{phrase, id, label}]: for each phrase the question named, the node the query really read it as, with that node's own
    label, when the label differs from the phrase. The headline leads with "Read as <label>" for these."""
    ids, _ = query_anchors(cypher)
    lab = (lambda i: str(label(i))) if label else (lambda i: i)
    out = []
    for note in notes:
        rid = _resolved_id(note, ids, lab)
        if rid and lab(rid).lower() != note["phrase"].lower() and rid not in {x["id"] for x in out}:
            out.append(dict(phrase=note["phrase"], id=rid, label=lab(rid)))
    return out


def explanation_labels(explanation, question, cypher, label=None):
    """FIX2 F6 (BB-1). Takes the model's explanation, the question, the openCypher that ran and id -> label. Returns the
    explanation with every "<words of the question> (<id the query used>)" rewritten to "<that node's label> (<id>)", so the
    explanation never names a node by the user's phrase ('non-small cell lung carcinoma (MONDO:0005138)' becomes 'lung
    carcinoma (MONDO:0005138)'). Text without such a pair is returned unchanged."""
    text = str(explanation or "")
    if not text or not label:
        return text
    ids, _ = query_anchors(cypher)
    words = _words(question)
    for nid in dict.fromkeys(ids):
        right = str(label(nid) or "")
        if not right or right == nid:
            continue
        done = False
        for n in range(min(8, len(words)), 0, -1):                 # the longest phrase of the question first, once per id
            for i in range(len(words) - n + 1):
                if words[i].lower() in STOP:
                    continue
                phrase = " ".join(words[i:i + n])
                pat = re.compile(r"(?<![\w-])" + re.escape(phrase) + r"\s*\(" + re.escape(nid) + r"\)", re.I)
                if pat.search(text):
                    if phrase.lower() != right.lower():
                        text = pat.sub(lambda m: f"{right} ({nid})", text)
                    done = True
                    break
            if done:
                break
    return text


def _norm_question(text):
    return re.sub(r"[\s?.!]+$", "", " ".join(str(text or "").split())).lower()


def tested_question(question):
    """Takes a question as typed. Returns the tested question (a DEMO_QUESTIONS entry) with the same text, ignoring case,
    spacing and a final '?', '.' or '!'; None when there is none."""
    q = _norm_question(question)
    return next((d for d in DEMO_QUESTIONS if _norm_question(d["question"]) == q), None) if q else None


def tested_answer(graph, demo):
    """Takes the graph and a tested question. Returns its answer in the shape ask() returns (question, entities, cypher,
    columns, rows, evidence, ordering, passed, note, tested = the question's id; problems / attempts empty; no model
    named, because none is called), so a pill or a typed question with a tested question's text gets the SAME answer as
    the tested-question strip (review finding U5). entities = the ids the fixed query names, and the graph's nodes with
    the names it names."""
    res = dict(graph.demo(ids={demo["id"]})[0])
    ids, names = query_anchors(demo["cypher"])
    low = {n.lower() for n in names}
    found = graph.find_entities(demo["question"]) if hasattr(graph, "find_entities") else []
    named = [e for e in found if str(graph.label(e)).lower() in low or e in ids]
    entities = list(dict.fromkeys([i for i in ids if i in graph.nodes] + named))
    return dict(res, entities=entities, problems=[] if res.get("passed") else [res.get("note") or "the tested question did not pass its check"],
                attempts=[], neo4j="", backend="", model="", abstained=False, tested=demo["id"], entity_notes=[], entity_sentences=[])


def display_result(res):
    """Takes an answer (rows, columns ...). Returns it with the rows as the page prints them (answer_summary.display_rows:
    decimals at 3 significant figures; review finding U11). Link ids and everything else are unchanged."""
    import answer_summary
    return dict(res, rows=answer_summary.display_rows(res.get("rows") or [])) if res.get("rows") else res


NO_ENTITY = ("No gene, protein, disease, compound or other entity named in the question was found in the graph, so no query "
             "was written. Name one the way the graph labels it (the search box finds it), or use its identifier.")


def abstention(graph, nim, question, max_words=5):
    """The answer to a question that names nothing the graph knows, given WITHOUT a model call: problems [NO_ENTITY] and
    "did you mean" suggestions for its content words (graph.suggest), at most max_words words tried, symbol-like words
    (with a digit or capitals) first."""
    words = [w for w in dict.fromkeys(_words(question)) if len(w) >= 3 and w.lower() not in STOP]
    words = [w for _, w in sorted(enumerate(words), key=lambda iw: (not (re.search(r"\d", iw[1]) or iw[1].isupper()), iw[0]))]
    sugg, seen = [], set()
    for w in words[:max_words]:
        for s in (graph.suggest(w, n=2) if hasattr(graph, "suggest") else []):
            if s["id"] not in seen:
                seen.add(s["id"])
                sugg.append(dict(s, word=w))
    return dict(question=question, entities=[], cypher="", neo4j="", problems=[NO_ENTITY], columns=[], rows=[], evidence=[],
                attempts=[], backend=nim.backend, model=nim.model("querier"), abstained=True, suggestions=sugg[:5],
                entity_notes=[])


def predictions_source(root):
    """Takes the repo root. Returns (path, model) of the predictions file the app shows: the meta-path file
    (outputs/linkpred/predictions_metapath.tsv.gz, each prediction with the named paths and edge ids behind it) when it
    exists, else the GNN file (predictions.tsv.gz), else (None, None). Used by the live graph and the card index so
    both show the same file."""
    d = Path(root) / "outputs" / "linkpred"
    if (d / "predictions_metapath.tsv.gz").exists():
        return d / "predictions_metapath.tsv.gz", "metapath"
    if (d / "predictions.tsv.gz").exists():
        return d / "predictions.tsv.gz", "gnn"
    return None, None


CONTEXT_SUFFIX = "_context"
CONTEXT_WORDS = " (statistical or not-provided level)"


def path_predicate(term):
    """Takes the predicate of an edge on a predicted path: a CURIE ('biolink:treats'), a bare PyG relation name
    ('has_gene_product', as predictions_metapath.tsv.gz writes them) or the benchmark's context relation
    ('gene_associated_with_condition_context': gene-disease edges of the statistical_association / not_provided levels,
    linkpred.predict moves them there). Returns the words the card shows: the local name, the context relation as
    'gene_associated_with_condition (statistical or not-provided level)'. Found 7 Nov: local() raised IndexError on bare
    names, so every card with meta-path predictions failed."""
    t = str(term or "")
    name = t.split(":", 1)[1] if ":" in t else t
    if name.endswith(CONTEXT_SUFFIX):
        return name[: -len(CONTEXT_SUFFIX)] + CONTEXT_WORDS
    return name


def metapath_card_paths(raw, label, max_paths=3):
    """Takes the `paths` cell of predictions_metapath.tsv.gz (JSON list of {shape, count, via, nodes, edges}) and a
    function id -> label. Returns up to max_paths paths in the shape the card renders: {via, via_label, shape, edges:
    [{id, statement}]}, each statement an edge of the graph worded 'A -predicate-> B' (predicate via path_predicate)."""
    out = []
    for x in json.loads(raw or "[]")[:max_paths]:
        out.append(dict(via=x["via"], via_label=label(x["via"]), shape=x.get("shape", ""),
                        edges=[dict(id=e["id"], statement=f"{label(e['subject'])} -{path_predicate(e['predicate'])}-> "
                                                          f"{label(e['object'])}") for e in x["edges"]]))
    return out


NO_PREDICTIONS_NOTE = ("no predictions file yet: run python -m pipelines.linkpred.predict after the GNN experiments "
                       "(EXECUTION_PLAN.md Phase 5)")


POTENCY_COLS = ["activity", "activity_type", "pchembl_max", "n_activities"]
POTENCY_UNIT = "pChEMBL (-log10 molar; 6 = 1 uM, higher = stronger)"


def potency(e):
    """Takes an edge (dict or row). Returns its measured potency as the card and the chips show it, or None:
    {value, measure, n_activities, unit, word}. Pharos ligand-activity edges give activity (a pChEMBL-style value) and
    activity_type (IC50, Ki ...); ChEMBL 37 edges give pchembl_max (the best pChEMBL) and n_activities. word = 'weaker than
    1 uM' below pChEMBL 6, else '1 uM or stronger' (pChEMBL 6 = 1 uM by definition; no other cut is implied)."""
    def num(x):
        try:
            v = float(x)
            return v if v == v else None
        except (TypeError, ValueError):
            return None
    g = (lambda k: e.get(k)) if hasattr(e, "get") else (lambda k: getattr(e, k, None))
    value, measure = num(g("activity")), g("activity_type")
    if value is None:
        value, measure = num(g("pchembl_max")), "best pChEMBL" if num(g("pchembl_max")) is not None else None
    if value is None:
        return None
    n = num(g("n_activities"))
    return dict(value=round(value, 2), measure=str(measure) if measure not in (None, "") else None,
                n_activities=int(n) if n is not None else None, unit=POTENCY_UNIT,
                word="weaker than 1 uM" if value < 6 else "1 uM or stronger")


CTD_DETAIL = "https://ctdbase.org/detail.go"
_ENSG, _DIS = re.compile(r"ENSG\d{11}"), re.compile(r"(?:EFO|MONDO|Orphanet|HP|DOID|OTAR|MP)_\d+")
_REACT, _KEGG, _WP = re.compile(r"R-[A-Z]{3}-\d+"), re.compile(r"[a-z]{2,4}\d{5}"), re.compile(r"WP\d+")
_UNIPROT, _CHEMBL, _GO = re.compile(r"[A-Z0-9]{6,10}"), re.compile(r"CHEMBL\d+"), re.compile(r"GO:\d{7}")


def source_url(row):
    """Takes an edge or card row (source, subject, object and, when the caller has it, source_record = '<table>|<columns>').
    Returns the URL of the source's own page for that row, or None. Built only from the row's own ids, never looked up or
    guessed (PK6, ES-04; CC-41 for CTD):
      CTD             the gene's disease associations on CTD (NCBI Gene id), else the disease's CTD page (MESH, OMIM)
      Open Targets    the target-disease evidence page (ENSG and disease id from the record or the row's ids); a drug row: the drug page
      ChEMBL          the compound report card (ChEMBL 37 rows and Pharos ligand rows with a ChEMBL id)
      GO annotation   QuickGO's annotations of the protein for the GO term
      pathways        the Reactome, KEGG or WikiPathways page of the pathway
      TIGA            the trait's page in the TIGA app (trait id from the record)
      STRING          the STRING network of the two proteins
      UniProt         the protein's disease section on UniProt
    Every other row: None (the page then offers a PubMed search for the two names)."""
    src = str(row.get("source") or "")
    ids = [str(row.get("subject") or ""), str(row.get("object") or "")]
    rec = str(row.get("source_record") or "").split("|")
    table, cols = (rec[0], rec[1:]) if len(rec) > 1 else ("", [])

    def acc(prefix, pattern):
        for i in ids:
            pre, _, a = i.partition(":")
            if pre == prefix and pattern.fullmatch(a):
                return a
        return None
    if src == "CTD":
        for i in ids:
            if i.startswith("NCBIGene:") and i[9:].isdigit():
                return f"{CTD_DETAIL}?type=gene&acc={i[9:]}&view=disease"
        for i in ids:
            pre, _, a = i.partition(":")
            if pre in ("MESH", "OMIM") and a and a.isalnum():
                return f"{CTD_DETAIL}?type=disease&acc={pre}:{a}"
        return None
    chembl = acc("CHEMBL.COMPOUND", _CHEMBL)
    uniprot = acc("UniProtKB", _UNIPROT)
    if src.startswith("Open Targets"):
        ensg = next((c for c in cols if _ENSG.fullmatch(c)), None) or acc("ENSEMBL", _ENSG)
        dis = next((c for c in cols if _DIS.fullmatch(c)), None)
        if dis is None:
            for i in ids:
                pre, _, a = i.partition(":")
                if pre in ("EFO", "MONDO", "Orphanet", "HP", "DOID") and a.isalnum():
                    dis = f"{pre}_{a}"
        if ensg and dis:
            return f"https://platform.opentargets.org/evidence/{ensg}/{dis}"
        if chembl:
            return f"https://platform.opentargets.org/drug/{chembl}"
        return None
    if src.startswith("ChEMBL") or src == "CHEMBL" or table in ("activities", "mechanisms", "indications", "ligands"):
        return f"https://www.ebi.ac.uk/chembl/compound_report_card/{chembl}/" if chembl else None
    if src.startswith("Gene Ontology") or table == "go":
        go = next((i for i in ids if _GO.fullmatch(i)), None)
        return f"https://www.ebi.ac.uk/QuickGO/annotations?geneProductId={uniprot}&goId={go}" if uniprot and go else None
    for i in ids:
        pre, _, a = i.partition(":")
        if pre == "REACT" and _REACT.fullmatch(a):
            return f"https://reactome.org/content/detail/{a}"
        if pre == "KEGG.PATHWAY" and _KEGG.fullmatch(a):
            return f"https://www.kegg.jp/pathway/{a}"
        if pre == "WIKIPATHWAYS" and _WP.fullmatch(a):
            return f"https://www.wikipathways.org/pathways/{a}.html"
    if src.startswith("TIGA") or table == "tiga":
        trait = next((c for c in cols if _DIS.fullmatch(c)), None)
        return f"https://unmtid-shinyapps.net/shiny/tiga/?trait={trait}" if trait else None
    if "STRING" in src.upper() or (table == "ppi" and any("STRING" in c.upper() for c in cols)):
        prots = [i.split(":", 1)[1] for i in ids if i.startswith("UniProtKB:") and _UNIPROT.fullmatch(i.split(":", 1)[1])]
        return f"https://string-db.org/cgi/network?identifiers={'%0d'.join(prots)}&species=9606" if len(prots) == 2 else None
    if src.startswith("UniProt") and uniprot:
        return f"https://www.uniprot.org/uniprotkb/{uniprot}/entry#disease_variants"
    return None


def card_row(base, label, other, other_label, other_category, outgoing, kind):
    """Takes an edge row from PharosGraph._edge_row (id, subject, predicate, object, statement, layer, source,
    source_version, knowledge_level, verdict, confidence), the card node's label, the other end (id, label,
    category), the direction and the evidence kind. Returns the row as the card shows it: the input plus other,
    other_label, other_category, outgoing, phrase, sentence, word, level_words, kind, source_url (see source_url).
    Used by the live card and by the card index (pharos_index.py), so both word a row identically."""
    r = dict(base)
    phrase = personas.predicate_phrase(r["predicate"])
    r.update(other=other, other_label=other_label, other_category=other_category, outgoing=outgoing, phrase=phrase,
             sentence=f"{label} {phrase} {other_label}" if outgoing else f"{other_label} {phrase} {label}",
             word=personas.confidence_word(r.get("verdict"), r.get("confidence")),
             level_words=personas.KNOWLEDGE_LEVELS.get(str(r.get("knowledge_level") or ""), r.get("knowledge_level")),
             kind=kind, source_url=source_url(r))
    return r


def card_row_order(r):
    """Sort key of the rows inside one evidence kind: kept first, then higher confidence, then the sentence."""
    return (r["word"] != "kept", -(r["confidence"] or 0), r["sentence"])


def assemble_card(nid, node, label, known, predicted, prediction_note, n_known, picture_edges=40):
    """Takes the node id, its node dict, its label, known = [{kind, n, n_kept, n_pruned, rows (sorted, cut)}] in any
    order, the predicted links (predicted_links rows), the note shown when there are none, and the node's edge
    count. Returns the card dict (about, summary, known in personas.EVIDENCE_KINDS order with meaning and colour,
    predicted, prediction_note, picture). Shared by PharosGraph.card and pharos_index.CardIndex.card."""
    by_kind = {k["kind"]: k for k in known}
    known = [dict(kind=kind, meaning=meaning, color=color, n=by_kind[kind]["n"], n_kept=by_kind[kind]["n_kept"],
                  n_pruned=by_kind[kind]["n_pruned"], rows=by_kind[kind]["rows"])
             for kind, meaning, color in personas.EVIDENCE_KINDS if kind in by_kind and by_kind[kind]["n"]]
    # the picture: kept, non-text-mining edges first, then the rest, then the predicted links (dashed)
    pick = [r for grp in known if grp["kind"] != "text mining" for r in grp["rows"]]
    pick = sorted(pick, key=lambda r: (r["word"] != "kept", r["kind"] == "annotation"))[:picture_edges]
    pnodes = {nid: dict(id=nid, label=label, category=node.get("category", ""))}
    pedges = []
    for r in pick:
        pnodes.setdefault(r["other"], dict(id=r["other"], label=r["other_label"], category=r["other_category"]))
        pedges.append(dict(id=r["id"], source=r["subject"], target=r["object"], predicate=r["predicate"],
                           evidence_type=r["kind"], predicted=False, word=r["word"]))
    for x in predicted:
        pnodes.setdefault(x["other"], dict(id=x["other"], label=x["other_label"], category=x.get("other_category", "")))
        pedges.append(dict(id=f"pred:{x['subject']}|{x['object']}", source=x["subject"], target=x["object"],
                           predicate="predicted", evidence_type="predicted", predicted=True, word="predicted"))
    cat = local(node.get("category", ""))
    about = dict(id=nid, label=label, category=node.get("category", ""), type_word=personas.CATEGORIES.get(cat, cat.lower()),
                 ids=[nid] + [v for k, v in node.items() if k in ("uniprot", "xref", "hgnc", "ensembl") and v],
                 properties={k: v for k, v in node.items() if k not in ("id", "category", "name")})
    summary = dict(known=n_known, kinds=len(known), kept=sum(g["n_kept"] for g in known),
                   pruned=sum(g["n_pruned"] for g in known), predicted=len(predicted),
                   layers=sorted({r["layer"] for grp in known for r in grp["rows"] if r.get("layer")}))
    return dict(about=about, summary=summary, known=known, predicted=predicted, prediction_note=prediction_note,
                picture=dict(nodes=list(pnodes.values()), edges=pedges))


# ------------------------------------------------------------------ CC-30 (review finding M1): novel predictions only
NOVELTY_TSV = Path("outputs") / "linkpred" / "predictions_novelty.tsv.gz"
NOVELTY_MD = Path("outputs") / "linkpred" / "predictions_novelty.md"
PRED_POOL = 20                    # predictions read per card before the split (the card index stores at most --n-pred = 20)
_ANCESTORS = {}


def ancestor_pairs(path):
    """Takes the per-row novelty file (scripts/predictions_novelty.py, Claude Science CS-5). Returns the set of (subject,
    object) pairs flagged ancestor_of_known = yes: the predicted disease is a strict MONDO ancestor of a disease the subject
    already has an edge to (a broad category). Empty set when the file does not exist. Cached per file and mtime."""
    p = Path(path)
    if not p.is_file():
        return set()
    key = (str(p), p.stat().st_mtime)
    if key not in _ANCESTORS:
        df = pd.read_csv(p, sep="\t", dtype=str, keep_default_na=False, usecols=["subject", "object", "ancestor_of_known"])
        df = df[df["ancestor_of_known"] == "yes"]
        _ANCESTORS.clear()
        _ANCESTORS[key] = set(zip(df["subject"], df["object"]))
    return _ANCESTORS[key]


def split_predictions(card, ancestors=frozenset(), n=10):
    """Takes a card (assemble_card), the broad-category pairs (ancestor_pairs) and how many novel links to show. Returns the
    card with predicted = the NOVEL links only (known_in empty): specific diseases first, then the broad categories
    (broad_category True), each group in the model's score order, cut at n and ranked 1..n (the file's own rank kept as
    model_rank); recovered_known = the predictions whose pair a layer already states (the model's sanity check, ranked
    1..m); the picture loses the dashed lines of known pairs; summary.predicted = novel shown, summary.recovered_known,
    summary.broad_category; prediction_counts = {considered, novel, broad_category, recovered_known}. A link with known_in
    non-empty is never called new."""
    preds = [dict(x) for x in card.get("predicted") or []]
    novel = [x for x in preds if not x.get("known_in")]
    known = [x for x in preds if x.get("known_in")]
    for x in novel:
        x["broad_category"] = (x.get("subject"), x.get("object")) in ancestors
    novel = [x for x in novel if not x["broad_category"]] + [x for x in novel if x["broad_category"]]
    counts = dict(considered=len(preds), novel=len(novel), broad_category=sum(x["broad_category"] for x in novel),
                  recovered_known=len(known))
    novel = novel[:n]
    for group in (novel, known):
        for i, x in enumerate(group, 1):
            x["model_rank"], x["rank"] = x.get("rank"), i
    keep = {f"pred:{x['subject']}|{x['object']}" for x in novel}
    pic = card.get("picture") or {"nodes": [], "edges": []}
    edges = [e for e in pic["edges"] if not e.get("predicted") or e["id"] in keep]
    used = {card["about"]["id"]} | {e["source"] for e in edges} | {e["target"] for e in edges}
    out = dict(card, predicted=novel, recovered_known=known, prediction_counts=counts,
               picture=dict(pic, edges=edges, nodes=[x for x in pic["nodes"] if x["id"] in used]),
               summary=dict(card["summary"], predicted=len(novel), recovered_known=len(known),
                            broad_category=sum(x["broad_category"] for x in novel)))
    if preds and not novel:
        out["prediction_note"] = (f"All {len(known)} of the model's top predictions for this node restate links the graph "
                                  "already holds; none is new.")
    return out


def novelty_summary(path):
    """Takes predictions_novelty.md (CS-5). Returns {sentence, all, known, novel, novel_specific, file} read from it: the
    start-page sentence under '## Sentence for the start page' and the rows / counts of its table; None when the file is
    missing. Nothing is computed here, so the page quotes the file."""
    p = Path(path)
    if not p.is_file():
        return None
    text = p.read_text(encoding="utf-8")
    out = dict(file=p.name, sentence=None)
    m = re.search(r"## Sentence for the start page[^\n]*\n+>\s*(.+?)(?:\n\s*\n|\Z)", text, re.S)
    if m:
        out["sentence"] = " ".join(l.lstrip("> ").strip() for l in m.group(1).splitlines()).strip()
    def row(label):
        r = re.search(r"^\|\s*\**" + re.escape(label) + r"\**[^|]*\|\s*\**([\d,]+)\**", text, re.M)
        return int(r.group(1).replace(",", "")) if r else None
    out.update({"all": row("all predictions"), "known": row("known in at least one layer"), "novel": row("novel"),
                "novel_specific": row("novel and not an ancestor of a known disease")})
    return out


class _Edges(Mapping):
    """kg.edges: edge id -> edge dict, built from the table row on demand."""

    def __init__(self, g):
        self.g = g

    def __getitem__(self, eid):
        try:
            pos = self.g._eindex.get_loc(eid)
        except KeyError:
            raise KeyError(eid)
        return self.g._edge(pos)

    def __contains__(self, eid):
        return eid in self.g._eindex

    def __iter__(self):
        return iter(self.g._eindex)

    def __len__(self):
        return len(self.g._eindex)


class _Adjacency(Mapping):
    """kg.by_subject / kg.by_object: node id -> list of edge dicts (empty list for an unknown node)."""

    def __init__(self, g, groups):
        self.g, self.groups = g, groups

    def __getitem__(self, nid):
        return [self.g._edge(p) for p in self.groups[nid]]

    def get(self, nid, default=None):
        return self[nid] if nid in self.groups else ([] if default is None else default)

    def __contains__(self, nid):
        return nid in self.groups

    def __iter__(self):
        return iter(self.groups)

    def __len__(self):
        return len(self.groups)


class GraphViews:
    """The views that only need nodes, edges, adjacency, verdict(), same_pair() and label(): shared by the live PharosGraph and
    the card index's IndexGraph (pharos_index), so both give the same node view, explanation and timeline."""

    def label(self, nid):
        n = self.nodes.get(nid, {})
        return n.get("name") or n.get("symbol") or nid

    def statement(self, e):
        return f"{self.label(e['subject'])} -{local(e['predicate'])}-> {self.label(e['object'])}"

    def _edge_row(self, e):
        v = self.verdict(e["id"])
        return dict(id=e["id"], subject=e["subject"], predicate=e["predicate"], object=e["object"],
                    statement=self.statement(e), layer=e.get("layer"), source=e.get("source"),
                    source_version=e.get("source_version"), knowledge_level=e.get("knowledge_level"),
                    verdict=v.get("verdict") if v else None, confidence=float(v["confidence"]) if v and v.get("confidence") else None,
                    potency=potency(e))

    def node_view(self, nid, limit=200):
        """The node and its edges grouped by predicate (at most `limit` per direction)."""
        if nid not in self.nodes:
            return None
        outgoing = sorted(self.by_subject.get(nid, []), key=lambda e: (e["predicate"], e["object"]))[:limit]
        incoming = sorted(self.by_object.get(nid, []), key=lambda e: (e["predicate"], e["subject"]))[:limit]
        groups = {}
        for e in outgoing + incoming:
            groups.setdefault(e["predicate"], []).append(self._edge_row(e))
        return dict(node=self.nodes[nid], label=self.label(nid), degree=len(self.by_subject.get(nid, [])) +
                    len(self.by_object.get(nid, [])), edges_by_predicate=groups,
                    predictions_available=self._predictions is not None)

    def explain_edge(self, eid):
        """Everything behind one edge: provenance, pruning verdict and reasons, corroborating rows, timeline."""
        if eid not in self.edges:
            return None
        e = self.edges[eid]
        others = self.same_pair(e["subject"], e["object"], exclude=eid)
        events = self._events(e) + [ev for o in others for ev in self._events(o, corroborating=True)]
        events.sort(key=lambda x: x["date"])
        return dict(id=eid, statement=self.statement(e),
                    subject={"id": e["subject"], **self.nodes.get(e["subject"], {})},
                    object={"id": e["object"], **self.nodes.get(e["object"], {})},
                    predicate=e["predicate"], provenance={k: e.get(k) for k in KGX if k in e},
                    properties={k: v for k, v in e.items() if k not in KGX},
                    verdict=self.verdict(eid), corroboration=[self._edge_row(o) for o in others],
                    source_url=source_url(e), release=release_words(e.get("layer"), e.get("source_version")),
                    timeline=dict(events=events, summary=timeline.summarise(events)))

    def _events(self, e, corroborating=False):
        """Dated timeline events for one edge row: its own first_year (ChEMBL activities; '2018.0' counts) or its layer's
        release (or download) month. The label is in a reader's words: the source and its release, never a layer code."""
        layer, source = e.get("layer", ""), e.get("source") or e.get("primary_knowledge_source") or ""
        own = year_of(e.get("first_year"))
        date = own or RELEASE_DATES.get(layer)                 # the edge's own first year beats the release month
        if date is None:
            return []
        kind = "new_support" if corroborating else "first_seen"
        v = self.verdict(e["id"])
        pruned = bool(v and v.get("verdict") == "prune")          # a pruned row is shown but does not support
        rel = release_words(layer, e.get("source_version"))
        label = source or rel or "source"
        if own:
            label += ", first reported" + (f" (in {rel})" if rel and not label.startswith(rel) else "")
        elif rel and label.startswith(rel):
            label += ", release"
        elif rel:
            label += f" ({rel})" if layer in DOWNLOADED else f" ({rel} release)"
        label += " - pruned" if pruned else ""
        return [timeline.event(date, label, kind, "neutral" if pruned else "supports", reference=e.get("source_record"))]



class PharosGraph(GraphViews):
    def __init__(self, root=ROOT, layers=None, predictions=None, skip_predicates=None):
        self.root = Path(root)
        self.layers_used, self.layers_missing = [], []
        skip = set(skip_predicates if skip_predicates is not None
                   else [p for p in os.environ.get("TMKG_PHAROS_SKIP_PREDICATES", "").split(",") if p])
        node_frames, edge_frames, verdict_frames = [], [], []
        for layer in (layers or LAYERS):
            d = make_graphs.layer_dir(layer, self.root)
            if d is None or not (d / "edges.tsv.gz").exists():
                self.layers_missing.append(layer)
                continue
            self.layers_used.append(layer)
            node_frames.append((layer, _read(d / "nodes.tsv.gz")))
            e = _read(d / "edges.tsv.gz")
            if skip:
                e = e[~e["predicate"].isin(skip)]
            e["layer"] = layer
            edge_frames.append(e)
            vp = d / "edge_verdicts.tsv.gz"
            if vp.exists():
                verdict_frames.append(_read(vp).assign(layer=layer))
        if not edge_frames:
            raise FileNotFoundError(f"no built Pharos layer under {self.root / 'data' / 'kg'}: run "
                                    "python pipelines/build_kg.py sources/pharos.yaml first")
        nodes = harmonised_nodes(node_frames)
        self.nodes = {r["id"]: _clean(r) for r in nodes.to_dict("records")}
        self._chembl = chembl_names(self.root)
        self.synonyms = name_compounds(self.nodes, self._chembl)
        edges = pd.concat(edge_frames, ignore_index=True).drop_duplicates("id", keep="first")
        edges = edges.fillna("")
        for c in edges.columns:
            if c not in ("id", "source_record") and edges[c].nunique() < 50_000:
                edges[c] = edges[c].astype("category")
        self._edges = edges.reset_index(drop=True)
        self._eindex = pd.Index(self._edges["id"])
        self._cols = [c for c in self._edges.columns if c != "id"]
        self.edges = _Edges(self)
        self.by_subject = _Adjacency(self, self._edges.groupby("subject", observed=True).indices)
        self.by_object = _Adjacency(self, self._edges.groupby("object", observed=True).indices)
        if verdict_frames:
            v = pd.concat(verdict_frames, ignore_index=True).drop_duplicates("id", keep="first")
            self._verdicts = v.set_index("id")
        else:
            self._verdicts = None
        if predictions:
            pfile, self._model = Path(predictions), "metapath" if "metapath" in Path(predictions).name else "gnn"
        else:
            pfile, self._model = predictions_source(self.root)
        self._predictions = _read(pfile) if pfile is not None and pfile.exists() else None
        if self._predictions is not None:
            self._predictions["score"] = self._predictions["score"].astype(float)
        self.version = {"layers": self.layers_used,
                        "source_versions": sorted(set(self._edges["source_version"].astype(str)) - {""})}
        self._names = None

    # ------------------------------------------------------------------ KGIndex interface
    def _edge(self, pos):
        row = self._edges.iloc[pos]
        d = {"id": row["id"]}
        d.update((k, str(v)) for k, v in zip(self._cols, row.values[1:]) if v != "")
        return d

    def out(self, nid, predicate):
        return sorted((e for e in self.by_subject.get(nid, []) if e["predicate"] == predicate),
                      key=lambda e: (e["object"], e["id"]))

    def inn(self, nid, predicate):
        return sorted((e for e in self.by_object.get(nid, []) if e["predicate"] == predicate),
                      key=lambda e: (e["subject"], e["id"]))

    def name(self, nid):
        return self.nodes.get(nid, {}).get("name")

    def verdict(self, eid):
        if self._verdicts is None or eid not in self._verdicts.index:
            return None
        return _clean(self._verdicts.loc[eid].to_dict())

    # ------------------------------------------------------------------ views
    def status(self):
        by_layer = self._edges["layer"].value_counts().to_dict()
        by_cat = pd.Series([n["category"] for n in self.nodes.values()]).value_counts().to_dict()
        by_pred = self._edges["predicate"].value_counts().to_dict()
        verdicts = (self._verdicts["verdict"].value_counts().to_dict() if self._verdicts is not None else {})
        preds = None
        if self._predictions is not None:
            preds = dict(rows=int(len(self._predictions)), model=self._model,
                         graphs=sorted(set(self._predictions["graph"])), tasks=sorted(set(self._predictions["task"])))
        return dict(layers=self.layers_used, layers_missing=self.layers_missing, nodes=len(self.nodes),
                    edges=int(len(self._edges)), edges_by_layer={k: int(v) for k, v in by_layer.items()},
                    nodes_by_category={k: int(v) for k, v in by_cat.items()},
                    edges_by_predicate={k: int(v) for k, v in by_pred.items()},
                    verdicts={k: int(v) for k, v in verdicts.items()}, predictions=preds,
                    source_versions=self.version["source_versions"])

    def link_counts(self, ids):
        """FIX1 F9. Takes node ids. Returns {id: number of links of that node in the graph}."""
        return {i: len(self.by_subject.get(i, [])) + len(self.by_object.get(i, [])) for i in ids if i in self.nodes}

    def suggest(self, q, n=3):
        """"Did you mean" for a query no search matches: the nodes whose name or symbol is most like q (close_matches over names
        of about the same length that start with the same character). Each: {id, label, category, did_you_mean, score}."""
        q = q.lower().strip()
        if not q:
            return []
        if self._names is None:
            self.find_entities("")
        lo, hi = length_window(q)
        cands = [(t, nid) for t, ids in self._names.items() if t[:1] == q[:1] and lo <= len(t) <= hi for nid in ids]
        return [dict(id=nid, label=self.label(nid), category=self.nodes[nid]["category"], did_you_mean=True, score=round(s, 3))
                for s, nid, _ in close_matches(q, cands, n)]

    def search(self, q, limit=20):
        """Nodes whose id, name, symbol or synonym contains q (case-insensitive). An exact match (the whole id, name, symbol
        or a synonym) ranks above a substring match; then shorter names first."""
        q = q.lower().strip()
        if not q:
            return []
        hits = []
        for n in self.nodes.values():
            m = search_match(q, n, self.synonyms.get(n["id"], ()))
            if m is not None:
                hits.append((-m, len(n.get("name") or n["id"]), n["id"], n))
        hits.sort(key=lambda h: h[:3])
        return [dict(id=n["id"], label=self.label(n["id"]), category=n["category"]) for *_, n in hits[:limit]]

    def card(self, nid, n_pred=10, per_kind=40, picture_edges=40):
        """The entity card behind /api/pharos/card/{id} (docs/USABILITY_PLAN.md P1.2). Takes a node id. Returns:
        about (type word, name, ids, properties), summary counts, known = the node's edges grouped by evidence kind
        (personas.EVIDENCE_KINDS order; each group: n, n_kept, n_pruned, rows as sentences with a confidence word),
        predicted = top-n GNN links with paths (or a note), picture = a small graph (kept non-text-mining edges first,
        predicted links flagged) for the page to draw. None when the node does not exist. The same card comes from
        the build-time index (pharos_index.CardIndex.card) without reading the edge table."""
        if nid not in self.nodes:
            return None
        label = self.label(nid)
        rows = []
        for e in sorted(self.by_subject.get(nid, []), key=lambda e: (e["predicate"], e["object"])):
            rows.append((e, e["object"], True))
        for e in sorted(self.by_object.get(nid, []), key=lambda e: (e["predicate"], e["subject"])):
            rows.append((e, e["subject"], False))
        groups = {}
        for e, other, outgoing in rows:
            r = card_row(self._edge_row(e), label, other, self.label(other),
                         self.nodes.get(other, {}).get("category", ""), outgoing, personas.evidence_kind(e))
            groups.setdefault(r["kind"], []).append(r)
        known = []
        for kind, g in groups.items():
            g.sort(key=card_row_order)
            known.append(dict(kind=kind, n=len(g), n_kept=sum(r["word"] == "kept" for r in g),
                              n_pruned=sum(r["word"] == "pruned" for r in g), rows=g[:per_kind]))
        pred = self.predicted_links(nid, n=n_pred) or {}
        return assemble_card(nid, self.nodes[nid], label, known, pred.get("predictions", []), pred.get("note"),
                             n_known=len(rows), picture_edges=picture_edges)

    def same_pair(self, subject, obj, exclude=None):
        """Edges that state the same (subject, object) pair in any layer or source (other than `exclude`)."""
        pos = set(self.by_subject.groups.get(subject, [])) & set(self.by_object.groups.get(obj, []))
        return [self._edge(p) for p in sorted(pos) if self._edges.iloc[p]["id"] != exclude]

    def predicted_links(self, nid, n=20, task=None):
        """Top-n predictions involving the node (subject or object), best score first, each with its supporting
        two-hop paths (shared neighbours) and the layers that already contain the pair."""
        if nid not in self.nodes:
            return None
        if self._predictions is None:
            return dict(node=nid, label=self.label(nid), predictions=[], note=NO_PREDICTIONS_NOTE)
        p = self._predictions
        p = p[(p["subject"] == nid) | (p["object"] == nid)]
        if task:
            p = p[p["task"] == task]
        p = p.sort_values(["score", "subject", "object"], ascending=[False, True, True]).drop_duplicates(["subject", "object"])
        rows = []
        for r in p.head(n).to_dict("records"):
            other = r["object"] if r["subject"] == nid else r["subject"]
            rows.append(dict(subject=r["subject"], object=r["object"], other=other, other_label=self.label(other),
                             other_category=self.nodes.get(other, {}).get("category", ""),
                             score=round(float(r["score"]), 4), rank=int(r["rank"]) if str(r.get("rank", "")).isdigit() else None,
                             graph=r.get("graph"), task=r.get("task"),
                             known_in=sorted({o["layer"] for o in self.same_pair(r["subject"], r["object"])}),
                             model=MODEL_WORDS[self._model],
                             paths=(metapath_card_paths(r["paths"], self.label) if self._model == "metapath"
                                    else self.paths(r["subject"], r["object"]))))
        return dict(node=nid, label=self.label(nid), predictions=rows)

    def paths(self, a, b, max_paths=3):
        """Up to max_paths two-hop paths a - x - b through shared neighbours (any direction), as edge statements."""
        def neigh(nid):
            out = {}
            for e in self.by_subject.get(nid, []):
                out.setdefault(e["object"], e)
            for e in self.by_object.get(nid, []):
                out.setdefault(e["subject"], e)
            return out
        na, nb = neigh(a), neigh(b)
        shared = sorted((set(na) & set(nb)) - {a, b}, key=lambda x: (self.label(x), x))
        return [dict(via=x, via_label=self.label(x),
                     edges=[dict(id=na[x]["id"], statement=self.statement(na[x])),
                            dict(id=nb[x]["id"], statement=self.statement(nb[x]))]) for x in shared[:max_paths]]

    # ------------------------------------------------------------------ querier support
    def schema_card(self):
        """Labels, their properties, (label, type, label) patterns with counts and relationship properties,
        from the tables (querier.schema_card walks every edge dict, too slow for 16M edges)."""
        labels = {}
        for n in self.nodes.values():
            labels.setdefault(local(n["category"]), set()).update(k for k in n if k != "category")
        cat = pd.Series({k: local(v["category"]) for k, v in self.nodes.items()})
        e = self._edges
        pats = (pd.DataFrame({"s": e["subject"].astype(str).map(cat), "p": e["predicate"].astype(str).map(local),
                              "o": e["object"].astype(str).map(cat)}).value_counts().sort_index())
        rel_props = sorted(c for c in self._edges.columns if c not in ("subject", "object") and (self._edges[c] != "").any())
        import querier
        rel_enums = querier.finish_value_sets(querier.add_value_sets({}, self._edges))    # CC-31c: literals are checked
        return dict(labels={k: sorted(v) for k, v in sorted(labels.items())},
                    patterns=[(s, p, o, int(n)) for (s, p, o), n in pats.items()], rel_props=rel_props, enums={},
                    rel_enums=rel_enums)

    def find_entities(self, question, limit=10):
        """Node ids named in the question: identifiers (MONDO:, CHEMBL, NCBIGene:, GO:, HP:, UniProt) and exact
        matches of 1-3 word phrases against node names and symbols. Nothing is guessed."""
        if self._names is None:
            self._names = {}
            for nid, n in self.nodes.items():
                for k in ("name", "symbol"):
                    if n.get(k) and len(n[k]) >= 3:
                        self._names.setdefault(n[k].lower(), []).append(nid)
        found = set()
        for pat in ID_PATTERNS:
            for m in re.findall(pat, question):
                for cand in (m, f"CHEMBL.COMPOUND:{m}" if m.startswith("CHEMBL") else None,
                             f"UniProtKB:{m}" if re.fullmatch(r"[OPQ][0-9][A-Z0-9]{3}[0-9]", m) else None):
                    if cand in self.nodes:
                        found.add(cand)
        words = [re.sub(r"'s$", "", w) for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", question)]   # "ibuprofen's" -> ibuprofen
        for i in range(len(words)):
            for j in (1, 2, 3):
                phrase = " ".join(words[i:i + j]).lower()
                found.update(self._names.get(phrase, []))
        return sorted(found)[:limit]

    def demo(self, ids=None):
        """Runs DEMO_QUESTIONS (only those whose id is in `ids` when given) on the graph. Each result: rows,
        evidence ids, and whether the expected shape holds."""
        import querier
        out = []
        for d in DEMO_QUESTIONS:
            if ids is not None and d["id"] not in ids:
                continue
            try:
                res = querier.run_local(d["cypher"], self, max_steps=0, max_seconds=0, max_bindings=0, evidence_first=True)
                missing = [c for c in d["expect"]["columns"] if c not in res["columns"]]
                ok = len(res["rows"]) >= d["expect"]["min_rows"] and not missing
                out.append(display_result(dict(d, rows=res["rows"], columns=res["columns"], evidence=res["evidence"], passed=ok,
                                               ordering=res.get("ordering"),
                                               note="" if ok else (f"columns missing: {missing}" if missing else "no rows on this graph"))))
            except Exception as err:                       # a demo must never take the page down
                out.append(dict(d, rows=[], columns=[], evidence=[], passed=False, note=f"query failed: {err}"))
        return out


def ask(graph, nim, question, runner=None):
    """Plain-language question -> validated openCypher over the Pharos graph -> rows with evidence edge ids.
    The demo questions are the worked examples shown to the model. A question whose text is a tested question is answered
    by that tested question's fixed query (tested_answer, no model call; with a `runner`, i.e. another database such as
    Neptune, the model path is kept ON PURPOSE (S03/PK2): a Neptune question must exercise that database, and the fixed
    query would read the local graph instead; the Neptune path is therefore the one place where a tested question's text
    reaches the model). CC-31g: a question that names nothing the graph knows is answered by abstention() without a model call;
    every answer carries entity_notes (substitution_notes) and their sentences (entity_sentences), without the notes for
    names the executed query did resolve (entity_caveats). Row values are returned as the page prints them
    (display_result)."""
    import querier
    demo = tested_question(question) if runner is None else None
    if demo is not None:
        return dict(tested_answer(graph, demo), question=question)
    entities = graph.find_entities(question)
    if not entities:
        return abstention(graph, nim, question)
    notes = substitution_notes(graph, question, entities)
    examples = [dict(id=d["id"], question=d["question"], cypher=d["cypher"]) for d in DEMO_QUESTIONS]
    res = querier.Querier(nim, graph, examples=examples, pattern_examples=[], runner=runner, evidence_first=True).ask(
        question, entities=entities)
    lab = graph.label if hasattr(graph, "label") else None
    notes, sentences = entity_caveats(notes, res.get("cypher"), lab)
    extra = dict(read_as=read_as(notes, res.get("cypher"), lab))                  # FIX2 F6 (BB-1)
    if res.get("explanation"):
        extra["explanation"] = explanation_labels(res["explanation"], question, res.get("cypher"), lab)
    return display_result(dict(res, entity_notes=notes, entity_sentences=sentences, **extra))
