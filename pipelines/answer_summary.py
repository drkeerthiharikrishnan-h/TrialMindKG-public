"""
The bottom line of an answer, composed by rules from the answer itself (no language model; docs/ux/V3_DESIGN_2026-11-08.md).

Two kinds of answer:
- a graph answer (POST /api/pharos/ask, GET /api/pharos/demo/<id>, POST /api/query): question, entities, cypher, columns,
  rows (each row cites edge ids), evidence, problems, label, ...
- a trial report (POST /api/ask, GET /api/ask_stream): sections [{role, title, claims [{claim, evidence, evidence_status,
  support_status, indicators, uncertainty}]}], rejected, flow_links, seeds, run, label.

Every sentence is built from fields of the answer, or from the edge records the answer cites (edge_facts: source, knowledge
level, pruning verdict, confidence and reasons looked up by edge id in the graph that answered). Nothing else is stated.
Each point carries refs (row numbers, claim numbers "section.claim", edge ids) so the page can show what it rests on.

Functions
- edge_facts(graph, edge_ids, limit=300) -> {edge id: facts}   facts of the cited edges, looked up in the graph
- strength_word(knowledge_level, kind=None) -> 'curated' | 'experimental' | 'statistical' | 'text-mined' | ...
- reasons_words(reasons) -> plain words for a pruning reasons string ('plausibility=0.53;sources=3;...')
- cypher_words(cypher) -> the graph query in plain words
- source_word(source) -> the source name a reader sees
- classify_row(row, columns, facts) -> how one row is supported (class by its strongest link that was not pruned, per step)
- pharos_summary(result, facts=None, labels=None) -> summary dict
- program_facts(root) -> {program id: name, what_failed, ...} from the trial graph's node table
- trial_summary(report, programs=None) -> summary dict
- sig3 / display_value / display_rows -> row cells as the page prints them (decimals at 3 significant figures)
- strip_arm_codes(text) -> a claim sentence without registry group codes (OG001 ...)
Summary dict: headline, headline_refs, points [{text, refs}], confidence {level, reason}, counts, caveats,
what_would_change, next_step, plan, checks, evidence_analysis, refs, abstained (True when there is nothing to rest an
answer on); a graph answer also has pruned_hidden ({n, of, rows, edges, text, action} or None: the links the pruning
check removed, for a page that hides them behind a control), a trial report stated_failure.
"""
import csv
import math
import re
import sys
from pathlib import Path

try:                                    # the predicate verbs and evidence kinds the page uses (pipelines/personas.py)
    import personas as _personas
except ImportError:                     # imported as pipelines.answer_summary
    from pipelines import personas as _personas

EDGE_ID = re.compile(r"^e[0-9a-f]{16}$")
STRENGTH_BY_LEVEL = {"knowledge_assertion": "curated", "statistical_association": "statistical",
                     "text_co_occurrence": "text-mined", "observation": "observed", "not_provided": "unspecified",
                     "prediction": "predicted", "logical_entailment": "inferred"}
STRENGTH_GLOSS = {"curated": "a curated database statement",
                  "experimental": "a measured value or interaction (binding, activity)",
                  "statistical": "a statistical association (for example GWAS or expression)",
                  "observed": "an observation recorded by the source",
                  "text-mined": "words found together in papers: context, not proof",
                  "unspecified": "the source gives no evidence level",
                  "predicted": "a prediction, not a source statement",
                  "inferred": "inferred from other statements"}
STRONG = {"curated", "experimental"}
WEAK = {"text-mined", "predicted"}
RANK = {"curated": 4, "experimental": 4, "statistical": 3, "observed": 3, "inferred": 2, "unspecified": 1,
        "text-mined": 0, "predicted": 0}
REASON_WORDS = {"plausibility": "plausibility", "sources": "sources for the pair", "reliability": "source reliability",
                "source_score": "the source's own score", "records": "record support", "corroboration": "corroboration",
                "flagged": "flagged"}
NON_LABEL_COLUMNS = re.compile(r"(^e\d*$|evidence|source|knowledge_level|^id$|_id$)")


# ------------------------------------------------------------------ small helpers
def _plural(n, word, many=None):
    return f"{n:,} {word if n == 1 else (many or word + 's')}"


def _fmt_refs(nums, prefix="row"):
    """[1, 2, 3, 7] -> 'rows 1-3, 7' (at most 8 refs written out)."""
    nums = sorted(set(nums))
    if not nums:
        return ""
    spans, start, prev = [], nums[0], nums[0]
    for n in nums[1:] + [None]:
        if n is not None and n == prev + 1:
            prev = n
            continue
        spans.append(f"{start}" if start == prev else f"{start}-{prev}")
        if n is not None:
            start = prev = n
    more = len(spans) - 8
    text = ", ".join(spans[:8]) + (f" and {more} more" if more > 0 else "")
    return f"{prefix if len(nums) == 1 else prefix + 's'} {text}"


def _names(values, n=3):
    vals = [v for v in dict.fromkeys(str(x) for x in values if x not in (None, ""))]
    if not vals:
        return ""
    head = vals[:n]
    text = head[0] if len(head) == 1 else ", ".join(head[:-1]) + " and " + head[-1]
    return text + (f" (and {len(vals) - n} more)" if len(vals) > n else "")


def _float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _first_sentence(text, cap=240):
    t = " ".join(str(text or "").split())
    m = re.search(r"(?<=[.!?])\s+(?=[A-Z])", t)
    if m and m.start() <= cap:
        t = t[:m.start()]
    if len(t) > cap:
        t = t[:cap].rsplit(" ", 1)[0] + "…"
    return t


def strength_word(knowledge_level, kind=None):
    """Takes a BioLink knowledge level and, optionally, the evidence kind (personas.evidence_kind). Returns the word the
    page shows: a curated statement whose kind is 'binding or activity' is 'experimental' (a measured value)."""
    w = STRENGTH_BY_LEVEL.get(str(knowledge_level or ""), None)
    if w == "curated" and kind == "binding or activity":
        return "experimental"
    if w is None and kind == "text mining":
        return "text-mined"
    return w


def reasons_words(reasons):
    """'plausibility=0.527;sources=3;reliability=0.5' -> 'plausibility 0.53; 3 sources for the pair; source reliability 0.50'."""
    out = []
    for part in str(reasons or "").split(";"):
        if "=" not in part:
            if part.strip():
                out.append(part.strip())
            continue
        k, v = (x.strip() for x in part.split("=", 1))
        f = _float(v)
        if k == "sources" and f is not None:
            out.append(f"{int(f)} source{'s' if int(f) != 1 else ''} for the pair")
        else:
            out.append(f"{REASON_WORDS.get(k, k.replace('_', ' '))} {f:.2f}" if f is not None else f"{k.replace('_', ' ')} {v}")
    return "; ".join(out)


def _verdict_word(v):
    if not v:
        return None
    return {"keep": "kept", "prune": "pruned"}.get(str(v.get("verdict")), v.get("verdict"))


def _potency(e):
    """pharos_app.potency (the card's potency words) when importable, else None."""
    try:
        import pharos_app
    except Exception:
        return None
    return pharos_app.potency(e)


def edge_facts(graph, edge_ids, limit=1000):
    """Takes the graph that answered (PharosGraph, IndexGraph or the trial KGIndex: anything with .edges[id], optionally
    .verdict(id) and .label(id)) and edge ids. Returns {id: facts} for the first `limit` ids (1,000: the pruned-link count of an
    answer needs the verdict of every link it cites; the largest tested answer cites 566): statement parts,
    source, layer, knowledge level, strength word, kind, measured activity if any, pruning verdict word, confidence and
    reasons (raw and in words). Ids not in the graph are left out."""
    out = []
    for eid in list(dict.fromkeys(edge_ids))[:limit]:
        try:
            if eid not in graph.edges:
                continue
            e = graph.edges[eid]
        except Exception:                               # a lookup must never break an answer
            continue
        v = graph.verdict(eid) if hasattr(graph, "verdict") else None
        lab = (lambda n: graph.label(n)) if hasattr(graph, "label") else (lambda n: (graph.nodes.get(n) or {}).get("name") or n)
        kind = _personas.evidence_kind(e)
        level = e.get("knowledge_level")
        out.append((eid, dict(
            id=eid, subject=e.get("subject"), subject_label=lab(e.get("subject")), predicate=e.get("predicate"),
            phrase=_personas.predicate_phrase(e.get("predicate") or ""), object=e.get("object"),
            object_label=lab(e.get("object")), source=e.get("source") or e.get("primary_knowledge_source"),
            source_record=e.get("source_record"), layer=e.get("layer"), source_version=e.get("source_version"),
            knowledge_level=level, strength=strength_word(level, kind), kind=kind,
            activity=e.get("activity"), activity_type=e.get("activity_type"), potency=_potency(e),
            evidence_type=e.get("evidence_type"),
            verdict=_verdict_word(v), confidence=_float((v or {}).get("confidence")),
            reasons=(v or {}).get("reasons"), reasons_words=reasons_words((v or {}).get("reasons")) if v else None)))
    return dict(out)


# ------------------------------------------------------------------ the graph query in plain words
_PAT = re.compile(r"\((?P<nv>\w*)(?::(?P<nl>\w+))?(?:\s*\{(?P<np>[^}]*)\})?\)|(?P<l><?-)\[(?P<rv>\w*)(?::(?P<rt>[\w|]+))?\](?P<r>->|-)|(?P<comma>,)")


def _node_words(label, props):
    noun = _personas.CATEGORIES.get(label, label.lower()) if label else "anything"
    bits = []
    for m in re.finditer(r"(\w+)\s*:\s*'([^']*)'", props or ""):
        bits.append(f"named {m.group(2)}" if m.group(1) == "name" else f"with {m.group(1)} {m.group(2)}")
    return " ".join([noun] + bits)


def cypher_words(cypher):
    """Takes the openCypher the graph ran. Returns it in plain words ('' when there is none): the pattern, the filter,
    what is returned, the order and the row limit. Deterministic regex reading of the querier's subset."""
    c = " ".join(str(cypher or "").split())
    if not c:
        return ""
    m = re.match(r"MATCH (?P<pat>.*?)(?: WHERE (?P<where>.*?))? RETURN (?P<ret>.*?)(?: ORDER BY (?P<ord>.*?))?(?: LIMIT (?P<lim>\d+))?$", c, re.I)
    if not m:
        return c
    parts, pat = [], []
    for t in _PAT.finditer(m.group("pat")):
        if t.group("comma"):
            parts.append(" ".join(pat))
            pat = []
        elif t.group("r") is not None:
            rel = " or ".join(_personas.predicate_phrase(x) for x in (t.group("rt") or "related_to").split("|"))
            pat.append(f"<-{rel}-" if t.group("l") == "<-" else f"-{rel}->")
        else:
            pat.append(_node_words(t.group("nl"), t.group("np")))
    parts.append(" ".join(pat))
    out = "Find " + "; and ".join(p for p in parts if p)
    if m.group("where"):
        out += f", keeping rows where {m.group('where')}"
    cols = [re.split(r"\s+AS\s+", x.strip(), flags=re.I)[-1].replace("_", " ") for x in m.group("ret").split(",")]
    steps = [c for c in cols if re.fullmatch(r"e\d+", c.strip())]      # FIX1 F8 (G1-U-06): e1..eN are the steps' evidence
    cols = [c for c in cols if c not in steps] + (["the evidence of each step"] if steps else [])
    out += ". Return " + ", ".join(cols)
    if m.group("ord"):
        o = m.group("ord").strip()
        desc = o.upper().endswith(" DESC")
        o = re.sub(r"\s+(DESC|ASC)$", "", o, flags=re.I).replace("_", " ")
        out += f", {'highest ' + o + ' first' if desc else 'ordered by ' + o}"
    if m.group("lim"):
        out += f"; at most {m.group('lim')} rows"
    return out + "."


# ------------------------------------------------------------------ graph answers
def _row_edges(row, columns):
    out = []
    for c in columns:
        v = row.get(c)
        for x in (v if isinstance(v, list) else [v]):
            if isinstance(x, str) and EDGE_ID.match(x):
                out.append(x)
    return out


def _row_level_columns(row, columns):
    """Knowledge levels and sources the row itself returns (columns named *knowledge_level / *_level / *source). A merged
    row (querier evidence-first ordering) holds a list in a column: its values are flattened."""
    def vals(c):
        v = row.get(c)
        return [x for x in (v if isinstance(v, list) else [v]) if x]
    levels = [x for c in columns if c.endswith("knowledge_level") or c.endswith("_level") for x in vals(c)]
    sources = [x for c in columns if c == "source" or c.endswith("_source") for x in vals(c)]
    return levels, sources


CURIE = re.compile(r"^[A-Za-z][\w.]*:\S+$")


def _label_column(columns, rows=()):
    """The column that names each answer: the first non-evidence column whose first value is a name, not an id (CURIE)."""
    cands = [c for c in columns if not NON_LABEL_COLUMNS.search(c)]
    for c in cands:
        v = rows[0].get(c) if rows else None
        if not (isinstance(v, str) and CURIE.match(v)):
            return c
    return cands[0] if cands else None


EVIDENCE_COLUMNS = re.compile(r"(^e\d*$|evidence|source|knowledge_level)")


def id_label(row, columns, lab_col):
    """Takes one answer row, the columns and the label column. Returns how the page names an answer whose label is empty
    (FIX2 F6, G2-U-01): 'unnamed compound (CHEMBL2103830)' for a ChEMBL compound id, else the id itself, read from the column
    that holds an identifier (the one named like the label column first: drug_name -> drug / drug_id); None without one."""
    stem = re.sub(r"_?name$", "", str(lab_col or ""))
    cands = [c for c in columns if c != lab_col and not EVIDENCE_COLUMNS.search(c)]
    cands.sort(key=lambda c: 0 if stem and re.sub(r"_id$", "", c) == stem else 1)
    for c in cands:
        v = row.get(c)
        v = v[0] if isinstance(v, list) and v else v
        if isinstance(v, str) and CURIE.match(v):
            m = re.match(r"^CHEMBL\.COMPOUND:(.+)$", v)
            return f"unnamed compound ({m.group(1)})" if m else v
    return None


def _limit(cypher):
    m = re.search(r"\bLIMIT\s+(\d+)\s*$", str(cypher or ""), re.I)
    return int(m.group(1)) if m else None


# The class of a row, best first. 'curated' = a curated or measured link; 'statistical' = a statistical association or an
# observation; 'other' = only links whose source states no evidence level (or inferred ones); 'text-mined' = text mining
# or prediction only; 'pruned' = every link of some step was pruned; 'unknown' = no evidence level could be read.
ROW_CLASSES = ["curated", "statistical", "other", "text-mined", "pruned", "unknown"]


def _rank_class(rank):
    return "curated" if rank >= 4 else "statistical" if rank == 3 else "other" if rank >= 1 else "text-mined"


def _and(parts):
    parts = [p for p in parts if p]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1] if parts else ""


def classify_row(row, columns, facts):
    """Takes one answer row, the answer's columns and the facts of the cited links (edge_facts). Returns how the row is
    supported: {row_class (one of ROW_CLASSES), strength (the strength word that decides the class, or None), steps
    [{column, strength, edges}], sources (every cited source), live_sources (the sources of the links that were not
    pruned), best_sources and best_edges (of the strongest link of each
    step; empty when the class was read from the level columns), pruned, kept, edges}.

    A step is one column of link ids (r1.id AS e1): after the querier merges duplicate rows of one answer, that column
    holds every link that states the step. A step is as strong as its STRONGEST link that was not pruned; a row with
    several steps is as strong as its weakest step. So a row is 'text-mined' only when some step has no curated, measured
    or statistical link at all. Without looked-up facts the row's own knowledge-level columns are read the same way (one
    column = one step)."""
    edges, steps = [], []
    for c in columns:
        v = row.get(c)
        es = [x for x in (v if isinstance(v, list) else [v]) if isinstance(x, str) and EDGE_ID.match(x)]
        if not es:
            continue
        edges += es
        live = [e for e in es if e in facts and facts[e].get("verdict") != "pruned" and facts[e].get("strength")]
        if live:
            top = max(RANK.get(facts[e]["strength"], 1) for e in live)
            best = [e for e in live if RANK.get(facts[e]["strength"], 1) == top]
            steps.append(dict(column=c, strength=facts[best[0]]["strength"], rank=top, edges=best))
        elif any(e in facts for e in es) and all(facts[e].get("verdict") == "pruned" for e in es if e in facts):
            steps.append(dict(column=c, strength="pruned", rank=-1, edges=[]))
        else:
            steps.append(dict(column=c, strength=None, rank=None, edges=[]))
    levels, level_sources = _row_level_columns(row, columns)
    if not steps or any(s["rank"] is None for s in steps):       # no (or partial) lookups: the row's own level columns
        steps = []
        for c in columns:
            if c.endswith("knowledge_level") or c.endswith("_level"):
                v = row.get(c)
                words = [w for w in (strength_word(x) for x in (v if isinstance(v, list) else [v]) if x) if w]
                if words:
                    top = max(words, key=lambda w: RANK.get(w, 1))
                    steps.append(dict(column=c, strength=top, rank=RANK.get(top, 1), edges=[]))
    sources = [facts[e]["source"] for e in edges if e in facts and facts[e].get("source")] or level_sources
    best_edges = [e for s in steps for e in s["edges"]]
    live_sources = [facts[e]["source"] for e in edges if e in facts and facts[e].get("source") and facts[e].get("verdict") != "pruned"]
    out = dict(steps=steps, sources=sources, live_sources=live_sources if any(e in facts for e in edges) else sources,
               best_edges=best_edges, edges=edges,
               best_sources=list(dict.fromkeys(facts[e]["source"] for e in best_edges if facts[e].get("source"))),
               pruned=[e for e in edges if facts.get(e, {}).get("verdict") == "pruned"],
               kept=[e for e in edges if facts.get(e, {}).get("verdict") == "kept"])
    if not steps:
        return dict(out, row_class="unknown", strength=None)
    weakest = min(steps, key=lambda s: s["rank"])
    if weakest["rank"] < 0:
        return dict(out, row_class="pruned", strength="pruned")
    return dict(out, row_class=_rank_class(weakest["rank"]), strength=weakest["strength"])


def _noun(column, columns):
    """The word for what a row names, from its column: 'disease', 'target_name' -> 'target'; a bare 'name' column takes
    the word of the first column (the id it names)."""
    w = re.sub(r"_?name$", "", str(column or ""))
    if not w:
        w = next((c for c in columns if c != column and not re.search(r"(^e\d*$|evidence|source|_level$)", c)), "answer")
        w = re.sub(r"_id$", "", w)
    return w.replace("_", " ").strip() or "answer"


def _plural_word(word):
    if re.search(r"(s|x|z|ch|sh)$", word):
        return word + "es"
    if re.search(r"[^aeiou]y$", word):
        return word[:-1] + "ies"
    return word + "s"


def source_word(source):
    """Takes an edge's source as the graph holds it. Returns the name a reader sees: a curated project table
    ('curated:<file name>') is 'curated record' (no file name in an answer); every other source keeps its name."""
    s = str(source or "")
    return "curated record" if s.startswith("curated:") else s


def _short(text, cap=90):
    t = " ".join(str(text).split())
    return t if len(t) <= cap else t[:cap].rsplit(" ", 1)[0] + "…"


MEASURE_WORDS = re.compile(r"score|count|conf|rank|pchembl|value|support|weight|^n$|^n_|_n$|num|total|strength|evidence_n", re.I)


def order_kind(cypher, ordering=None):
    """FIX1 F8 (gate 1, G1-P5-03). Takes the query and the querier's `ordering`. Returns how the rows are ordered, for the
    word in front of the named answers: 'evidence' (the querier ranked the rows by their evidence: "Best-supported"),
    'measure' (ORDER BY ... DESC, or ORDER BY a score / count / confidence column: "Top"), 'alphabetical' (ORDER BY a name or id,
    ascending: "First ..., alphabetically") or 'none' (no order: "... include"). The querier's evidence ranking comes first
    when it ran: it sorts the rows by their evidence and the query's ORDER BY only breaks ties (querier._rank)."""
    if (ordering or {}).get("by") == "evidence":
        return "evidence"
    m = re.search(r"ORDER BY\s+(\w+(?:\.\w+)?)(\s+DESC)?", str(cypher or ""), re.I)
    if m and (m.group(2) or MEASURE_WORDS.search(m.group(1).split(".")[-1])):
        return "measure"
    return "alphabetical" if m else "none"


def _graph_headline(rows, columns, lab_col, lead_rows, per_row, facts, n, ordering, lim, n_links, n_sources, kind="measure"):
    """The headline of a graph answer: the first three answers by name, the source of their strongest links, the total and
    how many are text-mined only ('PTGS2 is associated with A, B and C (CTD, DrugCentral Indication); 3,705 diseases in
    all, 3,373 text-mined only.'). Takes the rows, columns, the column that names an answer, the row numbers in the order
    to name them, classify_row per row, the link facts, the row count, the querier's `ordering`, the query's row limit
    and the link / source counts. Returns (headline, {rows, edges} it rests on). An answer that is text-mined only, has
    no support left after pruning or has no readable evidence level is named only when no better-supported answer
    exists. Falls back to the counts when the rows name nothing."""
    def name_of(i):
        v = rows[i - 1].get(lab_col) if lab_col else None
        v = v[0] if isinstance(v, list) and v else v
        if v in (None, "") or isinstance(v, (list, dict)):
            return id_label(rows[i - 1], columns, lab_col)       # an empty label: the id, as the page names it
        return str(v)
    supported = [i for i in lead_rows if per_row[i]["row_class"] in ("curated", "statistical", "other")]
    picked, names = [], []
    for i in supported or lead_rows:
        v = name_of(i)
        if v is None or v in names:
            continue
        picked.append(i)
        names.append(v)
        if len(names) == 3:
            break
    if not names:
        return (f"{_plural(n, 'row')} answer{'s' if n == 1 else ''} this question, resting on {_plural(n_links, 'link')} from "
                f"{_plural(n_sources, 'source')}.", dict(rows=[], edges=[]))
    noun = _noun(lab_col, columns)
    nouns = _plural_word(noun)
    best_class = {}                                       # answer name -> the best class among its rows
    for i in per_row:
        v, c = name_of(i), per_row[i]["row_class"]
        if v is not None and (v not in best_class or ROW_CLASSES.index(c) < ROW_CLASSES.index(best_class[v])):
            best_class[v] = c
    by_evidence = ordering.get("by") == "evidence"
    total = ordering.get("n_total") if by_evidence and ordering.get("n_total") else len(best_class)
    text_only = sum(1 for c in best_class.values() if c == "text-mined") + (ordering.get("n_more_text_mined") or 0 if by_evidence else 0)
    listed = _and([_short(x) for x in names])
    # one step from one entity: say it as a sentence ('PTGS2 is associated with A, B and C')
    best = [[facts[e] for e in per_row[i]["best_edges"]] for i in picked]
    subjects = {(f.get("subject_label"), f.get("phrase")) for fs in best for f in fs}
    one_step = (all(len(per_row[i]["steps"]) == 1 for i in picked) and all(best) and len(subjects) == 1
                and all(str(f.get("object_label") or "").lower() == name.lower() for fs, name in zip(best, names) for f in fs))
    subject, phrase = next(iter(subjects)) if one_step else (None, None)
    if one_step and subject and phrase:
        lead = f"{_short(subject)} {phrase} {listed}"
    elif total <= len(names):
        lead = f"{(noun if len(names) == 1 else nouns).capitalize()}: {listed}"
    elif kind == "measure":
        lead = f"Top {nouns}: {listed}"
    elif kind == "evidence":
        lead = f"Best-supported {nouns}: {listed}"
    elif kind == "alphabetical":
        lead = f"First {nouns}, alphabetically: {listed}"
    else:
        lead = f"{nouns.capitalize()} include {listed}"
    srcs = list(dict.fromkeys(source_word(s) for i in picked for s in per_row[i]["best_sources"]))
    if srcs:
        lead += " (" + ", ".join(srcs[:3]) + (f" and {len(srcs) - 3} more" if len(srcs) > 3 else "") + ")"
    capped = bool(lim and n >= lim and not by_evidence)
    if total > len(names) or text_only or capped:
        lead += (f"; {total:,} {noun if total == 1 else nouns} " + ("shown, and there may be more" if capped else "in all") +
                 (f", {text_only:,} text-mined only" if text_only else ""))
    return lead + ".", dict(rows=picked, edges=[e for i in picked for e in per_row[i]["best_edges"]])


def json_key(v):
    """A hashable form of a row value (a merged row holds lists)."""
    return tuple(json_key(x) for x in v) if isinstance(v, list) else str(v)


# ------------------------------------------------------------------ values as the page prints them
def sig3(x):
    """Takes a number. Returns it as text with 3 significant figures, never dropping digits of the whole-number part
    (74.33001924 -> '74.3', 0.000123456 -> '0.000123', 1234.567 -> '1235')."""
    x = float(x)
    if x == 0 or x != x or x in (float("inf"), float("-inf")):
        return str(x)
    d = 2 - int(math.floor(math.log10(abs(x))))
    return f"{x:.{max(d, 0)}f}"


_NUMBER = re.compile(r"^-?(\d+\.\d+|\.\d+|\d+(\.\d+)?[eE][-+]?\d+)$")


def display_value(v):
    """Takes one cell of an answer row. Returns it as the page should print it: a decimal number with more than 3
    significant figures is rounded (sig3); a list is treated cell by cell; everything else (names, ids, whole numbers,
    short decimals such as '10.7') is returned unchanged."""
    if isinstance(v, list):
        return [display_value(x) for x in v]
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, float):
        s = sig3(v)
        return float(s) if float(s) != v else v
    if isinstance(v, str) and _NUMBER.match(v.strip()):
        s = sig3(v)
        return s if float(s) != float(v) else v
    return v


def display_rows(rows, columns=None):
    """Takes answer rows (list of dicts). Returns new rows with every cell passed through display_value; link-id cells
    are untouched (they are not numbers). The input is not changed."""
    return [{k: display_value(v) for k, v in r.items()} for r in rows or []]


_ARM = r"[OBE]G\d{3}"
_ARMS = rf"{_ARM}(?:\s*(?:vs\.?|versus|and|or|to|,|/)\s*{_ARM})*"


def strip_arm_codes(text):
    """Takes a sentence of a trial claim. Returns it without ClinicalTrials.gov result-group codes (OG000, BG001, EG002):
    '(OG001 vs OG002)' and 'for groups OG000 vs OG001' are dropped; a code that is the subject of a verb becomes 'the
    compared groups'. The numbers and names around the codes are not changed."""
    t = re.sub(rf"\s*\((?:(?:groups?|arms?)\s+)?{_ARMS}\)", "", str(text or ""))
    t = re.sub(rf",?\s*(?:for|in|between|comparing|across)?\s*(?:the\s+)?(?:groups?|arms?)\s+{_ARMS}", "", t)
    t = re.sub(rf",?\s*(?:for|in|between|comparing|across)\s+{_ARMS}", "", t)
    t = re.sub(_ARMS, "the compared groups", t)
    t = re.sub(r"\s+([,.;:)])", r"\1", t)
    t = re.sub(r"([,;:])\s*(?=[,;.])", "", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def query_entities(cypher, graph, limit=5):
    """FIX1 F8 (gate 1, G1-U-03). Takes the openCypher that ran and the graph it ran on (anything with find_entities(text) and
    label(id) or nodes). Returns the ids the query is anchored on, in query order: every {id:'...'} value, and for every
    {name:'...'} / {symbol:'...'} value the graph nodes whose label is exactly that name (case ignored). A tested question
    carries no recognised entities of its own, so its "How the question was read" line is filled from the query it ran
    instead of saying that nothing was recognised. [] when the query names nothing."""
    import pharos_app
    ids, names = pharos_app.query_anchors(cypher)
    lab = graph.label if hasattr(graph, "label") else (lambda n: ((getattr(graph, "nodes", {}) or {}).get(n) or {}).get("name") or n)
    out = [i for i in ids]
    for name in names:
        try:
            hits = graph.find_entities(name) if hasattr(graph, "find_entities") else []
        except Exception:
            hits = []
        out += [h for h in hits if str(lab(h)).lower() == name.lower()][:1]
    return list(dict.fromkeys(out))[:limit]


def _zero_row_next_step(entities):
    """Takes the recognised entities [{id, label}]. Returns the next step of an answer without rows (FIX2 F6, G2-U-03): the
    card of the gene or disease the question named (usability.card_next_steps: "Open the MAP3K5 card."), else the generic
    sentence."""
    try:
        import usability
    except ImportError:
        from pipelines import usability
    sentence, _ = usability.card_next_steps(entities)
    return (sentence + ".") if sentence else "Try one of the tested questions, or open the card of an entity named in the question."


def pharos_summary(result, facts=None, labels=None):
    """Takes a graph answer (keys question, entities, columns, rows, evidence, cypher, problems, label; a tested question
    also has passed / note / why), the facts of its cited edges (edge_facts) and {id: label} for the entities. Returns the
    summary dict (module docstring). An empty answer says so and why (the problems list), never an empty table.

    What the sentences rest on (review of 11 Nov, U1 / U8 / AC-06):
    - headline: the top answers by name with the source of their strongest links, the total and how many are text-mined
      only (_graph_headline); never a bare row count when the rows name something.
    - every row is classed by classify_row: per step its strongest link that was not pruned, per row its weakest step.
      'N have curated or measured support, M statistical only, K text-mined only' counts those classes.
    - links the pruning check removed do not count as support; the answer says how many there are and in which rows, and
      pruned_hidden carries the same for a page that hides them behind a control.
    - confidence: low when more than half the rows are text-mined only, have no support left or no readable level; high
      when at least half have curated or measured support from two or more sources; else moderate."""
    facts = facts or {}
    labels = labels or {}
    rows, columns = result.get("rows") or [], result.get("columns") or []
    problems = [str(p) for p in (result.get("problems") or [])]
    label = result.get("label") or {}
    entities = [dict(id=e, label=labels.get(e, e)) for e in (result.get("entities") or [])]
    cypher = result.get("cypher") or ""
    dropped = [d for d in (result.get("dropped_constraints") or []) if d.get("text")]
    plan = dict(entities=entities, query_words=cypher_words(cypher), cypher=cypher, dropped=dropped)
    caveats = [str(x) for x in (result.get("entity_sentences") or [])]       # CC-31g: a name read as a shorter one
    if dropped:                                          # CC-31d: never narrow a question silently
        caveats.append("The query that ran leaves out " + ("a condition" if len(dropped) == 1 else f"{len(dropped)} conditions") +
                       " an earlier attempt had (" + "; ".join(d["text"] for d in dropped[:5]) +
                       "); the rows are not limited by " + ("it." if len(dropped) == 1 else "them."))
    if label.get("text") and label.get("kind") in ("offline", "fallback"):
        caveats.append(label["text"])
    checks = []
    if "passed" in result:
        checks.append(dict(name="tested-question shape check", ok=bool(result.get("passed")),
                           text="passed" if result.get("passed") else f"failed: {result.get('note')}"))
    checks.append(dict(name="query validation", ok=not problems and bool(cypher),
                       text="the query passed validation" if not problems and cypher else "; ".join(problems) or "no query"))
    if result.get("attempts"):
        checks.append(dict(name="attempts", ok=not problems, text=f"{_plural(len(result['attempts']), 'attempt')} to write a valid query"))
    if dropped:
        checks.append(dict(name="conditions kept", ok=False,
                           text=f"{_plural(len(dropped), 'condition')} of an earlier attempt not in the query that ran"))

    if not rows:
        why = problems or ([str(result["note"])] if result.get("note") else ["the query ran and matched nothing in the graph"])
        attempts = result.get("attempts") or []
        if problems and attempts and not any("too broad" in p for p in problems):   # CC-31g: no validator text in the answer
            why = [f"the model did not write a valid query for this question in {_plural(len(attempts), 'attempt')} "
                   "(the validator's reasons are under checks)"]
        points = [dict(text=f"Here is why: {'; '.join(w.rstrip('.') for w in why)}.", refs={})]
        sugg = result.get("suggestions") or []
        if sugg:
            points.append(dict(text="Did you mean: " + ", ".join(f"{s.get('label')} ({s.get('id')})" for s in sugg) + "?",
                               refs=dict(entities=[s.get("id") for s in sugg])))
        if entities:
            points.append(dict(text=f"Entities recognised in the question: {_names([e['label'] for e in entities], 5)}.",
                               refs=dict(entities=[e["id"] for e in entities])))
        elif not result.get("abstained"):                # an abstention already says so in its first point
            points.append(dict(text="No entity in the question was recognised in the graph.", refs={}))
        change = ["A question the graph can turn into a valid query; the tested questions are known to return rows."]
        if not entities:
            change.append("Naming a gene, disease or compound the way the graph labels it, so it can be recognised.")
        return dict(headline="The graph returned no rows for this question.", points=points, abstained=True,
                    confidence=dict(level="low", reason="No rows came back, so there is nothing to rest an answer on."),
                    counts=dict(rows=0, links=0, links_looked_up=0, sources=0, kept=0, pruned=0, unscored=0, by_strength={}),
                    caveats=caveats, what_would_change=change,
                    next_step=_zero_row_next_step(entities),
                    plan=plan, checks=checks, evidence_analysis=dict(by_strength={}, agreement=[], flagged=[]),
                    refs=dict(rows={}, edges={}))

    n = len(rows)
    row_edges = {i + 1: _row_edges(r, columns) for i, r in enumerate(rows)}
    links = list(dict.fromkeys([e for es in row_edges.values() for e in es] + list(result.get("evidence") or [])))
    looked = [e for e in links if e in facts]
    per_row = {i: classify_row(r, columns, facts) for i, r in enumerate(rows, 1)}
    multi_step = any(len(p["steps"]) > 1 for p in per_row.values())
    sources = list(dict.fromkeys(s for p in per_row.values() for s in p["sources"]))
    live_sources = list(dict.fromkeys(s for p in per_row.values() for s in p["live_sources"]))   # pruned links do not count
    in_class = {c: [i for i, p in per_row.items() if p["row_class"] == c] for c in ROW_CLASSES}
    strong_rows, stat_rows, text_rows = in_class["curated"], in_class["statistical"], in_class["text-mined"]
    unknown_rows, gone_rows = in_class["unknown"], in_class["pruned"]
    pruned_rows = [i for i, p in per_row.items() if p["pruned"]]
    kept = sum(1 for e in looked if facts[e].get("verdict") == "kept")
    pruned_links = [e for e in looked if facts[e].get("verdict") == "pruned"]
    pruned = len(pruned_links)
    by_strength = {}
    for e in looked:
        s = facts[e].get("strength") or "unknown"
        by_strength[s] = by_strength.get(s, 0) + 1
    if not looked:                                       # no lookups: count the rows' own level columns
        for p in per_row.values():
            s = p["strength"] or "unknown"
            by_strength[s] = by_strength.get(s, 0) + 1
    counts = dict(rows=n, links=len(links), links_looked_up=len(looked), sources=len(sources), kept=kept, pruned=pruned,
                  unscored=len(looked) - kept - pruned, by_strength=by_strength,
                  rows_strong=len(strong_rows), rows_statistical=len(stat_rows), rows_other=len(in_class["other"]),
                  rows_text_only=len(text_rows), rows_pruned_only=len(gone_rows), rows_unknown=len(unknown_rows),
                  rows_with_pruned=len(pruned_rows))

    lab_col = _label_column(columns, rows)
    ordering = result.get("ordering") or {}
    by_evidence = ordering.get("by") == "evidence"
    lim = _limit(cypher)
    more = bool(by_evidence and ordering.get("n_more")) or bool(lim and n >= lim and not by_evidence)
    shown = " shown" if more else ""
    order = re.search(r"ORDER BY\s+(\w+)(\s+DESC)?", cypher, re.I)
    ranked = by_evidence or bool(order and order.group(2))  # evidence first, or DESC on a measure = a real ranking
    # the answers the headline names: the first rows of a ranking, else the rows with the best support first
    lead_rows = list(range(1, n + 1)) if ranked else sorted(per_row, key=lambda i: ROW_CLASSES.index(per_row[i]["row_class"]))
    headline, lead = _graph_headline(rows, columns, lab_col, lead_rows, per_row, facts, n, ordering, lim, len(links), len(sources),
                                     kind=order_kind(cypher, ordering))

    # ---- points: how the rows are supported, what is text-mined only, what the pruning check hid
    def part(idx, one, many):
        return [f"{len(idx)} {one if len(idx) == 1 else many}"] if idx else []
    parts = (part(strong_rows, "has curated or measured support", "have curated or measured support") +
             part(stat_rows, "has statistical support only", "have statistical support only") +
             part(in_class["other"], "rests only on links whose source states no evidence level",
                  "rest only on links whose source states no evidence level") +
             part(text_rows, "is text-mined only", "are text-mined only") +
             part(gone_rows, "rests only on pruned links", "rest only on pruned links") +
             part(unknown_rows, "has no evidence level that could be read", "have no evidence level that could be read"))
    rule = ("A row is classed by its weakest step, and each step by its strongest link that was not pruned." if multi_step
            else "A row is classed by its strongest link that was not pruned.")
    support = (f"Of the {n} rows{shown}, " + _and(parts) if n > 1 else "The one row " + parts[0].split(" ", 1)[1]) + ". " + rule
    best_refs = strong_rows or stat_rows
    points = [dict(text=support, refs=dict(rows=best_refs, edges=[e for i in best_refs for e in per_row[i]["best_edges"]]))]
    if text_rows:
        srcs = _names([source_word(s) for i in text_rows for s in (per_row[i]["best_sources"] or per_row[i]["sources"])])
        points.append(dict(text=f"{len(text_rows)} of {n} rows rest on text mining or prediction only ({srcs}); the graph keeps "
                                f"these as context, never as proof ({_fmt_refs(text_rows)}).",
                           refs=dict(rows=text_rows, edges=[e for i in text_rows for e in row_edges[i]])))
    hidden = None
    if looked and not kept and not pruned:
        points.append(dict(text=f"None of the {len(looked):,} links behind this answer carries a pruning verdict in this graph.", refs={}))
    elif looked and not pruned:
        points.append(dict(text=f"No link behind this answer was pruned: the pruning check kept {kept:,} of {len(looked):,}"
                                + (f" and {len(looked) - kept:,} carry no verdict." if len(looked) > kept else "."), refs={}))
    elif looked:
        was = "was" if pruned == 1 else "were"
        t = (f"{pruned:,} of the {len(looked):,} links behind this answer {was} pruned and {'is' if pruned == 1 else 'are'} left out "
             f"of its support ({_fmt_refs(pruned_rows)}); the pruning check kept {kept:,}.")
        points.append(dict(text=t, refs=dict(rows=pruned_rows, edges=pruned_links)))
        # the same fact for a page that hides pruned links behind a control (the links stay in the rows and in refs.edges)
        hidden = dict(n=pruned, of=len(looked), rows=pruned_rows, edges=pruned_links, action="show them",
                      text=f"{_plural(pruned, 'link')} behind this answer {was} pruned and {'is' if pruned == 1 else 'are'} hidden")

    # agreement: the same answer value named by more than one source
    agreement = []
    if lab_col:
        groups = {}
        for i, r in enumerate(rows, 1):
            groups.setdefault(str(r.get(lab_col)), []).append(i)
        for val, idx in groups.items():
            if len(idx) < 2:                             # one row naming two sources is a multi-hop row, not agreement
                continue
            srcs = list(dict.fromkeys(s for i in idx for s in per_row[i]["sources"]))
            if len(srcs) > 1:
                agreement.append(dict(value=val, sources=srcs, rows=idx))
    flagged = [dict(edge=e, row=i, reasons=facts[e].get("reasons_words"), confidence=facts[e].get("confidence"))
               for i in pruned_rows for e in per_row[i]["pruned"]][:10]

    every = " for every step" if multi_step else ""
    not_shown = ""
    if by_evidence and ordering.get("n_more"):
        more_text = ordering.get("n_more_text_mined") or 0
        not_shown = (f" {ordering['n_more']:,} more answers are not shown" +
                     (f", {more_text:,} of them text-mined only." if more_text else "."))
    if len(unknown_rows) > n / 2:
        conf = dict(level="low", reason=f"The evidence level is not known for {len(unknown_rows)} of {n} rows.")
    elif len(text_rows) > n / 2:
        conf = dict(level="low", reason=f"{len(text_rows)} of {n} rows{shown} rest on text mining or prediction only.")
    elif len(gone_rows) > n / 2:
        conf = dict(level="low", reason=f"{len(gone_rows)} of {n} rows{shown} have no support left after pruning.")
    elif len(strong_rows) >= n / 2 and len(live_sources) >= 2:
        conf = dict(level="high", reason=f"{len(strong_rows)} of {n} rows{shown} have curated or measured support{every}, from "
                                         f"{_plural(len(live_sources), 'source')}; pruned links are not counted.{not_shown}")
    else:
        bits = [f"{len(strong_rows)} of {n} rows{shown} have curated or measured support{every}"]
        if stat_rows:
            bits.append(f"{len(stat_rows)} statistical only")
        if text_rows:
            bits.append(f"{len(text_rows)} text-mined only")
        if gone_rows:
            bits.append(f"{len(gone_rows)} with no support left after pruning")
        if len(live_sources) < 2:
            bits.append("all from one source" + (" once pruned links are left out" if len(sources) > len(live_sources) else ""))
        conf = dict(level="moderate", reason="; ".join(bits) + "." + not_shown)

    if text_rows:
        caveats.append(f"Only text-mined (or predicted) support for {_fmt_refs(text_rows)}.")
    if len(sources) == 1:
        caveats.append(f"Single source: every row cites {source_word(sources[0])}.")
    if gone_rows:
        caveats.append(f"{_fmt_refs(gone_rows).capitalize()} {'has' if len(gone_rows) == 1 else 'have'} no support left after pruning: "
                       "every link of at least one step was pruned.")
    if by_evidence and ordering.get("n_more"):
        more_text = ordering.get("n_more_text_mined") or 0
        caveats.append(f"Showing the top {ordering['n_shown']} of {ordering['n_total']} answers by evidence; {ordering['n_more']} more "
                       f"are not shown" + (f", {more_text} of them text-mined only." if more_text else "."))
    elif lim and n >= lim and not by_evidence:
        caveats.append(f"The query stops at {lim} rows; there may be more answers than shown.")
    if len(links) > len(looked) and facts:
        caveats.append(f"{len(links) - len(looked):,} of {len(links):,} links were not looked up for this summary.")
    if "passed" in result and not result.get("passed"):
        caveats.append(f"The tested-question check failed: {result.get('note')}.")

    change = []
    if text_rows:
        change.append("Curated, measured or statistical support for the text-mined rows would raise confidence.")
    if pruned:
        change.append("A different pruning verdict on the links left out would count them as support again.")
    if more:
        change.append("A higher row limit could add answers that are not shown here.")
    if conf["level"] == "high":
        change.append("A curated source that contradicts these rows, or pruning of their links, would lower confidence.")
    if not change:
        change.append("A second independent source for the single-source rows would raise confidence.")
    best = (strong_rows or [1])[0]
    first_edge = (per_row[best]["best_edges"] or row_edges[best] or [None])[0]
    nxt = f"Open the evidence for row {best}" if first_edge else f"Look at row {best}"     # FIX1 F8: no raw edge id
    nxt += f", or open the card for {entities[0]['label']}." if entities else "."
    edges_ref = {e: facts[e] for e in looked}
    read = [r for r in (result.get("read_as") or []) if isinstance(r, dict) and r.get("label")]
    if read:                                             # FIX2 F6 (BB-1): a name read as another node says so first
        headline = "Read as " + _and([str(r["label"]) for r in read]) + ": " + headline
    return dict(headline=headline, headline_refs=lead, points=points[:4], abstained=False, confidence=conf, counts=counts,
                caveats=caveats, ordering=ordering or None, pruned_hidden=hidden,
                what_would_change=change[:2], next_step=nxt, plan=plan, checks=checks,
                evidence_analysis=dict(by_strength=by_strength, agreement=agreement[:10], n_agreement=len(agreement),
                                       flagged=flagged, row_classes={c: len(v) for c, v in in_class.items() if v}, row_class_rule=rule,
                                       strength_gloss={k: STRENGTH_GLOSS[k] for k in by_strength if k in STRENGTH_GLOSS}),
                refs=dict(rows={i: row_edges[i] for i in row_edges}, row_strength={i: per_row[i]["strength"] for i in per_row},
                          row_class={i: per_row[i]["row_class"] for i in per_row}, edges=edges_ref))


# ------------------------------------------------------------------ trial reports
def _claims(report):
    out = []
    for si, s in enumerate(report.get("sections") or [], 1):
        for ci, c in enumerate(s.get("claims") or [], 1):
            out.append((f"{si}.{ci}", s, c))
    return out


ROOT = Path(__file__).resolve().parents[1]
_PROGRAMS = {}
PROGRAM_CATEGORY = "tmkg:ClinicalTrialProgram"
# words by which a diagnosis claim states what happened (not only what was planned)
OUTCOME_WORDS = re.compile(r"\b(miss(ed|es)?|fail(ed|s|ure)?|did not|does not|not (statistically )?significant|non-?significant|"
                           r"no (statistically )?significant|futil\w*|stopped|terminated|halted|discontinued|lack of|"
                           r"no (clear |meaningful )?(benefit|difference|improvement)|worse|safety)\b", re.I)


RESULT_NUMBERS = re.compile(r"\bp\s*[=<>≤]\s*0?\.\d|\b\d{2}\s?% CI\b|\bCI\s+[-−]?\d")


def program_facts(root=ROOT):
    """Takes the repo root. Returns {program id: {id, name, what_failed, sponsor, year, indication}} for every failed
    program of the trial graph, read once from its node table (outputs/kg_nodes.csv; the curated inventory is where
    `what_failed` comes from). {} when the file is not there."""
    key = str(root)
    if key not in _PROGRAMS:
        out, p = {}, Path(root) / "outputs" / "kg_nodes.csv"
        try:
            csv.field_size_limit(sys.maxsize)
            with open(p, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    if r.get("category") == PROGRAM_CATEGORY:
                        out[r["id"]] = {k: r.get(k) or None for k in ("id", "name", "what_failed", "sponsor", "year", "indication")}
        except OSError:
            out = {}
        _PROGRAMS[key] = out
    return _PROGRAMS[key]


RESTATES_FAILURE = re.compile(r"\b(lists?|listed|records?|recorded|states?|stated|marks?|marked|labell?ed|reports?)\b[^.]*"
                              r"\b(failed|failure|discontinued|terminated|unsuccessful)\b", re.I)


def restates_failure(text):
    """Takes a claim's text. Returns True when it only repeats that the program failed (a source 'lists the program as
    failed'): it holds no number and no result, so it adds nothing to the stated failure the headline already gives."""
    t = str(text or "")
    return bool(RESTATES_FAILURE.search(t)) and not re.search(r"\d", t)


def _diagnosis_claim(claims):
    """Takes the numbered claims [(ref, section, claim)]. Returns (ref, claim, supported?) of the claim the headline
    quotes. In the failure-diagnosis section (else the first section with claims): the first supported claim that reports
    a result (a p-value or a confidence interval), else the first supported claim that states an outcome (OUTCOME_WORDS),
    else the first supported claim, else the first claim (supported? = False). A claim that only restates that the program
    failed (restates_failure) is skipped; when no other claim is left it returns (None, None, False) and the headline shows
    the stated failure alone."""
    diag = [x for x in claims if x[1].get("role") == "failure_diagnostician"] or [x for x in claims if x[1] is claims[0][1]]
    text = lambda x: str(x[2].get("claim") or "")
    diag = [x for x in diag if not restates_failure(text(x))]
    if not diag:
        return None, None, False
    sup = [x for x in diag if x[2].get("support_status") == "supported"]
    pick = (next((x for x in sup if RESULT_NUMBERS.search(text(x))), None) or
            next((x for x in sup if OUTCOME_WORDS.search(text(x))), None) or (sup[0] if sup else diag[0]))
    return pick[0], pick[2], bool(sup)


def trial_summary(report, programs=None):
    """Takes a trial report (orchestrator.ask output, with label) and, optionally, {program id: facts} (default:
    program_facts(), the trial graph's own program nodes). Returns the summary dict (module docstring). Claims are
    numbered '<section>.<claim>' in the report's own order (section 1 = the first section of the report). The headline is
    the stated failure of the program the report is about (the curated `what_failed` of its node) and then the first
    supported diagnosis claim that states an outcome, with registry group codes removed (strip_arm_codes)."""
    sections = report.get("sections") or []
    claims = _claims(report)
    rejected = report.get("rejected") or []
    label = report.get("label") or {}
    node_label = {n["id"]: n.get("label") or n["id"] for s in sections for n in ((s.get("graph") or {}).get("nodes") or [])}
    seeds = [dict(id=x, label=node_label.get(x, x)) for x in (report.get("seeds") or [])]
    plan = dict(entities=seeds, query_words="Each specialist reads a subgraph around these seeds; the verifier checks every "
                                            "claim against the evidence it cites." if seeds else "", cypher="")
    caveats = []
    if label.get("text") and label.get("kind") in ("offline", "fallback"):
        caveats.append(label["text"])
    evidence = {}
    for _, _, c in claims:
        for e in c.get("evidence") or []:
            evidence.setdefault(e.get("id"), dict(source=e.get("source"), source_record=e.get("source_record"),
                                                  evidence_type=e.get("evidence_type")))
    sources = list(dict.fromkeys(v["source"] for v in evidence.values() if v.get("source")))
    by = lambda key: [ref for ref, _, c in claims if key(c)]
    supported = by(lambda c: c.get("support_status") == "supported")
    hyp = by(lambda c: c.get("support_status") == "hypothesis_generating" or c.get("evidence_status") in ("post_hoc", "hypothesis_generating"))
    mixed = by(lambda c: c.get("support_status") == "mixed" or c.get("contradiction_check") == "contradicted")
    strong = by(lambda c: (c.get("indicators") or {}).get("evidence_strength") == "strong")
    limited = by(lambda c: (c.get("indicators") or {}).get("evidence_strength") == "limited")
    low_map = by(lambda c: (c.get("indicators") or {}).get("mapping_confidence") in ("low",))
    rewritten = by(lambda c: bool(c.get("repaired_from")))
    empty = [s for s in sections if not s.get("claims")]
    filled = [s for s in sections if s.get("claims")]
    ev_status, strength = {}, {}
    for _, _, c in claims:
        ev_status[c.get("evidence_status")] = ev_status.get(c.get("evidence_status"), 0) + 1
        k = (c.get("indicators") or {}).get("evidence_strength") or "unknown"
        strength[k] = strength.get(k, 0) + 1
    counts = dict(claims=len(claims), sections=len(sections), sections_with_claims=len(filled), rejected=len(rejected),
                  evidence=len(evidence), sources=len(sources), supported=len(supported), hypothesis_generating=len(hyp),
                  mixed=len(mixed), rewritten=len(rewritten), by_strength=strength, by_evidence_status=ev_status)
    checks = [dict(name="verifier", ok=True, text=f"kept {_plural(len(claims), 'claim')}, removed {len(rejected)}"),
              dict(name="rewrites", ok=True, text=f"{_plural(len(rewritten), 'claim')} rewritten once after a failed check")]
    agreement = [dict(source=f"{l['source']['role']}#{l['source']['index'] + 1}", target=f"{l['target']['role']}#{l['target']['index'] + 1}",
                      shared=l.get("shared") or []) for l in (report.get("flow_links") or [])]
    role_num = {s.get("role"): i for i, s in enumerate(sections, 1)}
    for a, l in zip(agreement, report.get("flow_links") or []):
        a["source_ref"] = f"{role_num.get(l['source']['role'], '?')}.{l['source']['index'] + 1}"
        a["target_ref"] = f"{role_num.get(l['target']['role'], '?')}.{l['target']['index'] + 1}"
    ev_analysis = dict(by_strength=strength, by_evidence_status=ev_status, agreement=agreement[:20], n_agreement=len(agreement),
                       flagged=[dict(claim=r) for r in mixed])
    refs = dict(claims={ref: dict(section=s.get("title"), role=s.get("role"), claim=c.get("claim"),
                                  evidence=[e.get("id") for e in c.get("evidence") or []]) for ref, s, c in claims},
                evidence=evidence)

    if not claims:
        why = [f"{s.get('title')}: {s.get('note')}" for s in sections if s.get("note")] or ["no section has a verified claim"]
        points = [dict(text=f"Here is why: {'; '.join(why)}.", refs={})]
        if rejected:
            points.append(dict(text=f"The verifier removed {_plural(len(rejected), 'claim')}: "
                                    f"{_names([r for x in rejected for r in x.get('reasons') or []], 3)}.", refs=dict(rejected=len(rejected))))
        return dict(headline="The report has no verified claim for this question.", points=points, abstained=True,
                    confidence=dict(level="low", reason="No claim survived the verifier."), counts=counts, caveats=caveats,
                    what_would_change=["Evidence in the trial layer that the specialists can cite for this question."],
                    next_step="Ask about a failed program by name, or start from a tested example.",
                    plan=plan, checks=checks, evidence_analysis=ev_analysis, refs=refs)

    ref0, s0, c0 = claims[0]
    programs = program_facts() if programs is None else programs
    in_report = list(dict.fromkeys([x for x in (report.get("seeds") or []) if x in programs] +
                                   [nid for nid in node_label if nid in programs]))
    stated = [dict(program=programs[x].get("name") or x, what_failed=programs[x]["what_failed"])
              for x in in_report if programs[x].get("what_failed")][:2]
    dref, dclaim, dsupported = _diagnosis_claim(claims)
    headline = " ".join(f"{x['program']}. Stated failure: {x['what_failed'].rstrip('.')}." for x in stated)
    if dclaim is not None or not stated:             # no other claim left and a stated failure: that alone is the headline
        if dclaim is None:                            # nothing but restatements and no stated failure: the first claim, as before
            dref, dclaim, dsupported = claims[0][0], claims[0][2], False
        said = _first_sentence(strip_arm_codes(dclaim.get("claim")), cap=260)
        if not said.endswith((".", "…", "?", "!")):
            said += "."
        headline += (" " if headline else "") + (f"Diagnosis (claim {dref}, supported): " if dsupported
                                                 else f"Diagnosis (claim {dref}, not marked supported): ") + said
    points = [dict(text=f"The report keeps {_plural(len(claims), 'verified claim')} in {len(filled)} of {len(sections)} sections; "
                        f"the verifier removed {len(rejected)}.", refs=dict(claims=[r for r, _, _ in claims]))]
    for s in filled[:2]:
        refs_s = [r for r, ss, _ in claims if ss is s]
        sup = [r for r in refs_s if r in supported]
        hy = [r for r in refs_s if r in hyp]
        t = f"{s.get('title')}: {_plural(len(refs_s), 'claim')}, {len(sup)} supported"
        t += f", {len(hy)} hypothesis-generating ({', '.join(hy[:6])})" if hy else ""
        points.append(dict(text=t + f" (claims {refs_s[0]}-{refs_s[-1]})." if len(refs_s) > 1 else t + f" (claim {refs_s[0]}).",
                           refs=dict(claims=refs_s)))
    if empty:
        note = re.sub(r"^no verified claim:\s*", "", empty[0].get("note") or "") or "the subgraph lacks the needed evidence"
        points.append(dict(text=f"No verified claim for {_names([s.get('title') for s in empty])}: {note}.", refs={}))
    points = points[:4]

    n = len(claims)
    if mixed:
        conf = dict(level="low", reason=f"{_plural(len(mixed), 'claim')} ({', '.join(mixed[:5])}) met contradicting evidence.")
    elif len(strong) / n < 0.25 or len(hyp) / n > 0.5:
        conf = dict(level="low", reason=f"Only {len(strong)} of {n} claims cite strong sources, and {len(hyp)} are hypothesis-generating.")
    elif len(strong) / n >= 0.75 and len(hyp) / n <= 0.25 and not low_map:
        conf = dict(level="high", reason=f"{len(strong)} of {n} claims cite strong sources (registry or curated records) and "
                                         f"at most a quarter are hypothesis-generating.")
    else:
        bits = [f"{len(strong)} of {n} claims cite strong sources"]
        if hyp:
            bits.append(f"{len(hyp)} rest on post-hoc or exploratory analyses (hypothesis-generating)")
        if low_map:
            bits.append(f"{len(low_map)} have low id matching")
        conf = dict(level="moderate", reason="; ".join(bits) + ".")
    if hyp:
        caveats.append(f"Hypothesis-generating claims (post-hoc or exploratory): {', '.join(hyp[:8])}.")
    if limited:
        caveats.append(f"Limited source strength (no curated or statistical source) for claims {', '.join(limited[:8])}.")
    if low_map:
        caveats.append(f"Low id matching for claims {', '.join(low_map[:8])}: the cited records may not name the same entity.")
    if len(sources) == 1:
        caveats.append(f"Single source: every claim cites {source_word(sources[0])}.")
    if empty:
        caveats.append(f"Empty section{'s' if len(empty) > 1 else ''}: {_names([s.get('title') for s in empty], 4)}.")
    change = []
    if hyp:
        change.append(f"A prespecified or confirmatory analysis for claims {', '.join(hyp[:4])} would turn them from "
                      "hypothesis-generating into supported.")
    if empty:
        change.append(f"Trial-layer evidence for {empty[0].get('title')} would fill the empty section.")
    if mixed:
        change.append("Resolving the contradicting evidence would decide the mixed claims.")
    if not change:
        change.append("A contradicting registry or curated record would lower confidence.")
    e0 = (dclaim.get("evidence") or [{}])[0]
    where = source_word(e0.get("source"))                # a curated project table is named as such, without its file or row
    where += f", {e0.get('source_record')}" if where == e0.get("source") and e0.get("source_record") else ""
    nxt = f"Open the evidence for claim {dref} ({where})." if e0.get("id") else f"Read claim {dref}."
    return dict(headline=headline, headline_refs=dict(claims=[dref]), stated_failure=stated, points=points, abstained=False,
                confidence=conf, counts=counts, caveats=caveats,
                what_would_change=change[:2], next_step=nxt, plan=plan, checks=checks, evidence_analysis=ev_analysis, refs=refs)


def summary_text(summary):
    """Every sentence of a summary joined (for tests and the PDF): headline, points, confidence reason, caveats, changes, next step."""
    parts = [summary.get("headline", "")] + [p["text"] for p in summary.get("points", [])]
    parts += [summary.get("confidence", {}).get("reason", "")] + list(summary.get("caveats", [])) + list(summary.get("what_would_change", []))
    parts.append(summary.get("next_step", ""))
    return " ".join(p for p in parts if p)
row_edges = _row_edges              # public name (pipelines/app.py collects an answer's edge ids with it)
