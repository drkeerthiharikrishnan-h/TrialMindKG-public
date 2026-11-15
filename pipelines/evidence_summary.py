"""
A short model-written summary of the evidence lines an answer already shows (packet PK4; owner request).

The bottom line of an answer (answer_summary.py) is composed by rules. This module adds ONE language-model reading of the
same evidence: the first 30 rows, claims or links of the answer become numbered plain sentences with their source and
strength, the querier model of config/nim_models.yaml writes 80 to 120 words that cite them as [n], and every sentence is
then checked against the lines it cites by the claim-support verifier model. A sentence the check does not find supported
is dropped; with fewer than two sentences left there is no summary. Nothing is added when anything fails: the page then
shows the bottom line only.

What a line says (every word comes from the answer or the graph's own fields; no file path, layer name or run tag):
- a graph answer row (Pharos ask, tested question, trial-graph query): its cells, the statements of the links it cites,
  each source with its strength word and pruning verdict, and the row's class as the bottom line counts it
- a trial-report claim: the claim, its section, the sources of its evidence, evidence status, strength and support check
- an entity card: one line per known link (round-robin over the evidence kinds, so every kind is read) and at most five
  predicted links, which are labelled as predictions, not source statements
- a 'why this link' drawer: the link, the values the source gives, the other records of the same pair, the timeline

Functions (input -> output)
- graph_lines(answer, facts=None, max_lines=30)   -> [line]   a graph answer's rows (facts: answer_summary.edge_facts)
- claim_lines(report, max_lines=30)              -> [line]   a trial report's claims, numbered as the page numbers them
- card_lines(card, max_lines=30)                 -> [line]   an entity card (GET /api/pharos/card/<id>)
- edge_lines(explain, max_lines=30)              -> [line]   a link's drawer (GET /api/pharos/explain/<id>)
  A line is {n, id, kind ('row' | 'claim' | 'link' | 'prediction' | 'timeline' | 'values'), ref, text, sources}; sources
  are the source names the text prints (rule F2 reads them; they are not part of the cache key or of the stored text).
- card_question(card), card_bottom_line(card), edge_question(explain), edge_bottom_line(explain) -> str
- prompt(question, bottom_line, lines)           -> the one user message sent to the model
- references(text)                               -> [int] every [n] cited (ranges [2-4] and lists [1, 3] expanded), or
                                                    None when a bracket cites something that is not a line number form
- split_sentences(text)                          -> [str]
- rule_check(sentences, lines, read_as=None)     -> (kept, dropped): the three fixed rules of FIX3, no model call
  (F1 'predict' words only over prediction lines; F2 a number that is only part of a source name; F3 the user's
  phrase where the answer read another term)
- read_as_note(read_as)                          -> 'Read as lung carcinoma (the question named ...).' or ''
- check_sentences(sentences, lines, client, read_as=None) -> (kept, dropped) or None when the check could not run
- summarise(question, bottom_line, lines, client=None, check=True, read_as=None) -> {text, model, created, ...} or None
- cache_key(question, lines, model)              -> 16-hex key of (question, line ids, model, prompt version)
- cached(key, folders) / store(record, folder)   -> read / write one JSON file per answer (<key>.json)
- summary_for(question, bottom_line, lines, folders, write_folder=None, client=None, call=True) -> record or None
  the cache first; with call=True and no cached record, summarise() once and keep the result
- response_keys(record, lines)                   -> {summary_text, summary_meta} or {} (the keys are absent when None)
- attach(out, record, lines)                     -> out with those keys added (a copy)

Rules kept here: the reply is refused (None) on any failure, an empty reply, more than MAX_WORDS words, or a [n] outside the
numbered lines; the model call goes through nim.web_client (its own deadline, DEADLINE seconds); an offline stand-in never
writes a summary; TMKG_SUMMARY=off switches the whole step off.
"""
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import answer_summary as AS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "outputs" / "summaries"        # the committed summaries (filled by scripts/precompute_examples.py --summaries)
SUBFOLDER = Path("outputs") / "summaries"      # the same place under any data root
PROMPT_VERSION = "pk4-1"                       # part of the cache key: a changed prompt never reuses an older summary
WRITER_ROLE = "querier"                        # the model that writes (config/nim_models.yaml roles.querier)
CHECK_ROLE = "verifier"                        # the claim-support model that checks every sentence (roles.verifier)
DEADLINE = 30                                  # seconds for the writer call, and again for all sentence checks together
MAX_LINES = 30
MAX_WORDS = 180
MIN_SENTENCES = 2
MAX_PREDICTED = 5                              # predicted links read from an entity card
INSTRUCTION = ("Write 80 to 120 words for a scientist. Use only the numbered lines. Refer to lines as [n]. Say what is "
               "curated, statistical, text-mined or predicted, and what the lines do not show. Add no facts.")
CHECK_SCHEMA = {"type": "object", "required": ["supported"],
                "properties": {"supported": {"type": "boolean"}, "reason": {"type": "string"}}}
UNIT = {"row": ("row", "rows"), "claim": ("claim", "claims"), "link": ("link", "links")}
ROW_CLASS_WORDS = {"curated": "curated or measured support", "statistical": "statistical support only",
                   "other": "links whose source states no evidence level", "text-mined": "text-mined or predicted links only",
                   "pruned": "every link of a step was pruned", "unknown": "no evidence level could be read"}
SUPPORT_WORDS = {"supported": "supported by its cited records", "mixed": "mixed: some records support it, some do not",
                 "hypothesis_generating": "hypothesis-generating", "contradicted": "contradicted by a cited record",
                 "unsupported": "not supported by its cited records"}
STATUS_WORDS = {"prespecified": "prespecified", "post_hoc": "post hoc", "prospectively_confirmed": "prospectively confirmed",
                "hypothesis_generating": "hypothesis-generating", "registry_record": "registry record",
                "curated_summary": "curated summary"}


def enabled():
    """True unless TMKG_SUMMARY=off (the switch that takes the whole step out of every answer)."""
    return os.environ.get("TMKG_SUMMARY", "on").strip().lower() not in ("off", "0", "false", "no")


# ------------------------------------------------------------------ small helpers
def _clean(text, cap=220):
    """One line of plain text: whitespace collapsed, cut at `cap` characters on a word boundary."""
    t = " ".join(str(text if text is not None else "").split())
    return t if len(t) <= cap else t[:cap].rsplit(" ", 1)[0] + "…"


def _words(column):
    return str(column).replace("_", " ").strip()


def _strength(fact):
    """Takes the facts of one link (answer_summary.edge_facts) or a card row. Returns the strength word a reader sees: a
    text-mining link is 'text-mined' whatever level its source states, a prediction is 'predicted', else the bottom line's
    word (answer_summary.strength_word), else 'evidence level not stated'."""
    kind = fact.get("kind")
    if kind is None and (fact.get("predicate") or fact.get("source")):
        try:
            kind = AS._personas.evidence_kind(fact)
        except Exception:
            kind = None
    if kind == "text mining":
        return "text-mined"
    w = fact.get("strength") or AS.strength_word(fact.get("knowledge_level"), kind)
    return w or "evidence level not stated"


def _verdict(word, confidence):
    """'kept' / 'pruned' (or keep / prune) and a confidence -> 'kept by the pruning check, confidence 0.90'; '' unscored."""
    w = {"keep": "kept", "prune": "pruned"}.get(str(word or ""), word)
    if w not in ("kept", "pruned"):
        return ""
    c = AS._float(confidence)
    return f"{w} by the pruning check" + (f", confidence {c:.2f}" if c is not None else "")


def _statement(fact):
    """'PTGS2 is associated with Alzheimer disease' from a link's facts (names, never ids when a name is there)."""
    s, o = fact.get("subject_label") or fact.get("subject"), fact.get("object_label") or fact.get("object")
    phrase = fact.get("phrase") or _words(str(fact.get("predicate") or "").split(":")[-1])
    return _clean(f"{s} {phrase} {o}", 160) if s and o else ""


def potency_words(p):
    """A potency (pharos_app.potency: {value, measure, n_activities, unit, word}, or already words) -> 'IC50 6.2 (pChEMBL
    scale; 1 uM or stronger)'; '' when there is none."""
    if not p:
        return ""
    if not isinstance(p, dict):
        return _clean(p, 80)
    if p.get("value") is None:
        return ""
    head = f"{p.get('measure') or 'activity'} {AS.display_value(p['value'])}"
    tail = ["pChEMBL scale" if "pchembl" in str(p.get("unit") or "").lower() else p.get("unit"), p.get("word")]
    tail = [x for x in tail if x]
    if p.get("n_activities"):
        tail.append(f"{int(p['n_activities'])} measurements")
    return head + (f" ({'; '.join(str(x) for x in tail)})" if tail else "")


def _source_part(fact):
    """'DisGeNET (curated, kept by the pruning check, confidence 0.90)' for one link."""
    bits = [_strength(fact)]
    v = _verdict(fact.get("verdict"), fact.get("confidence"))
    if v:
        bits.append(v)
    if potency_words(fact.get("potency")):
        bits.append(potency_words(fact.get("potency")))
    src = AS.source_word(fact.get("source")) or "source not named"
    return f"{src} ({', '.join(bits)})"


def _line(n, lid, kind, ref, text, sources=()):
    return dict(n=n, id=lid, kind=kind, ref=ref, text=_clean(text, 600), sources=[x for x in dict.fromkeys(sources) if x])


def _hash(text):
    return hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:12]


# ------------------------------------------------------------------ lines of a graph answer
def _row_cells(row, columns):
    """The row's own cells in words ('disease: Alzheimer disease; activity: 7.2'): evidence, source and level columns
    are left out (the links say those), and an identifier is left out when the row has a name as well."""
    cells = []
    for c in columns:
        if AS.NON_LABEL_COLUMNS.search(c) or str(c).endswith("_level"):
            continue
        v = row.get(c)
        vals = [x for x in (v if isinstance(v, list) else [v]) if x not in (None, "") and not
                (isinstance(x, str) and AS.EDGE_ID.match(x))]
        if not vals:
            continue
        shown = [str(AS.display_value(x)) for x in vals[:5]]
        cells.append((c, "; ".join(dict.fromkeys(shown)) + (f" and {len(vals) - 5} more" if len(vals) > 5 else ""),
                      all(isinstance(x, str) and AS.CURIE.match(x) for x in vals)))
    named = [c for c in cells if not c[2]]
    return "; ".join(f"{_words(c)}: {_clean(v, 200)}" for c, v, _ in (named or cells))


def graph_lines(answer, facts=None, max_lines=MAX_LINES):
    """Takes a graph answer (columns, rows; a Pharos answer also has summary.refs from answer_summary.pharos_summary) and
    optionally the facts of its cited links (default: the facts the answer's bottom line already holds, summary.refs.edges).
    Returns one line per row for the first `max_lines` rows, numbered as the page numbers the rows (line n = row n):
    the row's cells, the statements of its links, each source with strength and pruning verdict, and the row's class."""
    rows, columns = answer.get("rows") or [], answer.get("columns") or []
    refs = ((answer.get("summary") or {}).get("refs") or {}) if isinstance(answer.get("summary"), dict) else {}
    facts = facts if facts is not None else (refs.get("edges") or {})
    row_class = refs.get("row_class") or {}
    out = []
    for i, row in enumerate(rows[:max_lines], 1):
        row = row if isinstance(row, dict) else {}
        eids = list(dict.fromkeys(AS.row_edges(row, columns)))
        known = [facts[e] for e in eids if e in facts]
        parts = [p for p in [_row_cells(row, columns)] if p]
        stmts = list(dict.fromkeys(s for s in (_statement(f) for f in known) if s))
        if stmts:
            parts.append("Links: " + "; ".join(stmts[:3]) + (f" and {len(stmts) - 3} more" if len(stmts) > 3 else ""))
        srcs = list(dict.fromkeys(_source_part(f) for f in known))
        names = [AS.source_word(f.get("source")) for f in known]
        if srcs:
            parts.append("Sources: " + "; ".join(srcs[:6]) + (f" and {len(srcs) - 6} more" if len(srcs) > 6 else ""))
        elif not known:
            levels, sources = AS._row_level_columns(row, columns)
            words = list(dict.fromkeys(w for w in (AS.strength_word(x) for x in levels) if w))
            names = [AS.source_word(x) for x in sources]
            if sources or words:
                parts.append("Sources: " + (", ".join(dict.fromkeys(AS.source_word(s) for s in sources)) or "not named") +
                             (f" ({', '.join(words)})" if words else ""))
        cls = row_class.get(str(i), row_class.get(i))
        if cls in ROW_CLASS_WORDS:
            parts.append(f"Row support: {ROW_CLASS_WORDS[cls]}")
        if not parts:                                    # never skipped: line n must stay row n
            parts.append("The row holds no value the summary can read")
        lid = "row:" + (",".join(eids) if eids else _hash(json.dumps(row, sort_keys=True, default=str)))
        out.append(_line(i, lid, "row", str(i), ". ".join(p.rstrip(".") for p in parts) + ".", names))
    return out


# ------------------------------------------------------------------ lines of a trial report
def claim_lines(report, max_lines=MAX_LINES):
    """Takes a trial report (sections with claims). Returns one line per claim for the first `max_lines` claims in report
    order; line n is the n-th claim of the report, as the page numbers [n] in the summary, and ref is the claim number
    the page shows next to it ('1.2' = section 1, claim 2). Rejected claims are not read (they are not part of the
    answer). The text keeps [n] as written: the page turns [n] into a link to row n or claim n."""
    out = []
    for ref, section, c in AS._claims(report):
        if len(out) >= max_lines:
            break
        text = AS.strip_arm_codes(str(c.get("claim") or "")).strip() or "The claim has no text"   # never skipped: [n] = claim n
        ev = c.get("evidence") or []
        sources = list(dict.fromkeys(AS.source_word(e.get("source")) for e in ev if e.get("source")))
        kinds = list(dict.fromkeys(_words(e.get("evidence_type")) for e in ev if e.get("evidence_type")))
        bits = []
        if section.get("title"):
            bits.append(f"section: {_clean(section['title'], 80)}")
        bits.append("sources: " + (", ".join(sources[:5]) if sources else "none cited"))
        if kinds:
            bits.append("record types: " + ", ".join(kinds[:4]))
        if c.get("evidence_status"):
            bits.append("evidence status: " + STATUS_WORDS.get(c["evidence_status"], _words(c["evidence_status"])))
        strength = (c.get("indicators") or {}).get("evidence_strength")
        if strength:
            bits.append(f"evidence strength: {_words(strength)}")
        if c.get("support_status"):
            bits.append("check: " + SUPPORT_WORDS.get(c["support_status"], _words(c["support_status"])))
        lid = f"claim:{ref}:" + ",".join(str(e.get("id")) for e in ev if e.get("id"))
        out.append(_line(len(out) + 1, lid, "claim", ref, f"{text.rstrip('.')}. ({'; '.join(bits)}).", sources[:5]))
    return out


# ------------------------------------------------------------------ lines of an entity card and of a link's drawer
def _card_row_line(n, r):
    text = r.get("sentence") or _statement(dict(subject_label=r.get("subject"), object_label=r.get("other_label"),
                                                 predicate=r.get("predicate"), phrase=r.get("phrase")))
    bits = [f"source: {AS.source_word(r.get('source')) or 'not named'}"]
    if r.get("kind"):
        bits.append(f"evidence kind: {r['kind']}")
    if _strength(r) != r.get("kind"):
        bits.append(_strength(r))
    v = _verdict(r.get("verdict") or r.get("word"), r.get("confidence"))
    bits.append(v or "not scored by the pruning check")
    if potency_words(r.get("potency")):
        bits.append(potency_words(r.get("potency")))
    return _line(n, f"link:{r.get('id')}", "link", r.get("id"), f"{_clean(text, 200).rstrip('.')}. ({'; '.join(bits)}).",
                 [AS.source_word(r.get("source"))])


def card_lines(card, max_lines=MAX_LINES):
    """Takes an entity card. Returns its known links as lines, one evidence kind after the other in turn (row 1 of every
    kind, then row 2, ...) so that every kind is read, then at most MAX_PREDICTED predicted links (novel ones), each said
    to be a prediction with its score; at most `max_lines` lines in all."""
    about = card.get("about") or {}
    preds = [p for p in (card.get("predicted") or []) if isinstance(p, dict)][:MAX_PREDICTED]
    room = max(0, max_lines - len(preds))
    queues = [[r for r in (sec.get("rows") or []) if isinstance(r, dict)] for sec in card.get("known") or []]
    picked, depth = [], 0
    while len(picked) < room and any(depth < len(q) for q in queues):
        for q in queues:
            if depth < len(q) and len(picked) < room:
                picked.append(q[depth])
        depth += 1
    out = [_card_row_line(i, r) for i, r in enumerate(picked, 1)]
    for p in preds:
        name = about.get("label") or about.get("id")
        bits = ["a prediction of the link-prediction model, not a source statement"]
        if AS._float(p.get("score")) is not None:
            bits.append(f"score {AS._float(p['score']):.3f}")
        if p.get("rank"):
            bits.append(f"rank {p['rank']} of the new links shown")
        if p.get("broad_category"):
            bits.append("a broad disease category")
        other = p.get("other_label") or p.get("other")
        out.append(_line(len(out) + 1, f"pred:{p.get('subject')}|{p.get('object')}", "prediction", None,
                         f"Predicted link between {name} and {other} ({'; '.join(bits)})."))
    return out[:max_lines]


_ARROW = re.compile(r"^(?P<s>.+?) -(?P<p>[\w:]+)-> (?P<o>.+)$")


def _arrow_words(statement, predicate=None):
    """'PTGS2 -gene_associated_with_condition-> Alzheimer disease' -> 'PTGS2 is associated with Alzheimer disease'."""
    m = _ARROW.match(str(statement or ""))
    if not m:
        return _clean(statement, 200)
    phrase = AS._personas.predicate_phrase(predicate or ("biolink:" + m.group("p")))
    return _clean(f"{m.group('s')} {phrase} {m.group('o')}", 200)


def edge_question(explain):
    """The question a link's drawer answers, in words: 'Why does the graph hold the link PTGS2 is associated with ...?'."""
    return f"Why does the graph hold the link '{_drawer_statement(explain)}', and how strong is it?"


def _drawer_statement(explain):
    s, o = (explain.get("subject") or {}), (explain.get("object") or {})
    phrase = AS._personas.predicate_phrase(explain.get("predicate") or "")
    if s.get("name") and o.get("name") and phrase:
        return _clean(f"{s['name']} {phrase} {o['name']}", 200)
    return _arrow_words(explain.get("statement"), explain.get("predicate"))


def edge_bottom_line(explain):
    """One sentence from the drawer's own fields: the link, its source and strength, and the pruning verdict."""
    prov, v = explain.get("provenance") or {}, explain.get("verdict") or {}
    fact = dict(knowledge_level=prov.get("knowledge_level"), kind=AS._personas.evidence_kind(prov))
    bits = [AS.source_word(prov.get("source")) or "source not named", _strength(fact)]
    verdict = _verdict(v.get("verdict"), v.get("confidence"))
    bits.append(verdict or "not scored by the pruning check")
    return f"{_drawer_statement(explain)}: {', '.join(bits)}."


def edge_lines(explain, max_lines=MAX_LINES):
    """Takes a link's drawer (explain_edge). Returns lines: the link with its source, strength, pruning verdict and reasons;
    the values the source gives for it; every other record of the same pair; the timeline summary (only fields that are
    there; no empty field is written)."""
    prov, v = explain.get("provenance") or {}, explain.get("verdict") or {}
    eid = explain.get("id")
    out = []
    bits = [f"source: {AS.source_word(prov.get('source')) or 'not named'}",
            _strength(dict(knowledge_level=prov.get("knowledge_level"), kind=AS._personas.evidence_kind(prov)))]
    verdict = _verdict(v.get("verdict"), v.get("confidence"))
    bits.append(verdict or "not scored by the pruning check")
    if v.get("reasons"):
        bits.append("pruning reasons: " + AS.reasons_words(v["reasons"]))
    if AS._float(prov.get("n_records")):
        bits.append(f"{int(AS._float(prov['n_records']))} source record(s)")
    out.append(_line(1, f"link:{eid}", "link", eid, f"{_drawer_statement(explain)}. ({'; '.join(bits)}).",
                     [AS.source_word(prov.get("source"))]))
    props = [(k, val) for k, val in (explain.get("properties") or {}).items()
             if val not in (None, "") and not re.search(r"(^id$|_id$|path|file|url)", str(k)) and len(str(val)) <= 80
             and "/" not in str(val)]
    if props:
        out.append(_line(len(out) + 1, f"values:{eid}", "values", eid,
                         "Values the source gives for this link: " +
                         "; ".join(f"{_words(k)} {AS.display_value(val)}" for k, val in props[:8]) + "."))
    for c in explain.get("corroboration") or []:
        if len(out) >= max_lines - 1:
            break
        text = _arrow_words(c.get("statement"), c.get("predicate"))
        cb = [f"source: {AS.source_word(c.get('source')) or 'not named'}", _strength(c)]
        cv = _verdict(c.get("verdict"), c.get("confidence"))
        cb.append(cv or "not scored by the pruning check")
        if potency_words(c.get("potency")):
            cb.append(potency_words(c.get("potency")))
        out.append(_line(len(out) + 1, f"link:{c.get('id')}", "link", c.get("id"),
                         f"Another record of the same pair: {text}. ({'; '.join(cb)}).", [AS.source_word(c.get("source"))]))
    ts = ((explain.get("timeline") or {}).get("summary") or {})
    tl = []
    if ts.get("first_seen"):
        tl.append(f"first seen {ts['first_seen']}")
    if ts.get("last_confirmed"):
        tl.append(f"last confirmed {ts['last_confirmed']}")
    if ts.get("n_supporting") is not None or ts.get("n_contradicting") is not None:
        tl.append(f"{int(ts.get('n_supporting') or 0)} supporting and {int(ts.get('n_contradicting') or 0)} contradicting records")
    if ts.get("trend") and ts["trend"] != "unknown":
        tl.append(f"trend {_words(ts['trend'])}")
    if tl and len(out) < max_lines:
        out.append(_line(len(out) + 1, f"timeline:{eid}", "timeline", eid, "Timeline of the records: " + "; ".join(tl) + "."))
    return out


def card_question(card):
    """The question an entity card answers: 'What does the graph hold about PTGS2 (gene)?'."""
    about = card.get("about") or {}
    name = about.get("label") or about.get("id")
    return f"What does the graph hold about {name}" + (f" ({about['type_word']})" if about.get("type_word") else "") + "?"


def card_bottom_line(card):
    """One sentence computed from the card's own counts: what the entity is, its links and the pruning verdicts."""
    about, s = card.get("about") or {}, card.get("summary") or {}
    name = about.get("label") or about.get("id")
    parts = [f"{name} is a {about['type_word']}." if about.get("type_word") else ""]
    if s.get("known") is not None:
        kinds = s.get("kinds")
        parts.append(f"The graph holds {int(s['known']):,} link{'s' if int(s['known']) != 1 else ''}" +
                     (f" of {int(kinds)} kind{'s' if int(kinds) != 1 else ''}" if kinds is not None else "") +
                     (f"; pruning kept {int(s['kept']):,} and pruned {int(s['pruned']):,}" if s.get("kept") is not None
                      and s.get("pruned") is not None else "") + ".")
    if s.get("predicted"):
        parts.append(f"{int(s['predicted'])} new link{'s are' if int(s['predicted']) != 1 else ' is'} predicted.")
    return " ".join(p for p in parts if p)


# ------------------------------------------------------------------ the prompt, the reply and its checks
def numbered(lines):
    """The lines as the model reads them: '[1] ...' one per line."""
    return "\n".join(f"[{ln['n']}] {ln['text']}" for ln in lines)


def prompt(question, bottom_line, lines):
    """Takes the question, the bottom line the page already shows and the lines. Returns the one user message."""
    head = [f"Question: {_clean(question, 500)}"]
    if bottom_line:
        head.append(f"Bottom line already shown to the reader: {_clean(bottom_line, 600)}")
    return ("\n".join(head) + "\n\nNumbered lines:\n" + numbered(lines) + "\n\n" + INSTRUCTION +
            " Reply with the summary text only.")


_BRACKET = re.compile(r"\[([^\[\]]{1,40})\]")
_NUMERIC = re.compile(r"^\s*(?:lines?\s+)?\d+(?:\s*(?:[-–,;]|and|to)\s*\d+)*\s*$", re.I)


def references(text):
    """Takes a reply. Returns every line number it cites, in order ([2-4] -> 2, 3, 4; [1, 3] -> 1, 3). Brackets that hold
    no number ([Ca2+]) are not citations and are skipped."""
    out = []
    for m in _BRACKET.finditer(text or ""):
        inner = m.group(1)
        if not _NUMERIC.match(inner):
            continue
        inner = re.sub(r"(?i)lines?", "", inner)
        for part in re.split(r"\s*(?:,|;|and)\s*", inner.strip()):
            r = re.match(r"^\s*(\d+)\s*(?:[-–]|to)\s*(\d+)\s*$", part)
            if r:
                a, b = int(r.group(1)), int(r.group(2))
                out.extend(range(min(a, b), max(a, b) + 1) if abs(b - a) <= 100 else [a, b])
            elif part.strip().isdigit():
                out.append(int(part))
    return out


def clean_reply(text):
    """The model's reply as one paragraph: hidden reasoning (<think>...</think>), a leading 'Summary:' label, markdown
    emphasis and line breaks removed."""
    t = re.sub(r"(?is)<think>.*?</think>", " ", str(text or ""))
    t = re.sub(r"(?i)^\s*(\*\*)?summary(\*\*)?\s*:\s*", "", t.strip())
    t = t.replace("**", "").replace("__", "")
    return " ".join(t.split())


_ABBR = re.compile(r"(?:\b(?:e\.g|i\.e|vs|cf|approx|incl|et al|Fig|No|Dr|St)\.)$", re.I)


def split_sentences(text):
    """Takes a paragraph. Returns its sentences. A break is a '.', '!' or '?' followed by a space and a capital, a digit,
    '[' or '('; abbreviations (e.g., i.e., vs., et al.) do not break; a piece that is only citations ('[3].') is joined to
    the sentence before it."""
    pieces = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\[(\"'])", (text or "").strip())
    out = []
    for p in pieces:
        p = p.strip()
        if not p:
            continue
        if out and (_ABBR.search(out[-1]) or re.fullmatch(r"(?:\[[^\[\]]{1,40}\]\s*[.,;]?\s*)+", p)):
            out[-1] = out[-1] + " " + p
        else:
            out.append(p)
    return out


def _check_message(sentence, lines):
    return ("Numbered lines (the only evidence):\n" + numbered(lines) + "\n\nSentence: " + sentence + "\n\n"
            "Do the numbered lines support every statement in the sentence? A statement that the lines do not show "
            "something is supported when none of the lines shows it. A fact, number, name or judgement that is not in "
            "the lines is not supported. Reply with JSON: {\"supported\": true or false, \"reason\": \"a few words\"}.")


# ------------------------------------------------------------------ FIX3: three fixed rules, no model call (Gate 3)
_PREDICT = re.compile(r"\bpredict(?:s|ed|ing|ion|ions|ive|or|ors)?\b", re.I)
# The lines' own words for a row class (ROW_CLASS_WORDS['text-mined']) and the bottom line's caveat: quoting them is
# not calling a link a prediction.
_LINE_PREDICT_WORDS = ("text-mined or predicted", "text-mined (or predicted)")
# A predict word said of nothing ('the lines show no statistical, text-mined or predicted links', 'none of the lines is a
# prediction'): a noun or adjective form (never the verb 'predicts' / 'predicting'), a negation at most ten words before it
# in the same clause (commas of a list do not end it; ; : ( ) and 'but / only / except / rather / while ...' do), and not
# 'any / the / these ...' just before it ('for any predicted disease' presumes that predictions exist).
_NEGATED = re.compile(r"(?:no|not|none|neither|nor|never|without|cannot)", re.I)
_PRESUMES = {"any", "the", "these", "those", "each", "every", "all", "its", "their", "new", "some"}
_CLAUSE_END = {";", ":", "(", ")", "but", "only", "except", "rather", "other", "than", "while", "whereas", "although",
               "though", "yet", "because", "so"}
_NUMBER = re.compile(r"(?<![\w.,\-])\d+(?:[.,]\d+)*(?![\w])")


def _cited(sentence, lines):
    """The lines a sentence cites ([n] that are line numbers), in order; [] when it cites none."""
    by_n = {ln["n"]: ln for ln in lines}
    return [by_n[n] for n in dict.fromkeys(references(sentence)) if n in by_n]


def _no_citations(text):
    """The text without its [n] citations (brackets that hold no line number, such as [Ca2+], stay)."""
    return _BRACKET.sub(lambda m: " " if _NUMERIC.match(m.group(1)) else m.group(0), str(text or ""))


def _without(text, phrases):
    """The text with every phrase removed (case ignored; a space or a hyphen between the words of a phrase matches either)."""
    for ph in sorted({p for p in phrases if p}, key=len, reverse=True):
        words = [w for w in re.split(r"[\s\-]+", str(ph).strip()) if w]
        if words:
            text = re.sub(r"(?<![\w-])" + r"[\s\-]+".join(re.escape(w) for w in words) + r"(?![\w-])", " ", text, flags=re.I)
    return text


def _numbers(text):
    """The numbers a text states, thousands commas taken out ('2,033' -> '2033'); words such as BACE1 or IL-6 hold none."""
    return [m.group(0).replace(",", "") if re.fullmatch(r"\d{1,3}(?:,\d{3})+", m.group(0)) else m.group(0)
            for m in _NUMBER.finditer(text)]


def _name_forms(name):
    """A source name and its shorter forms that end in a number ('Open Targets 26.09 overall direct' -> also 'Open Targets
    26.09'): a sentence that names the source that way states no value."""
    words = str(name or "").split()
    return [str(name)] + [" ".join(words[:i + 1]) for i, w in enumerate(words[:-1]) if i and _NUMBER.fullmatch(w)]


def _negated(text, match):
    """True when the predict word at `match` is said of nothing (see _NEGATED above)."""
    if match.group(0).lower() in ("predict", "predicts", "predicting"):
        return False
    words = [w.lower() for w in re.findall(r"[\w'-]+|[;:()]", text[:match.start()])]
    if words and words[-1] in _PRESUMES:
        return False
    for w in reversed(words[-11:]):
        if w in _CLAUSE_END:
            return False
        if _NEGATED.fullmatch(w) or w.endswith("n't"):
            return True
    return False


def _rule_prediction(sentence, cited, lines):
    """F1 (MD-1): a sentence that says predict / predicted / prediction(s) is dropped unless a line it cites is a prediction;
    a sentence that cites nothing is dropped when no line of the answer is a prediction. Not counted: the lines' own words
    'text-mined or predicted' and a predict word said of nothing ('no statistical or predicted links'). Returns a reason
    or None."""
    scope = cited or lines
    quoted = [w for w in _LINE_PREDICT_WORDS if any(w in ln["text"] for ln in scope)]
    text = _without(sentence, quoted)
    if all(_negated(text, m) for m in _PREDICT.finditer(text)):         # no predict word, or each one said of nothing
        return None
    if any(ln.get("kind") == "prediction" for ln in scope):
        return None
    return ("speaks of predictions, but none of the lines it cites is a prediction" if cited else
            "speaks of predictions, but no line of this answer is a prediction")


def _rule_source_number(sentence, cited, lines):
    """F2 (G3-01): a sentence that states a number which its lines hold only inside a source name (the release in 'Open
    Targets 26.09 overall direct') is dropped: a release or version number is not a score. Returns a reason or None."""
    scope = cited or lines
    names = [f for ln in scope for n in (ln.get("sources") or []) for f in _name_forms(n)]
    if not names:
        return None
    said = _numbers(_without(_no_citations(sentence), names))
    if not said:
        return None
    anywhere = {x for ln in scope for x in _numbers(_no_citations(ln["text"]))}
    as_value = {x for ln in scope for x in _numbers(_without(_no_citations(ln["text"]), names))}
    only_name = [x for x in dict.fromkeys(said) if x in anywhere and x not in as_value]
    if not only_name:
        return None
    return f"states {only_name[0]}, which the lines hold only as part of a source name, not as a value"


# Words of a question that join two names ('What links EGFR to ...' gives the phrase 'links EGFR'): a phrase whose extra words
# are only these names no other term, so the writer is not told of it and the sentence check does not count it.
_JOINING_WORDS = {"link", "links", "connect", "connects", "relate", "relates", "tie", "ties", "cause", "causes", "treat",
                  "treats", "regulate", "regulates", "affect", "affects", "explain", "explains", "make", "makes", "has",
                  "have", "had", "mean", "means", "drive", "drives", "underlie", "underlies"}


def _read_as_terms(read_as):
    """The (phrase, label) pairs of read_as where the question named another term: the phrase is not the label (case
    ignored) and its words beyond the label are not only joining words ('links EGFR' read as EGFR names no other term)."""
    out = []
    for r in read_as or []:
        if not isinstance(r, dict):
            continue
        phrase, label = str(r.get("phrase") or "").strip(), str(r.get("label") or "").strip()
        if not phrase or not label or phrase.lower() == label.lower():
            continue
        extra = [w for w in _without(phrase, [label]).lower().split() if w]
        if extra and all(w in _JOINING_WORDS for w in extra):
            continue
        out.append((phrase, label))
    return out


def _rule_read_as(sentence, lines, read_as):
    """F3 (G3-08): where the answer read the user's phrase as another term ('non-small cell lung carcinoma' read as lung
    carcinoma), a sentence that names the phrase is dropped, also when it names the term it was read as too ('EGFR is linked
    to non-small cell lung carcinoma through lung carcinoma'); the one way to name the phrase is 'the question named <phrase>'.
    A phrase the lines themselves name is not counted. Returns a reason or None."""
    texts = " ".join(ln["text"] for ln in lines)
    for phrase, label in _read_as_terms(read_as):
        if _without(texts, [phrase]) != texts:                       # the lines name the phrase: naming it is fine
            continue
        base = _without(sentence, [f"the question named {phrase}"])  # allowed: the phrase as what the question said
        if _without(base, [phrase]) != base:
            if _without(base, [label]) == base:
                return f"names '{phrase}', but the answer was read as {label}"
            return f"names '{phrase}' as linked, but the answer was read as {label}"
    return None


def rule_check(sentences, lines, read_as=None):
    """FIX3 (Gate 3). Takes the sentences of a reply, the numbered lines and the terms the answer was read as
    ([{phrase, label}] as pharos_app.read_as gives them; None for other answers). Applies the three fixed rules, in order:
    F1 prediction words only over prediction lines, F2 no number that is only part of a source name, F3 the resolved term,
    not the user's phrase. Returns (kept sentences, dropped [{sentence, reason, rule}]); no model is called."""
    kept, dropped = [], []
    for s in sentences:
        refusal = _refusal(s, lines, read_as)
        if refusal:
            dropped.append(refusal)
        else:
            kept.append(s)
    return kept, dropped


def _refusal(sentence, lines, read_as=None):
    """The first fixed rule a sentence breaks, as {sentence, reason, rule}; None when it breaks none."""
    cited = _cited(sentence, lines)
    for rule, test in (("prediction", lambda: _rule_prediction(sentence, cited, lines)),
                       ("source_number", lambda: _rule_source_number(sentence, cited, lines)),
                       ("read_as", lambda: _rule_read_as(sentence, lines, read_as))):
        why = test()
        if why:
            return dict(sentence=sentence, reason=why, rule=rule)
    return None


def read_as_note(read_as):
    """FIX3 F3. Takes the terms the answer was read as ([{phrase, label}]). Returns the sentence the writer is given after the
    bottom line, 'Read as lung carcinoma (the question named non-small cell lung carcinoma).', or '' when there is none."""
    bits = [f"{_clean(label, 120)} (the question named {_clean(phrase, 120)})" for phrase, label in _read_as_terms(read_as)]
    return ("Read as " + "; ".join(bits) + ".") if bits else ""


def check_sentences(sentences, lines, client, read_as=None):
    """Takes the sentences of a reply, the numbered lines, a model client and the terms the answer was read as. First drops
    the sentences the fixed rules refuse (rule_check, no model call), then passes each other sentence with the lines it
    cites (all lines when it cites none) to the verifier model. Returns (kept sentences, dropped [{sentence, reason}]) in
    reply order, or None when the check could not be made for some sentence (no hosted answer, a reply that is not the
    JSON asked for, no time left): a summary is only shown when every sentence was checked."""
    by_n = {ln["n"]: ln for ln in lines}
    kept, dropped = [], []
    for s in sentences:
        refusal = _refusal(s, lines, read_as)
        if refusal:
            dropped.append(refusal)
            continue
        cited = [by_n[n] for n in dict.fromkeys(references(s)) if n in by_n]
        try:
            res = client.chat(CHECK_ROLE, [{"role": "user", "content": _check_message(s, cited or lines)}], schema=CHECK_SCHEMA)
        except Exception:
            return None
        if res.backend != "nim" or not isinstance(res.data, dict) or not isinstance(res.data.get("supported"), bool):
            return None
        if res.data["supported"]:
            kept.append(s)
        else:
            dropped.append(dict(sentence=s, reason=_clean(res.data.get("reason") or "", 160)))
    return kept, dropped


def _web_client(seconds):
    import nim
    return nim.web_client(seconds)


def summarise(question, bottom_line, lines, client=None, check=True, check_client=None, read_as=None):
    """Takes the question, the bottom line the page shows, the numbered lines (at most MAX_LINES are read) and optionally
    the model clients (default: nim.web_client(DEADLINE) each, one for writing and one for the sentence checks). Makes ONE
    writer call with prompt(...) and, with check=True, one verifier call per sentence. Returns
    {text, model, created, checked_by, raw, sentences, dropped [{sentence, reason}]} or None on any failure: no lines, the
    offline backend (a stand-in never writes a summary), an exception or timeout, an answer from the stand-in, an empty
    reply, more than MAX_WORDS words, a [n] outside the lines, a check that could not run, or fewer than MIN_SENTENCES
    sentences left after the unsupported ones are dropped."""
    lines = list(lines or [])[:MAX_LINES]
    if not lines or not enabled():
        return None
    try:
        client = client if client is not None else _web_client(DEADLINE)
        if getattr(client, "backend", "offline") != "nim":
            return None
        res = client.chat(WRITER_ROLE, [{"role": "user", "content": prompt(question, bottom_line, lines)}])
    except Exception:
        return None
    if res.backend != "nim":
        return None
    text = clean_reply(res.text)
    if not text or len(text.split()) > MAX_WORDS:
        return None
    refs = references(text)
    if any(n < 1 or n > len(lines) for n in refs):
        return None
    sentences = split_sentences(text)
    checked_by, dropped = None, []
    if check:
        try:
            checker = check_client if check_client is not None else _web_client(DEADLINE)
        except Exception:
            return None
        if getattr(checker, "backend", "offline") != "nim":
            return None
        result = check_sentences(sentences, lines, checker, read_as=read_as)
        if result is None:
            return None
        sentences, dropped = result
        checked_by = checker.model(CHECK_ROLE)
    else:
        sentences, dropped = rule_check(sentences, lines, read_as)          # FIX3: the fixed rules need no model
    if len(sentences) < MIN_SENTENCES:
        return None
    return dict(text=" ".join(sentences), model=res.model, created=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                checked_by=checked_by, raw=text, sentences=len(sentences), dropped=dropped)


# ------------------------------------------------------------------ the cache: one JSON file per answer
_memory = OrderedDict()                 # key -> record, when the cache folder cannot be written (a read-only data root)
_memory_lock = threading.Lock()
MEMORY_MAX = 500


def norm_question(question):
    """The question as the key reads it: lower case, single spaces, no final '?' (a typed tested question and its pill
    share one summary)."""
    return " ".join(str(question or "").lower().split()).rstrip(" ?")


def writer_model(client=None):
    """The configured writer model id (config roles.querier): the id the key uses, also when a backup model answered."""
    if client is not None and hasattr(client, "model"):
        return client.model(WRITER_ROLE)
    return _configured_roles()[WRITER_ROLE]


_roles = {}


def _configured_roles():
    """config/nim_models.yaml roles, read once per process (the card endpoint asks for the writer id on every card)."""
    if "roles" not in _roles:
        import yaml
        _roles["roles"] = yaml.safe_load((ROOT / "config" / "nim_models.yaml").read_text())["roles"]
    return _roles["roles"]


def cache_key(question, lines, model):
    """Takes the question, the lines and the writer model id. Returns 16 hex characters of the SHA-256 of
    (question as norm_question reads it, the line ids in order, the model id, PROMPT_VERSION)."""
    canon = json.dumps([norm_question(question), [ln["id"] for ln in lines[:MAX_LINES]], model, PROMPT_VERSION],
                       separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def folders_for(data_root=None):
    """Takes the app's data root. Returns the folders read for a cached summary, first match wins: TMKG_SUMMARY_DIR when
    set, <data root>/outputs/summaries, then the committed FOLDER of this repository."""
    out = []
    if os.environ.get("TMKG_SUMMARY_DIR"):
        out.append(Path(os.environ["TMKG_SUMMARY_DIR"]))
    if data_root is not None:
        out.append(Path(data_root) / SUBFOLDER)
    out.append(FOLDER)
    return list(dict.fromkeys(out))


def cached(key, folders):
    """Takes a key and the folders to read. Returns the record kept for it (a file, else this process's memory), or None."""
    if not re.fullmatch(r"[0-9a-f]{16}", str(key or "")):
        return None
    for f in folders:
        p = Path(f) / f"{key}.json"
        try:
            if p.is_file():
                d = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(d, dict) and d.get("text"):
                    return d
        except (OSError, ValueError):
            continue
    with _memory_lock:
        d = _memory.get(key)
        if d is not None:
            _memory.move_to_end(key)
        return d


def store(record, folder):
    """Takes a record (with its key) and the folder to write. Writes <folder>/<key>.json (atomically). When the folder
    cannot be written (the hosted data root is read-only) the record is kept in this process's memory instead.
    Returns the path written, or None when only memory was used."""
    key = record["key"]
    try:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{key}.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, folder / f"{key}.json")
        return folder / f"{key}.json"
    except OSError:
        with _memory_lock:
            _memory[key] = record
            _memory.move_to_end(key)
            while len(_memory) > MEMORY_MAX:
                _memory.popitem(last=False)
        return None


def summary_for(question, bottom_line, lines, folders, write_folder=None, client=None, call=True, check_client=None,
                read_as=None):
    """Takes the question, the bottom line, the lines, the folders to read, the folder to write (default: the first of
    `folders`), optionally the model clients, whether a model may be called and the terms the answer was read as (FIX3 F3:
    the sentence check reads them; they are not part of the key). Returns the cached record for this
    (question, lines, model), else, with call=True, the record summarise() writes now (kept for the next visitor), else
    None. A record is {key, question, bottom_line, lines (the numbered text, for proofreading), text, model, created,
    checked_by, raw, sentences, dropped, prompt_version}."""
    lines = list(lines or [])[:MAX_LINES]
    if not lines or not enabled():
        return None
    model = writer_model(client)
    key = cache_key(question, lines, model)
    hit = cached(key, folders)
    if hit is not None:
        return dict(hit, from_cache=True)
    if not call:
        return None
    out = summarise(question, bottom_line, lines, client=client, check_client=check_client, read_as=read_as)
    if out is None:
        return None
    record = dict(out, key=key, question=question, bottom_line=bottom_line, prompt_version=PROMPT_VERSION,
                  lines=[f"[{ln['n']}] {ln['text']}" for ln in lines])
    store(record, write_folder or list(folders)[0])
    return dict(record, from_cache=False)


# ------------------------------------------------------------------ what the response carries
def label_sentence(record, lines):
    """The sentence under the summary, computed from the record: what it was written from and what the check removed."""
    kinds = {ln["kind"] for ln in lines}
    unit = UNIT["claim"] if kinds == {"claim"} else UNIT["row"] if kinds == {"row"} else UNIT["link"]
    n = len(lines)
    s = (f"Summary written by a language model from the {unit[1] if n != 1 else unit[0]} below "
         f"({n} {unit[1] if n != 1 else unit[0]} read)")
    if record.get("checked_by"):
        s += "; a second model checked each sentence against the " + unit[1] + " it cites"
        k = len(record.get("dropped") or [])
        if k:
            s += f", and {k} sentence{'s' if k != 1 else ''} it did not find supported {'were' if k != 1 else 'was'} removed"
    return s + "."


def response_keys(record, lines):
    """Takes a record (or None) and the lines it was written from. Returns {summary_text, summary_meta} or {} when there is
    no summary (the keys are then absent and the page shows the bottom line only). summary_meta: {model, created,
    checked_by, sentences, removed, label, lines [{n, kind, ref}], stored (True when read from the cache)}."""
    if not record or not record.get("text"):
        return {}
    lines = list(lines or [])[:MAX_LINES]
    meta = dict(model=record.get("model"), created=record.get("created"), checked_by=record.get("checked_by"),
                sentences=record.get("sentences"), removed=len(record.get("dropped") or []),
                label=label_sentence(record, lines), stored=bool(record.get("from_cache")),
                lines=[dict(n=ln["n"], kind=ln["kind"], ref=ln["ref"]) for ln in lines])
    return dict(summary_text=record["text"], summary_meta=meta)


def attach(out, record, lines):
    """Takes a response dict, a record (or None) and its lines. Returns a copy with summary_text / summary_meta added (none
    added when record is None; keys already there from an older call are removed then)."""
    out = {k: v for k, v in dict(out).items() if k not in ("summary_text", "summary_meta")}
    out.update(response_keys(record, lines))
    return out


def bottom_line(answer):
    """The headline of an answer's bottom line (answer['summary']['headline']), '' when there is none."""
    s = answer.get("summary") if isinstance(answer, dict) else None
    return str(s.get("headline") or "") if isinstance(s, dict) and not s.get("error") else ""
