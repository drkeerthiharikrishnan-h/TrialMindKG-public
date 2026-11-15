"""
Pharos alone versus the agent-built graph, visible in every answer: the server part (review finding U12).

The agent-built graph = the Pharos links plus the links of the enrichment layers. This module reads the layer of every link an
answer cites and says, per answer row, per entity-card section and per drawn node / edge, which side it rests on:

    'pharos_only'       every cited link is a Pharos link
    'enrichment_only'   every cited link was added by an enrichment layer
    'both'              links of both sides are cited
    'unknown'           none of the cited links could be looked up in the graph that answered

and whether the row would still be there in Pharos alone (`in_pharos_alone`). A row needs one link per step of the question
(gene -> protein -> compound = two steps); it stays in Pharos alone only when every step has a Pharos link, and the Pharos links
chosen for the steps meet at the same nodes. So a 'both' row can still be missing from Pharos alone (a Pharos first step, an
enrichment second step), and the sentence counts those rows as well.

Nothing here runs a query or a model. The marks come from the links the answer already cites, so the Pharos-alone view is the
shown answer without the enrichment, not a second run of the question: when the full answer was cut at a row limit, Pharos alone
may hold more rows than this view shows, and the view's bottom line says so.

Pure functions (no web framework needed)
- pharos_layers() -> the layer names that are "Pharos alone" (make_graphs.RECIPES['G0'])
- side_of(layer) -> 'pharos' | 'enrichment' | None
- answer_compare(result, graph, labels=None) -> the compare block of a graph answer (see ANSWER BLOCK below)
- card_compare(card, graph=None) -> the compare block of an entity card (see CARD BLOCK below)
- mark_picture(picture, sides, pair_sides=None, prediction_side=None, center=None) -> the picture with marks on nodes and edges
- question_steps(cypher, columns), row_in_pharos(cells, steps, edges) -> which links must meet, and whether Pharos links do
- answer_graph(rows_cells, edges, graph) -> the drawable steps of the top rows; pharos_alone_view(...) -> the Pharos-alone rows and bottom line
- compare_sentence(...) / card_sentence(...) -> the one sentence, computed from the counts
- cut_headline(headline, n_kept, n_shown) -> the Pharos-alone bottom line of a cut answer ("N of the M rows shown")
- limit_sentences(...) -> what a comparison of a very large answer left out

ANSWER BLOCK (answer_compare)
  applicable            False when the payload is not a graph answer (a trial report): nothing else is filled in
  rows                  number of answer rows
  pharos_only, enrichment_only, both, unknown      rows by the side of their cited links
  in_pharos_alone       rows that stay when the enrichment is taken away
  missing_from_pharos_alone   rows that exist only because of the enrichment (= rows - in_pharos_alone - undetermined)
  undetermined          rows that could not be decided (a cited link was not found)
  links                 {pharos, enrichment, unknown}: distinct cited links by side
  limits                {rows_total, rows_compared, links_total, links_looked_up}: an answer above MAX_ROWS rows or MAX_LINKS
                        cited links is compared in part; every count above is of the rows compared, and `sentences` says so
  added_by              the sources of the enrichment links (source names, as the rows show them)
  sentence              "12 of 50 rows exist only because of the enrichment."
  sentences             the sentence plus, when it applies, what the enrichment adds to rows that stay, and what is undetermined
  marks                 one entry per row, in row order: {row (1-based), support, in_pharos_alone, links {pharos, enrichment,
                        unknown}, added_by, pharos_sources}
  graph                 {nodes, edges, rows_drawn, rows_total}: the steps of the top rows, drawable by the page's graph code
                        (same keys as a card picture); every node and edge carries side / support / in_pharos_alone
  pharos_alone          {columns, rows: [{row: number in the full answer, values: the row as Pharos alone gives it}],
                        evidence, ordering, summary}: summary is answer_summary.pharos_summary run on exactly these rows, so
                        the toggle changes the bottom line as well as the table. Row numbers inside that summary count the
                        rows of THIS view (1..n); `row` maps each back to the full answer.

CARD BLOCK (card_compare)
  rows, pharos_only, enrichment_only, both, unknown    the links shown on the card, by the sides that state the same pair
                        (subject, object) anywhere in the graph: 'enrichment_only' = Pharos does not hold the pair at all
  hidden_in_pharos_alone  links shown that are themselves enrichment links (they go when the toggle is on Pharos alone)
  sentence, sentences
  sections              per evidence kind: {kind, rows, pharos_only, enrichment_only, both, unknown, support, sentence,
                        marks: {link id: {side, support, in_pharos_alone}}, pharos_alone: {rows, kept, pruned}}
  predicted             {n, in_pharos_alone, sentence}: the predicted links come from a model run on one graph
  picture               the card's picture with the same marks on every node and edge
  pharos_alone          {summary: {known_shown, kinds, kept, pruned, predicted}, sentence}

Router (make_router(host) -> fastapi.APIRouter; host = the app module, which supplies the graph and the existing answers)
- POST /api/pharos/compare/answer     {"result": <a graph answer the page holds>} -> its compare block
- GET  /api/pharos/compare/demo/{id}  one tested question -> {id, question, compare}
- GET  /api/pharos/compare/card/{id}  one entity card -> {id, label, compare}
All read-only. The page part (the toggle) is a separate change.
"""
import json
import re

try:                                    # run from pipelines/ (the app, the tests)
    import answer_summary
    import make_graphs
    import personas
except ImportError:                     # imported as pipelines.routes_compare
    from pipelines import answer_summary, make_graphs, personas

PHAROS, ENRICHMENT, PREDICTED = "pharos", "enrichment", "predicted"
PHAROS_ONLY, ENRICHMENT_ONLY, BOTH, UNKNOWN = "pharos_only", "enrichment_only", "both", "unknown"
MAX_ROWS = 2000               # rows of one answer that are marked (the querier's own limit is 500)
MAX_LINKS = 20_000            # cited links looked up for one answer
MAX_STEPS = 5000              # link combinations tried per row before the simpler per-step rule is used
MAX_GRAPH_NODES = 40          # nodes drawn for an answer (the top rows first)
PHAROS_RECIPE = "G0"          # the graph recipe that is Pharos alone (make_graphs.RECIPES)


# ------------------------------------------------------------------ sides
def pharos_layers():
    """The layer names that make up "Pharos alone", read from the graph recipes (make_graphs.RECIPES). Returns a set."""
    return set(make_graphs.RECIPES[PHAROS_RECIPE]["layers"])


def side_of(layer):
    """Takes an edge's layer name. Returns 'pharos' for a Pharos layer, 'enrichment' for any other layer, None when the
    edge names no layer (the side is then not known)."""
    layer = str(layer or "")
    if not layer:
        return None
    return PHAROS if layer in pharos_layers() else ENRICHMENT


def support_of(sides):
    """Takes the sides of a row's cited links (any iterable of 'pharos' / 'enrichment' / None). Returns the row's mark:
    'pharos_only', 'enrichment_only', 'both', or 'unknown' when no side is known."""
    known = {s for s in sides if s in (PHAROS, ENRICHMENT)}
    if known == {PHAROS}:
        return PHAROS_ONLY
    if known == {ENRICHMENT}:
        return ENRICHMENT_ONLY
    return BOTH if known else UNKNOWN


def prediction_in_pharos(graph_code):
    """Takes the graph a prediction was made on (the `graph` field of a predicted link). Returns True when that graph is built
    from the Pharos layers only, False when it includes enrichment layers, None when the code is missing or not a recipe."""
    recipe = make_graphs.RECIPES.get(str(graph_code or ""))
    if recipe is None:
        return None
    return set(recipe["layers"]) <= pharos_layers()


def _plural(n, word, many=None):
    return f"{n:,} {word if n == 1 else (many or word + 's')}"


def _lookup(graph, edge_ids, limit=MAX_LINKS):
    """Takes the graph that answered (anything with .edges[id]) and edge ids. Returns {id: edge dict} for the ids found, at
    most `limit` looked up; a failing lookup leaves the id out (its side is then unknown)."""
    out = {}
    if graph is None or not hasattr(graph, "edges"):
        return out
    for eid in list(dict.fromkeys(edge_ids))[:limit]:
        try:
            if eid in graph.edges:
                out[eid] = graph.edges[eid]
        except Exception:                               # a lookup must never break an answer
            continue
    return out


def _cells(row, columns):
    """Takes an answer row and the columns. Returns {column: [edge ids]} for the columns in which the row cites links (a merged
    row holds a list in a column)."""
    out = {}
    for c in columns:
        v = row.get(c)
        ids = [x for x in (v if isinstance(v, list) else [v]) if isinstance(x, str) and answer_summary.EDGE_ID.match(x)]
        if ids:
            out[c] = ids
    return out


# ------------------------------------------------------------------ the steps of the question (which links must meet)
def question_steps(cypher, columns):
    """Takes the query that ran and the answer's columns. Returns (steps, link_columns): steps = [(column holding the link ids
    of this step, variable of the node on its left, variable on its right, direction 'out' | 'in' | 'both')], one per
    relationship of the query; link_columns = {column: (the step's id column, the link property it returns)} for the columns
    that return a property of a link (r.source AS source). (None, {}) when the query cannot be read, or when a relationship
    does not return its id: the per-column rule is used then."""
    try:
        try:
            import querier
        except ImportError:
            from pipelines import querier
        q = querier.parse(str(cypher or ""))
    except Exception:
        return None, {}
    rvars = {r.var for r in q.rels}
    id_col, link_cols = {}, {}
    for expr, alias in q.items:
        if not (isinstance(expr, tuple) and len(expr) >= 3 and expr[0] == "prop" and expr[1] in rvars):
            continue
        if expr[2] == "id":
            id_col.setdefault(expr[1], alias)
    for expr, alias in q.items:
        if isinstance(expr, tuple) and len(expr) >= 3 and expr[0] == "prop" and expr[1] in id_col and expr[2] != "id":
            link_cols[alias] = (id_col[expr[1]], expr[2])
    if not q.rels or set(id_col) != rvars or any(c not in columns for c in id_col.values()):
        return None, {}
    return [(id_col[r.var], q.nodes[r.left].var, q.nodes[r.right].var, r.direction) for r in q.rels], link_cols


class _TooMany(Exception):
    pass


def _assignments(steps, cands, cap=MAX_STEPS):
    """Takes the steps (question_steps) and {column: [edge dicts]} (the links allowed for each step). Yields {column: edge id}
    for every choice of one link per step in which the links meet at the same nodes (a node variable shared by two steps is
    the same node in both). A link without subject / object (not found in the graph) fits anywhere. Raises _TooMany after
    `cap` tries."""
    tried = [0]

    def rec(i, bind, chosen):
        if i == len(steps):
            yield dict(chosen)
            return
        col, lv, rv, direction = steps[i]
        for e in cands.get(col, ()):
            s, o = e.get("subject"), e.get("object")
            ways = [(s, o)] if direction == "out" else [(o, s)] if direction == "in" else [(s, o), (o, s)]
            if s is None or o is None or s == o:
                ways = ways[:1]
            for left, right in ways:
                tried[0] += 1
                if tried[0] > cap:
                    raise _TooMany()
                nb = bind
                if left is not None and right is not None:
                    if bind.get(lv, left) != left or bind.get(rv, right) != right or (lv == rv and left != right):
                        continue
                    nb = dict(bind)
                    nb[lv], nb[rv] = left, right
                chosen[col] = e["id"]
                yield from rec(i + 1, nb, chosen)
                del chosen[col]
    yield from rec(0, {}, {})


def _free_steps(cells):
    """The steps when the query cannot be read: one step per link column, no shared nodes (any combination fits)."""
    return [(c, f"_l{i}", f"_r{i}", "out") for i, c in enumerate(cells)]


def row_in_pharos(cells, steps, edges):
    """Takes one row's cited links {column: [ids]}, the question's steps (or None) and {id: edge dict} of the links found.
    Returns (in_pharos_alone, usable, combos): in_pharos_alone = True when Pharos links alone still give the row, False when
    they do not, None when that depends on a link that was not found; usable = {column: [the Pharos link ids that take part in
    such a row]}; combos = the first combinations (at most 200) of one Pharos link per step, for the evidence order."""
    def cand(sides):
        return {c: [edges.get(i) or {"id": i} for i in ids if side_of((edges.get(i) or {}).get("layer")) in sides]
                for c, ids in cells.items()}
    if not cells:                                        # the row cites no link: nothing to decide from
        return None, {}, []
    use = steps if steps and {c for c, *_ in steps} == set(cells) else None
    if use is not None:
        try:                                             # the pattern must fit the row as it stands, else it is not used
            if next(_assignments(use, cand({PHAROS, ENRICHMENT, None})), None) is None:
                use = None
        except _TooMany:
            use = None
    if use is None:
        use = _free_steps(cells)
    pharos = cand({PHAROS})
    combos, usable = [], {c: [] for c in cells}
    try:
        for a in _assignments(use, pharos):
            if len(combos) < 200:
                combos.append(a)
            for c, eid in a.items():
                if eid not in usable[c]:
                    usable[c].append(eid)
    except _TooMany:                                     # too many combinations: one Pharos link per step is enough
        ok = all(pharos[c] for c in cells)
        usable = {c: [e["id"] for e in pharos[c]] for c in cells} if ok else {c: [] for c in cells}
        combos = [{c: pharos[c][0]["id"] for c in cells}] if ok else []
    if combos:
        return True, {c: [i for i in cells[c] if i in usable[c]] for c in cells}, combos
    try:
        maybe = next(_assignments(use, cand({PHAROS, None})), None) is not None
    except _TooMany:
        maybe = all(cand({PHAROS, None})[c] for c in cells)
    has_unknown = any(i not in edges or side_of(edges[i].get("layer")) is None for ids in cells.values() for i in ids)
    return (None if maybe and has_unknown else False), {c: [] for c in cells}, []


# ------------------------------------------------------------------ the sentence
def compare_sentence(rows, missing, gains=0, undetermined=0, enrichment_links=0, unit="row", not_looked_up=0):
    """Takes the number of rows, how many exist only because of the enrichment, how many stay in Pharos alone but cite
    enrichment links as well (gains), how many are undetermined, the number of enrichment links cited, and how many cited
    links were left out of the lookup because the answer cites more than MAX_LINKS (not_looked_up). Returns
    [sentence, more sentences...]: the first is the one sentence the answer shows, the others apply only when their count is
    not zero. Every word is chosen from the counts."""
    many = unit + "s"
    if not rows:
        return [f"There are no {many} to compare."]
    decided = rows - undetermined
    if not decided:
        first = (f"The links behind {'this ' + unit if rows == 1 else f'these {rows:,} {many}'} could not be looked up, so the "
                 "comparison with Pharos alone is not available.")
        return [first]
    why = "was not found in the graph or was not looked up" if not_looked_up else "was not found in the graph"
    if missing == 0 and not enrichment_links:
        first = (f"{'The' if decided == 1 else 'All'} {_plural(decided, unit)} rest{'s' if decided == 1 else ''} on Pharos links "
                 "alone; the enrichment adds nothing to this answer.")
    elif missing == 0:
        first = (f"{'The' if decided == 1 else 'All'} {_plural(decided, unit)} {'is' if decided == 1 else 'are'} already in "
                 "Pharos alone.")
    elif missing == decided and decided == 1:
        first = f"The 1 {unit} exists only because of the enrichment; Pharos alone does not return it."
    elif missing == decided:
        first = f"All {decided:,} {many} exist only because of the enrichment; Pharos alone returns none of them."
    else:
        first = f"{missing:,} of {decided:,} {many} exist{'s' if missing == 1 else ''} only because of the enrichment."
    out = [first]
    if gains:
        out.append(f"The enrichment adds supporting links to {_plural(gains, unit)} that Pharos alone already has.")
    if undetermined:
        out.append(f"{_plural(undetermined, unit)} could not be decided, because a link {'it cites' if undetermined == 1 else 'they cite'} "
                   f"{why}.")
    return out


# ------------------------------------------------------------------ the picture (graph payload) with marks
def mark_picture(picture, sides, pair_sides=None, prediction_side=None, center=None):
    """Takes a picture {nodes: [{id, ...}], edges: [{id, source, target, predicted?, ...}]}, {edge id: side} for its links,
    optionally {edge id: sides of every link stating the same pair} (default: the links drawn between the same two nodes),
    whether predicted links belong to Pharos alone (True / False / None), and the id of a node that is always shown.
    Returns a copy in which every edge has side ('pharos' | 'enrichment' | 'predicted' | None), support and in_pharos_alone,
    and every node has support (from the links drawn at it) and in_pharos_alone (False = hide it with the enrichment)."""
    edges = [dict(e) for e in (picture or {}).get("edges") or []]
    drawn = {}
    for e in edges:
        if not e.get("predicted"):
            drawn.setdefault(frozenset((e.get("source"), e.get("target"))), set()).add(sides.get(e.get("id")))
    at_node = {}
    for e in edges:
        if e.get("predicted"):
            e.update(side=PREDICTED, support=PREDICTED, in_pharos_alone=prediction_side)
        else:
            side = sides.get(e.get("id"))
            pair = (pair_sides or {}).get(e.get("id")) or drawn[frozenset((e.get("source"), e.get("target")))]
            e.update(side=side, support=support_of(set(pair) | {side}),
                     in_pharos_alone=True if side == PHAROS else False if side == ENRICHMENT else None)
        for n in (e.get("source"), e.get("target")):
            at_node.setdefault(n, []).append(e)
    nodes = []
    for n in (picture or {}).get("nodes") or []:
        n = dict(n)
        es = at_node.get(n.get("id"), [])
        known = [e["side"] for e in es if e["side"] in (PHAROS, ENRICHMENT)]
        n["support"] = support_of(known)
        if n.get("id") == center or any(e["in_pharos_alone"] is True for e in es):
            n["in_pharos_alone"] = True
        elif es and all(e["in_pharos_alone"] is False for e in es):
            n["in_pharos_alone"] = False
        else:
            n["in_pharos_alone"] = None if es else True
        nodes.append(n)
    return dict(picture or {}, nodes=nodes, edges=edges)


def answer_graph(rows_cells, edges, graph, max_nodes=MAX_GRAPH_NODES):
    """Takes the rows' cited links [{column: [ids]}] in row order, {id: edge dict}, the graph (for labels, categories and
    verdicts) and the node cap. Returns {nodes, edges, rows_drawn, rows_total} with the keys of a card picture (edge: id,
    source, target, predicate, evidence_type, predicted, word, rows = the row numbers citing it), unmarked: the links of the
    top rows, whole rows only, until the next row would pass the cap (the first row is always drawn)."""
    label = (lambda n: graph.label(n)) if hasattr(graph, "label") else (lambda n: n)
    nodes, out, drawn = {}, {}, 0
    for i, cells in enumerate(rows_cells, 1):
        es = [edges[x] for ids in cells.values() for x in ids if x in edges and edges[x].get("subject") and edges[x].get("object")]
        new = {n for e in es for n in (e["subject"], e["object"])} - set(nodes)
        if drawn and len(nodes) + len(new) > max_nodes:
            break
        drawn = i
        for e in es:
            for n in (e["subject"], e["object"]):
                if n not in nodes:
                    try:
                        cat = (graph.nodes.get(n) or {}).get("category", "") if hasattr(graph, "nodes") else ""
                    except Exception:
                        cat = ""
                    nodes[n] = dict(id=n, label=label(n), category=cat)
            if e["id"] not in out:
                v = graph.verdict(e["id"]) if hasattr(graph, "verdict") else None
                out[e["id"]] = dict(id=e["id"], source=e["subject"], target=e["object"], predicate=e.get("predicate"),
                                    evidence_type=personas.evidence_kind(e), predicted=False,
                                    word=personas.confidence_word((v or {}).get("verdict"), (v or {}).get("confidence")), rows=[])
            if i not in out[e["id"]]["rows"]:
                out[e["id"]]["rows"].append(i)
    return dict(nodes=list(nodes.values()), edges=list(out.values()), rows_drawn=drawn, rows_total=len(rows_cells))


# ------------------------------------------------------------------ answers
class _PharosView:
    """The graph as Pharos alone sees it, for the evidence order only: same_pair keeps the Pharos links, verdict passes through."""

    def __init__(self, graph):
        if hasattr(graph, "same_pair"):
            self.same_pair = lambda s, o: [e for e in graph.same_pair(s, o) if side_of(e.get("layer")) == PHAROS]
        if hasattr(graph, "verdict"):
            self.verdict = graph.verdict


def _same(a, b):
    """True when a returned cell value equals a link's own value (the graph holds text; the answer may hold a number)."""
    if a is None or b is None:
        return a is None and b in (None, "")
    if str(a) == str(b):
        return True
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return False


def _pharos_row(row, columns, cells, usable, link_cols, edges):
    """Takes a row that stays in Pharos alone, its link cells, the Pharos link ids that take part (usable) and the columns that
    return a link property. Returns the row as Pharos alone gives it: each link cell keeps its usable ids; a merged cell (a
    list) of a link property keeps the values the kept links have; other cells are unchanged."""
    out = dict(row)
    for c, ids in usable.items():
        out[c] = ids[0] if len(ids) == 1 else ids
    kept_all = [edges[i] for ids in usable.values() for i in ids if i in edges]
    for c in columns:
        v = row.get(c)
        if c in cells or not isinstance(v, list):
            continue
        if c in link_cols and link_cols[c][0] in usable:
            have = [edges[i].get(link_cols[c][1]) for i in usable[link_cols[c][0]] if i in edges]
        elif c == "source" or c.endswith("_source"):
            have = [e.get("source") for e in kept_all]
        elif c.endswith("knowledge_level") or c.endswith("_level"):
            have = [e.get("knowledge_level") for e in kept_all]
        else:
            continue
        vals = [x for x in v if any(_same(x, h) for h in have)]
        out[c] = vals[0] if len(vals) == 1 else vals if vals else None
    return out


def _cut(result):
    """How many rows the full answer was cut at, or None: its evidence ordering says more answers exist, or the query's LIMIT
    was reached."""
    rows = result.get("rows") or []
    o = result.get("ordering") or {}
    if o.get("n_more"):
        return len(rows)
    m = re.search(r"\bLIMIT\s+(\d+)\s*$", str(result.get("cypher") or ""), re.I)
    return len(rows) if m and len(rows) >= int(m.group(1)) else None


SUMMARY_NOT_AVAILABLE = "The bottom line for Pharos alone is not available for this answer."
_IN_ALL = re.compile(r"; [\d,]+ [^;]*? in all(?=[,.])")


def cut_headline(headline, n_kept, n_shown):
    """Takes the bottom line computed on the Pharos-alone rows, the number of those rows and the number of rows the cut full
    answer shows. Returns the headline with its total ("; 50 diseases in all") replaced by "; 50 of the 50 rows shown": the
    view filters the rows shown, so it does not know how many answers Pharos alone holds in all. A headline without that
    total is returned unchanged."""
    return _IN_ALL.sub(f"; {n_kept:,} of the {_plural(n_shown, 'row')} shown", str(headline or ""), count=1)


def limit_sentences(rows_total, rows_compared, links_total, links_looked_up):
    """Takes the rows an answer holds and how many were compared, the distinct links those rows cite and how many were looked
    up. Returns the sentences that say what the comparison left out (an empty list when nothing was left out)."""
    out = []
    if rows_total > rows_compared:
        out.append(f"This answer has {_plural(rows_total, 'row')}; the comparison covers the first {rows_compared:,} only.")
    if links_total > links_looked_up:
        out.append(f"The rows compared cite {_plural(links_total, 'link')}; the first {links_looked_up:,} were looked up, and a "
                   "row that rests on one of the others is counted as not decided.")
    return out


def pharos_alone_view(result, kept, edges, graph, labels=None):
    """Takes the full answer, kept = [(row number in the full answer, the row as Pharos alone gives it, its combinations of
    Pharos links)], {id: edge dict}, the graph and {entity id: label}. Returns the Pharos-alone view {columns, rows: [{row,
    values}], evidence, ordering, summary}. When the full answer is ordered by evidence the kept rows are ordered again by the
    evidence of their Pharos links (querier.evidence_key, the rule the full answer used). summary = the existing bottom-line
    code (answer_summary.pharos_summary) run on these rows with the facts of their links; an empty view says why. When the full answer was cut (_cut), the headline's
    total is reworded by cut_headline. A failing summary gives {error: SUMMARY_NOT_AVAILABLE}, never the exception's text."""
    columns = result.get("columns") or []
    n_full = len(result.get("rows") or [])
    ordering_full = result.get("ordering") or {}
    by_evidence = ordering_full.get("by") == "evidence"
    if by_evidence and graph is None:                    # no graph to rank with: the order of the full answer is kept
        by_evidence = False
    if by_evidence and kept:
        try:
            try:
                import querier
            except ImportError:
                from pipelines import querier
            view = _PharosView(graph) if ordering_full.get("full", True) else None
            cache = {}
            key = lambda combos: min(querier.evidence_key([edges[i] for i in a.values() if i in edges], view, cache)
                                     for a in combos)
            kept = [k for _, k in sorted(enumerate(kept), key=lambda ik: (key(ik[1][2]), ik[0]))]
        except Exception:                                # the order of the full answer is kept
            by_evidence = False
    rows = [values for _, values, _ in kept]
    evidence = sorted({i for values in rows for ids in _cells(values, columns).values() for i in ids})
    res = {k: v for k, v in result.items() if k not in ("summary", "compare", "passed", "note", "why", "ordering", "trials_panel")}
    res.update(rows=rows, evidence=evidence)
    ordering = None
    if by_evidence:
        ordering = dict(by="evidence", n_total=len(rows), n_shown=len(rows), n_more=0, n_more_text_mined=0, collapsed=0,
                        full=bool(ordering_full.get("full", True)),
                        sentence=f"The {_plural(len(rows), 'row')} of the full answer that Pharos alone gives, strongest evidence first.")
        res["ordering"] = ordering
    if not rows and n_full:
        res["note"] = (f"none of the {_plural(n_full, 'row')} of the full answer rests on Pharos links alone; "
                       f"{'it needs' if n_full == 1 else 'every one needs'} a link the enrichment added")
    facts = answer_summary.edge_facts(graph, evidence, limit=max(300, len(evidence))) if graph is not None else {}
    try:
        summary = dict(answer_summary.pharos_summary(res, facts, labels or {}))
    except Exception:                                    # a summary failure never breaks the comparison; the error's own
        summary = dict(error=SUMMARY_NOT_AVAILABLE)      # text (it can hold a file path) is not put into the answer
    if "error" not in summary:
        summary["view"] = "pharos_alone"
        if not rows and n_full:
            summary["headline"] = "Pharos alone returns no rows for this question."
            summary["what_would_change"] = ["A Pharos link for the steps that only the enrichment covers would bring rows back."]
            summary["next_step"] = "Switch back to the agent-built graph to see the rows the enrichment adds."
        cut = _cut(result)
        if cut:
            summary["headline"] = cut_headline(summary.get("headline"), len(rows), cut)
            summary["caveats"] = list(summary.get("caveats") or []) + [
                f"This view keeps the rows of the full answer that Pharos alone supports. The full answer stops at "
                f"{_plural(cut, 'row')}, so Pharos alone may hold further rows that are not shown."]
    return dict(columns=columns, rows=[dict(row=num, values=values) for num, values, _ in kept], evidence=evidence,
                ordering=ordering, summary=summary)


def answer_compare(result, graph, labels=None):
    """Takes a graph answer (keys columns, rows, cypher, entities, ordering ...; the payload of the ask endpoint, a tested
    question or a stored answer), the graph that answered and optionally {entity id: label}. Returns the ANSWER BLOCK of the
    module docstring. A payload that is not a graph answer (a trial report, no columns) gives applicable=False."""
    result = result if isinstance(result, dict) else {}
    columns = [c for c in (result.get("columns") or []) if isinstance(c, str)]
    all_rows = [r for r in (result.get("rows") or []) if isinstance(r, dict)]
    rows = all_rows[:MAX_ROWS]
    if not columns and "sections" in result:
        return dict(applicable=False, rows=0, sentence="This answer comes from the trial layer, which is not part of the "
                                                       "comparison with Pharos alone.")
    cells = [_cells(r, columns) for r in rows]
    cited = list(dict.fromkeys(i for c in cells for ids in c.values() for i in ids))
    edges = _lookup(graph, cited, MAX_LINKS)
    limits = dict(rows_total=len(all_rows), rows_compared=len(rows), links_total=len(cited),
                  links_looked_up=min(len(cited), MAX_LINKS))
    steps, link_cols = question_steps(result.get("cypher"), columns)
    if labels is None:
        lab = graph.label if hasattr(graph, "label") else (lambda n: n)
        labels = {e: lab(e) for e in (result.get("entities") or []) if isinstance(e, str)}
    marks, kept = [], []
    counts = {PHAROS_ONLY: 0, ENRICHMENT_ONLY: 0, BOTH: 0, UNKNOWN: 0}
    link_side = {}
    for num, (row, c) in enumerate(zip(rows, cells), 1):
        ids = list(dict.fromkeys(i for v in c.values() for i in v))
        sides = {i: side_of((edges.get(i) or {}).get("layer")) for i in ids}
        link_side.update(sides)
        support = support_of(sides.values())
        counts[support] += 1
        stays, usable, combos = row_in_pharos(c, steps, edges)
        n_side = lambda s: sum(1 for i in ids if sides[i] == s)
        src = lambda s: list(dict.fromkeys(str(edges[i].get("source") or edges[i].get("primary_knowledge_source"))
                                           for i in ids if sides[i] == s and
                                           (edges[i].get("source") or edges[i].get("primary_knowledge_source"))))
        marks.append(dict(row=num, support=support, in_pharos_alone=stays,
                          links={PHAROS: n_side(PHAROS), ENRICHMENT: n_side(ENRICHMENT), UNKNOWN: n_side(None)},
                          added_by=src(ENRICHMENT), pharos_sources=src(PHAROS)))
        if stays:
            kept.append((num, _pharos_row(row, columns, c, usable, link_cols, edges), combos))
    n = len(rows)
    undetermined = sum(1 for m in marks if m["in_pharos_alone"] is None)
    missing = sum(1 for m in marks if m["in_pharos_alone"] is False)
    gains = sum(1 for m in marks if m["in_pharos_alone"] and m["links"][ENRICHMENT])
    links = {s: sum(1 for v in link_side.values() if v == k) for s, k in ((PHAROS, PHAROS), (ENRICHMENT, ENRICHMENT), (UNKNOWN, None))}
    sentences = compare_sentence(n, missing, gains, undetermined, links[ENRICHMENT],
                                 not_looked_up=limits["links_total"] - limits["links_looked_up"])
    sentences += limit_sentences(**limits)
    picture = answer_graph(cells, edges, graph)
    picture = dict(mark_picture(picture, {e["id"]: link_side.get(e["id"]) for e in picture["edges"]}),
                   rows_drawn=picture["rows_drawn"], rows_total=picture["rows_total"])
    for node in picture["nodes"]:                        # an entity the question names is drawn in both views
        if node["id"] in (result.get("entities") or []):
            node["in_pharos_alone"] = True
    return dict(applicable=True, rows=n, pharos_only=counts[PHAROS_ONLY], enrichment_only=counts[ENRICHMENT_ONLY],
                both=counts[BOTH], unknown=counts[UNKNOWN], in_pharos_alone=len(kept), missing_from_pharos_alone=missing,
                undetermined=undetermined, links=links, limits=limits,
                added_by=list(dict.fromkeys(s for m in marks for s in m["added_by"])),
                sentence=sentences[0], sentences=sentences, marks=marks, graph=picture,
                pharos_alone=pharos_alone_view(dict(result, rows=rows, columns=columns), kept, edges, graph, labels))


# ------------------------------------------------------------------ entity cards
def card_sentence(rows, new, restated, unknown=0):
    """Takes the number of links shown, how many state a pair Pharos does not hold (new), how many are enrichment links for a
    pair Pharos holds too (restated) and how many have no known side. Returns [sentence, more sentences...] from the counts."""
    if not rows:
        return ["There are no links to compare."]
    known = rows - unknown
    if not known:
        return [f"The side of the {_plural(rows, 'link')} shown could not be looked up, so the comparison with Pharos alone "
                "is not available."]
    if not new and not restated:
        first = (f"The 1 link shown is a Pharos link; the enrichment adds none here." if known == 1 else
                 f"All {known:,} links shown are Pharos links; the enrichment adds none here.")
    elif not new:
        first = f"Every pair behind the {_plural(known, 'link')} shown is already in Pharos alone."
    elif new == known:
        first = (f"{'The' if known == 1 else 'All'} {_plural(known, 'link')} shown exist{'s' if known == 1 else ''} only because of "
                 "the enrichment.")
    else:
        first = f"{new:,} of the {known:,} links shown exist{'s' if new == 1 else ''} only because of the enrichment."
    out = [first]
    if restated:
        out.append(f"{_plural(restated, 'more link')} shown {'comes' if restated == 1 else 'come'} from the enrichment and "
                   f"restate{'s' if restated == 1 else ''} a pair Pharos already holds.")
    if unknown:
        out.append(f"The side of {_plural(unknown, 'link')} shown is not known.")
    return out


def card_compare(card, graph=None):
    """Takes an entity card (pharos_app.assemble_card / split_predictions: about, known = sections by evidence kind with rows
    that carry id, subject, object, layer, word; predicted; picture) and optionally the graph. Returns the CARD BLOCK of the
    module docstring. A row's own side comes from its layer; its support from every link that states the same (subject,
    object) pair (graph.same_pair when the graph has it, else the rows on the card), so 'enrichment_only' means Pharos does not
    hold the pair at all."""
    card = card if isinstance(card, dict) else {}
    known = [k for k in (card.get("known") or []) if isinstance(k, dict)]
    all_rows = [r for k in known for r in (k.get("rows") or [])]
    on_card = {}
    for r in all_rows:
        on_card.setdefault((r.get("subject"), r.get("object")), set()).add(side_of(r.get("layer")))
    cache = {}

    def pair_sides(r):
        key = (r.get("subject"), r.get("object"))
        if key not in cache:
            sides = set(on_card.get(key, ()))
            if graph is not None and hasattr(graph, "same_pair") and key[0] and key[1]:
                try:
                    sides |= {side_of(e.get("layer")) for e in graph.same_pair(key[0], key[1])}
                except Exception:                        # a lookup must never break a card
                    pass
            cache[key] = sides
        return cache[key]

    sections, sides_by_id, pairs_by_id = [], {}, {}
    total = {PHAROS_ONLY: 0, ENRICHMENT_ONLY: 0, BOTH: 0, UNKNOWN: 0}
    hidden = restated_all = 0
    for k in known:
        rows = k.get("rows") or []
        c = {PHAROS_ONLY: 0, ENRICHMENT_ONLY: 0, BOTH: 0, UNKNOWN: 0}
        marks, restated = {}, 0
        for r in rows:
            side = side_of(r.get("layer"))
            sup = support_of(pair_sides(r) | {side})
            c[sup] += 1
            marks[r.get("id")] = dict(side=side, support=sup,
                                      in_pharos_alone=True if side == PHAROS else False if side == ENRICHMENT else None)
            sides_by_id[r.get("id")], pairs_by_id[r.get("id")] = side, pair_sides(r)
            restated += side == ENRICHMENT and sup == BOTH
        stay = [r for r in rows if side_of(r.get("layer")) == PHAROS]
        gone = sum(1 for r in rows if side_of(r.get("layer")) == ENRICHMENT)
        hidden += gone
        restated_all += restated
        for key in total:
            total[key] += c[key]
        sections.append(dict(kind=k.get("kind"), rows=len(rows), pharos_only=c[PHAROS_ONLY], enrichment_only=c[ENRICHMENT_ONLY],
                             both=c[BOTH], unknown=c[UNKNOWN], hidden_in_pharos_alone=gone,
                             support=support_of(side_of(r.get("layer")) for r in rows),
                             sentence=card_sentence(len(rows), c[ENRICHMENT_ONLY], restated, c[UNKNOWN])[0], marks=marks,
                             pharos_alone=dict(rows=len(stay), kept=sum(r.get("word") == "kept" for r in stay),
                                               pruned=sum(r.get("word") == "pruned" for r in stay))))
    predicted = card.get("predicted") or []
    pred_sides = {prediction_in_pharos(p.get("graph")) for p in predicted}
    pred_in = pred_sides.pop() if len(pred_sides) == 1 else None
    if not predicted:
        pred_sentence = ""
    elif pred_in is False:
        pred_sentence = (f"The {_plural(len(predicted), 'predicted link')} {'comes' if len(predicted) == 1 else 'come'} from the model "
                         "run on the agent-built graph, so " + ("it is" if len(predicted) == 1 else "they are") +
                         " not part of the Pharos-alone view.")
    elif pred_in is True:
        pred_sentence = (f"The {_plural(len(predicted), 'predicted link')} {'comes' if len(predicted) == 1 else 'come'} from the model "
                         "run on Pharos alone.")
    else:
        pred_sentence = (f"Which graph the {_plural(len(predicted), 'predicted link')} {'was' if len(predicted) == 1 else 'were'} "
                         "computed on is not recorded, so the Pharos-alone view leaves " + ("it" if len(predicted) == 1 else "them") + " out.")
    n = len(all_rows)
    sentences = card_sentence(n, total[ENRICHMENT_ONLY], restated_all, total[UNKNOWN])
    stay_sections = [s for s in sections if s["pharos_alone"]["rows"]]
    n_stay = sum(s["pharos_alone"]["rows"] for s in sections)
    kept, pruned = (sum(s["pharos_alone"][w] for s in sections) for w in ("kept", "pruned"))
    alone = dict(known_shown=n_stay, kinds=len(stay_sections), kept=kept, pruned=pruned,
                 predicted=len(predicted) if pred_in is True else 0)
    if not n:
        alone_sentence = "This card shows no links, so there is nothing to compare."
    elif not n_stay:
        alone_sentence = (f"In Pharos alone this card shows none of its {_plural(n, 'link')}: "
                          f"{'it was' if n == 1 else 'all were'} added by the enrichment.")
    else:
        alone_sentence = (f"In Pharos alone this card shows {n_stay:,} of the {_plural(n, 'link')} shown here, in "
                          f"{_plural(len(stay_sections), 'kind')} of evidence ({kept:,} kept, {pruned:,} pruned by the pruning check).")
    about = card.get("about") or {}
    return dict(applicable=True, rows=n, pharos_only=total[PHAROS_ONLY], enrichment_only=total[ENRICHMENT_ONLY],
                both=total[BOTH], unknown=total[UNKNOWN], hidden_in_pharos_alone=hidden, sentence=sentences[0],
                sentences=sentences, sections=sections,
                predicted=dict(n=len(predicted), in_pharos_alone=pred_in if predicted else None, sentence=pred_sentence),
                picture=mark_picture(card.get("picture") or {"nodes": [], "edges": []}, sides_by_id, pairs_by_id,
                                     prediction_side=pred_in if predicted else None, center=about.get("id")),
                pharos_alone=dict(summary=alone, sentence=alone_sentence))


# ------------------------------------------------------------------ the router
def make_router(host):
    """Takes the app module (pipelines/app.py: answering_graph(), api_pharos_demo_one(), api_pharos_card(), pjson()). Returns
    a fastapi.APIRouter with the three read-only compare endpoints of the module docstring. The tested question and the card
    are taken from the app's own endpoints, so the comparison is made on exactly the answer the page shows."""
    from fastapi import APIRouter
    from pydantic import BaseModel

    router = APIRouter()

    class CompareRequest(BaseModel):
        result: dict

    def body(response):
        return json.loads(response.body)

    @router.post("/api/pharos/compare/answer")
    def api_compare_answer(r: CompareRequest):
        """Pharos alone versus the agent-built graph for a graph answer the page already holds (an asked question, a stored
        answer, a tested question). Takes {"result": the answer}; returns its compare block (answer_compare)."""
        return host.pjson(answer_compare(r.result, host.answering_graph()))

    @router.get("/api/pharos/compare/demo/{demo_id}")
    def api_compare_demo(demo_id: str):
        """One tested question: {id, question, compare}. 404 for an unknown id, as the tested-question endpoint."""
        one = body(host.api_pharos_demo_one(demo_id))
        return host.pjson(dict(id=one.get("id"), question=one.get("question"),
                               compare=answer_compare(one, host.answering_graph())))

    @router.get("/api/pharos/compare/card/{node_id}")
    def api_compare_card(node_id: str, n: int = 10):
        """One entity card: {id, label, compare}. 404 for an unknown node, as the card endpoint; n = predicted links shown."""
        card = body(host.api_pharos_card(node_id, n))
        about = card.get("about") or {}
        return host.pjson(dict(id=about.get("id"), label=about.get("label"), compare=card_compare(card, host.answering_graph())))

    return router
