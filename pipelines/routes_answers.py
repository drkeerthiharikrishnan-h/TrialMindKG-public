"""
Read-only endpoints for the truth of what an answer and the first screen say (packet PK2; review findings U5 and U14).
Its own router, included by pipelines/app.py with one line, so parallel branches do not meet in app.py.

  GET /api/graph_numbers            the verdict counts of the first-screen sentence, each labelled with what it counts:
                                    the Pharos links (every one has a keep-or-prune verdict) and, separately, all layers
                                    of the agent-built graph. Read from the committed pruning report and graph summaries
                                    (the files the headline is read from), not from the loaded graph.
  GET /api/pharos/tested?question=  which tested question, if any, has exactly this text (case, spacing and the final
                                    '?' ignored): the page can then open /api/pharos/demo/<id> for a pill or a typed
                                    question, so one question never has two answers. Nothing is run.

Functions
  graph_numbers(root) -> {pharos: {links, kept, pruned, pruned_percent}, all_layers: {links, kept, pruned, pruned_percent},
                          added_layers, sentence}; a block whose file is missing is None and is left out of the sentence.
                          No file names and no layer codes in the answer (reader names only).
  added_layers(root)  -> the layers added to Pharos, by reader name (Pharos itself is the seed, not one of them)
  write_graph_totals(root) -> writes outputs/graph_totals.json from the G2 / G3 summaries (the hosted image does not carry
                          outputs/g2 and outputs/g3, but it does carry outputs/); graph_numbers reads it when they are absent
  tested(question)    -> {tested: bool, id, question}
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fastapi import APIRouter

ROOT = Path(__file__).resolve().parents[1]
TOTALS = "outputs/graph_totals.json"       # shipped copy of the G2 / G3 summary numbers (outputs/g2|g3 are not in the image)
router = APIRouter()


def graph_numbers(root=ROOT):
    """Takes the repo root. Returns the verdict counts for the first screen (module docstring). Every number is read by
    headline.prune_numbers from a committed file; kept = links - pruned, the percentages are computed here. The sentence
    is built from the numbers that are there (rule 7): the Pharos links first, then the all-layer totals, labelled."""
    import headline
    p = headline.prune_numbers(root)
    if not (p.get("g2_edges") and p.get("g3_removed") is not None):       # the server: read the shipped totals file
        p.update({k: v for k, v in _read_totals(root).items() if p.get(k) is None})
    pharos = all_layers = None
    if p.get("scored") and p.get("pruned") is not None:
        pharos = dict(links=p["scored"], kept=p["scored"] - p["pruned"], pruned=p["pruned"],
                      pruned_percent=round(100.0 * p["pruned"] / p["scored"], 2))
    if p.get("g2_edges") and p.get("g3_removed") is not None:
        all_layers = dict(links=p["g2_edges"], kept=p["g2_edges"] - p["g3_removed"], pruned=p["g3_removed"],
                          pruned_percent=round(100.0 * p["g3_removed"] / p["g2_edges"], 2))
    parts = []
    if pharos:
        parts.append(f"Every one of the {pharos['links']:,} Pharos links has a keep-or-prune verdict: {pharos['kept']:,} kept and "
                     f"{pharos['pruned']:,} pruned ({pharos['pruned_percent']:.2f}%).")
    if all_layers:
        parts.append(f"Across all layers of the graph (Pharos and the sources added to it), {all_layers['kept']:,} of "
                     f"{all_layers['links']:,} links are kept and {all_layers['pruned']:,} pruned ({all_layers['pruned_percent']:.2f}%).")
    return dict(pharos=pharos, all_layers=all_layers, added_layers=added_layers(root), sentence=" ".join(parts))


def _read_totals(root):
    """Takes the repo root. Returns {g2_edges, g3_edges, g3_removed, layers} from outputs/graph_totals.json, or {}."""
    import json
    try:
        d = json.loads((Path(root) / TOTALS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: d[k] for k in ("g2_edges", "g3_edges", "g3_removed", "layers") if k in d}


def _layer_codes(root):
    """Takes the repo root. Returns the layer codes of the agent-built graph, every one but 'pharos' (the seed, not an
    enrichment layer, U14): from the heading of outputs/g2/summary.md ('# G2 (pharos + dto + ...)'), else from the shipped
    totals file; [] when neither is there."""
    import re
    try:
        head = (Path(root) / "outputs" / "g2" / "summary.md").read_text(encoding="utf-8").splitlines()[0]
        m = re.search(r"\(([^)]*)\)", head)
        codes = [x.strip() for x in m.group(1).split("+")] if m else []
    except (OSError, IndexError):
        codes = _read_totals(root).get("layers") or []
    return [x for x in codes if x and x != "pharos"]


def added_layers(root=ROOT):
    """Takes the repo root. Returns the layers the agent added to Pharos as reader names (routes_agent.LAYER_NAMES, e.g.
    'Open Targets'), never the layer codes. [] when neither the G2 summary nor the totals file is there."""
    from routes_agent import _name
    return list(dict.fromkeys(_name(c) for c in _layer_codes(root)))


def write_graph_totals(root=ROOT):
    """Takes the repo root. Writes outputs/graph_totals.json from outputs/g2|g3/summary.md (numbers read by
    headline.prune_numbers, layer codes from the G2 heading) and returns the dict; run it after a graph rebuild."""
    import json, headline
    p = headline.prune_numbers(root)
    d = dict(g2_edges=p["g2_edges"], g3_edges=p["g3_edges"], g3_removed=p["g3_removed"],
             layers=["pharos"] + _layer_codes(root), source="outputs/g2/summary.md, outputs/g3/summary.md")
    (Path(root) / TOTALS).write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8")
    return d


def tested(question):
    """Takes a question. Returns {tested, id, question}: the tested question with the same text (pharos_app.tested_question),
    or tested False with id and question None."""
    import pharos_app
    d = pharos_app.tested_question(question)
    return dict(tested=d is not None, id=d["id"] if d else None, question=d["question"] if d else None)


@router.get("/api/graph_numbers")
def api_graph_numbers():
    return graph_numbers(ROOT)


@router.get("/api/pharos/tested")
def api_pharos_tested(question: str = ""):
    return tested(question)
