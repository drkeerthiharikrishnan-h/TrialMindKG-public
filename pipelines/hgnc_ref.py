"""
Gene id cross-references from HGNC, as a small committed reference table, so every layer names a human gene the
same way (NCBIGene first, as Pharos does): Open Targets and the trial layer carry Ensembl ids, Pharos carries
NCBI Gene ids (3,369 targets only an Ensembl id), and without this table their gene nodes never join.

Usage:   python pipelines/hgnc_ref.py [path/to/hgnc_complete_set.txt]
Reads:   data/external/hgnc/hgnc_complete_set.txt (scripts/download_latest_layers.sh; the owner's Mac or the cloud)
Writes:  data/reference/hgnc_gene_ids.tsv (committed): one row per approved HGNC gene and UniProt accession:
         hgnc_id, symbol, ensembl_id (ENSG...), ncbigene_id (NCBIGene:...), uniprot_id (P...), locus_group
         Used by source cards as ref_file lookups (engine.build): ensembl_id -> ncbigene_id, uniprot_id -> ncbigene_id,
         and by make_graphs.py to rename the trial layer's ENSEMBL gene ids.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IN = ROOT / "data" / "external" / "hgnc" / "hgnc_complete_set.txt"
OUT = ROOT / "data" / "reference" / "hgnc_gene_ids.tsv"
COLS = ["hgnc_id", "symbol", "ensembl_id", "ncbigene_id", "uniprot_id", "locus_group"]


def gene_id_table(hgnc):
    """Takes the HGNC complete set as a DataFrame (columns hgnc_id, symbol, status, locus_group, entrez_id,
    ensembl_gene_id, uniprot_ids). Returns the reference table: approved genes only, NCBIGene: prefix on the
    Entrez id, one row per UniProt accession (the uniprot_ids cell holds 'P1|P2'; a gene without one keeps one row
    with an empty uniprot_id), sorted by hgnc_id then uniprot_id."""
    h = hgnc[hgnc["status"] == "Approved"].copy()
    for c in ("entrez_id", "ensembl_gene_id", "uniprot_ids"):
        h[c] = h[c].fillna("").astype(str).str.strip()
    h["ncbigene_id"] = h["entrez_id"].map(lambda v: f"NCBIGene:{v.split('.')[0]}" if v else "")
    h["uniprot_id"] = h["uniprot_ids"].str.split("|")
    h = h.explode("uniprot_id")
    h["uniprot_id"] = h["uniprot_id"].fillna("").str.strip()
    out = h.rename(columns={"ensembl_gene_id": "ensembl_id"})[COLS].fillna("")
    return out.sort_values(["hgnc_id", "uniprot_id"], ignore_index=True)


def load_lookup(key, value, path=OUT):
    """Takes two column names of the reference table. Returns {key: value} over rows where both are filled and the
    key is unique (an ambiguous key, e.g. one UniProt accession on two genes, is left out)."""
    ref = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
    ref = ref[(ref[key] != "") & (ref[value] != "")].drop_duplicates([key, value])
    ref = ref[~ref.duplicated(key, keep=False)]
    return dict(zip(ref[key], ref[value]))


def main(path=DEFAULT_IN):
    hgnc = pd.read_csv(path, sep="\t", dtype=str, low_memory=False)
    out = gene_id_table(hgnc)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, sep="\t", index=False)
    genes = out["hgnc_id"].nunique()
    print(f"{genes:,} approved genes, {len(out):,} rows; with NCBI Gene id {out.loc[out['ncbigene_id'] != '', 'hgnc_id'].nunique():,}, "
          f"with Ensembl id {out.loc[out['ensembl_id'] != '', 'hgnc_id'].nunique():,}, "
          f"UniProt accessions {out.loc[out['uniprot_id'] != '', 'uniprot_id'].nunique():,} -> {OUT}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_IN)
