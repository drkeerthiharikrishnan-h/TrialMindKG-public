"""FIX5 (owner decision, 15 Nov): a calmer first screen, front end only (docs/packets_2026-11-11/FIX5.md, F1 to F7).

Folds are closed <details> (never the hidden attribute: the click gate skips anything under `hidden`), clamps are CSS only;
every id and every control stays in the DOM. Static checks read the page source; the behaviour checks run the page in
node + jsdom with stubbed API answers (tests/test_page_pk3.py's runner) and are skipped without jsdom.
  F1 the persona cards fold under "Or start as a role (N roles)"; a click on the summary then a card still chooses the role.
  F2 the tested-question list folds; "Run the N tested questions" stays visible; each question still runs its demo.
  F3 "Also for this role" folds ("More questions for this role (N)"); the glossary shows its first sentence and "more".
  F4 each worked-example card folds its program record and "runs as" tag; no program code or bare trial id at load (U11).
  F5 "The graph in numbers" is one line; the tiles and the long sentence fold under "All the counts".
  F6a the lede and the role description are clamped to one line with "more"; the full text stays in the DOM.
  F6b the "Also:" and model lines fold under "More on this result"; the source link stays visible.
  F7 the three lower sections are quieter (CSS only; ids, order and section tags unchanged).
  Acceptance: at load at most 500 visible words and 26 visible buttons (the audit's rule), and the new labels pass R11.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_page_pk3 import T, base_stubs, needs_jsdom, run_page  # noqa: E402

START = T[T.index('<div id="start">'):T.index('<div id="mainview" hidden>')]

# the audit's rule (docs/handoff/audits_2026-11-15/audit_ui_density.md): hidden attribute, display:none (inline, stylesheet, or the
# desktop rules the audit applied by hand) and the inside of a closed <details> (not its summary) count as hidden
VIS = r"""
window.__hid = el => { for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
    if (e.hidden) return true; if (getComputedStyle(e).display === "none") return true;
    const p = e.parentElement; if (p && p.tagName === "DETAILS" && !p.open && e.tagName !== "SUMMARY") return true; }
  if (el.closest("nav.bottom")) return true;
  return document.body.classList.contains("ids-hover") && !!el.closest(".idtag") && !el.closest(".ctx, tr, h2, .evcard, .hc, .dbody, .ent"); };
window.__vtext = root => { const out = []; const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n; (n = tw.nextNode());) { const el = n.parentElement; if (el && !["SCRIPT", "STYLE"].includes(el.tagName) && !__hid(el)) out.push(n.textContent); }
  return out.join(" "); };
window.__count = () => JSON.stringify({ words: __vtext(document.body).split(/\s+/).filter(w => /[A-Za-z0-9]/.test(w)).length,
  buttons: [...document.querySelectorAll("button")].filter(b => !__hid(b)).length });
"""


def ev(name, js):
    return {"wait": 300, "name": name, "eval": js}


def stubs():
    s = base_stubs()
    s["/api/pharos/demo_questions"] = [dict(id="d1", question="Which diseases is the gene PTGS2 associated with?", why="one hop"),
                                       dict(id="d2", question="Which compounds act on EGFR?", why="one hop")]
    s["/api/pharos/status"] = {"nodes": 1234, "edges": 5678, "layers": ["pharos"], "verdicts": {"keep": 5000, "prune": 678}, "predictions": None}
    return s


# ------------------------------------------------------------------ static
def closed_details(fid):
    m = re.search(r'<details class="[^"]*" id="%s"([^>]*)>' % fid, T)
    assert m, fid
    assert "open" not in m.group(1) and "hidden" not in m.group(1), fid
    return m


def test_folds_are_closed_details_and_never_the_hidden_attribute():
    for fid in ("heromore", "tqfold", "numfold", "rolefold"):
        closed_details(fid)
    # nothing in the first screen folds with the hidden attribute except what was hidden before FIX5 (filled by the script)
    hid = re.findall(r'<(?:details|div|section|ol|p)[^>]*\bhidden\b[^>]*>', START)
    assert [re.search(r'id="(\w+)"', h).group(1) for h in hid] == ["heroalso", "heromodel"], hid
    assert '<details class="fold" id="xalsofold"${XALSO_OPEN ? " open" : ""}>' in T and "let XALSO_OPEN = false;" in T
    assert '<details class="rec"><summary>Program record</summary>${ctxHtml(x.context)}' in T


def test_every_audit_id_stays_and_the_reading_order_holds():
    for i in ("hero", "seeworked", "askown", "asksec", "xsec", "tqsec", "numsec", "safarinote", "personaCards", "tqlist", "xalso",
              "topnav", "themesw", "xtabs", "xlist", "xmain", "hovercard", "drawer", "hchips", "herogain", "heroline", "heroalso",
              "heromodel", "herosource", "howsteps", "about", "stats", "lede", "xrole", "tqall", "rolesec", "attnotice", "modelbadge",
              "xmainfold"):
        assert T.count(f'id="{i}"') == 1, i
    assert T.count('id="nimbanner" role="alert"') == 1 and 'id="hchips" role="group"' in T
    order = [T.index(x) for x in ('id="hero"', 'id="seeworked"', 'id="asksec"', 'id="xsec"', 'id="tqsec"', 'id="numsec"', 'id="safarinote"', 'id="personaCards"')]
    assert order == sorted(order)


def test_f6b_fold_holds_only_the_two_extra_lines_and_the_hero_core_is_unchanged():
    hero = START[START.index('id="hero"'):START.index('id="asksec"')]
    fold = hero[hero.index('id="heromore"'):hero.index("</details>")]
    assert 'id="heroalso"' in fold and 'id="heromodel"' in fold and 'id="herosource"' not in fold and 'id="heroline"' not in fold
    assert hero.index('id="heroline"') < hero.index('id="heromore"') < hero.index('id="herosource"') < hero.index('id="seeworked"')
    assert ">See a worked answer</button>" in hero and ">Ask your own question</button>" in hero and hero.count("<li><b>") == 4
    assert ">Where this number comes from: the three runs and their folds</a>" in hero


def test_f5_safari_note_stays_in_its_own_details_outside_the_counts_fold():
    num = START[START.index('id="numsec"'):START.index('id="rolesec"')]
    fold = num[num.index('id="numfold"'):num.index("</details>")]
    assert 'id="stats"' in fold and 'id="about"' in fold and 'id="safarinote"' not in fold
    assert num.index('id="numline"') < num.index('id="numfold"') < num.index('class="pagehelp"')


def test_f7_lower_sections_are_css_only_and_the_section_count_is_unchanged():
    assert re.findall(r'<section class="([^"]*)" id="(\w+)"', START) == [
        ("card hero", "hero"), ("card", "asksec"), ("card", "xsec"), ("card lower", "tqsec"), ("card lower", "numsec"), ("card lower", "rolesec")]
    assert "#start section.lower { background:none; border:0;" in T and "#start section.lower > h2 { font-size:var(--t2);" in T


R11 = re.compile(r"\.(py|json|md|csv|tsv|html)\b|/|\bG[0-4]\b|\brun [AB]\b|\bcommit|\bpacket|\bFIX\d|\bPK\d|\bCC-\d|\b[0-9a-f]{7,}\b", re.I)


def test_r11_new_labels_name_no_file_path_code_or_run():
    labels = ["More on this result", "more", "less", "Show the tested questions", "All the counts", "Or start as a role",
              "Program record", "Worked examples, by role."]
    for lab in labels:
        assert lab in T, lab
        assert not R11.search(lab), lab
    for tpl in ("Show the ${qs.length} question", "More questions for this role (${qs.length})", "Or start as a role (${PERSONAS.length} role",
                "nodes and ${num(links)} links, every link with its source.", "Failed-trial layer: "):
        assert tpl in T, tpl
        assert not R11.search(re.sub(r"\$\{[^}]*\}", "N", tpl)), tpl


# ------------------------------------------------------------------ behaviour (jsdom)
@needs_jsdom
def test_first_screen_at_load_is_folded_and_under_the_caps(tmp_path):
    out = run_page(tmp_path, stubs(), [
        {"wait": 900, "name": "_", "eval": VIS + ";1"},
        ev("open", "JSON.stringify(['heromore','tqfold','numfold','rolefold','xalsofold'].map(i => [i, document.getElementById(i) && document.getElementById(i).open]))"),
        ev("sums", "JSON.stringify([...document.querySelectorAll('#start summary')].map(s => s.textContent))"),
        ev("count", "__count()"),
        ev("visible", "JSON.stringify({src: !__hid($('#herosource')), src_text: $('#herosource').textContent, tqall: !__hid($('#tqall')), tqall_text: $('#tqall').textContent, "
                      "persona: __hid($('.persona')), tq: __hid($('#tqlist [data-tq]')), rtab: __hid($('#xalso [data-rtab]')), stats: __hid($('#stats')), about: __hid($('#about')), "
                      "alsoh: $('#xalsofold h4').textContent, also: $('#heroalso').textContent, model_in_fold: !!$('#heromodel').closest('#heromore'), "
                      "numline: $('#numline').textContent, about_text: $('#about').textContent, safari_closed: !$('#safarinote').closest('details').open})"),
        ev("u11", "__vtext($('#xlist'))"),
        ev("cards", "JSON.stringify([...document.querySelectorAll('#xlist .xcard')].map(c => [!!c.querySelector('details.rec:not([open]) .ctx'), !!c.querySelector('details.rec .tag'), !__hid(c.querySelector('.qq')), !__hid(c.querySelector('button[data-x]'))]))"),
    ], width=1400)
    assert dict(json.loads(out["open"])) == {"heromore": False, "tqfold": False, "numfold": False, "rolefold": False, "xalsofold": False}
    sums = json.loads(out["sums"])
    for s in ("More on this result", "Show the 2 questions", "All the counts", "Page looks wrong?", "Program record", "more"):
        assert s in sums, (s, sums)
    assert any(re.fullmatch(r"Or start as a role \(\d+ roles\)", s) for s in sums), sums
    assert any(re.fullmatch(r"More questions for this role \(\d+\)", s) for s in sums), sums
    v = json.loads(out["visible"])
    assert v["src"] and v["src_text"] == "Where this number comes from: the three runs and their folds"
    assert v["tqall"] and v["tqall_text"] == "Run the 2 tested questions"
    assert v["persona"] and v["tq"] and v["rtab"] and v["stats"] and v["about"]          # folded, not removed
    assert v["alsoh"] == "Also for this role" and v["also"].startswith("Also: ") and v["model_in_fold"]
    assert v["numline"] == "1,234 nodes and 5,678 links, every link with its source." or v["numline"].endswith("links, every link with its source.")
    assert v["about_text"].startswith("A graph of 1,234 nodes") and v["safari_closed"]
    assert not re.search(r"\bP\d{1,2}\b|\bNCT\d{8}\b", out["u11"]), out["u11"]           # U11: no program code or bare trial id at load
    cards = json.loads(out["cards"])
    assert len(cards) == 3 and all(c == [True, True, True, True] for c in cards), cards
    c = json.loads(out["count"])
    assert c["words"] <= 500 and c["buttons"] <= 26, c


@needs_jsdom
def test_f1_summary_then_persona_chooses_the_role(tmp_path):
    out = run_page(tmp_path, stubs(), [
        {"click": "#rolefold summary", "wait": 200},
        ev("opened", "String($('#rolefold').open)"),
        {"click": ".persona[data-id=\"clinician\"]", "wait": 600},
        ev("role", "[$('#mainview').hidden, $('#start').hidden, $('#bName').textContent].join('|')"),
    ], width=1400)
    assert out["opened"] == "true"
    assert out["role"] == "false|true|Clinician"


@needs_jsdom
def test_f2_f3_folded_controls_still_run(tmp_path):
    out = run_page(tmp_path, stubs(), [
        ev("n", "[document.querySelectorAll('#tqlist [data-tq]').length, document.querySelectorAll('#xalso [data-rtab]').length].join('|')"),
        {"click": "#tqlist [data-tq=\"d1\"]", "wait": 600},
        ev("tq", "[$('#pharos').hidden, $('#pquestion').value, /PTGS2/.test($('#pansw').textContent)].join('|')"),
    ], width=1400)
    ntq, nrt = map(int, out["n"].split("|"))
    assert ntq == 2 and nrt >= 1
    assert out["tq"] == "false|Which diseases is the gene PTGS2 associated with?|true"
    out = run_page(tmp_path, stubs(), [
        ev("q", "$('#xalso [data-rtab]').dataset.q + '|' + $('#xalso [data-rtab]').dataset.rtab"),
        {"click": "#xalso [data-rtab]", "wait": 600},
        ev("ran", "[$('#mainview').hidden, $('#pq').value, $('#pquestion').value, $('#q').value].join('|')"),
        ev("gloss", "[$('#xgloss').firstChild.textContent.trim(), $('#xgloss details.more').open, /program's record/.test($('#xgloss').textContent)].join('|')"),
    ], width=1400)
    q, rtab = out["q"].split("|")
    hidden, pq, pquestion, tq = out["ran"].split("|")
    assert hidden == "false" and q in (pq, pquestion, tq)
    assert out["gloss"] == "Worked examples, by role.|false|true"


@needs_jsdom
def test_f3_fold_state_is_kept_across_role_tabs(tmp_path):
    out = run_page(tmp_path, stubs(), [
        {"click": "#xalsofold summary", "wait": 200},
        {"click": "#xtabs [data-xt=\"clinician\"]", "wait": 200},
        ev("open", "String($('#xalsofold').open)"),
    ], width=1400)
    assert out["open"] == "true"


@needs_jsdom
def test_f4_metreo_card_shows_its_question_and_runs_it(tmp_path):
    out = run_page(tmp_path, stubs(), [
        {"click": "#xtabs [data-xt=\"clinician\"]", "wait": 300},
        {"wait": 0, "name": "_", "eval": VIS + ";1"},
        ev("card", "(() => { const c = $('#xlist .xcard'); return [document.querySelectorAll('#xlist .xcard').length, c.querySelector('.qq').textContent, !__hid(c.querySelector('.qq')), c.querySelector('details.rec').open].join('|'); })()"),
        {"click": "#xlist .xcard button[data-x]", "wait": 800},
        ev("ran", "[$('#trials').hidden, $('#q').value].join('|')"),
    ], width=1400)
    n, q, shown, opened = out["card"].split("|")
    assert n == "3" and q == "Why did mepolizumab fail in METREO for COPD, and which patients might respond?" and shown == "true" and opened == "false"
    assert out["ran"] == "false|" + q


@needs_jsdom
def test_f6a_clamps_keep_the_full_text_and_more_opens_it(tmp_path):
    out = run_page(tmp_path, stubs(), [
        ev("before", "[$('#lede').className, $('#xrole').className, $('#lede').textContent.length, $('#xrole').textContent.length].join('|')"),
        {"click": "details.more[data-clamp=\"lede\"] summary", "wait": 200},
        {"click": "details.more[data-clamp=\"xrole\"] summary", "wait": 200},
        ev("after", "[$('#lede').className, $('#xrole').className, $('details.more[data-clamp=\"lede\"] summary').textContent].join('|')"),
    ], width=1400)
    lc, xc, ln, xn = out["before"].split("|")
    assert lc == "lede clamp1" and xc == "xrole clamp1" and int(ln) > 100 and int(xn) > 20
    assert out["after"] == "lede clamp1 open|xrole clamp1 open|less"
    assert ".clamp1:not(.open) { display:-webkit-box; -webkit-line-clamp:1;" in T


# ================================================================== part B (F8 to F12): smooth, easy to navigate
def stored_stubs():
    from test_page_pk3 import stored
    x = stored("x05")
    meta = {k: x[k] for k in ("id", "question", "kind", "endpoint", "recorded", "backend", "model")}
    return dict(stubs(), **{"/api/stored_answers": {"answers": [meta]}, "/api/stored_answer/": {"answer": x["answer"]}})


def test_part_b_static_routing_and_footer():
    assert '<nav class="crumbs" id="crumbs" aria-label="Where you are"></nav>' in T
    assert "if (location.hash.length <= 1) { showStart(null, true); return; }" in T             # Back to the first screen
    assert "history.pushState(null, \"\", location.pathname + location.search)" in T
    foot = T[T.index('<footer class="attrib"'):T.index("</footer>")]
    assert '<details id="attfold"><summary>Data sources and licences</summary><p id="attnotice">' in foot and 'id="attlist"' in foot
    assert '<a href="#about" data-go="about">About</a><a href="#judges" data-go="judges">For judges</a>' in foot
    assert '<button type="button" class="totop" id="totop"' in T and "hidden" not in T[T.index('id="totop"') - 60:T.index('id="totop"') + 80]


# F11: the quiet class marks notes about the page only; never a protected (scientific) element
PROTECTED = ("heroline", "herogain", "heroalso", "herosource", "nimbanner", "about", "numline", "stats", "pansw", "out", "pcard", "seeworked")


def test_f11_quiet_class_is_only_on_page_notes():
    tagged = re.findall(r'<[a-z0-9]+ [^>]*class="[^"]*\bqn\b[^"]*"[^>]*>', T)
    ids = sorted(re.search(r'id="([^"]+)"', x).group(1) for x in tagged if 'id="' in x)
    assert ids == ["heromodel", "modelbadge", "safarinote", "xgloss"], ids
    assert sum('class="tag qn"' in x for x in tagged) == 1 and sum("glossary qn" in x for x in tagged) == 2 and sum("rolehint qn" in x for x in tagged) == 1
    for pid in PROTECTED:
        m = re.search(r'<[a-z0-9]+ [^>]*id="%s"[^>]*>' % pid, T)
        assert m and not re.search(r'class="[^"]*\bqn\b', m.group(0)), pid
    for cls in ("bottomline", "rejected", "abstain", "limitsblk", "spot", "warn", "nimbanner"):     # no protected class is muted by it
        assert not re.search(r'class="[^"]*\b%s\b[^"]*\bqn\b|class="[^"]*\bqn\b[^"]*\b%s\b' % (cls, cls), T), cls
    assert ".qn { font-size:13px; color:var(--muted); font-weight:400; }" in T


@needs_jsdom
def test_f8_back_returns_to_the_first_screen_and_crumbs_name_the_path(tmp_path):
    out = run_page(tmp_path, stored_stubs(), [
        ev("crumbs0", "$('#crumbs').textContent"),
        {"click": "#seeworked", "wait": 1500},
        ev("crumbs1", "JSON.stringify([...document.querySelectorAll('#crumbs a')].map(a => [a.dataset.crumb, a.textContent]))"),
        ev("hash1", "location.hash"),
        {"wait": 0, "name": "_", "eval": "history.back(); 1"}, {"wait": 800},
        ev("after_back1", "[$('#start').hidden, location.hash].join('|')"),
        {"wait": 0, "name": "_", "eval": "history.back(); 1"}, {"wait": 800},
        ev("after_back", "[$('#start').hidden, $('#mainview').hidden, !!$('#seeworked'), location.hash].join('|')"),
        {"click": "#seeworked", "wait": 1500},
        ev("again", "[$('#start').hidden, $('#trials').hidden, /TULIP-1/.test($('#out').textContent)].join('|')"),
        {"click": "#crumbs a[data-crumb=\"start\"]", "wait": 300},
        ev("start_crumb", "[$('#start').hidden, $('#mainview').hidden, $('#crumbs').textContent].join('|')"),
    ], width=1400)
    assert out["crumbs0"] == ""
    crumbs = json.loads(out["crumbs1"])
    assert [c[0] for c in crumbs] == ["start", "role", "view", "answer"], crumbs
    assert crumbs[0][1] == "Start" and crumbs[2][1] == "Failed trials" and "TULIP-1" in crumbs[3][1]
    assert out["hash1"].startswith("#trials")
    assert out["after_back1"] == "true|#trials"                                                   # Back: the previous view first
    assert out["after_back"] == "false|true|true|"                                               # the first screen, the example still there
    assert out["again"] == "true|false|true"
    assert out["start_crumb"] == "false|true|"
    for c in crumbs[:3]:
        assert not R11.search(c[1]), c


@needs_jsdom
def test_f8_shared_links_open_as_before(tmp_path):
    for url in ("http://x.test/#numbers", "http://x.test/?node=NCBIGene%3A5743"):
        out = run_page(tmp_path, stubs(), [ev("v", "[$('#start').hidden, $('#mainview').hidden, location.hash].join('|')")], url=url, width=1400)
        assert out["v"].startswith("true|false|#"), (url, out)


@needs_jsdom
def test_f9_outline_of_the_tulip_answer_and_focus(tmp_path):
    out = run_page(tmp_path, stored_stubs(), [
        {"click": "#seeworked", "wait": 1500},
        ev("o", "JSON.stringify({nav: [...document.querySelectorAll('#out .blpin nav a')].map(a => [a.textContent, a.dataset.jump]), short: $('#out .blpin nav').className, "
                "blocks: [...document.querySelectorAll('#out .ans > section.blk')].filter(s => s.dataset.stage !== 'question').map(s => s.id)})"),
        {"click": "#out .blpin nav a[data-jump$=\"-evidence\"]", "wait": 300},
        ev("focus", "(() => { const a = document.activeElement; return [a.tagName, a.closest('section.blk') && a.closest('section.blk').dataset.stage].join('|'); })()"),
        ev("top", "[!!$('#totop'), $('#totop').className, $('#totop').hidden].join('|')"),
    ], width=1400)
    o = json.loads(out["o"])
    assert o["short"] == "" and len(o["nav"]) > 3
    assert sorted(j for _, j in o["nav"]) == sorted(o["blocks"])                                     # one entry per section of the answer
    assert [t for t, _ in o["nav"]][:2] == ["Bottom line", "Graph"] or "Evidence" in [t for t, _ in o["nav"]]
    assert out["focus"] == "H2|evidence"
    assert out["top"] == "true|totop|false"                                                          # Top shows only after one screen of scrolling


def test_f9_three_sections_or_fewer_have_no_outline():
    assert '<nav aria-label="In this answer"${has.length - 1 > 3 ? "" : \' class="short"\'}>' in T and ".blpin nav.short { display:none; }" in T


@needs_jsdom
def test_f12_run_this_example_focuses_the_answer_heading_and_esc_closes_the_drawer(tmp_path):
    out = run_page(tmp_path, stored_stubs(), [
        {"click": "#xlist button[data-x=\"x05\"]", "wait": 1500},
        ev("focus", "[document.activeElement.className, document.activeElement.textContent.slice(0, 40)].join('|')"),
        {"click": "#out .claim a.chip", "wait": 1200},
        ev("open", "String(!$('#drawer').hidden || $('#drawer').classList.contains('on') || document.body.classList.contains('dockmode'))"),
        {"key": "Escape", "wait": 400},
        ev("closed", "String(!$('#drawer').classList.contains('on') && !document.body.classList.contains('dockmode'))"),
    ], width=1400)
    cls, txt = out["focus"].split("|")
    assert cls == "q" and "TULIP-1" in txt
    assert out["open"] == "true" and out["closed"] == "true"


@needs_jsdom
def test_f10_footer_is_one_short_line_at_load(tmp_path):
    out = run_page(tmp_path, stubs(), [{"wait": 600, "name": "_", "eval": VIS + ";1"},
                                       ev("f", "JSON.stringify({w: __vtext($('#attribution')).split(/\\s+/).filter(w => /[A-Za-z0-9]/.test(w)).length, "
                                               "n: $('#attnotice').textContent.length, li: document.querySelectorAll('#attlist li').length})"),
                                       {"click": "#attribution a[data-go=\"about\"]", "wait": 500},
                                       ev("about", "[$('#aboutpage').hidden, $('#start').hidden].join('|')")], width=1400)
    f = json.loads(out["f"])
    assert f["w"] <= 12 and f["n"] > 50 and f["li"] > 3, f
    assert out["about"] == "false|true"


@needs_jsdom
def test_f12_worked_answer_from_the_first_screen_opens_the_view_when_a_role_is_chosen(tmp_path):
    # before FIX5 part B: role chosen -> back to the first screen -> "See a worked answer" left the first screen up and drew the
    # report in the hidden main view
    out = run_page(tmp_path, stored_stubs(), [
        {"click": ".persona[data-id=\"physician_scientist\"]", "wait": 600},
        {"click": "#brand", "wait": 300},
        {"click": "#seeworked", "wait": 1500},
        ev("v", "[$('#start').hidden, $('#mainview').hidden, $('#trials').hidden, document.activeElement.className].join('|')")], width=1400)
    assert out["v"] == "true|false|false|q"
