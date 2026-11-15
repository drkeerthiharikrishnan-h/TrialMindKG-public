"""Tests for pipelines/drug_names.py with a fake snapshot (no network)."""
import drug_names as dn
import nim, resolver as rv
from snapshot import Response

BODIES = {
    "pubchem": {"InformationList": {"Information": [{"CID": 1, "Synonym": ["Lanabecestat", "AZD-3293", "LY3314814"]}]}},
    "gsrs": {"content": [{"_name": "LANABECESTAT", "names": [{"name": "AZD3293"}, {"name": "LANABECESTAT [USAN]"}]},
                         {"_name": "OTHER", "names": [{"name": "AZD-3293 CAMSYLATE"}]}]},
    "nameres": [{"label": "AZD-4547", "synonyms": ["AZD4547", "FEXAGRATINIB"]}],      # fuzzy wrong hit
}


class FakeSnap:
    def get(self, source, url, params=None):
        return Response(BODIES[source], "2026-10-05T00:00:00Z", "k", source)


def test_other_names_exact_only():
    r = dn.other_names(FakeSnap(), "AZD-3293")
    assert r["pubchem"] == ["LY3314814", "Lanabecestat"]                  # query itself removed
    assert "LANABECESTAT" in r["gsrs"] and "OTHER" not in r["gsrs"]        # only substances naming it exactly
    assert r["nameres"] == []                                              # fuzzy hit for another code dropped


def test_outage_means_channel_unused():
    class Down:
        def get(self, *a, **k):
            raise TimeoutError
    assert dn.other_names(Down(), "AZD-3293") == {"pubchem": [], "gsrs": [], "nameres": []}


def test_external_names_rank_first_only_on_exact_chembl_name():
    mols = [dict(chembl_id="CHEMBL3989948", parent="CHEMBL3989948", pref_name="LANABECESTAT", synonyms=[],
                 syn_types={}, molecule_type="Small molecule", max_phase=3.0)]
    res = rv.Resolver(nim.NIM(backend="offline"), rv.build_index(), dictionary=mols)
    ext = dn.other_names(FakeSnap(), "AZD-3293")
    top = res.rank("AZD-3293", external=ext)[0]
    assert top["chembl_id"] == "CHEMBL3989948" and top["method"].startswith("external+exact")
    assert res.external_named("AZD-3293", {"pubchem": ["NOT A CHEMBL NAME"]}, []) == []
