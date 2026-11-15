"""
Turn an OBO Graphs JSON ontology file (hp.json, go-basic.json, mondo.json from scripts/download_latest_layers.sh)
into the small tables the KG builder reads, for the L2 hierarchy cards (sources/hpo_hierarchy.yaml,
go_hierarchy.yaml, mondo_hierarchy.yaml; EXECUTION_PLAN.md Phase 1d).

Usage:   python pipelines/ontology_prepare.py                 (all three: hpo, go, mondo)
         python pipelines/ontology_prepare.py go mondo        (a subset)
Reads:   data/external/hpo/hp.json, data/external/go/go-basic.json, data/external/mondo/mondo.json
Writes:  data/ontologies/<name>/terms.tsv.gz      id, name, namespace            (git-ignored)
         data/ontologies/go/terms_<namespace>.tsv.gz   the same split by GO aspect (node tables of the GO card)
         data/ontologies/<name>/relations.tsv.gz  subject, relation, object, subject_namespace, object_namespace
         data/ontologies/<name>/version.txt       the release named inside the file (version IRI)
         outputs/<name>_hierarchy/prepare.md      counts (committed, so a new release shows up in git)

Only the ontology's own terms are kept (HP:, GO:, MONDO: - imported terms from other ontologies are dropped),
deprecated terms are dropped with every relation touching them, and only is_a and part_of relations are kept.
GO 'namespace' is biological_process / molecular_function / cellular_component; HP and MONDO have one namespace.
"""
import gzip
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OBO = "http://purl.obolibrary.org/obo/"
PART_OF = OBO + "BFO_0000050"
NAMESPACE_PRED = "http://www.geneontology.org/formats/oboInOwl#hasOBONamespace"
ONTOLOGIES = {
    "hpo": {"file": "data/external/hpo/hp.json", "prefix": "HP"},
    "go": {"file": "data/external/go/go-basic.json", "prefix": "GO"},
    "mondo": {"file": "data/external/mondo/mondo.json", "prefix": "MONDO"},
}


def curie(iri, prefix):
    """'http://purl.obolibrary.org/obo/HP_0000118' -> 'HP:0000118' when the IRI belongs to `prefix`, else None."""
    if not iri.startswith(OBO + prefix + "_"):
        return None
    return prefix + ":" + iri[len(OBO) + len(prefix) + 1:]


def parse(graph, prefix):
    """Takes one OBO Graphs graph dict and the ontology's prefix. Returns (terms, relations, version):
    terms = DataFrame(id, name, namespace) of live terms with that prefix; relations = DataFrame(subject,
    relation, object, subject_namespace, object_namespace) with relation is_a or part_of, both ends live terms;
    version = the version IRI named in the file (or '')."""
    terms = {}
    for n in graph.get("nodes", []):
        cid = curie(n.get("id", ""), prefix)
        if cid is None or n.get("type", "CLASS") != "CLASS":
            continue
        meta = n.get("meta") or {}
        if meta.get("deprecated"):
            continue
        ns = next((p.get("val") for p in meta.get("basicPropertyValues", []) if p.get("pred") == NAMESPACE_PRED),
                  prefix.lower())
        terms[cid] = {"id": cid, "name": n.get("lbl", ""), "namespace": ns}
    rels = []
    for e in graph.get("edges", []):
        rel = {"is_a": "is_a", PART_OF: "part_of"}.get(e.get("pred"))
        s, o = curie(e.get("sub", ""), prefix), curie(e.get("obj", ""), prefix)
        if rel is None or s not in terms or o not in terms:
            continue
        rels.append({"subject": s, "relation": rel, "object": o,
                     "subject_namespace": terms[s]["namespace"], "object_namespace": terms[o]["namespace"]})
    version = (graph.get("meta") or {}).get("version", "")
    t = pd.DataFrame(list(terms.values()), columns=["id", "name", "namespace"]).sort_values("id", ignore_index=True)
    r = (pd.DataFrame(rels, columns=["subject", "relation", "object", "subject_namespace", "object_namespace"])
         .drop_duplicates().sort_values(["subject", "relation", "object"], ignore_index=True))
    return t, r, version


def convert(json_path, prefix, out_dir):
    """Takes the OBO Graphs file, the prefix and an output folder. Writes terms.tsv.gz, relations.tsv.gz and
    version.txt there. Returns a report dict with the counts."""
    with open(json_path, encoding="utf-8") as f:
        doc = json.load(f)
    graphs = doc.get("graphs") or []
    own = next((g for g in graphs if any(curie(n.get("id", ""), prefix) for n in g.get("nodes", [])[:50])),
               graphs[0] if graphs else {})
    terms, rels, version = parse(own, prefix)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = [("terms", terms), ("relations", rels)]
    if terms["namespace"].nunique() > 1:                    # GO: one term table per aspect, for the node specs
        tables += [(f"terms_{ns}", grp) for ns, grp in terms.groupby("namespace")]
    for name, df in tables:
        with gzip.GzipFile(out_dir / f"{name}.tsv.gz", "wb", mtime=0) as gz:
            gz.write(df.to_csv(sep="\t", index=False).encode())
    (out_dir / "version.txt").write_text(version + "\n")
    return {"prefix": prefix, "version": version, "n_terms": len(terms), "n_relations": len(rels),
            "by_relation": rels["relation"].value_counts().to_dict() if len(rels) else {},
            "by_namespace": terms["namespace"].value_counts().to_dict() if len(terms) else {}}


def report_markdown(name, rep):
    """Report dict -> markdown with the counts."""
    lines = [f"# {name}: ontology tables for the hierarchy card", "",
             f"Release (version IRI in the file): {rep['version'] or 'not stated'}",
             f"Live {rep['prefix']}: terms: {rep['n_terms']:,}; is_a / part_of relations: {rep['n_relations']:,}", "",
             "| relation | rows |", "|---|---|"]
    lines += [f"| {k} | {v:,} |" for k, v in sorted(rep["by_relation"].items())]
    lines += ["", "| namespace | terms |", "|---|---|"]
    lines += [f"| {k} | {v:,} |" for k, v in sorted(rep["by_namespace"].items())]
    return "\n".join(lines) + "\n"


def main(names):
    for name in names:
        cfg = ONTOLOGIES[name]
        src = ROOT / cfg["file"]
        if not src.exists():
            print(f"{name}: {src} not found (download it on the Mac with scripts/download_latest_layers.sh)")
            continue
        rep = convert(src, cfg["prefix"], ROOT / "data" / "ontologies" / name)
        out = ROOT / "outputs" / f"{name}_hierarchy"
        out.mkdir(parents=True, exist_ok=True)
        (out / "prepare.md").write_text(report_markdown(name, rep))
        print(f"{name}: {rep['n_terms']:,} terms, {rep['n_relations']:,} relations ({rep['version'] or 'no version'})"
              f" -> data/ontologies/{name}/")


if __name__ == "__main__":
    main(sys.argv[1:] or list(ONTOLOGIES))
