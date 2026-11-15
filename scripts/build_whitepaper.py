"""Build docs/WHITEPAPER.md from committed result files.

    .venv/bin/python scripts/build_whitepaper.py            # writes docs/WHITEPAPER.md

Every number in the paper is read by this script from the file that holds it (a regular expression on one named line
of a Markdown file, or a key of a JSON file) and recorded with its file and line or key. The template below contains no
data values: `check_template_digits` fails the build if a digit appears in the template outside a short list of product
names, and `check_body_numbers` fails it if a number in the written text is not one of the recorded values. A
"Sources of numbers" appendix is written at the end of the paper. A value that cannot be found stops the build.

Source files are checked with `git ls-files`; a value read from an untracked file is listed as a deviation in the
appendix.

The spot-check breakdown (kept rows judged unsupported, by evidence type) is computed from the label files the scorer
uses (scripts/score_spotcheck.py: final_labels on the raters' labels, the owner's 40 blind rows and her adjudications),
joined to the verdict in the blinded key and to the row's source, knowledge level and predicate in the blind sheet.
Only aggregate counts are written; no row of the key is printed or written.

The architecture figure is inserted only if docs/figures/architecture.png exists when the script runs; otherwise a
named placeholder is written.

PDF (not done by this script; run from a separate environment that has pandoc and weasyprint):
    cd docs && pandoc WHITEPAPER.md -s --metadata title="TrialMindKG white paper" --css <css> -o <html>
    && weasyprint <html> WHITEPAPER.pdf
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
OUT_MD = DOCS / "WHITEPAPER.md"

N = {}            # id -> value (string, exactly as written in the source file unless noted)
SRC = {}          # id -> (file, line or key)
ORDER = []
DEVIATIONS = []
_TRACKED = {}


def tracked(rel):
    if rel not in _TRACKED:
        env = dict(os.environ, HOME="/tmp", GIT_CONFIG_GLOBAL="/dev/null")
        r = subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=ROOT, env=env,
                           capture_output=True, text=True)
        _TRACKED[rel] = r.returncode == 0
        if not _TRACKED[rel]:
            DEVIATIONS.append(f"`{rel}` is not tracked by git; values read from it are listed in the table below.")
    return _TRACKED[rel]


def put(nid, value, rel, where):
    if nid in N:
        raise SystemExit(f"duplicate number id {nid}")
    N[nid] = str(value)
    SRC[nid] = (rel, where)
    ORDER.append(nid)


def line_rx(rel, pattern, names):
    """First line of `rel` matching `pattern`; its groups become the numbers `names` (None = group not kept)."""
    tracked(rel)
    lines = (ROOT / rel).read_text(encoding="utf-8").splitlines()
    rx = re.compile(pattern)
    for i, line in enumerate(lines, 1):
        m = rx.search(line)
        if m:
            if len(m.groups()) != len(names):
                raise SystemExit(f"{rel}: pattern has {len(m.groups())} groups, {len(names)} names")
            for nid, val in zip(names, m.groups()):
                if nid:
                    put(nid, val, rel, f"line {i}")
            return
    raise SystemExit(f"NOT FOUND in {rel}: {pattern}")


def json_key(rel, path, nid, fmt=None):
    tracked(rel)
    obj = json.loads((ROOT / rel).read_text(encoding="utf-8"))
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        obj = obj[int(part[1:-1])] if part.startswith("[") else obj[part]
    put(nid, fmt(obj) if fmt else obj, rel, f"key {path}")
    return obj


# ---------------------------------------------------------------------------------------------------- numbers
SIGNED = r"([-+]\d+\.\d+)"
PCT = r"(\d+\.\d+%)"
INT = r"(\d[\d,]*)"


def read_numbers():
    cmp_ = "outputs/linkpred/compare_cs9_gnnpath.md"
    line_rx(cmp_, r"^\| Variant \| paired folds \| mean difference \| (\d+%) CI \|", ["ci_level"])
    for tag, p in (("enr", "G2"), ("prn", "G3")):
        line_rx(cmp_, rf"^\| {p}@diag_gnnpath_1m_cs9 \| (\d+) \| {SIGNED} \| {SIGNED} to {SIGNED} \| (\d+) \| (\w+) \|$",
                [f"{tag}_n", f"{tag}_d", f"{tag}_lo", f"{tag}_hi", f"{tag}_folds", None])

    lp = "outputs/linkpred"
    folds = {}
    for g, f in (("pha", "G0_B_kfold@diag_gnnpath_1m_gpu.json"), ("enr", "G2_B_kfold@diag_gnnpath_1m_cs9.json"),
                 ("prn", "G3_B_kfold@diag_gnnpath_1m_cs9.json")):
        rel = f"{lp}/{f}"
        json_key(rel, "per_seed[0].auroc_gnnpath", f"auroc_{g}", lambda v: f"{v:.4f}")
        d = json.loads((ROOT / rel).read_text())
        folds[g] = [x["n_test"] for x in d["per_seed"][0]["folds"]]
    if not (folds["pha"] == folds["enr"] == folds["prn"]):
        raise SystemExit("per-fold test counts differ between the three graphs")
    rel = f"{lp}/G2_B_kfold@diag_gnnpath_1m_cs9.json"
    put("ntest_min", f"{min(folds['enr']):,}", rel, "min over key per_seed[0].folds[*].n_test (same in the G0 and G3 files)")
    put("ntest_max", f"{max(folds['enr']):,}", rel, "max over key per_seed[0].folds[*].n_test (same in the G0 and G3 files)")

    exp = "docs/EXPERIMENTS_LINKPRED.md"
    line_rx(exp, rf"^\| R-GCN, frozen protocol .*\| {SIGNED} \({SIGNED} to {SIGNED}\), (\d/\d) \|",
            ["rgcn_d", "rgcn_lo", "rgcn_hi", "rgcn_folds"])
    line_rx(exp, rf"without the MONDO hierarchy\*\* \(control\).*\*\*{SIGNED} \({SIGNED} to {SIGNED}\), (\d/\d)\*\*",
            ["nomondo_d", "nomondo_lo", "nomondo_hi", "nomondo_folds"])
    line_rx(exp, r"Most of the path gain is the MONDO disease hierarchy\*\* \(E005: about (\d+%)\)", ["mondo_share_meta"])
    line_rx(exp, r"MONDO carries ~(\d+%) of that model's gain", ["mondo_share_gnn"])
    line_rx(exp, rf"^\| RotatE embedding, dim 256 \|.*\*\*{SIGNED} \({SIGNED} to {SIGNED}\), (\d/\d)\*\* \| E006",
            ["rot_d", "rot_lo", "rot_hi", "rot_folds"])
    line_rx(exp, rf"^\| RotatE dim 256, random-pruned control.*G2R-G2 {SIGNED} \({SIGNED} to {SIGNED}\), (\d/\d) \| "
                 rf"\*\*G3-G2R {SIGNED} \({SIGNED} to {SIGNED}\), (\d/\d)\*\*",
            ["g2r_d", "g2r_lo", "g2r_hi", "g2r_folds", "rand_d", "rand_lo", "rand_hi", "rand_folds"])

    pr = "outputs/pharos/prune_report.md"
    line_rx(pr, rf"edges scored: \*\*{INT}\*\*; verdict 'prune': \*\*{INT}\*\* \(\*\*{PCT}\*\*\)",
            ["pr_scored", "pr_prune", "pr_pct"])

    sc = "outputs/pharos/spotcheck_blind_result.md"
    line_rx(sc, r"^- Pruned edges judged unsupported: \*\*(\d+) of (\d+)\*\* \(threshold >= (\d+)\)",
            ["sc_pu", "sc_pn", "sc_pthr"])
    line_rx(sc, r"^- Kept edges judged unsupported: \*\*(\d+) of (\d+)\*\* \(threshold <= (\d+)\)",
            ["sc_ku", "sc_kn", "sc_kthr"])
    line_rx(sc, r"^- Result: \*\*(\w+)\*\*", ["sc_result"])
    line_rx(sc, r"^\| prune \| (\d+) \| (\d+) \| (\d+) \|$", [None, "sc_p_sup", "sc_p_ct"])
    line_rx(sc, r"^\| keep \| (\d+) \| (\d+) \| (\d+) \|$", [None, "sc_k_sup", "sc_k_ct"])
    line_rx(sc, r"^- the owner's blind label \(her (\d+) rows\): (\d+) rows", ["sc_own_n", "sc_own_used"])
    line_rx(sc, r"^- all three raters agree: (\d+) rows", ["sc_agree3"])
    line_rx(sc, r"^- flagged row, adjudicated by the owner: (\d+) rows", ["sc_adj"])
    line_rx(sc, r"^- flagged row, not adjudicated: majority label: (\d+) rows", ["sc_major"])
    line_rx(sc, r"^\| all three \| (\d+) \| (\d+) \(" + PCT + r"\)", ["sc_rows", "sc_all3", "sc_all3_pct"])
    line_rx(sc, r"on the rows she rated: (\d+) of (\d+) agree \(" + PCT + r"\), Cohen's kappa (-?\d+\.\d+)",
            ["sc_hum_agree", "sc_hum_n", "sc_hum_pct", "sc_kappa"])
    line_rx(sc, r"pre-registered level \((\d+) of (\d+) agreeing rows\)", ["sc_hum_min", "sc_hum_of"])

    au = "outputs/pharos/audit_sample_result.md"
    for v in ("prune", "keep"):
        line_rx(au, rf"^\| {v} \| (\d+) \| (\d+) = {PCT} \({PCT} to {PCT}\)",
                [f"au_{v}_n", f"au_{v}_u", f"au_{v}_pct", f"au_{v}_lo", f"au_{v}_hi"])

    ca = "outputs/pharos/prune_calibration.md"
    line_rx(ca, rf"gene-disease pairs: {INT}\. Pharos gene-disease edges checked: {INT} non-text-mining \+ {INT} text-mining",
            ["ot_pairs", "gd_nontm", "gd_tm"])
    line_rx(ca, rf"Least-confident decile: in OT {PCT}.*most-confident decile: {PCT}", ["dec_lo", "dec_hi"])
    for v in ("keep", "prune"):
        line_rx(ca, rf"^\| {v} \| {INT} \| {PCT} \|", [f"ot_{v}_n", f"ot_{v}_pct"])

    line_rx("outputs/pharos/ppi_corroboration.md", rf"\*\*Headline: {PCT} of pruned rows and {PCT} of kept rows",
            ["intact_prune", "intact_keep"])
    nov = "outputs/linkpred/predictions_novelty.md"
    line_rx(nov, rf"^\| all predictions \| {INT} \|", ["nov_all"])
    line_rx(nov, rf"^\| novel and not an ancestor of a known disease \| \*\*{INT}\*\* \({PCT}\)", ["nov_n", "nov_pct"])

    nep = "outputs/neptune/live_parity.json"
    json_key(nep, "loaded.nodes", "nep_nodes", lambda v: f"{v:,}")
    json_key(nep, "loaded.links", "nep_links", lambda v: f"{v:,}")
    json_key(nep, "counts_equal", "nep_equal", lambda v: "equal" if v is True else "NOT EQUAL")
    pinned = json.loads((ROOT / nep).read_text())["pinned"]
    put("nep_pin_ok", sum(1 for p in pinned if p["digest_equal"]), nep, "count of key pinned[*].digest_equal == true")
    put("nep_pin_n", len(pinned), nep, "length of key pinned")

    sch = "docs/SCHEMA_CARD.md"
    line_rx(sch, r"\*\*As few as the identifiers allow\.\*\* (\d+) node classes and (\d+) predicates",
            ["n_classes", "n_predicates"])
    line_rx(sch, rf"^\| protein-coding gene \| \[(http://purl\.obolibrary\.org/obo/SO_\d+)\].*type of {INT} of {INT} Gene nodes",
            ["iri_so", "iri_so_n", "gene_nodes"])
    line_rx(sch, rf"^\| protein \| \[(http://purl\.obolibrary\.org/obo/PR_\d+)\].*\| {INT} Protein nodes \|",
            ["iri_pr", "iri_pr_n"])
    line_rx(sch, rf"The layer of failed drug programs \({INT} nodes, {INT} edges", ["trial_nodes", "trial_edges"])

    lim = "docs/LIMITATIONS.md"
    line_rx(lim, r"It holds (\d+) failed drug programs", ["trial_programs"])
    line_rx(lim, r"^\| Pharos / TCRD \(NCATS\) \| Pharos (\d+\.\d+\.\d+) / TCRD (\d+\.\d+\.\d+),", ["v_pharos", "v_tcrd"])
    line_rx(lim, r"^\| Open Targets Platform \| (\d+\.\d+) \|", ["v_ot"])
    line_rx(lim, r"^\| ChEMBL \| (\d+) \|", ["v_chembl"])
    line_rx(lim, r"Waking it takes about (\d+) minutes", ["nep_wake"])
    sub = "docs/SUBMISSION.md"
    line_rx(sub, r"^\*\*Hosted site: (https://[a-z0-9.]+/)\*\*", ["site_url"])
    line_rx(sub, r"^\*\*Video \(\d+ min \d+ s\): (https://youtu\.be/[A-Za-z0-9_-]+)\*\*", ["video_url"])

    # Latency medians from the S11 latency table (VIDEO_SCRIPT version 2 no longer carries the sentence version 1 had).
    s11 = "outputs/review/S11_verify_2026-11-14.md"
    line_rx(s11, r"^\| persona example x05,.*\| (\d+\.\d+) \|\s*$", ["lat_stored_lo"])
    line_rx(s11, r"^\| persona example x07,.*\| (\d+\.\d+) \|\s*$", ["lat_stored_hi"])
    line_rx(s11, r"^\| typed question .*\| (\d+\.\d+) \|\s*$", ["lat_typed"])
    line_rx("docs/RUNBOOK_JUDGING.md", r"--region ([a-z]+-[a-z]+-\d)", ["aws_region"])
    byo = "docs/BRING_YOUR_OWN_SOURCE.md"
    line_rx(byo, r"^## Five steps \(about (\d+) minutes\)", ["byo_min"])
    line_rx(byo, r"Example result: (\d+) nodes, (\d+) edges", ["byo_nodes", "byo_edges"])


# ---------------------------------------------------------------------------------------------- breakdown
def spotcheck_breakdown():
    """Kept rows whose final label is 'unsupported', counted by knowledge level, source and predicate (aggregates only)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import pandas as pd
    import score_spotcheck as s

    rels = ["outputs/pharos/spotcheck_machine_labels.tsv", "outputs/pharos/spotcheck_owner40.xlsx",
            "outputs/pharos/spotcheck_adjudicate.xlsx", "outputs/pharos/spotcheck_blind_key.tsv",
            "outputs/pharos/spotcheck_blind.xlsx"]
    for r in rels:
        tracked(r)
    rk = lambda p: pd.read_csv(ROOT / p, sep="\t", dtype=str, keep_default_na=False)
    fin = s.final_labels(s.machine_table(rk(rels[0])), s.read_sheet(ROOT / rels[1]), s.read_sheet(ROOT / rels[2]))
    key = rk(rels[3])
    key = key.assign(row=key["row"].astype(int))[["row", "verdict"]]
    sheet = pd.read_excel(ROOT / rels[4], sheet_name="spot_check", dtype=str)
    sheet = sheet.assign(row=sheet["row"].astype(int))[["row", "source", "knowledge_level", "predicate"]]
    d = fin[["row", "final"]].merge(key, on="row").merge(sheet, on="row")
    k = d[(d["verdict"] == "keep") & (d["final"] == "unsupported")]
    where = "computed: final label (score_spotcheck.final_labels on spotcheck_machine_labels.tsv, spotcheck_owner40.xlsx, " \
            "spotcheck_adjudicate.xlsx) x verdict (spotcheck_blind_key.tsv) x column of spotcheck_blind.xlsx"
    put("bd_total", len(k), "outputs/pharos/ (five files)", where + "; count of kept rows labelled unsupported")
    if str(len(k)) != N["sc_ku"]:
        DEVIATIONS.append(f"The breakdown join gives {len(k)} kept-and-unsupported rows; the result file gives {N['sc_ku']}.")
    tables = {}
    for col in ("knowledge_level", "source"):
        vc = k[col].value_counts()
        rows = []
        for j, (val, cnt) in enumerate(vc.items()):
            nid = f"bd_{col}_{j}"
            put(nid, int(cnt), "outputs/pharos/ (five files)", where + f"; column {col} = {val}")
            rows.append((val, nid))
        tables[col] = rows
    # the same kept-and-unsupported rows: how their final label was reached
    vb = k.merge(fin[["row", "basis"]], on="row")["basis"].value_counts()
    tables["basis"] = []
    for j, (val, cnt) in enumerate(vb.items()):
        nid = f"bd_basis_{j}"
        put(nid, int(cnt), "outputs/pharos/ (five files)", where + f"; basis of final label = {val}")
        tables["basis"].append((val, nid))
    return tables


# ------------------------------------------------------------------------------------------------ template
ALLOWED_TEMPLATE_DIGITS = {"H100", "L40S", "SHA-256", "EC2", "S3", "Figure 1", "Figure 2", "Figure 3", "Figure 4", "FIGURE 1"}


def check_template_digits(text):
    t = text
    for a in ALLOWED_TEMPLATE_DIGITS:
        t = t.replace(a, "")
    t = re.sub(r"\{[a-z0-9_]+\}", "", t)          # placeholders
    t = re.sub(r"`[^`]*`", "", t)                  # file names in code spans (no data values in them)
    t = re.sub(r"(?m)^\d\. ", "", t)              # ordered-list markers
    t = re.sub(r"\]\([^)]*\)", "]", t)             # image / link targets
    bad = re.findall(r"\S*\d\S*", t)
    if bad:
        raise SystemExit(f"digits typed into the template: {bad[:10]}")


ARCH_REL = "figures/architecture.png"

TEMPLATE = r"""# TrialMindKG: an agent that enriches, prunes and explains the Pharos knowledge graph

*White paper for the NVIDIA-AWS BioAgentKG Challenge. Every number in this paper is read by `scripts/build_whitepaper.py` from a committed result file; the table at the end gives the file and line for each one.*

## Abstract

TrialMindKG is a semi-autonomous agent built around the Pharos knowledge graph of drug targets. It adds evidence from other public sources, gives every edge a keep or prune verdict with a confidence and its reasons, and answers questions in plain language with the evidence behind each answer. On a held-out gene-to-disease link-prediction task, a path-aware graph model scores higher on the enriched graph than on Pharos alone by {enr_d} AUROC ({ci_level} interval {enr_lo} to {enr_hi}; higher in {enr_folds} of {enr_n} folds). On the enriched and pruned graph the gain is {prn_d} ({prn_lo} to {prn_hi}; {prn_folds} of {prn_n} folds). The pruner scored all {pr_scored} Pharos edges and marked {pr_prune} ({pr_pct}) for pruning. In a graph-embedding model, pruning beats removing the same number of edges at random by {rand_d} ({rand_lo} to {rand_hi}; {rand_folds} folds). A blinded spot check under a rule fixed before rating FAILED: {sc_pu} of {sc_pn} pruned edges and {sc_ku} of {sc_kn} kept edges were judged unsupported, against a rule of at least {sc_pthr} and at most {sc_kthr}. Against Open Targets, which the pruner never sees, pruned gene-disease edges were found far less often than kept ones: {ot_prune_n} pruned edges at {ot_prune_pct}, against {ot_keep_n} kept edges at {ot_keep_pct} (edges that are not text mining). Our reading is plain. Enrichment gives the large gain. Pruning keeps it and beats random removal slightly. Kept does not mean confirmed. The system runs on AWS with NVIDIA NIM models, and Amazon Neptune holds the same graph as the download.

## Problem and brief

Knowledge graphs for drug discovery are assembled from many sources. Each source has its own identifiers, its own idea of evidence, and its own errors. Pharos, from the NCATS Illuminating the Druggable Genome programme, is a good core: it covers human targets, diseases, ligands, protein interactions and text-mining links. It also carries the problems of any merged graph. Many links are weak, some are wrong, and a user cannot easily see which is which.

The organisers asked whether an agent, or a swarm of agents, can run one loop over such a graph: Ingest, Filter, Clean, Limit and Maintain. They asked that the result be usable by a non-expert, that it explain where each link comes from, that it follow community standards, and that the approach generalise beyond Pharos. They also asked for evidence that pruning removes the right edges.

We set ourselves three tests. First, does enrichment make the graph more useful for a real task, measured on held-out data with a pre-registered rule? Second, does pruning remove edges that are less likely to be true, measured against evidence the pruner never saw? Third, can a person who is not a graph specialist get a sourced answer from it in a few minutes? This paper reports the answer to each, including the test that failed.

TrialMindKG is the work of one biologist with coding agents. The agents wrote the source cards and programs, ran the experiments and recorded each hypothesis and result in a dated ledger. The scientist decided the open questions: which classes to keep, which mappings to exclude, and which controls to run.

## System overview (the loop)

The system follows the five steps of the brief. The same step table is shown in the app under "For judges" and in `docs/AGENT_LOOP.md`; a test checks that every module it names exists.

**Ingest.** A source card is a short YAML file that says how one table becomes nodes and edges. The builder checks the card against the BioLink model before it reads a row. It gives every edge its source and release. Rows whose identifiers cannot be standardised are counted and listed with the reason, not dropped silently (`pipelines/kgbuilder/engine.py`).

**Filter.** A question in plain language becomes one graph query. A language model drafts the query. A validator checks it against the graph's own classes, relationship types, properties and allowed values before it runs. The answer is the edges that matched, each cited by identifier, strongest evidence first (`pipelines/querier.py`). The edges of one answer can be downloaded as a small graph.

**Clean.** Every edge gets keep or prune, a confidence between zero and one, and the reasons. The score uses how the source asserted the link, the source's own score, agreement between sources, how well the edge fits its neighbourhood, and hard flags such as an implausible activity value. Pruned edges stay in the graph, marked, so nothing is lost (`pipelines/prune_kg.py`). The confidence is then checked against Open Targets, which the pruner never sees.

**Limit.** The brief calls this step probably unnecessary. We keep the whole Pharos graph out of the app's memory. A card index built ahead of time serves search and entity cards from a small database, the pruned graph leaves out pruned edges, and each answer exports only its own sub-graph (`pipelines/pharos_index.py`).

**Maintain.** A weekly check compares each source's current release with the release the repository pulled. It downloads and rebuilds nothing. A scientist approves the re-pull, the rebuild and the re-scoring.

{arch_figure}

## Data and standards

The core is Pharos {v_pharos} on TCRD {v_tcrd}. The enrichment layers add Open Targets Platform {v_ot} gene-disease associations, ChEMBL {v_chembl} compound-target activities and indications, and the hierarchies of MONDO, the Gene Ontology and the Human Phenotype Ontology. A separate trial layer describes {trial_programs} failed drug programs, chosen by hand as case studies, with {trial_nodes} nodes and {trial_edges} edges drawn from ClinicalTrials.gov, openFDA, GTEx and the literature. It is not a registry of all trials.

The Pharos core and the enrichment layers use {n_classes} node classes and {n_predicates} predicates, all BioLink terms (`docs/SCHEMA_CARD.md`). A class exists only where an identifier prefix needs it. The Gene Ontology keeps three classes, one for each of its branches, because folding them together would lose which kind of annotation an edge is. Where layers disagree on the class of a term, the term is re-typed so that one identifier has one class everywhere.

The three IRIs named in the brief are in the graph. {iri_so} types {iri_so_n} of the {gene_nodes} gene nodes. {iri_pr} types {iri_pr_n} protein nodes. The sequence-ontology relation "translates to" is kept as the original predicate of the gene-to-protein edges. The edge itself uses BioLink's `has_gene_product`, because in BioLink "translates to" joins a transcript to a protein, and this graph has no transcript nodes.

The trial layer needs things BioLink has no specific term for: a trial arm, a registered endpoint, an outcome result. These use classes and predicates under the prefix `tmkg:`, and each names its BioLink parent on every node and edge.

Every edge carries its primary knowledge source, its knowledge level (for example a curated assertion, a statistical association or a text co-occurrence), its agent type, the source row and the card version. The graph is exported as KGX tables, a PyTorch Geometric graph and Amazon Neptune load files, each with SHA-256 sums. The Neptune copy holds {nep_nodes} nodes and {nep_links} links; its counts are {nep_equal} to the download, and {nep_pin_ok} of {nep_pin_n} pinned check questions return the same evidence there (`outputs/neptune/live_parity.json`).

## Link-prediction benchmark

**Task.** The benchmark asks whether a model can recover gene-to-disease links that were removed from the graph. The held-out links are split into folds. Each fold holds between {ntest_min} and {ntest_max} test pairs (key `n_test` in each result file), and the three graphs are tested on the same pairs: each result file carries a digest of its test set, and the comparison reports whether they match. Negative pairs are matched on node degree. Links that would leak the answer are removed from training. Training is capped at the same number of edges per edge type on both sides of every comparison.

**Statistics.** Each comparison is a paired difference per fold, with a {ci_level} interval over the folds, and a count of the folds in which the variant scores higher. Each confirmatory experiment had its rule written before its result was seen; exploratory runs are marked as such. Every result, including the null ones, is in `docs/EXPERIMENTS_LINKPRED.md`.

**Model.** The headline model combines a relational graph network with features counted on the paths between a gene and a disease. It ran on an NVIDIA H100.

| Graph | Mean AUROC | Difference from Pharos alone | {ci_level} interval | Folds higher |
|---|---|---|---|---|
| Pharos alone | {auroc_pha} | | | |
| Enriched | {auroc_enr} | {enr_d} | {enr_lo} to {enr_hi} | {enr_folds} of {enr_n} |
| Enriched and pruned | {auroc_prn} | {prn_d} | {prn_lo} to {prn_hi} | {prn_folds} of {prn_n} |

![Figure 2. AUROC per fold for the same model on Pharos alone, the enriched graph and the enriched and pruned graph. Thin lines join the same fold; bars are the means.](figures/judges_card_paired_folds.png)

*Figure 2. AUROC per fold on Pharos alone, the enriched graph, and the enriched and pruned graph. Thin lines join the same fold. Enriched minus Pharos alone: {enr_d} ({enr_lo} to {enr_hi}). Enriched and pruned minus Pharos alone: {prn_d} ({prn_lo} to {prn_hi}).*

**What carries the gain.** Three controls bound the headline.

- A plain message-passing graph network, run under the frozen protocol on an earlier build of the graphs, gains nothing: {rgcn_d} ({rgcn_lo} to {rgcn_hi}; {rgcn_folds} folds higher). It builds one vector per node and cannot count the paths between a gene and a disease, which is where the new signal is. The path-aware model was chosen after this null result.
- Most of the path gain comes from the MONDO disease hierarchy: about {mondo_share_meta} for the meta-path model when MONDO is dropped. A gene linked to a parent or child of a disease is predicted for that disease. This is legitimate and explainable, but it is partly a restatement of existing knowledge at a neighbouring level of the ontology.
- Without MONDO, the enriched graph still beats Pharos alone with the headline model: {nomondo_d} ({nomondo_lo} to {nomondo_hi}; {nomondo_folds} folds). MONDO carries about {mondo_share_gnn} of that model's gain.

The model's predictions are hypotheses, not findings. Of the {nov_all} predictions of the meta-path model on the enriched and pruned graph, {nov_n} ({nov_pct}) are in no layer of the graph and do not merely name an ancestor of a known disease. Each prediction shows the paths that produced it and says that it is predicted, not reported.

## Pruning and evaluation

**What was pruned.** The pruner scored all {pr_scored} Pharos edges and marked {pr_prune} ({pr_pct}) for pruning. No gold standard of wrong edges exists for Pharos. The verdicts are therefore a confidence re-weighting, not proven error removal. We tested them four ways.

**Effect on the benchmark.** Pruning keeps the headline gain: {prn_d} over Pharos alone against {enr_d} for the enriched graph. For a graph-embedding model (RotatE) trained for the same number of epochs on every graph, the enriched and pruned graph beats the enriched graph by {rot_d} ({rot_lo} to {rot_hi}; {rot_folds} folds). The key control removes the same number of edges per edge type at random. The enriched and pruned graph beats this random-pruning control by {rand_d} ({rand_lo} to {rand_hi}; {rand_folds} folds), while random removal itself changes the score by {g2r_d} ({g2r_lo} to {g2r_hi}; {g2r_folds} folds). The gain therefore comes from which edges the verdicts remove, not from removing edges as such. The effect is small.

**Open Targets cross-check.** Open Targets lists {ot_pairs} gene-disease pairs, and the pruner never sees them. Of the {gd_nontm} Pharos gene-disease edges that are not text mining, {ot_keep_n} are kept and {ot_prune_n} are pruned. Of the kept edges {ot_keep_pct} are also in Open Targets; of the pruned edges {ot_prune_pct} are. The pruned group is small, so this check covers few of the pruned edges. Across confidence deciles the share in Open Targets rises from {dec_lo} in the least confident decile to {dec_hi} in the most confident.

![Figure 3. Share of Pharos gene-disease edges also asserted by Open Targets, by decile of pruning confidence.](figures/prune_calibration_deciles.png)

*Figure 3. Share of non-text-mining Pharos gene-disease edges also asserted by Open Targets, by decile of pruning confidence (least confident on the left).*

For protein interactions, an independent check without any model gives a weaker signal. An independent IntAct record exists for {intact_prune} of pruned and {intact_keep} of kept protein-protein rows.

**Blinded spot check (pre-registered).** Before rating began we fixed a rule. The check would pass if at least {sc_pthr} of {sc_pn} pruned edges were judged unsupported and at most {sc_kthr} of {sc_kn} kept edges were. The rows were shuffled and the verdicts hidden. Three model raters labelled each row: two NVIDIA Nemotron models and one Mistral model. The scientist rated {sc_own_n} rows blind and adjudicated flagged rows.

The check FAILED. {sc_pu} of {sc_pn} pruned edges were judged unsupported (rule: at least {sc_pthr}), which meets its half of the rule. {sc_ku} of {sc_kn} kept edges were also judged unsupported (rule: at most {sc_kthr}), which fails badly. Among pruned edges {sc_p_sup} were judged supported and {sc_p_ct} could not be told; among kept edges, {sc_k_sup} and {sc_k_ct}. The final label came from the scientist's blind label on {sc_own_used} rows, from agreement of all three raters on {sc_agree3}, from her adjudication on {sc_adj} and from the raters' majority on {sc_major}. The raters agreed among themselves on {sc_all3} of {sc_rows} rows ({sc_all3_pct}). On the rows she rated, the scientist agreed with the raters' majority label on {sc_hum_agree} of {sc_hum_n} ({sc_hum_pct}), Cohen's kappa {sc_kappa}. The pre-registered level for treating the machine labels as checked was {sc_hum_min} of {sc_hum_of}. The raters' labels are therefore not confirmed by a human. The stored independent-check records of the {sc_rows} spot-check rows carry the raters' judgement; the final label of each row is in `outputs/pharos/spotcheck_blind_result.md`.

**Independent audit sample (no pre-registered rule).** A separate sample of {au_prune_n} pruned and {au_keep_n} kept edges was labelled by the same three raters only. {au_prune_u} pruned edges were judged unsupported, {au_prune_pct} ({au_prune_lo} to {au_prune_hi}); {au_keep_u} kept edges were, {au_keep_pct} ({au_keep_lo} to {au_keep_hi}). This sample had no pass rule and was never mixed into the spot check. Within protein-protein rows from one source, pruned and kept rows are judged unsupported at similar rates, so much of the difference is the mix of sources.

**After-the-fact analysis (not pre-registered).** We broke down the {bd_total} kept edges judged unsupported in the spot check by evidence type. The counts are computed by the build script from the final labels the scorer uses, joined to each row's knowledge level and source, and by how the final label was reached.

{breakdown_tables}

Nearly all of the kept-but-unsupported rows are protein-interaction rows from STRING or text co-occurrence links from TIN-X. Both are weak kinds of evidence. This analysis was planned after the result was known and does not change it.

**Conclusion on pruning.** Enrichment gives the large gain. Pruning keeps it and beats random removal slightly. Kept does not mean confirmed: in both samples a large share of kept edges, especially text-mined and protein-interaction rows, had no support the raters could find.

## Explainability (edge lineage)

Every edge can be traced. Its card names the source database and release, the source row, the identifiers at both ends, the publications where the source gives them, the knowledge level, and the keep or prune verdict with its confidence and reasons. A user reaches this card by hovering on an edge or clicking "why" next to it.

Every answer to a question opens with its plan: the graph query that was run, after validation. A short written summary sits above the evidence rows. A language model writes the summary, and a second check tests each of its sentences against the rows it cites. Every predicted link shows the graph paths that produced it and is marked "predicted, not yet reported".

![Figure 4. The evidence record of one edge in the app.](figures/o05_evidence_panel.png)

*Figure 4. The evidence record of one edge in the app, opened from an answer about a trial of mepolizumab in COPD: the statement with its predicate, the source record in ClinicalTrials.gov, the evidence type and retrieval time, the knowledge source and the knowledge level with its agent. The record continues below the part shown.*

The limits are real. Publications are counted on many links, but their PubMed identifiers are not carried for every source. Several sources have no link to their own record page. A predicted link's band ranks pairs; the card does not yet show how often each band was right on held-out links.

## Usability (how a non-expert uses it)

A visitor opens the site and picks one of five roles: physician-scientist, clinician, bench scientist, drug-development lead, or computational biologist. Each role offers ready questions, many about a failed drug program. Clicking one returns a plan, a short narrative, the evidence rows behind it and an interactive graph. A visitor can also type a question in plain words. Every node and edge has a hover card. Every answer can be downloaded as a small graph.

Stored worked examples answer in {lat_stored_lo} s to {lat_stored_hi} s. A typed question took {lat_typed} s (median) in a latency run, because it calls a language model to draft and check the query. We therefore lead new users to the ready questions first. Typed questions to the trial layer can fail; the stored trial answers and the program cards are the reliable route there. Questions about a whole drug class are refused rather than answered loosely.

Evidence of usability is thin. No usability test with outside users was run; the site was checked only in the owner's own pass in Chrome, Safari and on a phone.

## Deployment on AWS with NVIDIA

The site runs on one Amazon EC2 server in the {aws_region} region (region read from `docs/RUNBOOK_JUDGING.md`). Amazon S3 holds the code bundle, the graph index and the downloads, which the server loads at start. Amazon CloudFront gives a second address in front of the same server and caches the downloads. AWS Systems Manager keeps the model key in Parameter Store and runs commands on the server. Amazon CloudWatch raises a status alarm, and Amazon EventBridge Scheduler with AWS Lambda puts Amazon Neptune to sleep after an hour without a question and every night, to save cost.

Amazon Neptune holds the whole shipped graph. Under any answer, "Run this on Amazon Neptune" runs the same validated query there and prints the rows, the time, and how many evidence links the two answers share. When Neptune is asleep, the control offers to wake it, which takes about {nep_wake} minutes; the answer from the server's own graph stays on screen meanwhile.

NVIDIA NIM serves the language models. Nemotron chat models turn a typed question into a checked graph query and write and check the evidence summary. NIM embedding and reranking models match a drug's research code or trade name to the right compound. The path-aware model behind the headline ran on an NVIDIA H100; the earlier graph-embedding runs ran on an NVIDIA L40S.

The hosted site is at {site_url}. A short video walks through one question on it: {video_url}.

## Generalising to new sources

Nothing in the loop is specific to kinases or to Pharos. A new source is one new source card. `docs/BRING_YOUR_OWN_SOURCE.md` walks a non-programmer through five steps, in about {byo_min} minutes, with a worked example in `examples/byo_source/` whose values are labelled SYNTHETIC.

1. Put the table in a folder as a gzipped, tab-separated file, one finding per row.
2. Copy the example card and edit it: which columns hold the two ends of the edge, which BioLink class and identifier system each uses, which BioLink predicate joins them, and who asserts it and how.
3. Build it. The builder checks the card against BioLink first, so an impossible relationship such as "protein treats protein" is refused with a message. Rows whose identifiers cannot be standardised are listed with the reason, never guessed. The example gives {byo_nodes} nodes and {byo_edges} edges.
4. Score it. The same pruner gives every edge a verdict, a confidence and the reasons. With one small source it has little to compare with, so the example's verdicts only illustrate the output.
5. Use it with the rest of the graph. Layers merge by identifier, so a protein in the new table shares a node with the Pharos evidence, and its card lists the new finding next to the others with its own source and verdict.

Every edge of a new layer carries the same provenance contract as Pharos edges. One card describes one source, so several sources become several layers, which is what makes agreement between sources visible to the pruner. A test (`tests/test_byo_source.py`) exercises the example. A science that needs a class BioLink lacks uses a `tmkg:` term and says so, as the trial layer does.

## Limitations

- **The pruning check failed.** The kept set contains many edges that raters could not support. Pruning is a confidence re-weighting, not error removal.
- **The machine labels are not confirmed by a human.** The scientist agreed with the raters' majority on {sc_hum_agree} of {sc_hum_n} rows.
- **The headline gain is mostly ontology smoothing.** About {mondo_share_gnn} of the path-aware model's gain comes from the MONDO hierarchy. The model was chosen after the frozen protocol showed no gain for a plain graph network, and some controls were run on an earlier build of the graphs.
- **The graph is a snapshot.** Nothing in it is newer than its source releases. Maintenance is a procedure a person approves; no program rebuilds the graph by itself.
- **Coverage.** The graph is human only, with no patient data, no genotypes, no transcripts and no cell types. Absence of a link is not evidence of absence.
- **Most gene-disease links are text mining.** Pharos contributes {gd_tm} text co-occurrence gene-disease links against {gd_nontm} that are not. Each row says which kind it is.
- **No usability test with outside users was run.** The only check was the owner's own pass in Chrome, Safari and on a phone.
- **Known gaps** in this version are listed in `docs/LIMITATIONS.md`. Examples: a disease card lists a limited number of links of each kind, ordered by pruning confidence rather than strength of genetic signal; antibodies from ChEMBL are typed as small molecules; and the held-out test links are rebuilt by code rather than shipped as a download.

## Reproducibility and licences

`HOW_TO_RUN.md` gives the set-up and four commands: the test suite with the app click-through and schema checks, a small end-to-end demo with the frozen benchmark command, an offline click-through of the app, and a read-only check of the hosted site. Python dependencies are pinned in `requirements.lock.txt`, and the server image is built from `aws/Dockerfile`. Large data that is not in git is listed with its source, release and how to fetch it. Each benchmark result file carries a digest of its test set, so a rerun can show it used the same links. The experiment ledger (`loop/ledger.tsv`) records each hypothesis and rule before its run.

The code is under the MIT licence (`LICENSE`). The data keep the licences of their sources, and every link names its source, so the licence of any row can be traced. Rows from CTD and DisGeNET are for non-commercial use only. Rows from ChEMBL are share-alike. The Human Phenotype Ontology, ClinicalTrials.gov and GTEx ask for specific attribution lines. The full table is `DATA_LICENSES.md`; it is not legal advice. To cite a fact found here, cite its source, which the link names. To cite the graph, give its name, the repository address and the commit used, because the graph is a dated snapshot.
"""

ARCH_SLOT = """> **FIGURE 1 SLOT: architecture (figures/architecture.png, pending)**
>
> *Figure 1 (caption draft). The loop. Source cards feed the builder (Ingest); the pruner scores every edge (Clean); a language model drafts a query that a validator checks against the graph (Filter); answers return with their evidence and lineage; a weekly release check asks a scientist to approve rebuilds (Maintain). The graph is served from Amazon EC2 and Amazon S3, mirrored in Amazon Neptune, with NVIDIA NIM models for questions and summaries.*"""

ARCH_IMG = """![Figure 1. The loop and its deployment.](figures/architecture.png)

*Figure 1. The loop. Source cards feed the builder (Ingest); the pruner scores every edge (Clean); a language model drafts a query that a validator checks against the graph (Filter); answers return with their evidence and lineage; a weekly release check asks a scientist to approve rebuilds (Maintain). The graph is served from Amazon EC2 and Amazon S3, mirrored in Amazon Neptune, with NVIDIA NIM models for questions and summaries.*"""

BREAKDOWN_TITLES = {"knowledge_level": "Knowledge level", "source": "Source", "predicate": "Predicate",
                    "basis": "How the final label was reached"}


def breakdown_md(tables):
    out = []
    for col in ("knowledge_level", "source", "basis"):
        out.append(f"| {BREAKDOWN_TITLES[col]} | Kept edges judged unsupported |")
        out.append("|---|---|")
        for val, nid in tables[col]:
            out.append(f"| {val} | {{{nid}}} |")
        out.append("| **Total** | **{bd_total}** |")
        out.append("")
    return "\n".join(out).rstrip()


def check_body_numbers(body):
    """Every number in the written body must be (part of) a recorded value."""
    vals = set(N.values())
    allowed = {"H100", "L40S", "256", "-256", "1", "2", "3", "4", "5"}   # list markers too   # product names; figure numbers; SHA-256
    t = re.sub(r"`[^`]*`", "", body)
    t = re.sub(r"\]\([^)]*\)", "]", t)
    bad = []
    for tok in re.findall(r"[-+]?\d[\d,.]*\d%?|[-+]?\d%?", t):
        if tok in allowed:
            continue
        if not any(tok in v for v in vals):
            bad.append(tok)
    if bad:
        raise SystemExit(f"numbers in the body that are not recorded values: {sorted(set(bad))[:20]}")


def words(text):
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)          # image syntax
    t = re.sub(r"\|[-| ]+\|", "", t)                       # table rules
    return len([w for w in re.findall(r"\S+", t) if re.search(r"[A-Za-z0-9]", w)])


def main():
    read_numbers()
    tables = spotcheck_breakdown()
    check_template_digits(TEMPLATE)
    arch_exists = (DOCS / ARCH_REL).exists()
    body = TEMPLATE.replace("{arch_figure}", ARCH_IMG if arch_exists else ARCH_SLOT)
    body = body.replace("{breakdown_tables}", breakdown_md(tables))
    body = body.format_map(N)
    check_body_numbers(body)

    app = ["", "## Appendix: sources of numbers", "",
           "Each number above was read by `scripts/build_whitepaper.py` from the file and line or key below. "
           "Tracked = the file is in git (`git ls-files`).", "",
           "| id | value | file | line or key | tracked |", "|---|---|---|---|---|"]
    for nid in ORDER:
        rel, where = SRC[nid]
        tr = "yes" if (rel.endswith("(five files)") or tracked(rel)) else "NO"
        val = N[nid].replace("|", "\\|")
        app.append(f"| {nid} | {val} | `{rel}` | {where} | {tr} |")
    app += ["", "**Deviations.** " + (" ".join(DEVIATIONS) if DEVIATIONS else "none"), ""]
    if not arch_exists:
        app.insert(4, "Figure 1 is a placeholder: `docs/figures/architecture.png` did not exist when this file was built.")
        app.insert(5, "")
    OUT_MD.write_text(body.rstrip() + "\n" + "\n".join(app), encoding="utf-8")
    print(f"wrote {OUT_MD.relative_to(ROOT)}; body words {words(body)}; numbers {len(ORDER)}; "
          f"architecture figure {'inserted' if arch_exists else 'slot'}; deviations {len(DEVIATIONS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
