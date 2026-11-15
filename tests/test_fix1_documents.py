"""FIX1 F11 (gate 1: G1-ES-02, G1-ES-03, G1-ES-04, G1-ES-06, G1-AC-07, G1-AC-10): documents say what is true now."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
import headline  # noqa: E402
import schema_card  # noqa: E402

DOCS = {p: (ROOT / p).read_text(encoding="utf-8") for p in ("README.md", "docs/SUBMISSION.md", "docs/LIMITATIONS.md",
                                                           "docs/AGENT_LOOP.md", "docs/SCHEMA_CARD.md")}


def test_the_agent_paragraph_has_the_deployed_summary_and_audit_clauses():
    assert {"PK4", "PK8", "PK9", "PK11"} <= headline.AGENT_SHIPPED
    text = headline.agent_statement()
    assert "summarises the evidence behind each answer" in text and "an auditing step checks edges" in text
    for p in ("README.md", "docs/SUBMISSION.md", "docs/AGENT_LOOP.md"):
        assert text in DOCS[p], p                       # regenerated with `python pipelines/headline.py --readme`
    assert headline.write_blocks.__doc__ and not [r for r, t in headline.expected(ROOT).items() if (ROOT / r).read_text() != t]


def test_no_document_says_every_gene_node_is_protein_coding():
    for p in ("README.md", "docs/SCHEMA_CARD.md"):
        assert "every Pharos gene node" not in DOCS[p] and "every Pharos Gene node carries" not in DOCS[p], p
    rep = schema_card._gene_report(schema_card.export_reports(ROOT))[1]["nodes"]
    measured = f"{rep['gene_types']['SO:0001217']:,} of the {rep['genes']:,} Gene nodes carry the more specific type `SO:0001217`"
    assert measured in DOCS["docs/SCHEMA_CARD.md"]
    assert f"`type` of the {rep['gene_types']['SO:0001217']:,}\nprotein-coding Gene nodes of the {rep['genes']:,}" in DOCS["README.md"]


def test_the_class_written_at_export_is_counted():
    card = json.loads((ROOT / "outputs" / "schema_card.json").read_text())
    names = [c["name"] for c in card["classes"]]
    assert "ChemicalEntity" in names and len(names) == 12
    ce = next(c for c in card["classes"] if c["name"] == "ChemicalEntity")
    assert ce["at_export"] is True and ce["nodes"] == {"dto": 17} and "CHEBI:24431" in ce["exact_mappings"]
    assert "12 node classes and 13 predicates" in DOCS["docs/SCHEMA_CARD.md"]
    assert schema_card.check(ROOT) == 0                  # `make schema`: the committed card equals a fresh build


def test_export_classes_needs_a_measured_count(tmp_path):
    assert schema_card.export_classes(tmp_path, set(), lambda c: dict(name=c, curie=c)) == []


def test_the_evidence_payload_carries_the_registry_page_not_a_repository_path():
    import app as appmod
    if appmod._state.get("kg") is None:
        appmod._state.pop("kg", None)
    kg = appmod.state()[0]
    nct_edge = next(e for e, x in kg.edges.items()
                    if "NCT" in str(x.get("source_record")) and kg.provenance(e)["rows"][0].get("raw_response"))
    card = appmod.evidence_card(kg, nct_edge)
    assert card["raw_response"].startswith("https://clinicaltrials.gov/study/NCT")
    assert "data/snapshots" not in json.dumps(card)
    assert appmod.registry_url({"source_record": "CHEMBL25"}, "data/snapshots/chembl/x.json.gz") is None


def test_the_spot_check_and_the_prediction_count_are_described_as_they_are():
    for p in ("docs/SUBMISSION.md", "docs/LIMITATIONS.md"):
        t = " ".join(DOCS[p].split())
        assert "by a scientist" not in t, p
        # FIX2 F2: the blinded check is stated as the FAIL it is, with the raters' agreement with the scientist
        assert "FAIL" in t and "7 of 39" in t and "reported pass or fail" not in t and "PENDING: outputs/pharos/spotcheck" not in t, p
        assert "confirmed by Open Targets" not in t and "2 (0.7%" in t and "of the 280 pruned" in t, p
        assert "meta-path model" in t and "112,187" in t and "29,270,260 edges" in t, p
    novelty = (ROOT / "outputs" / "linkpred" / "predictions_novelty.md").read_text()
    assert "**112,187**" in novelty and "29,270,260 edges" in novelty   # both numbers come from the committed file


def test_fix2_pruning_numbers_in_the_documents_are_the_committed_ones():
    """FIX2 F2/F3: every number of the pruning paragraph in README, SUBMISSION and LIMITATIONS is the one in its file."""
    import re
    import numbers_page as npg
    sc = npg.spot_check([ROOT])
    au = npg.audit_sample([ROOT])
    ppi = (ROOT / "outputs" / "pharos" / "ppi_corroboration.md").read_text(encoding="utf-8")
    pp, pk = re.search(r"\*\*Headline: ([\d.]+)% of pruned rows and ([\d.]+)% of kept rows", ppi).groups()
    p = headline.prune_numbers(ROOT)
    for doc in ("README.md", "docs/SUBMISSION.md", "docs/LIMITATIONS.md"):
        t = " ".join(DOCS[doc].split())
        assert sc["result"] == "FAIL" and "FAILED" in t, doc
        assert (f"{sc['pruned_unsupported']} of {sc['pruned_rated']} pruned and {sc['kept_unsupported']} of "
                f"{sc['kept_rated']} kept links were judged unsupported (rule: at least {sc['rule_pruned_min']} and at most "
                f"{sc['rule_kept_max']})") in t, doc
        assert f"agreed with them on {sc['owner_agree']} of {sc['owner_rows']}" in t, doc
        assert (f"{100 * au['pruned_unsupported'] / au['pruned_n']:.1f}% of pruned and "
                f"{100 * au['kept_unsupported'] / au['kept_n']:.1f}% of kept links unsupported") in t, doc
        assert f"{pp}% of pruned and {pk}% of kept protein-protein rows" in t, doc
        assert f"{p['nontext_gd_pruned_n']:,} pruned gene-disease" in t and f"{p['nontext_gd_pruned_in_ot_n']} (" in t, doc
        assert f"{100 * p['nontext_gd_pruned_n'] / p['pruned']:.2f}%" in t, doc


def test_fix2_one_story_about_amazon_neptune():
    """FIX2 F5 (G2-ES-02, DS-4, G2-U-04, G2-ES-05): no surface says the graph is not in a database; the counts and the wake
    time are the measured ones of outputs/neptune/live_parity.json; no graph code in a download title."""
    import re
    lp = json.loads((ROOT / "outputs" / "neptune" / "live_parity.json").read_text())
    dl = json.loads((ROOT / "downloads.json").read_text())
    nep = next(f for f in dl["files"] if f["path"] == "neptune/")
    one = (f"The same graph is loaded in Amazon Neptune, which this site uses on request (asleep to save cost; waking takes about "
           f"{lp['wake_minutes']} minutes)")
    assert one in nep["what"] and "not hosted in a database" not in nep["what"]
    for f in dl["files"]:
        assert not re.search(r"\bG[0-4]\b", f["title"] + " " + f["what"].replace("heterodata_G3.pt", "")), f["title"]
    counts = f"{lp['loaded']['nodes']:,} nodes and {lp['loaded']['links']:,} links"
    for doc in ("README.md", "docs/SUBMISSION.md"):
        t = " ".join(DOCS[doc].split())
        assert "not hosted in a database" not in t and counts in t, doc
    assert "r.biolink_predicate" in DOCS["README.md"] and "`0000050`" in DOCS["README.md"] and "`0000050`" in DOCS["docs/LIMITATIONS.md"]
    assert "once the cluster is loaded" not in DOCS["docs/AGENT_LOOP.md"]
    assert "also runs on Amazon Neptune, which holds the whole graph (see Numbers)" in DOCS["docs/AGENT_LOOP.md"]
