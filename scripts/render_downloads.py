#!/usr/bin/env python3
"""
Fills the Downloads section of README.md from downloads.json (the same file the app's For-judges page reads).

    python3 scripts/render_downloads.py [--root DIR] [--check]

README.md holds two marker lines, <!-- downloads:start --> and <!-- downloads:end -->; everything between them is replaced. While
downloads.json has no base_url (and TMKG_DOWNLOADS_BASE is unset) the block is left empty, so the section is hidden. Run it at deploy time,
after the base URL is known, and commit README.md. --check exits 1 when README.md is not up to date (and changes nothing).

  render(downloads) -> the markdown between the markers ('' when nothing is published)
  update(readme_text, downloads) -> the new README text
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
START, END = "<!-- downloads:start -->", "<!-- downloads:end -->"


def render(d):
    """Takes usability.downloads(). Returns the Downloads block (heading, one line, a table), or '' when nothing is published."""
    if not d["available"]:
        return ""
    rows = ["## Downloads", "",
            "Files that go with the graph (the pruned graph with the trial layer as Amazon Neptune load files, the pruned graph as KGX tables, "
            "the verdict file, the pruned graph for PyTorch Geometric); "
            "also listed in the app under **For judges**.", "", "| download | what it is |", "|---|---|"]
    for f in d["files"]:
        extra = (f" ({f['bytes'] / 1048576:.0f} MiB)" if f.get("bytes") and f["bytes"] > 1048576 else "") + \
                (f" sha256 `{str(f['sha256'])[:12]}...`" if f.get("sha256") else "")
        rows.append(f"| [{f['title']}]({f['url']}){' (folder)' if f['folder'] else ''} | {f['what']}{extra} |")
    return "\n".join(rows)


def update(text, d):
    """Takes the README text and the downloads. Returns the text with the block between the markers replaced (the markers stay)."""
    if START not in text or END not in text:
        raise ValueError(f"README.md has no {START} / {END} markers")
    block = render(d)
    return re.sub(re.escape(START) + r".*?" + re.escape(END), lambda m: f"{START}\n{block}\n{END}" if block else f"{START}\n{END}",
                  text, flags=re.S)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    import usability
    root = Path(a.root)
    new = update((root / "README.md").read_text(), usability.downloads(root))
    if a.check:
        return 0 if new == (root / "README.md").read_text() else 1
    (root / "README.md").write_text(new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
