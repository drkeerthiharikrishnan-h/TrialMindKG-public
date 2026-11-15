"""PK12 (13 Nov): accessibility, phone layout, speed, and SEC-14 (escaping and link schemes).

Static: colour contrast computed from the page's own tokens (both themes), no hard-coded text colours, one h1 in every view, roles and
labels, reduced motion, deferred work after the first paint, escaped attributes. Behaviour (node, else skipped): safeUrl and esc against
poisoned strings; the jsdom journey runner's axe action passes on the first screen and FAILS on a page with a serious violation (so
`make harness` fails on one). The browser checks (colour contrast by axe, 390 px overflow, keyboard walks) are docs/ux/a11y_browser_check.js.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
T = (ROOT / "pipelines" / "app_page_v3.html").read_text(encoding="utf-8")
V1 = (ROOT / "pipelines" / "app_page.html").read_text(encoding="utf-8")
JSDOM_DIR = Path(os.environ.get("TMKG_JSDOM_DIR", "/tmp/tmkg/jsdom"))
NODE = shutil.which("node")
needs_jsdom = pytest.mark.skipif(not NODE or not (JSDOM_DIR / "node_modules" / "jsdom").exists(), reason="node + jsdom not available")
needs_axe = pytest.mark.skipif(not (JSDOM_DIR / "node_modules" / "axe-core").exists(), reason="axe-core not installed (make jsdom)")


def lum(h):
    h = h.lstrip("#"); r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4
    return .2126 * f(r) + .7152 * f(g) + .0722 * f(b)


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + .05) / (lb + .05)


def tokens():
    dark = dict(re.findall(r"--([a-z0-9-]+):(#[0-9a-fA-F]{6})", re.search(r":root \{(.*?)\n\s*--head", T, re.S).group(1)))
    light_css = re.search(r':root\[data-theme="light"\] \{(.*?)color-scheme:light', T, re.S).group(1)
    light = dict(dark, **dict(re.findall(r"--([a-z0-9-]+):(#[0-9a-fA-F]{6})", light_css)))
    return dark, light


def test_every_text_colour_has_4_5_to_1_on_every_surface_in_both_themes():
    for name, t in zip(("dark", "light"), tokens()):
        for fg in ("ink", "muted", "teal", "lav", "amber", "peach", "rose", "accent", "c-unspecified"):
            for bg in ("bg", "bg2", "bg3", "card"):
                assert ratio(t[fg], t[bg]) >= 4.5, (name, fg, bg, round(ratio(t[fg], t[bg]), 2))
        assert ratio(t["on-accent"], t["accent"]) >= 4.5, name            # text on the accent colour (buttons, step numbers)
        for bg in ("bg", "bg2", "bg3"):
            assert ratio(t["ctl"], t[bg]) >= 3.0, (name, "field edge", bg)  # the edge of a field is a UI component (WCAG 1.4.11)


def test_no_hard_coded_text_colours_or_dimmed_text():
    body = T[T.index("<style>"):]
    for bad in ("#1e7b4f", "#b42318", 'color:#868e96', "color:#fff;", ".pathbox.thin { opacity"):
        assert bad not in body, bad
    assert "::placeholder { color:var(--muted); opacity:1; }" in T
    assert "input[type=text]:focus { border-color:var(--amber)" in T and "outline:none" not in re.search(r"input\[type=text\]:focus \{[^}]*\}", T).group(0)


def test_one_h1_per_view_the_skip_link_lands_and_the_title_follows_the_view():
    assert 'id="viewh1"' in T and 'href="#maincontent"' in T and 'id="maincontent" tabindex="-1"' in T
    assert "viewTitle(name, !fromRoute)" in T and "document.title = t ? t + \" - TrialMindKG\"" in T
    assert "levelHeadings" in T and 'setAttribute("aria-level"' in T          # an h4 under an h2 is level 3


def test_controls_are_real_controls_with_roles_and_labels():
    assert '<button type="button" class="badge" id="badge"' in T and 'aria-expanded="false" aria-controls="pop"' in T
    assert 'id="drawer" role="dialog" aria-labelledby="dtitle"' in T and 'aria-label="Close this record"' in T
    assert 'id="subnav" role="group"' in T and 'id="hchips" role="group"' in T
    assert 'setAttribute("role", "listbox")' in T and 'aria-activedescendant' in T and "listboxify($(\"#pq\")" in T
    assert "tabIndex = 0; el.setAttribute(\"role\", \"region\")" in T          # a table that scrolls can be scrolled from the keyboard
    assert 'role="button" aria-label="${esc(edgeSay(' in T and "one tab stop" in T                  # every link of a graph is a keyboard stop with a spoken label
    assert 'setAttribute("aria-modal"' in T and 'setAttribute("inert", "")' in T and "DRAWER_FROM" in T


def test_nothing_is_told_by_colour_alone():
    for shape in ('"biolink:Gene": "circle"', '"biolink:Disease": "cross"', '"biolink:ClinicalTrial": "square"', "function nodeShapeEl"):
        assert shape in T
    assert "circle: supports" in T and "diamond: contradicts" in T and "open square: seen, not counted" in T      # the timeline, in words and shapes
    assert "const edgeMark" in T and "SHAPES[c]" in T                                                           # a strength marker on the middle of a link
    assert "STRENGTH_NAME" in T and "VERDICT_SAY" in T


def test_reduced_motion_and_phone_layout_rules_are_there():
    assert "prefers-reduced-motion: reduce) { * { transition:none !important; animation:none !important; }" in T
    assert 'behavior: "auto"' in T                                              # a smooth scroll is turned into an instant one
    assert "svg.graph.full" in T and "data-gfull" in T and "Open full screen" in T and "Close full screen" in T
    assert "html, body { overflow-x:clip; }" in T and ".tscroll { max-width:100%; }" in T
    assert "touch-action:pan-x pan-y pinch-zoom" in T


def test_the_first_screen_asks_for_three_things_and_the_rest_after_the_first_paint():
    boot = T[T.index("const landingReady = loadLanding();"):T.index("</script>", T.index("const landingReady"))]
    assert "loadPersonas();" in boot and boot.index("loadPersonas();") < boot.index("afterPaint(")
    for later in ("loadStatus();", "loadStrip();", "askQuota();", "loadAttribution();", "loadStoredAnswers();"):
        assert later in boot.split("afterPaint(")[1], later
    assert T.count('fetch("/api/stored_answers")') == 1                         # one request for the two readers
    assert "loadLanding();\n  loadAttribution();" not in T


# ---- SEC-14
def test_unescaped_sinks_are_fixed():
    assert 'data-order="${esc(order.join(","))}"' in T and 'data-order="${order.join(",")}"' not in T
    assert 'querySelector(`[data-demoans="${cssq(d.id)}"]`)' in T
    assert re.search(r"const esc = t => String\(t \?\? \"\"\)\.replace\(/\[&<>\"'\]/g", V1)    # the first page's esc now escapes the single quote
    for page in (T, V1):
        for m in re.finditer(r'href="\$\{esc\(([^)]*)\)\}"', page):
            assert m.group(1).startswith("safeUrl(") or m.group(1) in ("x.url", "c.iri", "f.url"), m.group(0)        # x.url: tested with a regexp; c.iri, f.url: server-built


def test_script_json_cannot_leave_its_script_element():
    import persona_examples as PE
    nasty = {"a": ["</script><script>alert(1)</script>", "<!-- x", "a b c", "&amp;", "'\""]}
    out = PE.script_json(nasty)
    assert "<" not in out and ">" not in out and "&" not in out and " " not in out and " " not in out
    assert json.loads(out) == nasty                                             # the same value once parsed
    html = PE.inject("<script>const X = " + PE.PLACEHOLDER + ";</script>", nasty)
    assert html.count("</script>") == 1


def _js(code):
    return subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60)


@pytest.mark.skipif(not NODE, reason="node not available")
def test_safe_url_and_esc_against_poisoned_strings():
    fn = re.search(r"const safeUrl = .*?\n.*?\n", T).group(0)
    esc = re.search(r"const esc = .*", T).group(0)
    code = fn + esc + r"""
const bad = ["javascript:alert(1)", " JaVaScRiPt:alert(1)", "java\tscript:alert(1)", "java\nscript:alert(1)", "\u0001javascript:x", "data:text/html,<script>1</script>", "vbscript:x", "//evil.example/x", "  ", "", null, undefined, "file:///etc/passwd"];
const good = ["https://example.org/a?b=1", "http://example.org", "/api/file?name=x.md", "api/file?name=x", "#sec-known", "?node=NCBIGene%3A1"];
const out = {bad: bad.map(safeUrl), good: good.map(safeUrl), esc: esc("<img src=x onerror=alert(1)>\"'&")};
console.log(JSON.stringify(out));"""
    r = _js(code); assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout)
    assert all(x == "" for x in o["bad"]), o["bad"]
    assert o["good"] == ["https://example.org/a?b=1", "http://example.org", "/api/file?name=x.md", "api/file?name=x", "#sec-known", "?node=NCBIGene%3A1"]
    assert "<" not in o["esc"] and '"' not in o["esc"] and "'" not in o["esc"]


@needs_jsdom
@needs_axe
def test_the_harness_axe_action_passes_the_first_screen_and_fails_a_serious_violation():
    H = ROOT / "docs" / "ux" / "harness_v3"
    py = ROOT / ".venv" / "bin" / "python"
    py = str(py) if py.exists() else sys.executable
    env = dict(os.environ, NODE_PATH=str(JSDOM_DIR / "node_modules"), TMKG_LLM_BACKEND="offline", HOME="/tmp")

    def run(acts):
        r = subprocess.run([NODE, str(H / "run_page_v3.js"), py, str(H / "bridge.py"), "/", "", json.dumps(acts)], capture_output=True, text=True, timeout=200, env=env, cwd=str(JSDOM_DIR))
        assert r.returncode == 0, r.stderr[-1500:]
        return json.loads(r.stdout)
    ok = run([{"wait": 1200}, {"axe": "first_screen"}])
    assert ok["script_errors"] == 0 and ok["axe"][0]["name"] == "first_screen", ok["errors"]
    broken = run([{"wait": 800}, {"eval": "document.body.insertAdjacentHTML('beforeend', '<img src=\"x.png\">'), 1"}, {"axe": "broken"}])
    assert broken["script_errors"] >= 1 and any("image-alt" in e for e in broken["errors"]), broken["errors"]


def test_make_harness_installs_axe_and_the_journey_is_in_the_script():
    mk = (ROOT / "Makefile").read_text()
    assert "jsdom axe-core" in mk
    js = (ROOT / "docs" / "ux" / "harness_v3" / "journeys.sh").read_text()
    assert "run J11_axe_accessibility" in js and '"axe":"worked_answer"' in js
