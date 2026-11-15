#!/usr/bin/env python3
"""
Pre-public scrub check: what to look at before the repository is made public. It CHANGES NOTHING; it prints a to-do list.

    python3 scripts/prepublic_check.py [--root DIR] [--names "First,Last"] [--names-file FILE] [--history] [--json] [--max-mb 5]

Looks only at files git tracks (what a public clone would contain) and, with --history, at what ever was committed. Given
a directory that is not a git repository (the tree written by scripts/make_public_tree.sh) it looks at every file in it:

  secrets      key / token shapes (nvapi-, AKIA, private-key blocks, 'api_key = <long value>')            rule 5 of CLAUDE.md
  env_files    a tracked .env* file other than .env.example, anything tracked under data/internal/ except README.md
  emails       e-mail addresses (not noreply / example addresses)
  ips          IPv4 addresses (not loopback, 0.0.0.0, the documentation ranges, or version strings)
  infra        infrastructure identifiers (PK5, finding SEC-13): an AWS account id (12 digits inside an ARN, an ECR host,
               an 'account id' phrase or a bucket-style name), a full ARN with an account id, EC2 / VPC resource ids
               (i-, sg-, eipalloc-, eni-, vpc-, subnet-, vol-, snap-, ami-, rtb-, igw-, nat- + 8 or 17 hex), and a personal
               home path (/Users/<name>/, /home/<name>/). Placeholders are not flagged (123456789012, ACCOUNT_ID, i-0example,
               /Users/you, /home/user, /home/tmkg, <name>)
  names        personal names: --names, --names-file (one name per line, '#' comments; keep it OUTSIDE the repo, e.g. the
               other teams' names from the organisers' Q&A) plus the git user.name tokens of this clone (never hard-coded here)
  internal     internal review and hand-off notes (INTERNAL_PATTERNS: docs/reviews/, docs/handoff/, HANDOFF*, docs/sessions/,
               docs/archive/, docs/packets_*/, dated reviews and sprint plans, loop/state.yaml ...); scripts/make_public_tree.sh
               leaves all of them out
  junk         *.save *.orig *.bak *.rej *.swp .DS_Store
  large        tracked files over --max-mb (default 5)
  history      (--history) .env or data/internal ever committed other than data/internal/README.md; blobs over 20 MB in any
               commit; author / committer e-mail addresses in any commit that are not noreply addresses (a public push of the
               history publishes them: publish one fresh commit with a noreply author instead)

  scan(root, names, max_mb, history) -> list of findings {category, path, line, detail, action}
  render(findings)                   -> the to-do list text
Exit code 0 = nothing to look at, 1 = items listed (so CI or a shell can stop on it). Standard library only.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

# docs/organizers/QUESTIONS_FROM_TEAMS.md is the REDACTED public copy since CC-37 (no other team's name); the organisers'
# original .docx files and logs/ stay internal (docs/PREPUBLIC_MOVES.md).
INTERNAL_PATTERNS = [r"^docs/reviews/", r"^docs/handoff/", r"(^|/)HANDOFF[^/]*$", r"^docs/sessions/", r"^docs/claude_science/",
                     r"^docs/archive/", r"^docs/packets_[^/]*/", r"^docs/REVIEW_[^/]*$", r"^docs/[A-Z_]*SPRINT_PLAN[^/]*$",
                     r"^docs/organizers/.*\.docx$", r"^logs/", r"^loop/state\.yaml$", r"(^|/)settings\.local\.json$"]
JUNK = re.compile(r"(\.(save|orig|bak|rej|swp|tmp)$)|(^|/)\.DS_Store$|~$")
SKIP_CONTENT = re.compile(r"\.(gz|zip|png|jpg|jpeg|gif|pdf|parquet|pt|sqlite|xlsx|docx|ico|woff2?|tgz|bz2|xz|bin)$|(^|/)uv\.lock$|\.lock$", re.I)
EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
ALLOWED_EMAIL = re.compile(r"(^noreply@|^no-reply@|@example\.(com|org|net)$|@users\.noreply\.github\.com$|@localhost$|@anthropic\.com$"
                           r"|@2x\.|\.(png|jpg|svg|gif|webp)$|^\w+@\d"
                           r"|@[\w.-]+\.(json|md|csv|tsv|gz|txt|py|ya?ml|html?|js|pt|sqlite|tex|log|sh|cfg|ini|toml|lock)$)", re.I)
IPV4 = re.compile(r"(?<!\d)(?<!\d\.)((?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3})(?!\d)(?!\.\d)")
ALLOWED_IP = re.compile(r"^(127\.|0\.|255\.|192\.0\.2\.|198\.51\.100\.|203\.0\.113\.|169\.254\.)")
SECRETS = [("NVIDIA key", re.compile(r"\bnvapi-[A-Za-z0-9_-]{20,}")), ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
           ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
           ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
           ("api key / secret assignment", re.compile(r"""(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*["']?[A-Za-z0-9_\-/+]{24,}["']?"""))]
# Infrastructure identifiers (SEC-13). Each: (label, pattern, group holding the value to test against INFRA_PLACEHOLDER).
_HEX = r"(?:[0-9a-f]{17}|[0-9a-f]{8})"
INFRA = [("AWS account id in an ARN", re.compile(r"\barn:aws[a-z-]*:[a-z0-9-]*:[a-z0-9-]*:(\d{12}):"), 1),
         ("AWS account id (ECR host)", re.compile(r"\b(\d{12})\.dkr\.ecr\."), 1),
         ("AWS account id", re.compile(r"(?i)\baccount(?:[ _-]?id)?\b[^\n\d]{0,12}?\b(\d{12})\b"), 1),
         ("name embedding an AWS account id", re.compile(r"\b[a-z][a-z0-9]*(?:-[a-z0-9]+)*-(\d{12})(?:-[a-z0-9]+)*\b"), 1),
         ("EC2 / VPC resource id", re.compile(r"(?<![A-Za-z0-9-])((?:i|sg|eipalloc|eni|vpc|subnet|vol|snap|ami|rtb|igw|nat|acl)-"
                                                + _HEX + r")(?![0-9A-Za-z])"), 1),
         ("personal home path", re.compile(r"(?<![\w.])(/(?:Users|home)/([A-Za-z0-9._-]+))(?=/|\b)"), 2)]
INFRA_PLACEHOLDER = re.compile(r"^(123456789012|000000000000|111122223333|you|user|username|name|me|runner|ubuntu|ec2-user|"
                               r"root|shared|example|tmkg|\.\.\.)$", re.I)  # tmkg = the server image's own service user
MAX_SCAN_BYTES = 2_000_000
PER_FILE_EXAMPLES = 3


def git(root, *args):
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def is_git_root(root):
    """True when root is the top directory of a git work tree (not merely a folder inside one)."""
    top = git(root, "rev-parse", "--show-toplevel").strip()
    return bool(top) and Path(top).resolve() == Path(root).resolve()


def tracked(root):
    """Takes a directory. Returns the files a public copy would contain, relative to it: the files git tracks when root is
    the top of a git work tree, else every file under it (an exported tree, scripts/make_public_tree.sh), .git left out."""
    if is_git_root(root):
        out = git(root, "ls-files", "-z")
        return [p for p in out.split("\0") if p]
    root = Path(root)
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                  if p.is_file() and ".git" not in p.relative_to(root).parts)


def default_names(root):
    """Tokens (4+ letters) of this clone's git user.name: the person whose name is most likely to be in the files."""
    name = git(root, "config", "user.name").strip()
    return [t for t in re.split(r"[\s,]+", name) if len(t) >= 4 and t.isalpha()]


def read_names(path):
    """Takes a text file path (one name per line; blank lines and '#' comments ignored). Returns the list of names;
    [] when path is empty or the file does not exist."""
    if not path or not Path(path).is_file():
        return []
    return [l.strip() for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip() and not l.strip().startswith("#")]


def _f(category, path, detail, action, line=None):
    return dict(category=category, path=path, line=line, detail=detail, action=action)


def scan_file(rel, text, names, name_re):
    """Takes a path and its text. Returns the content findings (emails, ips, names, secrets, infra) of that file."""
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if len(line) > 5000:
            continue
        for label, rx in SECRETS:
            if rx.search(line):
                out.append(_f("secrets", rel, f"{label} shape", "check it is a placeholder; if real, revoke the key and purge history", i))
        for label, rx, grp in INFRA:
            for m in rx.finditer(line):
                if not INFRA_PLACEHOLDER.match(m.group(grp)):
                    out.append(_f("infra", rel, f"{label}: {m.group(1)}",
                                  "replace with a placeholder (ACCOUNT_ID, i-0example, ~/...) or move the file out of the public tree", i))
        for m in EMAIL.finditer(line):
            if not ALLOWED_EMAIL.search(m.group(0)):
                out.append(_f("emails", rel, m.group(0), "remove or replace with a role address", i))
        for m in IPV4.finditer(line):
            ip = m.group(1)
            if not ALLOWED_IP.match(ip) and not re.search(r"(?i)(version|v\d|==|>=|<=|~=)\s*" + re.escape(ip), line):
                out.append(_f("ips", rel, ip, "remove or replace with a documentation address (192.0.2.x)", i))
        if name_re:
            for m in name_re.finditer(line):
                out.append(_f("names", rel, m.group(0), "decide: keep as author credit, or replace with a role (the owner)", i))
    return out


def scan(root, names=None, max_mb=5.0, history=False, history_mb=20.0):
    """Takes the repo root, extra personal names, the size limit in MB, whether to read history and its blob limit in MB. Returns the list of
    findings {category, path, line, detail, action}; reads files, changes none."""
    root = Path(root)
    names = sorted(set((names or []) + default_names(root)))
    name_re = re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b", re.I) if names else None
    out = []
    for rel in tracked(root):
        p = root / rel
        if not p.is_file():
            continue
        base = os.path.basename(rel)
        if (base.startswith(".env") and base != ".env.example") or (rel.startswith("data/internal/") and rel != "data/internal/README.md"):
            out.append(_f("env_files", rel, "tracked file that should never be public", "git rm --cached it; check history with --history"))
        if any(re.search(rx, rel) for rx in INTERNAL_PATTERNS):
            out.append(_f("internal", rel, "internal review / hand-off / working note", "move out of the public tree or confirm it may be public"))
        if JUNK.search(rel):
            out.append(_f("junk", rel, "backup / editor / OS leftover", "git rm it"))
        size = p.stat().st_size
        if size > max_mb * 1_000_000:
            out.append(_f("large", rel, f"{size / 1e6:.1f} MB", "keep out of git (download script or release asset), or confirm"))
        if SKIP_CONTENT.search(rel) or size > MAX_SCAN_BYTES:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        out += scan_file(rel, text, names, name_re)
    if history:
        out += scan_history(root, max_mb=history_mb)
    return out


def scan_history(root, max_mb=20.0):
    """Findings from every commit: .env / data/internal paths other than data/internal/README.md, blobs over max_mb, and
    author / committer e-mail addresses that are not noreply addresses (with the number of commits carrying each)."""
    out = []
    mails = defaultdict(int)
    for line in git(root, "log", "--all", "--format=%ae%n%ce").splitlines():
        if line.strip():
            mails[line.strip()] += 1
    for mail, n in sorted(mails.items()):
        if not ALLOWED_EMAIL.search(mail):
            out.append(_f("history", "(commit metadata)", f"{mail} on {n} author/committer line(s)",
                          "publish a fresh single-commit repository with a noreply author (docs/PREPUBLIC_MOVES.md section 3)"))
    log = git(root, "log", "--all", "--name-only", "--pretty=format:", "--", ".env", ".env.*", "data/internal")
    seen = sorted({l for l in log.splitlines() if l and l not in ("data/internal/README.md", ".env.example")})
    for path in seen:
        out.append(_f("history", path, "committed at some point", "purge with git filter-repo before going public, then rotate any key in it"))
    objs = git(root, "rev-list", "--objects", "--all")
    names = {}
    for line in objs.splitlines():
        sha, _, path = line.partition(" ")
        if path:
            names[sha] = path
    proc = subprocess.run(["git", "-C", str(root), "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
                          input="\n".join(names), capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        sha, typ, size = line.split()
        if typ == "blob" and int(size) > max_mb * 1_000_000:
            out.append(_f("history", names[sha], f"{int(size) / 1e6:.0f} MB blob in history", "purge or accept the clone size"))
    return out


ORDER = ["secrets", "env_files", "history", "infra", "emails", "ips", "names", "internal", "junk", "large"]


def render(findings):
    """Takes the findings. Returns the to-do list text: per category the count, then per file up to three examples."""
    if not findings:
        return "Nothing to look at: no secrets, env files, infrastructure identifiers, e-mail addresses, IPs, names, internal notes, junk or large files.\n"
    by = defaultdict(list)
    for f in findings:
        by[f["category"]].append(f)
    lines = [f"{len(findings)} item(s) to look at before the repository is public (nothing was changed):", ""]
    for cat in ORDER:
        items = by.get(cat)
        if not items:
            continue
        lines.append(f"## {cat}: {len(items)}   action: {items[0]['action']}")
        per = defaultdict(list)
        for f in items:
            per[f["path"]].append(f)
        if len(per) > 60:                    # a whole corpus (raw/, data/snapshots/): group by the first two path parts
            groups = defaultdict(lambda: [0, 0, set()])
            for path, fs in per.items():
                g = groups["/".join(path.split("/")[:2])]
                g[0] += 1
                g[1] += len(fs)
                g[2].update(f["detail"] for f in fs[:2])
            for g, (nf, ni, ex) in sorted(groups.items(), key=lambda kv: -kv[1][1])[:40]:
                lines.append(f"- {g}/  {ni} item(s) in {nf} file(s), e.g. {', '.join(sorted(ex)[:3])}")
            lines.append("")
            continue
        for path, fs in sorted(per.items())[:60]:
            ex = ", ".join(f"{f['line']}: {f['detail']}" if f["line"] else f["detail"] for f in fs[:PER_FILE_EXAMPLES])
            more = f" (+{len(fs) - PER_FILE_EXAMPLES} more)" if len(fs) > PER_FILE_EXAMPLES else ""
            lines.append(f"- {path}  [{ex}]{more}")
        if len(per) > 60:
            lines.append(f"- ... {len(per) - 60} more file(s)")
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    p.add_argument("--names", default="", help="comma list of personal names to look for (besides git user.name)")
    p.add_argument("--names-file", default="", help="file with one name per line (keep it outside the repository)")
    p.add_argument("--max-mb", type=float, default=5.0)
    p.add_argument("--history", action="store_true", help="also read every commit (slow on a big repository)")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    names = [n.strip() for n in a.names.split(",") if n.strip()] + read_names(a.names_file)
    findings = scan(a.root, names, a.max_mb, a.history)
    print(json.dumps(findings, indent=1) if a.json else render(findings))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
