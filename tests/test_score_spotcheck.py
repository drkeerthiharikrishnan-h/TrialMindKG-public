"""Tests for scripts/score_spotcheck.py with a small filled-in fixture sheet + key + rule."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import score_spotcheck as s  # noqa: E402

RULE_TEXT = "# rule\n\nsome prose\n\n`RULE: prune_unsupported_min=6; keep_unsupported_max=1`\n"


def _fixture(tmp_path, prune_j, keep_j):
    """10 pruned + 10 kept rows, interleaved; judgements given per verdict in row order."""
    verdicts = ["prune", "keep"] * 10
    pj, kj = iter(prune_j), iter(keep_j)
    judg = [next(pj) if v == "prune" else next(kj) for v in verdicts]
    sheet = pd.DataFrame({"row": range(1, 21), "subject_name": "x", "owner_judgement": judg, "notes": ""})
    xlsx = tmp_path / "sheet.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        sheet.to_excel(w, index=False, sheet_name="spot_check")
        pd.DataFrame({"item": ["seed"], "value": ["1"]}).to_excel(w, index=False, sheet_name="README")
    key = tmp_path / "key.tsv"
    pd.DataFrame({"row": range(1, 21), "verdict": verdicts, "confidence": "0.5", "reasons": "r"}).to_csv(
        key, sep="\t", index=False)
    rule = tmp_path / "rule.md"
    rule.write_text(RULE_TEXT)
    return xlsx, key, rule


def test_load_rule(tmp_path):
    p = tmp_path / "r.md"
    p.write_text(RULE_TEXT)
    assert s.load_rule(p) == (6, 1)
    p.write_text("no rule here\n")
    with pytest.raises(ValueError):
        s.load_rule(p)


def test_normalise():
    assert s.normalise(" Unsupported ") == "unsupported"
    assert s.normalise("Cannot  Tell") == "cannot tell"
    assert s.normalise("cannot_tell") == "cannot tell"
    assert s.normalise(None) == "blank" and s.normalise(float("nan")) == "blank"
    assert s.normalise("maybe") == "invalid"


def test_pass_case(tmp_path):
    xlsx, key, rule = _fixture(tmp_path, ["unsupported"] * 6 + ["supported", "cannot tell", "", "maybe"],
                               ["supported"] * 8 + ["unsupported", "cannot tell"])
    t = s.tally(s.read_sheet(xlsx), pd.read_csv(key, sep="\t", dtype=str))
    assert t.loc["prune", "unsupported"] == 6 and t.loc["prune", "supported"] == 1
    assert t.loc["prune", "cannot tell"] == 1 and t.loc["prune", "blank"] == 1 and t.loc["prune", "invalid"] == 1
    assert t.loc["keep", "unsupported"] == 1 and t.loc["keep", "supported"] == 8
    assert s.decide(t, s.load_rule(rule)) == (True, 6, 1)


def test_fail_cases(tmp_path):
    # 'cannot tell' counts as neither: 5 unsupported + 5 cannot tell is below 6
    xlsx, key, rule = _fixture(tmp_path, ["unsupported"] * 5 + ["cannot tell"] * 5, ["supported"] * 10)
    t = s.tally(s.read_sheet(xlsx), pd.read_csv(key, sep="\t", dtype=str))
    assert s.decide(t, s.load_rule(rule)) == (False, 5, 0)
    # enough pruned unsupported, but too many kept unsupported
    xlsx, key, rule = _fixture(tmp_path, ["unsupported"] * 10, ["unsupported"] * 2 + ["supported"] * 8)
    t = s.tally(s.read_sheet(xlsx), pd.read_csv(key, sep="\t", dtype=str))
    assert s.decide(t, s.load_rule(rule)) == (False, 10, 2)


def test_tally_rejects_mismatched_key(tmp_path):
    xlsx, key, _ = _fixture(tmp_path, ["supported"] * 10, ["supported"] * 10)
    k = pd.read_csv(key, sep="\t", dtype=str).iloc[:-1]
    with pytest.raises(ValueError):
        s.tally(s.read_sheet(xlsx), k)


def test_agreement(tmp_path):
    a = pd.DataFrame({"row": [1, 2, 3, 4], "judgement": ["supported", "unsupported", "cannot tell", "supported"]})
    b = pd.DataFrame({"row": [1, 2, 3], "judgement": ["supported", "supported", "supported"]})
    assert s.agreement(a, b) == (2, 1)


def test_report_and_main(tmp_path, capsys):
    xlsx, key, rule = _fixture(tmp_path, ["unsupported"] * 6 + ["supported"] * 4, ["supported"] * 10)
    out = tmp_path / "result.md"
    assert s.main(["--sheet", str(xlsx), "--key", str(key), "--rule", str(rule), "--sheet2", str(xlsx),
                   "--write", str(out)]) == 0
    text = capsys.readouterr().out
    assert "**6 of 10**" in text and "**0 of 10**" in text and "**PASS**" in text
    assert "| prune | 6 | 4 |" in text and "| keep | 0 | 10 |" in text
    assert "Second rater: 20 of 20" in text
    assert out.read_text().startswith("# Blinded spot check: result")


# ------------------------------------------------------------------ Amendment 1, addition A6: final labels and agreement
def _amend_fixture(tmp_path):
    """8 rows (prune, keep alternating). Raters: rows 1-4 unanimous, 5-6 two against one, 7-8 all differ."""
    trios = {1: ("unsupported",) * 3, 2: ("supported",) * 3, 3: ("unsupported",) * 3, 4: ("unsupported",) * 3,
             5: ("unsupported", "unsupported", "cannot tell"), 6: ("supported", "supported", "unsupported"),
             7: ("supported", "unsupported", "cannot tell"), 8: ("cannot tell", "supported", "unsupported")}
    labels = pd.DataFrame([{"row": r, "rater": k, "model": f"fam/{k}", "label": l} for r, t in trios.items() for k, l in zip("ABC", t)])
    key = pd.DataFrame({"row": range(1, 9), "verdict": ["prune", "keep"] * 4})
    sources = pd.DataFrame({"row": range(1, 9), "source": ["STRINGDB"] * 6 + ["TIN-X"] * 2})
    return labels, key, sources


def _judged(rows):
    return pd.DataFrame({"row": list(rows), "judgement": [s.normalise(j) for j in rows.values()]})


def test_wilson_and_kappa():
    lo, hi = s.wilson(5, 10)
    assert 0.23 < lo < 0.24 and 0.76 < hi < 0.77
    assert s.wilson(0, 10)[0] == 0.0 and s.wilson(10, 10)[1] > 0.9999 and all(x != x for x in s.wilson(0, 0))
    a = ["supported", "supported", "unsupported", "unsupported"]
    assert s.kappa(a, a) == 1.0 and s.kappa(a, ["supported", "unsupported", "supported", "unsupported"]) == 0.0
    assert abs(s.kappa(a, ["supported", "supported", "unsupported", "supported"]) - 0.5) < 1e-9
    assert s.kappa(["supported"] * 3, ["supported"] * 3) != s.kappa(["supported"] * 3, ["supported"] * 3)   # nan: chance agreement is 1
    assert s.kappa([], []) != s.kappa([], [])


def test_machine_table(tmp_path):
    labels, _, _ = _amend_fixture(tmp_path)
    m = s.machine_table(labels).set_index("row")
    assert m.loc[1, "proposal"] == "unsupported" and not m.loc[1, "flagged"] and m.loc[1, "machine"] == "unsupported"
    assert m.loc[5, "proposal"] == "" and m.loc[5, "flagged"] and m.loc[5, "majority"] == "unsupported"
    assert m.loc[7, "majority"] == "" and m.loc[7, "machine"] == "cannot tell"
    with pytest.raises(ValueError):
        s.machine_table(labels[~((labels["row"] == 3) & (labels["rater"] == "C"))])


def test_final_labels_follow_the_preregistered_order(tmp_path):
    labels, _, _ = _amend_fixture(tmp_path)
    m = s.machine_table(labels)
    owner = _judged({1: "supported", 5: "cannot tell", 2: ""})            # row 2 left blank: not rated
    adj = _judged({6: "unsupported", 1: "unsupported", 7: None})          # row 1 is hers already; row 7 left empty
    f = s.final_labels(m, owner, adj).set_index("row")
    assert (f.loc[1, "final"], f.loc[1, "basis"]) == ("supported", "owner blind")          # her blind label beats three raters
    assert (f.loc[2, "final"], f.loc[2, "basis"]) == ("supported", "raters agree")         # blank owner cell: the raters' label
    assert (f.loc[5, "final"], f.loc[5, "basis"]) == ("cannot tell", "owner blind")
    assert (f.loc[6, "final"], f.loc[6, "basis"]) == ("unsupported", "owner adjudicated")
    assert (f.loc[7, "final"], f.loc[7, "basis"]) == ("cannot tell", "no majority")
    assert (f.loc[8, "final"], f.loc[8, "basis"]) == ("cannot tell", "no majority")
    plain = s.final_labels(m).set_index("row")
    assert (plain.loc[5, "final"], plain.loc[5, "basis"]) == ("unsupported", "majority")


def test_agreement_tables(tmp_path):
    labels, _, _ = _amend_fixture(tmp_path)
    m = s.machine_table(labels)
    ra = {p: (n, a) for p, n, a, _ in s.rater_agreement(m)}
    assert ra == {"A and B": (8, 6), "A and C": (8, 4), "B and C": (8, 4), "all three": (8, 4)}
    n, a, k = s.owner_agreement(m, _judged({1: "unsupported", 2: "supported", 5: "supported", 7: "cannot tell", 3: ""}))
    assert (n, a) == (4, 3) and 0 < k < 1                                  # the blank row is not counted
    assert s.owner_agreement(m, None)[:2] == (0, 0)


def test_tally_final_and_per_source(tmp_path):
    labels, key, sources = _amend_fixture(tmp_path)
    f = s.final_labels(s.machine_table(labels))
    t = s.tally_final(f, key.astype(str))
    assert t.loc["prune"].tolist() == [0, 3, 1] and t.loc["keep"].tolist() == [2, 1, 1]     # columns: supported, unsupported, cannot tell
    with pytest.raises(ValueError):
        s.tally_final(f, key.iloc[:5])
    ps = s.per_source(f, key, sources)
    top = ps.iloc[0]
    assert (top["verdict"], top["source"], top["rows"], top["unsupported"]) == ("prune", "STRINGDB", 3, 3)
    assert top["share"] == 1.0 and 0.43 < top["lo"] < 0.44 and top["hi"] > 0.9999
    assert set(zip(ps["verdict"], ps["source"])) == {("prune", "STRINGDB"), ("keep", "STRINGDB"), ("prune", "TIN-X"), ("keep", "TIN-X")}


def test_report_final_pass_fail_and_owner_statement(tmp_path):
    labels, key, sources = _amend_fixture(tmp_path)
    m = s.machine_table(labels)
    lines, passed = s.report_final(s.final_labels(m), key, sources, (3, 1), None, {"A": "fam/A", "B": "fam/B", "C": "fam/C"})
    text = "\n".join(lines)
    assert passed and "**3 of 4**" in text and "**1 of 4**" in text and "Result: **PASS**" in text
    assert "all three raters agree: 4 rows" in text and "majority label: 2 rows" in text and "no majority: cannot tell: 2 rows" in text
    assert "NOT checked against a blind human subset" in text and "A = `fam/A`" in text and "| prune | STRINGDB | 3 | 3 | 100.0%" in text
    assert not s.report_final(s.final_labels(m), key, sources, (4, 1))[1]                   # first condition fails
    assert not s.report_final(s.final_labels(m), key, sources, (3, 0))[1]                   # second condition fails
    owner = _judged({r: l for r, l in zip(m["row"], m["machine"])})
    many = pd.concat([owner.assign(row=owner["row"] + 8 * i) for i in range(5)])          # 40 agreeing rows
    big = pd.concat([m.assign(row=m["row"] + 8 * i) for i in range(5)])
    assert s.owner_agreement(big, many)[:2] == (40, 40)
    key40 = pd.DataFrame({"row": range(1, 41), "verdict": ["prune", "keep"] * 20})
    src40 = pd.DataFrame({"row": range(1, 41), "source": "X"})
    text40 = "\n".join(s.report_final(s.final_labels(big, many), key40, src40, (3, 1), many)[0])
    assert "40 of 40 agree" in text40 and "were checked against a blind human subset" in text40


def test_report_audit_has_no_pass_rule(tmp_path):
    labels, key, sources = _amend_fixture(tmp_path)
    text = "\n".join(s.report_audit(s.machine_table(labels), key, sources, {"A": "fam/A"}))
    assert "PASS" not in text and "FAIL" not in text and "No pass rule" in text
    assert "| prune | 4 | 3 = 75.0%" in text and "| keep | 4 | 1 = 25.0%" in text and "all three differ (labelled cannot tell): 2" in text


def test_main_amendment_and_audit_modes(tmp_path, capsys):
    labels, key, sources = _amend_fixture(tmp_path)
    lp, kp, rp = tmp_path / "labels.tsv", tmp_path / "key.tsv", tmp_path / "rule.md"
    labels.to_csv(lp, sep="\t", index=False)
    key.to_csv(kp, sep="\t", index=False)
    rp.write_text("`RULE: prune_unsupported_min=3; keep_unsupported_max=1`\n")

    def xlsx(name, df):
        p = tmp_path / name
        with pd.ExcelWriter(p, engine="openpyxl") as w:
            df.to_excel(w, index=False, sheet_name="spot_check")
        return p
    sheet = xlsx("sheet.xlsx", sources.assign(owner_judgement=""))
    owner = xlsx("owner.xlsx", pd.DataFrame({"row": [1, 2], "source": "STRINGDB", "owner_judgement": ["supported", "supported"]}))
    out = tmp_path / "result.md"
    args = ["--sheet", str(sheet), "--key", str(kp), "--rule", str(rp), "--labels", str(lp)]
    assert s.main(args + ["--write", str(out)]) == 0
    assert "Result: **PASS**" in out.read_text() and "(Amendment 1)" in capsys.readouterr().out
    assert s.main(args + ["--owner40", str(owner), "--write", str(out)]) == 0
    assert "Result: **FAIL**" in out.read_text()                              # her blind 'supported' on row 1 leaves 2 of 4
    assert "the owner's blind label (her 40 rows): 2 rows" in out.read_text()
    assert s.main(["--audit", "--sheet", str(sheet), "--key", str(kp), "--labels", str(lp), "--write", str(out)]) == 0
    assert out.read_text().startswith("# Independent audit sample")
    assert list(s.read_sheet_sources(sheet)["row"]) == list(range(1, 9))
