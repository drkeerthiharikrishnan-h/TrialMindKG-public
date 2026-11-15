"""
ChEMBL synonym table, extracted once from the full ChEMBL MySQL dump (EBI FTP), so the resolver can look up
EVERY ChEMBL name / research code / trade name exactly - not only the ~300 molecules in our frozen API responses.

    curl -s https://ftp.ebi.ac.uk/pub/databases/chembl/ChEMBLdb/releases/chembl_37/chembl_37_mysql.tar.gz \
      | tar -xzO --wildcards '*.dmp' | python pipelines/chembl_synonyms.py chembl_37
    (scripts/download_external_data.sh runs this; ~2 GB streamed, nothing large is stored)

Reads three tables from the dump on stdin (molecule_dictionary, molecule_hierarchy, molecule_synonyms) and writes
data/reference/chembl_synonyms.tsv.gz (committed, a few MB): one row per molecule that has a preferred name or a
synonym. Line 1 records the ChEMBL release. Columns:
  chembl_id, parent_chembl_id, pref_name, max_phase, molecule_type, synonyms ('name::SYN_TYPE' joined by '|')

Functions
- parse_values(sql)       -> list of row tuples from one 'INSERT INTO ... VALUES (...),(...);' line
- columns(create_lines)   -> column names of a CREATE TABLE block
- extract(lines, release, out) -> number of molecules written
- load(path)              -> (release, [dict(chembl_id, parent, pref_name, synonyms, syn_types, molecule_type,
                              max_phase)]) in the same shape as resolver.build_index()
"""
import gzip, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "reference" / "chembl_synonyms.tsv.gz"
TABLES = ("molecule_dictionary", "molecule_hierarchy", "molecule_synonyms")
TOKEN = re.compile(r"'((?:[^'\\]|\\.)*)'|(NULL)|(-?[0-9.]+(?:[eE][-+]?\d+)?)|([(),])")
UNESCAPE = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "Z": "\x1a"}


def _unescape(s):
    return re.sub(r"\\(.)", lambda m: UNESCAPE.get(m.group(1), m.group(1)), s)


def parse_values(sql):
    """'INSERT INTO `t` VALUES (1,'a\\'b',NULL),(2,'c',3.5);' -> [(1, "a'b", None), (2, 'c', 3.5)]."""
    body = sql.split(" VALUES ", 1)[1]
    rows, row, depth = [], None, 0
    for m in TOKEN.finditer(body):
        s, null, num, punct = m.groups()
        if punct == "(":
            row, depth = [], depth + 1
        elif punct == ")":
            rows.append(tuple(row))
            depth -= 1
        elif punct == ",":
            continue
        elif row is not None:
            if s is not None:
                row.append(_unescape(s))
            elif null:
                row.append(None)
            else:
                row.append(float(num) if "." in num or "e" in num.lower() else int(num))
    return rows


def columns(create_lines):
    """Column names, in order, from the lines of a CREATE TABLE block."""
    return [m.group(1) for l in create_lines if (m := re.match(r"\s*`(\w+)`\s", l))]


def extract(lines, release, out=OUT):
    cols, data, current = {}, {t: [] for t in TABLES}, None
    for line in lines:
        if line.startswith("CREATE TABLE `"):
            t = line.split("`")[1]
            current = t if t in TABLES else None
            if current:
                cols[current] = []
        elif current and current in cols and not cols[current] is None and line.startswith("  `"):
            cols[current].append(line)
        elif line.startswith(")"):
            current = None
        elif line.startswith("INSERT INTO `"):
            t = line.split("`")[1]
            if t in TABLES:
                c = columns(cols[t])
                keep = {"molecule_dictionary": ("molregno", "pref_name", "chembl_id", "max_phase", "molecule_type"),
                        "molecule_hierarchy": ("molregno", "parent_molregno"),
                        "molecule_synonyms": ("molregno", "syn_type", "synonyms")}[t]
                idx = [c.index(k) for k in keep]
                data[t] += [tuple(r[i] for i in idx) for r in parse_values(line)]
    chembl = {r[0]: r for r in data["molecule_dictionary"]}
    parent = {m: p for m, p in data["molecule_hierarchy"] if p}
    syns = {}
    for m, typ, name in data["molecule_synonyms"]:
        if name:
            syns.setdefault(m, set()).add((name.strip(), typ or ""))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")   # replace the committed table only when the dump yielded molecules
    n = 0
    with gzip.GzipFile(tmp, "wb", mtime=0) as gz:
        gz.write(f"# source: ChEMBL {release} (molecule_dictionary, molecule_hierarchy, molecule_synonyms)\n"
                 "chembl_id\tparent_chembl_id\tpref_name\tmax_phase\tmolecule_type\tsynonyms\n".encode())
        for m in sorted(chembl, key=lambda k: chembl[k][2]):
            _, pref, cid, phase, mtype = chembl[m]
            s = sorted(syns.get(m, ()))
            if not pref and not s:
                continue
            clean = lambda x: str(x or "").replace("\t", " ").replace("\n", " ").replace("|", "/")
            par = chembl.get(parent.get(m), (None, None, cid))[2]
            gz.write("\t".join([cid, par, clean(pref), clean(phase), clean(mtype),
                                "|".join(f"{clean(a)}::{b}" for a, b in s)]).encode() + b"\n")
            n += 1
    if n:
        tmp.replace(out)
    else:
        tmp.unlink()
    return n


def load(path=OUT):
    """-> (release text, molecules) in resolver.build_index() shape; (None, []) if the table is missing."""
    path = Path(path)
    if not path.exists():
        return None, []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        release = f.readline().strip("# \n")
        f.readline()
        mols = []
        for line in f:
            cid, par, pref, phase, mtype, syn = line.rstrip("\n").split("\t")
            pairs = [x.rsplit("::", 1) for x in syn.split("|") if x]
            types = {}
            for name, t in pairs:
                types.setdefault(name, set()).add(t)
            mols.append(dict(chembl_id=cid, parent=par, pref_name=pref, synonyms=sorted(types),
                             syn_types={k: sorted(v) for k, v in types.items()}, molecule_type=mtype,
                             max_phase=float(phase) if phase else None))
    return release, mols


if __name__ == "__main__":
    rel = sys.argv[1] if len(sys.argv) > 1 else "unknown release"
    n = extract((l.decode("utf-8", "replace") for l in sys.stdin.buffer), rel)
    print(f"{n} molecules with names -> {OUT}" if n else f"no molecules read from stdin; {OUT} left unchanged")
    sys.exit(0 if n else 1)
