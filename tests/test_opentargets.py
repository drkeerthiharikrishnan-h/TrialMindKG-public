"""Tests for pipelines/opentargets.py. Offline: uses a saved real API response (Open Targets 26.09)."""
import json
from pathlib import Path
import opentargets as ot
import pandas as pd
import seed_kg as sk

FIX = Path(__file__).parent / "fixtures" / "opentargets_drug_moa_CHEMBL1888176.json"


def test_parse_trospium_mechanisms():
    moa = ot.parse_mechanisms(json.loads(FIX.read_text()))
    by_symbol = {m["symbol"]: m for m in moa}
    assert set(by_symbol) == {"CHRM3", "CHRM2"}
    assert by_symbol["CHRM3"]["ensembl"] == "ENSG00000133019"
    assert by_symbol["CHRM3"]["uniprot"] == ["P20309"]          # Swiss-Prot only
    assert by_symbol["CHRM3"]["action_type"] == "ANTAGONIST"
    assert by_symbol["CHRM3"]["pmids"] == ["15723094", "19029429"]  # de-duplicated


def test_parse_drug_without_mechanisms():
    assert ot.parse_mechanisms({"drug": {"id": "X", "mechanismsOfAction": None}}) == []
    assert ot.parse_mechanisms({"drug": None}) == []


REPORT = sk.OUT / "resolution_report.csv"


def test_fallback_recorded_in_report():
    r = pd.read_csv(REPORT)
    row = r[(r.entity_type == "mechanism") & (r["query"] == "trospium")].iloc[0]
    assert row["rule"] == "open_targets_fallback" and row["identifier"] == "CHRM3;CHRM2"   # sorted by Ensembl id
    assert r[r.entity_type == "mechanism"]["rule"].str.endswith("NEEDS_REVIEW").sum() == 5


def test_summarise_evidence_counts_only_what_is_there():
    import opentargets as ot
    rows = [dict(datasourceId="eva", score=0.9, target={"id": "ENSG1"}, variantRsId="rs2", studyId="RCV0002",
                 clinicalSignificances=["pathogenic"], literature=["11"], directionOnTrait="risk",
                 variantFunctionalConsequence={"label": "missense_variant"}, mutatedSamples=None),
            dict(datasourceId="eva", score=0.5, target={"id": "ENSG1"}, variantRsId="rs1", studyId="RCV0001",
                 clinicalSignificances=["benign"], literature=[], directionOnTrait=None,
                 variantFunctionalConsequence=None, mutatedSamples=None),
            dict(datasourceId="cancer_gene_census", score=1, target={"id": "ENSG1"}, variantRsId=None, studyId=None,
                 clinicalSignificances=[], literature=["9"], directionOnTrait=None, variantFunctionalConsequence=None,
                 mutatedSamples=[{"numberMutatedSamples": 5, "numberSamplesTested": 50},
                                 {"numberMutatedSamples": 9, "numberSamplesTested": 900}])]
    s = ot.summarise_evidence(rows)
    eva, cgc = s[("ENSG1", "eva")], s[("ENSG1", "cancer_gene_census")]
    assert eva["n_evidence"] == 2 and eva["clinical_significance"] == {"benign": 1, "pathogenic": 1}
    assert eva["clinvar_records"] == ["RCV0001", "RCV0002"] and eva["variants"] == ["rs1", "rs2"]
    assert eva["max_score"] == 0.9 and eva["direction"] == {"risk": 1}
    assert cgc["mutated_samples"] == [9, 900] and cgc["clinvar_records"] == []


def test_pharmgkb_annotations_parse_and_sort():
    import pharmgkb
    from snapshot import Response

    class Snap:
        def get(self, source, url, params=None):
            return Response({"status": "success", "data": [
                {"accessionId": "PA2", "levelOfEvidence": {"term": "3"}, "types": ["Toxicity"],
                 "location": {"displayName": "rs1", "genes": [{"symbol": "NOS3"}]},
                 "relatedDiseases": [{"name": "CAD"}], "relatedChemicals": [{"name": "clopidogrel"}], "score": 2.0},
                {"accessionId": "PA1", "levelOfEvidence": {"term": "1A"}, "types": ["Efficacy"],
                 "location": {"displayName": "CYP2C19*2", "genes": [{"symbol": "CYP2C19"}]},
                 "relatedChemicals": [{"name": "clopidogrel"}]}]}, "t", "k", source)
    _, rows = pharmgkb.annotations(Snap(), "Clopidogrel")
    assert [r["level"] for r in rows] == ["1A", "3"] and rows[1]["genes"] == ["NOS3"] and rows[1]["diseases"] == ["CAD"]

    class NoRecord:
        def get(self, source, url, params=None):
            return Response(None, "t", "k", source)                 # HTTP 404 stored as body None
    assert pharmgkb.annotations(NoRecord(), "semaglutide")[1] == []


def test_gene_ontology_keeps_experimental_biological_process_only():
    import opentargets as ot
    from snapshot import Response

    class Snap:
        def post(self, source, url, body, headers=None, store_client_errors=True):
            return Response({"data": {"target": {"id": "ENSG1", "geneOntology": [
                {"aspect": "P", "evidence": "IDA", "source": "PMID:1", "term": {"id": "GO:0000001", "label": "a"}},
                {"aspect": "P", "evidence": "IMP", "source": "PMID:2", "term": {"id": "GO:0000001", "label": "a"}},
                {"aspect": "P", "evidence": "IEA", "source": "GO_REF:1", "term": {"id": "GO:0000002", "label": "b"}},
                {"aspect": "F", "evidence": "IDA", "source": "PMID:3", "term": {"id": "GO:0000003", "label": "c"}}]}}},
                "t", "k", source)
    _, rows = ot.gene_ontology(Snap(), "ENSG1")
    assert rows == [dict(go="GO:0000001", label="a", evidence=["IDA", "IMP"], sources=["PMID:1", "PMID:2"])]


def test_gwas_keeps_genome_wide_hits_and_stops_paging():
    import gwas
    from snapshot import Response
    calls = []

    class Snap:
        def get(self, source, url, params=None):
            calls.append(params["page"])
            return Response({"_embedded": {"associations": [
                {"p_value": 1e-20, "efo_traits": [{"efo_id": "MONDO_0004979", "efo_trait": "asthma"}],
                 "snp_effect_allele": ["rs1-A"], "accession_id": "GCST1", "pubmed_id": "1"},
                {"p_value": 1e-5, "efo_traits": [{"efo_id": "EFO_0004842", "efo_trait": "eosinophil count"}],
                 "accession_id": "GCST2", "pubmed_id": "2"}]}, "page": {"totalPages": 5}}, "t", "k", source)
    _, rows = gwas.associations(Snap(), "IL5")
    assert rows == [dict(p_value=1e-20, traits=[("MONDO:0004979", "asthma")], snps=["rs1-A"], study="GCST1", pmid="1")]
    assert calls == [0]                     # sorted by p: a non-significant row ends the paging


def test_disease_phenotypes_keep_curated_positive_hpo_only():
    import opentargets as ot
    from snapshot import Response

    class Snap:
        def post(self, source, url, body, headers=None, store_client_errors=True):
            return Response({"data": {"disease": {"id": "MONDO_1", "phenotypes": {"count": 3, "rows": [
                {"phenotypeHPO": {"id": "HP_0002483", "name": "Bulbar signs"},
                 "evidence": [{"resource": "HPO", "qualifierNot": False, "evidenceType": "PCS", "frequencyHPO": None}]},
                {"phenotypeHPO": {"id": "HP_0000007", "name": "Autosomal recessive inheritance"},
                 "evidence": [{"resource": "HPO", "qualifierNot": False, "evidenceType": "IEA"}]},
                {"phenotypeHPO": {"id": "HP_0001250", "name": "Seizure"},
                 "evidence": [{"resource": "HPO", "qualifierNot": True, "evidenceType": "PCS"}]}]}}}}, "t", "k", source)
    _, rows = ot.disease_phenotypes(Snap(), "MONDO_1")
    assert rows == [dict(hp="HP:0002483", name="Bulbar signs", evidence=["PCS"], frequencies=[])]
