"""
Document-retrieval (RAG) baseline (P7): the same model answers from retrieved document passages, with NO graph.
This is the application's "LLM-only retrieval baseline" - the fair second comparison next to the no-context baseline.

Corpus per question = the documents of the question's program (raw/<program>_*/: registry records, abstracts,
open-access full texts as Markdown; raw_text/<program>_*/: protocol / SAP / web PDFs as text). Steps:
  1. chunk every document (CHUNK characters, OVERLAP overlap), keep the source path
  2. lexical BM25 ranking against the question -> top FIRST chunks (no model calls)
  3. NVIDIA reranker orders them -> top KEEP passages
  4. the 'baseline' model answers from those passages only, citing passage numbers
Claims are then judged by evaluate.judge() against the graph evidence, exactly like the other two systems.

Functions: program_documents(pid) -> [(path, text)];  chunks(docs) -> [dict(path, start, text)];
           bm25(question, chunks, k) -> [chunk];  answer(nim, question, pid) -> dict(claims, passages)
"""
import gzip, json, math, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[1]
CHUNK, OVERLAP, FIRST, KEEP = 1200, 200, 40, 8
SYSTEM = ("Answer the clinical drug-development question using ONLY the numbered PASSAGES. Return JSON "
          "{\"claims\": [{\"text\": \"...\", \"passages\": [numbers]}]} with at most 8 short factual claims; copy numbers "
          "exactly; if the passages do not answer, return fewer claims.")
SCHEMA = {"type": "object", "required": ["claims"], "properties": {"claims": {"type": "array", "items": {
    "type": "object", "required": ["text", "passages"], "properties": {
        "text": {"type": "string"}, "passages": {"type": "array", "items": {"type": "integer"}}}}}}}


def program_documents(pid):
    out = []
    for base, pattern in ((ROOT / "raw", "**/*.md"), (ROOT / "raw_text", "**/*.txt.gz")):
        for folder in sorted(base.glob(f"{pid}_*")):
            for f in sorted(folder.glob(pattern)):
                text = gzip.decompress(f.read_bytes()).decode("utf-8", "replace") if f.suffix == ".gz" \
                    else f.read_text(encoding="utf-8", errors="replace")
                out.append((str(f.relative_to(ROOT)), text))
    return out


def chunks(docs):
    out = []
    for path, text in docs:
        for start in range(0, max(1, len(text) - OVERLAP), CHUNK - OVERLAP):
            t = text[start:start + CHUNK].strip()
            if len(t) > 100:
                out.append(dict(path=path, start=start, text=t))
    return out


def _tok(s):
    return re.findall(r"[a-z0-9]+", s.lower())


def bm25(question, chs, k=FIRST, k1=1.5, b=0.75):
    q = [t for t in set(_tok(question)) if len(t) > 2]
    docs = [_tok(c["text"]) for c in chs]
    n, avg = len(docs), sum(map(len, docs)) / (len(docs) or 1)
    df = {t: sum(1 for d in docs if t in set(d)) for t in q}
    scores = []
    for i, d in enumerate(docs):
        tf = {}
        for t in d:
            tf[t] = tf.get(t, 0) + 1
        s = sum(math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) * tf[t] * (k1 + 1) /
                (tf[t] + k1 * (1 - b + b * len(d) / avg)) for t in q if tf.get(t))
        scores.append((-s, i))
    return [chs[i] for s, i in sorted(scores)[:k] if s < 0]


def answer(nim, question, pid):
    """-> dict(claims [dict(text, passages)], passages [dict(path, start, text)]). Raises OfflineUnavailable offline."""
    first = bm25(question, chunks(program_documents(pid)))
    if not first:
        return dict(claims=[], passages=[])
    order = nim.rerank(question, [c["text"] for c in first])
    kept = [first[i] for i, _ in order[:KEEP]]
    user = f"QUESTION: {question}\nPASSAGES:\n" + "\n".join(f"[{i + 1}] ({c['path']}) {c['text']}"
                                                         for i, c in enumerate(kept))
    r = nim.chat("baseline", [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], schema=SCHEMA)
    return dict(claims=(r.data or {}).get("claims", []), passages=kept)
