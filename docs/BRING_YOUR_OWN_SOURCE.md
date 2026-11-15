# Bring your own data: from a table to a graph with provenance and keep/prune verdicts

You do not need to be a programmer or a knowledge-graph expert. If your data are a table (one row = one finding), a
15-line text file called a **source card** turns it into graph nodes and edges, every edge carrying where it came from,
and the same keep/prune verdict machinery that scores the Pharos edges can score yours. Nothing here is specific to
kinases or to Pharos: change the card's classes and predicates and the same steps work for any BioLink-describable
science (genes, diseases, compounds, pathways, phenotypes, exposures...).

The worked example is in `examples/byo_source/`. **Its hits and signal values are SYNTHETIC** (illustrations, not
measurements); the identifiers (five ChEMBL compounds, four UniProt proteins) are real and were checked against the
ChEMBL and UniProt services on 2026-11-04.

## Five steps (about 10 minutes)

1. **Put your table in a folder as a gzipped, tab-separated file.** One header row, one finding per row. In the example,
   `examples/byo_source/screen.tsv.gz` has the columns `compound_chembl`, `target_uniprot`, `signal_pct`, `hit`, `assay`.
   From a spreadsheet: save as "Tab-separated values", then `gzip yourfile.tsv`.
2. **Copy the card** `examples/byo_source/lab_screen.yaml` and edit it. The parts you change:
   - `name`, `version`, `tables_dir` (the folder from step 1);
   - `table:` the file name without `.tsv.gz`;
   - `subject:` / `object:` which column holds each end of the edge and which kind of thing it is (`category`) and which
     identifier system (`prefix`). Use the same identifiers Pharos uses (UniProt for proteins, MONDO for diseases,
     CHEMBL.COMPOUND for compounds) and your data land on the same nodes as the Pharos core;
   - `predicate:` the relationship, one of the BioLink predicates;
   - `source`, `primary_knowledge_source`, `knowledge_level`, `agent_type`: who says it, and how (kept on every edge);
   - `filter:` optional, keeps only some rows; `properties:` extra columns to keep on the edge.
3. **Build it:** `python pipelines/build_kg.py examples/byo_source/lab_screen.yaml`. The builder checks the card against
   the BioLink model first (an impossible relationship such as "protein treats protein" is refused with a message) and prints
   the counts. Example result: 9 nodes, 6 edges (the 2 rows with `hit: no` are filtered out), no problems. Rows whose
   identifier cannot be standardised are listed in `outputs/<name>/unmapped.tsv` with the reason: they are never guessed.
4. **Score it:** `python pipelines/prune_kg.py examples/byo_source/lab_screen.yaml` gives every edge a verdict (keep or
   prune), a confidence and the reasons, written to `data/kg/<name>/edge_verdicts.tsv.gz`. Example row: gefitinib - EGFR
   comes out `prune` with confidence 0.506 and the reasons `plausibility=0.0;sources=1;reliability=0.6;source_score=0.458`.
   Read this honestly: with 6 edges and one source the pruner has almost nothing to compare with, so these verdicts only
   illustrate the output. On a real layer it also sees corroboration by other layers and the graph around the edge.
5. **Use it with the rest of the graph.** Layers are merged by identifier, so proteins in your table that are in Pharos
   share a node with Pharos's evidence. The app card for that protein then lists your finding next to the Pharos and
   Open Targets edges, with its own source and verdict (see `docs/MAINTAIN.md` for adding a layer to the training graphs).

## What every edge carries (the provenance contract)
`primary_knowledge_source`, `knowledge_level`, `agent_type`, `source`, `source_record` (the raw columns of the row), the
card `version`, plus any properties you list. Clicking "why" on an edge in the app shows them.

## Limits you should know about
- The builder reads gzipped TSV (or Parquet); it does not read Excel or CSV directly.
- Only BioLink classes and predicates are accepted for the core graph; if your science needs a class BioLink lacks,
  use a `tmkg:` term and say so (CLAUDE.md rule 6).
- One card describes one source. Several sources = several cards = several layers, which is what makes corroboration visible.
- Synthetic or example data must stay labelled SYNTHETIC in the table, the card and any text that reports them (rule 4).

## For an AI assistant doing this for you
Give it this page, your table and the sentence "write a source card for this table following docs/BRING_YOUR_OWN_SOURCE.md,
then build and score it". The builder's checks (BioLink domain/range, identifier standardisation, the unmapped report) are
what keep it from guessing; a human should still read `unmapped.tsv` and a sample of the edges.
