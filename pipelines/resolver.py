"""
Resolver agent: free-text drug name -> ranked ChEMBL candidates (NVIDIA embedding model + reranker).

    python pipelines/resolver.py propose     # ranked proposals for every unresolved name in the review queue
    python pipelines/resolver.py evaluate    # leave-one-out accuracy on ChEMBL research codes / trade names

How it decides (CLAUDE.md rule 3 - never guess):
  1. exact match on a ChEMBL preferred name or synonym  -> accepted (same rule the graph builder uses)
  2. otherwise four channels propose candidates:
       external - PubChem / FDA GSRS / RENCI name lists (drug_names.py) give other names of the same substance;
                  a molecule counts only if one of them is EXACTLY its ChEMBL name/synonym (ranked first)
       namer    - the language model names the generic ingredient(s) of a code / trade name (suggest_names); a
                  suggestion counts only if it is EXACTLY a ChEMBL name/synonym in the index (no invented ids)
       lexical  - molecules sharing a rare whole word with the query (e.g. 'Broncho saline' ~ synonym 'Saline')
       semantic - embedding search over the candidate pool
     external hits first, then namer hits; the reranker orders the union of the lexical and semantic top 10
     -> written to outputs/resolver_proposals.csv as PROPOSED; nothing enters the graph until a curator
        copies the chosen row into data/curated/drug_overrides.csv (registry_name, chembl_id, justification).

Functions
- build_index(dirs)                    -> [dict(chembl_id, pref_name, synonyms, syn_types, molecule_type, max_phase)]
                                          from every ChEMBL molecule in the frozen API responses
- passage(mol, exclude=())             -> text the embedding/reranker models see for a molecule
- Resolver(nim, index).resolve(name)   -> ranked candidates [dict(rank, chembl_id, pref_name, score, method)]
- suggest_names(nim, names)            -> {name: [generic names]} from the 'namer' model (batched, frozen)
- evaluate(nim, index, syn_types)      -> accuracy report (top-1, top-3, MRR) per synonym type and per channel;
                                          top1_same_active_ingredient also accepts a salt / parent form of the
                                          molecule (ChEMBL molecule_hierarchy), as the graph builder does
- propose(nim, index)                  -> rows written to outputs/resolver_proposals.csv
"""
import csv, gzip, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nim as nimmod

ROOT = Path(__file__).resolve().parents[1]
SNAP_DIRS = [ROOT / "data" / "snapshots" / "chembl", ROOT / "data" / "snapshots_adc_demo" / "chembl"]
PROPOSALS = ROOT / "outputs" / "resolver_proposals.csv"


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def build_index(dirs=SNAP_DIRS):
    mols = {}
    for d in dirs:
        for f in sorted(Path(d).glob("*.json.gz")):
            body = json.loads(gzip.decompress(f.read_bytes()))["body"]
            for m in (body or {}).get("molecules", []) if isinstance(body, dict) else []:
                mols.setdefault(m["molecule_chembl_id"], m)
    out = []
    for cid in sorted(mols):
        m = mols[cid]
        syns = sorted({s["molecule_synonym"] for s in m.get("molecule_synonyms", []) if s.get("molecule_synonym")})
        types = {}
        for s in m.get("molecule_synonyms", []):
            types.setdefault(s.get("molecule_synonym"), set()).add(s.get("syn_type"))
        out.append(dict(chembl_id=cid, pref_name=m.get("pref_name") or "", synonyms=syns,
                        syn_types={k: sorted(v) for k, v in types.items()},
                        molecule_type=m.get("molecule_type") or "", max_phase=m.get("max_phase"),
                        parent=(m.get("molecule_hierarchy") or {}).get("parent_chembl_id") or cid))
    return out


def passage(mol, exclude=()):
    """'PREF NAME | synonyms: a; b | type: Antibody | max phase: 4.0' with excluded names removed (by norm)."""
    ex = {norm(x) for x in exclude}
    syns = [s for s in mol["synonyms"] if norm(s) not in ex]
    name = mol["pref_name"] if norm(mol["pref_name"]) not in ex else ""
    return (f"{name or mol['chembl_id']} | synonyms: {'; '.join(syns) or '-'} | "
            f"type: {mol['molecule_type'] or '-'} | max phase: {mol['max_phase'] or '-'}")


NAMER_SYSTEM = ("For each numbered drug research code or trade name, give the generic (INN/USAN) name(s) of its active "
                "ingredient(s) if you know them. Answer each line on its own - do not carry an answer over from "
                "another line. Use an empty list when you do not know.")
NAMER_SCHEMA = {"type": "object", "required": ["answers"], "properties": {"answers": {"type": "array", "items": {
    "type": "object", "required": ["n", "query", "generic_names"], "properties": {
        "n": {"type": "integer"}, "query": {"type": "string"},
        "generic_names": {"type": "array", "items": {"type": "string"}}}}}}}
STOP = {"soln", "solution", "component", "of", "in", "and", "with", "for", "injection", "tablet", "tablets", "oral",
        "sodium", "hydrochloride", "chloride", "the", "mg", "kit"}     # salt / form words say nothing about identity


def suggest_names(nim, names, batch=10):
    """{name: [generic names]} for each name. Offline backend: no language model -> {} (channel unused)."""
    out = {}
    names = sorted(set(names))
    for i in range(0, len(names), batch):
        chunk = names[i:i + batch]
        try:
            r = nim.chat("namer", [{"role": "system", "content": NAMER_SYSTEM},
                                   {"role": "user", "content": "\n".join(f"{k + 1}. {n}" for k, n in enumerate(chunk))}],
                         schema=NAMER_SCHEMA)
        except (nimmod.OfflineUnavailable, ValueError):
            continue
        for a in r.data["answers"]:
            k = a.get("n", 0) - 1
            if 0 <= k < len(chunk) and norm(a.get("query")) == norm(chunk[k]):     # answer belongs to that line
                out[chunk[k]] = [g for g in a.get("generic_names") or [] if norm(g)]
    return out


def words(s):
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) >= 4 and w not in STOP}


class Resolver:
    def __init__(self, nim, index, k=10, dictionary=None):
        """dictionary: optional full ChEMBL name table (chembl_synonyms.load()); exact lookups and the model's
        suggested generic names are then checked against ALL of ChEMBL, not only the index."""
        self.nim, self.index, self.k = nim, index, k
        self.names, self.dict_names, self.dict_mol = {}, {}, {}
        for m in index:
            for n in [m["pref_name"]] + m["synonyms"]:
                if norm(n):
                    self.names.setdefault(norm(n), set()).add(m["chembl_id"])
        for m in dictionary or []:
            self.dict_mol[m["chembl_id"]] = m
            for n in [m["pref_name"]] + m["synonyms"]:
                if norm(n):
                    self.dict_names.setdefault(norm(n), set()).add(m["chembl_id"])
        self._pvec = None

    def _passage_vectors(self):
        if self._pvec is None:
            self._pvec = self.nim.embed([passage(m) for m in self.index], input_type="passage")
        return self._pvec

    def exact(self, name):
        return sorted(self.names.get(norm(name)) or self.dict_names.get(norm(name)) or set())

    def dictionary_named(self, name, suggestions, skip=()):
        """ChEMBL ids (full table) named exactly by a suggestion; preferred-name hits and parent molecules first."""
        want = {norm(g) for g in suggestions or []} - {norm(name)}
        ids = {i for w in want for i in self.dict_names.get(w, ())} - set(skip)
        key = lambda i: (norm(self.dict_mol[i]["pref_name"]) not in want, self.dict_mol[i]["parent"] != i, i)
        return sorted(ids, key=key)

    def lexical(self, name, pool):
        """Pool indexes of molecules sharing a whole word (>= 4 letters, not a salt/form word) with name, best
        first: score = summed rarity (1/number of molecules using the word) of the shared words."""
        q = words(name)
        if not q:
            return []
        mw = [set().union(*[words(n) for n in [m["pref_name"]] + m["synonyms"]]) for m in pool]
        df = {}
        for ws in mw:
            for w in ws & q:
                df[w] = df.get(w, 0) + 1
        sc = [(sum(1 / df[w] for w in ws & q), i) for i, ws in enumerate(mw) if ws & q]
        return [i for _, i in sorted(sc, key=lambda x: (-x[0], x[1]))[:self.k]]

    def named(self, name, suggestions, pool):
        """Pool indexes whose name/synonym is exactly one of the model's suggested generic names."""
        want = {norm(g) for g in suggestions or []} - {norm(name)}      # repeating the query is not an answer
        return [i for i, m in enumerate(pool) if want & {norm(n) for n in [m["pref_name"]] + m["synonyms"]}]

    def external_named(self, name, external, pool):
        """Molecules (pool first, then the full ChEMBL table) named exactly by an external dictionary name.
        external = drug_names.other_names() output. -> [(molecule dict, n names that hit, sources)] best first."""
        hits = {}
        q = norm(name)
        for src, names in (external or {}).items():
            for n in names:
                k = norm(n)
                if not k or k == q:
                    continue
                ids = {m["chembl_id"] for m in pool if k in {norm(x) for x in [m["pref_name"]] + m["synonyms"]}} \
                    if len(pool) < 5000 else set()
                ids |= self.dict_names.get(k, set())
                for cid in ids:
                    h = hits.setdefault(cid, [0, set(), False])
                    h[0] += 1
                    h[1].add(src)
        mol = {m["chembl_id"]: m for m in pool}
        mol = {**self.dict_mol, **mol}
        for cid, h in hits.items():
            h[2] = norm(mol[cid]["pref_name"]) in {norm(n) for ns in external.values() for n in ns}
        best = sorted(hits, key=lambda c: (-len(hits[c][1]), not hits[c][2], -hits[c][0],
                                           mol[c].get("parent", c) != c, c))
        return [(mol[c], hits[c][0], sorted(hits[c][1])) for c in best]

    def rank(self, name, pool_vectors=None, pool=None, suggestions=None, external=None):
        """namer hits first (method 'namer+exact'), then the reranked union of lexical + embedding top-k.
        pool/pool_vectors default to the whole index; suggestions = suggest_names() output for this name."""
        pool = pool or self.index
        pv = pool_vectors or self._passage_vectors()
        q = self.nim.embed([name], input_type="query")[0]
        sims = sorted(((nimmod.cosine(q, v), i) for i, v in enumerate(pv)), key=lambda x: (-x[0], x[1]))[:self.k]
        named = self.named(name, suggestions, pool)
        lex = self.lexical(name, pool)
        idx = list(dict.fromkeys([i for _, i in sims] + lex))
        idx = [i for i in idx if i not in named]
        texts = [passage(pool[i]) for i in idx]
        order = self.nim.rerank(name, texts) if texts else []
        be = self.nim.backend if self.nim.backend == "nim" else nimmod.OFFLINE
        out = [dict(chembl_id=m["chembl_id"], pref_name=m["pref_name"], score=1.0, passage=passage(m),
                    method=f"external+exact ({'/'.join(src)}: {k} names)") for m, k, src in
               self.external_named(name, external, pool)]
        seen = {c["chembl_id"] for c in out}
        out += [dict(chembl_id=pool[i]["chembl_id"], pref_name=pool[i]["pref_name"], score=1.0,
                     method=f"namer+exact ({be}: {', '.join(suggestions)})", passage=passage(pool[i]))
                for i in named if pool[i]["chembl_id"] not in seen]
        for cid in self.dictionary_named(name, suggestions, skip={c["chembl_id"] for c in out}):
            m = self.dict_mol[cid]
            out.append(dict(chembl_id=cid, pref_name=m["pref_name"], score=1.0, passage=passage(m),
                            method=f"namer+chembl_dictionary ({be}: {', '.join(suggestions)})"))
        seen = {c["chembl_id"] for c in out}
        order = [(j, sc) for j, sc in order if pool[idx[j]]["chembl_id"] not in seen]
        for j, sc in order:
            i = idx[j]
            ch = "+".join(c for c, ok in (("embedding", i in {x for _, x in sims}), ("lexical", i in lex)) if ok)
            out.append(dict(chembl_id=pool[i]["chembl_id"], pref_name=pool[i]["pref_name"], score=round(sc, 6),
                            method=f"{ch}+rerank ({be})", passage=texts[j]))
        return [dict(rank=r + 1, **c) for r, c in enumerate(out)]

    def resolve(self, name, suggestions=None, external=None):
        hits = self.exact(name)
        if hits:
            return [dict(rank=i + 1, chembl_id=c, pref_name=next(m["pref_name"] for m in self.index
                                                                    if m["chembl_id"] == c),
                         score=1.0, method="exact name/synonym", passage="") for i, c in enumerate(hits)]
        return self.rank(name, suggestions=suggestions if suggestions is not None
                         else suggest_names(self.nim, [name]).get(name), external=external)


def eval_cases(index, syn_types=("RESEARCH_CODE", "TRADE_NAME")):
    """Leave-one-out cases from ChEMBL's own synonyms: (query synonym, molecule, all molecules sharing it)."""
    owners = {}
    for m in index:
        for n in [m["pref_name"]] + m["synonyms"]:
            owners.setdefault(norm(n), set()).add(m["chembl_id"])
    cases, seen = [], set()
    for m in index:
        for s in m["synonyms"]:
            t = set(m["syn_types"].get(s, []))
            if t & set(syn_types) and (m["chembl_id"], norm(s)) not in seen and norm(s) != norm(m["pref_name"]):
                seen.add((m["chembl_id"], norm(s)))
                cases.append(dict(query=s, syn_type=sorted(t & set(syn_types))[0], target=m["chembl_id"],
                                  accept=sorted(owners[norm(s)])))
    return cases


def evaluate(nim, index, syn_types=("RESEARCH_CODE", "TRADE_NAME"), limit=None, dictionary=None, snap=None):
    """snap: a snapshot.Snapshot to use the external name dictionaries (drug_names.py); None = channel unused."""
    """For each case the molecule's own passage is rebuilt WITHOUT the queried synonym (no answer leakage),
    then the resolver ranks the whole index. Returns dict(cases, top1, top3, mrr, by_type, rows)."""
    res = Resolver(nim, index, dictionary=dictionary)
    base = res._passage_vectors()
    cases = eval_cases(index, syn_types)[:limit]
    pos = {m["chembl_id"]: i for i, m in enumerate(index)}
    held = nim.embed([passage(index[pos[c["target"]]], exclude=[c["query"]]) for c in cases], input_type="passage")
    sugg = suggest_names(nim, [c["query"] for c in cases])
    ext = {}
    if snap is not None:
        import drug_names
        ext = {q: drug_names.other_names(snap, q) for q in sorted({c["query"] for c in cases})}
    parent = {m["chembl_id"]: m["parent"] for m in index}           # salt / hydrate -> parent molecule
    rows = []
    for c, hv in zip(cases, held):
        pool = list(index)
        vecs = list(base)
        i = pos[c["target"]]
        pool[i] = dict(index[i], synonyms=[s for s in index[i]["synonyms"] if norm(s) != norm(c["query"])])
        vecs[i] = hv
        ranked = res.rank(c["query"], pool_vectors=vecs, pool=pool, suggestions=sugg.get(c["query"]),
                          external=ext.get(c["query"]))
        hit = next((r["rank"] for r in ranked if r["chembl_id"] in c["accept"]), None)
        fam = {parent[a] for a in c["accept"]}
        hit_moiety = next((r["rank"] for r in ranked if parent.get(r["chembl_id"]) in fam), None)
        rows.append(dict(c, hit_rank=hit, hit_rank_moiety=hit_moiety, top1=ranked[0]["chembl_id"] if ranked else "",
                         top1_method=ranked[0]["method"].split(" ")[0] if ranked else "",
                         suggested=sugg.get(c["query"], [])))
    def stats(rs):
        n = len(rs) or 1
        return dict(n=len(rs), top1=round(sum(r["hit_rank"] == 1 for r in rs) / n, 3),
                    top3=round(sum(bool(r["hit_rank"]) and r["hit_rank"] <= 3 for r in rs) / n, 3),
                    mrr=round(sum(1 / r["hit_rank"] for r in rs if r["hit_rank"]) / n, 3),
                    top1_same_active_ingredient=round(sum(r["hit_rank_moiety"] == 1 for r in rs) / n, 3))
    by = {t: stats([r for r in rows if r["syn_type"] == t]) for t in syn_types}
    ch = {}
    for r in rows:
        c = ch.setdefault(r["top1_method"] or "none", dict(n=0, correct=0))
        c["n"] += 1
        c["correct"] += r["hit_rank"] == 1
    for c in ch.values():
        c["precision"] = round(c["correct"] / c["n"], 3)
    return dict(backend=nim.backend, **stats(rows), by_type=by, by_top1_channel=dict(sorted(ch.items())),
                dictionary=f"{len(res.dict_mol)} ChEMBL molecules" if dictionary else "none",
                external_dictionaries="PubChem, FDA GSRS, RENCI NameRes" if snap is not None else "none", rows=rows)


def unresolved_names(report=ROOT / "outputs" / "resolution_report.csv"):
    """(program, name, detail) for every drug name the builder could not match exactly."""
    out = []
    for r in csv.DictReader(open(report, encoding="utf-8")):
        if r["entity_type"] in ("trial_intervention", "drug") and r["rule"].endswith("NEEDS_REVIEW"):
            out.append((r["program"], r["query"], r.get("detail") or ""))
    return out


def propose(nim, index, path=PROPOSALS, top=3):
    import chembl_synonyms
    res = Resolver(nim, index, dictionary=chembl_synonyms.load()[1])
    rows = []
    todo = unresolved_names()
    sugg = suggest_names(nim, [n for _, n, _ in todo])
    import drug_names
    snap = drug_names.snapshot()
    for pid, name, detail in todo:
        for c in res.rank(name, suggestions=sugg.get(name), external=drug_names.other_names(snap, name))[:top]:
            rows.append(dict(program=pid, registry_name=name, context=detail, rank=c["rank"], chembl_id=c["chembl_id"],
                             pref_name=c["pref_name"], score=c["score"], method=c["method"],
                             status="PROPOSED - confirm by copying to data/curated/drug_overrides.csv"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, list(rows[0]) if rows else ["program"], lineterminator="\n")
        w.writeheader(); w.writerows(rows)
    return rows


if __name__ == "__main__":
    n, idx = nimmod.NIM(), build_index()
    if sys.argv[1:] == ["evaluate"]:
        import chembl_synonyms
        import drug_names
        r = evaluate(n, idx, dictionary=chembl_synonyms.load()[1], snap=drug_names.snapshot())
        print(json.dumps({k: v for k, v in r.items() if k != "rows"}, indent=2))
        out = ROOT / "outputs" / "eval" / f"resolver_{r['backend']}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(r, indent=1))
    else:
        rows = propose(n, idx)
        print(f"{len(rows)} proposals -> {PROPOSALS} (backend: {n.backend})")
