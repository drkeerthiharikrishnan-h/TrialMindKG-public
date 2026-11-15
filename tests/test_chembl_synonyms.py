"""Tests for pipelines/chembl_synonyms.py on a tiny hand-written MySQL dump (no download needed)."""
import chembl_synonyms as cs

DUMP = """CREATE TABLE `molecule_dictionary` (
  `molregno` bigint NOT NULL,
  `pref_name` varchar(255) DEFAULT NULL,
  `chembl_id` varchar(20) NOT NULL,
  `max_phase` decimal(2,1) DEFAULT NULL,
  `molecule_type` varchar(30) DEFAULT NULL,
  PRIMARY KEY (`molregno`)
) ENGINE=InnoDB;
INSERT INTO `molecule_dictionary` VALUES (1,'IDARUBICIN','CHEMBL1117',4.0,'Small molecule'),(2,'IDARUBICIN HYDROCHLORIDE','CHEMBL1200976',4.0,'Small molecule'),(3,NULL,'CHEMBL999',NULL,NULL);
CREATE TABLE `molecule_hierarchy` (
  `molregno` bigint NOT NULL,
  `parent_molregno` bigint DEFAULT NULL,
  `active_molregno` bigint DEFAULT NULL
);
INSERT INTO `molecule_hierarchy` VALUES (1,1,1),(2,1,1);
CREATE TABLE `molecule_synonyms` (
  `molregno` bigint NOT NULL,
  `syn_type` varchar(50) NOT NULL,
  `molsyn_id` bigint NOT NULL,
  `res_stem_id` bigint DEFAULT NULL,
  `synonyms` varchar(250) DEFAULT NULL
);
INSERT INTO `molecule_synonyms` VALUES (2,'TRADE_NAME',10,NULL,'Idamycin'),(2,'TRADE_NAME',11,NULL,'Idamycin pfs'),(1,'RESEARCH_CODE',12,NULL,'NSC-256439'),(1,'OTHER',13,NULL,'it\\'s, (odd)');
"""


def test_parse_values_handles_quotes_commas_and_null():
    assert cs.parse_values("INSERT INTO `t` VALUES (1,'a\\'b, (c)',NULL),(2,'d',3.5);") == \
        [(1, "a'b, (c)", None), (2, "d", 3.5)]


def test_extract_and_load_roundtrip(tmp_path):
    out = tmp_path / "s.tsv.gz"
    assert cs.extract(DUMP.splitlines(True), "chembl_test", out) == 2        # CHEMBL999 has no name: skipped
    release, mols = cs.load(out)
    m = {x["chembl_id"]: x for x in mols}
    assert "chembl_test" in release
    assert m["CHEMBL1200976"]["parent"] == "CHEMBL1117"
    assert m["CHEMBL1200976"]["synonyms"] == ["Idamycin", "Idamycin pfs"]
    assert m["CHEMBL1117"]["syn_types"]["NSC-256439"] == ["RESEARCH_CODE"]
    assert "it's, (odd)" in m["CHEMBL1117"]["synonyms"]


def test_load_missing_table(tmp_path):
    assert cs.load(tmp_path / "none.tsv.gz") == (None, [])


def test_empty_dump_leaves_existing_table_untouched(tmp_path):
    # 2026-10-30: on the Mac a tar error fed an empty stream in and the committed table was overwritten with 0 rows.
    out = tmp_path / "s.tsv.gz"
    cs.extract(DUMP.splitlines(True), "chembl_test", out)
    before = out.read_bytes()
    assert cs.extract(["-- usage text from a failed tar, no INSERT lines\n"], "chembl_test", out) == 0
    assert out.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))
