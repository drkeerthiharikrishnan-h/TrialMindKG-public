"""Novelty of the link predictions (CS-5, review finding M1, data half).

For every row of outputs/linkpred/predictions_metapath.tsv.gz: is the (subject, object) pair already stated by any edge
of the assembled graph (any layer, any predicate, same direction: the card's `known_in` rule in
pharos_app.PharosGraph.same_pair) or by an edge a layer's pruner marked 'prune' (G3 holds only kept edges; the card
still lists pruned ones), and is the predicted disease a MONDO ancestor (transitive biolink:subclass_of in the
mondo_hierarchy layer) of a disease already linked to that subject? Streams the edge file (gzip, one line at a time;
only the subject / predicate / object / layer / verdict columns are kept for the edges that matter).

    python scripts/predictions_novelty.py \
        --predictions outputs/linkpred/predictions_metapath.tsv.gz \
        --edges data/kg/assembled/g3/edges.tsv.gz \
        --mondo data/kg/mondo_hierarchy/edges.tsv.gz \
        --verdicts data/kg/pharos/edge_verdicts.tsv.gz,data/kg/opentargets/edge_verdicts.tsv.gz \
        --out outputs/linkpred/predictions_novelty.tsv.gz --md outputs/linkpred/predictions_novelty.md

Functions:
  read_predictions(path)                    -> list of dicts (paths column dropped)
  scan_edges(path, subjects, pairs)          -> dict(known_layers, known_levels, known_mondo, verdicts, products,
                                                reverse, n_edges)
  pruned_pairs(paths, pairs, subjects)       -> ({(s, o)} stated only by 'prune' rows, {s: {MONDO object}} of them)
  mondo_parents(path)                        -> {child: {parent}} from biolink:subclass_of MONDO -> MONDO edges
  ancestors(node, parents, memo)             -> frozenset of strict ancestors
  equivalent_genes(products)                 -> {gene: {other genes sharing a gene product}}
  flag(preds, scan, parents, pruned)         -> rows with known_in_any_layer, known_layers, known_levels,
                                                pruned_only, stated_reverse, ancestor_of_known
  node_counts(rows, node, top_n)             -> counts for rows involving one node (subject or object)
  summary_markdown(rows, scan, args, nodes)  -> markdown text
"""
import argparse
import csv
import gzip
import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

KEEP_COLS = ("graph", "task", "subject", "object", "score", "rank", "by", "model", "best_shape", "n_shapes")
NODES = ("NCBIGene:1956", "NCBIGene:5743", "NCBIGene:6647", "MONDO:0004975")
PRODUCT_PREDICATE = "biolink:has_gene_product"
ISA = "biolink:subclass_of"
csv.field_size_limit(sys.maxsize)


def _open(path):
    path = Path(path)
    return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", newline="") if path.suffix == ".gz" \
        else open(path, encoding="utf-8", newline="")


def read_predictions(path):
    """Takes the predictions TSV(.gz). Returns a list of dicts with KEEP_COLS (the heavy `paths` column is dropped)."""
    with _open(path) as fh:
        r = csv.DictReader(fh, delimiter="\t")
        return [{k: row.get(k, "") for k in KEEP_COLS} for row in r]


def scan_edges(path, subjects, pairs):
    """Takes an edge TSV(.gz) with columns subject, predicate, object, layer (verdict optional), the set of predicted
    subjects and the set of predicted (subject, object) pairs. One streaming pass. Returns dict:
    known_layers {(s, o): {layer}} for predicted pairs stated by an edge (same direction);
    known_mondo {s: {MONDO object}} for every edge from a predicted subject to a MONDO id;
    reverse {(s, o)} predicted pairs stated only as o -> s; products {gene: {product}} (has_gene_product edges);
    verdicts Counter over all edges; n_edges."""
    known_layers, known_levels = defaultdict(set), defaultdict(set)
    known_mondo, products = defaultdict(set), defaultdict(set)
    rev_hits, verdicts, n = set(), Counter(), 0
    flipped = {(o, s) for s, o in pairs}
    with _open(path) as fh:
        header = fh.readline().rstrip("\r\n").split("\t")
        ix = {c: header.index(c) for c in ("subject", "predicate", "object", "layer")}
        iv = header.index("verdict") if "verdict" in header else None
        il = header.index("knowledge_level") if "knowledge_level" in header else None
        need = max(list(ix.values()) + [i for i in (iv, il) if i is not None]) + 1
        for line in fh:
            f = line.rstrip("\r\n").split("\t", need)
            n += 1
            s, p, o = f[ix["subject"]], f[ix["predicate"]], f[ix["object"]]
            verdicts[f[iv] if iv is not None and len(f) > iv else ""] += 1
            if p == PRODUCT_PREDICATE:
                products[s].add(o)
            if s in subjects:
                if o.startswith("MONDO:"):
                    known_mondo[s].add(sys.intern(o))
                if (s, o) in pairs:
                    known_layers[(s, o)].add(f[ix["layer"]])
                    known_levels[(s, o)].add(f[il] if il is not None else "")
            if (s, o) in flipped:
                rev_hits.add((o, s))
    reverse = {k for k in rev_hits if k not in known_layers}
    return dict(known_layers=dict(known_layers), known_levels=dict(known_levels), known_mondo=dict(known_mondo),
                products=dict(products),
                reverse=reverse, verdicts=verdicts, n_edges=n)


def pruned_pairs(paths, pairs, subjects):
    """Takes layer verdict files (edge_verdicts.tsv.gz: subject, object, verdict), the predicted pairs and subjects.
    Returns (set of predicted pairs stated by a 'prune' row, {subject: {MONDO object of a 'prune' row}})."""
    hits, mondo = set(), defaultdict(set)
    for path in paths:
        with _open(path) as fh:
            header = fh.readline().rstrip("\r\n").split("\t")
            i_s, i_o, i_v = header.index("subject"), header.index("object"), header.index("verdict")
            need = max(i_s, i_o, i_v) + 1
            for line in fh:
                f = line.rstrip("\r\n").split("\t", need)
                if f[i_v] != "prune":
                    continue
                s, o = f[i_s], f[i_o]
                if (s, o) in pairs:
                    hits.add((s, o))
                if s in subjects and o.startswith("MONDO:"):
                    mondo[s].add(o)
    return hits, dict(mondo)


def mondo_parents(path):
    """Takes the mondo_hierarchy edge TSV(.gz). Returns {child: {parent}} for biolink:subclass_of edges between
    MONDO ids."""
    parents = defaultdict(set)
    with _open(path) as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            s, o = row["subject"], row["object"]
            if row["predicate"] == ISA and s.startswith("MONDO:") and o.startswith("MONDO:"):
                parents[s].add(o)
    return dict(parents)


def ancestors(node, parents, memo):
    """Strict ancestors of node (transitive closure of parents; cycles tolerated). memo caches results."""
    if node in memo:
        return memo[node]
    seen, stack = set(), list(parents.get(node, ()))
    while stack:
        x = stack.pop()
        if x in seen or x == node:
            continue
        seen.add(x)
        if x in memo:
            seen |= memo[x]
            continue
        stack.extend(parents.get(x, ()))
    memo[node] = frozenset(seen)
    return memo[node]


def equivalent_genes(products):
    """Takes {gene: {product}}. Returns {gene: {other genes with at least one shared product}} (e.g. an ENSEMBL gene and
    the NCBIGene gene of the same UniProtKB protein)."""
    by_product = defaultdict(set)
    for g, ps in products.items():
        for p in ps:
            by_product[p].add(g)
    out = defaultdict(set)
    for gs in by_product.values():
        if len(gs) > 1:
            for g in gs:
                out[g] |= gs - {g}
    return dict(out)


def flag(preds, scan, parents, pruned=(set(), {})):
    """Takes prediction rows, the scan_edges result, mondo_parents and the pruned_pairs result. Returns new rows with
    known_in_any_layer (yes/no: stated by a graph edge or by a pruned layer edge), known_layers and known_levels (comma
    lists, graph edges), pruned_only (yes/no: stated only by a pruned edge), stated_reverse (yes/no), ancestor_of_known
    (yes/no: the object is a strict MONDO ancestor of a MONDO disease the subject already has an edge to, kept or
    pruned)."""
    pruned_hits, pruned_mondo = pruned
    memo, anc_by_subject, out = {}, {}, []
    for r in preds:
        s, o = r["subject"], r["object"]
        if s not in anc_by_subject:
            anc = set()
            for d in set(scan["known_mondo"].get(s, ())) | set(pruned_mondo.get(s, ())):
                anc |= ancestors(d, parents, memo)
            anc_by_subject[s] = anc
        layers = sorted(scan["known_layers"].get((s, o), ()))
        levels = sorted(scan.get("known_levels", {}).get((s, o), ()))
        p_only = not layers and (s, o) in pruned_hits
        out.append(dict(r, known_in_any_layer="yes" if layers or p_only else "no", known_layers=",".join(layers),
                        known_levels=",".join(levels), pruned_only="yes" if p_only else "no",
                        stated_reverse="yes" if (s, o) in scan["reverse"] else "no",
                        ancestor_of_known="yes" if o in anc_by_subject[s] else "no"))
    return out


def node_counts(rows, node, top_n=20):
    """Counts for the rows involving node (as subject or object), unique (subject, object) pairs, and for the top_n
    pairs by score the card shows (pharos_app.predicted_links order: score desc, subject, object)."""
    mine = [r for r in rows if r["subject"] == node or r["object"] == node]
    best = {}
    for r in sorted(mine, key=lambda r: (-float(r["score"]), r["subject"], r["object"])):
        best.setdefault((r["subject"], r["object"]), r)
    uniq = list(best.values())
    top = uniq[:top_n]
    nov = lambda rs: sum(r["known_in_any_layer"] == "no" for r in rs)
    nov_na = lambda rs: sum(r["known_in_any_layer"] == "no" and r["ancestor_of_known"] == "no" for r in rs)
    return dict(node=node, rows=len(mine), pairs=len(uniq), novel=nov(uniq), novel_not_ancestor=nov_na(uniq),
                top=len(top), top_novel=nov(top), top_novel_not_ancestor=nov_na(top))


def _pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "-"


def summary_markdown(rows, scan, args, nodes=NODES):
    total = len(rows)
    pairs = {(r["subject"], r["object"]) for r in rows}
    novel = [r for r in rows if r["known_in_any_layer"] == "no"]
    novel_na = [r for r in novel if r["ancestor_of_known"] == "no"]
    novel_pairs = {(r["subject"], r["object"]) for r in novel}
    novel_na_pairs = {(r["subject"], r["object"]) for r in novel_na}
    known_anc = sum(r["known_in_any_layer"] == "yes" and r["ancestor_of_known"] == "yes" for r in rows)
    rev = sum(r["stated_reverse"] == "yes" for r in rows)
    layer_counts = Counter(l for r in rows for l in r["known_layers"].split(",") if l)
    level_sets = Counter(r["known_levels"] for r in rows if r["known_levels"])
    text_only = level_sets.get("text_co_occurrence", 0)
    p_only = sum(r["pruned_only"] == "yes" for r in rows)
    equiv = scan.get("equiv_known", 0)
    lines = [
        "# Novelty of the link predictions (CS-5, review finding M1)", "",
        f"Inputs: `{args.predictions}` (model {sorted({r['model'] for r in rows})}, graph {sorted({r['graph'] for r in rows})}); "
        f"edges `{args.edges}` ({scan['n_edges']:,} edges, every layer); MONDO is_a from `{args.mondo}`. "
        "Script `scripts/predictions_novelty.py`; per-row flags in `predictions_novelty.tsv.gz`.", "",
        "Rules: a pair is **known** when any edge of the graph states the same (subject, object) in the same direction, "
        "any layer, any predicate (the card's `known_in` rule, `pharos_app.PharosGraph.same_pair`), or when a layer's "
        f"pruner marked such an edge 'prune' (G3's edge file holds kept edges only; pruned rows read from "
        f"{', '.join(f'`{v}`' for v in args.verdicts.split(',') if v)}). "
        "**Ancestor of known**: the predicted disease is a strict MONDO ancestor (transitive `biolink:subclass_of`) of a "
        "MONDO disease the subject already has an edge to (a broad category, not a new disease).", "",
        "| | rows | unique (subject, object) pairs |", "|---|---|---|",
        f"| all predictions | {total:,} | {len(pairs):,} |",
        f"| known in at least one layer | {total - len(novel):,} ({_pct(total - len(novel), total)}) | {len(pairs) - len(novel_pairs):,} |",
        f"| **novel** (in no layer) | **{len(novel):,}** ({_pct(len(novel), total)}) | {len(novel_pairs):,} |",
        f"| novel and not an ancestor of a known disease | **{len(novel_na):,}** ({_pct(len(novel_na), total)}) | {len(novel_na_pairs):,} |",
        f"| known and ancestor of a known disease | {known_anc:,} | - |", "",
        f"- Known only through a pruned layer edge: {p_only:,} rows.",
        f"- Known rows whose graph edges are all text co-occurrence (TIN-X): {text_only:,} of {total - len(novel):,}. "
        "The predictor skips pairs already in its training graph, which drops text co-occurrence edges, so curated "
        "pairs are excluded upstream and the known pairs left are text-mined ones.",
        "- Known rows by the edges' knowledge levels: " + ", ".join(f"{k} {v:,}" for k, v in level_sets.most_common()) + ".",
        f"- Pairs stated only in the reverse direction (object -> subject): {rev:,} rows (counted as novel above).",
        f"- Novel rows whose subject shares a gene product (`biolink:has_gene_product`) with another gene id that already "
        f"has an edge to the disease (e.g. an ENSEMBL and an NCBIGene id of one gene): {equiv:,} (still counted as novel "
        f"above, by the card's exact-id rule).",
        "- Known rows by the edge file's layer code (a row can be in several): " + ", ".join(f"{k} {v:,}" for k, v in sorted(layer_counts.items())) + ".",
        "- Edge verdicts in the edge file: " + ", ".join(f"{k or '(none)'} {v:,}" for k, v in sorted(scan["verdicts"].items())) + ".",
        "", "## Per node (rows where the node is subject or object; card = top 20 unique pairs by score)", "",
        "| node | rows | unique pairs | novel | novel, not ancestor | card top 20: novel | card top 20: novel, not ancestor |",
        "|---|---|---|---|---|---|---|"]
    for n in nodes:
        c = node_counts(rows, n)
        lines.append(f"| {n} | {c['rows']:,} | {c['pairs']:,} | {c['novel']:,} | {c['novel_not_ancestor']:,} | "
                     f"{c['top_novel']} of {c['top']} | {c['top_novel_not_ancestor']} of {c['top']} |")
    lines += ["", "## Sentence for the start page (Claude Code, CC-30)", "",
              f"> The model proposes {len(novel_na):,} gene-disease links that no source in the graph states yet "
              f"(of {total:,} scored predictions: {total - len(novel):,} restate a pair the graph already holds, "
              f"{text_only:,} of them only as a text-mining co-mention, and {len(novel) - len(novel_na):,} name a "
              f"broader category of a disease already linked to the gene).", ""]
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--predictions", default="outputs/linkpred/predictions_metapath.tsv.gz")
    p.add_argument("--edges", default="data/kg/assembled/g3/edges.tsv.gz")
    p.add_argument("--mondo", default="data/kg/mondo_hierarchy/edges.tsv.gz")
    p.add_argument("--verdicts", default="data/kg/pharos/edge_verdicts.tsv.gz,data/kg/opentargets/edge_verdicts.tsv.gz",
                   help="comma-separated layer verdict files whose 'prune' rows also count as known")
    p.add_argument("--out", default="outputs/linkpred/predictions_novelty.tsv.gz")
    p.add_argument("--md", default="outputs/linkpred/predictions_novelty.md")
    args = p.parse_args(argv)
    preds = read_predictions(args.predictions)
    subjects = {r["subject"] for r in preds}
    pairs = {(r["subject"], r["object"]) for r in preds}
    print(f"predictions {len(preds):,} rows, {len(pairs):,} pairs, {len(subjects):,} subjects", flush=True)
    scan = scan_edges(args.edges, subjects, pairs)
    print(f"edges scanned {scan['n_edges']:,}; known pairs {len(scan['known_layers']):,}", flush=True)
    pruned = pruned_pairs([v for v in args.verdicts.split(",") if v], pairs, subjects)
    print(f"pairs stated by a pruned layer edge {len(pruned[0]):,}", flush=True)
    rows = flag(preds, scan, mondo_parents(args.mondo), pruned)
    # id-equivalence check (reported, not used to flip the flag): ENSEMBL subject vs genes sharing a product
    eq = equivalent_genes(scan["products"])
    need = {(g2, r["object"]) for r in rows if r["known_in_any_layer"] == "no" for g2 in eq.get(r["subject"], ())}
    if need:
        sub2 = {g for g, _ in need}
        scan2 = scan_edges(args.edges, sub2, need)
        hit = set(scan2["known_layers"])
        scan["equiv_known"] = sum(r["known_in_any_layer"] == "no" and any((g2, r["object"]) in hit
                                  for g2 in eq.get(r["subject"], ())) for r in rows)
    cols = list(KEEP_COLS) + ["known_in_any_layer", "known_layers", "known_levels", "pruned_only", "stated_reverse",
                              "ancestor_of_known"]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.out, "wt", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    Path(args.md).write_text(summary_markdown(rows, scan, args))
    print(json.dumps(dict(rows=len(rows), novel=sum(r["known_in_any_layer"] == "no" for r in rows),
                          novel_not_ancestor=sum(r["known_in_any_layer"] == "no" and r["ancestor_of_known"] == "no"
                                                 for r in rows), equiv_known=scan.get("equiv_known", 0))))


if __name__ == "__main__":
    sys.exit(main())
