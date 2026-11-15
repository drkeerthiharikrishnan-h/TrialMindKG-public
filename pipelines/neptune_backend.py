"""
The Neptune side of the app's "graph backend" switch (local | neptune): the same validated openCypher query that
querier.run_local runs on the in-memory graph is sent to the Neptune Database openCypher endpoint
(aws/neptune_create.sh, aws/neptune_load.sh) and the answer is returned in run_local's shape,

    {columns, rows: [{column: value}], evidence: [edge ids]}

so the app, the evidence drawer and the parity test do not care which backend answered. Evidence edge ids are the values of the
columns that return a relationship id (r.id AS evidence), found with the same rule run_local uses (querier._is_evidence).
The HTTP call goes through snapshot.Snapshot (source 'neptune'): a query asked once is frozen and replays offline, which is also
how the tests and the parity suite run without a cluster. The web app uses the LIVE path instead (PK15, live=True): every
question reaches the cluster (a stored answer must never make a stopped cluster look awake), through Snapshot's own HTTP call with
one attempt and a 20 s budget, and nothing is written to disk (the container's file system is read-only there). Nothing here has
run against a live cluster yet: Claude Science does that in task S16 (the loaded counts must equal the export manifest first).

  NeptuneRunner(endpoint, live=False).run(cypher) -> dict  (no 'backend' key: the answer's own key names the MODEL) raises
                                                           NeptuneError with the server's message on a failed query
  backend_options(env)                -> list of {id, label, available, reason}   for GET /api/backends and /api/landing:
                                         Neptune is available only when routes_neptune.neptune_state says 'available'
"""
import os
import time
from pathlib import Path

from snapshot import HttpBudget, Snapshot

ROOT = Path(__file__).resolve().parents[1]
SNAP_DIR = ROOT / "data" / "snapshots_neptune"


class NeptuneError(RuntimeError):
    pass


class NeptuneRunner:
    def __init__(self, endpoint=None, snap=None, port=8182, iam_headers=None, live=False, timeout=20.0):
        """endpoint: the cluster's host name (default NEPTUNE_ENDPOINT). snap: the Snapshot to use (default data/snapshots_neptune).
        live=True (the web app): every call goes to the cluster, one attempt within `timeout` seconds, nothing stored;
        live=False (tests, parity suite): the snapshot path, a stored answer replays."""
        self.endpoint = endpoint or os.environ.get("NEPTUNE_ENDPOINT", "")
        self.snap = snap or Snapshot(SNAP_DIR)
        self.url = f"https://{self.endpoint}:{port}/openCypher"
        self.iam_headers = iam_headers          # SigV4 headers are the caller's job (aws/neptune_load.sh uses awscurl)
        self.live, self.timeout = bool(live), float(timeout)

    def _post_live(self, cypher):
        """One live POST of the query, through Snapshot's HTTP call (rule 9) but not its store: one attempt, `timeout` seconds
        per attempt and as the deadline, no rate-limit wait. Returns the parsed body; raises on any failure."""
        budget = HttpBudget(timeout=self.timeout, attempts=1, rate_limit_waits=0, backoff=0.0,
                            deadline=time.monotonic() + self.timeout, store_optional=True)
        return self.snap._http("POST", self.url, None, {"query": cypher}, self.iam_headers, False, budget)

    def run(self, cypher):
        """Takes a validated openCypher query (querier.validate). Returns run_local's dict {columns, rows, evidence}."""
        import querier
        if not self.endpoint:
            raise NeptuneError("NEPTUNE_ENDPOINT is not set")
        try:
            if self.live:
                body = self._post_live(cypher) or {}
            else:
                body = self.snap.post("neptune", self.url, {"query": cypher}, headers=self.iam_headers).body or {}
        except Exception as err:
            raise NeptuneError(f"Neptune query failed: {str(err)[:300]}") from err
        if "results" not in body:
            raise NeptuneError(f"Neptune answered without results: {str(body)[:200]}")
        q = querier.parse(cypher)
        cols = [a for _, a in q.items]
        rvars = {r.var for r in q.rels}
        ev_cols = [a for e, a in q.items if querier._is_evidence(e, rvars)]
        rows = [{c: row.get(c) for c in cols} for row in body["results"]]
        evidence = sorted({x for r in rows for c in ev_cols for x in (r[c] if isinstance(r[c], list) else [r[c]]) if x})
        return dict(columns=cols, rows=rows, evidence=evidence)


def backend_options(env=None, now=None):
    """Takes the environment (default os.environ) and the time (default now). Returns the two graph backends:
    [{id 'local', label, available True, reason ''}, {id 'neptune', label, available, reason}]. Neptune is available only when
    its state (routes_neptune.neptune_state: the host's status file) is 'available'; otherwise reason is the user sentence for
    its state (not set up, asleep, waking, cannot be reached)."""
    import routes_neptune
    st = routes_neptune.neptune_state(env, now)
    return [dict(id="local", label="This server's graph", available=True, reason=""),
            dict(id="neptune", label="Amazon Neptune", available=st == "available", state=st,
                 reason=routes_neptune.state_sentence(st, env))]
