"""Score the blinded spot check against its closed key and the pre-registered rule (FIX_PLAN_2026-11-08 CS-4).

    .venv/bin/python scripts/score_spotcheck.py [--sheet outputs/pharos/spotcheck_blind.xlsx]
        [--key outputs/pharos/spotcheck_blind_key.tsv] [--rule outputs/pharos/spotcheck_rule.md]
        [--sheet2 <second rater's copy of the sheet>] [--write outputs/pharos/spotcheck_blind_result.md]

The thresholds come from the one line in the rule file that starts with `RULE:`
(`RULE: prune_unsupported_min=60; keep_unsupported_max=15`), so they are set in one place, before the sheet is opened.
Judgements: 'supported', 'unsupported', 'cannot tell' (case and spacing ignored). 'cannot tell', blank and any other
value count as neither. PASS = (pruned rows judged unsupported >= prune_unsupported_min) AND
(kept rows judged unsupported <= keep_unsupported_max). Prints the two counts, PASS/FAIL and the verdict x judgement table.

Amendment 1 (outputs/pharos/spotcheck_rule.md, addition A6): with the raters' labels file the rule is applied to the
FINAL label of each row: the owner's blind label on her 40 rows; else the label all three raters give; else the
owner's adjudication of a flagged row; else the majority label ('cannot tell' when there is none).

    .venv/bin/python scripts/score_spotcheck.py --write outputs/pharos/spotcheck_blind_result.md
        (uses outputs/pharos/spotcheck_machine_labels.tsv, spotcheck_owner40.xlsx and spotcheck_adjudicate.xlsx when
         they exist; --labels / --owner40 / --adjudicated name other files)
    .venv/bin/python scripts/score_spotcheck.py --audit --write outputs/pharos/audit_sample_result.md
        (the separate audit sample: machine raters only, no pass rule)

Reported next to the result, without changing PASS or FAIL: agreement between each pair of raters and between the
owner and the machine label on her rows (raw and Cohen's kappa), and a per-source table with Wilson 95% intervals.
"""
import argparse
import math
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "pharos"
CLASSES = ["unsupported", "supported", "cannot tell", "blank", "invalid"]
LABELS = ["supported", "unsupported", "cannot tell"]
RATERS = ["A", "B", "C"]
OWNER_AGREE_MIN = 32          # of 40: below this the machine labels are reported as not checked by the human subset


def load_rule(path):
    """(prune_unsupported_min, keep_unsupported_max) from the `RULE:` line of the rule file."""
    for line in Path(path).read_text().splitlines():
        if line.strip().strip("`").startswith("RULE:"):
            vals = dict(re.findall(r"(\w+)\s*=\s*(\d+)", line))
            return int(vals["prune_unsupported_min"]), int(vals["keep_unsupported_max"])
    raise ValueError(f"no 'RULE:' line in {path}")


def normalise(j):
    """One of CLASSES for a raw cell value."""
    s = " ".join(str(j if j is not None else "").replace("_", " ").split()).lower()
    if s in ("", "nan", "none"):
        return "blank"
    if s in ("cannot tell", "can't tell", "cant tell"):
        return "cannot tell"
    return s if s in ("supported", "unsupported") else "invalid"


def read_sheet(path):
    """The 'spot_check' tab with a `judgement` column (normalised owner_judgement)."""
    df = pd.read_excel(path, sheet_name="spot_check", dtype=str)
    df["row"] = df["row"].astype(int)
    df["judgement"] = df["owner_judgement"].map(normalise)
    return df


def read_sheet_sources(path):
    """row and source of every row of a blind sheet (for the per-source table)."""
    df = pd.read_excel(path, sheet_name="spot_check", dtype=str, usecols=["row", "source"])
    return df.assign(row=df["row"].astype(int))


def tally(sheet, key):
    """verdict (prune/keep) x judgement-class count table (DataFrame, index verdict, columns CLASSES).
    Raises if the sheet and key rows do not match one-to-one."""
    key = key.assign(row=key["row"].astype(int))
    if sorted(sheet["row"]) != sorted(key["row"]):
        raise ValueError("sheet rows and key rows differ: wrong key file or edited row numbers")
    df = sheet[["row", "judgement"]].merge(key[["row", "verdict"]], on="row")
    t = pd.crosstab(df["verdict"], df["judgement"]).reindex(index=["prune", "keep"], columns=CLASSES, fill_value=0)
    return t.fillna(0).astype(int)


def decide(table, rule):
    """(passed, pruned_unsupported, kept_unsupported) under rule = (prune_unsupported_min, keep_unsupported_max)."""
    pu, ku = int(table.loc["prune", "unsupported"]), int(table.loc["keep", "unsupported"])
    return (pu >= rule[0] and ku <= rule[1]), pu, ku


def agreement(sheet1, sheet2):
    """(n rows both raters judged supported/unsupported, n of those that agree)."""
    m = sheet1[["row", "judgement"]].merge(sheet2[["row", "judgement"]], on="row", suffixes=("_1", "_2"))
    both = m[m["judgement_1"].isin(["supported", "unsupported"]) & m["judgement_2"].isin(["supported", "unsupported"])]
    return len(both), int((both["judgement_1"] == both["judgement_2"]).sum())


def report(table, rule, passed, pu, ku, agree=None):
    """Markdown lines: the two counts, PASS/FAIL, the 2x2 table and the 'neither' counts."""
    n_p, n_k = int(table.loc["prune"].sum()), int(table.loc["keep"].sum())
    lines = ["# Blinded spot check: result", "",
             f"Rule (pre-registered): PASS if >= {rule[0]} of the pruned edges are judged unsupported AND "
             f"<= {rule[1]} of the kept edges are judged unsupported.", "",
             f"- Pruned edges judged unsupported: **{pu} of {n_p}** (threshold >= {rule[0]})",
             f"- Kept edges judged unsupported: **{ku} of {n_k}** (threshold <= {rule[1]})",
             f"- Result: **{'PASS' if passed else 'FAIL'}**", "",
             "| pruner verdict | judged unsupported | judged supported |", "|---|---|---|",
             f"| prune | {pu} | {int(table.loc['prune', 'supported'])} |",
             f"| keep | {ku} | {int(table.loc['keep', 'supported'])} |", "",
             "Neither (counted in no cell): " + "; ".join(
                 f"{v}: cannot tell {int(table.loc[v, 'cannot tell'])}, blank {int(table.loc[v, 'blank'])}, "
                 f"other value {int(table.loc[v, 'invalid'])}" for v in ("prune", "keep"))]
    if agree is not None:
        lines += ["", f"Second rater: {agree[1]} of {agree[0]} rows agree (rows both judged supported/unsupported)."]
    return lines


# ---------------------------------------------------------------------------------------- Amendment 1, addition A6
def wilson(k, n, z=1.959964):
    """Wilson score interval (lo, hi) for k successes in n trials (95% by default); (nan, nan) when n is 0."""
    if n <= 0:
        return float("nan"), float("nan")
    ph, d = k / n, 1 + z * z / n
    centre, half = (ph + z * z / (2 * n)) / d, z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def kappa(a, b, classes=LABELS):
    """Cohen's kappa of two equal-length label lists over `classes`; nan when chance agreement is 1 or the lists are empty."""
    a, b = list(a), list(b)
    n = len(a)
    if n == 0 or n != len(b):
        return float("nan")
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in classes)
    return float("nan") if pe >= 1 else (po - pe) / (1 - pe)


def machine_table(labels):
    """One line per row from the raters' labels file (columns row, rater, label): label_A, label_B, label_C, proposal
    (the label all three give, else ''), flagged, majority ('' when all three differ), machine (majority, or
    'cannot tell' when there is none). Rows without all three raters raise: the run is not complete."""
    wide = labels.assign(row=labels["row"].astype(int)).pivot_table(index="row", columns="rater", values="label", aggfunc="first")
    wide = wide.reindex(columns=RATERS)
    if wide.isna().any().any():
        raise ValueError(f"{int(wide.isna().any(axis=1).sum())} rows lack a label from one of the three raters")
    out = wide.rename(columns={k: f"label_{k}" for k in RATERS}).reset_index()
    trio = out[[f"label_{k}" for k in RATERS]].values.tolist()
    out["proposal"] = [t[0] if len(set(t)) == 1 else "" for t in trio]
    out["flagged"] = [len(set(t)) > 1 for t in trio]
    out["majority"] = [next((x for x in LABELS if t.count(x) >= 2), "") for t in trio]
    out["machine"] = [m or "cannot tell" for m in out["majority"]]
    return out


def final_labels(machine, owner=None, adjudicated=None):
    """The final label of every row (addition A6). machine: machine_table(); owner / adjudicated: DataFrames with row
    and judgement (read_sheet) or None. Returns machine plus `final` and `basis`: 'owner blind', 'raters agree',
    'owner adjudicated', 'majority' or 'no majority'. A blank or invalid owner cell counts as not rated."""
    rated = lambda df: {} if df is None else {int(r): j for r, j in zip(df["row"], df["judgement"]) if j in LABELS}
    own, adj = rated(owner), rated(adjudicated)
    final, basis = [], []
    for _, m in machine.iterrows():
        r = int(m["row"])
        if r in own:
            final.append(own[r]); basis.append("owner blind")
        elif not m["flagged"]:
            final.append(m["proposal"]); basis.append("raters agree")
        elif r in adj:
            final.append(adj[r]); basis.append("owner adjudicated")
        else:
            final.append(m["machine"]); basis.append("majority" if m["majority"] else "no majority")
    return machine.assign(final=final, basis=basis)


def rater_agreement(machine):
    """[(pair, n, agree, kappa)] for each pair of raters over all rows, then ('all three', n, agree, nan)."""
    out = []
    for i, x in enumerate(RATERS):
        for y in RATERS[i + 1:]:
            a, b = list(machine[f"label_{x}"]), list(machine[f"label_{y}"])
            out.append((f"{x} and {y}", len(a), sum(p == q for p, q in zip(a, b)), kappa(a, b)))
    out.append(("all three", len(machine), int((~machine["flagged"]).sum()), float("nan")))
    return out


def owner_agreement(machine, owner):
    """(n rated, n agree, kappa) between the owner's blind labels and the machine label on the rows she rated."""
    if owner is None:
        return 0, 0, float("nan")
    m = machine[["row", "machine"]].merge(owner[owner["judgement"].isin(LABELS)][["row", "judgement"]], on="row")
    return len(m), int((m["machine"] == m["judgement"]).sum()), kappa(m["machine"], m["judgement"])


def tally_final(labelled, key, column="final"):
    """verdict x label count table (index prune / keep, columns LABELS) of `column` joined to the key by row."""
    key = key.assign(row=key["row"].astype(int))
    if sorted(labelled["row"]) != sorted(key["row"]):
        raise ValueError("label rows and key rows differ: wrong key file")
    df = labelled[["row", column]].merge(key[["row", "verdict"]], on="row")
    return pd.crosstab(df["verdict"], df[column]).reindex(index=["prune", "keep"], columns=LABELS, fill_value=0).fillna(0).astype(int)


def per_source(labelled, key, sources, column="final"):
    """Per verdict and source: rows, rows labelled unsupported, share and Wilson 95% interval. sources: DataFrame row,
    source (the blind sheet). Returns a DataFrame sorted by verdict (prune first) and row count."""
    df = labelled[["row", column]].merge(key.assign(row=key["row"].astype(int))[["row", "verdict"]], on="row")
    df = df.merge(sources.assign(row=sources["row"].astype(int))[["row", "source"]], on="row")
    recs = []
    for (v, src), g in df.groupby(["verdict", "source"]):
        k, n = int((g[column] == "unsupported").sum()), len(g)
        lo, hi = wilson(k, n)
        recs.append({"verdict": v, "source": src, "rows": n, "unsupported": k, "share": k / n, "lo": lo, "hi": hi,
                     "supported": int((g[column] == "supported").sum()), "cannot_tell": int((g[column] == "cannot tell").sum())})
    out = pd.DataFrame(recs)
    return out.sort_values(["verdict", "rows"], ascending=[False, False]).reset_index(drop=True) if len(out) else out


def _pct(x):
    return "n/a" if x != x else f"{100 * x:.1f}%"


def _kap(x):
    return "n/a" if x != x else f"{x:.2f}"


def _source_lines(tab):
    lines = ["| pruner verdict | source | rows | unsupported | share (95% interval) | supported | cannot tell |", "|---|---|---|---|---|---|---|"]
    for _, r in tab.iterrows():
        lines.append(f"| {r['verdict']} | {r['source']} | {r['rows']} | {r['unsupported']} | {_pct(r['share'])} "
                     f"({_pct(r['lo'])} to {_pct(r['hi'])}) | {r['supported']} | {r['cannot_tell']} |")
    return lines


def report_final(fin, key, sources, rule, owner=None, models=None):
    """Markdown lines of the Amendment 1 result: the rule on the final labels, PASS or FAIL, how each final label was
    reached, agreement between raters and with the owner, the per-source table. Returns (lines, passed)."""
    t = tally_final(fin, key)
    pu, ku = int(t.loc["prune", "unsupported"]), int(t.loc["keep", "unsupported"])
    n_p, n_k = int(t.loc["prune"].sum()), int(t.loc["keep"].sum())
    passed = pu >= rule[0] and ku <= rule[1]
    basis = fin["basis"].value_counts()
    n_o, a_o, k_o = owner_agreement(fin, owner)
    lines = ["# Blinded spot check: result (Amendment 1)", "",
             f"Rule (pre-registered): PASS if >= {rule[0]} of the pruned edges are judged unsupported AND <= {rule[1]} of the "
             "kept edges are judged unsupported. Applied to the final label of each row (rule file, addition A6).", "",
             f"- Pruned edges judged unsupported: **{pu} of {n_p}** (threshold >= {rule[0]})",
             f"- Kept edges judged unsupported: **{ku} of {n_k}** (threshold <= {rule[1]})",
             f"- Result: **{'PASS' if passed else 'FAIL'}**", "",
             "| pruner verdict | unsupported | supported | cannot tell |", "|---|---|---|---|"]
    lines += [f"| {v} | {int(t.loc[v, 'unsupported'])} | {int(t.loc[v, 'supported'])} | {int(t.loc[v, 'cannot tell'])} |" for v in ("prune", "keep")]
    lines += ["", "## How each final label was reached", ""]
    names = {"owner blind": "the owner's blind label (her 40 rows)", "raters agree": "all three raters agree",
             "owner adjudicated": "flagged row, adjudicated by the owner", "majority": "flagged row, not adjudicated: majority label",
             "no majority": "flagged row, not adjudicated, no majority: cannot tell"}
    lines += [f"- {names[b]}: {int(basis.get(b, 0))} rows" for b in names]
    lines += ["", "## Agreement (does not change PASS or FAIL)", "", "| raters | rows | agree | Cohen's kappa |", "|---|---|---|---|"]
    lines += [f"| {p} | {n} | {a} ({_pct(a / n) if n else 'n/a'}) | {_kap(k)} |" for p, n, a, k in rater_agreement(fin)]
    if models:
        lines += ["", "Raters: " + "; ".join(f"{k} = `{v}`" for k, v in sorted(models.items())) + "."]
    lines += ["", f"Owner's blind labels against the machine label (majority of the three raters) on the rows she rated: "
              f"{a_o} of {n_o} agree" + (f" ({_pct(a_o / n_o)}), Cohen's kappa {_kap(k_o)}." if n_o else "."),
              ("The machine labels were checked against a blind human subset." if a_o >= OWNER_AGREE_MIN else
               f"The machine labels were NOT checked against a blind human subset to the pre-registered level "
               f"({OWNER_AGREE_MIN} of 40 agreeing rows)."),
              "", "## Per source (final labels)", ""] + _source_lines(per_source(fin, key, sources))
    return lines, passed


def report_audit(machine, key, sources, models=None):
    """Markdown lines of the separate audit sample (machine raters only, label = majority of three, no pass rule)."""
    t = tally_final(machine, key, "machine")
    lines = ["# Independent audit sample: result (machine raters only)", "",
             "Separate from the pre-registered 200 rows and never mixed into their rule (rule file, addition A7). "
             "Label = majority of three verdict-blind raters; `cannot tell` when all three differ. No pass rule.", "",
             "| pruner verdict | rows | unsupported (95% interval) | supported | cannot tell |", "|---|---|---|---|---|"]
    for v in ("prune", "keep"):
        n, k = int(t.loc[v].sum()), int(t.loc[v, "unsupported"])
        lo, hi = wilson(k, n)
        lines.append(f"| {v} | {n} | {k} = {_pct(k / n) if n else 'n/a'} ({_pct(lo)} to {_pct(hi)}) | {int(t.loc[v, 'supported'])} | {int(t.loc[v, 'cannot tell'])} |")
    lines += ["", "## Agreement between raters", "", "| raters | rows | agree | Cohen's kappa |", "|---|---|---|---|"]
    lines += [f"| {p} | {n} | {a} ({_pct(a / n) if n else 'n/a'}) | {_kap(k)} |" for p, n, a, k in rater_agreement(machine)]
    lines += [f"", f"Rows on which all three differ (labelled cannot tell): {int((machine['majority'] == '').sum())}."]
    if models:
        lines += ["", "Raters: " + "; ".join(f"{k} = `{v}`" for k, v in sorted(models.items())) + "."]
    lines += ["", "## Per source (majority labels)", ""] + _source_lines(per_source(machine, key, sources, "machine"))
    return lines


def _models(labels):
    return {k: sorted(set(g["model"]) - {"deterministic (UniProt cross-reference)"})[0] for k, g in labels.groupby("rater")
            if set(g["model"]) - {"deterministic (UniProt cross-reference)"}}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sheet", default=str(OUT / "spotcheck_blind.xlsx"))
    ap.add_argument("--key", default=str(OUT / "spotcheck_blind_key.tsv"))
    ap.add_argument("--rule", default=str(OUT / "spotcheck_rule.md"))
    ap.add_argument("--sheet2", default=None)
    ap.add_argument("--write", default=None)
    ap.add_argument("--labels", default=None, help="the raters' labels file (Amendment 1)")
    ap.add_argument("--owner40", default=None)
    ap.add_argument("--adjudicated", default=None)
    ap.add_argument("--audit", action="store_true", help="score the separate audit sample (machine raters only)")
    a = ap.parse_args(argv)
    read_key = lambda path: pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
    default_sheet = a.sheet == str(OUT / "spotcheck_blind.xlsx")
    if a.audit:
        sheet = a.sheet if not default_sheet else str(OUT / "audit_sample_blind.xlsx")
        key = a.key if a.key != str(OUT / "spotcheck_blind_key.tsv") else str(OUT / "audit_sample_key.tsv")
        labels = read_key(a.labels or str(OUT / "audit_sample_machine_labels.tsv"))
        lines = report_audit(machine_table(labels), read_key(key), read_sheet_sources(sheet), _models(labels))
    else:
        labels_path = a.labels or (str(OUT / "spotcheck_machine_labels.tsv") if default_sheet else None)
        rule = load_rule(a.rule)
        if labels_path and Path(labels_path).exists():
            labels = read_key(labels_path)
            opt = lambda given, name: given or (str(OUT / name) if default_sheet and (OUT / name).exists() else None)
            o40, adj = opt(a.owner40, "spotcheck_owner40.xlsx"), opt(a.adjudicated, "spotcheck_adjudicate.xlsx")
            owner = read_sheet(o40) if o40 else None
            fin = final_labels(machine_table(labels), owner, read_sheet(adj) if adj else None)
            lines, _ = report_final(fin, read_key(a.key), read_sheet_sources(a.sheet), rule, owner, _models(labels))
        else:
            sheet = read_sheet(a.sheet)
            table = tally(sheet, read_key(a.key))
            passed, pu, ku = decide(table, rule)
            agree = agreement(sheet, read_sheet(a.sheet2)) if a.sheet2 else None
            lines = report(table, rule, passed, pu, ku, agree)
    print("\n".join(lines))
    if a.write:
        Path(a.write).write_text("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
