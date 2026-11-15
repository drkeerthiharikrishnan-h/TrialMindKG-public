"""CC-46 item 1: docs/ux/CLICK_INVENTORY_2026-11-09.md is true to the page files.

Every function named in the Handler column (a backticked identifier) exists in the page file(s) of that row, every `#id`
in the Selector column exists in them, every /api endpoint named matches an app route, and the harness section carries
the click gate's pass list.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "ux" / "CLICK_INVENTORY_2026-11-09.md"
# The inventory (9 Nov) names the pages by the addresses of that day. Since 11 Nov the page it calls "/v3" is the front page
# at "/" and the page it calls "/" is at "/classic"; the keys below are the inventory's labels, not today's routes.
FILES = {"/": ROOT / "pipelines" / "app_page.html", "/v3": ROOT / "pipelines" / "app_page_v3.html"}
NATIVE = {"details", "summary", "toggle"}


def rows():
    out = []
    for line in DOC.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 7 and cells[0] in ("/ + /v3", "/", "/v3"):
            out.append(cells)
    return out


def defined(src, name):
    return bool(re.search(rf"(?:function\s+{name}\s*\(|(?:const|let|var)\s+{name}\s*=)", src))


def test_inventory_has_every_family():
    rs = rows()
    assert len(rs) >= 50
    sels = " ".join(r[1] for r in rs)
    for must in ("#hchips", "#ppills", "#tpills", ".step", "#trail", "#hf", "#pask", "#pq", "#pf", "#pstrip", "#pdemo", ".nepctl",
                 "#badge", "#pop", ".persona", "#tabPharos", "a.why", "[data-nid]", "a.chip", "#thr", "#pchain", "#attribution",
                 "/api/file", "#xlist", "#xmain", "#bnav", "#f", "#json", "#dclose"):
        assert must in sels, must


def test_every_named_handler_exists_in_its_page():
    missing = []
    for page, sel, handler, *_ in rows():
        pages = ["/", "/v3"] if page == "/ + /v3" else [page]
        for name in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", handler):
            if name in NATIVE:
                continue
            for p in pages:
                if not defined(FILES[p].read_text(encoding="utf-8"), name):
                    missing.append((p, sel, name))
    assert not missing, missing


def test_every_id_in_a_selector_exists_in_its_page():
    missing = []
    for page, sel, *_ in rows():
        pages = ["/", "/v3"] if page == "/ + /v3" else [page]
        for i in re.findall(r"#([A-Za-z][\w-]*)", sel):
            for p in pages:
                src = FILES[p].read_text(encoding="utf-8")
                if f'id="{i}"' not in src and f"#{i}" not in src:
                    missing.append((p, sel, i))
    assert not missing, missing


def test_every_data_attribute_in_a_selector_exists_in_its_page():
    missing = []
    for page, sel, *_ in rows():
        pages = ["/", "/v3"] if page == "/ + /v3" else [page]
        for a in re.findall(r"\[(data-[a-z]+)", sel):
            for p in pages:
                if not re.search(rf'{a}(=|\])|{a}"|dataset\.{a[5:]}\b', FILES[p].read_text(encoding="utf-8")):
                    missing.append((p, sel, a))
    assert not missing, missing


def test_every_endpoint_named_is_a_route():
    sys.path.insert(0, str(ROOT / "pipelines"))
    import app as A
    def walk(routes):                       # PK15: routes of an included router module (FastAPI wraps them in _IncludedRouter)
        for r in routes:
            if hasattr(r, "path"):
                yield r.path
            sub = getattr(r, "original_router", None)
            if sub is not None:
                yield from walk(sub.routes)
    paths = list(walk(A.app.routes))
    pats = [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", p) + "$") for p in paths]
    bad = []
    for page, sel, handler, api, *_ in rows():
        for u in re.findall(r"`?(/api/[A-Za-z0-9_./{}-]+)", api):
            u = u.rstrip("`.,")
            probe = re.sub(r"\{[^}]+\}", "x", u)
            if not any(p.match(probe) for p in pats):
                bad.append((sel, u))
    assert not bad, bad


def test_harness_pass_list_is_in_the_doc():
    text = DOC.read_text(encoding="utf-8")
    assert "GATE_RESULT_PLACEHOLDER" not in text
    assert re.search(r"click gate: \d+/\d+ passed", text)
