"""
Small, testable pieces behind the app's first screen and its honesty labels (docs/USABILITY_PLAN.md; MASTER_PLAN packet CC-6).
Everything is computed from files or objects already in the repo; no text here states a result.

  landing_chips(personas)       -> three example chips for the one search box (an entity card, a graph question, a trial question)
  model_label(nim, preflight, paused) -> {backend, offline, label, note, warn, paused}: which model answers, in words for
                                   the header badge; paused when the last hosted calls all failed (ModelHealth)
  answer_label(result)          -> {kind 'hosted' | 'offline' | 'cached' | 'fallback' | 'partly_offline' | 'second_model' |
                                   'model_unavailable' | 'too_broad', text, offline}: where ONE answer came from
                                   (PK15: + graph, graph_text when asked on Amazon Neptune)
  card_next_steps(entities, labels) -> FIX1 F4: "Open the <label> card" for the first gene or disease (two when two were
                                   recognised), the next step of an answer the model could not run
  generalization(root)          -> the Generalization tab: the trial layer, the bring-your-own-source example (built on the spot),
                                   the ADC example (SYNTHETIC) - counts read from files, never typed
  agent_story(root)             -> PK7B: the five-step strip of the "What the agent did" tab, in user words, each figure read from a record
  no_evidence_example(root)     -> PK7B: one stored question whose honest answer is "no evidence in this graph", with the nearest related
                                   evidence (trial-graph rows with edge ids)
  readable_page(name, root)     -> PK7B: the tutorial / the ADC answer as blocks the page can show (GET /api/page/{name}, routes_pages.py)
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFLINE = "offline-stand-in"


def landing_chips(personas):
    """Takes the persona list (personas.load_personas()). Returns exactly three chips {kind, text, label, persona}: the first
    entity-card question, the first Pharos-graph question and the first trial question that any persona offers, in persona
    order, so the chips are the examples the role pages already show. kind: 'card' | 'pharos' | 'trials'."""
    want = [("card", "Open a card"), ("pharos", "Ask the graph"), ("trials", "Ask about failed trials")]
    chips = []
    for kind, label in want:
        for p in personas:
            q = next((x for x in p.get("questions", []) if x.get("tab") == kind and x.get("text")), None)
            if q:
                chips.append(dict(kind=kind, text=q["text"], label=label, persona=p["id"]))
                break
    return chips


PAUSED_NOTE = ("Typed questions are paused: the model service is not answering. Stored and tested answers, cards and "
               "downloads work.")


class ModelHealth:
    """FIX1 F4 (gate 1, G1-U-01): the outcome of the last answers that needed the hosted model, kept in memory (no extra model
    call). record(ok, now) after each such answer (ok False when the hosted model did not answer); paused(now) is True when the
    last `n` recorded outcomes all failed and all fall inside the last `window` seconds. Thread-safe enough for the app: a
    deque append / read under the GIL."""
    def __init__(self, n=3, window=600.0, keep=20):
        from collections import deque
        self.n, self.window = n, window
        self.log = deque(maxlen=keep)

    def record(self, ok, now):
        self.log.append((float(now), bool(ok)))

    def paused(self, now):
        last = list(self.log)[-self.n:]
        return len(last) == self.n and all(not ok and now - t < self.window for t, ok in last)

    def clear(self):
        self.log.clear()


def label_outcome(label):
    """Takes an answer label (answer_label). Returns True when the hosted model answered (hosted, backup model, partly
    offline), False when it did not (model_unavailable, fallback), None when the answer says nothing about the model service
    (stored, offline backend, too broad)."""
    kind = (label or {}).get("kind")
    return True if kind in ("hosted", "second_model", "partly_offline") else False if kind in ("model_unavailable", "fallback") else None


def model_label(nim, preflight=None, paused=False):
    """Takes a NIM, optionally the start-up check (nim.preflight_summary(...) dict) and whether typed questions are paused
    (ModelHealth.paused: the last hosted calls all failed). Returns {backend, offline, label, note, preflight, warn, paused}
    for the header: hosted Nemotron, or the offline stand-in with what that means (rule-based, deterministic, not a language
    model). With a check, its sentence is appended to the note; warn is True when a hosted model failed the check or typed
    questions are paused (the page then shows a banner); when paused the note says so in plain words (PAUSED_NOTE)."""
    off = nim.backend == "offline"
    note = ("Questions are answered by fixed rules, not a language model: set NVIDIA_API_KEY to use the hosted model."
            if off else "Answers come from NVIDIA Nemotron; each cites edges of the graph and is checked against them.")
    if preflight and preflight.get("sentence") and not off:
        note += " " + preflight["sentence"]
    paused = bool(paused and not off)
    if paused:
        note = PAUSED_NOTE
    return dict(backend=nim.backend, offline=off,
                label="Offline stand-in (no hosted model)" if off else "NVIDIA Nemotron (hosted)",
                note=note, preflight=preflight, paused=paused,
                warn=bool((preflight and preflight.get("state") == "failed" and not off) or paused))


GENE_PREFIXES = ("NCBIGene:", "HGNC:", "ENSEMBL:")         # not UniProtKB: a gene and its protein are one card choice
DISEASE_PREFIXES = ("MONDO:", "DOID:", "EFO:", "Orphanet:", "OMIM:", "UMLS:", "MESH:")


def card_next_steps(entities, limit=2):
    """FIX1 F4 (gate 1, G1-U-02). Takes the recognised entities of an answer ([{id, label}], summary['plan']['entities']).
    Returns (sentence, cards): cards = [{id, label}] for the first gene or disease (and the second, when two were
    recognised), in the order of the question; sentence = "Open the LRRK2 card" / "Open the LRRK2 card or the NASH card",
    or "" when no gene or disease was recognised. Only ids of a gene or disease vocabulary count (a pathway or a phenotype
    is not offered)."""
    cards = []
    for e in entities or []:
        eid = str((e or {}).get("id") or "")
        if eid.startswith(GENE_PREFIXES + DISEASE_PREFIXES) and eid not in {c["id"] for c in cards}:
            cards.append(dict(id=eid, label=str(e.get("label") or eid)))
        if len(cards) >= limit:
            break
    sentence = " or ".join(f"the {c['label']} card" for c in cards)
    return ("Open " + sentence if cards else ""), cards


def answer_label(result):
    """PK15: the label of _model_answer_label (below), plus, when the question was asked on Amazon Neptune (result['neptune']),
    graph = the graph that answered in user words ('Amazon Neptune' or "this server's graph") and graph_text, the sentence the
    page shows (the model text and kind are unchanged, so an answer not asked on Neptune gets exactly the label it had)."""
    lab = _model_answer_label(result)
    nep = result.get("neptune")
    if isinstance(nep, dict) and nep.get("asked"):
        on = nep.get("answered") and result.get("graph_backend") == "neptune"
        lab = dict(lab, graph="Amazon Neptune" if on else "this server's graph",
                   graph_text="The rows come from Amazon Neptune." if on else
                   "The rows come from this server's graph; Amazon Neptune did not answer this question.")
    return lab


def _model_answer_label(result):
    """Takes one answer dict (querier.Querier.ask / orchestrator report: keys backend, cached, fallback_from). Returns
    {kind, text, offline}: 'model_unavailable' when the hosted model failed and no query could be run at all (no rows,
    no_query), 'fallback' when the hosted model failed and the stand-in answered, 'offline' for the stand-in,
    'cached' for a stored hosted answer, else 'hosted'. The text is what the page shows next to the answer."""
    if result.get("server_error") and isinstance(result.get("label"), dict):   # FIX1 F5: app.query_failed_answer
        return result["label"]
    backend = str(result.get("backend") or "")
    broad = [str(x) for x in result.get("problems") or [] if "too broad" in str(x)]
    if broad:
        m = re.search(r"\((it [^)]*)\)\s*$", broad[0])           # the named limit (querier.too_broad_text)
        return dict(kind="too_broad", offline=backend in (OFFLINE, "offline"),
                    text="This question is too broad for the hosted demo" + (f": {m.group(1)}" if m else "") +
                         ". Name a specific gene, disease or compound and ask again.")
    if result.get("no_query") and not result.get("rows") and result.get("fallback_from"):
        # SEC-03: the hosted model failed and the stand-in could not write a query either: this is NOT "the graph has no rows"
        lead = ("Open the card of a gene or disease named in the question (cards are read without the model), try again "
                "in a minute, or use one of the tested questions.") if result.get("entities") else \
            "Try again in a minute, or use one of the tested questions."           # FIX1 F4 (G1-U-02)
        return dict(kind="model_unavailable", offline=True,
                    text="The hosted model did not answer, so this question was not run on the graph. " + lead)
    calls = result.get("model_calls") or []                # orchestrator run: per call, which backend answered
    k_off = sum(1 for c in calls if c.get("backend") == OFFLINE)
    if backend == "nim" and not result.get("fallback_from") and 0 < k_off < len(calls):
        # FIX1 F4 (G1-P1-04): some steps of a report used the stand-in, the others the hosted model: say how many
        return dict(kind="partly_offline", offline=False, steps_offline=k_off, steps=len(calls),
                    text=f"Partly answered by fixed rules: the hosted model did not answer {k_off} of the {len(calls)} steps "
                         "of this report, so fixed rules answered those; the hosted model answered the others. Check the "
                         "evidence rows; they are the graph's own.")
    if result.get("fallback_from") or (backend == "nim" and k_off and k_off == len(calls)):
        return dict(kind="fallback", offline=True,
                    text="Fixed-rule answer: the hosted model could not be reached, so fixed rules answered instead. "
                         "Check the evidence rows; they are the graph's own.")
    if result.get("model_fallback_from"):
        return dict(kind="second_model", offline=False,
                    text=f"Backup model: the main hosted model ({result['model_fallback_from']}) did not answer, so "
                         f"{result.get('model') or 'the backup model'} answered. Check the evidence rows; they are the graph's own.")
    if backend in (OFFLINE, "offline"):
        return dict(kind="offline", offline=True,
                    text="Fixed-rule answer: this answer comes from fixed rules, not a language model.")
    if result.get("cached"):
        return dict(kind="cached", offline=False,
                    text="Stored answer of the hosted model (recorded earlier, shown without calling the API).")
    return dict(kind="hosted", offline=False, text="")


def _sentences(path, marker):
    """Lines of a file that contain `marker` (case-sensitive), stripped; [] if the file is missing."""
    p = Path(path)
    return [l.strip() for l in p.read_text().splitlines() if marker in l] if p.exists() else []


def _byo(root):
    """The bring-your-own-source example: the five steps from the tutorial (bold title and the command, when it has one), and the card built NOW on its
    synthetic table (counts from the builder, problems listed), or why it could not be built here."""
    doc = Path(root) / "docs" / "BRING_YOUR_OWN_SOURCE.md"
    steps = []
    if doc.exists():
        for m in re.finditer(r"^(\d)\. (.+)$", doc.read_text(), re.M):
            bold = re.search(r"\*\*(.+?)\*\*", m.group(2))
            cmd = re.search(r"`([^`]+)`", m.group(2))
            title = (bold.group(1) if bold else m.group(2)).strip().rstrip(":.")
            steps.append(dict(title=title, command=cmd.group(1) if cmd and cmd.group(1).startswith("python") else ""))
    steps = steps[:5]
    out = dict(doc="docs/BRING_YOUR_OWN_SOURCE.md", card="examples/byo_source/lab_screen.yaml", steps=steps,
               label="the example hits and signal values are SYNTHETIC; the identifiers are real", built=None, reason="")
    try:
        import sys
        sys.path.insert(0, str(Path(root) / "pipelines"))
        from kgbuilder import engine
        from kgbuilder.biolink import BioLink, DEFAULT
        if not Path(DEFAULT).exists():
            raise FileNotFoundError("BioLink model file not downloaded (scripts/download_external_data.sh)")
        card = engine.load_card(Path(root) / out["card"])
        problems = engine.check_card(card, BioLink())
        b = engine.build(card, tables_dir=Path(root) / card["tables_dir"], bl=BioLink())
        out["built"] = dict(nodes=int(len(b.nodes)), edges=int(len(b.edges)), problems=problems + list(b.problems),
                            classes=sorted(set(b.nodes["category"])), predicates=sorted(set(b.edges["predicate"])),
                            sources=sorted(set(b.edges["primary_knowledge_source"])))
    except Exception as err:
        out["reason"] = f"could not be built here: {type(err).__name__}: {str(err)[:150]}"
    return out


def _adc(root):
    """The ADC example (TUB-040): title and the SYNTHETIC label lines of the frozen answer docs/demo_query_TUB040.md."""
    p = Path(root) / "docs" / "demo_query_TUB040.md"
    if not p.exists():
        return dict(available=False)
    lines = p.read_text().splitlines()
    return dict(available=True, file="docs/demo_query_TUB040.md", title=lines[0].lstrip("# ").strip(),
                label="the internal layer (data/synthetic) is SYNTHETIC, not sponsor data", synthetic_lines=len(_sentences(p, "SYNTHETIC")),
                question=next((l.lstrip("> ").strip() for l in lines if l.startswith("> ")), ""),
                sections=[l.lstrip("# ").strip() for l in lines if l.startswith("## ")])


def generalization(root=ROOT):
    """Takes the repo root. Returns the Generalization tab: {trial_layer: counts, cards: source cards, byo, adc}. The trial layer's
    counts come from outputs/run_manifest.json, the cards from sources/*.yaml, the example is built now; nothing is typed in."""
    root = Path(root)
    man = {}
    if (root / "outputs" / "run_manifest.json").exists():
        man = json.loads((root / "outputs" / "run_manifest.json").read_text()).get("counts", {})
    cats, preds = man.get("by_category", {}), man.get("by_predicate", {})
    cards = []
    for f in sorted((root / "sources").glob("*.yaml")) if (root / "sources").exists() else []:
        txt = f.read_text()
        m = re.search(r"^layer:\s*(\S+)", txt, re.M)
        cards.append(dict(card=f"sources/{f.name}", name=(re.search(r"^name:\s*(\S+)", txt, re.M) or [None, f.stem])[1],
                          layer=m.group(1) if m else ""))
    byo = _byo(root)
    return dict(
        pages=[dict(id=k, title=v["title"], url=f"/api/page/{k}") for k, v in PAGES.items() if (root / v["file"]).is_file()],
        trial_layer=dict(nodes=man.get("nodes"), edges=man.get("edges"), classes=len(cats), predicates=len(preds),
                         programs=cats.get("tmkg:ClinicalTrialProgram"), source="outputs/run_manifest.json",
                         shows="a second, different graph - failed drug programs, trials, arms, outcomes, adverse events - in "
                               "the same KGX/BioLink exchange format and with the same provenance fields on every edge"),
        cards=cards, byo=byo, adc=_adc(root))


AGENT_LOOP_DOC = "docs/AGENT_LOOP.md"


def agent_loop(root=ROOT):
    """Takes the repo root. Returns the "For judges" page: {brief: the brief's file, doc, steps: [{step, brief, plain, module,
    functions, command, tests, exercises, also}], honest: the sentences under "What is autonomous and what is not"}, read from
    the yaml block of docs/AGENT_LOOP.md (the one place the mapping is written; the README and the test read the same
    block). {available: False} when the file is missing."""
    import yaml
    p = Path(root) / AGENT_LOOP_DOC
    if not p.exists():
        return dict(available=False, doc=AGENT_LOOP_DOC)
    text = p.read_text()
    block = re.search(r"```yaml\n(.*?)```", text, re.S)
    steps = yaml.safe_load(block.group(1))["loop"] if block else []
    honest = re.search(r"## What is autonomous and what is not\n(.*?)\n## ", text, re.S)
    bullets = [re.sub(r"\s+", " ", b).strip() for b in re.split(r"\n- ", "\n" + honest.group(1))[1:]] if honest else []
    return dict(available=True, doc=AGENT_LOOP_DOC, brief="docs/organizers/BRIEF.md",
                steps=[{**s, "plain": re.sub(r"\s+", " ", s["plain"]).strip()} for s in steps], honest=bullets,
                story=agent_story(root), no_evidence=no_evidence_example(root))


# ---- PK7B: the strip, the stored "no evidence" example and the readable pages
def _sys_path():
    import sys
    p = str(Path(__file__).resolve().parent)
    if p not in sys.path:
        sys.path.insert(0, p)


# What each step does, in user words (a description of the method; every figure beside it is read from a record).
STORY_WORDS = {
    "Ingest": "Each source is read through a short card that says where its columns go. A row whose identifier cannot be "
              "standardised is counted and listed, never guessed or dropped silently.",
    "Filter": "A question becomes one query on the graph, checked against what the graph really holds. The answer is the "
              "matching links, each with its source. A question the graph cannot support is answered \"no evidence\".",
    "Clean": "Every Pharos link gets keep or prune, a confidence between 0 and 1 and the reasons. Pruned links stay in the "
             "record, marked.",
    "Limit": "The links marked prune are left out of the version people query, and each answer carries only its own small graph.",
    "Maintain": "A weekly check compares each source's current release with what was pulled. A scientist approves every "
                "rebuild and reads the change against the build before it.",
}
STORY_STEPS = ["Ingest", "Filter", "Clean", "Limit", "Maintain"]


def _fig(n, label, record):
    return dict(value=n, text=f"{n:,}", label=label, record=record)


def agent_story(root=ROOT):
    """Takes the repo root. Returns the five-step strip of the "What the agent did" tab: {available, steps: [{step, what,
    figures: [{value, text, label, record}]}], records: {record title: committed file}, scientist_decisions}. Every figure is
    read from a committed record (the layers' build records, the pruning report, the assembled graph's record, the tested
    questions, the change records); a record missing on this machine gives no figure for it and nothing raises. Nothing is typed."""
    _sys_path()
    import routes_agent as ra
    root = Path(root)
    figs = {k: [] for k in STORY_STEPS}
    rec = {}
    layers = [r for r in ra.shipped_records(root) if not re.match(r"outputs/g\d+/", r)]
    nodes = edges = left = built = 0
    for rel in layers:
        text = ra._read(root, rel) or ""
        s = ra.parse_summary(text)
        if s["n_edges"] is None or not s["title"]:
            continue
        built += 1
        nodes, edges = nodes + s["n_nodes"], edges + s["n_edges"]
        left += sum(_int_cell(c[2]) for c in ra._table(text, "source rows not in the graph") if len(c) == 3)
    if built:
        rec["Build records of the layers"] = "outputs/<layer>/summary.md (one per layer)"
        figs["Ingest"] += [_fig(built, "layers built, each from its own source card", "Build records of the layers"),
                           _fig(edges, "links in those layers", "Build records of the layers")]
        if left:
            figs["Ingest"].append(_fig(left, "source rows counted as not mapped (identifier could not be standardised)",
                                       "Build records of the layers"))
    golden = root / "data" / "curated" / "golden_questions.csv"
    if golden.is_file():
        import csv
        n = sum(1 for _ in csv.DictReader(golden.open(encoding="utf-8")))
        if n:
            rec["Tested questions"] = "data/curated/golden_questions.csv"
            figs["Filter"].append(_fig(n, "questions with a known answer that must keep passing against the trial graph", "Tested questions"))
    text = ra._read(root, ra.PRUNE_REPORT) or ""
    m = re.search(r"edges scored: \**([\d,]+)\**; verdict 'prune': \**([\d,]+)\**", text)
    if m:
        scored, pruned = ra._int(m.group(1)), ra._int(m.group(2))
        rec["Pruning report of the Pharos layer"] = ra.PRUNE_REPORT
        figs["Clean"] += [_fig(scored, "Pharos links given a confidence", "Pruning report of the Pharos layer"),
                          _fig(pruned, "of them marked prune", "Pruning report of the Pharos layer")]
    for rel in [r for r in ra.shipped_records(root) if re.match(r"outputs/g\d+/", r)]:
        t = ra._read(root, rel) or ""
        c = re.search(r"^([\d,]+) nodes, ([\d,]+) edges; ([\d,]+) edges removed by pruning", t, re.M)
        if c and ra._int(c.group(3)):                      # the assembled graph that left pruned links out
            rec["Record of the enriched graph"] = rel
            figs["Limit"] += [_fig(ra._int(c.group(3)), "links marked prune left out of the version people query", "Record of the enriched graph"),
                              _fig(ra._int(c.group(2)), "links kept in it", "Record of the enriched graph")]
            break
    entries = ra.build(root)
    diffs = [e for e in entries if e["step"] == "Maintain"]
    for e in diffs:
        for title, rel, _ in e["_cites"]:
            rec.setdefault(title, rel)
    if diffs:
        figs["Maintain"].append(_fig(len(diffs), f"layer{'s' if len(diffs) != 1 else ''} compared with the build before {'them' if len(diffs) != 1 else 'it'}", diffs[0]["_cites"][0][0]))
    decisions = sum(e["by"] == "scientist" for e in entries)
    if decisions:
        rec["Experiment log"] = ra.LEDGER
        figs["Maintain"].append(_fig(decisions, f"decision{'s' if decisions != 1 else ''} taken by the scientist, marked in the log below", "Experiment log"))
    steps = [dict(step=k, what=STORY_WORDS[k], figures=figs[k]) for k in STORY_STEPS]
    return dict(available=any(f for f in figs.values()), steps=steps, records=rec, scientist_decisions=decisions)


def _int_cell(x):
    try:
        return int(str(x).replace(",", "").strip())
    except ValueError:
        return 0


def _trial_kg(root):
    """The trial graph read through the answer harness (outputs/kg_nodes.csv + kg_edges.csv), or None when it is not on disk."""
    _sys_path()
    import kg_query
    out = Path(root) / "outputs"
    return kg_query.KGIndex(out) if (out / "kg_nodes.csv").is_file() and (out / "kg_edges.csv").is_file() else None


def no_evidence_example(root=ROOT):
    """Takes the repo root. Returns ONE stored example whose honest answer is "no evidence in this graph": {available, question,
    answer, headline, nearest: {trials: [...], related_programs: [...]}, edges: [edge ids], note}. The program is chosen by
    computation from the trial graph: the first program (in id order) that has trials but no recorded post-failure analysis,
    repurposing pivot or data-access route, and that shares a target with a program that has one. Answers only through
    kg_query functions; every row carries the edge ids it rests on. {available: False} when the trial graph is not on disk or
    no program fits."""
    kg = _trial_kg(root)
    if kg is None:
        return dict(available=False)
    pids = sorted(n["id"] for n in kg.nodes.values() if n.get("category") == "tmkg:ClinicalTrialProgram")
    shared = kg.shared_targets()["rows"]
    history = {p: kg.program_history(p)["rows"] for p in pids}
    for pid in pids:
        trials = kg.program_trials(pid)["rows"]
        if not trials or history[pid]:
            continue
        related = sorted({q for r in shared if pid in r["programs"] for q in r["programs"] if q != pid and history[q]})
        if not related:
            continue
        name = kg.nodes[pid].get("name") or pid
        rel_rows = []
        for q in related:
            kinds = sorted({str(h.get("kind") or "").replace("_", " ") for h in history[q] if h.get("kind")})
            rel_rows.append(dict(program=kg.nodes[q].get("name") or q, records=len(history[q]), kinds=kinds,
                                 targets=sorted({r["target_name"] for r in shared if {pid, q} <= set(r["programs"])}),
                                 edges=sorted({e for h in history[q] for e in h.get("evidence", [])})))
        tr_rows = [dict(trial=t["trial"].split(":", 1)[-1], title=t.get("acronym") or t.get("name"), status=(t.get("status") or "").lower().replace("_", " "),
                        phase=(t.get("phase") or "").replace("PHASE", "Phase ").strip(), has_results=t.get("has_results") == "true",
                        edges=list(t.get("evidence", []))) for t in trials]
        n_rel = sum(r["records"] for r in rel_rows)
        edges = sorted({e for r in tr_rows for e in r["edges"]} | {e for r in rel_rows for e in r["edges"]})
        return dict(
            available=True,
            question=f"Is there a recorded post-failure analysis, repurposing pivot or data-access route for {name}?",
            headline="No evidence in this graph.",
            answer=(f"The graph holds no post-failure analysis, repurposing pivot or data-access route for this program. "
                    f"It does hold {len(tr_rows)} trial{'s' if len(tr_rows) != 1 else ''} of the program"
                    f" and {n_rel} such record{'s' if n_rel != 1 else ''} for {len(rel_rows)} other "
                    f"program{'s' if len(rel_rows) != 1 else ''} aimed at {'the same target' if len({t for r in rel_rows for t in r['targets']}) == 1 else 'the same targets'}. "
                    f"Those are shown below as related evidence; they are not an answer about {name}."),
            nearest=dict(trials=tr_rows, related_programs=rel_rows), edges=edges,
            note="The graph says nothing here, and that is different from saying there is nothing: a record may exist that "
                 "the graph's sources do not cover. The page does not guess.")
    return dict(available=False)


PAGES = {
    "tutorial": dict(title="Tutorial: from your table to a graph", file="docs/BRING_YOUR_OWN_SOURCE.md", developer=True),
    "adc-answer": dict(title="The sponsor-question answer (SYNTHETIC internal data)", file="docs/demo_query_TUB040.md", developer=False),
}
# A path-like code span in a page for readers is replaced by what it is (a name, never a path).
_PATH_WORDS = [(r"outputs/adc/kg_edges\.csv", "the ADC graph's edge table"), (r"SYN_\*\.csv", "the synthetic tables"),
               (r"adc_demo\.py", "the generator"), (r"data/synthetic", "the synthetic internal layer"),
               (r"\s*\(node [A-Za-z]+:\w+\)", ""), (r",\s[A-Z]+(?:_[A-Z]+)+", "")]      # a graph node id and a constant's name


def _readerize(text):
    for pat, words in _PATH_WORDS:
        text = re.sub(r"`?" + pat + r"`?", words, text)
    return re.sub(r"`([^`]*[/.][^`]*\.(?:py|csv|tsv|gz|md|yaml|json))`", "a file of the repository", text)


def readable_page(name, root=ROOT):
    """Takes a page name of PAGES and the repo root. Returns {id, title, developer, label, blocks: [{t: 'h'|'p'|'ul'|'ol'|'quote'|
    'code'|'table', ...}]} read from the committed Markdown file, or None for a name that is not a page (nothing else is ever
    read). Text only: no HTML is passed through. The page for readers has path-like code spans replaced by their names."""
    spec = PAGES.get(name)
    f = Path(root) / spec["file"] if spec else None
    if not spec or not f.is_file():
        return None
    lines = f.read_text(encoding="utf-8").splitlines()
    fix = (lambda x: x) if spec["developer"] else _readerize
    blocks, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
        elif ln.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            blocks.append(dict(t="code", text="\n".join(lines[i + 1:j]))); i = j + 1
        elif ln.startswith("#"):
            blocks.append(dict(t="h", level=len(ln) - len(ln.lstrip("#")), text=fix(ln.lstrip("# ").strip()))); i += 1
        elif ln.startswith(">"):
            blocks.append(dict(t="quote", text=fix(ln.lstrip("> ").strip()))); i += 1
        elif ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not set("".join(cells)) <= set("-: "):
                    rows.append([fix(c) for c in cells])
                i += 1
            blocks.append(dict(t="table", header=rows[0], rows=rows[1:]))
        elif re.match(r"\s*(?:[-*]|\d+\.)\s", ln):
            ordered = bool(re.match(r"\s*\d+\.", ln))
            items = []
            while i < len(lines) and (re.match(r"\s*(?:[-*]|\d+\.)\s", lines[i]) or (lines[i].startswith("  ") and lines[i].strip() and items)):
                if re.match(r"\s*(?:[-*]|\d+\.)\s", lines[i]) and not (lines[i].startswith("   ") and items):
                    items.append(fix(re.sub(r"^\s*(?:[-*]|\d+\.)\s", "", lines[i])))
                else:
                    items[-1] += " " + fix(lines[i].strip())
                i += 1
            blocks.append(dict(t="ol" if ordered else "ul", items=items))
        else:
            para = [ln.strip()]
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(r"```|#|>|\||\s*(?:[-*]|\d+\.)\s", lines[i]):
                para.append(lines[i].strip()); i += 1
            blocks.append(dict(t="p", text=fix(" ".join(para))))
    synthetic = "SYNTHETIC" in f.read_text(encoding="utf-8")
    return dict(id=name, title=spec["title"], developer=spec["developer"], blocks=blocks,
                label="Contains SYNTHETIC data: the numbers illustrate the method." if synthetic else "")


DOWNLOADS_FILE = "downloads.json"


def downloads(root=ROOT, env=None):
    """Takes the repo root (and the environment). Returns the Downloads section of the For-judges page and the README: {available,
    base_url, files: [{title, what, path, url, folder, bytes, sha256}]} read from downloads.json. base_url comes from the file, or from
    TMKG_DOWNLOADS_BASE when that is set; while it is empty, or the file is missing or lists nothing, available is False and the
    page and README show nothing. Nothing is invented: every entry is one the manifest lists."""
    import os
    env = os.environ if env is None else env
    p = Path(root) / DOWNLOADS_FILE
    try:
        man = json.loads(p.read_text())
    except (OSError, ValueError):
        return dict(available=False, base_url="", files=[])
    base = (env.get("TMKG_DOWNLOADS_BASE") or man.get("base_url") or "").strip()
    if base and not base.endswith("/"):
        base += "/"
    files = []
    for f in man.get("files", []):
        if not f.get("path") or not f.get("title"):
            continue
        path = str(f["path"]).lstrip("/")
        files.append(dict(title=f["title"], what=f.get("what", ""), path=path, url=base + path if base else "",
                          folder=path.endswith("/"), bytes=f.get("bytes"), sha256=f.get("sha256")))
    return dict(available=bool(base and files), base_url=base, files=files)
