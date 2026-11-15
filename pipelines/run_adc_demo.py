"""
Run the TUB-040 internal + external demo end to end.

    python pipelines/run_adc_demo.py

1. Pulls the EXTERNAL layer live: ClinicalTrials.gov, ChEMBL, Open Targets, GTEx, openFDA FAERS.
2. Loads the INTERNAL layer: by default the SYNTHETIC tables in data/synthetic/.
   To use real tables, put them in data/internal/ (never committed) with the same file names.
3. Builds the joined graph and writes the demo-query answer.

Every API response is frozen in data/snapshots_adc_demo/ (TMKG_SNAPSHOT_MODE=offline rebuilds without network).
Outputs: outputs/demo_query_TUB040.md, outputs/adc/ (graph CSVs, Neptune files, manifest),
outputs/faers_adc_class.csv, gtex_slc34a2.csv, adc_literature_evidence.csv
"""
import os, sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seed_kg as sk                      # shared API helpers (responses frozen via snapshot.py)
import adc_demo as ad
import opentargets
from snapshot import Snapshot

# own snapshot folder, so pruning the seed build never deletes the demo's stored responses
sk.SNAP = Snapshot(sk.ROOT / "data" / "snapshots_adc_demo")

ROOT, OUT = sk.ROOT, sk.OUT
OT = "https://api.platform.opentargets.org/api/v4/graphql"
GTEX = "https://gtexportal.org/api/v2"
FDA = "https://api.fda.gov/drug/event.json"
TARGET_ENSEMBL = "ENSG00000157765"        # SLC34A2 / NaPi2b

DRUGS = {  # name -> (payload, payload class)
    "upifitamab rilsodotin": ("auristatin F-HPA (dolaflexin)", "microtubule inhibitor"),
    "lifastuzumab vedotin": ("MMAE", "microtubule inhibitor"),
    "TUB-040": ("topoisomerase-I inhibitor (Tubutecan, DAR 8)", "TOP1 inhibitor"),
    "mirvetuximab soravtansine": ("DM4", "microtubule inhibitor"),
    "trastuzumab deruxtecan": ("DXd", "TOP1 inhibitor"),
    "sacituzumab govitecan": ("SN-38", "TOP1 inhibitor"),
    "datopotamab deruxtecan": ("DXd", "TOP1 inhibitor"),
    "enfortumab vedotin": ("MMAE", "microtubule inhibitor"),
}
FAERS_NAMES = {"trastuzumab deruxtecan": "fam-trastuzumab deruxtecan"}
TRIALS = {"NCT03319628": "upifitamab rilsodotin", "NCT05329545": "upifitamab rilsodotin",
          "NCT04907968": "upifitamab rilsodotin", "NCT01991210": "lifastuzumab vedotin",
          "NCT06303505": "TUB-040", "NCT07743541": "TUB-040", "NCT07736937": "TUB-040", "NCT07806773": "TUB-040"}
FAERS_TERMS = {"ILD/pneumonitis": '("interstitial lung disease" OR "pneumonitis")',
               "neutropenia": '("neutropenia" OR "febrile neutropenia" OR "neutrophil count decreased")',
               "ocular (keratopathy/vision)": '("keratopathy" OR "vision blurred" OR "visual impairment")'}


def fetch_open_targets():
    """-> (overall score, genetic_association score, Response) for SLC34A2 x pulmonary alveolar microlithiasis."""
    q = """query($id:String!){ target(ensemblId:$id){ associatedDiseases(page:{index:0,size:25}){
           rows{ disease{id name} score datatypeScores{id score} } } } }"""
    r = opentargets.query(sk.SNAP, q, {"id": TARGET_ENSEMBL})
    rows = r.body["data"]["target"]["associatedDiseases"]["rows"]
    pam = next(x for x in rows if x["disease"]["id"] == "MONDO_0009928")   # pulmonary alveolar microlithiasis
    return pam["score"], next(d["score"] for d in pam["datatypeScores"] if d["id"] == "genetic_association"), r


def fetch_gtex():
    """-> (median expression per tissue, sorted high to low; Response of the expression call)."""
    g = sk.get(f"{GTEX}/reference/gene", {"geneId": "SLC34A2", "gencodeVersion": "v26",
               "genomeBuild": "GRCh38/hg38"})["data"][0]["gencodeId"]
    r = sk.fetch(f"{GTEX}/expression/medianGeneExpression", {"gencodeId": g, "datasetId": "gtex_v8"})
    df = (pd.DataFrame(r.body["data"]).sort_values(["median", "tissueSiteDetailId"], ascending=[False, True])
          [["tissueSiteDetailId", "median", "unit"]])
    df.to_csv(OUT / "gtex_slc34a2.csv", index=False, lineterminator="\n")
    return df, r


def fetch_faers():
    """-> table of FAERS report counts per ADC; `retrieved`/`snapshot_key` come from the base-count response."""
    def total(search):
        r = sk.fetch(FDA, {"search": search, "limit": 1})
        return (r.body or {}).get("meta", {}).get("results", {}).get("total", 0), r
    rows = []
    for name in ["trastuzumab deruxtecan", "sacituzumab govitecan", "datopotamab deruxtecan",
                 "mirvetuximab soravtansine", "enfortumab vedotin"]:
        base = f'patient.drug.openfda.generic_name:"{FAERS_NAMES.get(name, name)}"'
        n, r = total(base)
        row = dict(adc=name, payload=DRUGS[name][1], reports=n)
        for k, t in FAERS_TERMS.items():
            row[k] = total(f"{base} AND patient.reaction.reactionmeddrapt:{t}")[0]
            row[k + " %"] = round(100 * row[k] / row["reports"], 1) if row["reports"] else None
        row.update(retrieved=r.fetched_at, snapshot_key=r.key)
        rows.append(row)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "faers_adc_class.csv", index=False, lineterminator="\n")
    return df


def fetch_drugs():
    out = {}
    for name, (payload, pclass) in DRUGS.items():
        r = sk.resolve_drug(name)
        exact = bool(r) and r["match"].startswith("exact")
        mech = sk.mechanisms(r["chembl_id"]) if exact else []
        napi = [m for m in mech if "phosphate transport protein 2B" in (m["target_name"] or "")]
        ev, when, key = "literature", ad.LITERATURE_RETRIEVED, None
        if napi:
            src, rec = "ChEMBL", f"ChEMBL mechanism {napi[0]['mec_id']}"
            ev, when, key = "curated", napi[0]["fetched_at"], napi[0]["key"]
        elif name == "upifitamab rilsodotin":
            src, rec = "curated literature (ChEMBL has no mechanism record)", ad.LITERATURE[0]["source"]
        elif name == "TUB-040":
            src, rec = "curated: sponsor press release (no verified ChEMBL record)", ad.LITERATURE[6]["source"]
        else:
            src, rec = "", ""
        out[name] = dict(id=f"CHEMBL:{r['chembl_id']}" if exact else f"tmkg:{name.replace(' ', '_')}",
                         payload=payload, payload_class=pclass,
                         target="NaPi2b" if (napi or name in ("upifitamab rilsodotin", "TUB-040")) else None,
                         target_source=src, target_record=rec, target_evidence=ev, target_retrieved=when,
                         target_key=key,
                         id_note="ChEMBL exact match" if exact else "no verified ChEMBL record; project identifier")
    return out


def fetch_trials():
    out = {}
    for nct, drug in TRIALS.items():
        r = sk.fetch(f"{sk.CT}/{nct}", {"fields": sk.CT_FIELDS + ",WhyStopped,LeadSponsorName"})
        p = (r.body or {}).get("protocolSection", {})
        out[nct] = dict(drug=drug, retrieved=r.fetched_at, key=r.key,
                        title=p.get("identificationModule", {}).get("briefTitle"),
                        status=p.get("statusModule", {}).get("overallStatus"),
                        why=p.get("statusModule", {}).get("whyStopped", ""),
                        sponsor=p.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name"))
    return out


def load_internal():
    """-> (tables dict, folder used). Real tables in data/internal/ win over the SYNTHETIC ones."""
    real = ROOT / "data" / "internal"
    src = real if (real / "SYN_ADSL.csv").exists() or (real / "ADSL.csv").exists() else ROOT / "data" / "synthetic"
    T = {}
    for k in ["ADSL", "ADBM", "ADRS", "ADPC", "ADTTE", "ADAE"]:
        f = src / f"{k}.csv" if (src / f"{k}.csv").exists() else src / f"SYN_{k}.csv"
        T[k] = pd.read_csv(f)
    for b in ["PRIPARPI", "PRIBEV", "PRIFRADC", "BRCAMUT"]:
        T["ADSL"][b] = T["ADSL"][b].astype(str).str.lower().eq("true")
    T["ADRS"]["CONFIRMED"] = T["ADRS"]["CONFIRMED"].astype(str).str.lower().eq("true")
    return T, src


def internal_date(src):
    """Date of the internal tables: commit date for the SYNTHETIC set, else the newest file's date."""
    if "synthetic" in str(src):
        return ad.SYNTHETIC_COMMITTED
    import datetime
    newest = max(f.stat().st_mtime for f in Path(src).glob("*.csv"))
    return datetime.datetime.fromtimestamp(newest, datetime.timezone.utc).strftime("%Y-%m-%d")


def main(out_dir=None):
    """Builds the ADC demo; returns the export manifest. out_dir defaults to outputs/ (graph in outputs/adc/)."""
    from kg_schema import validate, biolink_annotate
    from export import export_all
    out = Path(out_dir) if out_dir else OUT
    print("1/4 Fetching external evidence (registry, ChEMBL, Open Targets, GTEx, FAERS)...")
    pam_score, pam_genetic, ot_r = fetch_open_targets()
    gtex, gtex_r = fetch_gtex()
    faers = fetch_faers()
    drugs, trials = fetch_drugs(), fetch_trials()
    ext = dict(pam_score=pam_score, pam_genetic=pam_genetic, gtex_lung=float(gtex.iloc[0]["median"]),
               gtex_n=len(gtex), gtex_2nd=(gtex.iloc[1]["tissueSiteDetailId"], float(gtex.iloc[1]["median"])),
               faers=faers, drugs=drugs, trials=trials, ot_retrieved=ot_r.fetched_at, ot_key=ot_r.key,
               gtex_retrieved=gtex_r.fetched_at, gtex_key=gtex_r.key)
    if gtex.iloc[0]["tissueSiteDetailId"] != "Lung":
        print("   NOTE: lung is no longer the top GTEx tissue; review the lung-safety statement.")

    print("2/4 Loading internal tables...")
    T, src = load_internal()
    synthetic = "synthetic" in str(src)
    print(f"   using {src.relative_to(ROOT)}  ({'SYNTHETIC' if synthetic else 'REAL - confidential'})")

    print("3/4 Analysing and building the joined graph...")
    s = ad.analyse_internal(T); s["summary"] = s
    G = ad.build_graph(ext, dict(summary=s, retrieved=internal_date(src), synthetic=synthetic))
    import provenance
    problems = provenance.annotate(G, sk.SNAP)
    problems += validate(biolink_annotate(G), "adc")
    if problems:
        raise SystemExit("ADC GRAPH FAILED VALIDATION - nothing written:\n  " + "\n  ".join(problems[:50]))
    versions = {"Open Targets Platform": opentargets.release(sk.SNAP), "GTEx": "gtex_v8 (GTEx Portal API v2)",
                "openFDA FAERS": "drug/event counts (responses frozen in snapshot)",
                "ChEMBL / ClinicalTrials.gov": "responses frozen in snapshot",
                "curated literature": f"adc_demo.LITERATURE as of {ad.LITERATURE_RETRIEVED}",
                "internal layer": f"{src.relative_to(ROOT)} ({'SYNTHETIC' if synthetic else 'REAL'})",
                "infores registry": "biolink/information-resource-registry infores_catalog.yaml (frozen in snapshot)"}
    report = pd.DataFrame(columns=["program", "entity_type", "query", "resolved", "identifier", "rule",
                                   "n_candidates", "detail"])
    manifest = export_all(G, report, versions, sk.SNAP, out / "adc", profile="adc")
    pd.DataFrame(ad.LITERATURE).to_csv(out / "adc_literature_evidence.csv", index=False, lineterminator="\n")

    print("4/4 Answering the demo query...")
    asof = max(e["retrieved"] for e in G.edges.values())[:10]
    hdr = (f"# TrialMindKG demo query - TUB-040 (NaPi2b ADC)\n\n*Evidence as of {asof} (newest source record in "
           f"the graph). Internal layer: {src.relative_to(ROOT)}"
           f"{' - SYNTHETIC, not sponsor data' if synthetic else ''}.*\n\n")
    (out / "demo_query_TUB040.md").write_text(hdr + ad.demo_query(G, ext, s))
    c = manifest["counts"]
    print(f"Done: graph {c['nodes']} nodes / {c['edges']} edges -> {out}/adc/, {out}/demo_query_TUB040.md")
    return manifest


if __name__ == "__main__":
    main()
