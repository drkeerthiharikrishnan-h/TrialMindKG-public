"""Tests for pipelines/curated_tables.py and their linking into the graph."""
import pandas as pd
import curated_tables as ct
import seed_kg as sk

MD = open(sk.DATASET).read()


def test_row_counts_and_links():
    pf, rp, da = ct.post_failure_rows(MD), ct.repurposing_rows(MD), ct.data_access_rows(MD, sk.expand_trial_tokens)
    assert (len(pf), len(rp), len(da)) == (8, 3, 7)
    assert pf[0]["key"] == ["Anifrolumab"] and "BICLA" in pf[0]["props"]["pivot_decision"]
    assert len(pf[0]["urls"]) == 2                       # row has no links; both come from its Sources line
    assert da[0]["key"] == ["GRADUATE I", "GRADUATE II"]
    assert rp[0]["section_urls"]                        # repurposing sources are section-level only


def test_strip_links():
    assert ct.strip_links("see [paper](https://x.org/1) now") == ("see paper now", ["https://x.org/1"])


def test_every_curated_row_linked_or_flagged():
    e = pd.read_csv(sk.OUT / "kg_edges.csv", low_memory=False)
    linked = set(e[e.object.str.startswith("DEV:")].object)
    r = pd.read_csv(sk.OUT / "resolution_report.csv")
    flagged = set(r[r.entity_type == "curated_event"]["query"].str.split(" ").str[0])
    assert len(linked | flagged) == 18
