"""Tests for pipelines/ot_releases.py (offline: fake directory listings)."""
import ot_releases as otr

HTML = ('<a href="?C=N;O=D">Name</a><a href="/pub/databases/opentargets/platform/">Parent</a>'
        '<a href="etl/">etl/</a><a href="part-0001.parquet">p</a><a href="_SUCCESS">s</a>')


def test_parse_listing_skips_parent_and_sort_links():
    assert otr.parse_listing(HTML) == (["etl"], ["part-0001.parquet", "_SUCCESS"])


def test_norm_matches_old_and_new_folder_names():
    assert otr.norm("associationByOverallDirect") == otr.norm("association_overall_direct")
    assert otr.norm("association_by_datasource_direct") != otr.norm("association_overall_direct")


class FakeResp:
    def __init__(self, text):
        self.body = {"text": text}


class FakeSnap:
    TREE = {"R/": '<a href="input/">i</a><a href="output/">o</a>',
            "R/output/": '<a href="etl/">e</a>',
            "R/output/etl/": '<a href="parquet/">p</a>',
            "R/output/etl/parquet/": '<a href="associationByOverallDirect/">a</a>',
            "R/output/etl/parquet/associationByOverallDirect/": '<a href="part-0.parquet">p</a>',
            "R/input/": '<a href="association_overall_direct/">decoy</a>'}

    def get_text(self, source, url):
        return FakeResp(self.TREE.get(url, ""))


def test_find_dataset_searches_nested_folders_and_skips_input():
    assert otr.find_dataset(FakeSnap(), "R/", "association_overall_direct") == \
        "R/output/etl/parquet/associationByOverallDirect/"
    assert otr.find_dataset(FakeSnap(), "R/", "evidence_eva") is None


def test_yearly_plan_covers_2016_to_2026():
    assert list(otr.YEARLY) == list(range(2016, 2027)) and otr.YEARLY[2026] == [otr.LATEST]


def test_manifest_merges(tmp_path):
    m = tmp_path / "m.tsv"
    otr.write_manifest([dict(release="26.09", dataset="a", url="u", files=1, bytes=2)], m)
    otr.write_manifest([dict(release="16.04", dataset="a", url="u", files=1, bytes=3)], m)
    assert m.read_text().splitlines()[1].startswith("16.04\ta")


class FakeSnap2(FakeSnap):
    # 23.06-style: output/metadata/<name>/ holds only metadata, the data is deeper under output/etl/json/;
    # 26.09-style: intermediate/target/ comes before output/target/
    TREE = {"R/": '<a href="intermediate/">i</a><a href="output/">o</a>',
            "R/intermediate/": '<a href="target/">t</a>',
            "R/intermediate/target/": '<a href="part-0.parquet">p</a>',
            "R/output/": '<a href="metadata/">m</a><a href="etl/">e</a><a href="target/">t</a>',
            "R/output/target/": '<a href="part-0.zstd.parquet">p</a>',
            "R/output/metadata/": '<a href="associationByOverallDirect/">a</a>',
            "R/output/metadata/associationByOverallDirect/": '<a href="_SUCCESS">s</a>',
            "R/output/etl/": '<a href="json/">j</a>',
            "R/output/etl/json/": '<a href="associationByOverallDirect/">a</a>',
            "R/output/etl/json/associationByOverallDirect/": '<a href="part-0.json">p</a>'}


def test_find_dataset_skips_metadata_and_intermediate_folders():
    assert otr.find_dataset(FakeSnap2(), "R/", "association_overall_direct") == \
        "R/output/etl/json/associationByOverallDirect/"
    assert otr.find_dataset(FakeSnap2(), "R/", "target") == "R/output/target/"


def test_old_association_file_also_looks_in_output(monkeypatch):
    monkeypatch.setattr(otr, "BASE", "B/")

    class S(FakeSnap):
        TREE = {"B/19.06/": '<a href="output/">o</a>',
                "B/19.06/output/": '<a href="19.06_association_data.json.gz">a</a>'}
    assert otr.old_association_file(S(), "19.06") == "B/19.06/output/19.06_association_data.json.gz"
