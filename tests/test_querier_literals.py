"""CC-31c (review finding M7): string literals compared with an enumerated relationship property (primary_knowledge_source,
knowledge_level, source, verdict, layer) must be values the graph holds; the validator says which values exist and the
model gets them back, as it does for unknown columns. The value sets come from the graph (PharosGraph, the card index meta)."""
import json
import sys

import pandas as pd
import pytest

sys.path.insert(0, "pipelines")
import querier  # noqa: E402
from pharos_app import PharosGraph  # noqa: E402
from pharos_index import CardIndex, build_index, refresh_value_sets  # noqa: E402
from fixtures.pharos_fixture import build  # noqa: E402


@pytest.fixture(scope="module")
def graphs(tmp_path_factory):
    root = build(tmp_path_factory.mktemp("lit"))
    g = PharosGraph(root=root)
    ci = CardIndex(build_index(root, chunk=3, log=lambda *_: None)["path"])
    return root, g, ci


def test_value_sets_are_read_from_the_graph_not_written_here(graphs):
    _, g, ci = graphs
    live = g.schema_card()["rel_enums"]
    assert "JensenLab Text Mining" in live["source"] and "knowledge_assertion" in live["knowledge_level"]
    assert live["layer"] == ["opentargets_layer", "pharos"]
    assert ci.graph().schema_card()["rel_enums"] == live                    # the index stores the same sets at build time


def test_add_value_sets_stops_enumerating_a_property_with_too_many_values():
    acc = querier.add_value_sets({}, pd.DataFrame({"source": [f"s{i}" for i in range(querier.REL_ENUM_MAX + 1)],
                                                   "knowledge_level": ["knowledge_assertion"] * (querier.REL_ENUM_MAX + 1)}))
    assert acc["source"] is None and querier.finish_value_sets(acc) == {"knowledge_level": ["knowledge_assertion"]}
    assert querier.add_value_sets({}, [{"source": "A"}, {"source": ""}, {"layer": "x"}]) == {"source": {"A"}, "layer": {"x"}}


@pytest.mark.parametrize("where, bad", [
    ("r.primary_knowledge_source <> 'text mining'", "text mining"),          # the review's case
    ("r.knowledge_level = 'curated'", "curated"),
    ("r.source IN ['CTD', 'TIN-X']", "TIN-X"),
])
def test_a_literal_the_graph_does_not_hold_is_rejected_with_the_allowed_values(graphs, where, bad):
    _, g, _ = graphs
    cy = (f"MATCH (x:Gene {{name:'PTGS2'}})-[r:gene_associated_with_condition]->(d:Disease) WHERE {where} "
          "RETURN d.name AS disease, r.id AS evidence LIMIT 10")
    probs = querier.validate(cy, g.schema_card())
    assert len(probs) == 1 and f"has no value {bad!r}" in probs[0] and "allowed:" in probs[0]
    assert "Compare a property only with one of the allowed values" in querier.fix_hints(probs)


def test_values_the_graph_holds_pass_and_did_you_mean_is_offered(graphs):
    _, g, _ = graphs
    card = g.schema_card()
    ok = ("MATCH (x:Gene {name:'PTGS2'})-[r:gene_associated_with_condition]->(d:Disease) "
          "WHERE r.knowledge_level = 'knowledge_assertion' AND r.source <> 'CTD' RETURN d.name AS disease, r.id AS evidence LIMIT 10")
    assert querier.validate(ok, card) == []
    near = querier.literal_problem("r", "source", "jensenlab", ["CTD", "JensenLab Text Mining"])
    assert "did you mean 'JensenLab Text Mining'?" in near


def test_the_prompt_lists_the_values_and_the_text_mining_hint_only_when_the_graph_has_it(graphs):
    _, g, _ = graphs
    card = dict(g.schema_card())
    text = querier.card_text(card)
    assert "RELATIONSHIP PROPERTY VALUES" in text and "'JensenLab Text Mining'" in text
    assert "text_co_occurrence" not in text                                 # the fixture has no text-mined level
    card["rel_enums"] = dict(card["rel_enums"], knowledge_level=["knowledge_assertion", "text_co_occurrence"])
    assert "to leave text mining out write r.knowledge_level <> 'text_co_occurrence'" in querier.card_text(card)


def test_refresh_value_sets_writes_the_meta_of_an_existing_index(graphs, tmp_path):
    root, g, ci = graphs
    import shutil, sqlite3
    path = tmp_path / "old.sqlite"
    shutil.copy(ci.path, path)
    con = sqlite3.connect(path)
    con.execute("DELETE FROM meta WHERE key = 'rel_values'")
    con.commit()
    con.close()
    assert CardIndex(path).graph().schema_card()["rel_enums"] == {}              # an index built before CC-31c
    out = refresh_value_sets(path, root, chunk=3)
    assert CardIndex(path).graph().schema_card()["rel_enums"] == out == g.schema_card()["rel_enums"]
