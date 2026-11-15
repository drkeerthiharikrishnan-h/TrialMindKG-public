"""
The four reasoning specialists and the verifier (Nemotron via NIM; deterministic stand-ins offline).

Each specialist receives ONLY its PCST subgraph as numbered facts and returns typed claims:
    {"text", "evidence_edges": [edge ids], "evidence_status", "uncertainty"}
evidence_status: prespecified | post_hoc | prospectively_confirmed | hypothesis_generating |
                 registry_record | curated_summary
verify_claim() then rejects a claim that (1) cites nothing, (2) cites an edge outside the subgraph, (3) states a
number that is not in the cited facts (kg_query.verify_answer), (4) uses causal wording on post hoc / hypothesis /
curated evidence (claims.causal_problem), or (5) is judged unsupported by the verifier model.

A rejected claim gets ONE repair attempt (repair_claim): the specialist sees its claim, the rejection reasons and its
facts, and either rewrites the claim from the facts or withdraws it. The rewrite goes through the same checks.

Functions: run_specialist(nim, role, question, subgraph) -> claims;  verify_claim(nim, kg, claim, subgraph) -> dict
           repair_claim(nim, claim, reasons, subgraph) -> revised claim dict or None (withdrawn)
"""
import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nim import register_offline
import kg_query
import claims as claims_rules

STATUSES = ["prespecified", "post_hoc", "prospectively_confirmed", "hypothesis_generating", "registry_record",
            "curated_summary"]
ROLES = {
    "failure_diagnostician": ("Failure Diagnosis",
        "Explain why the trial or program failed: which primary endpoint was missed, by how much, what the registry "
        "says about stopping, and what the curated record says."),
    "population_stratifier": ("Candidate Responder Population",
        "Identify subgroups, biomarkers or endpoints where response differed, and say whether each analysis was "
        "prespecified or post hoc."),
    "indication_explorer": ("Related Indications",
        "Use targets, genes, disease associations and other programs sharing the target to describe related "
        "indications; do not claim efficacy that the facts do not show."),
    "redesign_advisor": ("Evidence-Supported Redesign Options",
        "From what happened after the failure (endpoint changes, population changes, confirmatory trials), list "
        "redesign options and whether they were later prospectively confirmed."),
}
# CC-35 (review m14): a section that produced no verified option must not be titled as if it had options
EMPTY_TITLES = {"redesign_advisor": "Redesign options: none supported by the evidence found"}


def section_title(role, n_claims):
    """Takes a specialist role and the number of verified claims in its section. Returns the section title: ROLES[role][0],
    or EMPTY_TITLES[role] when the section has no claim and the role has an empty-section title."""
    return EMPTY_TITLES[role] if not n_claims and role in EMPTY_TITLES else ROLES[role][0]


CLAIM_SCHEMA = {"type": "object", "required": ["claims"], "properties": {"claims": {"type": "array", "items": {
    "type": "object", "required": ["text", "evidence_edges", "evidence_status", "uncertainty"],
    "properties": {"text": {"type": "string"}, "evidence_edges": {"type": "array", "items": {"type": "string"}},
                   "evidence_status": {"type": "string", "enum": STATUSES},
                   "uncertainty": {"type": "string", "enum": ["low", "medium", "high"]}}}}}}
VERDICT_SCHEMA = {"type": "object", "required": ["supported"],
                  "properties": {"supported": {"type": "boolean"}, "reason": {"type": "string"}}}


def prompt(role, question, sg):
    title, task = ROLES[role]
    system = (f"You are the {title} specialist of TrialMindKG. {task} Use ONLY the numbered facts. Every claim must "
              "cite the fact ids it rests on (evidence_edges, e.g. 'e0123456789abcdef'). Copy numbers exactly. "
              "Label each claim's evidence_status (" + ", ".join(STATUSES) + ") and uncertainty. "
              "If the facts are insufficient, return fewer claims - never fill gaps from memory.")
    user = f"ROLE: {role}\nQUESTION: {question}\nFACTS:\n" + "\n".join(sg.facts)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


EDGE_ID = re.compile(r"e[0-9a-f]{16}")
ABSENCE = re.compile(r"\bno (further |other )?(claims?|information|evidence|facts?|data)\b|cannot be (made|determined|assessed)|"
                     r"not (provided|available|stated|reported) in the (provided )?facts|insufficient (facts|evidence|information)",
                     re.I)


def clean_ids(ids):
    """Edge ids as written by the model ('/tmkg/edge/[e0123...]', '[e0123...]') -> the bare ids, order kept."""
    out = []
    for x in ids or []:
        out += [m for m in EDGE_ID.findall(str(x)) if m not in out]
    return out


def run_specialist(nim, role, question, sg):
    """Claims of one specialist. Statements that only say the facts are insufficient and cite nothing are not
    claims: they are dropped here (counted in the report as absence_statements), never verified."""
    if not sg.facts:
        return []
    r = nim.chat("specialist", prompt(role, question, sg), schema=CLAIM_SCHEMA)
    out = []
    for c in r.data["claims"]:
        c = dict(c, evidence_edges=clean_ids(c.get("evidence_edges")))
        absence = not c["evidence_edges"] and ABSENCE.search(c.get("text") or "")
        out.append(dict(c, role=role, backend=r.backend, model=r.model, snapshot_key=r.key, absence=bool(absence)))
    return out


SECTION_FAILED = ("This section could not be written: the model's answer for it was unusable. "
                  "The other sections are not affected; asking again may fill it.")


def run_specialist_safe(nim, role, question, sg):
    """run_specialist that never raises (review finding SEC-10: one malformed specialist reply used to end the whole
    report with an error). Takes the same arguments. Returns (claims, note): the specialist's claims and None, or
    ([], SECTION_FAILED) when its reply did not match the schema or the call failed; the report prints the note in
    that section and the other sections are written as usual."""
    try:
        return run_specialist(nim, role, question, sg), None
    except Exception as err:                          # malformed reply (ValueError), missing key, failed call
        print(f"specialist {role} failed: {type(err).__name__}: {str(err)[:300]}", file=sys.stderr)
        return [], SECTION_FAILED


def verify_claim(nim, kg, claim, sg):
    """-> dict(ok, reasons, verifier_backend). Deterministic checks first, then the verifier model."""
    reasons = []
    ev = claim.get("evidence_edges") or []
    if not ev:
        reasons.append("cites no evidence")
    outside = [e for e in ev if e not in sg.edges]
    if outside:
        reasons.append(f"cites edges outside its subgraph: {outside[:3]}")
    text = claim["text"] + " " + " ".join(f"[{e}]" for e in ev)
    v = kg_query.verify_answer(text, kg, allowed=sg.edges, conventions=True)
    if v["unsupported_numbers"]:
        reasons.append(f"numbers not in cited evidence: {v['unsupported_numbers'][:5]}")
    if v["unknown_citations"]:
        reasons.append("unknown edge ids")
    causal = claims_rules.causal_problem(claim)       # P5: no proven-cause wording on post hoc / curated evidence
    if causal:
        reasons.append(causal)
    backend = ""
    if not reasons:
        cited = [f for f in sg.facts if f[1:18] in ev]
        msgs = [{"role": "system", "content": "Answer whether the CLAIM is fully supported by the FACTS alone."},
                {"role": "user", "content": "CLAIM: " + claim["text"] + "\nFACTS:\n" + "\n".join(cited)}]
        r = None
        for extra in ([], [{"role": "user", "content": "Reply with one short JSON object only."}]):
            try:                                      # a malformed reply is asked once more (new request key)
                r = nim.chat("verifier", msgs + extra, schema=VERDICT_SCHEMA)
                break
            except ValueError:
                continue
        if r is None:                                 # unusable verifier output: never accept a claim unchecked
            return dict(ok=False, reasons=["verifier gave no usable answer (malformed model output)"],
                        verifier_backend=nim.backend)
        backend = r.backend
        if not r.data["supported"]:
            reasons.append("verifier: " + (r.data.get("reason") or "not supported"))
    return dict(ok=not reasons, reasons=reasons, verifier_backend=backend)


REPAIR_SYSTEM = ("A checker rejected your claim. Rewrite it so that it is fully supported by the numbered FACTS: copy "
                 "numbers exactly as written in the facts, state the analysis/population/time point the number belongs "
                 "to, drop anything the facts do not say, and cite the fact ids used. If no supported version exists, "
                 "return an empty claims list. Return at most one claim.")


def repair_claim(nim, claim, reasons, sg):
    """One revision of a rejected claim from the same facts -> revised claim (same role, repaired_from set) or None.
    Claims rejected because of a malformed verifier reply are not repaired (the claim itself was not judged)."""
    if not sg.facts or any("no usable answer" in r for r in reasons):
        return None
    user = (f"ROLE: {claim['role']}\nREJECTED CLAIM: {claim['text']}\nCITED: {', '.join(claim.get('evidence_edges') or [])}"
            f"\nREASONS: {'; '.join(reasons)}\nFACTS:\n" + "\n".join(sg.facts))
    try:
        r = nim.chat("repair", [{"role": "system", "content": REPAIR_SYSTEM}, {"role": "user", "content": user}],
                     schema=CLAIM_SCHEMA)
    except ValueError:
        return None
    new = (r.data or {}).get("claims") or []
    if not new or new[0].get("text", "").strip() == claim["text"].strip():
        return None
    return dict(new[0], role=claim["role"], backend=r.backend, model=r.model, snapshot_key=r.key,
                repaired_from=claim["text"], first_reasons=reasons)


# ---------------------------------------------------------------- offline stand-ins (deterministic, labelled)
FACT = re.compile(r"^\[(e[0-9a-f]{16})\] (.+?) \((\S+)\) -(\S+)-> (.+?) \((\S+)\) \| source: ([^|]+?)(?: \| (\{.*\}))?$")


def _parse_facts(user):
    out = []
    for line in user.split("FACTS:\n", 1)[-1].splitlines():
        m = FACT.match(line)
        if m:
            eid, s, scat, pred, o, ocat, src, props = m.groups()
            out.append(dict(eid=eid, s=s, scat=scat, pred=pred, o=o, ocat=ocat, src=src,
                            props=json.loads(props) if props else {}))
    return out


def _claim(text, eids, status, unc):
    return {"text": text, "evidence_edges": eids, "evidence_status": status, "uncertainty": unc}


@register_offline("specialist")
def _offline_specialist(messages, schema):
    user = messages[-1]["content"]
    role = re.search(r"ROLE: (\S+)", user).group(1)
    fs = _parse_facts(user)
    claims = []
    for f in fs:
        p = f["props"]
        if role == "failure_diagnostician":
            if f["pred"] == "tmkg:has_outcome_measure" and p.get("kind") == "PRIMARY":
                an = [a for a in json.loads(p.get("analyses", "[]")) if a.get("p_value")]
                txt = f"{f['s']}: primary outcome '{p.get('name')}'"
                if an:
                    txt += f"; {an[0].get('param_type')} {an[0].get('param_value')}, p = {an[0].get('p_value')}"
                claims.append(_claim(txt, [f["eid"]], "prespecified", "low"))
            elif f["pred"] == "tmkg:has_trial" and p.get("why_stopped"):
                claims.append(_claim(f"{f['o']}: registry reason for stopping: {p['why_stopped']}", [f["eid"]],
                                     "registry_record", "low"))
            elif f["ocat"] == "tmkg:DevelopmentEvent" and p.get("original_problem"):
                claims.append(_claim(f"Curated record: {p['original_problem']}", [f["eid"]], "curated_summary", "low"))
        elif role == "population_stratifier":
            if f["pred"] == "tmkg:has_outcome_measure" and p.get("kind") in ("SECONDARY", "POST_HOC", "OTHER_PRE_SPECIFIED") \
                    and re.search(r"sub-?group|high|low|positive|negative|eosinophil|levodopa|genotype", p.get("name", ""), re.I):
                claims.append(_claim(f"{f['s']}: outcome measured in a subgroup: '{p.get('name')}'", [f["eid"]],
                                     "post_hoc" if p.get("kind") == "POST_HOC" else "prespecified", "medium"))
            elif f["ocat"] == "tmkg:DevelopmentEvent" and p.get("analysis_showed"):
                claims.append(_claim(f"Curated record of the additional analysis: {p['analysis_showed']}", [f["eid"]],
                                     "curated_summary", "medium"))
        elif role == "indication_explorer":
            if f["pred"] in ("biolink:interacts_with", "biolink:associated_with", "biolink:gene_product_of"):
                extra = f" (Open Targets score {p['ot_score']})" if p.get("ot_score") else ""
                claims.append(_claim(f"{f['s']} {f['pred'].split(':')[1].replace('_', ' ')} {f['o']}{extra}", [f["eid"]],
                                     "registry_record" if f["pred"] != "biolink:associated_with" else "hypothesis_generating",
                                     "medium"))
        elif role == "redesign_advisor":
            if f["ocat"] == "tmkg:DevelopmentEvent" and p.get("pivot_decision"):
                claims.append(_claim(f"Decision after the failure: {p['pivot_decision']}", [f["eid"]], "curated_summary", "medium"))
                if p.get("outcome_evidence"):
                    claims.append(_claim(f"What happened next: {p['outcome_evidence']}", [f["eid"]], "curated_summary", "medium"))
            elif f["ocat"] == "tmkg:DevelopmentEvent" and p.get("what_changed"):
                claims.append(_claim(f"Repurposing: {p['what_changed']}", [f["eid"]], "curated_summary", "medium"))
    return json.dumps({"claims": claims[:8]})


@register_offline("verifier")
def _offline_verifier(messages, schema):
    user = messages[-1]["content"]
    claim, facts = user.split("\nFACTS:\n", 1)
    words = {w for w in re.findall(r"[a-z0-9.]{4,}", claim.lower())}
    fw = set(re.findall(r"[a-z0-9.]{4,}", facts.lower()))
    share = len(words & fw) / (len(words) or 1)
    return json.dumps({"supported": share >= 0.6, "reason": f"{share:.0%} of claim words found in cited facts"})


@register_offline("repair")
def _offline_repair(messages, schema):
    """Stand-in: withdraws the claim (no language model to rewrite it)."""
    return json.dumps({"claims": []})
