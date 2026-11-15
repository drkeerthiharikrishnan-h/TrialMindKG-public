# raw/ - source documents and data for the 34 failed programs

One folder per program. Start with `00_program.md`, then `trials/<NCT>/registry_summary.md`, then
`publications/`. The checklist of what exists and what is missing is **`CATALOGUE.xlsx`**;
`index.csv` lists every item with its source URL, status, size, checksum and date.
Files are source text re-laid out for reading - nothing is summarised or interpreted.

| Status | Items |
|---|---|
| could not download (paywall / site blocks scripts) - open in browser | 5357 |
| downloaded | 334 |
| link only (dataset or access request) | 564 |
| searched - none found | 4 |
| to download on your computer | 15 |
| web-search candidate - verify, then download | 36 |
| written (from source data) | 1937 |

## Folder layout

- `trials/<NCT>/` registry record + readable summary, `documents/` protocol / SAP / consent PDFs,
  `other_registries/` EU Clinical Trials Register pages
- `publications/` papers, conference abstracts and preprints that cite the trial (citation + abstract;
  `_fulltext.md` when open access); `all_literature_mentions.csv` = every record mentioning it, incl. reviews
- `supplementary/<PMCID>/` and `figures/<PMCID>/` files from open-access articles
- `sources/` press releases and pages cited in the curated dataset; `web/` pages found by web search
- `regulatory/` Drugs@FDA document list and letters / reviews / latest label
- `safety/` FAERS report counts and top reactions (all indications, not trial-specific)

## Getting the items marked 'to download on your computer'

The build machine could not reach several sites. On your Mac:

```bash
cd <your TrialMindKG folder>        # e.g. cd ~/projects/TrialMindKG
source .venv/bin/activate
python pipelines/raw_docs.py      # add --no-large to skip supplements/figures (GBs)
python pipelines/make_catalogue.py  # refresh the checklist
```

Items still failing after that (paywalls, sites that block scripts): open the URL from `index.csv` in a
browser and save the file at the `local_path` shown. Binary files (PDF, images, spreadsheets, slides) stay
on your computer (git-ignored); text files are committed.

Not collected automatically: patient-level data (request routes are listed), raw omics files (accession
links are listed; files are often GB-sized), JPM / investor decks that are not linked publicly.
