"""
Parsers for the three curated tables in data/curated/failed_clinical_trials_2016_2026.md that describe
what happened AFTER a failure. Pure functions: markdown text in, rows out. Nothing is inferred.

- strip_links(text)        -> (plain text, [urls])  '[label](url)' -> 'label'
- post_failure_rows(md)    -> rows of "How analysis after failure changed development" (+ per-drug sources)
- repurposing_rows(md)     -> rows of "Repurposing and portfolio pivots" (+ section-level sources)
- data_access_rows(md)     -> rows of "Trial-specific data-access evidence" (trial names expanded)
Every row: dict(event_id, kind, name, key (drug or trial names used for linking), props, urls,
                section_urls, row_number, section)
"""
import re

LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def strip_links(text):
    return LINK.sub(lambda m: m.group(1), text or "").strip(), [u for _, u in LINK.findall(text or "")]


def _section(md, heading):
    i = md.find(heading)
    if i < 0:
        return ""
    j = md.find("\n## ", i + len(heading))
    return md[i:j if j > 0 else len(md)]


def _table(sec):
    rows = [l for l in sec.splitlines() if l.startswith("|")][2:]
    return [[c.strip() for c in l.strip().strip("|").split("|")] for l in rows]


def _row(kind, n, title, name, key, cols, cells, section_urls=()):
    props, urls = {}, []
    for c, v in zip(cols, cells):
        props[c], u = strip_links(v)
        urls += u
    return dict(event_id=f"DEV:{kind}:{n}", kind=kind, name=name, key=key, props=props,
                urls=urls, section_urls=list(section_urls), row_number=n, section=title)


def post_failure_rows(md):
    title = "How analysis after failure changed development"
    sec = _section(md, "## " + title)
    cols = ["case", "original_problem", "analysis_showed", "pivot_decision", "outcome_evidence"]
    sources = {}                                   # '- Drug: [..](url), ..' lines below the table
    for l in sec.splitlines():
        m = re.match(r"- ([^:]+): (.*)", l)
        if m:
            sources[m.group(1).strip().lower()] = [u for _, u in LINK.findall(m.group(2))]
    out = []
    for n, cells in enumerate(_table(sec), 1):
        drug = cells[0].split(";")[0].strip()
        r = _row("post_failure", n, title, drug, [drug], cols, cells)
        r["urls"] += sources.get(drug.lower(), [])
        out.append(r)
    return out


def repurposing_rows(md):
    title = "Repurposing and portfolio pivots"
    sec = _section(md, "## " + title)
    cols = ["asset", "what_changed", "consequence", "caveat"]
    section_urls = [u for l in sec.splitlines() if l.startswith("Sources:") for _, u in LINK.findall(l)]
    return [_row("repurposing", n, title, cells[0], [cells[0]], cols, cells, section_urls)
            for n, cells in enumerate(_table(sec), 1)]


def data_access_rows(md, expand=lambda toks: toks):
    """`expand` = seed_kg.expand_trial_tokens ('GRADUATE I / II' -> GRADUATE I, GRADUATE II)."""
    title = "Trial-specific data-access evidence"
    sec = _section(md, "## " + title)
    cols = ["trials", "access_route", "limitation"]
    out = []
    for n, cells in enumerate(_table(sec), 1):
        names = cells[0].split(" — ")[0]
        trials = expand([t.strip() for t in names.split("/") if t.strip()])
        out.append(_row("data_access", n, title, cells[0], trials, cols, cells))
    return out
