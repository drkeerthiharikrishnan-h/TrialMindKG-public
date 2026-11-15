# TrialMindKG pipeline: repeatable builds, Neptune loading, trusted answers

This page explains how the graph is built so that every answer can be traced to a raw source record,
and how to load the same graph into Amazon Neptune.

## 1. One build, four guarantees

| Guarantee | How | Where |
|---|---|---|
| **Repeatable** | Every API response is frozen in `data/snapshots/<source>/<key>.json.gz` (committed). Same snapshot + same code = byte-identical outputs. `tests/test_reproducible.py` rebuilds offline and compares the sha256 of every file | `pipelines/snapshot.py` |
| **Traceable** | Every edge has `source`, `source_record`, `evidence_type`, `retrieved` (when the response was fetched) and `snapshot_key` (the file holding the raw response). `kg_query.provenance(edge_id)` returns the path | `pipelines/kg_schema.py` |
| **Valid** | The build refuses to write a graph with an unknown node type or relationship, a malformed ID (NCT, ChEMBL, MONDO...), a missing provenance field, a dangling edge or a relationship between the wrong kinds of node | `kg_schema.validate()` |
| **Honest about gaps** | Uncertain matches are never guessed: they go to `outputs/resolution_report.csv` with a rule ending in `NEEDS_REVIEW` and, where useful, candidate suggestions | review queue |

`outputs/run_manifest.json` records, for each build: source versions (ChEMBL release, Open Targets
release, Reactome version, MONDO release, ClinicalTrials.gov data timestamp, curated file hash), the
snapshot fingerprint, node/edge counts and the sha256 of every output file.

### Snapshot modes
```bash
python pipelines/seed_kg.py                                # use stored responses, fetch only what is missing
TMKG_SNAPSHOT_MODE=offline python pipelines/seed_kg.py     # no network at all; fails if anything is missing
TMKG_SNAPSHOT_MODE=refresh python pipelines/seed_kg.py     # fetch everything again = new data version
TMKG_SNAPSHOT_PRUNE=1 python pipelines/seed_kg.py          # also delete stored responses no longer used
```
A refresh changes files in `data/snapshots/`; commit them together with the new outputs, so git history
shows exactly when the underlying data changed.

The **ADC demo** (`run_adc_demo.py`) uses the same machinery with its own schema profile (`adc` in
`kg_schema.py`) and snapshot (`data/snapshots_adc_demo/`); its graph, Neptune files and manifest are in
`outputs/adc/`. Internal-layer edges carry evidence type `synthetic` while the tables in `data/synthetic/` are
used, and the answer's date line comes from the newest source record, not the run time.

### Fixing review items
| Rule in the review queue | What to do |
|---|---|
| `no_exact_MONDO_match_NEEDS_REVIEW` | Add a row to `data/curated/condition_overrides.csv` (nct, condition, mondo_id, justification). The candidates column lists suggestions |
| `mesh_without_MONDO_NEEDS_REVIEW` | Same file, with nct `*` and the MeSH id as condition (e.g. `*,MESH:D050171,MONDO:...,why`) |
| `curator_override:*`, trial rules | Edit `data/curated/curated_overrides.csv` |
| `no_mechanism_in_ChEMBL_or_OpenTargets_NEEDS_REVIEW` | Mechanism not curated in either database. Needs a curated mechanism file (not yet built) |
| `no_exact_ChEMBL_match_NEEDS_REVIEW` (trial_intervention) | Registry drug name that matched nothing exactly (typos, drug classes, code names) |
| `investigational_drug_not_found_in_registry_arms_NEEDS_REVIEW` | A registry drug may be a code name for the investigational drug; it was left out rather than labelled |

## 1b. Trial registry source: AACT
Trials come from AACT (CTTI's copy of ClinicalTrials.gov), read by `pipelines/aact.py` into exactly the record shape
of the ClinicalTrials.gov API, so all parsers are shared. The owner downloads AACT's monthly *Flat Text Files* on a
Mac (`AACT_FLATFILES` in `.env`) and runs `aact.py freeze`; every search and trial record is frozen in
`data/snapshots/aact/`, so the cloud build is offline and repeatable. Equivalence (Oct 5 2026 export):
45/45 trial records and 34/34 program-trial matches identical to the API, order included.
Rules learnt on the way:
- AACT row ids keep the registry's own order (it sets outcome ids P1/S1.. and which name is tried first).
- AACT's `*_date` columns fill in a day the sponsor never gave; `*_month_year` keeps the true precision.
- AACT stores numbers as numbers (`0.80` -> `0.8`, `95` -> `95.0`) and line breaks as `~`: same values, kept as AACT
  has them; the comparison checks values, not formatting.
- AACT has no search engine: trials are found by the drug's ChEMBL names plus curator-confirmed registry names
  (`data/curated/drug_overrides.csv`, e.g. TQJ230 = pelacarsen). Known difference: CARDIO-TTRansform's two imaging
  sub-studies (no drug intervention) are not listed as sub-studies.
Edges built from AACT carry `via=AACT` and `infores:aact` as aggregator knowledge source.
`TMKG_TRIAL_SOURCE=ctgov` rebuilds from the frozen API responses instead (fallback).

## 2. What the graph contains

| Node type | From | Example id |
|---|---|---|
| tmkg:ClinicalTrialProgram | curated dataset | `P19` |
| biolink:ClinicalTrial (status, why stopped, sponsor, IPD sharing statement, per-arm serious AEs and deaths) | ClinicalTrials.gov | `NCT:NCT02446912` |
| tmkg:OutcomeMeasure (posted results and statistical analyses, else registered outcomes) | ClinicalTrials.gov | `OUTCOME:NCT02446912:P1` |
| biolink:Drug (investigational, comparator, background) | ChEMBL | `CHEMBL:CHEMBL2364653` |
| biolink:Protein / MacromolecularComplex / ProteinFamily / RNAProduct / SmallMolecule | ChEMBL targets | `CHEMBL:CHEMBL2487` |
| biolink:Gene | Open Targets id mapping, ChEMBL | `ENSEMBL:ENSG00000142192` |
| biolink:Disease | ClinicalTrials.gov MeSH, MONDO exact matches | `MESH:D000544`, `MONDO:0004975` |
| biolink:Pathway | Reactome | `REACT:R-HSA-114608` |
| biolink:Publication (title, journal, year, DOI) | ClinicalTrials.gov references, curated sources, Europe PMC | `PMID:31387606` |
| tmkg:DevelopmentEvent (post-failure analysis, repurposing pivot, data-access route) | the three curated tables in `failed_clinical_trials_2016_2026.md`; linked only by exact drug/trial name | `DEV:post_failure:1` |

Relationships are listed with their allowed node types in `pipelines/kg_schema.py` (`PREDICATES`).

## 3. Loading into Amazon Neptune

The build writes Neptune bulk-load files in both formats. Pick **one** per cluster:

| Format | Files | Query with |
|---|---|---|
| `opencypher` | `outputs/neptune/opencypher/nodes.csv`, `edges.csv` | openCypher (recommended: same style as Neo4j) |
| `csv` (Gremlin) | `outputs/neptune/gremlin/nodes.csv`, `edges.csv` | Gremlin |

Node labels are the local part of the category (`ClinicalTrial`, `Drug`, ...) and relationship types the
local part of the predicate (`has_trial`, `interacts_with`, ...); the full CURIEs are kept as the
`category` / `predicate` properties. **Edge ids are the same stable ids used locally**, so a citation
`[e…]` resolves to the same fact in Neptune, Neo4j and `kg_query.py`.

Steps, once the challenge AWS account is ready:
1. Upload, versioned by the snapshot fingerprint in `run_manifest.json`:
   ```bash
   FP=$(python -c "import json;print(json.load(open('outputs/run_manifest.json'))['snapshot']['fingerprint'][:12])")
   aws s3 sync outputs/neptune/opencypher/ s3://<bucket>/trialmindkg/$FP/opencypher/
   ```
2. Start the bulk load from a machine inside the Neptune VPC (use `awscurl` if IAM auth is on):
   ```bash
   curl -X POST -H 'Content-Type: application/json' https://<cluster-endpoint>:8182/loader -d '{
     "source": "s3://<bucket>/trialmindkg/'$FP'/opencypher/", "format": "opencypher",
     "iamRoleArn": "arn:aws:iam::<account-id>:role/<NeptuneLoadFromS3>", "region": "<region>",
     "failOnError": "TRUE", "parallelism": "MEDIUM", "queueRequest": "TRUE" }'
   curl https://<cluster-endpoint>:8182/loader/<loadId>?details=true&errors=true
   ```
3. Check the load matches the manifest (`counts.nodes`, `counts.edges`):
   ```cypher
   MATCH (n) RETURN count(n);
   MATCH ()-[r]->() RETURN count(r);
   ```
Example (same answer as `kg_query.program_trials("P19")`):
```cypher
MATCH (p:ClinicalTrialProgram)-[e:has_trial]->(t:ClinicalTrial) WHERE id(p) = 'P19'
RETURN id(t) AS trial, t.status, t.has_results, t.ipd_sharing, id(e) AS evidence ORDER BY trial;
```
Neptune Analytics accepts the same openCypher CSV files. Text values keep newlines as the two
characters `\n` (one record per line).

## 4. The answer harness (how agents must answer)

Agents never write free text straight from model memory. They:
1. call a fixed query in `pipelines/kg_query.py` (e.g. `trial_outcomes`, `program_target_indication`);
   each returns rows sorted the same way every time, each row with the `evidence` edge ids it rests on,
   plus `graph_version` (edges file hash + snapshot fingerprint);
2. write the answer citing those ids as `[e0123456789abcdef]`, with conditional wording computed from
   the returned numbers (CLAUDE.md rule 7);
3. pass the text through `verify_answer(text, kg, allowed=evidence)`, which rejects it if it cites an
   unknown edge, cites an edge it was not given, or states a number that is not in the cited evidence;
4. log question, query, params, answer, evidence and `graph_version` so the answer can be replayed.

Model settings for agents: temperature 0, tool access limited to `kg_query` functions, and a fixed,
versioned system prompt. `data/curated/golden_questions.csv` is the regression set: every row must
still pass after any pipeline or data change (`tests/test_kg_query.py`). Add a row whenever you confirm
an answer by hand.
