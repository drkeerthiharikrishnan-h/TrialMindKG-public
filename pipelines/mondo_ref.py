"""
MONDO reference tables, extracted once from data/external/mondo/mondo.owl.

    python pipelines/mondo_ref.py        # regenerate data/reference/mondo_*.tsv

Writes small, committed tables, so builds are repeatable without the 240 MB OWL file:
  mondo_mesh_exact.tsv  mondo_id, mesh_id    - ONLY xrefs MONDO marks as MONDO:equivalentTo
  mondo_terms.tsv       mondo_id, label, exact_synonyms ('|'-separated); obsolete terms excluded
  mondo_umls_exact.tsv, mondo_doid_exact.tsv, mondo_efo_exact.tsv, mondo_orphanet_exact.tsv
                        <source id>, mondo_id, ambiguous (Y when the source id xrefs more than one MONDO term);
                        the source cards use them (skip_ambiguous) to land Pharos / Open Targets / ChEMBL diseases
                        on one MONDO node
Line 1 of each file records the MONDO release (owl:versionIRI) it came from.

Functions
- extract(owl_path, out_dir) -> MONDO version string; writes both tables
- load(ref_dir)              -> dict(version, mesh_to_mondo{mesh: [mondo]}, terms{mondo: (label, [syn])},
                                     name_index{normalised name: sorted [mondo]})
- norm_name(text)            -> lower-case alphanumerics only (used for exact name matching)
"""
import re, sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWL = ROOT / "data" / "external" / "mondo" / "mondo.owl"
REF = ROOT / "data" / "reference"

NS = {"owl": "http://www.w3.org/2002/07/owl#", "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
      "rdfs": "http://www.w3.org/2000/01/rdf-schema#", "oio": "http://www.geneontology.org/formats/oboInOwl#"}
T = {k: "{%s}" % v for k, v in NS.items()}
PREFIX = "http://purl.obolibrary.org/obo/MONDO_"


def norm_name(text):
    """'Alzheimer´s Disease' -> 'alzheimersdisease'."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _mondo(uri):
    return "MONDO:" + uri[len(PREFIX):] if uri and uri.startswith(PREFIX) else None


def extract(owl_path=OWL, out_dir=REF):
    version, terms, equiv = "unknown", {}, set()
    umls_xrefs, doid_xrefs = {}, {}      # UMLS:Cxxx -> MONDO, DOID:xxx -> MONDO (from hasDbXref on Class)
    efo_xrefs, orphanet_xrefs = {}, {}   # EFO:xxx -> MONDO, Orphanet:xxx -> MONDO (Open Targets / ChEMBL disease ids)
    for _, el in ET.iterparse(owl_path, events=("end",)):
        if el.tag == T["owl"] + "Ontology":
            v = el.find("owl:versionIRI", NS)
            version = v.get(T["rdf"] + "resource") if v is not None else version
        elif el.tag == T["owl"] + "Class":
            mid = _mondo(el.get(T["rdf"] + "about"))
            dep = el.find("owl:deprecated", NS)
            if mid and not (dep is not None and dep.text == "true"):
                label = el.findtext("rdfs:label", default="", namespaces=NS)
                syns = sorted({s.text for s in el.findall("oio:hasExactSynonym", NS) if s.text})
                terms[mid] = (label, syns)
                for x in el.findall("oio:hasDbXref", NS):
                    val = x.text or ""
                    if val.startswith("UMLS:"):
                        umls_xrefs.setdefault(val, []).append(mid)
                    elif val.startswith("DOID:"):
                        doid_xrefs.setdefault(val, []).append(mid)
                    elif val.startswith("EFO:"):
                        efo_xrefs.setdefault(val, []).append(mid)
                    elif val.startswith("Orphanet:"):
                        orphanet_xrefs.setdefault(val, []).append(mid)
            el.clear()
        elif el.tag == T["owl"] + "Axiom":
            prop = el.find("owl:annotatedProperty", NS)
            tgt = el.findtext("owl:annotatedTarget", default="", namespaces=NS)
            if (prop is not None and prop.get(T["rdf"] + "resource", "").endswith("#hasDbXref")
                    and tgt.startswith("MESH:")
                    and any(s.text == "MONDO:equivalentTo" for s in el.findall("oio:source", NS))):
                mid = _mondo(el.find("owl:annotatedSource", NS).get(T["rdf"] + "resource"))
                if mid:
                    equiv.add((mid, tgt))
            el.clear()
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "mondo_mesh_exact.tsv", "w") as f:
        f.write(f"# source: {version} (xrefs annotated MONDO:equivalentTo)\nmondo_id\tmesh_id\n")
        for m, x in sorted(e for e in equiv if e[0] in terms):
            f.write(f"{m}\t{x}\n")
    # UMLS -> MONDO (1:1 xrefs from non-obsolete classes; ambiguous mappings written but flagged)
    with open(out_dir / "mondo_umls_exact.tsv", "w") as f:
        f.write(f"# source: {version} (hasDbXref on non-obsolete classes)\numls_id\tmondo_id\tambiguous\n")
        for u in sorted(umls_xrefs):
            mondos = sorted(umls_xrefs[u])
            for m in mondos:
                f.write(f"{u}\t{m}\t{'Y' if len(mondos) > 1 else ''}\n")
    # DOID -> MONDO (same approach)
    with open(out_dir / "mondo_doid_exact.tsv", "w") as f:
        f.write(f"# source: {version} (hasDbXref on non-obsolete classes)\ndoid_id\tmondo_id\tambiguous\n")
        for d in sorted(doid_xrefs):
            mondos = sorted(doid_xrefs[d])
            for m in mondos:
                f.write(f"{d}\t{m}\t{'Y' if len(mondos) > 1 else ''}\n")
    # EFO -> MONDO and Orphanet -> MONDO (same approach): Open Targets and ChEMBL name diseases by these ids, so
    # the mapping lets their edges land on the Pharos (MONDO) disease node instead of a twin node beside it
    for fname, col, xrefs in (("mondo_efo_exact.tsv", "efo_id", efo_xrefs),
                              ("mondo_orphanet_exact.tsv", "orphanet_id", orphanet_xrefs)):
        with open(out_dir / fname, "w") as f:
            f.write(f"# source: {version} (hasDbXref on non-obsolete classes)\n{col}\tmondo_id\tambiguous\n")
            for k in sorted(xrefs):
                mondos = sorted(xrefs[k])
                for m in mondos:
                    f.write(f"{k}\t{m}\t{'Y' if len(mondos) > 1 else ''}\n")
    with open(out_dir / "mondo_terms.tsv", "w") as f:
        f.write(f"# source: {version} (non-obsolete classes)\nmondo_id\tlabel\texact_synonyms\n")
        for m in sorted(terms):
            label, syns = terms[m]
            f.write(f"{m}\t{label}\t{'|'.join(syns)}\n")
    return version


def load(ref_dir=REF):
    def rows(name):
        lines = (ref_dir / name).read_text().splitlines()
        return lines[0].split("source: ", 1)[1].split(" ")[0], [l.split("\t") for l in lines[2:]]
    version, mesh_rows = rows("mondo_mesh_exact.tsv")
    _, term_rows = rows("mondo_terms.tsv")
    mesh_to_mondo, terms, name_index = {}, {}, {}
    for m, x in mesh_rows:
        mesh_to_mondo.setdefault(x, []).append(m)
    for m, label, syns in term_rows:
        syn = [s for s in syns.split("|") if s]
        terms[m] = (label, syn)
        for n in [label] + syn:
            name_index.setdefault(norm_name(n), set()).add(m)
    # UMLS -> MONDO and DOID -> MONDO (1:1 only; skip ambiguous)
    umls_to_mondo, doid_to_mondo = {}, {}
    if (ref_dir / "mondo_umls_exact.tsv").exists():
        for row in rows("mondo_umls_exact.tsv")[1]:
            u, m, amb = row[0], row[1], row[2] if len(row) > 2 else ""
            if not amb:
                umls_to_mondo[u] = m
    if (ref_dir / "mondo_doid_exact.tsv").exists():
        for row in rows("mondo_doid_exact.tsv")[1]:
            d, m, amb = row[0], row[1], row[2] if len(row) > 2 else ""
            if not amb:
                doid_to_mondo[d] = m
    return dict(version=version, mesh_to_mondo=mesh_to_mondo, terms=terms,
                name_index={k: sorted(v) for k, v in name_index.items()},
                umls_to_mondo=umls_to_mondo, doid_to_mondo=doid_to_mondo)


if __name__ == "__main__":
    v = extract(Path(sys.argv[1]) if len(sys.argv) > 1 else OWL)
    print(f"Wrote {REF}/mondo_mesh_exact.tsv and mondo_terms.tsv from {v}")
