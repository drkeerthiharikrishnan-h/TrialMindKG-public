# Maintaining the graph: re-pull, rebuild, diff

How to bring the Pharos core and its layers up to date when a source releases new data, and how to see what changed.
Run on the Mac (it needs `data/`); every step reads and writes files, nothing is edited by hand. The rule behind it:
a source is pulled **as delivered** and frozen on disk; the graph is rebuilt from the frozen files; the committed
build summaries (`outputs/<layer>/summary.md`) are the record of what each build contained, so `git diff` and
`maintain.py diff` show every change.

```bash
cd ~/projects/TrialMindKG && source .venv/bin/activate && git pull --no-rebase --no-edit origin main
```

## 1. Re-pull a source

| layer | command | frozen in |
|---|---|---|
| Pharos (L0) | `python3 pipelines/pharos.py version` then `python3 pipelines/pharos.py pull` (resumable) | `data/pharos/*.tsv.gz`, manifest `data/reference/pharos_manifest.tsv` (committed) |
| Open Targets | `python3 scripts/download_ot_parquet.py` | `data/opentargets/*.parquet` |
| GO, HPO, MONDO | `bash scripts/download_latest_layers.sh`, then `python3 pipelines/ontology_prepare.py go` (likewise `hpo`, `mondo`) | `data/external/` -> `data/ontologies/<name>/` (release in `version.txt`) |
| ChEMBL 37, BioLink model | `bash scripts/download_external_data.sh`, then `python3 pipelines/chembl_prepare.py` | `data/external/` -> `data/chembl37/` |

`data/external/DATA_MANIFEST.tsv` (committed) gets one row per downloaded file (URL, date, size, sha256).
Then update the `version:` line of the layer's source card in `sources/<layer>.yaml` so every edge's
`source_version` says which release it came from.

## 2. Rebuild the layer

```bash
python3 pipelines/build_kg.py sources/pharos.yaml          # writes data/kg/pharos/ and outputs/pharos/summary.md
python3 pipelines/prune_kg.py sources/pharos.yaml          # verdict, confidence and reasons for every edge
python3 pipelines/verdict_file.py sources/pharos.yaml      # one row per Pharos source row, native Pharos ids
python3 pipelines/prune_calibration.py                     # confidence vs Open Targets (held-out agreement)
```

The same `build_kg.py` / `prune_kg.py` pair rebuilds any other layer (`sources/opentargets_layer.yaml`, ...).

## 3. Diff: what changed

```bash
python3 pipelines/maintain.py diff            # every layer: summary on disk vs the last committed one
python3 pipelines/maintain.py diff pharos     # one layer
```

Writes `outputs/maintain/diff_<layer>.md`: totals before and after, every node class and every
(predicate, primary source) row whose count changed, rows that appeared or vanished, and the source version change.
Read it before going on: a source that lost 30% of its edges usually means a changed column name or an outage
during the pull, not new biology. `git diff outputs/<layer>/unmapped.tsv` shows rows that could not be mapped.

## 4. Re-assemble and re-index

```bash
python3 pipelines/make_graphs.py G0,G1,G2,G3        # the training graphs (only if link prediction is re-run)
python3 pipelines/pharos_index.py                   # the app's card index (cards, search, status)
python3 pipelines/schema_card.py                    # docs/SCHEMA_CARD.md: classes, predicates, IRIs, counts
python3 -m pytest -q
```

## 5. Commit the record

```bash
git add outputs/*/summary.md outputs/*/unmapped.tsv outputs/maintain/ docs/SCHEMA_CARD.md outputs/schema_card.json data/reference/pharos_manifest.tsv sources/
git commit -m "[science] rebuild <layer> <new version>: <one line from diff_<layer>.md>"
git push origin main
```

Never commit `data/` contents other than the manifests and `data/reference/`; never commit while a link-prediction
run is writing `outputs/linkpred/`.
