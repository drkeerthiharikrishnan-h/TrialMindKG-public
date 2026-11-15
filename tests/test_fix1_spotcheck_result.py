"""FIX1 F1 (gate 1, G1-AC-01): the spot-check sentence reads only the scorer's own result line.

The scorer (scripts/score_spotcheck.py) writes "Rule (pre-registered): PASS if >= ..." BEFORE "- Result: **FAIL**"; the Numbers
page must never report PASS from the rule line."""
from pathlib import Path

from pipelines import numbers_page as npg

RULE = ("# rule\n\nSample: 100 pruned + 100 kept, stratified\n\n"
        "`RULE: prune_unsupported_min=60; keep_unsupported_max=15`\n")


def _root(tmp_path: Path, result_text=None) -> Path:
    (tmp_path / npg.SPOTCHECK_RULE).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / npg.SPOTCHECK_RULE).write_text(RULE, encoding="utf-8")
    if result_text is not None:
        (tmp_path / npg.SPOTCHECK_RESULT).write_text(result_text, encoding="utf-8")
    return tmp_path


def _scorer_text(verdict):
    return ("# Blinded spot check: result\n\n"
            "Rule (pre-registered): PASS if >= 60 of the pruned edges are judged unsupported AND <= 15 of the kept edges.\n\n"
            "- Pruned edges judged unsupported: **41 of 100** (threshold >= 60)\n"
            f"- Result: **{verdict}**\n")


def test_fail_result_reads_fail_even_with_the_rule_line_first(tmp_path):
    out = npg.spot_check([_root(tmp_path, _scorer_text("FAIL"))])
    assert out["result"] == "FAIL"
    assert out["sentence"].endswith(": FAIL.")
    assert "PASS." not in out["sentence"]


def test_pass_result_reads_pass(tmp_path):
    out = npg.spot_check([_root(tmp_path, _scorer_text("PASS"))])
    assert out["result"] == "PASS"
    assert out["sentence"].endswith(": PASS.")


def test_result_file_without_result_line_is_not_finished(tmp_path):
    # The rule line alone (it contains the word PASS) and stray words must not count as a result.
    text = "# Blinded spot check: result\n\nRule (pre-registered): PASS if >= 60 ... FAIL otherwise.\n"
    out = npg.spot_check([_root(tmp_path, text)])
    assert out["result"] is None
    assert "not finished" in out["sentence"]


def test_no_result_file_is_not_finished(tmp_path):
    out = npg.spot_check([_root(tmp_path)])
    assert out["result"] is None
    assert "not finished" in out["sentence"]


def test_real_scorer_report_round_trips(tmp_path):
    # The real report() of the scorer, so a change of its wording that breaks the reader fails here.
    import importlib.util
    import pandas as pd
    spec = importlib.util.spec_from_file_location("score_spotcheck", Path(__file__).resolve().parents[1] / "scripts" / "score_spotcheck.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    table = pd.DataFrame({"unsupported": [41, 9], "supported": [55, 88], "cannot tell": [4, 3], "blank": [0, 0], "invalid": [0, 0]}, index=["prune", "keep"])
    for passed, word in ((False, "FAIL"), (True, "PASS")):
        lines = mod.report(table, (60, 15), passed, 41, 9)
        out = npg.spot_check([_root(tmp_path, "\n".join(lines))])
        assert out["result"] == word
