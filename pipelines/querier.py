"""
Querier agent (Nemotron via NIM): plain-English question -> read-only openCypher over the knowledge graph.

    python pipelines/querier.py "What was the primary outcome of TULIP-1?"
    python pipelines/querier.py evaluate          # gold questions -> outputs/eval/querier_<backend>.json

Why a strict subset: a generated query is only trusted if it can be checked before it runs. The subset is
    MATCH <path>[, <path>...] [WHERE <conditions>] RETURN [DISTINCT] <items> [ORDER BY <columns>] LIMIT <n>
with labels / relationship types / properties taken from the graph itself (schema_card), no writes, no
variable-length paths, and at least one returned relationship id (`r.id`) so every answer row cites evidence
(CLAUDE.md rule 10). The same text runs on Amazon Neptune (as written: lowercase relationship types), on Neo4j
(to_neo4j() upper-cases relationship types), and locally on outputs/kg_*.csv (run_local) for tests.

Entity ids are resolved BEFORE the model is called (orchestrator.find_seeds) and handed to it, so the model never
has to invent an NCT / ChEMBL / MONDO id (rule 1).

Functions
- schema_card(kg)                 -> dict(labels={label: [props]}, patterns=[(from, type, to, n)], rel_props=[...])
- parse(cypher)                   -> Query (raises CypherError)
- validate(cypher, card)          -> list of problems ([] = valid)
- run_local(cypher, kg, evidence_first=False) -> dict(columns, rows, evidence[, ordering])
- rank_rows(cypher, result, kg)   -> another database's rows ordered and merged the same way (Neptune answers)
- evidence_key(edges, kg, cache)  -> sort key of one row: weakest knowledge level, fewest corroborating sources, lowest
                                     pruning confidence (CC-31a; LEVEL_RANK)
- to_neo4j(cypher)                -> Neo4j dialect of the same query
- Querier(nim, kg).ask(question)  -> dict(cypher, problems, columns, rows, evidence, attempts, backend, model,
                                     dropped_constraints [{text, attempt}], explanation)
- constraints(cypher) / dropped_constraints(attempts, final) -> conditions as text; those an earlier attempt had (CC-31d)
- evaluate(nim, kg)               -> execution accuracy against data/curated/cypher_gold.csv
"""
import csv, json, os, re, sys, threading, time
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kg_schema import local
from nim import register_offline

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "data" / "curated" / "cypher_gold.csv"
MAX_LIMIT = 500
FORBIDDEN = ["CREATE", "MERGE", "DELETE", "DETACH", "SET", "REMOVE", "DROP", "LOAD", "CALL", "FOREACH", "UNWIND",
             "WITH", "OPTIONAL", "UNION", "EXISTS"]
KEYWORDS = {"MATCH", "WHERE", "RETURN", "DISTINCT", "ORDER", "BY", "ASC", "DESC", "LIMIT", "AND", "OR", "NOT", "IN",
            "CONTAINS", "STARTS", "ENDS", "WITH", "IS", "NULL", "AS", "TRUE", "FALSE"}
FUNCS = {"tolower": str.lower, "toupper": str.upper}
AGGS = {"count", "collect"}


class CypherError(ValueError):
    pass


# ------------------------------------------------------------------ tokenizer + parser
TOKEN = re.compile(r"\s*(?:(?P<str>'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")|(?P<num>-?\d+(?:\.\d+)?)|"
                   r"(?P<id>[A-Za-z_][A-Za-z0-9_]*|`[^`]+`)|(?P<op><>|<=|>=|->|<-|[()\[\]{}:,.\-<>=|*;]))")


def tokenize(text):
    out, pos, text = [], 0, text.strip()
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise CypherError(f"cannot read the query near: {text[pos:pos + 20]!r}")
        pos = m.end()
        kind = m.lastgroup
        v = m.group(kind)
        if kind == "str":
            out.append(("str", bytes(v[1:-1], "utf-8").decode("unicode_escape")))
        elif kind == "num":
            out.append(("num", float(v) if "." in v else int(v)))
        elif kind == "id":
            v = v.strip("`")
            out.append(("kw", v.upper()) if v.upper() in KEYWORDS and not m.group(kind).startswith("`") else ("id", v))
        else:
            out.append(("op", v))
    return out


@dataclass
class Node:
    var: str
    label: str = None
    props: dict = field(default_factory=dict)


@dataclass
class Rel:
    var: str
    types: list
    props: dict
    left: int          # index of node on the left in the flattened node list
    right: int
    direction: str     # 'out' (left->right), 'in' (left<-right), 'both'


@dataclass
class Query:
    nodes: list
    rels: list
    where: object
    distinct: bool
    items: list        # [(expr, alias)]
    order: list        # [(alias, desc)]
    limit: int


def fix_hints(problems):
    """Concrete repair advice for the common validator problems (sent with the problems on a retry)."""
    text, out = " ".join(problems), []
    if "undefined variable" in text:
        out.append("Name every relationship you return, e.g. -[r1:has_intervention]-> ... r1.id AS evidence1.")
    if "expected RETURN" in text:
        out.append("Use ONE MATCH with comma-separated patterns, e.g. MATCH (a)-[r]->(b), (b)-[r2]->(c) RETURN ...")
    if "has no value" in text:
        out.append("Compare a property only with one of the allowed values listed in the problem (copy it exactly), or drop "
                   "that condition and say so in the explanation.")
    if "relationship id as evidence" in text:
        out.append("Return the id of an edge you matched, e.g. MATCH (p)-[r:has_trial]->(t) ... RETURN ..., r.id AS evidence.")
    return " ".join(out) + (" " if out else "")


class _P:
    def __init__(self, toks):
        self.t, self.i, self.anon = toks, 0, 0

    def peek(self, k=0):
        return self.t[self.i + k] if self.i + k < len(self.t) else ("eof", None)

    def take(self, kind=None, val=None):
        tok = self.peek()
        if (kind and tok[0] != kind) or (val is not None and tok[1] != val):
            raise CypherError(f"expected {val or kind}, found {tok[1]!r}")
        self.i += 1
        return tok[1]

    def opt(self, kind, val=None):
        tok = self.peek()
        if tok[0] == kind and (val is None or tok[1] == val):
            self.i += 1
            return True
        return False

    def _anon(self, p):
        self.anon += 1
        return f"_{p}{self.anon}"

    def literal(self):
        tok = self.peek()
        if tok[0] in ("str", "num"):
            self.i += 1
            return ("lit", tok[1])
        if tok == ("kw", "TRUE") or tok == ("kw", "FALSE"):
            self.i += 1
            return ("lit", tok[1] == "TRUE")
        if tok == ("op", "["):
            self.i += 1
            vals = []
            while not self.opt("op", "]"):
                vals.append(self.literal()[1])
                self.opt("op", ",")
            return ("lit", vals)
        raise CypherError(f"expected a value, found {tok[1]!r}")

    def props(self):
        d = {}
        if self.opt("op", "{"):
            while not self.opt("op", "}"):
                k = self.take("id")
                self.take("op", ":")
                d[k] = self.literal()[1]
                self.opt("op", ",")
        return d

    def node(self):
        self.take("op", "(")
        var = self.take("id") if self.peek()[0] == "id" else self._anon("n")
        label = None
        if self.opt("op", ":"):
            label = self.take("id")
            if self.peek() == ("op", ":"):
                raise CypherError("use one label per node")
        n = Node(var, label, self.props())
        self.take("op", ")")
        return n

    def rel_body(self):
        self.take("op", "[")
        var = self.take("id") if self.peek()[0] == "id" else self._anon("r")
        types = []
        if self.opt("op", ":"):
            types.append(self.take("id"))
            while self.opt("op", "|"):
                self.opt("op", ":")
                types.append(self.take("id"))
        if self.peek() == ("op", "*"):
            raise CypherError("variable-length paths (*) are not allowed")
        pr = self.props()
        self.take("op", "]")
        return var, types, pr

    def path(self, nodes, rels):
        nodes.append(self.node())
        while self.peek() in (("op", "-"), ("op", "<-")):
            left_arrow = self.take("op") == "<-"
            var, types, pr = self.rel_body()
            if self.opt("op", "->"):
                if left_arrow:
                    raise CypherError("a relationship cannot point both ways")
                direction = "out"
            else:
                self.take("op", "-")
                direction = "in" if left_arrow else "both"
            left = len(nodes) - 1
            nodes.append(self.node())
            rels.append(Rel(var, types, pr, left, len(nodes) - 1, direction))

    def operand(self):
        tok = self.peek()
        if tok[0] == "id" and self.peek(1) == ("op", "("):
            fn = self.take("id").lower()
            self.take("op", "(")
            if fn in AGGS:
                distinct = self.opt("kw", "DISTINCT")
                arg = ("star",) if self.opt("op", "*") else self.operand()
                self.take("op", ")")
                return ("agg", fn, distinct, arg)
            if fn == "type":
                v = self.take("id")
                self.take("op", ")")
                return ("type", v)
            if fn not in FUNCS:
                raise CypherError(f"function {fn}() is not allowed (allowed: toLower, toUpper, type, count, collect)")
            arg = self.operand()
            self.take("op", ")")
            return ("fn", fn, arg)
        if tok[0] == "id":
            v = self.take("id")
            if self.opt("op", "."):
                return ("prop", v, self.take("id"))
            raise CypherError(f"return or compare properties (e.g. {v}.name), not the whole variable {v}")
        return self.literal()

    def cond(self):
        if self.opt("kw", "NOT"):
            return ("not", self.cond())
        if self.opt("op", "("):
            e = self.expr()
            self.take("op", ")")
            return e
        a = self.operand()
        if self.opt("kw", "IS"):
            neg = self.opt("kw", "NOT")
            self.take("kw", "NULL")
            return ("notnull" if neg else "isnull", a)
        tok = self.peek()
        if tok[0] == "op" and tok[1] in ("=", "<>", "<", ">", "<=", ">="):
            op = self.take("op")
        elif tok == ("kw", "CONTAINS") or tok == ("kw", "IN"):
            op = self.take("kw")
        elif tok in (("kw", "STARTS"), ("kw", "ENDS")):
            op = self.take("kw")
            self.take("kw", "WITH")
        else:
            raise CypherError(f"expected a comparison, found {tok[1]!r}")
        if op == "IN" and self.opt("op", "("):                # SQL-style IN ('a', 'b') = Cypher IN ['a', 'b']
            vals = []
            while not self.opt("op", ")"):
                vals.append(self.literal()[1])
                self.opt("op", ",")
            return ("cmp", op, a, ("lit", vals))
        return ("cmp", op, a, self.operand())

    def conj(self):
        e = self.cond()
        while self.opt("kw", "AND"):
            e = ("and", e, self.cond())
        return e

    def expr(self):
        e = self.conj()
        while self.opt("kw", "OR"):
            e = ("or", e, self.conj())
        return e

    def query(self):
        self.take("kw", "MATCH")
        nodes, rels = [], []
        self.path(nodes, rels)
        while self.opt("op", ","):
            self.path(nodes, rels)
        if self.opt("kw", "MATCH"):
            raise CypherError("use one MATCH with comma-separated paths")
        where = self.expr() if self.opt("kw", "WHERE") else None
        self.take("kw", "RETURN")
        distinct = self.opt("kw", "DISTINCT")
        items = []
        while True:
            start = self.i
            e = self.operand()
            alias = self.take("id") if self.opt("kw", "AS") else _text(self.t[start:self.i])
            items.append((e, alias))
            if not self.opt("op", ","):
                break
        order = []
        if self.opt("kw", "ORDER"):
            self.take("kw", "BY")
            while True:
                start = self.i
                tok = self.peek()
                if tok[0] == "id" and self.peek(1) != ("op", ".") and self.peek(1) != ("op", "("):
                    name = self.take("id")
                else:
                    self.operand()
                    name = _text(self.t[start:self.i])
                desc = self.opt("kw", "DESC")
                if not desc:
                    self.opt("kw", "ASC")
                order.append((name, desc))
                if not self.opt("op", ","):
                    break
        if not self.opt("kw", "LIMIT"):
            raise CypherError("LIMIT is required")
        limit = self.take("num")
        self.opt("op", ";")
        if self.peek()[0] != "eof":
            raise CypherError(f"unexpected text after LIMIT: {self.peek()[1]!r}")
        return Query(nodes, rels, where, distinct, items, order, limit)


def _text(toks):
    return "".join(str(v) if k != "str" else repr(v) for k, v in toks)


def _strip_strings(text):
    return re.sub(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"", "''", text)


def parse(cypher):
    bare = re.sub(r"\b(STARTS|ENDS)\s+WITH\b", "", _strip_strings(cypher), flags=re.I)
    bad = sorted({w.upper() for w in re.findall(r"\b(" + "|".join(FORBIDDEN) + r")\b", bare, re.I)})
    if bad:
        raise CypherError(f"not allowed in the read-only subset: {', '.join(bad)}")
    return _P(tokenize(cypher)).query()


# ------------------------------------------------------------------ schema card + validation
# Plain meaning of each relationship type and of key properties (shown to the model with the schema).
MEANINGS = {
    "has_trial": "program -> its trial(s)",
    "has_intervention": "program -> its investigational drug(s) (curated)",
    "tests_intervention": "trial -> investigational drug it tests (for a drug's ROLE in a trial use tests_intervention / "
                          "has_comparator_drug / has_background_drug, not arm_receives)",
    "has_comparator_drug": "trial -> comparator drug",
    "has_background_drug": "trial -> background (standard-of-care) drug",
    "has_outcome_measure": "trial -> registered outcome / result (OutcomeMeasure)",
    "has_publication": "trial -> publication listed in the registry",
    "has_source_publication": "curated record -> publication it cites",
    "studies_condition": "trial -> disease studied (MeSH or MONDO)",
    "exact_match": "MeSH disease -> equivalent MONDO disease",
    "in_clinical_trials_for": "drug -> disease it was tested for in registry trials (derived; r.nct_ids, "
                              "r.max_research_phase)",
    "interacts_with": "drug -> its molecular TARGET (protein/complex/gene); use for 'target' questions",
    "gene_product_of": "protein -> gene encoding it",
    "has_component_gene": "protein complex -> member gene",
    "has_member_gene": "protein family -> member gene",
    "participates_in": "gene -> Reactome pathway",
    "associated_with": "gene -> disease, Open Targets association (r.ot_score = score 0-1)",
    "has_post_failure_analysis": "program -> curated record of what was analysed/decided AFTER the failure",
    "has_repurposing_pivot": "program -> curated record of a repurposing pivot after the failure",
    "has_data_access_route": "trial -> curated record of how patient-level data can be accessed",
    "has_study_population": "trial -> its enrolled population (StudyPopulation: enrollment, conditions)",
    "has_arm": "trial -> registry arm group (StudyArm: arm_type, dose_text / dose_value / dose_unit when written)",
    "arm_receives": "arm -> drug given in that arm",
    "has_registered_endpoint": "trial -> registered (protocol) endpoint (Endpoint: kind, time_frame)",
    "endpoint_result": "registered endpoint -> its posted result (OutcomeMeasure), identical title",
    "has_adverse_event_report": "trial -> posted adverse-event table (AdverseEventReport: event_type serious/other, "
                                "terms = JSON list of MedDRA term text with per-arm affected / at_risk)",
    "gene_associated_with_condition": "gene -> disease genetic evidence (r.ot_datasource e.g. eva=ClinVar, "
                                      "cancer_gene_census=COSMIC; r.n_pathogenic_or_likely, r.mutated_samples)",
}
PROPERTY_MEANINGS = {
    "ClinicalTrial.ae_groups": "per-arm safety counts as JSON: serious_affected, deaths_affected, *_at_risk "
                               "(a property of the trial: cite the program's has_trial edge r.id as evidence)",
    "ClinicalTrial.why_stopped": "registry text: why the trial stopped",
    "OutcomeMeasure.kind": "registered outcome type",
    "OutcomeMeasure.analyses": "JSON list of statistical analyses: param_value, ci_lower, ci_upper, p_value",
    "OutcomeMeasure.measurements": "JSON list of per-arm results",
    "DevelopmentEvent.pivot_decision": "what was decided after the failure",
    "DevelopmentEvent.outcome_evidence": "what happened next",
}
ENUM_MAX = 12                     # properties with at most this many distinct short values are listed in full
# Relationship properties whose string literals are checked against the graph's own values (CC-31c, review finding M7):
# "not text mining" written as r.primary_knowledge_source <> 'text mining' matched every row, because no edge has that value.
ENUM_REL_PROPS = ("primary_knowledge_source", "knowledge_level", "source", "verdict", "layer")
REL_ENUM_MAX = 300                # a property with more distinct values than this is not treated as enumerated


def add_value_sets(acc, frame, props=ENUM_REL_PROPS):
    """Takes {prop: set} (filled in place) and an edge table (pandas DataFrame or a list of edge dicts). Adds the non-empty
    values of each enumerated relationship property; a property past REL_ENUM_MAX values is marked None (not enumerated)."""
    for prop in props:
        if acc.get(prop, set()) is None:
            continue
        if hasattr(frame, "columns"):
            if prop not in frame.columns:
                continue
            vals = {str(x) for x in frame[prop].dropna().unique() if str(x) != ""}
        else:
            vals = {str(e[prop]) for e in frame if e.get(prop) not in (None, "")}
        if not vals:
            continue
        s = acc.setdefault(prop, set()) | vals
        acc[prop] = None if len(s) > REL_ENUM_MAX else s
    return acc


def finish_value_sets(acc):
    """{prop: set | None} -> {prop: sorted list} for the enumerated properties only (the schema card's rel_enums)."""
    return {k: sorted(v) for k, v in sorted(acc.items()) if v}


def schema_card(kg):
    labels, pats, rprops, values = {}, {}, set(), {}
    for n in kg.nodes.values():
        lab = local(n["category"])
        labels.setdefault(lab, set()).update(k for k in n if k != "category")
        for k, v in n.items():
            if k not in ("id", "category", "name") and isinstance(v, str) and len(v) <= 40 and v[:1] not in "[{":
                values.setdefault((lab, k), set()).add(v)
    for e in kg.edges.values():
        k = (local(kg.nodes[e["subject"]]["category"]), local(e["predicate"]), local(kg.nodes[e["object"]]["category"]))
        pats[k] = pats.get(k, 0) + 1
        rprops.update(x for x in e if x not in ("subject", "object"))
    counts = {}
    for n in kg.nodes.values():
        for k in n:
            counts[(local(n["category"]), k)] = counts.get((local(n["category"]), k), 0) + 1
    # a property is enumerated when every value is short and there are few distinct values
    enums = {f"{l}.{k}": sorted(v) for (l, k), v in sorted(values.items())
             if len(v) <= ENUM_MAX and sum(1 for n in kg.nodes.values() if local(n["category"]) == l and k in n
                                           and isinstance(n[k], str) and len(n[k]) <= 40
                                           and n[k][:1] not in "[{") == counts[(l, k)]}
    return dict(labels={k: sorted(v) for k, v in sorted(labels.items())},
                patterns=[(a, t, b, n) for (a, t, b), n in sorted(pats.items())], rel_props=sorted(rprops),
                enums=enums, rel_enums=finish_value_sets(add_value_sets({}, list(kg.edges.values()))))


def card_text(card):
    lines = ["NODE LABELS (properties):"]
    lines += [f"  {l}: {', '.join(p)}" for l, p in card["labels"].items()]
    lines.append("RELATIONSHIPS (from)-[type]->(to) count  # meaning:")
    lines += [f"  ({a})-[:{t}]->({b})  {n}" + (f"  # {MEANINGS[t]}" if t in MEANINGS else "")
              for a, t, b, n in card["patterns"]]
    lines.append("RELATIONSHIP PROPERTIES: " + ", ".join(card["rel_props"]))
    lines.append("KEY PROPERTIES:")
    lines += [f"  {k}: {v}" for k, v in PROPERTY_MEANINGS.items()]
    lines.append("ALLOWED VALUES (a comparison with any other value matches nothing):")
    lines += [f"  {k}: {', '.join(v)}" for k, v in card.get("enums", {}).items()]
    rel = card.get("rel_enums") or {}
    if rel:
        lines.append("RELATIONSHIP PROPERTY VALUES (r.<property>; a comparison with any other value is rejected):")
        lines += [f"  r.{k}: {', '.join(repr(x) for x in v)}" for k, v in rel.items()]
    if TEXT_LEVEL in (rel.get("knowledge_level") or []):
        lines.append(f"  Text-mined edges have r.knowledge_level = '{TEXT_LEVEL}'; to leave text mining out write "
                     f"r.knowledge_level <> '{TEXT_LEVEL}'.")
    return "\n".join(lines)


def _refs(e, out):
    if isinstance(e, tuple):
        if e[0] == "prop":
            out.append((e[1], e[2]))
        elif e[0] == "type":
            out.append((e[1], None))
        else:
            for x in e[1:]:
                _refs(x, out)
    return out


def validate(cypher, card):
    """-> list of problems; [] means the query is in the subset and only uses names that exist in the graph."""
    try:
        q = parse(cypher)
    except CypherError as err:
        return [str(err)]
    probs = []
    types = {t for _, t, _, _ in card["patterns"]}
    nvars = {}
    for n in q.nodes:
        if n.label and n.label not in card["labels"]:
            probs.append(f"unknown label :{n.label}")
        if nvars.get(n.var, n.label) != n.label and n.label and nvars[n.var]:
            probs.append(f"variable {n.var} has two labels")
        nvars[n.var] = nvars.get(n.var) or n.label
    rvars = {}
    for r in q.rels:
        if r.var in rvars or r.var in nvars:
            probs.append(f"variable {r.var} is used twice")
        rvars[r.var] = r
        probs += [f"unknown relationship type :{t}" for t in r.types if t not in types]
        probs += [f"unknown relationship property {p}" for p in r.props if p not in card["rel_props"]]
    for n in q.nodes:
        allowed = card["labels"].get(nvars[n.var]) if nvars[n.var] else {p for v in card["labels"].values() for p in v}
        probs += [f"unknown property {n.var}.{p}" for p in n.props if allowed is not None and p not in allowed]
    refs = _refs(q.where, []) + [r for e, _ in q.items for r in _refs(e, [])]
    for v, p in refs:
        if v in rvars:
            if p and p not in card["rel_props"]:
                probs.append(f"unknown relationship property {v}.{p}")
        elif v in nvars:
            allowed = card["labels"].get(nvars[v]) if nvars[v] else {x for vs in card["labels"].values() for x in vs}
            if p is None:
                probs.append(f"type() needs a relationship variable, not {v}")
            elif allowed is not None and p not in allowed:
                probs.append(f"unknown property {v}.{p}" + (f" for :{nvars[v]}" if nvars[v] else ""))
        else:
            probs.append(f"undefined variable {v}")
    enums, rel_enums = card.get("enums", {}), card.get("rel_enums") or {}
    for v, p, lit in _literal_comparisons(q):
        if v in nvars:
            allowed = enums.get(f"{nvars.get(v)}.{p}")
        elif v in rvars and p in ENUM_REL_PROPS:
            allowed = rel_enums.get(p)
        else:
            allowed = None
        bad = [x for x in (lit if isinstance(lit, list) else [lit]) if allowed is not None and str(x) not in allowed]
        if bad:
            probs.append(literal_problem(v, p, bad[0], allowed))
    if not any(_is_evidence(e, rvars) for e, _ in q.items):
        probs.append("RETURN must include a relationship id as evidence, e.g. r.id AS evidence")
    aliases = [a for _, a in q.items]
    probs += [f"ORDER BY {a} is not a returned column" for a, _ in q.order if a not in aliases]
    if not isinstance(q.limit, int) or not 0 < q.limit <= MAX_LIMIT:
        probs.append(f"LIMIT must be between 1 and {MAX_LIMIT}")
    return sorted(set(probs), key=probs.index)


def literal_problem(var, prop, value, allowed, show=25):
    """The validator's sentence for a literal the graph does not hold: the allowed values (the first `show`) and, when one
    contains the literal or the other way round (case ignored), "did you mean" those values."""
    low = str(value).lower()
    near = [a for a in allowed if low and (low in a.lower() or a.lower() in low)]
    head = ", ".join(repr(a) for a in allowed[:show]) + (f" ... ({len(allowed)} values)" if len(allowed) > show else "")
    return (f"{var}.{prop} has no value {value!r}" + (f"; did you mean {', '.join(repr(a) for a in near[:3])}?" if near else "")
            + f"; allowed: {head}")


# ------------------------------------------------------------------ constraints kept or dropped between attempts (CC-31d)
def _expr_text(e, names):
    """One WHERE expression as text, each variable shown as its class or relationship type (names: var -> label)."""
    k = e[0]
    if k == "lit":
        return repr(e[1])
    if k == "prop":
        return f"{names.get(e[1], e[1])}.{e[2]}"
    if k == "type":
        return f"type({names.get(e[1], e[1])})"
    if k == "fn":
        return f"{e[1]}({_expr_text(e[2], names)})"
    if k == "agg":
        return f"{e[1]}(...)"
    if k == "not":
        return "NOT " + _expr_text(e[1], names)
    if k in ("and", "or"):
        return f"({_expr_text(e[1], names)} {k.upper()} {_expr_text(e[2], names)})"
    if k == "isnull":
        return _expr_text(e[1], names) + " IS NULL"
    if k == "notnull":
        return _expr_text(e[1], names) + " IS NOT NULL"
    if k == "cmp":
        return f"{_expr_text(e[2], names)} {e[1]} {_expr_text(e[3], names)}"
    return str(e)


def constraints(cypher):
    """Takes a query. Returns its conditions as plain strings, variables replaced by their class or relationship type so
    two attempts can be compared: every inline property ({name:'X'} -> "Gene.name = 'X'"), every relationship type
    ("relationship gene_associated_with_condition") and every top-level AND part of WHERE. [] when the text does not parse."""
    try:
        q = parse(cypher)
    except CypherError:
        return []
    names = {}
    for n in q.nodes:
        if n.label:
            names[n.var] = n.label
    for r in q.rels:
        names[r.var] = "|".join(r.types) or r.var
    out = [f"{names.get(n.var, n.var)}.{k} = {v!r}" for n in q.nodes for k, v in n.props.items()]
    out += [f"{names.get(r.var, r.var)}.{k} = {v!r}" for r in q.rels for k, v in r.props.items()]
    out += [f"relationship {t}" for r in q.rels for t in r.types]
    parts, stack = [], [q.where] if q.where is not None else []
    while stack:
        e = stack.pop()
        if e[0] == "and":
            stack += [e[2], e[1]]
        else:
            parts.append(_expr_text(e, names))
    return list(dict.fromkeys(out + parts))


def dropped_constraints(attempts, final_cypher):
    """Takes the attempts [{cypher, ...}] and the query that ran (or the last one written). Returns [{text, attempt}] for
    each condition an earlier attempt had that the final query does not contain (first attempt that had it), in order."""
    final = set(constraints(final_cypher))
    out, seen = [], set()
    for i, a in enumerate(attempts, 1):
        if a.get("cypher") == final_cypher:
            continue
        for c in constraints(a.get("cypher") or ""):
            if c not in final and c not in seen:
                seen.add(c)
                out.append(dict(text=c, attempt=i))
    return out


def _literal_comparisons(q):
    """(var, prop, literal) for every `var.prop = literal` / `<> literal` / `IN [..]` in WHERE and every inline {prop: literal}
    on a node or a relationship."""
    out = [(n.var, k, v) for n in q.nodes for k, v in n.props.items() if k != "id"]
    out += [(r.var, k, v) for r in q.rels for k, v in r.props.items() if k != "id"]
    def walk(e):
        if not isinstance(e, tuple):
            return
        if e[0] == "cmp" and e[1] in ("=", "<>", "IN"):
            a, b = e[2], e[3]
            for x, y in ((a, b), (b, a)):
                if x[0] == "prop" and y[0] == "lit":
                    out.append((x[1], x[2], y[1]))
        for x in e[1:]:
            walk(x)
    walk(q.where)
    return out


def _is_evidence(e, rvars):
    if e[0] == "agg" and e[1] == "collect":
        e = e[3]
    return e[0] == "prop" and e[1] in rvars and e[2] == "id"


# ------------------------------------------------------------------ local execution (tests / offline)
def _num(v):
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return v
    try:
        return float(v) if re.fullmatch(r"-?\d+(\.\d+)?([eE]-?\d+)?", str(v)) else v
    except ValueError:
        return v


def _get(b, kg, var, prop):
    x = b[var]
    obj = kg.nodes[x] if isinstance(x, str) else x
    if prop == "id":
        return obj["id"]
    v = obj.get(prop)
    if v in ("true", "false"):
        return v == "true"
    return v


def _ev(e, b, kg):
    k = e[0]
    if k == "lit":
        return e[1]
    if k == "prop":
        return _get(b, kg, e[1], e[2])
    if k == "type":
        return local(b[e[1]]["predicate"])
    if k == "fn":
        v = _ev(e[2], b, kg)
        return None if v is None else FUNCS[e[1]](str(v))
    if k == "not":
        v = _ev(e[1], b, kg)
        return None if v is None else not v
    if k == "and":
        return bool(_ev(e[1], b, kg)) and bool(_ev(e[2], b, kg))
    if k == "or":
        return bool(_ev(e[1], b, kg)) or bool(_ev(e[2], b, kg))
    if k == "isnull":
        return _ev(e[1], b, kg) is None
    if k == "notnull":
        return _ev(e[1], b, kg) is not None
    if k == "cmp":
        op, a, c = e[1], _ev(e[2], b, kg), _ev(e[3], b, kg)
        if a is None or c is None:
            return None
        if op == "IN":
            return _num(a) in [_num(x) for x in c] or str(a) in [str(x) for x in c]
        if op in ("CONTAINS", "STARTS", "ENDS"):
            a, c = str(a), str(c)
            return c in a if op == "CONTAINS" else a.startswith(c) if op == "STARTS" else a.endswith(c)
        a, c = _num(a), _num(c)
        if type(a) is not type(c) and not (isinstance(a, (int, float)) and isinstance(c, (int, float))):
            a, c = str(a), str(c)
        return {"=": a == c, "<>": a != c, "<": a < c, ">": a > c, "<=": a <= c, ">=": a >= c}[op]
    raise CypherError(f"cannot evaluate {k}")


class QueryTooBroad(Exception):
    """Raised inside _matches when the work budget (edge checks or seconds) is used up."""


TOO_BROAD = "query too broad for the hosted demo: name a specific gene, disease or compound"
# CC-31e (review finding M7): the guard that decides "too broad" is the work COUNT (link checks, matches held), so the same
# question on the same graph always gets the same answer; the clock is only a server safety net, set well above it.
LIMIT_WORDS = {"steps": ("it needs more than {n:,} link checks", "TMKG_QUERY_MAX_STEPS"),
               "bindings": ("it matches more than {n:,} rows before the row limit", "TMKG_QUERY_MAX_BINDINGS"),
               "seconds": ("it ran past the server's {n:g}-second safety limit, which depends on server load",
                           "TMKG_QUERY_MAX_SECONDS")}


def too_broad_text(kind, limit):
    """Takes the limit that fired ('steps' | 'bindings' | 'seconds') and its value. Returns the problem sentence: TOO_BROAD
    followed by the named limit, e.g. '... (it needs more than 2,000,000 link checks; limit TMKG_QUERY_MAX_STEPS)'."""
    words, env = LIMIT_WORDS[kind]
    return f"{TOO_BROAD} ({words.format(n=limit)}; limit {env})"


class QueryBusy(RuntimeError):
    """Raised when every graph-query slot stayed taken for the whole wait (the app answers 503 'busy, try again')."""


_slots = {"n": None, "sem": None}
_slots_lock = threading.Lock()


@contextmanager
def query_slot(wait=None):
    """Lets at most TMKG_QUERY_CONCURRENCY graph queries search for matches at the same time (unset or 0 = no limit: the
    pipelines and the tests; the hosted image sets 2). A broad query can hold hundreds of MB while it runs, so a few at once
    could use up a small server's memory. Takes the seconds to wait for a free slot (default TMKG_QUERY_WAIT_SECONDS, 20).
    Use as `with query_slot(): ...`; raises QueryBusy when no slot came free in time."""
    n = int(os.environ.get("TMKG_QUERY_CONCURRENCY", "0") or 0)
    if n <= 0:
        yield
        return
    with _slots_lock:
        if _slots["n"] != n:                             # first use, or the setting changed (tests)
            _slots["n"], _slots["sem"] = n, threading.BoundedSemaphore(n)
        sem = _slots["sem"]
    wait = float(os.environ.get("TMKG_QUERY_WAIT_SECONDS", "20") or 20) if wait is None else wait
    if not sem.acquire(timeout=max(0.0, wait)):
        raise QueryBusy("The server is busy with other questions right now. Please try again in a moment.")
    try:
        yield
    finally:
        sem.release()


def _limits(max_steps, max_seconds, max_bindings=None, node_cache=None):
    """-> (max edge/candidate checks, max seconds, max bindings, max cached nodes); None means the env var
    (TMKG_QUERY_MAX_STEPS 2,000,000 / TMKG_QUERY_MAX_SECONDS 60 / TMKG_QUERY_MAX_BINDINGS 100,000 / TMKG_QUERY_NODE_CACHE 100,000),
    0 means no limit (for the cache: no caching). The two counts decide (deterministic); the seconds are a safety net."""
    if max_steps is None:
        max_steps = int(os.environ.get("TMKG_QUERY_MAX_STEPS", 2_000_000))
    if max_seconds is None:
        max_seconds = float(os.environ.get("TMKG_QUERY_MAX_SECONDS", 60))
    if max_bindings is None:
        max_bindings = int(os.environ.get("TMKG_QUERY_MAX_BINDINGS", 100_000))
    if node_cache is None:
        node_cache = int(os.environ.get("TMKG_QUERY_NODE_CACHE", 100_000))
    return max_steps, max_seconds, max_bindings, node_cache


class _CachedNodes(Mapping):
    """kg.nodes with a dict in front: a node already read during this query is not read from the store again."""

    def __init__(self, nodes, limit=None):
        self.nodes, self.cache, self.limit = nodes, {}, limit

    def __getitem__(self, nid):
        if nid in self.cache:
            return self.cache[nid]
        got = self.nodes[nid]
        if self.limit is None or len(self.cache) < self.limit:      # beyond the bound: read through, do not keep
            self.cache[nid] = got
        return got

    def __contains__(self, nid):
        return nid in self.cache or nid in self.nodes

    def __iter__(self):
        return iter(self.nodes)

    def __len__(self):
        return len(self.nodes)


class _View:
    """The graph as one run_local call sees it: same attributes, nodes cached for the life of the query."""

    def __init__(self, kg, node_cache=None):
        self._kg = kg
        self.nodes = _CachedNodes(kg.nodes, node_cache)

    def __getattr__(self, name):
        return getattr(self._kg, name)


def _node_ok(kg, nid, n):
    nd = kg.nodes[nid]
    if n.label and local(nd["category"]) != n.label:
        return False
    return all(str(_get({n.var: nid}, kg, n.var, k)) == str(v) if not isinstance(v, bool)
               else _get({n.var: nid}, kg, n.var, k) is v for k, v in n.props.items())


def _adjacent(kg, nid, side, types):
    """Edges whose `side` ('subject' | 'object') is nid; a graph with adjacent() reads only the relationship types asked for."""
    if types and hasattr(kg, "adjacent"):
        return kg.adjacent(nid, side, types)
    return (kg.by_subject if side == "subject" else kg.by_object).get(nid, [])


def _between(kg, a, b):
    """Edges from node a to node b (subject a, object b)."""
    if hasattr(kg, "edges_between"):
        return kg.edges_between(a, b)
    sa, ob = kg.by_subject.get(a, []), kg.by_object.get(b, [])
    return [e for e in sa if e["object"] == b] if len(sa) <= len(ob) else [e for e in ob if e["subject"] == a]


def _matches(q, kg, max_steps=0, max_seconds=0):
    """Every binding {var: node id | edge dict} of the MATCH paths (relationships never bound twice). Raises QueryTooBroad after
    max_steps edge checks or max_seconds (0 = no limit)."""
    work = [0]
    t0 = time.monotonic()

    def tick(n):
        work[0] += n
        if max_steps and work[0] > max_steps:
            raise QueryTooBroad(too_broad_text("steps", max_steps))
        if max_seconds and time.monotonic() - t0 > max_seconds:
            raise QueryTooBroad(too_broad_text("seconds", max_seconds))

    first = {}
    for i, n in enumerate(q.nodes):
        first.setdefault(n.var, i)
    nodes = {n.var: n for n in reversed(q.nodes)}
    for n in q.nodes:                                   # merge repeated variables' constraints
        if n.label:
            nodes[n.var].label = nodes[n.var].label or n.label
        nodes[n.var].props.update(n.props)
    vars_ = list(dict.fromkeys(n.var for n in q.nodes))
    rels = [(r, q.nodes[r.left].var, q.nodes[r.right].var) for r in q.rels]

    def cands(var):
        n = nodes[var]
        if "id" in n.props:
            return [n.props["id"]] if n.props["id"] in kg.nodes else []
        if hasattr(kg, "candidates"):                   # a graph that can narrow by class / name itself (the card index) skips the scan
            got = kg.candidates(n.label, n.props, lambda x: _node_ok(kg, x, n))
            tick(len(got))
            return got
        tick(len(kg.nodes))
        return sorted(x for x in kg.nodes if _node_ok(kg, x, n))

    def extend(b, pending):
        if not pending:
            free = [v for v in vars_ if v not in b]
            if not free:
                yield dict(b)
                return
            v = min(free, key=lambda v: (0 if "id" in nodes[v].props else 1, first[v]))
            for x in cands(v):
                if _node_ok(kg, x, nodes[v]):
                    yield from extend({**b, v: x}, [rr for rr in rels if rr[1] == v or rr[2] == v])
            return
        ready = [rr for rr in rels if rr[0].var not in b and (rr[1] in b or rr[2] in b)]
        if not ready:
            yield from extend(b, [])
            return
        ready.sort(key=lambda rr: 0 if rr[1] in b and rr[2] in b else 1)      # both ends known: a pair lookup, before any walk
        r, lv, rv = ready[0]
        used = {x["id"] for x in b.values() if isinstance(x, dict)}
        if lv in b and rv in b:
            options = []
            if r.direction in ("out", "both"):
                options += [(e, b[rv]) for e in _between(kg, b[lv], b[rv])]
            if r.direction in ("in", "both"):
                options += [(e, b[rv]) for e in _between(kg, b[rv], b[lv])]
            other = rv
        elif lv in b:
            options = []
            if r.direction in ("out", "both"):
                options += [(e, e["object"]) for e in _adjacent(kg, b[lv], "subject", r.types)]
            if r.direction in ("in", "both"):
                options += [(e, e["subject"]) for e in _adjacent(kg, b[lv], "object", r.types)]
            other = rv
        else:
            options = []
            if r.direction in ("out", "both"):
                options += [(e, e["subject"]) for e in _adjacent(kg, b[rv], "object", r.types)]
            if r.direction in ("in", "both"):
                options += [(e, e["object"]) for e in _adjacent(kg, b[rv], "subject", r.types)]
            other = lv
        tick(len(options) + 1)
        for e, x in sorted(options, key=lambda t: t[0]["id"]):
            if e["id"] in used or (r.types and local(e["predicate"]) not in r.types):
                continue
            if any(str(e.get(k)) != str(v) for k, v in r.props.items()):
                continue
            if other in b:
                if b[other] != x:
                    continue
                yield from extend({**b, r.var: e}, pending)
            elif _node_ok(kg, x, nodes[other]):
                yield from extend({**b, r.var: e, other: x}, pending)

    anchors = [v for v in vars_ if "id" in nodes[v].props]                  # an id pins the node: bind all of them before walking
    start = {}
    for v in anchors:
        if nodes[v].props["id"] not in kg.nodes or not _node_ok(kg, nodes[v].props["id"], nodes[v]):
            return
        start[v] = nodes[v].props["id"]
    seen = set()
    for b in extend(start, [rr for rr in rels if rr[1] in start or rr[2] in start]):
        key = tuple(sorted((k, v if isinstance(v, str) else v["id"]) for k, v in b.items()))
        if key not in seen:
            seen.add(key)
            yield b


# ------------------------------------------------------------------ evidence-first ordering (CC-31a, review finding M2)
# Rank of a BioLink knowledge level: lower = stronger. The order follows the review's fix: curated statements, then other
# source assertions (inferred / observed), then statistical associations, then unspecified, then predictions, then text
# mining. A level that is missing or not listed counts as not_provided.
LEVEL_RANK = {"knowledge_assertion": 0, "logical_entailment": 1, "observation": 1, "statistical_association": 2,
              "not_provided": 3, "prediction": 4, "text_co_occurrence": 5}
TEXT_LEVEL = "text_co_occurrence"
EVIDENCE_LOOKUP_MAX = 20_000      # above this many matched rows only the level is used (no corroboration / verdict lookups);
                                  # env TMKG_EVIDENCE_LOOKUP_MAX overrides


def _edge_strength(e, kg, cache):
    """Takes one edge dict, the graph and a per-query cache. Returns (level rank, corroborating sources, confidence or None):
    corroborating sources = distinct `source` values stating the same (subject, object) pair in any layer, this edge
    included (kg.same_pair when the graph has it, else 1); confidence = the pruning verdict's confidence (kg.verdict)."""
    eid = e.get("id")
    if eid in cache:
        return cache[eid]
    rank = LEVEL_RANK.get(e.get("knowledge_level") or "not_provided", LEVEL_RANK["not_provided"])
    srcs = {e.get("source") or e.get("primary_knowledge_source") or eid}
    if hasattr(kg, "same_pair") and e.get("subject") and e.get("object"):
        pk = ("pair", e["subject"], e["object"])
        if pk not in cache:
            cache[pk] = {o.get("source") or o.get("primary_knowledge_source") or o.get("id")
                         for o in kg.same_pair(e["subject"], e["object"])}
        srcs |= cache[pk]
    conf = None
    if hasattr(kg, "verdict"):
        v = kg.verdict(eid) or {}
        try:
            conf = float(v["confidence"]) if v.get("confidence") not in (None, "") else None
        except (TypeError, ValueError):
            conf = None
    cache[eid] = (rank, len(srcs), conf)
    return cache[eid]


def evidence_key(edges, kg, cache=None):
    """Takes the edge dicts one answer row cites, the graph and an optional cache. Returns the row's sort key, smaller =
    stronger: (rank of its weakest knowledge level, minus its fewest corroborating sources, a row whose edges all carry a
    pruning confidence before one that does not, minus its lowest confidence). A row is as strong as its weakest edge."""
    cache = {} if cache is None else cache
    st = [_edge_strength(e, kg, cache) for e in edges if isinstance(e, dict)]
    if not st:
        return (LEVEL_RANK["not_provided"], -1, True, 0.0)
    confs = [c for _, _, c in st if c is not None]
    return (max(r for r, _, _ in st), -min(n for _, n, _ in st), len(confs) < len(st), -(min(confs) if confs else 0.0))


def _ordering_note(n_total, n_shown, n_more_text, collapsed, full=True):
    """The sentence the page shows about the row order, built from the counts. full=False: only the knowledge level was
    used (more matches than EVIDENCE_LOOKUP_MAX)."""
    how = ("curated before statistical before text-mined, then more sources, then pruning confidence" if full else
           f"curated before statistical before text-mined; more than {_lookup_max():,} matches, so sources and pruning "
           "confidence were not looked up")
    s = f"Top {n_shown} of {n_total} answers by evidence ({how})"
    if n_shown >= n_total:
        s = f"All {n_total} answers, strongest evidence first ({how})"
    more = n_total - n_shown
    if more:
        s += f"; {more} more not shown" + (f", {n_more_text} of them text-mined only" if n_more_text else "")
    if collapsed:
        s += f"; {collapsed} duplicate rows of the same answer merged into one row each"
    return s + "."


def _collapse(pairs, key_idx):
    """Takes [(row values, key, rank)] sorted strongest first and the indexes of the columns that name an answer. Returns
    (merged pairs, number of rows merged away): rows with the same values in those columns become the first (strongest) row,
    where every other column holds the list of distinct values of the merged rows (one value stays a value)."""
    if not key_idx:
        return pairs, 0
    groups, order = {}, []
    for row, key, rank in pairs:
        k = json.dumps([row[i] for i in key_idx], default=str)
        if k not in groups:
            groups[k] = [row, key, rank, [row]]
            order.append(k)
        else:
            groups[k][3].append(row)
    out = []
    for k in order:
        row, key, rank, rows = groups[k]
        if len(rows) > 1:
            merged = []
            for i, v in enumerate(row):
                if i in key_idx:
                    merged.append(v)
                    continue
                vals = []
                for r in rows:
                    for x in (r[i] if isinstance(r[i], list) else [r[i]]):
                        if x not in vals:
                            vals.append(x)
                merged.append(vals[0] if len(vals) == 1 else vals)
            row = merged
        out.append((row, key, rank))
    return out, len(pairs) - len(out)


def _lookup_max():
    return int(os.environ.get("TMKG_EVIDENCE_LOOKUP_MAX", EVIDENCE_LOOKUP_MAX))


def _rank(q, pairs, limit, full=True):
    """Takes the parsed query, [(row values, evidence key, level rank)] in the query's own order and the row limit. Returns
    (pairs strongest first with duplicate answers merged, the `ordering` dict). The sort is stable, so the query's ORDER BY
    breaks ties."""
    pairs = sorted(pairs, key=lambda p: p[1])
    nvars = {n.var for n in q.nodes}
    key_idx = [i for i, (e, _) in enumerate(q.items) if e[0] == "prop" and e[1] in nvars]
    pairs, collapsed = _collapse(pairs, key_idx)
    n_total, n_shown = len(pairs), min(len(pairs), limit)
    n_more_text = sum(1 for _, _, rank in pairs[limit:] if rank == LEVEL_RANK[TEXT_LEVEL])
    return pairs, dict(by="evidence", n_total=n_total, n_shown=n_shown, n_more=n_total - n_shown, n_more_text_mined=n_more_text,
                       collapsed=collapsed, full=full, sentence=_ordering_note(n_total, n_shown, n_more_text, collapsed, full))


def rank_rows(cypher, result, kg):
    """Takes a validated query, the {columns, rows, evidence} another database returned for it (Neptune: already cut at
    LIMIT) and the local graph. Returns the result with its rows ordered evidence-first and duplicate answers merged, as
    run_local(evidence_first=True) does, looking each cited edge up by id in kg.edges (an id the graph lacks counts as an
    edge of unknown level). Only the rows that came back are ordered; ordering['sentence'] says so. A query with count() /
    collect(), or a graph without edges by id, returns the result unchanged."""
    q = parse(cypher)
    if any(e[0] == "agg" for e, _ in q.items) or not hasattr(kg, "edges"):
        return result
    cols = [a for _, a in q.items]
    rvars = {r.var for r in q.rels}
    ev_cols = [i for i, (e, _) in enumerate(q.items) if _is_evidence(e, rvars)]
    cache, pairs = {}, []
    for row in result.get("rows") or []:
        vals = [row.get(c) for c in cols]
        edges = []
        for i in ev_cols:
            eid = vals[i]
            edges.append(kg.edges[eid] if isinstance(eid, str) and eid in kg.edges else {"id": eid})
        key = evidence_key(edges, kg, cache)
        pairs.append((vals, key, key[0]))
    pairs, ordering = _rank(q, pairs, q.limit)
    ordering["sentence"] = ordering["sentence"][:-1] + " (among the rows the database returned)."
    rows = [r for r, _, _ in pairs[:q.limit]]
    evidence = sorted({x for r in rows for i in ev_cols for x in (r[i] if isinstance(r[i], list) else [r[i]]) if x})
    return dict(result, rows=[dict(zip(cols, r)) for r in rows], evidence=evidence, ordering=ordering)


def _sort_key(v):
    v = _num(v)
    return (v is None, 0 if isinstance(v, (int, float)) and not isinstance(v, bool) else 1,
            v if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v))


def run_local(cypher, kg, max_steps=None, max_seconds=None, max_bindings=None, node_cache=None, evidence_first=False):
    """Executes a validated query on the in-memory graph -> dict(columns, rows, evidence).
    Row order: ORDER BY if given, otherwise sorted (Cypher leaves it undefined); collect() lists are sorted.
    evidence_first (the Pharos graph, CC-31a): BEFORE LIMIT, rows are ordered by evidence_key of the edges they cite (the
    query's ORDER BY stays the tie-breaker), rows naming the same answer (same values in every returned node property) are
    merged into one, and the result gets `ordering` {by, n_total, n_shown, n_more, n_more_text_mined, collapsed, sentence}.
    Queries with count() / collect() keep the plain order.
    Work budget (public server): after max_steps edge checks or max_seconds (defaults: env TMKG_QUERY_MAX_STEPS 2,000,000 and
    TMKG_QUERY_MAX_SECONDS 60; 0 = no limit), or when more than max_bindings (TMKG_QUERY_MAX_BINDINGS 100,000) matches are held in
    memory, it returns no rows and problems=[TOO_BROAD] instead of running on. node_cache (TMKG_QUERY_NODE_CACHE 100,000) bounds the
    per-query node cache. On a public server at most TMKG_QUERY_CONCURRENCY queries run at the same time (query_slot);
    QueryBusy is raised when no slot came free in time."""
    with query_slot():
        return _run_local(cypher, kg, max_steps, max_seconds, max_bindings, node_cache, evidence_first)


def _run_local(cypher, kg, max_steps, max_seconds, max_bindings, node_cache, evidence_first):
    """run_local without the concurrency gate (same arguments, same result)."""
    q = parse(cypher)
    ms, sec, mb, nc = _limits(max_steps, max_seconds, max_bindings, node_cache)
    kg = _View(kg, nc)
    cols = [a for _, a in q.items]
    try:
        binds = []
        for b in _matches(q, kg, ms, sec):
            if q.where is None or _ev(q.where, b, kg):
                binds.append(b)
                if mb and len(binds) > mb:
                    raise QueryTooBroad(too_broad_text("bindings", mb))
    except QueryTooBroad as err:
        return dict(columns=cols, rows=[], evidence=[], problems=[str(err)])
    aggs = [i for i, (e, _) in enumerate(q.items) if e[0] == "agg"]
    if aggs:
        groups = {}
        for b in binds:
            key = tuple(json.dumps(_ev(e, b, kg), sort_keys=True, default=str) if i not in aggs else None
                        for i, (e, _) in enumerate(q.items))
            groups.setdefault(key, []).append(b)
        if not groups and all(i in aggs for i in range(len(q.items))):
            groups[tuple(None for _ in q.items)] = []
        rows = []
        for key, bs in groups.items():
            row = []
            for i, (e, _) in enumerate(q.items):
                if i not in aggs:
                    row.append(json.loads(key[i]))
                    continue
                _, fn, distinct, arg = e
                vals = [None if arg == ("star",) else _ev(arg, b, kg) for b in bs]
                if arg != ("star",):
                    vals = [v for v in vals if v is not None]
                if distinct:
                    vals = list(dict.fromkeys(json.dumps(v, default=str) for v in vals))
                    vals = [json.loads(v) for v in vals]
                row.append(len(vals) if fn == "count" else sorted(vals, key=_sort_key))
            rows.append(row)
    rvars = {r.var for r in q.rels}
    ev_cols = [i for i, (e, _) in enumerate(q.items) if _is_evidence(e, rvars)]
    ranked = evidence_first and not aggs
    if not aggs:
        cache, ev_vars = {}, [q.items[i][0][1] for i in ev_cols]
        full = len(binds) <= _lookup_max()
        rows = []
        for b in binds:
            row = [_ev(e, b, kg) for e, _ in q.items]
            if ranked:
                key = evidence_key([b[v] for v in ev_vars], kg if full else None, cache)
                rows.append((row, key, key[0]))
            else:
                rows.append((row, None, None))
    else:
        rows = [(r, None, None) for r in rows]
    if q.distinct:
        best = {}
        for row, key, rank in rows:
            k = json.dumps(row, default=str)
            if k not in best or (key is not None and key < best[k][1]):
                best[k] = (row, key, rank)
        rows = list(best.values())
    rows.sort(key=lambda p: [_sort_key(json.dumps(v) if isinstance(v, list) else v) for v in p[0]])
    for name, desc in reversed(q.order):
        i = cols.index(name)
        rows.sort(key=lambda p: _sort_key(p[0][i]), reverse=desc)
    ordering = None
    if ranked:
        rows, ordering = _rank(q, rows, q.limit, full)
    rows = [r for r, _, _ in rows[:q.limit]]
    evidence = sorted({x for r in rows for i in ev_cols for x in (r[i] if isinstance(r[i], list) else [r[i]]) if x})
    out = dict(columns=cols, rows=[dict(zip(cols, r)) for r in rows], evidence=evidence)
    if ordering is not None:
        out["ordering"] = ordering
    return out


def to_neo4j(cypher):
    """Neptune names relationship types as exported (lowercase, e.g. interacts_with); the Neo4j loader upper-cases
    them. Labels and properties are identical, so only the type names inside [...] change."""
    def rel(m):
        head, props = (m.group(1).split("{", 1) + [None])[:2]
        head = re.sub(r"([:|]\s*)([A-Za-z_][A-Za-z0-9_]*)", lambda x: x.group(1) + x.group(2).upper(), head)
        return "[" + head + ("{" + props if props is not None else "") + "]"
    strings = []
    def keep(m):
        strings.append(m.group(0))
        return f"\x00{len(strings) - 1}\x00"
    masked = re.sub(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"", keep, cypher)
    out = re.sub(r"\[([^\]]*)\]", rel, masked)
    return re.sub(r"\x00(\d+)\x00", lambda m: strings[int(m.group(1))], out)


# ------------------------------------------------------------------ the agent
SYSTEM = ("You translate questions about the TrialMindKG clinical-trial knowledge graph into ONE openCypher query.\n"
          "Rules: use only the labels, relationship types and properties in the SCHEMA; write "
          "MATCH ... [WHERE ...] RETURN ... [ORDER BY ...] LIMIT n (one MATCH, comma-separated paths allowed; no "
          "WITH, OPTIONAL, UNWIND, CALL or writes; no variable-length paths). Functions: toLower, toUpper, type, "
          "count, collect. Always RETURN the id of every relationship you traverse (r.id AS evidence) so the answer "
          "can cite it. Use the entity ids given in ENTITIES exactly; never invent ids. Node ids are in the property "
          "'id' (e.g. {id:'NCT:NCT02446912'}). Return JSON: {\"cypher\": \"...\", \"explanation\": \"...\"}")
SCHEMA = {"type": "object", "required": ["cypher"],
          "properties": {"cypher": {"type": "string"}, "explanation": {"type": "string"}}}


def load_gold(path=GOLD):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def entity_lines(kg, ids):
    out = []
    for i in ids:
        n = kg.nodes[i]
        out.append(f"- {i} | {local(n['category'])} | {(n.get('acronym') + ' - ') if n.get('acronym') else ''}"
                   f"{(n.get('name') or '')[:80]}")
    return out


# Worked examples for patterns the model got wrong on the larger schema (2026-10-06 evaluation). They use OTHER
# programs / trials than the gold questions (data/curated/cypher_gold.csv), so no gold answer is shown.
PATTERN_EXAMPLES = [
    dict(question="What were the serious adverse events and deaths per arm in EXPEDITION3?",
         cypher="MATCH (p:ClinicalTrialProgram)-[r:has_trial]->(t:ClinicalTrial {id:'NCT:NCT01900665'}) "
                "RETURN t.ae_groups AS adverse_event_groups, t.ae_time_frame AS time_frame, r.id AS evidence LIMIT 10"),
    dict(question="What happened after the GENERATION HD1 failure?",
         cypher="MATCH (p:ClinicalTrialProgram {id:'P06'})-[r:has_post_failure_analysis]->(d:DevelopmentEvent) "
                "RETURN d.analysis_showed AS analysis, d.pivot_decision AS decision, d.outcome_evidence AS outcome, "
                "r.id AS evidence ORDER BY decision LIMIT 20"),
    dict(question="Which drugs were given in the MYSTIC trial and in which role?",
         cypher="MATCH (t:ClinicalTrial {id:'NCT:NCT02453282'})-[r:tests_intervention|has_comparator_drug|"
                "has_background_drug]->(d:Drug) RETURN d.name AS drug, type(r) AS role, r.id AS evidence "
                "ORDER BY drug LIMIT 50"),
    dict(question="Which genes are targeted by drugs of two different programs?",
         cypher="MATCH (p1:ClinicalTrialProgram)-[r1:has_intervention]->(d1:Drug)-[r2:interacts_with]->(t), "
                "(p2:ClinicalTrialProgram)-[r3:has_intervention]->(d2:Drug)-[r4:interacts_with]->(t) "
                "WHERE p1.id < p2.id RETURN t.name AS target, p1.id AS program_1, p2.id AS program_2, "
                "r1.id AS e1, r2.id AS e2, r3.id AS e3, r4.id AS e4 ORDER BY target LIMIT 100"),
]


class Querier:
    def __init__(self, nim, kg, examples=None, max_attempts=3, pattern_examples=None, runner=None, evidence_first=False):
        """pattern_examples: worked examples appended to the prompt (default PATTERN_EXAMPLES, written for the
        trial graph; pass [] for another graph such as the Pharos graph). runner: a function cypher -> {columns, rows,
        evidence} that runs the validated query somewhere else than run_local (neptune_backend.NeptuneRunner(...).run;
        rows then come back in the order the database gives them). evidence_first: run_local orders rows by evidence
        before LIMIT (the Pharos graph; run_local)."""
        self.nim, self.kg, self.max_attempts = nim, kg, max_attempts
        self.evidence_first = evidence_first
        self.runner = runner
        self.pattern_examples = PATTERN_EXAMPLES if pattern_examples is None else pattern_examples
        self.card = kg.schema_card() if hasattr(kg, "schema_card") else schema_card(kg)   # PharosGraph: from tables
        self.examples = load_gold() if examples is None else examples

    def messages(self, question, entities, exclude=None):
        shots = [g for g in self.examples if g["id"] != exclude][:8] + self.pattern_examples
        ex = "\n\n".join(f"QUESTION: {g['question']}\nCYPHER: {g['cypher']}" for g in shots)
        user = (f"SCHEMA:\n{card_text(self.card)}\n\nEXAMPLES:\n{ex}\n\nENTITIES:\n" +
                ("\n".join(entity_lines(self.kg, entities)) or "(none found in the question)") +
                f"\n\nQUESTION: {question}")
        return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]

    def ask(self, question, entities=None, exclude=None):
        """-> dict(question, entities, cypher, problems, columns, rows, evidence, attempts, backend, model).
        An invalid query is sent back once with the validator's problems; it is never executed. When no attempt gave a
        valid query the dict has no_query=True and carries fallback_from / reason / model_fallback_from of the last reply."""
        if entities is None:
            from orchestrator import find_seeds
            entities = find_seeds(self.kg, question)
        msgs, attempts, last = self.messages(question, entities, exclude), [], None
        for _ in range(self.max_attempts):
            try:
                r = last = self.nim.chat("querier", msgs, schema=SCHEMA)
            except ValueError as err:                    # malformed reply: count it as a failed attempt, retry once
                attempts.append(dict(cypher="", problems=[str(err)], snapshot_key=""))
                probs, cy = [str(err)], ""
                msgs = msgs + [{"role": "user", "content": "Your reply was not valid JSON. Reply with JSON only: "
                                                          '{"cypher": "...", "explanation": "..."}'}]
                continue
            cy = (r.data or {}).get("cypher", "").strip()
            probs = validate(cy, self.card) if cy else ["no query returned"]
            attempts.append(dict(cypher=cy, problems=probs, snapshot_key=r.key, cached=bool(r.meta.get("cached"))))
            if not probs:
                if self.runner:
                    res = self.runner(cy)
                    if self.evidence_first:
                        res = rank_rows(cy, res, self.kg)
                else:
                    res = run_local(cy, self.kg, evidence_first=self.evidence_first)
                return dict(question=question, entities=entities, cypher=cy, neo4j=to_neo4j(cy), **{"problems": [], **res}, attempts=attempts, backend=r.backend, model=r.model,
                            dropped_constraints=dropped_constraints(attempts, cy),      # CC-31d: conditions an earlier attempt had
                            explanation=str((r.data or {}).get("explanation") or "")[:500],
                            cached=bool(r.meta.get("cached")),            # answered from the frozen snapshot, not the API
                            fallback_from=r.meta.get("fallback_from"),     # 'nim' when the stand-in answered a failed hosted call
                            model_fallback_from=r.meta.get("model_fallback_from"))   # primary id when the backup model answered
            msgs = msgs + [{"role": "assistant", "content": r.text},
                           {"role": "user", "content": "The query is not valid: " + "; ".join(probs) + ". " +
                            fix_hints(probs) + "Return a corrected query as JSON."}]
        # no valid query after every attempt. SEC-03: when the last reply came from the stand-in after a failed hosted call
        # (or from the backup model), say so, so that the answer is labelled "the hosted model did not answer" and not "no rows"
        meta = last.meta if last is not None else {}
        if meta.get("reason"):                           # the technical reason stays in the server log, not in the answer
            print(f"querier: no query; last model failure: {meta['reason']}", file=sys.stderr)
        return dict(question=question, entities=entities, cypher=cy, neo4j="", problems=probs, columns=[], rows=[],
                    evidence=[], attempts=attempts, backend=self.nim.backend, model=self.nim.model("querier"),
                    dropped_constraints=dropped_constraints(attempts, cy), no_query=True,
                    fallback_from=meta.get("fallback_from"),
                    reason="the hosted model could not be reached" if meta.get("fallback_from") else None,
                    model_fallback_from=meta.get("model_fallback_from"))


# ------------------------------------------------------------------ evaluation
def evaluate(nim, kg, gold=None):
    """Execution accuracy: the generated query must return exactly the gold query's evidence edge ids.
    Each question is answered with its own example left out of the prompt (no answer leakage)."""
    gold = gold or load_gold()
    q = Querier(nim, kg, examples=gold)
    rows = []
    for g in gold:
        want = run_local(g["cypher"], kg)["evidence"]
        got = q.ask(g["question"], exclude=g["id"])
        rows.append(dict(id=g["id"], question=g["question"], valid=not got["problems"],
                         exact_evidence=got["evidence"] == want and bool(want), n_gold=len(want), n_got=len(got["evidence"]),
                         attempts=len(got["attempts"]), cypher=got["cypher"], problems=got["problems"]))
    n = len(rows) or 1
    return dict(backend=nim.backend, n=len(rows), valid_rate=round(sum(r["valid"] for r in rows) / n, 3),
                execution_accuracy=round(sum(r["exact_evidence"] for r in rows) / n, 3),
                first_try_valid=round(sum(r["valid"] and r["attempts"] == 1 for r in rows) / n, 3), rows=rows)


# ------------------------------------------------------------------ offline stand-in (template choice, labelled)
TEMPLATES = [  # (intent, question regex, entity label, cypher with {id})
    ("shared_targets", r"\bshare|\bcommon target|same target", None,
     "MATCH (p1:ClinicalTrialProgram)-[r1:has_intervention]->(d1:Drug)-[r2:interacts_with]->(t)"
     "<-[r3:interacts_with]-(d2:Drug)<-[r4:has_intervention]-(p2:ClinicalTrialProgram) WHERE p1.id < p2.id "
     "RETURN t.id AS target, t.name AS target_name, p1.id AS program_1, p2.id AS program_2, r1.id AS e1, r2.id AS e2, "
     "r3.id AS e3, r4.id AS e4 ORDER BY target LIMIT 200"),
    ("trial_safety", r"safety|adverse|serious|death", "ClinicalTrial",
     "MATCH (p:ClinicalTrialProgram)-[r:has_trial]->(t:ClinicalTrial {{id:'{id}'}}) "
     "RETURN t.ae_groups AS adverse_event_groups, t.ae_time_frame AS time_frame, r.id AS evidence LIMIT 10"),
    ("trial_publications", r"publication|paper|article|published", "ClinicalTrial",
     "MATCH (t:ClinicalTrial {{id:'{id}'}})-[r:has_publication]->(p:Publication) "
     "RETURN p.id AS pmid, p.title AS title, p.year AS year, r.reference_type AS reference_type, r.id AS evidence "
     "ORDER BY pmid LIMIT 200"),
    ("trial_primary", r"primary (end ?point|outcome)", "ClinicalTrial",
     "MATCH (t:ClinicalTrial {{id:'{id}'}})-[r:has_outcome_measure]->(o:OutcomeMeasure) WHERE o.kind = 'PRIMARY' "
     "RETURN o.name AS outcome, o.time_frame AS time_frame, o.analyses AS analyses, r.id AS evidence "
     "ORDER BY outcome LIMIT 50"),
    ("program_history", r"after|history|happened|repurpos|pivot|next|data access", "ClinicalTrialProgram",
     "MATCH (p:ClinicalTrialProgram {{id:'{id}'}})-[r:has_post_failure_analysis|has_repurposing_pivot]->"
     "(e:DevelopmentEvent) RETURN e.id AS event, type(r) AS relation, e.pivot_decision AS decision, "
     "e.outcome_evidence AS outcome, e.what_changed AS what_changed, r.id AS evidence ORDER BY event LIMIT 50"),
    ("drug_targets", r"target|mechanism|bind", "Drug",
     "MATCH (d:Drug {{id:'{id}'}})-[r:interacts_with]->(t) RETURN t.id AS target, t.name AS target_name, "
     "r.moa AS mechanism, r.chembl_action_type AS action, r.id AS evidence ORDER BY target LIMIT 50"),
    ("trial_drugs", r"drug|comparator|intervention|arm|placebo|background", "ClinicalTrial",
     "MATCH (t:ClinicalTrial {{id:'{id}'}})-[r:tests_intervention|has_comparator_drug|has_background_drug]->"
     "(d:Drug) RETURN d.id AS drug, d.name AS name, type(r) AS role, r.arm_types AS arm_types, r.id AS evidence "
     "ORDER BY role, drug LIMIT 50"),
    ("trial_outcomes", r"outcome|end ?point|result", "ClinicalTrial",
     "MATCH (t:ClinicalTrial {{id:'{id}'}})-[r:has_outcome_measure]->(o:OutcomeMeasure) "
     "RETURN o.kind AS kind, o.name AS outcome, r.id AS evidence ORDER BY kind, outcome LIMIT 200"),
    ("program_trials", r"trial|stud", "ClinicalTrialProgram",
     "MATCH (p:ClinicalTrialProgram {{id:'{id}'}})-[r:has_trial]->(t:ClinicalTrial) RETURN t.id AS trial, "
     "t.acronym AS acronym, t.status AS status, t.why_stopped AS why_stopped, t.phase AS phase, r.id AS evidence "
     "ORDER BY trial LIMIT 50"),
]


@register_offline("querier")
def _offline_querier(messages, schema):
    user = [m for m in messages if m["role"] == "user"][0]["content"]
    question = user.rsplit("QUESTION: ", 1)[-1].lower()
    ents = re.findall(r"^- (\S+) \| (\S+) \|", user.split("ENTITIES:", 1)[-1], re.M)
    for intent, pat, label, cy in TEMPLATES:
        if not re.search(pat, question):
            continue
        if label is None:
            return json.dumps({"cypher": cy, "explanation": f"offline template {intent}"})
        ids = [i for i, l in ents if l == label]
        if ids:
            return json.dumps({"cypher": cy.format(id=ids[0]), "explanation": f"offline template {intent}"})
    return json.dumps({"cypher": "", "explanation": "offline stand-in: no template matches this question"})


if __name__ == "__main__":
    import nim as nimmod
    from kg_query import KGIndex
    n, kg = nimmod.NIM(), KGIndex()
    if sys.argv[1:] == ["evaluate"]:
        r = evaluate(n, kg)
        print(json.dumps({k: v for k, v in r.items() if k != "rows"}, indent=2))
        out = ROOT / "outputs" / "eval" / f"querier_{r['backend']}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(r, indent=1, ensure_ascii=False))
    else:
        r = Querier(n, kg).ask(" ".join(sys.argv[1:]) or "What was the primary outcome of TULIP-1?")
        print(json.dumps({k: r[k] for k in ("cypher", "problems", "evidence", "backend")}, indent=1))
        for row in r["rows"][:20]:
            print(row)
