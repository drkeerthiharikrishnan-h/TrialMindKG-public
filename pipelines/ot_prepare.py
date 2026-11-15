"""
Convert Open Targets 26.09 parquet files (data/external/opentargets/26.09/<dataset>/*.parquet)
into the TSV tables the KG builder engine reads (data/external/opentargets/tables/<name>.tsv.gz).

Run after: python pipelines/ot_releases.py latest
Produces:  associations.tsv.gz  (overall target-disease scores)
           indications.tsv.gz   (drug-disease clinical indications with max phase)
           targets.tsv.gz       (gene id and symbol)
           genetic_evidence.tsv.gz (per-datasource genetic/somatic evidence rows)
"""
import gzip, sys
from pathlib import Path

try:
    import pyarrow.parquet as pq
except ImportError:
    sys.exit("pyarrow is required: pip install pyarrow")

ROOT = Path(__file__).resolve().parents[1]
OT_DIR = ROOT / "data" / "external" / "opentargets" / "26.09"
OUT = ROOT / "data" / "external" / "opentargets" / "tables"

EVIDENCE_DATASOURCES = [
    "eva", "eva_somatic", "gene_burden", "orphanet", "gene2phenotype",
    "clingen", "cancer_gene_census", "intogen", "uniprot_variants", "uniprot_literature",
]


def read_parquet_dir(path):
    """Read all parquet files in a folder into one pyarrow table."""
    files = sorted(path.glob("*.parquet"))
    if not files:
        return None
    return pq.read_table(files[0] if len(files) == 1 else path)


def write_tsv(df, name):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"{name}.tsv.gz"
    with gzip.open(p, "wt") as f:
        df.to_csv(f, sep="\t", index=False)
    print(f"  {name}: {len(df):,} rows -> {p}")
    return p


def prepare_associations():
    """Overall direct target-disease association scores."""
    import pandas as pd
    d = OT_DIR / "association_overall_direct"
    t = read_parquet_dir(d)
    if t is None:
        print(f"  associations: {d} not found, skipping")
        return
    df = t.to_pandas()
    score_cols = [c for c in df.columns if c.endswith("Score") or c == "score"]
    keep = ["targetId", "diseaseId"] + score_cols
    keep = [c for c in keep if c in df.columns]
    df = df[keep].copy()
    # add disease names from the disease dataset if available
    dis_dir = OT_DIR / "disease"
    dis_t = read_parquet_dir(dis_dir)
    if dis_t is not None:
        dis = dis_t.to_pandas()[["id", "name"]].drop_duplicates("id")
        df = df.merge(dis.rename(columns={"id": "diseaseId", "name": "diseaseName"}), on="diseaseId", how="left")
    write_tsv(df, "associations")


def prepare_targets():
    """Gene id and approved symbol."""
    import pandas as pd
    d = OT_DIR / "target"
    t = read_parquet_dir(d)
    if t is None:
        print(f"  targets: {d} not found, skipping")
        return
    df = t.to_pandas()
    keep = [c for c in ["id", "approvedSymbol", "approvedName", "biotype"] if c in df.columns]
    df = df[keep].rename(columns={"id": "ensemblId"}).drop_duplicates("ensemblId")
    write_tsv(df, "targets")


def prepare_indications():
    """Drug-disease clinical indications with max phase."""
    import pandas as pd
    # clinical_indication parquet: drugId, efoId (list), maxPhase or similar
    ind_dir = OT_DIR / "clinical_indication"
    mol_dir = OT_DIR / "drug_molecule"
    dis_dir = OT_DIR / "disease"
    t = read_parquet_dir(ind_dir)
    if t is None:
        print(f"  indications: {ind_dir} not found, skipping")
        return
    df = t.to_pandas()
    # The schema may vary; normalise to drugId, diseaseId, maxPhase
    if "efoId" in df.columns:
        df = df.rename(columns={"efoId": "diseaseId"})
    if "indication" in df.columns and "diseaseId" not in df.columns:
        # some OT versions nest indication.efoId and indication.maxPhase
        df["diseaseId"] = df["indication"].apply(lambda x: x.get("efoId") if isinstance(x, dict) else None)
        df["maxPhase"] = df["indication"].apply(lambda x: x.get("maxPhase") if isinstance(x, dict) else None)
    # if diseaseId is a list, explode
    if df["diseaseId"].apply(lambda x: isinstance(x, list)).any():
        df = df.explode("diseaseId")
    # drug names from molecule dataset
    mol_t = read_parquet_dir(mol_dir)
    if mol_t is not None:
        mol = mol_t.to_pandas()
        name_col = "name" if "name" in mol.columns else "drugName" if "drugName" in mol.columns else None
        id_col = "id" if "id" in mol.columns else "drugId" if "drugId" in mol.columns else None
        if name_col and id_col:
            mol = mol[[id_col, name_col]].drop_duplicates(id_col).rename(columns={id_col: "drugId", name_col: "drugName"})
            df = df.merge(mol, on="drugId", how="left")
    # disease names
    dis_t = read_parquet_dir(dis_dir)
    if dis_t is not None:
        dis = dis_t.to_pandas()[["id", "name"]].drop_duplicates("id")
        df = df.merge(dis.rename(columns={"id": "diseaseId", "name": "diseaseName"}), on="diseaseId", how="left")
    keep = [c for c in ["drugId", "diseaseId", "maxPhase", "drugName", "diseaseName"] if c in df.columns]
    df = df[keep].dropna(subset=["drugId", "diseaseId"])
    write_tsv(df, "indications")


def prepare_genetic_evidence():
    """Per-datasource genetic evidence rows."""
    import pandas as pd
    frames = []
    for ds in EVIDENCE_DATASOURCES:
        d = OT_DIR / f"evidence_{ds}"
        t = read_parquet_dir(d)
        if t is None:
            continue
        edf = t.to_pandas()
        if "datasourceId" not in edf.columns:
            edf["datasourceId"] = ds
        keep = [c for c in ["targetId", "diseaseId", "datasourceId", "score", "clinicalSignificances",
                            "variantRsId", "studyId", "directionOnTrait", "literature"] if c in edf.columns]
        # flatten variantFunctionalConsequence.label if present
        if "variantFunctionalConsequence" in edf.columns:
            edf["variantConsequenceLabel"] = edf["variantFunctionalConsequence"].apply(
                lambda x: x.get("label") if isinstance(x, dict) else None)
            keep.append("variantConsequenceLabel")
        # clinicalSignificances may be a list
        for col in ["clinicalSignificances", "literature"]:
            if col in edf.columns:
                edf[col] = edf[col].apply(lambda x: "|".join(str(v) for v in x) if isinstance(x, list) else x)
        frames.append(edf[keep])
        print(f"    {ds}: {len(edf):,} rows")
    if not frames:
        print("  genetic_evidence: no evidence datasets found, skipping")
        return
    df = pd.concat(frames, ignore_index=True)
    # add disease names
    dis_dir = OT_DIR / "disease"
    dis_t = read_parquet_dir(dis_dir)
    if dis_t is not None:
        dis = dis_t.to_pandas()[["id", "name"]].drop_duplicates("id")
        df = df.merge(dis.rename(columns={"id": "diseaseId", "name": "diseaseName"}), on="diseaseId", how="left")
    write_tsv(df, "genetic_evidence")


if __name__ == "__main__":
    print("Preparing Open Targets 26.09 tables for the KG builder...")
    prepare_associations()
    prepare_targets()
    prepare_indications()
    prepare_genetic_evidence()
    print("Done.")
