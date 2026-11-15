"""
Claim controller rules (P5): causal-language guard, duplicate merge, support status and three separate indicators.

- causal_problem(claim) -> reason or None
    Causal wording ('because', 'due to', 'caused', 'led to', 'drove', 'explains', 'resulted in') is allowed when the
    claim's evidence_status is prespecified, prospectively_confirmed, registry_record or curated_summary. On post_hoc /
    hypothesis_generating evidence it is a reason to reject (the repair step then rewrites the
    claim as an association or a hypothesis). Application: never present a development adaptation as a proven cause.
- indicators(claim, kg) -> dict(evidence_strength, mapping_confidence, model_uncertainty)
    evidence_strength  from the cited edges' BioLink knowledge_level and evidence_type, the WEAKEST cited edge:
                       strong (curated / registry knowledge assertion), moderate (statistical association,
                       observation), limited (prediction, logical entailment, computed)
    mapping_confidence weakest kg_schema mapping_confidence of the cited edges
    model_uncertainty  the specialist's own label (low / medium / high)
- dedupe(claims) -> claims  same claim in two sections is shown once (first section in ORDER), also_in lists the rest
- support_status(claim, verdict) -> supported | hypothesis_generating | mixed | unchecked
  (unchecked = the contradiction judge failed to run, verdict "unjudged"; the claim still passed the verifier)
    mixed = the judge finds the claim contradicted by other facts retrieved for the question
- contradiction_verdict(nim, text, facts) -> supported | contradicted | not_addressed | unjudged
"""
import re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kg_schema
from nim import register_offline

CAUSAL = re.compile(r"\b(because|due to|caused|causes|cause of|led to|leads to|drove|driven by|explains?|explained by|"
                    r"resulted in|results in|attributable to|owing to)\b", re.I)
CAUSAL_OK = {"prespecified", "prospectively_confirmed", "registry_record", "curated_summary"}   # curated summaries
# carry the curated source's own statement (e.g. 'the futility analysis led to stopping'); the guard targets
# post hoc / hypothesis-generating evidence presented as a proven cause
STRENGTH = ["strong", "moderate", "limited"]          # best first
VERDICTS = ["supported", "contradicted", "not_addressed"]
JUDGE_SCHEMA = {"type": "object", "required": ["verdict", "reason"], "properties": {
    "verdict": {"type": "string", "enum": VERDICTS}, "reason": {"type": "string"}}}
JUDGE_SYSTEM = ("You judge one CLAIM against the FACTS (registry records, curated records, database statements). "
                "verdict = supported: the facts state it, numbers included; contradicted: the facts state something "
                "incompatible (a different number, identifier, outcome or direction); not_addressed: the facts neither "
                "confirm nor contradict it. Use only the FACTS.")


def causal_problem(claim):
    m = CAUSAL.search(claim.get("text") or "")
    if m and claim.get("evidence_status") not in CAUSAL_OK:
        return (f"causal wording ('{m.group(0)}') on {claim.get('evidence_status')} evidence: state it as an "
                "association or a hypothesis, not a proven cause")
    return None


def edge_strength(e):
    if e.get("knowledge_level") == "knowledge_assertion" and e.get("evidence_type") in (
            "registry", "curated", "curator_confirmed", "database", "internal", "synthetic"):
        return "strong"
    if e.get("knowledge_level") in ("statistical_association", "observation"):
        return "moderate"
    return "limited"


def indicators(claim, kg):
    edges = [kg.edges[e] for e in claim.get("evidence_edges") or [] if e in kg.edges]
    strength = max((edge_strength(e) for e in edges), key=STRENGTH.index) if edges else "limited"
    maps = [e.get("mapping_confidence") for e in edges if e.get("mapping_confidence") in kg_schema.MAPPING_CONFIDENCE]
    return dict(evidence_strength=strength, mapping_confidence=kg_schema.weakest(maps) if maps else None,
                model_uncertainty=claim.get("uncertainty"))


def _tokens(text):
    return set(re.findall(r"[a-z0-9.]+", (text or "").lower()))


def dedupe(claims, order, threshold=0.8):
    """claims carry 'role'; keeps the first by section order. Duplicate = same cited evidence and >= threshold word
    overlap (Jaccard), or >= 0.9 word overlap whatever is cited."""
    ranked = sorted(claims, key=lambda c: order.index(c["role"]) if c["role"] in order else len(order))
    kept = []
    for c in ranked:
        t, ev = _tokens(c["text"]), set(c.get("evidence_edges") or [])
        for k in kept:
            kt = _tokens(k["text"])
            j = len(t & kt) / (len(t | kt) or 1)
            if j >= 0.9 or (j >= threshold and ev == set(k.get("evidence_edges") or [])):
                k.setdefault("also_in", []).append(c["role"])
                break
        else:
            kept.append(dict(c))
    return kept


def contradiction_verdict(nim, text, facts):
    msgs = [{"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": "CLAIM: " + text + "\nFACTS:\n" + "\n".join(facts)}]
    for extra in ([], [{"role": "user", "content": "Reply with one short JSON object only."}]):
        try:
            return nim.chat("judge", msgs + extra, schema=JUDGE_SCHEMA).data["verdict"]
        except ValueError:
            continue
    return "unjudged"


def support_status(claim, verdict):
    if verdict == "contradicted":
        return "mixed"
    if claim.get("evidence_status") in ("post_hoc", "hypothesis_generating"):
        return "hypothesis_generating"
    if verdict == "unjudged":
        return "unchecked"          # the contradiction judge did not run: never shown as plain "supported"
    return "supported"              # includes not_addressed: the judge ran and found nothing contradicting the claim


@register_offline("judge")
def _offline_judge(messages, schema):
    """Stand-in: never claims a contradiction (no language model to judge one)."""
    return '{"verdict": "not_addressed", "reason": "offline stand-in: not judged"}'
