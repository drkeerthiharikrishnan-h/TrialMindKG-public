"""
Data attribution footer (CC-40): one notice per data source, shown at the bottom of the app pages (v1 and v3) and in a
README block, so every page that shows graph rows also shows the licence and the attribution each source asks for.

    python pipelines/attribution.py            # print the footer text
    python pipelines/attribution.py --write    # rewrite outputs/attribution.json and the README block between the markers

The wording per source is curated (data/curated/attributions.csv: source, what, licence, attribution, url, conditions,
checked; licence findings by Claude Science, DATA_LICENSES.md is the full record). Versions and dates are never typed in:
the {placeholders} in the attribution column are filled from committed files:
  {pharos_version}  sources/pharos.yaml `version` (the part in brackets)      {pharos_date}  data/reference/pharos_manifest.tsv
  {ot_version}      sources/opentargets_layer.yaml `version` (first word)      {chembl_version} sources/chembl37.yaml
  {hpo_version}     the HPO release: outputs/hpo_hierarchy/prepare.md, else data/ontologies/hpo/version.txt (release date in
                    the version IRI), else "of the hp.json file downloaded <date>" from the hp.json row of data/external/DATA_MANIFEST.tsv
  {ctgov_date}      outputs/run_manifest.json sources["ClinicalTrials.gov via AACT"] (last date it names)
  {gtex_date}       fetched_at of the frozen GTEx Portal responses in data/snapshots_adc_demo/gtex/
A value that cannot be found is shown as "not recorded" (never guessed).

Functions:
  versions(root)            -> {placeholder: value}
  load_rows(root)           -> list of dicts from data/curated/attributions.csv
  footer(root)              -> {notice, items: [{source, licence, text, url, conditions}], licences_file, versions}
  footer_text(f)            -> plain-text footer (one line per source)
  readme_block(f)           -> the README markdown block;  render_readme(text, block) -> README with the block replaced
  load_footer(root)         -> outputs/attribution.json when committed, else footer(root); what the app serves
"""
import csv
import gzip
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROWS = Path("data") / "curated" / "attributions.csv"
OUT = Path("outputs") / "attribution.json"
START, END = "<!-- attribution:start -->", "<!-- attribution:end -->"
MISSING = "not recorded"
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
NOTICE = ("The MIT licence of this repository covers the code only. The data shown here come from the sources below and "
          "keep their own licences; reuse of a row follows the licence of its source (every edge names it). "
          "Rows from CTD and DisGeNET are for non-commercial use only; rows from ChEMBL are share-alike.")


def _card_version(root, card):
    """Takes the root and a source card name. Returns its `version` field as text ('' when the card is missing)."""
    p = Path(root) / "sources" / f"{card}.yaml"
    if not p.is_file():
        return ""
    return str((yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("version") or "")


def _pharos_date(root):
    p = Path(root) / "data" / "reference" / "pharos_manifest.tsv"
    if not p.is_file():
        return ""
    with p.open(encoding="utf-8") as f:
        dates = [r["retrieved"][:10] for r in csv.DictReader(f, delimiter="\t") if DATE.match(r.get("retrieved", ""))]
    return max(dates) if dates else ""


def _hpo_version(root):
    """The HPO release date from the prepare report or version.txt; else the release date the committed outputs/attribution.json
    recorded (written where version.txt was present, the Mac: a fresh cloud checkout has neither file and must reproduce the
    committed footer); else the hp.json download date from the manifest."""
    root = Path(root)
    for p, pat in ((root / "outputs" / "hpo_hierarchy" / "prepare.md", r"Release \(version IRI in the file\): (\S+)"),
                   (root / "data" / "ontologies" / "hpo" / "version.txt", r"(\S+)")):
        if p.is_file():
            m = re.search(pat, p.read_text(encoding="utf-8"))
            d = DATE.search(m.group(1)) if m else None
            if d:
                return d.group(0)
    try:                                   # only a release date counts here, never an earlier "downloaded" fallback
        rec = str(json.loads((root / OUT).read_text(encoding="utf-8")).get("versions", {}).get("hpo_version") or "")
        if DATE.fullmatch(rec):
            return rec
    except (OSError, ValueError):
        pass
    p = root / "data" / "external" / "DATA_MANIFEST.tsv"
    if p.is_file():
        with p.open(encoding="utf-8") as f:
            dates = [r["retrieved_utc"][:10] for r in csv.DictReader(f, delimiter="\t")
                     if r.get("file") == "hpo/hp.json" and DATE.match(r.get("retrieved_utc", ""))]
        if dates:
            return f"of the hp.json file downloaded {max(dates)}"
    return ""


def _ctgov_date(root):
    p = Path(root) / "outputs" / "run_manifest.json"
    if not p.is_file():
        return ""
    s = (json.loads(p.read_text(encoding="utf-8")).get("sources") or {}).get("ClinicalTrials.gov via AACT", "")
    dates = DATE.findall(s)
    return max(dates) if dates else ""


def _gtex_date(root):
    d = Path(root) / "data" / "snapshots_adc_demo" / "gtex"
    dates = []
    for p in sorted(d.glob("*.json.gz")) if d.is_dir() else []:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            m = DATE.match(json.load(f).get("fetched_at", ""))
        if m:
            dates.append(m.group(0))
    if not dates:
        return ""
    return min(dates) if min(dates) == max(dates) else f"{min(dates)} to {max(dates)}"


def versions(root=ROOT):
    """Takes the repo root. Returns {placeholder: value} read from committed files ('not recorded' when absent)."""
    pv = _card_version(root, "pharos")
    m = re.search(r"\(([^)]+)\)", pv)
    v = dict(pharos_version=m.group(1) if m else pv, pharos_date=_pharos_date(root),
             ot_version=(_card_version(root, "opentargets_layer").split() or [""])[0],
             chembl_version=_card_version(root, "chembl37"), hpo_version=_hpo_version(root),
             ctgov_date=_ctgov_date(root), gtex_date=_gtex_date(root))
    return {k: (x or MISSING) for k, x in v.items()}


def load_rows(root=ROOT):
    """Takes the repo root. Returns the curated rows of data/curated/attributions.csv as dicts (file order)."""
    with (Path(root) / ROWS).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def footer(root=ROOT):
    """Takes the repo root. Returns {notice, items, licences_file, versions}: items = one dict per source with the
    attribution text filled in from versions(root)."""
    v = versions(root)
    items = [dict(source=r["source"], licence=r["licence"], text=r["attribution"].format(**v), url=r["url"],
                  conditions=r["conditions"]) for r in load_rows(root)]
    return dict(notice=NOTICE, items=items, licences_file="DATA_LICENSES.md", versions=v)


def footer_text(f):
    """Takes a footer dict. Returns plain text: the notice, then one line per source."""
    return "\n".join([f["notice"]] + [f"- {i['source']} ({i['licence']}): {i['text']} {i['url']}" for i in f["items"]])


def readme_block(f):
    """Takes a footer dict. Returns the README markdown block (a table, one row per source)."""
    lines = [f"**Data attribution** (written by `python pipelines/attribution.py --write` from `{ROWS.as_posix()}`; the "
             f"app shows the same footer on every page; full record: [DATA_LICENSES.md](DATA_LICENSES.md)). "
             + f["notice"], "", "| source | licence | attribution | conditions |", "|---|---|---|---|"]
    lines += [f"| [{i['source']}]({i['url']}) | {i['licence']} | {i['text']} | {i['conditions']} |" for i in f["items"]]
    return "\n".join(lines) + "\n"


def render_readme(text, block):
    """Takes the README text and the block. Returns the text with the part between START and END replaced
    (ValueError when the markers are missing)."""
    if START not in text or END not in text:
        raise ValueError("README.md has no attribution markers")
    a, rest = text.split(START, 1)
    _, b = rest.split(END, 1)
    return a + START + "\n" + block.rstrip("\n") + "\n" + END + b


def load_footer(root=ROOT):
    """Takes the repo root. Returns the committed outputs/attribution.json when present (the hosted image may not carry
    the snapshot folders it is computed from), else footer(root)."""
    p = Path(root) / OUT
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return footer(root)


if __name__ == "__main__":
    f = footer()
    if "--write" in sys.argv:
        (ROOT / OUT).write_text(json.dumps(f, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        p = ROOT / "README.md"
        p.write_text(render_readme(p.read_text(encoding="utf-8"), readme_block(f)), encoding="utf-8")
        print(f"{OUT} and the README attribution block rewritten")
    else:
        print(footer_text(f))
