"""
Prize-collecting Steiner tree (PCST) retrieval: the small, connected subgraph each specialist reasons over.

Nodes get a prize (how useful they are to that specialist), edges a cost (how weak or generic they are);
the subgraph keeps the question's own entities (seeds) and adds nodes only when they are worth their cost.
Deterministic heuristic (no randomness, sorted iteration):
  1. terminals = seeds + nodes whose prize exceeds their shortest-path distance from the seeds (net gain),
     best net gain first, at most `max_terminals`
  2. connect them with networkx's Steiner-tree approximation (Mehlhorn) on edge costs
  3. strong pruning: repeatedly drop non-seed leaves whose prize is below the cost of their edge

Functions
- build_graph(kg)                          -> networkx.Graph with edge 'cost' and 'eid' (one edge per node pair)
- edge_cost(edge, deg_u, deg_v)            -> cost: evidence strength + hub penalty
- pcst(G, prizes, seeds, max_terminals)    -> (node set, edge id list)
- subgraph_for(kg, G, seeds, role)         -> Subgraph(nodes, edges, facts) for one specialist role
- facts(kg, edge_ids)                      -> lines '[e…] subject -predicate-> object | key properties'
- label(node, kg)                          -> short display name ('METREO (NCT02105961)')
"""
import json, math
from dataclasses import dataclass
import networkx as nx
from networkx.algorithms.approximation import steiner_tree

EVIDENCE_COST = {"registry": 0.5, "curated": 0.5, "curator_confirmed": 0.5, "literature": 0.8,
                 "database": 1.0, "computed": 1.5, "internal": 0.8, "synthetic": 0.8}

# prize per node category, per specialist (application: Failure Diagnostician, Population Stratifier,
# Indication Explorer, Trial Redesign Advisor)
ROLE_PRIZES = {
    "failure_diagnostician": {"biolink:ClinicalTrial": 3, "tmkg:OutcomeMeasure": 4, "tmkg:DevelopmentEvent": 4,
                              "biolink:Drug": 2, "biolink:Publication": 0.5, "biolink:Protein": 1, "biolink:Gene": 1},
    "population_stratifier": {"tmkg:OutcomeMeasure": 4, "tmkg:DevelopmentEvent": 3, "biolink:ClinicalTrial": 3,
                              "biolink:Disease": 2, "biolink:Gene": 1},
    "indication_explorer": {"biolink:Gene": 3, "biolink:Disease": 4, "biolink:Pathway": 1.5, "biolink:Drug": 2,
                            "tmkg:ClinicalTrialProgram": 3, "biolink:Protein": 2},
    "redesign_advisor": {"tmkg:DevelopmentEvent": 5, "tmkg:OutcomeMeasure": 3, "biolink:ClinicalTrial": 2,
                         "biolink:Drug": 2, "biolink:Disease": 1},
}
# node properties shown to the models next to an edge (compact, verbatim)
SHOW = ["name", "status", "why_stopped", "phase", "kind", "time_frame", "analyses", "ipd_sharing", "pivot_decision",
        "analysis_showed", "outcome_evidence", "original_problem", "what_changed", "consequence", "caveat",
        "access_route", "symbol", "ot_score"]


@dataclass
class Subgraph:
    role: str
    seeds: list
    nodes: list
    edges: list            # edge ids
    facts: list            # text lines given to the model


def edge_cost(e, deg_u, deg_v):
    base = EVIDENCE_COST.get(e.get("evidence_type"), 1.0)
    return round(base + 0.25 * math.log1p(max(deg_u, deg_v)), 6)      # hubs (big pathways, MeSH terms) cost more


def build_graph(kg):
    G = nx.Graph()
    for nid in sorted(kg.nodes):
        G.add_node(nid, category=kg.nodes[nid]["category"])
    deg = {}
    for e in kg.edges.values():
        deg[e["subject"]] = deg.get(e["subject"], 0) + 1
        deg[e["object"]] = deg.get(e["object"], 0) + 1
    for eid in sorted(kg.edges):
        e = kg.edges[eid]
        u, v = e["subject"], e["object"]
        c = edge_cost(e, deg.get(u, 0), deg.get(v, 0))
        if not G.has_edge(u, v) or c < G[u][v]["cost"] or (c == G[u][v]["cost"] and eid < G[u][v]["eid"]):
            G.add_edge(u, v, cost=c, eid=eid)
    return G


def pcst(G, prizes, seeds, max_terminals=25):
    seeds = sorted(s for s in seeds if s in G)
    if not seeds:
        return set(), []
    dist = nx.multi_source_dijkstra_path_length(G, seeds, weight="cost")
    gains = sorted(((prizes.get(n, 0) - d, n) for n, d in dist.items() if n not in seeds and prizes.get(n, 0) > d),
                   key=lambda x: (-x[0], x[1]))
    terminals = seeds + [n for _, n in gains[:max_terminals]]
    if len(terminals) == 1:
        return set(terminals), []
    comp = nx.node_connected_component(G, seeds[0])
    terminals = [t for t in terminals if t in comp]
    T = steiner_tree(G.subgraph(comp), terminals, weight="cost", method="mehlhorn")
    T = nx.Graph(T)
    changed = True
    while changed:                                   # strong pruning of unprofitable leaves
        changed = False
        for n in sorted(T.nodes):
            if n not in seeds and T.degree(n) == 1:
                (m,) = T.neighbors(n)
                if prizes.get(n, 0) < G[n][m]["cost"]:
                    T.remove_node(n)
                    changed = True
    if T.number_of_nodes() == 0:
        T.add_nodes_from(seeds)
    return set(T.nodes), sorted(G[u][v]["eid"] for u, v in T.edges)


def label(n, kg=None):
    """Short display name: trials as 'ACRONYM (NCT…)' - without a registry acronym, the curated trial name of a
    one-trial program (e.g. 'METREO') - others by name (max 90 characters)."""
    if n["category"] == "biolink:ClinicalTrial":
        name = n.get("acronym")
        if not name and kg is not None:
            progs = [e["subject"] for e in kg.inn(n["id"], "tmkg:has_trial")]
            if len(progs) == 1 and len(kg.out(progs[0], "tmkg:has_trial")) == 1:
                name = (kg.nodes[progs[0]].get("name") or "").split(" — ", 1)[-1] or None
        return f"{name or 'trial'} ({n['id'][4:]})"
    return (n.get("name") or n["id"])[:90]


def facts(kg, edge_ids):
    out = []
    for eid in edge_ids:
        e = kg.edges[eid]
        s, o = kg.nodes[e["subject"]], kg.nodes[e["object"]]
        props = {k: o[k] for k in SHOW if o.get(k) not in (None, "")}
        if "analyses" in props:                      # keep analyses compact but verbatim
            props["analyses"] = json.dumps([{k: a.get(k) for k in ("groups", "param_type", "param_value", "ci_lower",
                                                                    "ci_upper", "p_value", "method")}
                                            for a in json.loads(props["analyses"])])
        out.append(f"[{eid}] {label(s, kg)} ({s['category']}) -{e['predicate']}-> "
                   f"{label(o, kg)} ({o['category']}) | source: {e['source']}, {e['evidence_type']}"
                   + (f" | {json.dumps(props, ensure_ascii=False)}" if props else ""))
    return out


# roles that reason about THIS trial / program: prizes only for the seeds and their direct neighbours, so an
# unrelated program's trial that shares a disease node is not pulled in (it can still connect the tree)
FOCUSED = {"failure_diagnostician", "population_stratifier", "redesign_advisor"}


def subgraph_for(kg, G, seeds, role, max_terminals=25):
    weights = ROLE_PRIZES[role]
    scope = None
    if role in FOCUSED:
        scope = {s for s in seeds if s in G} | {m for s in seeds if s in G for m in G.neighbors(s)}
    prizes = {n: weights.get(d["category"], 0) if scope is None or n in scope else 0 for n, d in G.nodes(data=True)}
    nodes, edges = pcst(G, prizes, seeds, max_terminals)
    return Subgraph(role, sorted(seeds), sorted(nodes), edges, facts(kg, edges))
