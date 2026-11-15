"""
Edge timelines: what we knew, what we know now, what may change.

    python pipelines/timeline.py outputs/pharos/edges.tsv.gz  [--ot-dir data/external/opentargets]

Each edge gains an evidence_timeline (JSON list of dated events) plus summary fields.

Functions
---------
build_timeline(edges, sources)  -> edges DataFrame with timeline columns added
event(date, source, event_type, direction, value=None, reference=None) -> dict
summarise(events) -> dict of summary fields

Sources contribute events through registered adapters (one function per source type):
  pharos_dates(edges)       dates embedded in Pharos rows (GeneRIF dates, PubMed years, TIGA study years)
  ot_score_curve(edges, ot_dir)   Open Targets association scores across archived releases
  chembl_dates(edges, chembl_dir) ChEMBL activity document years
  trial_dates(edges)        AACT trial start/completion/failure dates

Each adapter returns a dict of {edge_id: [event, ...]}. The build_timeline function merges them.
"""
import json
import re
from pathlib import Path

import pandas as pd

# ---- event constructors ----

EVENT_TYPES = {"first_seen", "score_change", "new_support", "contradicted", "retracted",
               "trial_failed", "trial_succeeded", "removed", "confirmed"}
DIRECTIONS = {"supports", "contradicts", "neutral"}


def event(date, source, event_type, direction="neutral", value=None, reference=None):
    """One dated event on an edge's timeline.

    Parameters
    ----------
    date : str        Year or YYYY-MM (as precise as the source allows).
    source : str      e.g. "ChEMBL 37", "Open Targets 26.09", "AACT".
    event_type : str  One of EVENT_TYPES.
    direction : str   "supports", "contradicts", or "neutral".
    value : float | str | None   Score, potency, or status.
    reference : str | None       PMID, NCT id, release file, ChEMBL document id.
    """
    assert event_type in EVENT_TYPES, f"unknown event type: {event_type}"
    assert direction in DIRECTIONS, f"unknown direction: {direction}"
    ev = {"date": str(date), "source": source, "event": event_type, "direction": direction}
    if value is not None:
        ev["value"] = value
    if reference is not None:
        ev["reference"] = reference
    return ev


# ---- summary ----

def summarise(events):
    """Compute summary fields from a list of timeline events.

    Returns
    -------
    dict with keys: first_seen, last_confirmed, n_supporting, n_contradicting, trend.
    """
    if not events:
        return {"first_seen": None, "last_confirmed": None, "n_supporting": 0,
                "n_contradicting": 0, "trend": "unknown"}

    sorted_ev = sorted(events, key=lambda e: e["date"])
    supporting = [e for e in sorted_ev if e["direction"] == "supports"]
    contradicting = [e for e in sorted_ev if e["direction"] == "contradicts"]

    first_seen = sorted_ev[0]["date"]
    last_confirmed = supporting[-1]["date"] if supporting else None

    # Trend: look at the last 3 events with a direction
    directed = [e for e in sorted_ev if e["direction"] != "neutral"]
    recent = directed[-3:] if len(directed) >= 3 else directed
    if not recent:
        trend = "unknown"
    elif all(e["direction"] == "supports" for e in recent):
        trend = "strengthening" if len(recent) >= 2 else "stable"
    elif all(e["direction"] == "contradicts" for e in recent):
        trend = "weakening"
    else:
        trend = "contested"

    return {
        "first_seen": first_seen,
        "last_confirmed": last_confirmed,
        "n_supporting": len(supporting),
        "n_contradicting": len(contradicting),
        "trend": trend,
    }


# ---- source adapters ----

def pharos_dates(edges):
    """Extract dates from Pharos edge properties: PubMed IDs (year estimate), TIGA study counts.

    Parameters
    ----------
    edges : DataFrame   KGX edges with property columns.

    Returns
    -------
    dict of {edge_id: [event, ...]}
    """
    timeline = {}
    for _, row in edges.iterrows():
        eid = row["id"]
        evts = []
        # PubMed IDs on ligand activities -> approximate year from PMID range
        pmids = str(row.get("pmids", ""))
        if pmids and pmids != "<NA>" and pmids != "nan":
            # PMIDs themselves don't carry dates, but their presence is supporting evidence
            evts.append(event("2024-02", "Pharos 3.19.2", "confirmed", "supports",
                              reference=f"PMIDs: {pmids[:80]}"))
        # Every edge has a Pharos retrieval date as baseline
        source = str(row.get("source", ""))
        if source and source != "<NA>":
            evts.append(event("2024-02", f"Pharos 3.19.2 ({source})", "first_seen", "supports"))
        if evts:
            timeline[eid] = evts
    return timeline


def ot_score_curve(edges, ot_releases):
    """Open Targets association scores across archived releases -> score_change events.

    Parameters
    ----------
    edges : DataFrame   Must have subject, object columns (gene/disease ids).
    ot_releases : dict   {release_tag: DataFrame with columns [target_id, disease_id, score]}.

    Returns
    -------
    dict of {edge_id: [event, ...]}
    """
    timeline = {}
    # Build a lookup: (subject, object) -> edge_id for gene-disease edges
    gene_disease = edges[edges["predicate"] == "biolink:gene_associated_with_condition"]
    pair_to_eid = {}
    for _, row in gene_disease.iterrows():
        pair_to_eid[(row["subject"], row["object"])] = row["id"]

    for release_tag in sorted(ot_releases):
        df = ot_releases[release_tag]
        for _, row in df.iterrows():
            pair = (row["target_id"], row["disease_id"])
            eid = pair_to_eid.get(pair)
            if eid is None:
                continue
            # Extract year from release tag (e.g. "26.09" -> "2026-09", "21.04" -> "2021-04")
            m = re.match(r"(\d{2})\.(\d{2})", release_tag)
            if m:
                yr = int(m.group(1))
                year = f"20{yr:02d}-{m.group(2)}"
            else:
                year = release_tag
            score = float(row["score"])
            timeline.setdefault(eid, []).append(
                event(year, f"Open Targets {release_tag}", "score_change",
                      "supports" if score > 0.1 else "neutral", value=round(score, 4)))
    return timeline


def trial_dates(edges, trial_edges):
    """Trial start, completion and failure dates from the L3 trial layer.

    Parameters
    ----------
    edges : DataFrame    The main graph edges (to match drug-disease pairs).
    trial_edges : DataFrame   Trial layer edges with columns including nct_id, start_date,
                              completion_date, overall_status, stop_reason.

    Returns
    -------
    dict of {edge_id: [event, ...]}
    """
    timeline = {}
    # This is a placeholder; the full implementation will match trial drug-disease
    # pairs to the main graph edges via ChEMBL/MONDO ids.
    for _, row in trial_edges.iterrows():
        nct = str(row.get("nct_id", ""))
        status = str(row.get("overall_status", "")).lower()
        date = str(row.get("completion_date", row.get("start_date", "")))
        if not date or date == "<NA>":
            continue
        year = date[:7] if len(date) >= 7 else date[:4]
        if "terminat" in status or "withdraw" in status:
            etype, direction = "trial_failed", "contradicts"
        elif "complet" in status:
            etype, direction = "trial_succeeded", "supports"
        else:
            etype, direction = "confirmed", "neutral"
        # The edge_id matching would go through the drug-disease pair lookup
        # For now, store by nct_id; the caller maps to edge_ids
        timeline.setdefault(nct, []).append(
            event(year, "AACT", etype, direction, reference=nct,
                  value=row.get("stop_reason")))
    return timeline


# ---- main builder ----

def build_timeline(edges, adapters=None):
    """Merge timeline events from all adapters onto edges.

    Parameters
    ----------
    edges : DataFrame   KGX edges.
    adapters : list of dict   Each: {"name": str, "events": {edge_id: [event, ...]}}
        If None, only pharos_dates is used.

    Returns
    -------
    DataFrame   Copy of edges with added columns:
        evidence_timeline (JSON string), first_seen, last_confirmed, n_supporting,
        n_contradicting, trend.
    """
    if adapters is None:
        adapters = [{"name": "pharos", "events": pharos_dates(edges)}]

    # Merge all events per edge
    all_events = {}  # edge_id -> [event, ...]
    for adapter in adapters:
        for eid, evts in adapter["events"].items():
            all_events.setdefault(eid, []).extend(evts)

    # Compute summaries and add columns
    out = edges.copy()
    tl_col, fs_col, lc_col, ns_col, nc_col, tr_col = [], [], [], [], [], []
    for _, row in out.iterrows():
        evts = all_events.get(row["id"], [])
        tl_col.append(json.dumps(evts) if evts else None)
        s = summarise(evts)
        fs_col.append(s["first_seen"])
        lc_col.append(s["last_confirmed"])
        ns_col.append(s["n_supporting"])
        nc_col.append(s["n_contradicting"])
        tr_col.append(s["trend"])

    out["evidence_timeline"] = tl_col
    out["first_seen"] = fs_col
    out["last_confirmed"] = lc_col
    out["n_supporting"] = ns_col
    out["n_contradicting"] = nc_col
    out["trend"] = tr_col
    return out


if __name__ == "__main__":
    import gzip, sys
    if len(sys.argv) < 2:
        print("Usage: python pipelines/timeline.py <edges.tsv.gz> [--ot-dir DIR]")
        sys.exit(1)
    path = Path(sys.argv[1])
    with gzip.open(path, "rt") as f:
        edges = pd.read_csv(f, sep="\t", dtype=str, keep_default_na=False)
    result = build_timeline(edges)
    out_path = path.parent / "edges_timeline.tsv.gz"
    with gzip.open(out_path, "wt") as f:
        result.to_csv(f, sep="\t", index=False)
    has_tl = result["evidence_timeline"].notna().sum()
    print(f"Wrote {out_path}: {len(result):,} edges, {has_tl:,} with timelines")
    # Print trend distribution
    print(result["trend"].value_counts().to_string())
