"""
The size and SHA256 of every hosted download, written into HOW_TO_RUN.md (section 3), so a stranger can check a file.

    python scripts/download_hashes.py           fetch each folder's SHA256SUMS, rewrite the block in HOW_TO_RUN.md
    python scripts/download_hashes.py --check   no network: exit 1 when the block's sizes differ from downloads.json

Sizes come from downloads.json (written when the files are uploaded); hashes from the SHA256SUMS file in each download
folder (<base_url><path>SHA256SUMS), fetched through snapshot.Snapshot (data/snapshots_downloads/, git-ignored) in
refresh mode, because a re-upload changes them. A folder whose SHA256SUMS cannot be fetched is written as "not fetched"
with the reason; nothing is filled in (CLAUDE.md rule 1). Run it after every downloads upload, on a machine that reaches
the hosted site, and commit HOW_TO_RUN.md.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
from snapshot import Snapshot  # noqa: E402

START, END = "<!-- download-hashes:start -->", "<!-- download-hashes:end -->"
SNAP_DIR = ROOT / "data" / "snapshots_downloads"


def fetch_sums(base_url, files, snap):
    """Takes downloads.json's base_url and files, and a Snapshot. Returns {path: text of its SHA256SUMS, or an
    Exception when it could not be fetched}. Only folder entries (path ending in /) have a SHA256SUMS file."""
    out = {}
    for f in files:
        if not f["path"].endswith("/"):
            continue
        try:
            body = snap.get_text("downloads", base_url + f["path"] + "SHA256SUMS").body
            out[f["path"]] = body["text"] if body else RuntimeError("no SHA256SUMS (HTTP 400/404)")
        except Exception as e:     # a blocked or failing request is reported in the block, never guessed
            out[f["path"]] = e
    return out


def kept_sums(doc_text, downloads):
    """Takes HOW_TO_RUN.md's text and downloads.json. Returns {path: SHA256SUMS text} from the committed block for every
    folder whose size there still equals downloads.json: when the site cannot be reached, a folder that was not re-uploaded
    keeps its checksums, and only a changed or new folder says "not fetched yet" (old checksums of a changed folder would be
    wrong, so they are never kept)."""
    if START not in doc_text or END not in doc_text:
        return {}
    block = doc_text.split(START, 1)[1].split(END, 1)[0]
    base = downloads.get("base_url") or ""
    out = {}
    for f in downloads.get("files", []):
        same = f"| {base}{f['path']} | {f.get('bytes', 'not recorded')} |" in block
        m = re.search(re.escape(f"`{f['path']}SHA256SUMS`:") + r"\s*```\n(.*?)\n```", block, re.S)
        if same and m:
            out[f["path"]] = m.group(1)
    return out


def render(downloads, sums):
    """Takes downloads.json (dict) and fetch_sums' result. Returns the markdown block (between the markers): per
    download its title, URL and size, and the SHA256SUMS lines (or why they are missing)."""
    base = downloads.get("base_url") or ""
    lines = [START, "", "| download | URL | bytes |", "|---|---|---|"]
    for f in downloads.get("files", []):
        lines.append(f"| {f['title']} | {base}{f['path']} | {f.get('bytes', 'not recorded')} |")
    lines.append("")
    for f in downloads.get("files", []):
        if not f["path"].endswith("/"):
            lines += [f"`{f['path']}`: a single file, no SHA256SUMS folder", ""]
            continue
        s = sums.get(f["path"])
        lines.append(f"`{f['path']}SHA256SUMS`:")
        if isinstance(s, str):
            lines += ["", "```", s.strip(), "```", ""]
        else:
            reason = ("the site did not answer from where this ran" if "failed after retries" in str(s)
                      else str(s)[:80]) if s is not None else "not fetched"
            lines += [f"SHA256 not fetched yet ({reason}); run `.venv/bin/python scripts/download_hashes.py` where the site is "
                      "reachable", ""]
    lines.append(END)
    return "\n".join(lines)


def replace_block(text, block):
    """Takes a document and a block. Returns the document with the text between the markers replaced (the block is
    appended when the markers are absent)."""
    if START in text and END in text:
        return re.sub(re.escape(START) + ".*?" + re.escape(END), lambda _: block, text, flags=re.S)
    return text.rstrip("\n") + "\n\n" + block + "\n"


def check(doc_text, downloads):
    """Takes HOW_TO_RUN.md's text and downloads.json. Returns a list of problems: a missing block, or a download whose
    size in the block differs from downloads.json."""
    if START not in doc_text:
        return ["no download-hashes block"]
    block = doc_text.split(START, 1)[1].split(END, 1)[0]
    base = downloads.get("base_url") or ""
    return [f"{f['path']}: size in HOW_TO_RUN.md differs from downloads.json ({f.get('bytes')})"
            for f in downloads.get("files", []) if f"| {base}{f['path']} | {f.get('bytes', 'not recorded')} |" not in block]


def main(argv=None, root=ROOT, snap=None):
    argv = sys.argv[1:] if argv is None else argv
    doc = Path(root) / "HOW_TO_RUN.md"
    downloads = json.loads((Path(root) / "downloads.json").read_text())
    if "--check" in argv:
        problems = check(doc.read_text(), downloads)
        for p in problems:
            print("FAIL", p)
        print(f"download hashes block: {len(problems)} problem(s)")
        return 1 if problems else 0
    sums = fetch_sums(downloads.get("base_url") or "", downloads.get("files", []),
                      snap or Snapshot(SNAP_DIR, mode="refresh"))
    kept = kept_sums(doc.read_text(), downloads)        # a folder the site did not answer for, unchanged since the last fetch
    fetched = sum(isinstance(v, str) for v in sums.values())
    sums = {k: (kept[k] if not isinstance(v, str) and k in kept else v) for k, v in sums.items()}
    doc.write_text(replace_block(doc.read_text(), render(downloads, sums)))
    got = sum(isinstance(v, str) for v in sums.values())
    print(f"HOW_TO_RUN.md: {len(downloads.get('files', []))} downloads, SHA256SUMS fetched for {fetched} of {len(sums)} folders, "
          f"kept unchanged for {got - fetched}, missing for {len(sums) - got}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
