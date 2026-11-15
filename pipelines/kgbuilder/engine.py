"""
Generic KG builder: one source card (YAML, sources/<name>.yaml) + the source's tables -> BioLink/KGX nodes and edges.
Nothing here is specific to Pharos; a new source needs only a new card (see sources/pharos.yaml for the format).

build(card, tables_dir=None) -> Build(nodes, edges, unmapped, problems)
   nodes     DataFrame: id, category, name, provided_by + card properties
   edges     DataFrame: id, subject, predicate, object, source, source_record, primary_knowledge_source,
             aggregator_knowledge_source, knowledge_level, agent_type, layer, source_version, n_records + properties
   unmapped  DataFrame: edge spec, reason, count (rows whose identifiers could not be standardised - never guessed)
   problems  list of strings (card or graph problems; empty = valid)
export(build, out_dir) -> paths of nodes.tsv.gz / edges.tsv.gz (KGX TSV) ;  summary_markdown(build) -> text

Identifier rules (card 'subject' / 'object' / node 'id' specs):
  column / columns   first non-empty value wins (e.g. MONDO id, else the source's own disease id); a columns
                     entry may be {column, prefix, lookup, only_prefix} to give each candidate column its own rule
                     (only_prefix: [MONDO, HP] = use this candidate only for ids with one of those prefixes)
  explode_json       (edge spec) columns whose cells may hold a JSON list -> one row per value
  prefix             prepended when the value has no prefix ('P00441' -> 'UniProtKB:P00441')
  prefix_map         rename or drop prefixes found in the value ('MIM' -> 'OMIM'; 'UMLS': null = not used)
  replace            regex -> replacement applied first (e.g. strip 'path:' or a WikiPathways revision)
  prefix_by          prefix chosen from another column's value (e.g. pathway type -> REACT / KEGG.PATHWAY)
  lookup             map the value through a card lookup table (UniProt -> NCBIGene); a lookup with
                     key_prefix (e.g. MESH) also matches bare values ('D006255' finds the 'MESH:D006255' key)
  skip_keys          (ref_file lookup) keys never mapped (a reviewed wrong xref): the row falls through to the next
                     candidate column; the build report counts these rows
  route_property     (object spec) edge property that records which lookup gave the object ('efo_to_mondo',
                     'mesh_to_mondo', or 'as_given' when no lookup was used); the build report counts each route
  category / category_by_prefix   BioLink class, fixed or chosen from the identifier prefix
extra_prefixes     (card key) {class: [prefix]}: prefixes the card accepts for a class although BioLink does not list them
                   (e.g. DTO for biolink:ProteinFamily); every other prefix is still reported as a problem
Edges with the same (subject, predicate, object, source) are merged: numbers keep the maximum, text keeps the
distinct values joined with '|', n_records counts the merged source rows.
"""
import gzip, hashlib, json, re
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

from kgbuilder.biolink import BioLink

ROOT = Path(__file__).resolve().parents[2]
KGX_NODE_COLS = ["id", "category", "name", "provided_by"]
KGX_EDGE_COLS = ["id", "subject", "predicate", "object", "source", "source_record", "primary_knowledge_source",
                 "aggregator_knowledge_source", "knowledge_level", "agent_type", "layer", "source_version",
                 "n_records"]
MAX_JOINED = 20          # at most this many distinct text values kept when edges are merged


@dataclass
class Build:
    card: dict
    nodes: pd.DataFrame
    edges: pd.DataFrame
    unmapped: pd.DataFrame
    problems: list = field(default_factory=list)
    multi_value: list = field(default_factory=list)      # [(edge spec, column, records listing several values, values in them)]
    routes: list = field(default_factory=list)           # [(edge spec, route, source rows, distinct objects)] (route_property)


def parse_list_cell(s):
    """Takes a cell that holds a list as text. Returns the list of its items as strings. Accepts JSON ('["a","b"]'), a Python
    list repr ("['a', 'b']") and the numpy array repr that str(ndarray) gives ("['a' 'b']", items separated by spaces only).
    The numpy form must NOT be handed to ast.literal_eval: Python joins adjacent string literals, so
    "['CHEMBL1026' 'CHEMBL588']" would come back as the single string 'CHEMBL1026CHEMBL588'."""
    s = str(s).strip()
    try:
        v = json.loads(s)
        return [str(x) for x in v] if isinstance(v, list) else [str(v)]
    except (json.JSONDecodeError, TypeError):
        pass
    inner = s[1:-1] if s.startswith("[") and s.endswith("]") else s
    quoted = [a or b for a, b in re.findall(r"'([^']*)'|\"([^\"]*)\"", inner)]
    if quoted:
        return quoted
    return [t for t in re.split(r"[\s,]+", inner.strip()) if t]


def stringify_lists(df):
    """Takes a table read from Parquet. Returns it with every list / tuple / numpy-array cell written as JSON text
    ('["CHEMBL1026","CHEMBL588"]') so that astype(str) cannot turn an array into the numpy repr."""
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == object and out[c].map(lambda x: isinstance(x, (list, tuple, np.ndarray))).any():
            out[c] = out[c].map(lambda x: json.dumps([str(v) for v in x]) if isinstance(x, (list, tuple, np.ndarray)) else x)
    return out


def count_multi_value(df, spec):
    """Takes a table and an edge spec. Returns [(column, records with more than one value, values in those records)] for
    the spec's explode_json columns, counted on the rows that pass the spec's filter."""
    out = []
    for col in spec.get("explode_json") or []:
        cells = df[col].astype("string")
        lists = [parse_list_cell(x) for x in cells[cells.str.startswith("[", na=False)]]
        many = [x for x in lists if len(x) > 1]
        out.append((col, len(many), sum(len(x) for x in many)))
    return out


def load_card(path):
    card = yaml.safe_load(Path(path).read_text())
    card["_path"] = str(path)
    return card


def check_card(card, bl):
    """Card-level problems: unknown classes/predicates, domain/range violations (before any data is read)."""
    problems = []
    for n in card.get("nodes", []):
        if not bl.has_class(n["category"]):
            problems.append(f"node spec {n['table']}: unknown class {n['category']}")
    for e in card.get("edges", []):
        scats = [e["subject"]["category"]]
        ocats = list((e["object"].get("category_by_prefix") or {}).values()) or [e["object"]["category"]]
        for s in scats:
            for o in ocats:
                problems += [f"edge spec {e['name']}: {p}" for p in bl.check_edge_type(s, e["predicate"], o)]
    return problems


def _clean(series):
    s = series.astype("string").str.strip()
    return s.mask(s.isin(["", "nan", "None", "-"]))


def resolve_ids(df, spec, lookups):
    """Standardised CURIEs for one id spec, as a string Series (NA where no usable identifier exists)."""
    return resolve_ids_route(df, spec, lookups)[0]


def resolve_ids_route(df, spec, lookups, with_skips=False):
    """Takes a table, an id spec and the card's lookups. Returns (ids, route): ids as in resolve_ids; route = a string
    Series naming, per row, the lookup that gave the identifier ('as_given' when the winning candidate column used
    no lookup; NA where no identifier was found). with_skips=True adds a third value: a boolean Series, True where a
    candidate column's value is one of its lookup's skip_keys (the row fell through to a later column because of it)."""
    cols = spec.get("columns") or [spec["column"]]
    out = pd.Series(pd.NA, index=df.index, dtype="string")
    route = pd.Series(pd.NA, index=df.index, dtype="string")
    skipped = pd.Series(False, index=df.index)
    for c in cols:
        if isinstance(c, dict):                          # {column, prefix, lookup}: per candidate column
            spec = {**spec, "prefix": c.get("prefix"), "lookup": c.get("lookup"), "only_prefix": c.get("only_prefix")}
            c = c["column"]
        v = _clean(df[c]) if c in df else pd.Series(pd.NA, index=df.index, dtype="string")
        for pat, rep in (spec.get("replace") or {}).items():
            v = v.str.replace(pat, rep, regex=True)
        if spec.get("lookup"):
            lk = lookups[spec["lookup"]]
            if getattr(lk, "skipped", None):
                skipped |= (out.isna() & v.isin(list(lk.skipped))).fillna(False).astype(bool)
            v = v.map(lk).astype("string")
        if spec.get("only_prefix"):                      # candidate used only for ids with one of these prefixes
            v = v.where(v.str.extract(r"^([^:]+):", expand=False).isin(spec["only_prefix"]))
        has_prefix = v.str.contains(":", na=False)
        if spec.get("prefix_by"):
            pb = spec["prefix_by"]
            pref = df[pb["column"]].map(pb["map"]).astype("string")
            v = (pref + ":" + v).where(pref.notna())
        elif spec.get("prefix"):
            v = v.where(has_prefix, spec["prefix"] + ":" + v)
        if spec.get("prefix_map"):
            pm = spec["prefix_map"]
            pre = v.str.extract(r"^([^:]+):", expand=False)
            mapped = pre.map(lambda p: pm.get(p, p) if pd.notna(p) else pd.NA).astype("string")
            tail = v.str.split(":", n=1).str[1].astype("string")   # pandas 3: object + string dtype is refused
            v = v.where(pre.isna(), mapped + ":" + tail)   # prefix -> null drops the id
        new = out.isna() & v.notna()
        route = route.mask(new, spec.get("lookup") or "as_given")
        out = out.fillna(v)
    return (out, route, skipped) if with_skips else (out, route)


def category_of(ids, spec):
    """BioLink class per identifier: fixed, or by prefix (category_by_prefix; unknown prefix -> NA)."""
    if spec.get("category_by_prefix"):
        return ids.str.extract(r"^([^:]+):", expand=False).map(spec["category_by_prefix"]).astype("string")
    return pd.Series(spec["category"], index=ids.index, dtype="string")


def by_column(df, rule, default=None):
    """A value per row: a constant string, or {column, map, default} (map value '*' = any other value)."""
    if rule is None:
        return pd.Series(default, index=df.index, dtype="string")
    if isinstance(rule, str):
        return pd.Series(rule, index=df.index, dtype="string")
    if "map" not in rule:                            # the column's own value (default where empty)
        return _clean(df[rule["column"]]).fillna(rule.get("default", default))
    m = dict(rule["map"])
    other = m.pop("*", rule.get("default", default))
    return _clean(df[rule["column"]]).map(lambda v: m.get(v, other) if pd.notna(v) else other).astype("string")


def edge_ids(e):
    """Same recipe as kg_schema.edge_id (sha256 over the defining fields), vectorised."""
    key = (e["subject"] + "\x1f" + e["predicate"] + "\x1f" + e["object"] + "\x1f" + e["source"] + "\x1f"
           + e["source_record"])
    return key.map(lambda s: "e" + hashlib.sha256(s.encode()).hexdigest()[:16])


def _merge(edges, prop_cols):
    """Merge rows with the same (subject, predicate, object, source). Vectorised: one string key per row."""
    edges = edges.reset_index(drop=True)
    k = (edges["subject"] + "\x1f" + edges["predicate"] + "\x1f" + edges["object"] + "\x1f" + edges["source"])
    codes, _ = pd.factorize(k)
    first = ~pd.Series(codes).duplicated()
    out = edges.loc[first.values].drop(columns=prop_cols).copy()
    out.index = codes[first.values]
    out["n_records"] = pd.Series(codes).value_counts()
    for c in prop_cols:
        num = pd.to_numeric(edges[c], errors="coerce")
        if num.notna().sum() and num.notna().sum() == edges[c].notna().sum():
            out[c] = num.groupby(codes).max()
        else:
            t = pd.DataFrame({"g": codes, "v": edges[c]}).dropna().drop_duplicates().sort_values(["g", "v"])
            t = t[t.groupby("g").cumcount() < MAX_JOINED]
            out[c] = t.groupby("g")["v"].agg("|".join)
    return out.sort_index().reset_index(drop=True)


def build_edges(df, spec, card, lookups, routes=None):
    """One edge spec over one table -> (edges DataFrame, unmapped counts list). When the object spec has a
    route_property and a list is passed as routes, route_counts(...) rows are appended to it."""
    e, _, _, unmapped = edge_rows(df, spec, card, lookups)
    if routes is not None:
        routes += route_counts(e, spec)
    props = list(spec.get("properties") or {}) + [c for c in [spec["object"].get("route_property")] if c]
    merged = _merge(e, props)
    for k, v in (spec.get("constants") or {}).items():
        merged[k] = v
    return merged, [u for u in unmapped if u[2]]


def route_counts(e, spec):
    """Takes the unmerged edge rows of one spec (edge_rows' e) and the spec. Returns [(spec name, route, source rows,
    distinct objects)] per value of the object's route_property, sorted by route; [] when the spec has none."""
    col = spec["object"].get("route_property")
    if not col or col not in e or not len(e):
        return []
    g = e.groupby(col)["object"].agg(["size", "nunique"])
    out = [(spec["name"], r, int(x["size"]), int(x["nunique"])) for r, x in g.sort_index().iterrows()]
    n, d = e.attrs.get("skip_keys", (0, 0))
    if n:
        out.append((spec["name"], "fell through: key in skip_keys", n, d))
    return out


def filter_only(df, spec):
    """The rows an edge spec keeps before its JSON-list cells are exploded (filter / min_value / require only)."""
    return filter_rows(df, {k: v for k, v in spec.items() if k != "explode_json"})


def filter_rows(df, spec):
    """The table rows an edge spec works on: after its filter / require, with JSON-list cells exploded (one row per
    value; the exploded rows keep the source row's index)."""
    if spec.get("filter"):
        for col, allowed in spec["filter"].items():
            df = df[_clean(df[col]).isin(allowed if isinstance(allowed, list) else [allowed])]
    if spec.get("min_value"):                           # {column: minimum}: keep rows whose value is a number >= minimum
        for col, lo in spec["min_value"].items():       # (rows without a number are dropped: "excluded by the card filter")
            df = df[pd.to_numeric(df[col], errors="coerce") >= float(lo)]
    if spec.get("require"):
        df = df[_clean(df[spec["require"]]).notna()]
    for col in spec.get("explode_json") or []:          # cells like '["MP:1","MP:2"]' -> one row per value
        is_list = df[col].astype("string").str.startswith("[", na=False)
        if is_list.any():
            lists = df.loc[is_list, col].map(parse_list_cell)
            df = pd.concat([df[~is_list], df[is_list].assign(**{col: lists}).explode(col)])
    return df


def edge_rows(df, spec, card, lookups):
    """One edge spec over one table, BEFORE merging: returns (e, d, status, unmapped). e = one row per source row
    that became an edge (index = the source row's index in the table; a row exploded from a JSON list appears
    once per value), d = the source rows behind e, status = Series over every row that passed the spec's filter:
    'edge' or the reason it did not become an edge ('no subject identifier', 'no usable object identifier',
    'object prefix has no class'), unmapped = the counts per reason. build_edges merges e; verdict_file.py uses
    the rows to map every Pharos row to the edge (and verdict) it became."""
    df = filter_rows(df, spec)
    subj = resolve_ids(df, spec["subject"], lookups)
    obj, oroute, oskip = resolve_ids_route(df, spec["object"], lookups, with_skips=True)
    ocat = category_of(obj, spec["object"])
    unmapped = [(spec["name"], "no subject identifier", int(subj.isna().sum())),
                (spec["name"], "no usable object identifier", int((subj.notna() & obj.isna()).sum())),
                (spec["name"], "object prefix has no class",
                 int((subj.notna() & obj.notna() & ocat.isna()).sum()))]
    ok = subj.notna() & obj.notna() & ocat.notna()
    status = pd.Series("edge", index=df.index, dtype=object)
    status[subj.isna().to_numpy()] = "no subject identifier"
    status[(subj.notna() & obj.isna()).to_numpy()] = "no usable object identifier"
    status[(subj.notna() & obj.notna() & ocat.isna()).to_numpy()] = "object prefix has no class"
    d = df[ok]
    e = pd.DataFrame({"subject": subj[ok], "predicate": spec["predicate"], "object": obj[ok],
                      "subject_category": spec["subject"]["category"], "object_category": ocat[ok]})
    e["source"] = by_column(d, spec.get("source"), default=card["name"]).fillna(card["name"])
    e["primary_knowledge_source"] = by_column(d, spec.get("primary_knowledge_source"), card["aggregator"])
    e["knowledge_level"] = by_column(d, spec.get("knowledge_level"), "not_provided")
    e["agent_type"] = by_column(d, spec.get("agent_type"), "not_provided")
    e["layer"] = spec.get("layer", card.get("layer", "L0"))
    rec_cols = spec.get("record_columns") or [c for c in (spec["subject"].get("column"), spec["object"].get("column")) if c]
    joined = (d[rec_cols].astype("string").fillna("").agg("|".join, axis=1) if len(d)
              else pd.Series(index=d.index, dtype="string"))     # an empty table: agg would return a DataFrame
    e["source_record"] = spec["table"] + "|" + joined
    if spec["object"].get("name"):
        e["object_name"] = _clean(d[spec["object"]["name"]])
    if spec["object"].get("route_property"):
        e[spec["object"]["route_property"]] = oroute[ok]
        hit = oskip[ok]
        e.attrs["skip_keys"] = (int(hit.sum()), int(obj[ok][hit].nunique()))   # rows that fell through a skipped key
    props = spec.get("properties") or {}
    for out_name, col in props.items():
        e[out_name] = _clean(d[col])
    for k, v in (spec.get("constants") or {}).items():
        e[k] = v
    return e, d, status, unmapped


def build_nodes(df, spec, lookups):
    ids = resolve_ids(df, spec["id"], lookups)
    n = pd.DataFrame({"id": ids, "category": spec["category"]})
    n["name"] = _clean(df[spec["name"]]) if spec.get("name") else pd.NA
    for out_name, col in (spec.get("properties") or {}).items():
        if isinstance(col, dict):                        # {column, lookup}: the column's value mapped through a card lookup
            n[out_name] = _clean(df[col["column"]]).map(lookups[col["lookup"]]).astype("string")
        else:
            n[out_name] = _clean(df[col])
    for k, v in (spec.get("constants") or {}).items():
        n[k] = v
    return n[n["id"].notna()].drop_duplicates("id")


def _table_exists(tables_dir, name):
    """True if a table file exists in any supported format."""
    d = Path(tables_dir)
    return ((d / f"{name}.tsv.gz").exists()
            or (d / f"{name}.parquet").is_file()
            or ((d / name).is_dir() and any((d / name).glob("*.parquet"))))


def read_table(tables_dir, name, cache):
    """Read a table from tables_dir. Supports .tsv.gz, single .parquet, and Parquet directories."""
    if name not in cache:
        d = Path(tables_dir)
        tsv = d / f"{name}.tsv.gz"
        parquet_file = d / f"{name}.parquet"
        parquet_dir = d / name
        if tsv.exists():
            cache[name] = pd.read_csv(tsv, sep="\t", dtype=str, low_memory=False)
        elif parquet_file.is_file():
            cache[name] = stringify_lists(pd.read_parquet(parquet_file, dtype_backend="numpy_nullable")).astype(str)
        elif parquet_dir.is_dir() and any(parquet_dir.glob("*.parquet")):
            cache[name] = stringify_lists(pd.read_parquet(parquet_dir, dtype_backend="numpy_nullable")).astype(str)
        else:
            raise FileNotFoundError(f"no table {name} in {d} (tried .tsv.gz, .parquet, parquet directory)")
    return cache[name]


class Lookup(dict):
    """A lookup table (dict key -> CURIE) that also remembers the keys removed by skip_keys (attribute `skipped`)."""
    skipped = frozenset()


def ref_lookup(path, key, value, skip_ambiguous=False, key_prefix=None, skip_keys=None):
    """External reference TSV (data/reference/...) -> {key column value: value column value} over rows where both
    cells are filled (values are already-prefixed CURIEs). skip_ambiguous drops rows flagged in an 'ambiguous'
    column when the file has one, otherwise every key that maps to more than one distinct value. key_prefix
    (e.g. 'MESH'): every 'MESH:D006255' key is also stored bare ('D006255'), so a source that gives bare ids matches.
    skip_keys: keys removed after loading (a reviewed wrong mapping; with key_prefix the bare and the prefixed form are
    both accepted and both removed); a listed key the table does not hold is reported with a WARNING, not an error.
    Returns a Lookup (a dict with .skipped = the removed keys, every form)."""
    ref = pd.read_csv(path, sep="\t", comment="#", dtype=str, keep_default_na=False)
    ref = ref[(ref[key] != "") & (ref[value] != "")].drop_duplicates([key, value])
    if skip_ambiguous:
        if "ambiguous" in ref.columns:
            ref = ref[ref["ambiguous"] == ""]
        else:
            ref = ref[~ref.duplicated(key, keep=False)]
    out = dict(zip(ref[key], ref[value]))
    if key_prefix:
        pre = key_prefix + ":"
        out.update({k[len(pre):]: v for k, v in list(out.items()) if k.startswith(pre)})
    out = Lookup(out)
    forms = set()
    for k in skip_keys or []:
        k = str(k)
        both = {k}
        if key_prefix:
            pre = key_prefix + ":"
            both |= {k[len(pre):]} if k.startswith(pre) else {pre + k}
        if not both & set(out):
            print(f"WARNING: skip_keys entry {k} is not a key of {Path(path).name}; ignored")
        forms |= both
    for k in forms:
        out.pop(k, None)
    out.skipped = frozenset(forms)
    return out


def prepare(card, tables_dir=None):
    """Takes a card and the tables folder. Returns (tables_dir, cache, lookups): the folder as a Path, the table
    cache (filled with the lookup tables read) and {lookup name: dict} built exactly as build() builds them."""
    tables_dir = Path(tables_dir or ROOT / card["tables_dir"])
    cache, lookups = {}, {}
    for name, lk in (card.get("lookups") or {}).items():
        if "ref_file" in lk:
            lookups[name] = ref_lookup(ROOT / lk["ref_file"], lk["key"], lk["value"], lk.get("skip_ambiguous"),
                                       lk.get("key_prefix"), lk.get("skip_keys"))
            if not lookups[name]:
                print(f"WARNING: lookup {name} from {lk['ref_file']} is empty (placeholder table? regenerate it "
                      "with pipelines/mondo_ref.py); ids will not be mapped")
        else:
            t = read_table(tables_dir, lk["table"], cache)
            t = t[_clean(t[lk["value"]]).notna()].drop_duplicates(lk["key"])
            lookups[name] = dict(zip(t[lk["key"]], lk.get("prefix", "") + (":" if lk.get("prefix") else "")
                                     + t[lk["value"]].astype(str)))
    return tables_dir, cache, lookups


def build(card, tables_dir=None, bl=None):
    bl = bl or BioLink()
    problems = check_card(card, bl)
    tables_dir, cache, lookups = prepare(card, tables_dir)
    node_frames = [build_nodes(read_table(tables_dir, n["table"], cache), n, lookups) for n in card.get("nodes", [])]
    edge_frames, unmapped, multi_value, routes = [], [], [], []
    for spec in card.get("edges", []):
        if not _table_exists(tables_dir, spec["table"]):
            problems.append(f"edge spec {spec['name']}: table {spec['table']} not downloaded yet - skipped")
            continue
        table = read_table(tables_dir, spec["table"], cache)
        multi_value += [(spec["name"], col, n, v) for col, n, v in count_multi_value(filter_only(table, spec), spec) if n]
        e, u = build_edges(table, spec, card, lookups, routes)
        edge_frames.append(e)
        unmapped += u
    edges = pd.concat(edge_frames, ignore_index=True) if edge_frames else pd.DataFrame(columns=KGX_EDGE_COLS)
    edges["aggregator_knowledge_source"] = card["aggregator"]
    edges["source_version"] = card.get("version", "")
    edges["id"] = edge_ids(edges)
    # endpoint nodes not declared by a node spec (e.g. diseases) get their class and name from the edge spec
    ends = pd.concat([edges[["subject", "subject_category"]].set_axis(["id", "category"], axis=1),
                      edges[["object", "object_category"]].set_axis(["id", "category"], axis=1)
                      .assign(name=edges.get("object_name"))], ignore_index=True)
    nodes = pd.concat(node_frames + [ends], ignore_index=True).drop_duplicates("id", keep="first")
    nodes["provided_by"] = card["aggregator"]
    for cat in sorted(nodes["category"].dropna().unique()):
        allowed = set(bl.prefixes(cat)) | set((card.get("extra_prefixes") or {}).get(cat, []))
        bad = nodes.loc[nodes["category"] == cat, "id"].str.split(":").str[0]
        bad = sorted(set(bad) - allowed)
        if bad:
            problems.append(f"{cat}: prefixes not listed by BioLink {bl.version}: {bad}")
    from kg_schema import glued_id
    glued = sorted(i for i in set(nodes["id"]) | set(edges["subject"]) | set(edges["object"]) if glued_id(i))
    if glued:
        problems.append(f"{len(glued):,} identifiers join several accessions without a separator (a list that was "
                        f"joined instead of exploded), e.g. {glued[:3]}")
    edges = edges.drop(columns=[c for c in ("subject_category", "object_category", "object_name") if c in edges])
    nodes = nodes.sort_values("id", ignore_index=True)
    edges = edges.sort_values(["subject", "predicate", "object", "id"], ignore_index=True)
    um = pd.DataFrame(unmapped, columns=["edge_spec", "reason", "rows"])
    return Build(card, nodes, edges, um, problems, multi_value, routes)


def export(b, out_dir):
    """KGX TSV (gzipped, deterministic bytes). Returns (nodes_path, edges_path)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, df, first in (("nodes", b.nodes, KGX_NODE_COLS), ("edges", b.edges, KGX_EDGE_COLS)):
        cols = [c for c in first if c in df] + sorted(c for c in df if c not in first)
        p = out_dir / f"{name}.tsv.gz"
        with gzip.GzipFile(p, "wb", mtime=0) as gz:
            gz.write(df[cols].to_csv(sep="\t", index=False).encode())
        paths.append(p)
    return tuple(paths)


def summary_markdown(b):
    """Counts per class, per predicate x primary source, unmapped rows and problems - computed from the build."""
    lines = [f"# {b.card['name']} graph (source version {b.card.get('version', '?')})", "",
             f"{len(b.nodes):,} nodes, {len(b.edges):,} edges.", "", "## Nodes by class", "",
             "| class | nodes |", "|---|---|"]
    lines += [f"| {k} | {v:,} |" for k, v in b.nodes["category"].value_counts().items()]
    lines += ["", "## Edges by predicate and primary knowledge source", "",
              "| predicate | primary knowledge source | edges | source rows |", "|---|---|---|---|"]
    g = b.edges.groupby(["predicate", "primary_knowledge_source"]).agg(edges=("id", "size"), rows=("n_records", "sum"))
    lines += [f"| {p} | {s} | {r.edges:,} | {int(r.rows):,} |" for (p, s), r in g.iterrows()]
    lines += ["", "## Source rows not in the graph (identifier could not be standardised)", ""]
    if len(b.unmapped):
        lines += ["| edge spec | reason | rows |", "|---|---|---|"]
        lines += [f"| {r.edge_spec} | {r.reason} | {r.rows:,} |" for r in b.unmapped.itertuples()]
    else:
        lines.append("None.")
    if b.multi_value:
        lines += ["", "## Source records that list several values (exploded: one edge per value)", "",
                  "| edge spec | column | records | values in them |", "|---|---|---|---|"]
        lines += [f"| {s} | {c} | {n:,} | {v:,} |" for s, c, n, v in b.multi_value]
    if b.routes:
        lines += ["", "## How object identifiers were mapped (route per source row; first route that gave an id wins)", "",
                  "| edge spec | route | source rows | distinct objects |", "|---|---|---|---|"]
        lines += [f"| {s} | {r} | {n:,} | {d:,} |" for s, r, n, d in b.routes]
    lines += ["", "## Problems", ""] + ([f"- {p}" for p in b.problems] or ["None."])
    return "\n".join(lines) + "\n"
