"""T-003: judge labels and schema validation (findings of the 2026-11-03 harness review, confirmed in the code 2026-11-04)."""
import claims as cr
import evaluate
import nim
import numbers_page


def test_unjudged_claim_is_never_plain_supported():
    prespec = dict(evidence_status="prespecified")
    assert cr.support_status(prespec, "unjudged") == "unchecked"
    assert cr.support_status(prespec, "contradicted") == "mixed"
    assert cr.support_status(prespec, "supported") == "supported"
    # by design: the judge ran and found nothing contradicting the claim, which already passed the verifier
    assert cr.support_status(prespec, "not_addressed") == "supported"
    # an evidence-level label still wins over a failed judge
    assert cr.support_status(dict(evidence_status="post_hoc"), "unjudged") == "hypothesis_generating"


def test_schema_rejects_wrong_boolean_and_number_types():
    s = {"type": "object", "required": ["supported"], "properties": {"supported": {"type": "boolean"}}}
    assert nim.check_schema({"supported": True}, s) == []
    assert nim.check_schema({"supported": False}, s) == []
    for bad in ("false", "true", 0, 1, None):             # the string "false" is truthy: it must not pass as a verdict
        assert nim.check_schema({"supported": bad}, s), bad
    n = {"type": "object", "properties": {"n": {"type": "integer"}, "x": {"type": "number"}}}
    assert nim.check_schema({"n": 3, "x": 0.5}, n) == [] and nim.check_schema({"n": 3, "x": 2}, n) == []
    assert nim.check_schema({"n": True}, n) and nim.check_schema({"n": 2.5}, n) and nim.check_schema({"x": "1"}, n)
    assert nim.check_schema({"x": False}, n)               # a bool is not a number for our schemas


def test_report_metrics_counts_unchecked_claims():
    row = dict(id="q", kind="normal", drafted=3, supported=3, first_draft_supported=3, repaired=0, rejected=0,
               nct_mismatches=0, overclaims=[], seconds=1.0,
               support_status=dict(supported=1, hypothesis_generating=0, mixed=0, unchecked=2))
    m = evaluate.report_metrics([row])
    assert m["unchecked_claims"]["value"] == 2 and m["unchecked_claims"]["n"] == 3


def test_comparison_discloses_that_judging_uses_the_graphs_own_evidence():
    rows = [dict(system=s, verdict="supported", nct_mismatches=[]) for s in ("llm_only", "rag", "trialmindkg")]
    m = evaluate.comparison_metrics(rows)
    for name in ("llm_only_baseline", "rag_baseline", "trialmindkg_judged"):
        assert "itself retrieved" in str(m[f"{name}_supported_rate"])


def test_numbers_page_protocol_text_does_not_claim_one_cap_for_every_run():
    text = numbers_page.PROTOCOL_TEXT
    assert "every edge type capped at 2M" not in text
    assert "1M" in text and "2M" in text and "next to each result" in text
