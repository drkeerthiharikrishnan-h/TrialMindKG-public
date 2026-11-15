"""Tests for retrieval.py (PCST), specialists.py (claims + verifier) and orchestrator.py (offline report)."""
import networkx as nx
import pytest
import retrieval, specialists, orchestrator
from kg_query import KGIndex
from nim import NIM

Q = "Why did TULIP-1 miss its primary endpoint, and what changed later?"


@pytest.fixture(scope="module")
def kg():
    return KGIndex()


@pytest.fixture(scope="module")
def G(kg):
    return retrieval.build_graph(kg)


def test_pcst_keeps_seeds_and_prunes_unprofitable_leaves():
    G = nx.Graph()
    G.add_edge("s", "a", cost=1, eid="e1")
    G.add_edge("a", "b", cost=1, eid="e2")
    G.add_edge("s", "c", cost=5, eid="e3")
    nodes, edges = retrieval.pcst(G, {"a": 0.5, "b": 5, "c": 1}, ["s"])
    assert nodes == {"s", "a", "b"} and edges == ["e1", "e2"]      # c costs more than its prize
    assert retrieval.pcst(G, {}, ["missing"]) == (set(), [])


def test_find_seeds_tulip1(kg):
    seeds = orchestrator.find_seeds(kg, Q)
    assert "P19" in seeds and "NCT:NCT02446912" in seeds


def test_subgraph_is_connected_and_deterministic(kg, G):
    seeds = orchestrator.find_seeds(kg, Q)
    a = retrieval.subgraph_for(kg, G, seeds, "failure_diagnostician")
    b = retrieval.subgraph_for(kg, G, seeds, "failure_diagnostician")
    assert a == b and a.edges and len(a.facts) == len(a.edges)
    assert all(specialists.FACT.match(f) for f in a.facts)
    assert nx.is_connected(G.edge_subgraph([(kg.edges[e]["subject"], kg.edges[e]["object"]) for e in a.edges]))


def test_verifier_rejects_outside_edges_and_invented_numbers(kg, G):
    seeds = orchestrator.find_seeds(kg, Q)
    sg = retrieval.subgraph_for(kg, G, seeds, "failure_diagnostician")
    n = NIM(backend="offline")
    outside = next(e for e in sorted(kg.edges) if e not in sg.edges)
    base = dict(evidence_status="registry_record", uncertainty="low")
    assert not specialists.verify_claim(n, kg, dict(base, text="x", evidence_edges=[]), sg)["ok"]
    assert not specialists.verify_claim(n, kg, dict(base, text="x", evidence_edges=[outside]), sg)["ok"]
    bad = specialists.verify_claim(n, kg, dict(base, text="The effect was 97.31 points", evidence_edges=sg.edges[:1]), sg)
    assert not bad["ok"] and any("numbers" in r for r in bad["reasons"])


def test_offline_report_cites_only_subgraph_edges(kg):
    r1 = orchestrator.ask(Q, kg=kg, nim=NIM(backend="offline"))
    r2 = orchestrator.ask(Q, kg=kg, nim=NIM(backend="offline"))
    r1["run"].pop("seconds"); r2["run"].pop("seconds")
    assert r1 == r2
    fd = next(s for s in r1["sections"] if s["role"] == "failure_diagnostician")
    assert fd["claims"] and any("0.412" in c["claim"] for c in fd["claims"])
    for s in r1["sections"]:
        for c in s["claims"]:
            assert c["evidence"] and all(e["id"] in kg.edges and e["source"] for e in c["evidence"])
    md = orchestrator.to_markdown(r1)
    assert "offline stand-in" in md and "Failure Diagnosis" in md


def test_focused_roles_stay_on_the_question_trial(kg, G):
    seeds = orchestrator.find_seeds(kg, "Did a genotype-defined population rescue dalcetrapib in dal-GenE?")
    sg = retrieval.subgraph_for(kg, G, seeds, "failure_diagnostician")
    trials = {n for n in sg.nodes if n.startswith("NCT:")}
    assert trials == {"NCT:NCT02525939"}


def test_trial_label_falls_back_to_curated_trial_name(kg):
    assert retrieval.label(kg.nodes["NCT:NCT02105961"], kg) == "METREO (NCT02105961)"


def test_unusable_verifier_output_rejects_the_claim(kg, G, monkeypatch):
    import nim as nimmod
    seeds = orchestrator.find_seeds(kg, Q)
    sg = retrieval.subgraph_for(kg, G, seeds, "failure_diagnostician")
    monkeypatch.setitem(nimmod._HANDLERS, "verifier", lambda m, s: '{"supported": true, "reason": "unfinished\x00')
    claim = dict(text="trial record", evidence_edges=sg.edges[:1], evidence_status="registry_record", uncertainty="low")
    v = specialists.verify_claim(NIM(backend="offline"), kg, claim, sg)
    assert not v["ok"] and "no usable answer" in v["reasons"][0]


def test_rejected_claim_is_repaired_once_and_rechecked(kg, G, monkeypatch):
    """A claim with a wrong number is rewritten once from the same facts; the rewrite passes the same checks."""
    import json, nim as nimmod
    seeds = orchestrator.find_seeds(kg, Q)
    sg = retrieval.subgraph_for(kg, G, seeds, "failure_diagnostician")
    e = sg.edges[0]
    bad = dict(text="Primary result 123456.7", evidence_edges=[e], evidence_status="registry_record",
               uncertainty="low", role="failure_diagnostician")
    n = NIM(backend="offline")
    v = specialists.verify_claim(n, kg, bad, sg)
    assert not v["ok"] and "123456.7" in v["reasons"][0]
    fixed_text = sg.facts[0].split("] ", 1)[1][:200]
    monkeypatch.setitem(nimmod._HANDLERS, "repair", lambda m, s: json.dumps({"claims": [dict(
        text=fixed_text, evidence_edges=[e], evidence_status="registry_record", uncertainty="low")]}))
    r = specialists.repair_claim(n, bad, v["reasons"], sg)
    assert r["repaired_from"] == bad["text"] and r["role"] == "failure_diagnostician"
    # malformed-verifier rejections are not repaired; offline stand-in withdraws claims
    assert specialists.repair_claim(n, bad, ["verifier gave no usable answer (malformed model output)"], sg) is None
    monkeypatch.delitem(nimmod._HANDLERS, "repair")
    monkeypatch.setitem(nimmod._HANDLERS, "repair", specialists._offline_repair)
    assert specialists.repair_claim(n, bad, v["reasons"], sg) is None


def test_verifier_is_asked_again_after_a_malformed_reply(kg, G, monkeypatch):
    import nim as nimmod
    seeds = orchestrator.find_seeds(kg, Q)
    sg = retrieval.subgraph_for(kg, G, seeds, "failure_diagnostician")
    replies = iter(['{"supported": tru', '{"supported": true, "reason": "ok"}'])
    monkeypatch.setitem(nimmod._HANDLERS, "verifier", lambda m, s: next(replies))
    claim = dict(text="trial record", evidence_edges=sg.edges[:1], evidence_status="registry_record", uncertainty="low")
    assert specialists.verify_claim(NIM(backend="offline"), kg, claim, sg)["ok"]


def test_clean_ids_and_absence_statements():
    assert specialists.clean_ids(["/tmkg/edge/[ed9d87b62dc6abcb8]", "e0123456789abcdef", "junk"]) == \
        ["ed9d87b62dc6abcb8", "e0123456789abcdef"]
    assert specialists.ABSENCE.search("no further claims about post-failure events can be made from the provided facts")
    assert not specialists.ABSENCE.search("TULIP-1 missed its primary endpoint")


def test_convention_numbers_only_with_flag(kg):
    import kg_query
    e = next(iter(kg.edges))
    text = f"The CI crossed 1.0 and p was above 0.05 [{e}]"
    assert kg_query.verify_answer(text, kg)["unsupported_numbers"]
    assert kg_query.verify_answer(text, kg, conventions=True)["unsupported_numbers"] == []
