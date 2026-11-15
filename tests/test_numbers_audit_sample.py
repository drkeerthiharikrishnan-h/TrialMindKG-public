"""S08: the Numbers page reads the separate 500 + 500 audit table (outputs/pharos/audit_sample_result.md)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import numbers_page as N  # noqa: E402

TABLE = """# Independent audit sample: result (machine raters only)

| pruner verdict | rows | unsupported (95% interval) | supported | cannot tell |
|---|---|---|---|---|
| prune | 500 | 459 = 91.8% (89.1% to 93.9%) | 30 | 11 |
| keep | 500 | 275 = 55.0% (50.6% to 59.3%) | 216 | 9 |

| raters | rows | agree | Cohen's kappa |
|---|---|---|---|
| all three | 1000 | 874 (87.4%) | n/a |
"""


def _root(tmp_path, text):
    p = tmp_path / N.AUDIT_RESULT
    p.parent.mkdir(parents=True)
    p.write_text(text, encoding="utf-8")
    return tmp_path


def test_reads_both_rows_and_agreement(tmp_path):
    a = N.audit_sample([_root(tmp_path, TABLE)])
    assert (a["pruned_n"], a["pruned_unsupported"], a["kept_n"], a["kept_unsupported"]) == (500, 459, 500, 275)
    assert (a["rows"], a["rows_all_agree"]) == (1000, 874)
    assert "459 of the 500 pruned links (91.8%, 95% interval 89.1% to 93.9%)" in a["sentence"]
    assert "275 of the 500 kept links (55.0%, 50.6% to 59.3%)" in a["sentence"]
    assert "no pass rule" in a["sentence"] and a["file"] == "audit_sample_result.md"


def test_no_internal_terms_in_the_sentence(tmp_path):
    s = N.audit_sample([_root(tmp_path, TABLE)])["sentence"]
    for bad in ("outputs/", ".md", "G0", "G2", "G3", "nemotron", "mistral", "infores"):
        assert bad not in s


def test_missing_file_or_table_gives_none(tmp_path):
    assert N.audit_sample([tmp_path]) is None
    assert N.audit_sample([_root(tmp_path, "# result\n\nnothing scored yet\n")]) is None


def test_pruning_evidence_carries_it_and_the_file_is_cited():
    assert "audit_sample" in N.pruning_evidence(ROOT)
    assert N.OTHER_FILES["audit_sample_result.md"] == N.AUDIT_RESULT


def test_committed_result_is_read():
    if not (ROOT / N.AUDIT_RESULT).is_file():
        return
    a = N.audit_sample([ROOT])
    assert a is not None and a["pruned_n"] + a["kept_n"] == a["rows"]


# ------------------------------------------------------------------------------------------------ FIX2 F2 / F3
FAIL_FILE = """# Blinded spot check: result (Amendment 1)

Rule (pre-registered): PASS if >= 60 of the pruned edges are judged unsupported AND <= 15 of the kept edges are judged unsupported.

- Pruned edges judged unsupported: **41 of 50** (threshold >= 60)
- Kept edges judged unsupported: **30 of 50** (threshold <= 15)
- Result: **FAIL**

Raters: A = `model-a`; B = `model-b`; C = `model-c`.

Owner's blind labels against the machine label (majority of the three raters) on the rows she rated: 4 of 20 agree (20.0%), Cohen's kappa 0.01.
The machine labels were NOT checked against a blind human subset to the pre-registered level (32 of 40 agreeing rows).

## Per source (final labels)

| pruner verdict | source | rows | unsupported | share (95% interval) | supported | cannot tell |
|---|---|---|---|---|---|---|
| prune | STRINGDB (infores:string) | 40 | 33 | 82.5% (68.0% to 91.3%) | 7 | 0 |
| prune | TIN-X (infores:pharos) | 2 | 1 | 50.0% (9.5% to 90.5%) | 1 | 0 |
| keep | TIN-X (infores:pharos) | 25 | 10 | 40.0% (23.4% to 59.3%) | 15 | 0 |
| keep | STRINGDB (infores:string) | 20 | 16 | 80.0% (58.4% to 91.9%) | 4 | 0 |
"""
RULE = "RULE: prune_unsupported_min=60; keep_unsupported_max=15\nSample: 50 pruned + 50 kept\n"


def _spot_root(tmp_path, result=FAIL_FILE):
    (tmp_path / N.SPOTCHECK_RULE).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / N.SPOTCHECK_RULE).write_text(RULE, encoding="utf-8")
    if result is not None:
        (tmp_path / N.SPOTCHECK_RESULT).write_text(result, encoding="utf-8")
    return tmp_path


def test_spot_check_on_a_fail_file_states_the_counts_and_the_owner_agreement(tmp_path):
    sc = N.spot_check([_spot_root(tmp_path)])
    assert sc["result"] == "FAIL" and (sc["pruned_unsupported"], sc["kept_unsupported"]) == (41, 30)
    assert (sc["owner_agree"], sc["owner_rows"], sc["owner_needed"], sc["raters"]) == (4, 20, 32, 3)
    s = sc["sentence"]
    assert ": FAIL. 41 of the 50 pruned and 30 of the 50 kept links were judged unsupported." in s
    assert ("Three automated raters labelled the rows; on the 20 rows the scientist rated blind, she agreed with them on 4, "
            "so the raters' labels are not confirmed by a human.") in s
    assert sc["files"] == ["spotcheck_rule.md", "spotcheck_blind_result.md"]
    for bad in (".md", "outputs/", "model-a", "infores"):
        assert bad not in s


def test_spot_check_before_the_result_and_with_an_old_result_file(tmp_path):
    assert "no result is shown yet" in N.spot_check([_spot_root(tmp_path, None)])["sentence"]
    old = "- Result: **PASS**\n"                                                     # a file without the count lines
    sc = N.spot_check([_spot_root(tmp_path, old)])
    assert sc["sentence"].endswith(": PASS.") and "owner_agree" not in sc


def test_source_mix_sentence_is_computed_from_both_tables(tmp_path):
    spot = N.per_source(FAIL_FILE)
    assert spot[("prune", N.STRING_SOURCE)] == (40, 33, 7) and spot[("keep", N.TEXT_MINED_SOURCE)] == (25, 10, 15)
    audit = N.per_source(TABLE_WITH_SOURCES)
    s = N.source_mix_sentence(spot, audit)
    assert s.startswith("Within protein-protein rows from STRING, pruned and kept rows are judged unsupported at similar rates "
                        "(spot check 33 of 40 pruned against 16 of 20 kept; audit 98 of 100 pruned against 47 of 50 kept)")
    assert "kept sample holding many more text-mined disease links (spot check 25 kept against 2 pruned; audit 60 kept against 5 pruned)" in s
    assert s.endswith("the IntAct comparison is the model-free check for those.")
    far = {**audit, ("keep", N.STRING_SOURCE): (50, 10, 40)}                     # 98% against 20%: not similar
    assert "at different rates" in N.source_mix_sentence(spot, far)
    assert N.source_mix_sentence({}, {}) == ""
    a = N.audit_sample([_spot_root(_root(tmp_path, TABLE_WITH_SOURCES))])
    assert "Within protein-protein rows from STRING" in a["sentence"] and "infores" not in a["sentence"]


TABLE_WITH_SOURCES = TABLE + """
## Per source (majority labels)

| pruner verdict | source | rows | unsupported | share (95% interval) | supported | cannot tell |
|---|---|---|---|---|---|---|
| prune | STRINGDB (infores:string) | 100 | 98 | 98.0% (93.0% to 99.4%) | 2 | 0 |
| prune | TIN-X (infores:pharos) | 5 | 2 | 40.0% (11.8% to 76.9%) | 3 | 0 |
| keep | TIN-X (infores:pharos) | 60 | 15 | 25.0% (15.8% to 37.2%) | 45 | 0 |
| keep | STRINGDB (infores:string) | 50 | 47 | 94.0% (83.8% to 97.9%) | 3 | 0 |
"""


def test_committed_spot_check_is_the_fail_with_its_numbers():
    sc = N.spot_check([ROOT])
    if sc is None or not sc["result"]:
        return
    assert sc["result"] == "FAIL" and f"{sc['pruned_unsupported']} of the {sc['pruned_rated']} pruned" in sc["sentence"]
    assert f"she agreed with them on {sc['owner_agree']}," in sc["sentence"]


def test_little_evidence_is_said_only_when_the_string_rows_are_rarely_supported():
    """Review of FIX2 (rule 7): the sentence follows the supported share of the STRING rows."""
    spot, audit = N.per_source(FAIL_FILE), N.per_source(TABLE_WITH_SOURCES)
    assert spot[("prune", N.STRING_SOURCE)] == (40, 33, 7)
    assert "little evidence for any STRING row" in N.source_mix_sentence(spot, audit)       # 7/40, 4/20, 2/100, 3/50
    many = {**audit, ("keep", N.STRING_SOURCE): (50, 10, 40)}                                 # 80% supported
    assert "little evidence" not in N.source_mix_sentence(spot, many)
