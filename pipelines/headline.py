"""
The project's headline result, computed from the committed result files (CC-33, review findings M6 and m8).

Every number in the headline is read from a file in outputs/ and every verb follows the numbers (CLAUDE.md rule 7): a
difference is "higher" only when its 95% interval lies above 0, "lower" only when it lies below, otherwise "no clear
difference". The same text goes into README.md (between the headline markers), the Numbers page intro (/api/results) and
the landing JSON (/api/landing), so the three never drift apart.

    python pipelines/headline.py            # print the headline and its numbers
    python pipelines/headline.py --readme   # rewrite every generated block (README.md, docs/SUBMISSION.md, docs/AGENT_LOOP.md,
                                            # docs/EXPERIMENTS_LINKPRED.md); --write is the same

Which graph the headline is about (PK5, finding ES-05): the graph that is shipped, i.e. the 9 November rebuild that the
downloads and the card index hold. The same model on the two earlier builds of the graph is kept in ONE history table
(history(), written into docs/EXPERIMENTS_LINKPRED.md) and is never quoted as the headline. Three controls were run once,
on the 4 November build, and were not repeated: the headline says so in the sentence that uses them.

The agent paragraph (PK5, finding ES-06) is also written from here, so README.md, docs/SUBMISSION.md and docs/AGENT_LOOP.md
carry the same words: AGENT_CLAUSES holds, per step, the wording that is true today and the wording of the sprint plan
that becomes true when the named packet is merged; AGENT_SHIPPED lists the merged packets.

  COMPARISONS                    the paired comparisons the headline rests on (variant file, base file, metric)
  BUILD                          comparison name -> the build of the graph it was measured on (a key of BUILD_LABEL)
  HISTORY                        the same model on each build of the graph: (build, enriched file, pruned file, base file)
  comparisons(root)              -> {name: linkpred.compare.paired_delta + files + build}  (None for a missing file)
  prune_numbers(root)            -> the pruning numbers read from outputs/pharos/prune_report.md and outputs/g2|g3/summary.md
  headline(root)                 -> {sentences, text, lead, numbers, files}
  lead(root)                     -> the one-sentence result that opens the README ("" when its files are missing)
  history(root)                  -> [{build, label, shipped, g2, g3}] for the history table
  history_markdown(rows)         -> the history table as Markdown
  agent_statement(shipped)       -> the agent paragraph, built from AGENT_CLAUSES
  run_log(root)                  -> [{date, ledger, agent, decision, in_ledger}]: the dated "what the agent did" log
  run_log_markdown(rows)         -> the log as a Markdown table
  render_block(text, block, start, end) -> text with the part between two markers replaced
  render_readme(text, block)     -> README text with the block between START and END replaced
  write_blocks(root)             -> rewrites every generated block; returns the files it changed
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
LP = Path("outputs") / "linkpred"
PRUNE_REPORT = Path("outputs") / "pharos" / "prune_report.md"
PRUNE_CALIBRATION = Path("outputs") / "pharos" / "prune_calibration.json"
START, END = "<!-- headline:start -->", "<!-- headline:end -->"

LEAD_START, LEAD_END = "<!-- lead:start -->", "<!-- lead:end -->"
AGENT_START, AGENT_END = "<!-- agent:start -->", "<!-- agent:end -->"
HISTORY_START, HISTORY_END = "<!-- headline-history:start -->", "<!-- headline-history:end -->"
RUNLOG_START, RUNLOG_END = "<!-- runlog:start -->", "<!-- runlog:end -->"

# The base of every GNN + paths comparison on the shipped graph: Pharos alone, run on the same GPU as the enriched graphs
# (ledger rows "D" and "D (CS-9 graphs)"; the Mac run of the same graph differs by -0.0001, EXPERIMENTS_LINKPRED section D).
_G0_GPU = "G0_B_kfold@diag_gnnpath_1m_gpu.json"
_G0_MAC = "G0_B_kfold@diag_gnnpath_1m.json"

# name -> (variant result file, base result file, metric). The shipped graph (ledger "D (CS-9 graphs)") for the path model
# and for pruning; the frozen run for the plain R-GCN, the equal-epoch RotatE run, the MONDO control and the random-pruning
# control (ledger frozen_a, E006, E008, E009), which ran once on the 4 November build.
COMPARISONS = {
    "paths_g2": ("G2_B_kfold@diag_gnnpath_1m_cs9.json", _G0_GPU, "auroc_gnnpath"),
    "paths_g3": ("G3_B_kfold@diag_gnnpath_1m_cs9.json", _G0_GPU, "auroc_gnnpath"),
    "paths_g2_e008": ("G2_B_kfold@diag_gnnpath_1m.json", _G0_MAC, "auroc_gnnpath"),
    "paths_nomondo": ("G2_B_kfold@diag_gnnpath_1m_nomondo.json", _G0_MAC, "auroc_gnnpath"),
    "paths_pruning": ("G3_B_kfold@diag_gnnpath_1m_cs9.json", "G2_B_kfold@diag_gnnpath_1m_cs9.json", "auroc_gnnpath"),
    "rgcn_g2": ("G2_B_kfold@frozen_a.json", "G0_B_kfold@frozen_a.json", "auroc"),
    "rotate_g2": ("G2_B_kfold@diag_kge_rotate_d256.json", "G0_B_kfold@diag_kge_rotate_d256.json", "auroc"),
    "rotate_vs_random": ("G3_B_kfold@diag_kge_rotate_d256_e009.json", "G2R_B_kfold@diag_kge_rotate_d256_e009.json", "auroc"),
}

# The build of the graph each comparison was measured on. Pharos alone is the same in every build; the enriched and pruned
# graphs were rebuilt twice after the first runs (docs/EXPERIMENTS_LINKPRED.md, sections D).
BUILD_LABEL = {"shipped": "the shipped graph (9 November build)", "release": "the 7 November build",
               "nov4": "the 4 November build"}
BUILD = {"paths_g2": "shipped", "paths_g3": "shipped", "paths_pruning": "shipped", "paths_g2_e008": "nov4",
         "paths_nomondo": "nov4", "rgcn_g2": "nov4", "rotate_g2": "nov4", "rotate_vs_random": "nov4"}

# The same model (GNN + paths) on each build of the graph, newest first: (build, enriched file, pruned file, base file).
# Only the first row is quoted as the result; the others are history.
HISTORY = [
    ("shipped", "G2_B_kfold@diag_gnnpath_1m_cs9.json", "G3_B_kfold@diag_gnnpath_1m_cs9.json", _G0_GPU),
    ("release", "G2_B_kfold@diag_gnnpath_1m_release.json", "G3_B_kfold@diag_gnnpath_1m_release.json", _G0_GPU),
    ("nov4", "G2_B_kfold@diag_gnnpath_1m.json", "G3_B_kfold@diag_gnnpath_1m_gpu.json", _G0_GPU),
]

# The agent paragraph, clause by clause (docs/SPRINT_PLAN_2026-11-11.md section 2: "use only the clauses whose packet has
# shipped"). `full` is the plan's wording, true once `packet` is merged; `now` is what is true without it. To switch a
# clause on: add its packet id to AGENT_SHIPPED and run `python pipelines/headline.py --readme`.
AGENT_CLAUSES = [
    dict(step="Ingest", packet="PK8",
         full="it drafts the source card for a new table and builds from it once a scientist approves",
         now="it builds every layer from a source card that a scientist wrote or approved"),
    dict(step="Filter", packet="PK4",
         full="a language model turns each question into a validated graph query and summarises the evidence behind each answer",
         now="a language model turns each question into a validated graph query"),
    dict(step="Clean", packet="PK11",
         full="a pruner scores every edge and an auditing step checks edges against literature and interaction databases",
         now="a pruner scores every edge"),
    dict(step="Maintain", packet="PK9", full="a scheduled check watches the sources",
         now="a person starts the re-pull, rebuild and comparison that keep it current"),
    dict(step="log", packet="PK9", full="every decision is in a dated log",
         now="every experiment and decision is in a dated ledger"),
]
AGENT_SHIPPED = frozenset({"PK4", "PK8", "PK9", "PK11"})   # packet ids that are merged and deployed (FIX1 F11: PK4, PK11 added)


def agent_statement(shipped=AGENT_SHIPPED):
    """Takes the set of merged packet ids. Returns the agent paragraph: the sprint plan's sentence, each clause in its
    `full` wording when its packet is in `shipped`, else in its `now` wording (never a claim whose code is not merged)."""
    parts = [c["full"] if c["packet"] in shipped else c["now"] for c in AGENT_CLAUSES]
    return ("TrialMindKG is a semi-autonomous agent. It runs the loop Ingest, Filter, Clean, Limit, Maintain: "
            + "; ".join(parts) + ".")


# The dated "what the agent did" log (PK5, finding ES-06; plan item A4). One row per ledger entry (date and id exactly as
# in loop/ledger.tsv, checked by run_log); {name} is filled with the paired difference of that COMPARISONS entry, so no
# number is typed here. the third field is the rule or decision the ledger row records (never an approval the ledger does not hold).
LEDGER = Path("loop") / "ledger.tsv"
RUN_LOG = [
    ("2026-11-04", "E001", "Ran the frozen benchmark. With the enrichment the plain graph network {rgcn_g2_verb} ({rgcn_g2}).",
     "Hypothesis recorded as rejected; the protocol stayed as frozen."),
    ("2026-11-06", "E008", "Ran the path-aware model that was registered before the run: {paths_g2_e008} over Pharos alone "
     "on that day's build.", "Passed its rule, set before the run; the control without the disease hierarchy was queued "
     "before any claim."),
    ("2026-11-06", "E009", "Ran the random-pruning control: the pruned graph against one with the same number of edges "
     "removed at random ({rotate_vs_random}).", "Registered and approved by the scientist before the graph or any run existed."),
    ("2026-11-07", "E008 control", "Finished the control: without the MONDO disease hierarchy the enriched graph {paths_nomondo_verb} "
     "than Pharos alone ({paths_nomondo}).", "Reported next to the result ever since."),
    ("2026-11-07", "DECISION-OT-classes", "Reported that layers gave the same identifier different classes.",
     "The scientist chose one class per identifier prefix in every product."),
    ("2026-11-07", "DECISION-MeSH-exclusions", "Listed disease mappings whose cross-reference looked wrong, for review.",
     "The scientist excluded five of them; those rows keep their original identifier."),
    ("2026-11-09", "CS-9 part 1", "Rebuilt the graph with the protein-class layer, scored every edge again, rebuilt the "
     "card index and the download files.", "Recorded as done; this is the build the downloads hold."),
    ("2026-11-10", "D (CS-9 graphs)", "Ran the same model once on the shipped graph. Enriched: {paths_g2}. Pruned: {paths_g3}.",
     "Run once, no tuning; this is the result that is quoted."),
    ("2026-11-11", "CS-4-spotcheck-amend1", "Drew the blinded 200-edge spot check again, from the shipped verdicts.",
     "Amendment written before any row was rated."),
]


def run_log(root=ROOT, c=None):
    """Takes the repo root (and optionally comparisons()). Returns the dated log, oldest first: [{date, ledger, agent,
    decision, in_ledger}]. A row whose comparison files are missing is left out; in_ledger says loop/ledger.tsv holds a
    row with that date and id (the test requires it for every row)."""
    c = c or comparisons(root)
    seen = set()
    p = Path(root) / LEDGER
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines()[1:]:
            cells = line.split("\t")
            if len(cells) > 1:
                seen.add((cells[0], cells[1]))
    fill = {k: f"{_d(d['mean'])} AUROC, 95% CI {_d(d['ci_low'])} to {_d(d['ci_high'])}" for k, d in c.items() if d}
    # the verb follows the interval (CLAUDE.md rule 7): scored higher / scored lower / did not score clearly differently
    fill.update({k + "_verb": {"higher": "scored higher", "lower": "scored lower"}.get(_verb(d), "did not score clearly differently")
                 for k, d in c.items() if d})
    out = []
    for date, lid, agent, decision in RUN_LOG:
        try:
            text = agent.format(**fill)
        except KeyError:
            continue
        out.append(dict(date=date, ledger=lid, agent=text, decision=decision, in_ledger=(date, lid) in seen))
    return out


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def run_log_markdown(rows):
    """Takes run_log() rows. Returns the Markdown table (date, what the agent did, the rule or decision on record, the id
    of the ledger row that records it)."""
    lines = ["| date (2026) | what the agent did | rule or decision on record | ledger row |", "|---|---|---|---|"]
    for r in rows:
        day = f"{int(r['date'][8:10])} {_MONTHS[int(r['date'][5:7]) - 1]}"
        lines.append(f"| {day} | {r['agent']} | {r['decision']} | `{r['ledger']}` |")
    return "\n".join(lines) + "\n"


def run_log_block(root, c=None):
    """The log with its one-line introduction, for README.md and docs/AGENT_LOOP.md."""
    return ("Each row is an entry of the dated ledger `loop/ledger.tsv` (written at the time, including the rejected "
            "hypotheses); the numbers are recomputed from the result files by `pipelines/headline.py`.\n\n"
            + run_log_markdown(run_log(root, c)))


def comparisons(root=ROOT):
    """Takes the repo root. Returns {name: dict(paired_delta fields n, mean, ci_low, ci_high, wins, digest_match, variant,
    base, metric)} for every COMPARISONS entry, None when a file is missing or unreadable."""
    from linkpred import compare
    out = {}
    for name, (v, b, m) in COMPARISONS.items():
        try:
            rv, rb = (json.loads((Path(root) / LP / f).read_text()) for f in (v, b))
        except (OSError, ValueError):
            out[name] = None
            continue
        d = compare.paired_delta(rv, rb, m)
        out[name] = dict(d, variant=v, base=b, metric=m, build=BUILD.get(name, "")) if d["n"] else None
    return out


def _paired(root, variant, base, metric="auroc_gnnpath"):
    """Takes the repo root and two result file names. Returns linkpred.compare.paired_delta of variant minus base, or None
    when a file is missing, unreadable or shares no fold."""
    from linkpred import compare
    try:
        rv, rb = (json.loads((Path(root) / LP / f).read_text()) for f in (variant, base))
    except (OSError, ValueError):
        return None
    d = compare.paired_delta(rv, rb, metric)
    return dict(d, variant=variant, base=base, metric=metric) if d["n"] else None


def history(root=ROOT):
    """Takes the repo root. Returns one row per build of the graph, newest first: {build, label, shipped (True for the
    build that is shipped), g2, g3} where g2 / g3 are the paired gain of the enriched / pruned graph over Pharos alone
    (None when a file is missing)."""
    return [dict(build=b, label=BUILD_LABEL[b], shipped=b == "shipped", g2=_paired(root, g2, base), g3=_paired(root, g3, base))
            for b, g2, g3, base in HISTORY]


def history_markdown(rows):
    """Takes history() rows. Returns the Markdown table: one row per build, the gain over Pharos alone with its interval
    to four decimals, and the result files (a missing result is written as 'not run')."""
    def cell(d):
        return ("not run" if not d else
                f"{d['mean']:+.4f} ({d['ci_low']:+.4f} to {d['ci_high']:+.4f}; {d['wins']}/{d['n']})")

    def files(r):
        fs = [d["variant"] for d in (r["g2"], r["g3"]) if d] + [d["base"] for d in (r["g2"],) if d]
        return ", ".join(f"`{f}`" for f in fs) or "-"
    lines = ["| Build of the graph | Quoted as the result? | Enriched minus Pharos alone (95% CI; folds higher) | "
             "Pruned minus Pharos alone | Result files |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['label']} | {'**yes**' if r['shipped'] else 'no, history only'} | {cell(r['g2'])} | "
                     f"{cell(r['g3'])} | {files(r)} |")
    return "\n".join(lines) + "\n"


def _int(s):
    return int(s.replace(",", ""))


def prune_numbers(root=ROOT):
    """Takes the repo root. Returns the pruning numbers, each read from a committed file (None when it is not there):
    scored, pruned, pruned_share (Pharos layer, prune_report.md), text_share_of_pruned, nontext_gd_pruned_in_ot and
    nontext_gd_kept_in_ot (its shipped-verdicts threshold row), nontext_gd_pruned_n, nontext_gd_pruned_in_ot_n and
    nontext_gd_kept_n (how many links those shares are of, from prune_calibration.json), ppi_pruned (its by-predicate row physically_interacts_with),
    g2_edges, g3_edges, g3_removed (outputs/g2|g3/summary.md)."""
    root = Path(root)
    out = dict.fromkeys(["scored", "pruned", "pruned_share", "text_share_of_pruned", "nontext_gd_pruned_in_ot",
                         "nontext_gd_kept_in_ot", "nontext_gd_pruned_n", "nontext_gd_pruned_in_ot_n", "nontext_gd_kept_n",
                         "ppi_pruned", "g2_edges", "g3_edges", "g3_removed"])
    p = root / PRUNE_REPORT
    if p.is_file():
        t = p.read_text(encoding="utf-8")
        m = re.search(r"edges scored: \*\*([\d,]+)\*\*; verdict 'prune': \*\*([\d,]+)\*\* \(\*\*([\d.]+)%\*\*\)", t)
        if m:
            out.update(scored=_int(m.group(1)), pruned=_int(m.group(2)), pruned_share=float(m.group(3)))
        row = re.search(r"^\| shipped verdicts[^\n]*$", t, re.M)
        if row:
            cells = [c.strip() for c in row.group(0).strip("|").split("|")]
            # threshold | cut | pruned | share all | share G-D | text share of pruned | non-text pruned n / in OT / OT>=0.2 | kept in OT / OT>=0.2 | ...
            out["text_share_of_pruned"] = float(cells[5].rstrip("%"))
            out["nontext_gd_pruned_in_ot"] = float(cells[6].split("/")[1].strip().rstrip("%"))
            out["nontext_gd_kept_in_ot"] = float(cells[7].split("/")[0].strip().rstrip("%"))
        m = re.search(r"^\| biolink:physically_interacts_with \| ([\d,]+) \| ([\d,]+) \|", t, re.M)
        if m:
            out["ppi_pruned"] = _int(m.group(2))
    c = root / PRUNE_CALIBRATION                       # FIX2 F3: the n behind the Open Targets cross-check
    if c.is_file():
        try:
            v = {r["verdict"]: r for r in json.loads(c.read_text(encoding="utf-8")).get("by_verdict", [])}
        except (ValueError, KeyError, TypeError, AttributeError):
            v = {}
        if "prune" in v and "keep" in v:
            out.update(nontext_gd_pruned_n=int(v["prune"]["n"]), nontext_gd_kept_n=int(v["keep"]["n"]),
                       nontext_gd_pruned_in_ot_n=int(round(v["prune"]["n"] * v["prune"]["in_ot"])))
    for g in ("g2", "g3"):
        s = root / "outputs" / g / "summary.md"
        if s.is_file():
            t = s.read_text(encoding="utf-8")
            m = re.search(r"([\d,]+) nodes, ([\d,]+) edges(?:; ([\d,]+) edges removed by pruning)?", t)
            if m:
                out[f"{g}_edges"] = _int(m.group(2))
                if g == "g3" and m.group(3):
                    out["g3_removed"] = _int(m.group(3))
    return out


def _d(x):
    return f"{x:+.3f}"


def _ci(d):
    return f"95% CI {_d(d['ci_low'])} to {_d(d['ci_high'])}, {d['wins']} of {d['n']} folds higher"


def _verb(d, up="higher", down="lower"):
    return up if d["ci_low"] > 0 else down if d["ci_high"] < 0 else "not clearly different"


def lead(root=ROOT, c=None):
    """Takes the repo root (and optionally comparisons()). Returns the one-sentence result that opens the README, built
    from the shipped-graph comparison; "" when its files are missing."""
    c = c or comparisons(root)
    g2, g3 = c.get("paths_g2"), c.get("paths_g3")
    if not g2:
        return ""
    how = {"higher": "ranks held-out curated gene-disease links better", "lower": "ranks held-out curated gene-disease links worse",
           }.get(_verb(g2), "does not clearly rank held-out curated gene-disease links differently")
    txt = (f"The same path-aware graph model {how} on the enriched graph than on Pharos alone: {_d(g2['mean'])} AUROC "
           f"(95% CI {_d(g2['ci_low'])} to {_d(g2['ci_high'])}; higher in {g2['wins']} of {g2['n']} folds)")
    if g3:
        txt += f", and {_d(g3['mean'])} after pruning (95% CI {_d(g3['ci_low'])} to {_d(g3['ci_high'])})"
    return txt + "."


def _pct(a, b):
    return 100.0 * a / b


def ot_check_sentence(p):
    """Takes prune_numbers(). Returns the Open Targets cross-check as one sentence with its n (FIX2 F3, G2-AC-01): how many
    of the pruned non-text-mined gene-disease edges also appear in Open Targets, the kept share, and what part of all pruned
    edges those are. Without the calibration file only the two shares are given."""
    pr, kp = p["nontext_gd_pruned_in_ot"], p["nontext_gd_kept_in_ot"]
    n, k = p.get("nontext_gd_pruned_n"), p.get("nontext_gd_pruned_in_ot_n")
    if not n or k is None:
        return (f"Pruned gene-disease edges that are not text-mined also appear in Open Targets (which the pruner never sees) "
                f"{pr:.1f}% of the time, kept ones {kp:.1f}%.")
    txt = (f"Of the {n:,} pruned gene-disease edges that are not text-mined, {k:,} ({pr:.1f}%) also appear in Open Targets "
           f"(which the pruner never sees), against {kp:.1f}% of the {p['nontext_gd_kept_n']:,} kept ones")
    if p.get("pruned"):
        txt += f"; those {n:,} are {_pct(n, p['pruned']):.2f}% of the {p['pruned']:,} pruned edges"
        other = (p.get("ppi_pruned") or 0) + (p.get("text_share_of_pruned") or 0) * p["pruned"] / 100.0
        if other > p["pruned"] / 2:                  # computed from the report's protein-protein and text shares
            txt += ", most of which are protein-protein or text-mined"
        if n < p["pruned"] / 2:
            txt += ", so this check covers few of them"
    return txt + "."


def headline(root=ROOT):
    """Takes the repo root. Returns {sentences, text, numbers, files}: the headline as sentences built from comparisons() and
    prune_numbers(); a sentence whose files are missing is left out (never filled with a guess)."""
    c, p = comparisons(root), prune_numbers(root)
    s = []
    g2, g3, rg, rt = c["paths_g2"], c["paths_g3"], c["rgcn_g2"], c["rotate_g2"]
    if g2:
        v = _verb(g2)
        how = f"scores {v}" if v in ("higher", "lower") else "does not score clearly differently"
        txt = (f"On the held-out curated Pharos gene-disease pairs, a graph neural network that also counts the paths between a "
               f"gene and a disease (GNN + paths) {how} on the enriched graph that is shipped than on Pharos alone: "
               f"{_d(g2['mean'])} AUROC ({_ci(g2)})")
        if g3:
            txt += f"; {_d(g3['mean'])} on its pruned version ({_ci(g3)})"
        s.append(txt + ".")
    if rg and rt:
        def plain(d, name):
            v = _verb(d)
            return (f"{name} {'gains' if v == 'higher' else 'scores lower' if v == 'lower' else 'does not gain'} "
                    f"({_d(d['mean'])}, {_ci(d)})")
        lead_ = ("Without path counts the enrichment does not help" if "higher" not in (_verb(rg), _verb(rt))
                 else "Without path counts")
        when = f" (both measured on {BUILD_LABEL[rg['build']]})" if rg["build"] == rt["build"] != BUILD["paths_g2"] else ""
        s.append(f"{lead_}: {plain(rg, 'the plain R-GCN of the frozen protocol')}, and {plain(rt, 'a RotatE embedding')}{when}.")
    full, nm = c["paths_g2_e008"], c["paths_nomondo"]
    # a share is stated only when the full gain is clearly above 0 and the gain without MONDO lies between 0 and it
    if full and nm and full["ci_low"] > 0 and 0 <= nm["mean"] <= full["mean"]:
        share = 1 - nm["mean"] / full["mean"]
        s.append(f"About {round(100 * share)}% of the GNN + paths gain comes from the MONDO disease hierarchy (a disease scores "
                 f"well because the gene is already linked to a parent or child term): on {BUILD_LABEL[nm['build']]}, where "
                 f"this control was run, the gain without MONDO is {_d(nm['mean'])} ({_ci(nm)}) instead of {_d(full['mean'])}; "
                 f"the control was not repeated on the shipped graph.")
    if p["pruned"] and p["scored"]:
        txt = (f"Pruning marks {p['pruned']:,} of the {p['scored']:,} Pharos edges ({p['pruned_share']:.2f}%) as prune; "
               f"{p['text_share_of_pruned']:.1f}% of them are text co-occurrence")
        if p["ppi_pruned"]:
            txt += f" and {_pct(p['ppi_pruned'], p['pruned']):.0f}% protein-protein interaction rows"
        if p["g3_removed"] and p["g2_edges"]:
            txt += (f"; across all layers the pruned graph has {p['g3_removed']:,} fewer edges "
                    f"({_pct(p['g3_removed'], p['g2_edges']):.2f}% of {p['g2_edges']:,})")
        s.append(txt + ".")
    pr, rr = c["paths_pruning"], c["rotate_vs_random"]
    if pr and rr:
        s.append(f"Pruning {'raises' if pr['ci_low'] > 0 else 'lowers' if pr['ci_high'] < 0 else 'does not clearly change'} the "
                 f"GNN + paths score on the shipped graph ({_d(pr['mean'])}, {_ci(pr)}); for RotatE, on "
                 f"{BUILD_LABEL[rr['build']]}, the pruned graph scores {_verb(rr)} "
                 f"{'than' if _verb(rr) in ('higher', 'lower') else 'from'} a graph with the same number of edges removed "
                 f"at random ({_d(rr['mean'])}, {_ci(rr)}).")
    if p["nontext_gd_pruned_in_ot"] is not None and p["nontext_gd_kept_in_ot"] is not None:
        s.append(ot_check_sentence(p) + " No gold standard of wrong edges exists, so this is confidence re-weighting, "
                 "not proven error removal.")
    if s:
        s.append("AUROC here is a ranking score on a benchmark, not biological accuracy.")
    files = sorted({f for d in c.values() if d for f in (d["variant"], d["base"])})
    return dict(sentences=s, text=" ".join(s), lead=lead(root, c), numbers=dict(comparisons=c, pruning=p),
                files=[str(LP / f) for f in files] + [str(PRUNE_REPORT)]
                + ([str(PRUNE_CALIBRATION)] if p.get("nontext_gd_pruned_n") else []) + ["outputs/g2/summary.md", "outputs/g3/summary.md"])


def render_block(text, block, start, end):
    """Takes a document text, a block and its two marker lines. Returns the text with the part between the markers
    replaced by the block (ValueError when a marker is missing)."""
    if start not in text or end not in text:
        raise ValueError(f"no {start} ... {end} markers")
    a, rest = text.split(start, 1)
    _, b = rest.split(end, 1)
    return a + start + "\n" + block.rstrip("\n") + "\n" + end + b


def render_readme(text, block):
    """Takes the README text and the headline block. Returns the text with the part between START and END replaced
    (ValueError when the markers are missing)."""
    try:
        return render_block(text, block, START, END)
    except ValueError:
        raise ValueError("README.md has no headline markers") from None


def readme_block(h):
    """The README block: the headline paragraph and the files it was read from."""
    return ("**The result, in one paragraph** (computed by `pipelines/headline.py` from the files listed under it; the app's "
            "Numbers page shows the same text):\n\n" + h["text"] + "\n\nRead from: " + ", ".join(f"`{f}`" for f in h["files"]) + ".\n")


def lead_block(h):
    """The README's first block: the one-sentence result (from the shipped-graph files) in bold."""
    return f"**{h['lead']}**\n" if h.get("lead") else ""


def history_block(root=ROOT):
    """The history table with its one-line introduction, for docs/EXPERIMENTS_LINKPRED.md."""
    return ("Written by `python pipelines/headline.py --readme` from the result files in `outputs/linkpred/`; do not edit by "
            "hand. Pharos alone is the same graph in every row.\n\n" + history_markdown(history(root)))


# file -> [(start marker, end marker, function(root, headline) -> block)]: every generated block, in one place.
BLOCKS = {
    "README.md": [(LEAD_START, LEAD_END, lambda root, h: lead_block(h)),
                  (AGENT_START, AGENT_END, lambda root, h: agent_statement() + "\n"),
                  (RUNLOG_START, RUNLOG_END, lambda root, h: run_log_block(root, h["numbers"]["comparisons"])),
                  (START, END, lambda root, h: readme_block(h))],
    "docs/SUBMISSION.md": [(AGENT_START, AGENT_END, lambda root, h: agent_statement() + "\n")],
    "docs/AGENT_LOOP.md": [(AGENT_START, AGENT_END, lambda root, h: agent_statement() + "\n"),
                           (RUNLOG_START, RUNLOG_END, lambda root, h: run_log_block(root, h["numbers"]["comparisons"]))],
    "docs/EXPERIMENTS_LINKPRED.md": [(HISTORY_START, HISTORY_END, lambda root, h: history_block(root))],
}


def expected(root=ROOT):
    """Takes the repo root. Returns {file: text with every generated block rebuilt} for the files that exist and carry
    their markers (a file without its markers raises ValueError: a block must never silently disappear)."""
    root = Path(root)
    h = headline(root)
    out = {}
    for rel, blocks in BLOCKS.items():
        p = root / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8")
        for start, end, make in blocks:
            text = render_block(text, make(root, h), start, end)
        out[rel] = text
    return out


def write_blocks(root=ROOT):
    """Takes the repo root. Rewrites every generated block; returns the list of files whose text changed."""
    changed = []
    for rel, text in expected(root).items():
        p = Path(root) / rel
        if p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8")
            changed.append(rel)
    return changed


if __name__ == "__main__":
    if "--readme" in sys.argv or "--write" in sys.argv:
        changed = write_blocks()
        print("generated blocks rewritten in: " + (", ".join(changed) or "nothing (all up to date)"))
    else:
        h = headline()
        print(h["text"])
