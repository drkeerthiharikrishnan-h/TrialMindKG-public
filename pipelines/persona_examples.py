"""
Worked examples per role, built from the failed drug programs of the trial layer (the /v3 page; docs/ux/V3_DESIGN_2026-11-08.md).

Each example is a question phrased the way that role asks it, tied to one real program of the trial graph. Its CONTEXT
(program, trial ids and acronyms, phase, status, drug, target and the target's gene, indication, sponsor, year, the
stated failure reason and the registry's 'why stopped') is read from the trial graph when the page is served, never
typed here: only the question text and the program id are written below, and tests check every id exists.

How each example runs (all existing routes):
- trials: the four-part failed-trial report (GET /api/ask_stream?q=...)
- card:   the Pharos card of `entity` (GET /api/pharos/search then /api/pharos/card/<id>); `expect_kind` names the evidence
          kind the card must hold for the example to make sense (e.g. 'binding or activity')
- query:  the trial-graph querier (POST /api/query)

Functions
- program_context(kg, program_id) -> dict of the context fields that exist for that program
- build(kg) -> {persona id: [example, ...]}   examples with their context, in SPEC order
- check(example, kg, nim, card_index=None) -> {ok, n, note}   runs one example offline / on the local graph
- inject(html, examples) -> the page with the examples in place of the placeholder (the /v3 route)
"""
import json

PLACEHOLDER = "/*__PERSONA_EXAMPLES__*/null"

# Which question each role asks (the owner's four lenses mapped onto the five existing roles, personas/*.yaml):
#   clinician / physician-scientist = clinician and translational; bench scientist = biologist (mechanism, pathways);
#   drug-development lead = medicinal chemist (what else binds the target, potency) + portfolio (what a failure implies);
#   computational biologist / judge = evidence strength.
SPEC = [
    # clinician: why did this phase fail, who might still respond
    dict(persona="clinician", lens="Why did it fail, and who might still respond?", kind="trials", program="P20",
         question="Why did mepolizumab fail in METREO for COPD, and which patients might respond?"),
    dict(persona="clinician", lens="Why did it fail, and who might still respond?", kind="trials", program="P13",
         question="Why did tofersen miss its VALOR primary endpoint, and which patients might still benefit?"),
    dict(persona="clinician", lens="Why did it fail, and who might still respond?", kind="trials", program="P11",
         question="Why did prasinezumab miss its primary endpoints in PASADENA and PADOVA, and which patients might still respond?"),
    dict(persona="clinician", lens="What did the fuller data show?", kind="trials", program="P05",
         question="Why were aducanumab's ENGAGE and EMERGE trials stopped for futility, and what did the fuller data show?"),
    # physician-scientist: targets credibly linked to the disease, and the failed trials behind them
    dict(persona="physician_scientist", lens="What changed after the failure?", kind="trials", program="P19",
         question="Why did TULIP-1 miss its primary endpoint, and what changed later?"),
    dict(persona="physician_scientist", lens="Could a genetic subgroup respond?", kind="trials", program="P25",
         question="Did a genotype-defined population rescue dalcetrapib in dal-GenE?"),
    dict(persona="physician_scientist", lens="How is the failed target linked to disease?", kind="card", program="P02",
         entity="BACE1", expect_kind=None,
         question="Which diseases is BACE1, the target of verubecestat, linked to, and by what kind of evidence?"),
    dict(persona="physician_scientist", lens="What exactly was measured?", kind="query", program="P22",
         question="What was the primary endpoint of RESOLVE-IT?"),
    # bench scientist (biologist): mechanism and pathways around the target
    dict(persona="bench_scientist", lens="Mechanism around the target", kind="card", program="P19", entity="Interferon alpha/beta receptor 1",
         expect_kind="annotation",
         question="Which pathways and processes involve the IFNAR1 receptor protein that anifrolumab blocks?"),
    dict(persona="bench_scientist", lens="Mechanism around the target", kind="card", program="P11", entity="Alpha-synuclein",
         expect_kind="annotation",
         question="Which processes and interactors does alpha-synuclein, the target of prasinezumab, have?"),
    dict(persona="bench_scientist", lens="What does the drug act on?", kind="query", program="P21",
         question="Which targets does selonsertib bind, and by which mechanism?"),
    dict(persona="bench_scientist", lens="Mechanism around the target", kind="card", program="P21", entity="Mitogen-activated protein kinase kinase kinase 5",
         expect_kind="annotation",
         question="Which pathways involve the MAP3K5 kinase protein that selonsertib inhibits?"),
    # drug-development lead: medicinal chemistry, then portfolio
    dict(persona="drug_developer", lens="Medicinal chemistry: what else binds the target?", kind="card", program="P25",
         entity="Cholesteryl ester transfer protein", expect_kind="binding or activity",
         question="What else binds cholesteryl ester transfer protein, dalcetrapib's target, and how potent is it?"),
    dict(persona="drug_developer", lens="Medicinal chemistry: what else binds the target?", kind="card", program="P02",
         entity="Beta-secretase 1", expect_kind="binding or activity",
         question="Which other compounds bind beta-secretase 1, the target of verubecestat, and how strongly?"),
    dict(persona="drug_developer", lens="Portfolio: which programs share a target?", kind="query", program=None,
         question="Which failed programs share a drug target?"),
    dict(persona="drug_developer", lens="Portfolio: what does this failure imply?", kind="trials", program="P10",
         question="What does the semaglutide evoke failure imply for similar programs, and how strong is the evidence?"),
    # computational biologist / judge: evidence strength
    dict(persona="computational_biologist", lens="How strong is the evidence?", kind="trials", program="P22",
         question="How strong is the evidence that elafibranor's RESOLVE-IT missed its NASH-resolution endpoint?"),
    dict(persona="computational_biologist", lens="What exactly was measured?", kind="query", program="P17",
         question="What was the primary endpoint of ENHANCE?"),
    dict(persona="computational_biologist", lens="How strong is the evidence?", kind="card", program="P24", entity="PPARA",
         expect_kind=None,
         question="How much of what the graph says about PPARA, the target of pemafibrate, is curated, statistical or text-mined?"),
]

RUNS_AS = {"trials": "failed-trial report", "card": "Pharos card", "query": "trial-graph query"}


def _out(kg, nid, predicate):
    return [kg.nodes[e["object"]] for e in kg.by_subject.get(nid, []) if e["predicate"] == predicate and e["object"] in kg.nodes]


def program_context(kg, program_id):
    """Takes the trial graph (kg_query.KGIndex) and a program id ('P19'). Returns the context fields that exist: program
    {id, name}, indication, sponsor, year, stated_failure (the program record's what_failed), source_url, trials [{id,
    acronym, phase, status, why_stopped, start, primary_completion}], drugs [{id, name}], targets [{id, name, gene,
    mechanism}]. Fields that are empty in the data are left out. None for an unknown id."""
    p = kg.nodes.get(program_id) if program_id else None
    if not p or p.get("category") != "tmkg:ClinicalTrialProgram":
        return None
    ctx = dict(program=dict(id=p["id"], name=p.get("name")))
    for k, out in (("indication", "indication"), ("sponsor", "sponsor"), ("year", "year"), ("what_failed", "stated_failure"),
                   ("source_url", "source_url")):
        if p.get(k):
            ctx[out] = p[k]
    trials = []
    for t in _out(kg, p["id"], "tmkg:has_trial"):
        trials.append({k: t[k] for k in ("id", "acronym", "phase", "status", "why_stopped", "start", "primary_completion") if t.get(k)})
    drugs, targets = [], []
    for d in _out(kg, p["id"], "tmkg:has_intervention"):
        drugs.append(dict(id=d["id"], name=d.get("name")))
        for e in kg.by_subject.get(d["id"], []):
            if e["predicate"] != "biolink:interacts_with" or e["object"] not in kg.nodes:
                continue
            t = kg.nodes[e["object"]]
            genes = [kg.nodes[g["object"]].get("name") for g in kg.by_subject.get(t["id"], [])
                     if g["predicate"] == "biolink:gene_product_of" and g["object"] in kg.nodes]
            targets.append({k: v for k, v in dict(id=t["id"], name=t.get("name"), gene=genes[0] if genes else None,
                                                   mechanism=e.get("moa"), drug=d.get("name")).items() if v})
    if trials:
        ctx["trials"] = trials
    if drugs:
        ctx["drugs"] = drugs
    if targets:
        ctx["targets"] = targets
    return ctx


def build(kg, personas=None):
    """Takes the trial graph (and optionally the role list, to keep only roles that exist). Returns {persona id: [example]}
    with each example's id, persona, lens, kind, runs_as, question, entity / expect_kind for cards, program id and context
    (program_context; for the portfolio query without a program: the number of programs in the trial layer)."""
    ids = {p["id"] for p in personas} if personas else None
    out = {}
    n_programs = sum(1 for n in kg.nodes.values() if n.get("category") == "tmkg:ClinicalTrialProgram")
    for i, s in enumerate(SPEC, 1):
        if ids is not None and s["persona"] not in ids:
            continue
        ctx = program_context(kg, s["program"]) if s.get("program") else dict(programs_in_trial_layer=n_programs)
        if s.get("program") and ctx is None:
            continue                                 # a program id that is not in this trial graph is never shown
        ex = dict(id=f"x{i:02d}", persona=s["persona"], lens=s["lens"], kind=s["kind"], runs_as=RUNS_AS[s["kind"]],
                  question=s["question"], program=s.get("program"), context=ctx)
        if s["kind"] == "card":
            ex.update(entity=s["entity"], expect_kind=s.get("expect_kind"))
        out.setdefault(s["persona"], []).append(ex)
    return out


def check(example, kg, nim, card_index=None):
    """Runs one example the way the page does, offline / on the local graph. Returns {ok, n, note}: trials = number of
    verified claims (and whether the program is among the seeds), query = rows, card = rows of the expected kind (needs
    the card index; None when it is not given)."""
    if example["kind"] == "trials":
        import orchestrator
        rep = orchestrator.ask(example["question"], kg=kg, nim=nim)
        n = sum(len(s["claims"]) for s in rep["sections"])
        on_topic = not example.get("program") or example["program"] in rep.get("seeds", [])
        return dict(ok=n > 0 and on_topic, n=n, note=f"{n} verified claims; seeds {rep.get('seeds')}")
    if example["kind"] == "query":
        import querier
        r = querier.Querier(nim, kg).ask(example["question"])
        return dict(ok=bool(r["rows"]) and not r["problems"], n=len(r["rows"]), note="; ".join(r["problems"]) or f"{len(r['rows'])} rows")
    if card_index is None:
        return dict(ok=None, n=None, note="card index not available")
    hits = card_index.search(example["entity"])
    hit = next((h for h in hits if h["label"].lower() == example["entity"].lower()), hits[0] if hits else None)
    if hit is None:
        return dict(ok=False, n=0, note=f"no Pharos node named {example['entity']}")
    c = card_index.card(hit["id"])
    kinds = {k["kind"]: k["n"] for k in c["known"]}
    n = kinds.get(example["expect_kind"], 0) if example.get("expect_kind") else sum(kinds.values())
    return dict(ok=n > 0, n=n, note=f"{hit['id']}: {kinds}")


def script_json(obj):
    """JSON that is safe inside a <script> element: '<' and '>' and '&' become \\u003c, \\u003e, \\u0026 (so neither '</script>' nor '<!--' can occur),
    and the line separators U+2028 / U+2029 are escaped too. The text parses to the same value."""
    return (json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def inject(html, examples):
    """Puts the examples (JSON) into the page in place of PLACEHOLDER (script_json makes the JSON safe inside a <script> element)."""
    return html.replace(PLACEHOLDER, script_json(examples), 1)
