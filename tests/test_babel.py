"""Tests for pipelines/babel.py with a fake snapshot (no network)."""
import babel
from kg_schema import KG
from snapshot import Response

BODY = {"ENSEMBL:ENSG00000142168": {"id": {"identifier": "NCBIGene:6647", "label": "SOD1"}, "type": ["biolink:Gene"],
                                    "equivalent_identifiers": [{"identifier": "NCBIGene:6647"}, {"identifier": "HGNC:11179"},
                                                               {"identifier": "UMLS:C1420306"}]},
        "MESH:D000690": {"id": {"identifier": "MONDO:0004976", "label": "ALS"}, "type": ["biolink:Disease"],
                         "equivalent_identifiers": [{"identifier": "MONDO:0004976"}, {"identifier": "MEDDRA:10002026"}]},
        "MONDO:0004976": {"id": {"identifier": "MONDO:0004976", "label": "ALS"}, "type": ["biolink:Disease"],
                          "equivalent_identifiers": [{"identifier": "MONDO:0004976"}]},
        "EFO:0000001": None}


class FakeSnap:
    def post(self, source, url, body, headers=None, store_client_errors=True):
        return Response({c: BODY.get(c) for c in body["curies"]}, "2026-10-05T00:00:00Z", "k", source)


def test_to_babel_prefixes():
    assert babel.to_babel("CHEMBL:CHEMBL1117", "biolink:Drug") == "CHEMBL.COMPOUND:CHEMBL1117"
    assert babel.to_babel("CHEMBL:CHEMBL2311221", "biolink:Protein") is None       # ChEMBL targets are not compounds
    assert babel.to_babel("NCT:NCT01900665", "biolink:ClinicalTrial") is None


def test_annotate_adds_ids_drops_licensed_and_reports_unlinked_duplicates():
    kg = KG()
    kg.add_node("ENSEMBL:ENSG00000142168", "biolink:Gene")
    kg.add_node("MESH:D000690", "biolink:Disease")
    kg.add_node("MONDO:0004976", "biolink:Disease")
    kg.add_node("EFO:0000001", "biolink:Disease")
    n, asked, dups = babel.annotate(kg, FakeSnap())
    assert (n, asked) == (3, 4)
    g = kg.nodes["ENSEMBL:ENSG00000142168"]
    assert g["hgnc"] == "HGNC:11179" and g["ncbigene"] == "NCBIGene:6647" and "UMLS" not in g["equivalent_ids"]
    assert "MEDDRA" not in kg.nodes["MESH:D000690"]["equivalent_ids"]
    assert len(dups) == 1 and not dups[0]["already_linked"] and dups[0]["action"].startswith("REVIEW")
    kg.add_edge("MESH:D000690", "biolink:exact_match", "MONDO:0004976", "MONDO", "x", "database", "2026-10-05")
    assert babel.annotate(kg, FakeSnap())[2][0]["already_linked"]
