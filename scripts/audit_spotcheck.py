"""Verdict-blind audit of the pruner's verdicts: evidence dossiers, three model raters, the owner's sheets.

Rules, raters, seeds and final labels are pre-registered in outputs/pharos/spotcheck_rule.md (Amendment 1,
addition A). The collectors, the dossier and the label checks live in pipelines/edge_evidence.py (importable by the
app) and are re-exported here.

    .venv/bin/python scripts/audit_spotcheck.py sample                  # the separate 500 + 500 audit sample
    .venv/bin/python scripts/audit_spotcheck.py collect  --set spotcheck   # one cached JSON dossier per row
    .venv/bin/python scripts/audit_spotcheck.py owner40                 # the owner's 40-row sheet (no proposals)
    .venv/bin/python scripts/audit_spotcheck.py judge    --set spotcheck   # three raters -> machine labels
    .venv/bin/python scripts/audit_spotcheck.py adjudicate              # flagged rows for the owner (after her 40)
    .venv/bin/python scripts/audit_spotcheck.py publish  --set spotcheck   # stored records per edge id for the app
    .venv/bin/python scripts/audit_spotcheck.py cited                   # blind rows of every edge a stored answer or a
                                                                        # tested question cites (then collect / judge / publish --set cited)
    (--set audit for the audit sample; --set cited for the cited edges)

Blindness: `collect`, `owner40` and `judge` read the blind sheet only. No rater, model or human, receives a verdict,
a confidence, the pruner's reasons or an edge id. `publish` reads two columns of the key (row, edge_id) to name the
stored files; `sample` writes a key it never prints.

Files per set (SETS): the blind sheet, its key, one dossier per row under data/audit/<set>/dossiers/, the same
dossiers as one committed file outputs/pharos/<set>_dossiers.jsonl.gz, and the raters' labels
outputs/pharos/<set>_machine_labels.tsv (closed until the owner has rated her rows). HTTP answers are cached under
data/audit/snapshots, model replies under data/audit/snapshots_llm, so every step can be replayed.
"""
import argparse
import csv
import datetime as dt
import gzip
import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
sys.path.insert(0, str(ROOT / "scripts"))

import edge_evidence as ee  # noqa: E402
from edge_evidence import (BLIND_FIELDS, LABELS, FastFetcher, Fetcher, Lookups, audit_record, build_dossier,  # noqa: E402,F401
                           check_label, collect_chembl, collect_comention, collect_complex_portal, collect_impc,
                           collect_intact, collect_open_targets, collect_ot_literature, collect_quickgo, collect_reactome,
                           collect_reactome_pathway, collect_references, collect_string, collect_uniprot_gene,
                           collect_wikipathways, combine, dossier_text, judge)

OUT = ROOT / "outputs" / "pharos"
AUDIT = ROOT / "data" / "audit"
OWNER_SEED, OWNER_N = 20261112, 40
AUDIT_SEED, AUDIT_N = 20261113, 500
RATER_POST_TIMEOUT = 600      # seconds a rater call may wait for its answer (a reasoning rater can need minutes); _nim() and the probe share it
RATERS = ["A", "B", "C"]
LABEL_COLS = ["row", "rater", "model", "label", "label_raw", "items", "problems", "rationale", "reply_key"]
SETS = {
    "spotcheck": dict(sheet=OUT / "spotcheck_blind.xlsx", key=OUT / "spotcheck_blind_key.tsv", work=AUDIT / "spotcheck200",
                      dossiers=OUT / "spotcheck_dossiers.jsonl.gz", labels=OUT / "spotcheck_machine_labels.tsv"),
    "audit": dict(sheet=OUT / "audit_sample_blind.xlsx", key=OUT / "audit_sample_key.tsv", work=AUDIT / "audit1000",
                  dossiers=OUT / "audit_sample_dossiers.jsonl.gz", labels=OUT / "audit_sample_machine_labels.tsv"),
    "cited": dict(sheet=OUT / "cited_edges.xlsx", key=OUT / "cited_edges_ids.tsv", work=AUDIT / "cited",
                  dossiers=OUT / "cited_edges_dossiers.jsonl.gz", labels=OUT / "cited_edges_machine_labels.tsv"),
}
GROUP_TITLES = {"protein_protein": "Protein-protein rows", "gene_disease": "Gene-disease rows",
                "gene_ontology": "Gene Ontology rows", "pathway": "Pathway rows", "ligand": "Ligand rows",
                "phenotype": "Mouse-phenotype rows", "gene_product": "Gene-to-protein rows", "other": "Any other predicate"}


# ------------------------------------------------------------------------------------------------ sheets and rows
def read_rows(sheet):
    """The blind rows of a sheet's 'spot_check' tab: [{row, <BLIND_FIELDS>}], in row order. Reads no other column."""
    df = pd.read_excel(sheet, sheet_name="spot_check", dtype=str, usecols=["row"] + BLIND_FIELDS).fillna("")
    df["row"] = df["row"].astype(int)
    return df.sort_values("row").to_dict("records")


def draw_audit_sample(layer="pharos", n=AUDIT_N, seed=AUDIT_SEED, sheet=None, key=None, exclude_key=None):
    """Draws the separate audit sample (n pruned + n kept) from the shipped verdict file with the sampler of
    scripts/make_spotcheck_blinded.py and writes a blind sheet and its key. exclude_key: the key of the
    pre-registered sample; the number of shared edge ids is returned (never the rows). Returns a dict of counts."""
    import make_spotcheck_blinded as m
    kg = ROOT / "data" / "kg" / layer
    vpath, epath, npath = kg / "edge_verdicts.tsv.gz", kg / "edges.tsv.gz", kg / "nodes.tsv.gz"
    groups = m.load_pair_groups(ROOT / "sources" / f"{layer}.yaml")
    reservoir, sizes, n_rows = m.stream_reservoirs(vpath, groups, n, seed=seed)
    sample = m.draw(reservoir, sizes, {"prune": n, "keep": n})
    blind, keydf = m.build_sheet(sample, m.edge_details(epath, sample["id"]), m.node_names(npath), seed=seed)
    n_p, n_k = int((keydf["verdict"] == "prune").sum()), int((keydf["verdict"] == "keep").sum())
    readme = [("purpose", "Separate audit sample of the shipped pruner verdicts: machine raters only, reported apart "
                          "from the pre-registered 200 rows, no pass rule (outputs/pharos/spotcheck_rule.md, addition A7)."),
              ("generated", dt.datetime.now().strftime("%Y-%m-%d %H:%M") + " by scripts/audit_spotcheck.py sample"),
              ("seed", str(seed)), ("verdict rows read", f"{n_rows:,}"),
              ("sample", f"{n_p + n_k} edges: {n_p} the pruner prunes + {n_k} it keeps, shuffled together"),
              ("method", "the sampler of scripts/make_spotcheck_blinded.py: strata = verdict x predicate group, "
                         "proportional allocation within each verdict, at least 1 per group"),
              ("key", "the key file holds verdict, confidence and reasons per row; no rater reads it")]
    m.write_blind_xlsx(blind, readme, sheet or SETS["audit"]["sheet"])
    keydf.to_csv(key or SETS["audit"]["key"], sep="\t", index=False)
    shared = 0
    if exclude_key and Path(exclude_key).exists():
        old = pd.read_csv(exclude_key, sep="\t", dtype=str, usecols=["edge_id"])["edge_id"]
        shared = int(keydf["edge_id"].isin(set(old)).sum())
    return {"verdict_rows": n_rows, "prune": n_p, "keep": n_k, "shared_with_preregistered": shared}


def owner_rows(n_rows=200, n=OWNER_N, seed=OWNER_SEED):
    """The owner's rows: a simple random sample of n of the row numbers 1..n_rows, numpy default_rng(seed), sorted.
    Drawn without reference to any key."""
    return sorted(int(i) + 1 for i in np.random.default_rng(seed).choice(n_rows, size=min(n, n_rows), replace=False))


# ------------------------------------------------------------------------------------------------ cited edges
def cited_edge_ids(examples_dir, overlay=None):
    """{edge id: [where it is cited]} for every edge a stored answer cites (summary references, claim evidence, graph
    edges of outputs/examples/*.json) and every evidence edge of a tested question (the demo overlay file)."""
    out = {}

    def add(eid, where):
        if eid:
            out.setdefault(str(eid), [])
            if where not in out[str(eid)]:
                out[str(eid)].append(where)

    for f in sorted(Path(examples_dir).glob("*.json")):
        d = json.loads(f.read_text())
        ans, where = d.get("answer") or {}, f"stored answer {d.get('id') or f.stem}"
        for eid in (((ans.get("summary") or {}).get("refs") or {}).get("edges") or {}):
            add(eid, where)
        for sec in ans.get("sections") or []:
            for e in (sec.get("graph") or {}).get("edges") or []:
                add(e.get("id"), where)
            for c in sec.get("claims") or []:
                for ev in c.get("evidence") or []:
                    add(ev.get("id") if isinstance(ev, dict) else ev, where)
    if overlay and Path(overlay).exists():
        for qid, x in (json.loads(Path(overlay).read_text()).get("results") or {}).items():
            for ev in (x.get("result") or {}).get("evidence") or []:
                add(ev.get("id") if isinstance(ev, dict) else ev, f"tested question {qid}")
    return out


def blind_row(explained):
    """The blind columns of one edge from the index's explain_edge() record, or None when the index does not hold
    the edge (clinical-trial registry links are not in it). Verdict, confidence and reasons are not read."""
    import make_spotcheck_blinded as m
    if not explained or not explained.get("provenance"):
        return None
    pv, props = explained["provenance"], explained.get("properties") or {}
    sub, obj = explained.get("subject") or {}, explained.get("object") or {}
    pks = pv.get("primary_knowledge_source") or ""
    return {"subject_name": sub.get("symbol") or sub.get("name") or props.get("ligand_name") or props.get("drug_name") or "",
            "subject_id": pv.get("subject") or sub.get("id") or "", "predicate": str(pv.get("predicate") or "").replace("biolink:", ""),
            "object_name": obj.get("symbol") or obj.get("name") or "", "object_id": pv.get("object") or obj.get("id") or "",
            "source": f"{pv.get('source')} ({pks})" if pks else str(pv.get("source") or ""),
            "knowledge_level": pv.get("knowledge_level") or "", "source_statistic": m.stat_string({**props, "n_records": pv.get("n_records")}),
            "references": m.references(props)}


def write_cited_set(cited, explain, sheet, key):
    """Writes the blind sheet (tab 'spot_check') and the id table (row, edge_id, cited_by) for the cited edges the
    index holds, in edge-id order. explain: edge id -> explain_edge() record (may raise or return None). Returns
    {cited, in_index, not_in_index}."""
    rows, ids = [], []
    for eid in sorted(cited):
        try:
            b = blind_row(explain(eid))
        except Exception:
            b = None
        if b:
            rows.append({"row": len(rows) + 1, **b})
            ids.append({"row": len(rows), "edge_id": eid, "cited_by": "; ".join(cited[eid])})
    Path(sheet).parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(sheet, engine="openpyxl") as w:
        pd.DataFrame(rows, columns=["row"] + BLIND_FIELDS).to_excel(w, index=False, sheet_name="spot_check")
    pd.DataFrame(ids, columns=["row", "edge_id", "cited_by"]).to_csv(key, sep="\t", index=False)
    return {"cited": len(cited), "in_index": len(rows), "not_in_index": len(cited) - len(rows)}


# ------------------------------------------------------------------------------------------------ dossiers
def dossier_path(work, row):
    return Path(work) / "dossiers" / f"row_{int(row):04d}.json"


def needs_collection(path, retry_failed=True):
    """True when a row has no stored dossier, or (retry_failed) its dossier has a source that did not answer."""
    if not Path(path).exists():
        return True
    if not retry_failed:
        return False
    return any(s["status"] == "failed" for s in json.loads(Path(path).read_text())["sources"])


def collect_rows(rows, work, fetch, lookups, workers=6, retry_failed=True, today=None, edge_ids=None, rebuild=False):
    """Builds and stores one dossier per row (skips rows already stored; a dossier with a failed source is built
    again; rebuild=True builds every row again). Returns {built, kept, failed_sources}. edge_ids: optional
    {row: edge id} stored in the dossier."""
    todo = [r for r in rows if rebuild or needs_collection(dossier_path(work, r["row"]), retry_failed)]
    (Path(work) / "dossiers").mkdir(parents=True, exist_ok=True)

    def one(r):
        d = build_dossier(r, fetch, lookups, today=today, edge_id=(edge_ids or {}).get(r["row"], ""))
        d["row"] = int(r["row"])
        dossier_path(work, r["row"]).write_text(json.dumps(d, indent=1, sort_keys=True, ensure_ascii=False))
        return sum(s["status"] == "failed" for s in d["sources"])

    if workers > 1 and len(todo) > 1:
        with ThreadPoolExecutor(workers) as pool:
            failed = list(pool.map(one, todo))
    else:
        failed = [one(r) for r in todo]
    return {"built": len(todo), "kept": len(rows) - len(todo), "failed_sources": int(sum(failed))}


def load_dossiers(work):
    """{row: dossier} for every stored dossier of a set."""
    out = {}
    for f in sorted((Path(work) / "dossiers").glob("row_*.json")):
        d = json.loads(f.read_text())
        out[int(d["row"])] = d
    return out


def export_dossiers(dossiers, path):
    """Writes the dossiers as one gzipped JSON-lines file in row order (byte-identical for identical content)."""
    data = "".join(json.dumps(dossiers[r], sort_keys=True, ensure_ascii=False) + "\n" for r in sorted(dossiers))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(gzip.compress(data.encode(), mtime=0))
    return len(dossiers)


def dossier_summary(dossiers):
    """Counts for the log: rows per rule group and source statuses (source, status) -> rows."""
    groups, status = {}, {}
    for d in dossiers.values():
        groups[d["group"]] = groups.get(d["group"], 0) + 1
        for s in d["sources"]:
            status[(s["source"], s["status"])] = status.get((s["source"], s["status"]), 0) + 1
    return {"groups": groups, "status": status}


# ------------------------------------------------------------------------------------------------ raters
def rater_models(nim, probe=None):
    """{'A': id, 'B': id, 'C': id} from config key `spotcheck_raters`: judge_a, judge_b, and for C the first of
    rater_c_candidates for which probe(model) is true (default probe: a structured-output test call that is not
    stored). Raises when no candidate answers."""
    cfg = nim.cfg["spotcheck_raters"]

    def default_probe(model):
        import copy
        from snapshot import Snapshot
        test = copy.copy(nim)
        with tempfile.TemporaryDirectory() as tmp:
            test.snap, test.fallback, test.calls = Snapshot(Path(tmp), mode="refresh", sleep=0, post_timeout=RATER_POST_TIMEOUT), "none", []
            test.cfg = {**nim.cfg, "roles": {**nim.cfg["roles"], "audit:probe": model}}
            try:
                r = test.chat("audit:probe", [{"role": "user", "content": "Claim: A binds B. Evidence: [IA1] IntAct record, "
                              "physical association. Give label, rationale, items."}], schema=ee.JUDGE_SCHEMA)
                return r.data.get("label") in LABELS
            except Exception:
                return False

    probe = probe or default_probe
    for model in cfg["rater_c_candidates"]:
        if probe(model):
            return {"A": cfg["judge_a"], "B": cfg["judge_b"], "C": model}
    raise RuntimeError("no rater C candidate answered a structured-output test call")


def apply_rater_options(nim):
    """Copies the per-model request options of config key spotcheck_raters.options (for example a larger max_tokens
    for a reasoning model) into the client's model options, so every rater call of that model carries them. Returns
    the options applied."""
    opts = (nim.cfg.get("spotcheck_raters") or {}).get("options") or {}
    merged = dict(nim.cfg.get("model_options") or {})
    for model, o in opts.items():
        merged[model] = {**(merged.get(model) or {}), **o}
    nim.cfg["model_options"] = merged
    return opts


def read_labels(path):
    """The raters' labels file as a DataFrame (LABEL_COLS; empty when the file does not exist)."""
    if not Path(path).exists():
        return pd.DataFrame(columns=LABEL_COLS)
    df = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False, quoting=csv.QUOTE_MINIMAL)
    df["row"] = df["row"].astype(int)
    return df


def merge_labels(labels_path, new_rows):
    """Adds label rows to the labels file under an exclusive lock: the file is read again, rows with the same
    (row, rater) are replaced, and the whole file is rewritten. Safe when one process per rater writes the same
    file. Returns the merged DataFrame."""
    import fcntl
    path = Path(labels_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            have = read_labels(path)
            new = pd.DataFrame(new_rows)[LABEL_COLS]
            keys = set(zip(new["row"], new["rater"]))
            have = have[[k not in keys for k in zip(have["row"], have["rater"])]]
            out = pd.concat([have, new], ignore_index=True).sort_values(["row", "rater"])
            tmp = path.with_suffix(path.suffix + ".tmp")
            out.to_csv(tmp, sep="\t", index=False, quoting=csv.QUOTE_MINIMAL)
            tmp.replace(path)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    return out


def judge_rows(dossiers, nim, models, labels_path, workers=4, judge_fn=judge, retry_unusable=True, chunk=120):
    """Runs every rater on every dossier that has no label yet from that rater's model and merges the new labels into
    the labels file after every `chunk` calls (merge_labels; an interrupted run loses at most one chunk; model
    replies are cached anyway; several processes, one per rater, may write the same file). A stored
    label whose reply was unusable (label_raw '': no valid label in the reply) is asked again when retry_unusable.
    A call that fails is reported and left for the next run. Returns {done, kept, errors}."""
    have = read_labels(labels_path)
    if retry_unusable and len(have):
        have = have[(have["label_raw"] != "") | have["model"].str.startswith("deterministic")]
    seen = set(zip(have["row"], have["rater"], have["model"]))
    jobs = [(row, k, m) for row in sorted(dossiers) for k, m in models.items()
            if (row, k, m) not in seen and (row, k, "deterministic (UniProt cross-reference)") not in seen]

    def one(job):
        row, k, model = job
        try:
            j = judge_fn(dossiers[row], nim, model)
            return {"row": row, "rater": k, "model": j.get("model", model), "label": j["label"], "label_raw": j["label_raw"],
                    "items": " ".join(j["items"]), "problems": " | ".join(j["problems"]), "rationale": j["rationale"],
                    "reply_key": j.get("key", "")}
        except Exception as err:
            return {"row": row, "rater": k, "error": f"{type(err).__name__}: {str(err)[:160]}"}

    done, errors = 0, []
    for i in range(0, len(jobs), max(1, chunk)):
        part = jobs[i:i + max(1, chunk)]
        if workers > 1 and len(part) > 1:
            with ThreadPoolExecutor(workers) as pool:
                results = list(pool.map(one, part))
        else:
            results = [one(j) for j in part]
        good = [r for r in results if "error" not in r]
        errors += [r for r in results if "error" in r]
        if good:
            merge_labels(labels_path, good)
            done += len(good)
    return {"done": done, "kept": len(seen), "errors": errors}


def proposals(labels):
    """One line per row from the labels file: label_A, label_B, label_C, proposal (the label all three give, else ''),
    flagged, majority. Rows without all three raters are left out."""
    wide = labels.pivot_table(index="row", columns="rater", values="label", aggfunc="first")
    wide = wide.reindex(columns=RATERS).dropna()
    out = wide.rename(columns={k: f"label_{k}" for k in RATERS}).reset_index()
    comb = [combine([r[f"label_{k}"] for k in RATERS]) for _, r in out.iterrows()]
    for c in ("proposal", "flagged", "majority"):
        out[c] = [x[c] for x in comb]
    return out


# ------------------------------------------------------------------------------------------------ the owner's sheets
def _links(dossier):
    return "\n".join(f"{s['source']}: {s['url']}" for s in dossier["sources"] if s.get("url"))


def _write_rating_xlsx(df, readme, rules, path, text_cols):
    from openpyxl.styles import Alignment
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="spot_check")
        pd.DataFrame(readme, columns=["item", "value"]).to_excel(w, index=False, sheet_name="README")
        pd.DataFrame(rules, columns=["kind of row", "rule"]).to_excel(w, index=False, sheet_name="rules")
        ws = w.sheets["spot_check"]
        for i, col in enumerate(df.columns, 1):
            wide = col in text_cols
            longest = int(df[col].astype(str).str.len().max()) if len(df) else 0      # an empty sheet (no flagged row) is valid
            width = 110 if col == "dossier" else 60 if wide else min(max(len(str(col)), longest) + 2, 32)
            ws.column_dimensions[get_column_letter(i)].width = width
            for cell in ws[get_column_letter(i)][1:]:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        for r in range(2, len(df) + 2):
            lines = max(str(df.iloc[r - 2][c]).count("\n") + 1 + len(str(df.iloc[r - 2][c])) // 140 for c in text_cols)
            ws.row_dimensions[r].height = min(409, max(30, 13 * lines))
        j = get_column_letter(list(df.columns).index("owner_judgement") + 1)
        dv = DataValidation(type="list", formula1='"' + ",".join(LABELS) + '"', allow_blank=True)
        if len(df):
            ws.add_data_validation(dv)
            dv.add(f"{j}2:{j}{len(df) + 1}")
        ws.freeze_panes = "B2"
        for tab, widths in (("README", (24, 120)), ("rules", (26, 150))):
            for letter, wd in zip("AB", widths):
                w.sheets[tab].column_dimensions[letter].width = wd
            for cell in w.sheets[tab]["B"]:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
    return path


def _rules_tab():
    return [("All rows", ee.RULES_COMMON)] + [(GROUP_TITLES[g], t) for g, t in ee.RULES_GROUP.items()] + [
        (GROUP_TITLES["gene_product"], "No model: supported if the UniProt entry of the protein cross-references the gene.")]


def write_owner_sheet(dossiers, rows, path, seed=OWNER_SEED):
    """The owner's blind sheet: for each of `rows` the claim, the kind of row, the dossier text, the source links and
    an empty judgement. No proposal, no rater label, no rationale, no verdict. Returns the path."""
    recs = []
    for r in rows:
        d = dossiers[r]
        recs.append({"row": r, **{k: d["edge"][k] for k in BLIND_FIELDS}, "kind_of_row": GROUP_TITLES[d["group"]],
                     "dossier": dossier_text(d), "links": _links(d), "owner_judgement": "", "notes": ""})
    readme = [("purpose", "The owner's blind rating of 40 of the 200 pre-registered spot-check rows "
                          "(outputs/pharos/spotcheck_rule.md, Amendment 1, addition A6)."),
              ("generated", dt.datetime.now().strftime("%Y-%m-%d %H:%M") + " by scripts/audit_spotcheck.py owner40"),
              ("rows", f"{len(rows)} of {len(dossiers)} row numbers, simple random sample, numpy default_rng({seed}), "
                       "drawn without reference to the key"),
              ("how to rate", "Read the claim and the dossier. Choose owner_judgement: supported, unsupported or cannot "
                              "tell, by the rule for that kind of row (tab 'rules'). Notes are free text."),
              ("what is not here", "No machine proposal, no rater label or rationale, no pruner verdict, confidence or reasons."),
              ("dossier", "Each line starts with the source and whether it answered. [required source] = the rule needs "
                          "it to have answered before a row can be 'unsupported'. Context items cannot make a row 'supported'."),
              ("after rating", "Save the file under the same name. The flagged rows come in a second sheet, to be opened "
                               "only after these 40 are rated.")]
    return _write_rating_xlsx(pd.DataFrame(recs), readme, _rules_tab(), path, ["dossier", "links"])


def write_adjudication_sheet(dossiers, labels, exclude_rows, path):
    """The owner's second sheet: every flagged row (the three raters do not all agree) that is not among
    `exclude_rows`, with the dossier and the raters' labels and rationales, and an empty judgement. Still no verdict."""
    prop = proposals(labels)
    flagged = prop[prop["flagged"] & ~prop["row"].isin(set(exclude_rows))]
    lab = labels.set_index(["row", "rater"])
    recs = []
    for _, p in flagged.iterrows():
        d = dossiers[int(p["row"])]
        rec = {"row": int(p["row"]), **{k: d["edge"][k] for k in BLIND_FIELDS}, "kind_of_row": GROUP_TITLES[d["group"]],
               "dossier": dossier_text(d), "links": _links(d)}
        for k in RATERS:
            rec[f"rater_{k}"] = f"{lab.loc[(int(p['row']), k), 'label']}: {ee.cut_words(lab.loc[(int(p['row']), k), 'rationale'])}"
        rec.update({"majority": p["majority"] or "(none)", "owner_judgement": "", "notes": ""})
        recs.append(rec)
    cols = ["row"] + BLIND_FIELDS + ["kind_of_row", "dossier", "links"] + [f"rater_{k}" for k in RATERS] + ["majority", "owner_judgement", "notes"]
    readme = [("purpose", "Adjudication of the rows on which the three verdict-blind raters did not all agree "
                          "(outputs/pharos/spotcheck_rule.md, addition A6). Open only after the 40 blind rows are rated."),
              ("generated", dt.datetime.now().strftime("%Y-%m-%d %H:%M") + " by scripts/audit_spotcheck.py adjudicate"),
              ("rows", f"{len(recs)} flagged rows that are not among the owner's blind rows"),
              ("how to rate", "Read the claim, the dossier and the three raters' labels and reasons; choose owner_judgement. "
                              "A row left empty takes the majority label, or 'cannot tell' when there is none."),
              ("what is not here", "No pruner verdict, confidence or reasons.")]
    return _write_rating_xlsx(pd.DataFrame(recs, columns=cols), readme, _rules_tab(), path, ["dossier", "links"] + [f"rater_{k}" for k in RATERS])


# ------------------------------------------------------------------------------------------------ stored records
def edge_ids_from_key(key):
    """{row: edge id} from a key file. Reads the columns `row` and `edge_id` only."""
    df = pd.read_csv(key, sep="\t", dtype=str, usecols=["row", "edge_id"])
    return dict(zip(df["row"].astype(int), df["edge_id"]))


def publish(dossiers, labels, edge_ids, out_dir, min_raters=len(RATERS)):
    """Writes one stored record per edge id (edge_evidence.audit_record: sources, items, judgement, date) for the
    app's independent check, plus index.json (edge id -> label). Rows with fewer than `min_raters` raters get no
    judgement and are skipped. Returns the number written."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    labels = labels[(labels["label_raw"] != "") | labels["model"].str.startswith("deterministic")]   # an unanswered reply is no rating
    by_row = {int(r): g.to_dict("records") for r, g in labels.groupby("row")}
    index = {}
    for row, d in sorted(dossiers.items()):
        js = [dict(j, items=j["items"].split(), problems=[]) for j in by_row.get(row, [])]
        eid = edge_ids.get(row) or d.get("edge_id")
        if not eid or len(js) < min_raters:
            continue
        rec = audit_record(dict(d, edge_id=eid), js)
        (out_dir / f"{eid}.json").write_text(json.dumps(rec, indent=1, sort_keys=True, ensure_ascii=False))
        index[eid] = rec["judgement"]["label"]
    old = json.loads((out_dir / "index.json").read_text()) if (out_dir / "index.json").exists() else {}
    (out_dir / "index.json").write_text(json.dumps({**old, **index}, indent=0, sort_keys=True))
    return len(index)


# ------------------------------------------------------------------------------------------------ command line
def _nim():
    import nim as N
    from snapshot import Snapshot
    client = N.NIM(snap=Snapshot(AUDIT / "snapshots_llm", sleep=0, post_timeout=RATER_POST_TIMEOUT))
    if client.backend != "nim":
        raise SystemExit("no NVIDIA_API_KEY: the raters need the hosted models")
    apply_rater_options(client)
    return client


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["sample", "cited", "collect", "owner40", "judge", "adjudicate", "publish"])
    ap.add_argument("--set", default="spotcheck", choices=sorted(SETS))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="first N rows only (a trial run)")
    ap.add_argument("--rebuild", action="store_true", help="collect: build every dossier again (HTTP answers come from the cache)")
    ap.add_argument("--raters", default="ABC", help="judge: which raters to run (default all three); publish: a row needs this many")
    a = ap.parse_args(argv)
    s = SETS[a.set]
    if a.command == "sample":
        print(draw_audit_sample(exclude_key=SETS["spotcheck"]["key"]))
        return 0
    if a.command == "cited":
        import os
        import pharos_index
        index = pharos_index.CardIndex(os.environ.get("TMKG_PHAROS_INDEX") or str(pharos_index.index_path(ROOT)))
        overlay = ROOT / "data" / "kg" / "pharos_index" / "overlay_v3" / "demo_overlay.json"
        c = SETS["cited"]
        print(write_cited_set(cited_edge_ids(ROOT / "outputs" / "examples", overlay), index.explain_edge, c["sheet"], c["key"]))
        return 0
    if a.command == "collect":
        rows = read_rows(s["sheet"])
        rows = rows[:a.limit] if a.limit else rows
        ids = edge_ids_from_key(s["key"]) if a.set == "cited" else None          # the cited set has no verdicts to protect
        print(collect_rows(rows, s["work"], Fetcher(), Lookups(), workers=a.workers, rebuild=a.rebuild, edge_ids=ids))
        d = load_dossiers(s["work"])
        print(f"{export_dossiers(d, s['dossiers'])} dossiers -> {s['dossiers'].relative_to(ROOT)}")
        print(dossier_summary(d))
        return 0
    dossiers = load_dossiers(s["work"])
    if a.command == "owner40":
        rows = owner_rows(len(read_rows(SETS["spotcheck"]["sheet"])))
        p = write_owner_sheet(load_dossiers(SETS["spotcheck"]["work"]), rows, OUT / "spotcheck_owner40.xlsx")
        print(f"{len(rows)} rows -> {p.relative_to(ROOT)}")
        return 0
    if a.command == "judge":
        client = _nim()
        models = {k: m for k, m in rater_models(client).items() if k in a.raters}
        print("raters:", models)
        if a.limit:
            dossiers = {r: dossiers[r] for r in sorted(dossiers)[:a.limit]}
        res = judge_rows(dossiers, client, models, s["labels"], workers=min(a.workers, 8))
        print({"done": res["done"], "kept": res["kept"], "errors": len(res["errors"])})
        for e in res["errors"][:5]:
            print("  error", e["row"], e["rater"], e["error"])
        p = proposals(read_labels(s["labels"]))
        print(f"rows with three raters {len(p)}; all three agree {int((~p['flagged']).sum())}; flagged {int(p['flagged'].sum())}; "
              f"no majority {int((p['majority'] == '').sum())}")
        return 0
    if a.command == "adjudicate":
        p = write_adjudication_sheet(load_dossiers(SETS["spotcheck"]["work"]), read_labels(SETS["spotcheck"]["labels"]),
                                     owner_rows(len(read_rows(SETS["spotcheck"]["sheet"]))), OUT / "spotcheck_adjudicate.xlsx")
        print(f"-> {p.relative_to(ROOT)}")
        return 0
    if a.command == "publish":
        n = publish(dossiers, read_labels(s["labels"]), edge_ids_from_key(s["key"]), AUDIT / "edges", min_raters=len(a.raters))
        print(f"{n} stored records -> {(AUDIT / 'edges').relative_to(ROOT)}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
