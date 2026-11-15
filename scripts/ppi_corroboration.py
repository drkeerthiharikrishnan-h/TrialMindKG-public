"""Pruned versus kept Pharos protein-protein rows: share with an experimental record in IntAct (no model).

    .venv/bin/python scripts/ppi_corroboration.py [--intact data/external/intact/human.zip] [--layer pharos]
    .venv/bin/python scripts/ppi_corroboration.py --from-json     # report and figure again from the stored numbers

Reads  data/external/intact/human.zip      IntAct's human interaction file (PSI-MITAB 2.7; one line per evidence),
                                           from ftp.ebi.ac.uk/pub/databases/intact/current/psimitab/species/human.zip
       data/kg/<layer>/edge_verdicts.tsv.gz  the shipped pruner verdicts (streamed)
Writes outputs/<layer>/ppi_corroboration.md and .json; docs/figures/ppi_corroboration_intact.png (--figure)

The pruner never reads IntAct. For every row with predicate physically_interacts_with between two different
proteins, the unordered pair of UniProt accessions (isoform suffix removed) is looked up in IntAct. Four nested
definitions of "has a record", each excluding negative records:
  any          any IntAct evidence for the pair
  physical     an evidence whose interaction type is not colocalization and not a genetic interaction
  independent  a physical evidence from a publication other than the three BioPlex papers (an IntAct record from
               a BioPlex paper is the same experiment as a BioPlex-sourced row)
  binary       an independent physical evidence that is not a spoke-expanded co-complex purification
The headline column is `corroborated`: `independent` for a row whose own source includes BioPlex, `physical` for
every other row. Limits: IntAct covers a minority of true human interactions, and STRING's experimental channel is
partly built from IntAct, so this is a calibration check, not a gold standard.
"""
import argparse
import io
import json
import math
import re
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BIOPLEX_PMIDS = {"26186194", "28514442", "33961781"}          # Huttlin 2015, 2017, 2021 (titles checked in Europe PMC)
ANY, PHYSICAL, INDEPENDENT, BINARY = 1, 2, 4, 8
DEFS = [("any", ANY), ("physical", PHYSICAL), ("independent", INDEPENDENT), ("binary", BINARY)]
PREDICATE = "biolink:physically_interacts_with"
_ACC = re.compile(r"uniprotkb:([A-Z0-9]+)")


def record_mask(type_cell, pub_cell, expansion_cell):
    """Bit mask of one IntAct evidence line from its interaction type, publication ids and expansion method."""
    t = type_cell.lower()
    mask = ANY
    if "colocali" in t or "genetic" in t or "phenotypic" in t or "epistasis" in t:
        return mask
    mask |= PHYSICAL
    if not (set(re.findall(r"pubmed:(\d+)", pub_cell)) & BIOPLEX_PMIDS):
        mask |= INDEPENDENT
        if "expansion" not in expansion_cell.lower():
            mask |= BINARY
    return mask


def intact_pairs(lines):
    """{'A|B': mask} for every unordered pair of different UniProt proteins in MITAB 2.7 lines (A < B; isoform and
    chain suffixes removed; negative records, header lines and non-protein interactors skipped). Also returns the
    counts {lines, used, negative, not_two_proteins, self}."""
    pairs, n = {}, {"lines": 0, "used": 0, "negative": 0, "not_two_proteins": 0, "self": 0}
    for line in lines:
        if not line or line[0] == "#":
            continue
        n["lines"] += 1
        c = line.rstrip("\n").split("\t", 36)
        if len(c) < 36:
            n["not_two_proteins"] += 1
            continue
        a, b = _ACC.match(c[0]), _ACC.match(c[1])
        if not a or not b:
            n["not_two_proteins"] += 1
            continue
        a, b = a.group(1), b.group(1)
        if a == b:
            n["self"] += 1
            continue
        if c[35].strip().lower() == "true":
            n["negative"] += 1
            continue
        k = a + "|" + b if a < b else b + "|" + a
        pairs[k] = pairs.get(k, 0) | record_mask(c[11], c[8], c[15])
        n["used"] += 1
    return pairs, n


def open_intact(path, member="human.txt"):
    """Text line iterator over one member of the IntAct zip (streamed, never unpacked to disk)."""
    z = zipfile.ZipFile(path)
    return io.TextIOWrapper(z.open(member), encoding="utf-8", errors="replace")


def pair_keys(subject, obj):
    """Unordered pair keys 'A|B' for two Series of UniProtKB ids (prefix and isoform suffix removed)."""
    a = subject.str.split(":", n=1).str[-1].str.split("-", n=1).str[0]
    b = obj.str.split(":", n=1).str[-1].str.split("-", n=1).str[0]
    lo = a.where(a < b, b)
    hi = b.where(a < b, a)
    return lo + "|" + hi


def tally_rows(chunks, pairs):
    """One pass over verdict-file chunks (columns subject, predicate, object, source, confidence, verdict). Returns
    (counts, conf, corr, pruned, n_self, n_pairs): counts = DataFrame per (verdict, source) with rows and one column
    per definition plus `corroborated`; conf / corr / pruned = per-row arrays (confidence, corroborated flag, pruned
    flag) of the rows between two different proteins; n_self = self-pair rows by verdict; n_pairs = number of
    distinct unordered pairs among those rows."""
    lookup = pd.Series(pairs, dtype="int64")
    parts, conf, corr, pruned, n_self, seen = [], [], [], [], {}, set()
    for ch in chunks:
        ch = ch[ch["predicate"] == PREDICATE]
        if not len(ch):
            continue
        same = ch["subject"] == ch["object"]
        for v, c in ch[same]["verdict"].value_counts().items():
            n_self[v] = n_self.get(v, 0) + int(c)
        ch = ch[~same]
        if not len(ch):
            continue
        keys = pair_keys(ch["subject"], ch["object"])
        seen.update(keys.unique())
        mask = keys.map(lookup).fillna(0).astype("int64").to_numpy()
        bioplex = ch["source"].str.contains("BioPlex", case=False).to_numpy()
        df = pd.DataFrame({"verdict": ch["verdict"].to_numpy(), "source": ch["source"].to_numpy(), "rows": 1})
        for name, bit in DEFS:
            df[name] = (mask & bit) > 0
        df["corroborated"] = np.where(bioplex, df["independent"], df["physical"])
        parts.append(df.groupby(["verdict", "source"], as_index=False).sum())
        conf.append(pd.to_numeric(ch["confidence"], errors="coerce").to_numpy(dtype="float32"))
        corr.append(df["corroborated"].to_numpy())
        pruned.append((ch["verdict"] == "prune").to_numpy())
    cols = ["verdict", "source", "rows"] + [d for d, _ in DEFS] + ["corroborated"]
    counts = (pd.concat(parts).groupby(["verdict", "source"], as_index=False).sum() if parts else pd.DataFrame(columns=cols))
    cat = lambda xs, t: np.concatenate(xs) if xs else np.array([], dtype=t)
    return counts[cols], cat(conf, "float32"), cat(corr, bool), cat(pruned, bool), n_self, len(seen)


def wilson(k, n, z=1.959964):
    """Wilson score interval (lo, hi) for k of n; (nan, nan) when n is 0."""
    if n <= 0:
        return float("nan"), float("nan")
    ph, d = k / n, 1 + z * z / n
    centre, half = (ph + z * z / (2 * n)) / d, z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def by_verdict(counts):
    """{verdict: {rows, any, physical, independent, binary, corroborated}} summed over sources."""
    g = counts.drop(columns=["source"]).groupby("verdict").sum()
    return {v: {c: int(g.loc[v, c]) for c in g.columns} for v in g.index}


def within_source(counts):
    """[{source, prune_rows, prune_share, keep_rows, keep_share}] for every source that has both pruned and kept rows
    (largest first): the pruned-versus-kept comparison with the mix of sources held fixed."""
    out = []
    for src, g in counts.groupby("source"):
        g = g.set_index("verdict")
        if {"prune", "keep"} <= set(g.index) and g.loc["prune", "rows"] and g.loc["keep", "rows"]:
            out.append({"source": src, "prune_rows": int(g.loc["prune", "rows"]), "keep_rows": int(g.loc["keep", "rows"]),
                        "prune_share": float(g.loc["prune", "corroborated"] / g.loc["prune", "rows"]),
                        "keep_share": float(g.loc["keep", "corroborated"] / g.loc["keep", "rows"])})
    return sorted(out, key=lambda r: -(r["prune_rows"] + r["keep_rows"]))


def decile_table(conf, corr, pruned):
    """Rows in ten equal-count bins of pruner confidence (lowest first): [{decile, lo, hi, rows, corroborated,
    share, pruned}]. Rows without a confidence are left out."""
    ok = ~np.isnan(conf)
    conf, corr, pruned = conf[ok], corr[ok], pruned[ok]
    if not len(conf):
        return []
    order = np.argsort(conf, kind="stable")
    out = []
    for i, idx in enumerate(np.array_split(order, 10), 1):
        if len(idx):
            out.append({"decile": i, "lo": float(conf[idx[0]]), "hi": float(conf[idx[-1]]), "rows": int(len(idx)),
                        "corroborated": int(corr[idx].sum()), "share": float(corr[idx].mean()), "pruned": int(pruned[idx].sum())})
    return out


def summarise(counts, conf, corr, pruned, n_self, n_pairs, intact_counts, n_intact_pairs, inputs):
    """The result as a JSON-able dict (what the report and the figure are drawn from)."""
    v = by_verdict(counts)
    for d in v.values():
        for name in [x for x, _ in DEFS] + ["corroborated"]:
            lo, hi = wilson(d[name], d["rows"])
            d[name + "_share"], d[name + "_lo"], d[name + "_hi"] = d[name] / d["rows"] if d["rows"] else float("nan"), lo, hi
    total_corr = sum(d["corroborated"] for d in v.values())
    within = within_source(counts)
    own = counts[counts["source"].str.contains("BioPlex", case=False)].groupby("verdict")["rows"].sum()
    return {"within_source": within, "bioplex_rows": {k: int(x) for k, x in own.items()},
            "inputs": inputs, "intact": {**intact_counts, "pairs": n_intact_pairs}, "rows": int(counts["rows"].sum()),
            "distinct_pairs": int(n_pairs), "self_pair_rows": {k: int(x) for k, x in n_self.items()}, "by_verdict": v,
            "corroborated_rows_pruned_share": (v.get("prune", {}).get("corroborated", 0) / total_corr) if total_corr else float("nan"),
            "by_source": counts.to_dict("records"), "deciles": decile_table(conf, corr, pruned)}


def _pct(x, nd=2):
    return "n/a" if x != x else f"{100 * x:.{nd}f}%"


def report(s):
    """Markdown lines of the report from summarise()."""
    v = s["by_verdict"]
    p, k = v.get("prune", {}), v.get("keep", {})
    ratio = (k["corroborated_share"] / p["corroborated_share"]) if p.get("corroborated_share") else float("nan")
    lines = ["# Pruned versus kept protein-protein rows: experimental records in IntAct", "",
             f"No model is involved. The pruner never reads IntAct. Rows: every Pharos edge with predicate "
             f"`physically_interacts_with` between two different proteins: **{s['rows']:,} rows**, {s['distinct_pairs']:,} distinct "
             f"unordered protein pairs. IntAct: {s['intact']['pairs']:,} distinct human protein pairs with at least one "
             f"non-negative evidence ({s['intact']['used']:,} evidence lines used of {s['intact']['lines']:,}).", "",
             (f"**Headline: {_pct(p.get('corroborated_share', float('nan')))} of pruned rows and "
              f"{_pct(k.get('corroborated_share', float('nan')))} of kept rows have a physical record in IntAct that is independent "
              f"of the row's own source** (kept rows {ratio:.1f} times as often)." if ratio == ratio else
              "**Headline: not computable (a verdict has no rows).**"),
             f"Of all rows with such a record, {_pct(s['corroborated_rows_pruned_share'])} are pruned"
             + (f"; {_pct(p['rows'] / s['rows'])} of all rows are pruned." if p.get("rows") else "."), ""]
    w = s.get("within_source") or []
    if w:
        higher = sum(r["keep_share"] > r["prune_share"] for r in w)
        lines += [f"With the source held fixed, the kept share is higher than the pruned share in {higher} of the {len(w)} sources that "
                  "have both pruned and kept rows (table 'By source of the row').", ""]
    if p and k and p.get("any_share", 0) > k.get("any_share", 0):
        bp = s.get("bioplex_rows", {})
        lines += [f"Counting ANY IntAct record the order is the other way round ({_pct(p['any_share'])} of pruned rows, "
                  f"{_pct(k['any_share'])} of kept rows). That count is not a fair test: {bp.get('prune', 0):,} of the "
                  f"{p['rows']:,} pruned rows ({_pct(bp.get('prune', 0) / p['rows'])}) and {bp.get('keep', 0):,} of the {k['rows']:,} kept rows "
                  f"({_pct(bp.get('keep', 0) / k['rows'])}) come from BioPlex, whose own experiments are deposited in IntAct, so such a row finds "
                  "its own source there. The headline column leaves those records out for BioPlex rows.", ""]
    lines += [
             "| pruner verdict | rows | any record | physical record | independent physical | independent, binary | corroborated (95% interval) |",
             "|---|---|---|---|---|---|---|"]
    for name in ("prune", "keep"):
        d = v.get(name)
        if d:
            lines.append(f"| {name} | {d['rows']:,} | {d['any']:,} ({_pct(d['any_share'])}) | {d['physical']:,} ({_pct(d['physical_share'])}) | "
                         f"{d['independent']:,} ({_pct(d['independent_share'])}) | {d['binary']:,} ({_pct(d['binary_share'])}) | "
                         f"{d['corroborated']:,} = {_pct(d['corroborated_share'])} ({_pct(d['corroborated_lo'])} to {_pct(d['corroborated_hi'])}) |")
    lines += ["", "Figure: `docs/figures/ppi_corroboration_intact.png` (left: the headline share for all rows and within each "
              "source that has both verdicts; right: the same share by tenth of pruner confidence).",
              "", "Definitions (nested, negative records excluded): any = any IntAct evidence for the pair; physical = interaction "
              "type other than colocalization or genetic interaction; independent = physical, from a publication other than the "
              "three BioPlex papers; binary = independent and not a spoke-expanded co-complex purification. corroborated = "
              "independent for a row whose own source includes BioPlex (an IntAct record from a BioPlex paper is the same "
              "experiment), physical for every other row.", "", "## By source of the row", "",
              "| pruner verdict | source | rows | corroborated | share |", "|---|---|---|---|---|"]
    for r in sorted(s["by_source"], key=lambda r: (r["verdict"] != "prune", -r["rows"])):
        lines.append(f"| {r['verdict']} | {r['source']} | {int(r['rows']):,} | {int(r['corroborated']):,} | {_pct(r['corroborated'] / r['rows'])} |")
    lines += ["", "## By pruner confidence (ten equal-count bins, least confident first)", "",
              "| bin | confidence range | rows | pruned | corroborated | share |", "|---|---|---|---|---|---|"]
    for d in s["deciles"]:
        lines.append(f"| {d['decile']} | {d['lo']:.3f} to {d['hi']:.3f} | {d['rows']:,} | {d['pruned']:,} | {d['corroborated']:,} | {_pct(d['share'])} |")
    selfs = s["self_pair_rows"]
    lines += ["", f"Rows that link a protein to itself are not in the tables: {sum(selfs.values()):,} rows ("
              + ", ".join(f"{n:,} {v_}" for v_, n in sorted(selfs.items())) + ").", "",
              "## Limits", "",
              "- IntAct holds a minority of true human protein interactions, so a row without a record is not shown to be wrong.",
              "- STRING's experimental channel is partly built from IntAct. The pruner does not read IntAct, but a high STRING "
              "score and an IntAct record are not independent facts.",
              "- Pairs are matched on UniProt accession with isoform suffixes removed; an interaction curated on a secondary "
              "accession is missed.", "", "## Inputs", ""] + [f"- {i}" for i in s["inputs"]]
    return lines


FOCAL, COMPARATOR = "#1f5fa8", "#8a8a8a"                 # kept rows, pruned rows (as in scripts/plot_prune_calibration.py)
STYLE = {"font.size": 8, "axes.titlesize": 8, "axes.labelsize": 8, "xtick.labelsize": 6, "ytick.labelsize": 6,
         "axes.spines.top": False, "axes.spines.right": False, "xtick.direction": "out", "ytick.direction": "out",
         "savefig.dpi": 300, "pdf.fonttype": 42, "ps.fonttype": 42}


SOURCE_NAMES = {"STRINGDB": "STRING", "BioPlex,STRINGDB": "BioPlex\n+ STRING", "Reactome,STRINGDB": "Reactome\n+ STRING"}


def figure_series(s):
    """The bars of the left panel: [(label, pruned share, kept share)] for all rows, then every source with both
    verdicts (see within_source)."""
    p, k = s["by_verdict"].get("prune"), s["by_verdict"].get("keep")
    out = [("all rows", p["corroborated_share"], k["corroborated_share"])] if p and k else []
    return out + [(SOURCE_NAMES.get(r["source"], r["source"]), r["prune_share"], r["keep_share"]) for r in s.get("within_source") or []]


def figure_titles(s):
    """(left title, right title): a claim only when every plotted category bears it out, otherwise a plain description."""
    series = figure_series(s)
    left = "Independent IntAct records among pruned and kept interaction rows"
    if series and all(k > p for _, p, k in series):
        left = "Kept rows have an independent IntAct record more often than pruned rows, in every source"
    shares = [d["share"] for d in s["deciles"]]
    rising = len(shares) >= 3 and all(y > x for x, y in zip(shares, shares[1:]))
    right = "The share with a record rises with every tenth of pruner confidence" if rising else "IntAct records by tenth of pruner confidence"
    return left, right


def text_overlaps(fig):
    """Number of pairs of visible text boxes of a drawn figure that overlap (0 = clean)."""
    import matplotlib.text as mtext
    r = fig.canvas.get_renderer()
    boxes = [t.get_window_extent(r) for t in fig.findobj(mtext.Text) if t.get_text().strip() and t.get_visible()]
    return sum(1 for i, a in enumerate(boxes) for b in boxes[i + 1:] if a.overlaps(b))


def figure(s, path):
    """Two panels drawn from summarise() only: (a) share of pruned and kept rows with an independent physical IntAct
    record, for all rows and within each source that has both verdicts; (b) the same share by tenth of pruner
    confidence. No file path, run tag or graph code appears in the figure. Returns {'titles', 'bars', 'line',
    'overlaps'} (what was drawn, and the number of overlapping text boxes) for the test."""
    import textwrap
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    series = figure_series(s)
    left, right = figure_titles(s)
    drawn = {"titles": (left, right), "bars": [(lab, round(100 * p, 3), round(100 * k, 3)) for lab, p, k in series],
             "line": [round(100 * d["share"], 3) for d in s["deciles"]]}
    with plt.rc_context(STYLE):
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.2, 3.4), gridspec_kw={"width_ratios": [1.25, 1]})
        x = np.arange(len(series))
        for j, (idx, lab, col) in enumerate(((1, "pruned rows", COMPARATOR), (2, "kept rows", FOCAL))):
            y = [100 * r[idx] for r in series]
            bars = ax1.bar(x + (j - 0.5) * 0.36, y, 0.36, color=col, label=lab)
            if len(bars):                                     # the headline pair only; the two labels lean apart
                ax1.annotate(f"{y[0]:.2g}%", (bars[0].get_x() + bars[0].get_width() / 2, y[0]), ha="right" if j == 0 else "left",
                             va="bottom", fontsize=7, color=col, xytext=(-1 if j == 0 else 1, 2), textcoords="offset points")
        ax1.set_xticks(x)
        ax1.set_xticklabels([r[0] for r in series])
        ax1.set_ylabel("rows with an independent\nphysical record in IntAct (%)")
        ax1.set_title(textwrap.fill(left, 50), loc="left")
        ax1.legend(frameon=False, fontsize=7, loc="upper left")
        ax1.margins(y=0.15)
        ax1.set_xlim(-0.85, len(series) - 0.4)                # room for the first value label, clear of the axis
        dec = s["deciles"]
        ax2.plot([d["decile"] for d in dec], [100 * d["share"] for d in dec], color=FOCAL, lw=1.8, marker="o", ms=4.5)
        ax2.set_xticks([d["decile"] for d in dec])
        ax2.set_xlabel("tenth of pruner confidence (1 = least confident)")
        ax2.set_ylabel("rows with an independent\nphysical record in IntAct (%)")
        ax2.set_title(textwrap.fill(right, 38), loc="left")
        ax2.set_ylim(bottom=0)
        ax2.margins(x=0.06, y=0.12)
        fig.tight_layout()
        fig.canvas.draw()
        drawn["overlaps"] = text_overlaps(fig)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=300)
        plt.close(fig)
    return drawn


def _describe(p):
    import datetime as dt
    st = Path(p).stat()
    return f"`{Path(p).relative_to(ROOT)}` ({st.st_size:,} bytes, modified {dt.datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M})"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--intact", default=str(ROOT / "data" / "external" / "intact" / "human.zip"))
    ap.add_argument("--layer", default="pharos")
    ap.add_argument("--figure", default=str(ROOT / "docs" / "figures" / "ppi_corroboration_intact.png"))
    ap.add_argument("--from-json", action="store_true", help="redraw the report and the figure from the stored JSON")
    a = ap.parse_args(argv)
    vpath = ROOT / "data" / "kg" / a.layer / "edge_verdicts.tsv.gz"
    out = ROOT / "outputs" / a.layer
    if a.from_json:
        s = json.loads((out / "ppi_corroboration.json").read_text())
        (out / "ppi_corroboration.md").write_text("\n".join(report(s)) + "\n")
        if a.figure:
            figure(s, a.figure)
        print("\n".join(report(s)[:6]))
        return 0
    with open_intact(a.intact) as fh:
        pairs, n = intact_pairs(fh)
    print(f"IntAct: {n}; {len(pairs):,} pairs", flush=True)
    chunks = pd.read_csv(vpath, sep="\t", dtype=str, keep_default_na=False, chunksize=2_000_000,
                         usecols=["subject", "predicate", "object", "source", "confidence", "verdict"])
    counts, conf, corr, pruned, n_self, n_pairs = tally_rows(chunks, pairs)
    s = summarise(counts, conf, corr, pruned, n_self, n_pairs, n, len(pairs), [_describe(a.intact), _describe(vpath)])
    (out / "ppi_corroboration.json").write_text(json.dumps(s, indent=1, sort_keys=True))
    (out / "ppi_corroboration.md").write_text("\n".join(report(s)) + "\n")
    print("\n".join(report(s)[:12]))
    if a.figure:
        figure(s, a.figure)
        print("figure ->", Path(a.figure).relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
