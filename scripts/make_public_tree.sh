#!/bin/bash
# The public tree (PK5, finding SEC-13): what a public repository would contain, written to a NEW directory.
# It changes nothing in this repository: no file is removed from git, nothing is pushed.
#
#   bash scripts/make_public_tree.sh [OUT_DIR]            default OUT_DIR: ../TrialMindKG_public (must not exist, or be empty)
#   options:  --no-fulltext     leave out every raw/**/*fulltext* file, also the ones listed as open access, and every
#                               raw_text/ file extracted from one (fails if any remains)
#             --commit          also make OUT_DIR a git repository with ONE commit (needs PUBLIC_AUTHOR_NAME and a noreply
#                               PUBLIC_AUTHOR_EMAIL, e.g. 12345+name@users.noreply.github.com); nothing is pushed
#             --repo DIR        the repository to export (default: the one this script is in)
#   EXTRA_EXCLUDES="a b"        more paths or globs to leave out (relative to the repository root)
#
# Steps:  1. `git archive HEAD` (committed files only: nothing untracked, no history) into OUT_DIR
#         2. remove the exclude list below; keep a raw/**/*fulltext* file only when raw/index.csv lists it with the
#            document type "open-access full text"
#         3. scripts/prepublic_check.py on OUT_DIR, and the size of the tree
#         4. relative links in the Markdown files that now point at nothing
# Besides OUT_DIR it writes one file next to it, OUT_DIR.prepublic.json (the full list of the check; overwritten each run).
# Exit 0 when the categories secrets, env_files, infra and internal are empty and README.md / HOW_TO_RUN.md have no dead
# link; 1 otherwise; 2 on bad arguments. The e-mail addresses and names of authors in raw/ are listed, not failed on.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/.." && pwd); OUT=""; FULLTEXT=1; COMMIT=0
usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; }
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --no-fulltext) FULLTEXT=0 ;;
    --commit) COMMIT=1 ;;
    --repo) REPO=$(cd "${2:?--repo needs a directory}" && pwd); shift ;;
    -*) echo "unknown option $1" >&2; usage >&2; exit 2 ;;
    *) OUT="$1" ;;
  esac
  shift
done
OUT="${OUT:-$REPO/../TrialMindKG_public}"
PY="${PYTHON:-python3}"

# What leaves the public tree. Internal working material: superseded plans, hand-offs, session records, reviews, task
# packets, the organisers' original documents, run logs, the machine-written state files.
EXCLUDES=(
  docs/archive
  docs/sessions
  docs/handoff
  "docs/packets_*"
  docs/claude_science
  "docs/organizers/*.docx"
  "docs/REVIEW_*.md"
  "docs/*SPRINT_PLAN*.md"
  docs/HANDOFF.md
  logs
  loop/state.yaml
  outputs/review
  docs/MASTER_PLAN.md
  docs/NEXT_STEPS.md
  .claude
  docs/ux/mockups_2026-11-11
  docs/ux/pk3_shots
  docs/ux/pk12_shots
  docs/ui_before
  docs/ui_after
  docs/OWNER_CHECKLIST.md
  docs/GAP_ANALYSIS.md
  docs/AWS_COST_ESTIMATE_2026-11-04.md
  docs/ORGANIZER_FOLLOWUP_AWS.md
  docs/ORGANIZER_FOLLOWUP_NCATS.md
  docs/PREPUBLIC_MOVES.md
  docs/DEVELOPMENT_CONTROLLER.md
  docs/EMBEDDING_MODEL_OPTIONS.md
  docs/STRANGER_TEST.md
  docs/STRANGER_TEST_RESULTS_TEMPLATE.md
  tests/test_stranger_test.py
)
# outputs/review (S14, 15 Nov): the dated verification records and gate-reviewer files name instance ids and the account id.
# docs/MASTER_PLAN.md and docs/NEXT_STEPS.md (owner decision 15 Nov): working plans; some lines name other teams.
# .claude (S14, FIX6 M4): the build assistant's local settings, hooks and skills; no test reads them.
# docs/ux/mockups_2026-11-11, pk3_shots, pk12_shots, docs/ui_before, docs/ui_after (FIX6 S1): design mock-ups and dated
#   screenshots (~28 MB); no test reads them. docs/ux/harness_v3 and the two dated design notes stay.
# The eight docs/*.md after them (FIX6 S2): internal working documents (owner to-do list, gap analysis, cost estimate,
#   follow-ups to the organisers, the pre-public procedure, the controller note, model options); no test reads them.
#   Kept although S2 named them, because a test reads each in the public tree: docs/USABILITY_PLAN.md
#   (tests/test_headline.py), docs/STRATEGY_REVIEW_RESPONSE_2026-11-02.md (the frozen protocol; tests/test_demo.py).
#   docs/VIDEO_SCRIPT.md: docs/SUBMISSION.md names it.
# docs/STRANGER_TEST.md, its results template and tests/test_stranger_test.py (owner decision 15 Nov, S22): no test with
#   outside users was run, so the kit does not ship; the test that reads both files leaves with them. loop/stranger.py and
#   loop/scorecard.yaml only name the results path (the check reports it missing).
set -f   # EXTRA_EXCLUDES is split on spaces but never expanded against the current directory
# shellcheck disable=SC2206
EXCLUDES+=(${EXTRA_EXCLUDES:-})
set +f

git -C "$REPO" rev-parse --verify -q HEAD >/dev/null || { echo "$REPO is not a git repository with a commit" >&2; exit 2; }
if [ -e "$OUT" ] && [ -n "$(ls -A "$OUT" 2>/dev/null)" ]; then echo "$OUT exists and is not empty; give a new directory" >&2; exit 2; fi
case "$OUT" in *[\*\?\[]*) echo "OUT_DIR must not contain * ? or [ ($OUT)" >&2; exit 2 ;; esac
REPO=$(cd "$REPO" && pwd -P)
PARENT=$(dirname "$OUT"); [ -d "$PARENT" ] || { echo "the parent folder of OUT_DIR does not exist: $PARENT" >&2; exit 2; }
OUT="$(cd "$PARENT" && pwd -P)/$(basename "$OUT")"
case "$OUT/" in "$REPO"/*) echo "OUT_DIR must be outside the repository ($REPO)" >&2; exit 2 ;; esac
mkdir -p "$OUT"

echo "== 1. git archive HEAD ($(git -C "$REPO" rev-parse --short HEAD)) -> $OUT"
git -C "$REPO" archive --format=tar HEAD | tar -x -C "$OUT"
count() { find "$OUT" -type f -not -path "$OUT/.git/*" | wc -l | tr -d ' '; }
kb() { du -sk "$OUT" | cut -f1; }
N0=$(count); K0=$(kb)

echo "== 2. exclude list"
for pat in "${EXCLUDES[@]}"; do
  n=0
  # the pattern is expanded inside OUT only (never an absolute path, never upwards)
  case "$pat" in /*|*..*) echo "  refused (not a path inside the tree): $pat" >&2; exit 2 ;; esac
  while IFS= read -r -d '' hit; do
    n=$((n + $(find "$hit" -type f | wc -l)))
    rm -rf -- "$hit"
  done < <(find "$OUT" -path "$OUT/$pat" -prune -print0 2>/dev/null)
  echo "  $pat: $n file(s) left out"
done

# raw/**/*fulltext*: kept only when raw/index.csv lists the file as an open-access full text
set +e
"$PY" - "$OUT" "$FULLTEXT" <<'PYEOF'
import csv, sys
from pathlib import Path
out, keep_listed = Path(sys.argv[1]), sys.argv[2] == "1"
raw = out / "raw"
listed = set()
index = raw / "index.csv"
if keep_listed and index.is_file():
    csv.field_size_limit(sys.maxsize)
    with open(index, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("doc_type") or "").strip().lower() == "open-access full text" and row.get("local_path"):
                listed.add(row["local_path"].strip())
kept = dropped = 0
if raw.is_dir():
    for p in sorted(raw.rglob("*")):
        if p.is_file() and "fulltext" in p.name.lower():
            if p.relative_to(raw).as_posix() in listed:
                kept += 1
            else:
                p.unlink()
                dropped += 1
why = "listed in raw/index.csv as open-access full text" if keep_listed else "--no-fulltext"
print(f"  raw/**/*fulltext*: {kept} kept ({why}), {dropped} left out")
# raw_text/ files extracted from a full text (owner decision O06): the source of a raw_text file is the 'pdf' column of
# raw_text/index.csv (pipelines/pdf_text.py); a raw_text file whose own name contains 'fulltext' counts too. The same
# rule as above: kept only when its source is kept (listed open-access full text, and no --no-fulltext).
# (pdf_text.py skips .md files and every raw/**/*fulltext* file is .md, so this count is 0 today; the rule keeps it so.)
rt = out / "raw_text"
src_of = {}
rindex = rt / "index.csv"
if rindex.is_file():
    with open(rindex, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("text_file"):
                src_of[row["text_file"].strip()] = (row.get("pdf") or "").strip()
def from_fulltext(rel):
    return "fulltext" in Path(rel).name.lower() or "fulltext" in Path(src_of.get(rel, "")).name.lower()
rt_kept = rt_dropped = 0
if rt.is_dir():
    for p in sorted(rt.rglob("*")):
        rel = p.relative_to(rt).as_posix()
        if p.is_file() and from_fulltext(rel):
            if src_of.get(rel, "") in listed:
                rt_kept += 1
            else:
                p.unlink()
                rt_dropped += 1
print(f"  raw_text from full texts: {rt_kept} kept ({why}), {rt_dropped} left out")
if not keep_listed:   # the gate: with --no-fulltext nothing from a full text may remain
    left = [p for p in raw.rglob("*") if p.is_file() and "fulltext" in p.name.lower()] if raw.is_dir() else []
    left += [p for p in rt.rglob("*") if p.is_file() and from_fulltext(p.relative_to(rt).as_posix())] if rt.is_dir() else []
    print(f"  full-text files remaining after --no-fulltext: {len(left)} (must be 0)")
    for p in left[:25]:
        print(f"    {p.relative_to(out).as_posix()}")
    sys.exit(1 if left else 0)
PYEOF
FTGATE=$?
set -e
find "$OUT" -depth -type d -empty -not -path "$OUT/.git/*" -delete 2>/dev/null || true
N1=$(count); K1=$(kb)

echo "== 3. pre-public check on the tree"
set +e
"$PY" "$HERE/prepublic_check.py" --root "$OUT" --json > "$OUT.prepublic.json"
"$PY" - "$OUT.prepublic.json" <<'PYEOF'
import collections, json, sys
f = json.load(open(sys.argv[1]))
by = collections.Counter(x["category"] for x in f)
must = ["secrets", "env_files", "infra", "internal"]
for c in must + sorted(set(by) - set(must)):
    print(f"  {c}: {by.get(c, 0)}" + ("   (must be 0)" if c in must else ""))
for x in [x for x in f if x["category"] in must][:25]:
    print(f"    {x['category']}: {x['path']}" + (f":{x['line']}" if x.get("line") else "") + f"  {x['detail']}")
sys.exit(1 if any(by.get(c) for c in must) else 0)
PYEOF
GATE=$?
set -e
echo "  full list: $OUT.prepublic.json   (readable form: $PY scripts/prepublic_check.py --root $OUT)"
echo "  size: $N0 files, $((K0 / 1024)) MB as archived  ->  $N1 files, $((K1 / 1024)) MB in the public tree"

echo "== 4. links that now point at nothing"
set +e
"$PY" - "$OUT" <<'PYEOF'
import re, sys
from pathlib import Path
out = Path(sys.argv[1])
link = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
dead = []
for md in sorted(out.rglob("*.md")):
    rel = md.relative_to(out).as_posix()
    if rel.startswith(("raw/", "raw_text/", "data/", "outputs/")):
        continue
    for m in link.finditer(md.read_text(encoding="utf-8", errors="replace")):
        t = m.group(1)
        if re.match(r"[a-z][a-z0-9+.-]*:", t) or t.startswith("/"):
            continue
        if not (md.parent / t).exists():
            dead.append((rel, t))
front = [d for d in dead if d[0] in ("README.md", "HOW_TO_RUN.md")]
print(f"  {len(dead)} dead relative link(s), {len(front)} of them in README.md / HOW_TO_RUN.md (must be 0)")
for rel, t in dead[:40]:
    print(f"    {rel} -> {t}")
sys.exit(1 if front else 0)
PYEOF
LINKS=$?
set -e

if [ "$COMMIT" = 1 ]; then
  echo "== 5. one commit with a noreply author (nothing is pushed)"
  : "${PUBLIC_AUTHOR_NAME:?--commit needs PUBLIC_AUTHOR_NAME}"; : "${PUBLIC_AUTHOR_EMAIL:?--commit needs PUBLIC_AUTHOR_EMAIL}"
  case "$PUBLIC_AUTHOR_EMAIL" in
    *@users.noreply.github.com|noreply@*|no-reply@*) ;;
    *) echo "PUBLIC_AUTHOR_EMAIL must be a noreply address (GitHub: Settings -> Emails -> 'Keep my email addresses private')" >&2; exit 2 ;;
  esac
  if [ "$GATE" != 0 ] || [ "$LINKS" != 0 ] || [ "$FTGATE" != 0 ]; then echo "  not committed: fix the items above first" >&2; exit 1; fi
  git -C "$OUT" init -q -b main
  git -C "$OUT" add -A
  git -C "$OUT" -c user.name="$PUBLIC_AUTHOR_NAME" -c user.email="$PUBLIC_AUTHOR_EMAIL" commit -q \
      -m "TrialMindKG: public release (source commit $(git -C "$REPO" rev-parse --short HEAD))"
  echo "  committed: $(git -C "$OUT" log --oneline | head -1); author $(git -C "$OUT" log -1 --format='%an <%ae>')"
fi

if [ "$GATE" = 0 ] && [ "$LINKS" = 0 ] && [ "$FTGATE" = 0 ]; then echo "RESULT: PASS ($OUT)"; exit 0; fi
echo "RESULT: FAIL (see the items marked 'must be 0')"; exit 1
