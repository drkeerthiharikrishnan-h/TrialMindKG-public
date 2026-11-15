#!/usr/bin/env python3
"""Record the TrialMindKG demo video with an AI voice-over and captions, end to end.

It plays segments 1 to 7 of section 2 of docs/VIDEO_SCRIPT.md (version 2) on the live site, speaks each segment's
narration with a synthetic voice, writes captions, and joins it all into one 1080p MP4. No typing, no manual editing.

Install once, in a separate venv (not the project .venv):

    python3 -m venv ~/tmkg_rec
    ~/tmkg_rec/bin/pip install playwright imageio-ffmpeg edge-tts
    ~/tmkg_rec/bin/playwright install chromium

Run (from the repository root):

    ~/tmkg_rec/bin/python scripts/record_demo.py --dry-run          # list every step and hold time, no browser
    ~/tmkg_rec/bin/python scripts/record_demo.py --neptune          # the whole video, Neptune clip included
    ~/tmkg_rec/bin/python scripts/record_demo.py                    # the whole video, no Neptune wake

Output (default ~/Movies/tmkg_demo/):
    tmkg_demo_cc.mp4      1920x1080, H.264 + AAC, captions burned in      <- upload this one
    tmkg_demo.mp4         the same with captions as a switchable track
    tmkg_demo.srt         the captions
    take.webm, segment*.wav, o05_first_screen.png, o05_evidence_panel.png

Voice: --tts edge (neural voice, default when edge-tts is installed and online) or --tts say (macOS voice, offline).
Neptune: with --neptune the script wakes Amazon Neptune if it is asleep (about 11 minutes; at most six wakes a day),
records the same question running there in segment 6, and stops the cluster at the end with the aws CLI (rule D7).
--neptune-clip PATH uses an existing screen recording for that moment instead (its last 8 seconds).

Rules kept: it never types a question; it clicks only the labels the script names, outlined for 0.6 s first; if a
label is not found it saves a screenshot and prints the label (with --strict it stops); it never opens the drawer of
PTGS2 -> thrombotic disease. Rule R11: nothing it adds on screen names a file, path, code, commit, test or run tag.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT_MD = REPO / "docs" / "VIDEO_SCRIPT.md"
DEFAULT_URL = "https://d1mdyg1ov91fxt.cloudfront.net/"
TESTED_Q = "Which genes are associated with both Alzheimer disease and type 2 diabetes mellitus?"
METREO_Q = "Why did mepolizumab fail in METREO for COPD, and which patients might respond?"
WORDS_PER_S = 2.3
NEPTUNE_CLUSTER = "tmkg-neptune"
NEPTUNE_REGION = "ap-southeast-2"

# Spoken forms only (captions keep the script's text).
SAY_AS = {
    r"\bAUROC\b": "A U ROC",
    r"\bKGX\b": "K G X",
    r"\bIL5\b": "I L 5",
    r"\bMETREO\b": "Metreo",
    r"\bChEMBL\b": "Chembl",
    r"\bPyTorch\b": "Pie Torch",
    r"\bp at 0\.068\b": "p at 0.068",
    r"\bTrialMindKG\b": "Trial Mind K G",
}


# ----------------------------------------------------------------------------- the script

@dataclass
class Segment:
    num: int
    title: str
    narration: str
    words: int = field(init=False)

    def __post_init__(self):
        self.words = len(self.narration.split())

    @property
    def est_s(self) -> float:
        return round(self.words / WORDS_PER_S, 1)


def parse_script(text: str) -> list[Segment]:
    """Section 2 of VIDEO_SCRIPT.md -> seven segments with their narration (the '> ' lines after **Narration:**)."""
    if "## 2. Segments" not in text:
        raise ValueError("VIDEO_SCRIPT.md has no '## 2. Segments' section")
    sec2 = text.split("## 2. Segments", 1)[1]
    sec2 = re.split(r"\n## \d+\.", sec2, maxsplit=1)[0]
    segs = []
    for block in re.split(r"\n(?=### Segment \d)", sec2):
        m = re.match(r"### Segment (\d+) - [^\n]*? - ([^\n]+)", block)
        if not m:
            continue
        n = re.search(r"\*\*Narration:\*\*\s*\n((?:>[^\n]*\n?)+)", block)
        if not n:
            raise ValueError(f"segment {m.group(1)} has no narration block")
        narr = " ".join(re.sub(r"^>\s?", "", ln).strip() for ln in n.group(1).splitlines())
        segs.append(Segment(int(m.group(1)), m.group(2).strip(), re.sub(r"\s+", " ", narr).strip()))
    if [s.num for s in segs] != list(range(1, 8)):
        raise ValueError(f"expected segments 1..7, found {[s.num for s in segs]}")
    return segs


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.?!:])\s+(?=[A-Z0-9])", text) if s]


def spoken(text: str) -> str:
    for pat, rep in SAY_AS.items():
        text = re.sub(pat, rep, text)
    return text


# ----------------------------------------------------------------------------- the steps (what is clicked, when)
# Each step: (fraction of the segment's narration at which it starts, kind, argument, description).
# kind: click / hover / hold / scroll / esc / goto / neptune / card / shot

def plan(segs: list[Segment]) -> dict[str, list[tuple]]:
    return {
        "1": [
            (0.00, "shot", "first", "first screen held still (white-paper screenshot o05_first_screen.png)"),
            (0.80, "hover", "#badge", 'pointer to "Role: choose"'),
        ],
        "2": [
            (0.00, "click", "#badge", 'click "Role: choose"'),
            (0.05, "click", "role:Clinician", 'click "Clinician"'),
            (0.12, "click", "#xmainfold > summary", 'open "Worked examples for this role (from the failed-trial programs)"'),
            (0.20, "click", f"example:{METREO_Q}", f'under "{METREO_Q}" click "Run this example"'),
            (0.30, "waitfor", 'section[data-stage="bottomline"], .blpin', 'hold on "Bottom line" and the confidence line'),
            (0.55, "scrollto", "sec:1", 'scroll: "Failure Diagnosis"'),
            (0.62, "scrollto", "sec:2", 'scroll: "Candidate Responder Population"'),
            (0.69, "scrollto", "sec:4", 'scroll: "Evidence-Supported Redesign Options"'),
            (0.74, "scrollto", "sec:3", 'scroll: "Related Indications"'),
            (0.80, "scrollto", ".tag.s_hypothesis_generating", "stop on the eosinophil claim marked hypothesis-generating"),
            (0.92, "limits", "Claims removed by the verifier", 'end on "Claims removed by the verifier"'),
        ],
        "3": [
            (0.00, "click", 'claimchip:1.2:ClinicalTrials.gov', 'under claim 1.2 click "ClinicalTrials.gov · registry strong"'),
            (0.05, "waitfor", "#drawer:not([hidden])", '"Evidence record" drawer open'),
            (0.12, "shot", "drawer", "white-paper screenshot o05_evidence_panel.png"),
            (0.30, "esc", "", "press Esc"),
            (0.35, "scrollto", "sec:3", 'go to "Related Indications"'),
            (0.45, "scrollto", 'claim:3.2', 'show claim 3.2 (ChEMBL · curated strong)'),
            (0.60, "scrollto", 'claim:3.3', 'show claim 3.3 (Open Targets Platform · database moderate)'),
            (0.75, "click", "subgraph:3", 'open "Subgraph used (...)" of that section'),
        ],
        "4": [
            (0.00, "pharostab", "", 'go to "Ask the Pharos graph a question"'),
            (0.06, "click", f"tested:{TESTED_Q}", f'open the tested question "{TESTED_Q}"'),
            (0.12, "waitfor", '[data-cmpmode="alone"]', 'hold on "1 of 100 rows exists only because of the enrichment ..." and the confidence line'),
            (0.30, "click", '[data-cmpmode="alone"]', 'under "Read from" click "Pharos alone"; hold on "confidence: low" and "76 rows text-mined only"'),
            (0.55, "click", '[data-cmpmode="built"]', 'click "Agent-built graph"; hold on "4 rows text-mined only"'),
        ],
        "5": [
            (0.00, "click", '[data-go="numbers"]', 'open the "Numbers" tab'),
            (0.05, "scrollto", "#num-lead", 'the table "The result in three rows" (row "Difference from Pharos alone")'),
            (0.60, "scrollto", "prune:", "briefly, the pruning block and its spot-check line"),
        ],
        "6a": [
            (0.00, "neptune", "", 'back on the tested answer, click "Run this on Amazon Neptune"; hold until the Neptune result line appears'),
        ],
        "6b": [
            (0.00, "click", '[data-go="downloads"]', 'open the "Downloads" tab: the five folders'),
            (0.50, "scrollto", "text:SHA256SUMS", 'the line "SHA256SUMS lists a checksum for every file."'),
        ],
        "7": [
            (0.00, "click", '[data-go="about"]', 'open "About"'),
            (0.25, "click", 'button:The agent loop, step by step', 'click "The agent loop, step by step"'),
            (0.70, "card", "", "full-screen card with the link"),
        ],
    }


def split_segment6(seg6: Segment) -> tuple[str, str]:
    s = sentences(seg6.narration)
    return s[0], " ".join(s[1:])


NO_NEPTUNE_6A = "The same graph runs on Amazon Neptune."


def parts(segs: list[Segment], neptune_shown: bool = True) -> list[tuple[str, str]]:
    """(part id, narration) in order; segment 6 is two parts so its voice follows the Neptune clip, then Downloads.
    When no Neptune answer is shown, the first sentence of segment 6 is replaced so the voice never claims one."""
    out = []
    for s in segs:
        if s.num == 6:
            a, b = split_segment6(s)
            out += [("6a", a if neptune_shown else NO_NEPTUNE_6A), ("6b", b)]
        else:
            out.append((str(s.num), s.narration))
    return out


# ----------------------------------------------------------------------------- audio (TTS)

def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        sys.exit("No ffmpeg: install it with  ~/tmkg_rec/bin/pip install imageio-ffmpeg")


def run(cmd: list[str], **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(map(str, cmd[:6]))} ...\n{r.stderr[-1500:]}")
    return r


def wav_seconds(p: Path) -> float:
    with wave.open(str(p)) as w:
        return w.getnframes() / float(w.getframerate())


def pick_say_voice(pref: str | None) -> str:
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    names = [re.split(r"\s{2,}", ln.strip())[0] for ln in out.splitlines() if "en_" in ln]
    if pref:
        return pref
    for want in ["Ava (Premium)", "Zoe (Premium)", "Evan (Premium)", "Ava (Enhanced)", "Zoe (Enhanced)",
                 "Samantha (Enhanced)", "Evan (Enhanced)", "Samantha"]:
        if want in names:
            return want
    return names[0] if names else "Samantha"


def tts(parts_: list[tuple[str, str]], out: Path, engine: str, voice: str | None, ff: str) -> dict[str, Path]:
    """Each part -> out/segment<id>.wav (48 kHz mono, 0.3 s lead-in, 0.6 s tail)."""
    use_edge = engine == "edge" or (engine == "auto" and _has_edge())
    files = {}
    say_voice = None
    for pid, text in parts_:
        raw = out / f"segment{pid}_raw"
        wav = out / f"segment{pid}.wav"
        ok = False
        if use_edge:
            try:
                import asyncio
                import edge_tts
                v = voice or "en-US-AriaNeural"
                asyncio.run(edge_tts.Communicate(spoken(text), v, rate="-4%").save(str(raw) + ".mp3"))
                src, ok = Path(str(raw) + ".mp3"), True
                print(f"  voice {pid}: edge-tts {v}")
            except Exception as e:
                print(f"  edge-tts failed for {pid} ({str(e)[:80]}); falling back to macOS say")
                use_edge = False
        if not ok:
            say_voice = say_voice or pick_say_voice(voice if engine == "say" else None)
            src = Path(str(raw) + ".aiff")
            run(["say", "-v", say_voice, "-r", "150", "-o", str(src), spoken(text)])
            print(f"  voice {pid}: say {say_voice}")
        run([ff, "-y", "-i", str(src), "-af", "adelay=300|300,apad=pad_dur=0.6,loudnorm=I=-16:TP=-1.5",
             "-ar", "48000", "-ac", "1", str(wav)])
        files[pid] = wav
    return files


def _has_edge() -> bool:
    try:
        import edge_tts  # noqa: F401
        urllib.request.urlopen("https://speech.platform.bing.com", timeout=5)
    except urllib.error.HTTPError:
        return True  # reachable
    except Exception:
        return False
    return True


# ----------------------------------------------------------------------------- captions

def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def caption_chunks(text: str, max_words: int = 12) -> list[str]:
    """Each sentence -> near-equal chunks of at most max_words, broken after a comma when one is close."""
    out = []
    for s in sentences(text):
        w = s.split()
        n = -(-len(w) // max_words)
        while n > 1:
            target = round(len(w) / n)
            cut = target
            for k in sorted(range(max(2, target - 3), min(len(w) - 1, target + 3) + 1), key=lambda k: abs(k - target)):
                if w[k - 1].endswith(","):
                    cut = k
                    break
            out.append(" ".join(w[:cut]))
            w = w[cut:]
            n -= 1
        out.append(" ".join(w))
    return out


def build_srt(timeline: list[tuple[str, str, float, float]]) -> str:
    """timeline: (part id, narration, piece start in final video, speech length). Times shared out by word count."""
    lines, k = [], 1
    for _pid, text, start, speech in timeline:
        chunks = caption_chunks(text)
        total = sum(len(c.split()) for c in chunks) or 1
        t = start + 0.3
        span = max(speech - 0.9, 0.5)
        for c in chunks:
            d = span * len(c.split()) / total
            lines += [str(k), f"{srt_time(t)} --> {srt_time(t + d - 0.05)}", c, ""]
            k += 1
            t += d
    return "\n".join(lines)


# ----------------------------------------------------------------------------- Neptune (outside the take)

def neptune_status(url: str) -> dict:
    with urllib.request.urlopen(url.rstrip("/") + "/api/neptune/status", timeout=20) as r:
        return json.loads(r.read())


def neptune_wake(url: str, max_min: int = 15) -> bool:
    st = neptune_status(url)
    print(f"Amazon Neptune: {st.get('state')}")
    if st.get("state") == "available":
        return False
    if st.get("state") == "asleep":
        req = urllib.request.Request(url.rstrip("/") + "/api/neptune/wake", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30).read()
        print("  wake requested (about 11 minutes); polling every 20 s, at most %d min" % max_min)
    t0 = time.time()
    while time.time() - t0 < max_min * 60:
        time.sleep(20)
        s = neptune_status(url).get("state")
        print(f"  {int(time.time() - t0)} s: {s}")
        if s == "available":
            return True
    raise RuntimeError("Amazon Neptune did not become available within %d min" % max_min)


def neptune_stop():
    aws = shutil.which("aws")
    cmd = ["aws", "neptune", "stop-db-cluster", "--db-cluster-identifier", NEPTUNE_CLUSTER, "--region", NEPTUNE_REGION]
    if not aws:
        print("Stop Amazon Neptune now (rule D7):\n  " + " ".join(cmd))
        return
    r = subprocess.run(cmd, capture_output=True, text=True)
    print("Amazon Neptune stop:", "requested" if r.returncode == 0 else "FAILED - run by hand:\n  " + " ".join(cmd))


# ----------------------------------------------------------------------------- the browser take

class _Tee:
    def __init__(self, path):
        self.f, self.o = open(path, "w", encoding="utf-8"), sys.__stdout__

    def write(self, x):
        self.o.write(x)
        self.f.write(x)
        self.f.flush()

    def flush(self):
        self.o.flush()


class LabelMissing(Exception):
    pass


class Take:
    def __init__(self, page, out: Path, strict: bool):
        self.page, self.out, self.strict, self.missing = page, out, strict, []

    def loc(self, target: str):
        p = self.page
        kind, _, arg = target.partition(":") if re.match(r"^[a-z]+:", target) else ("css", "", target)
        if kind == "css":
            return p.locator(arg).filter(visible=True).first
        if kind == "role":
            return p.locator("#pop button", has_text=arg).first
        if kind == "example":
            box = p.locator("#xmain > *", has_text=arg).first
            return box.locator("button[data-x]", has_text="Run this example").first
        if kind == "tested":
            pill = p.locator("#pstrip [data-demo]", has_text=arg).filter(visible=True)
            return pill.first if pill.count() else p.locator(f'#tqlist [data-tq][data-label="{arg}"]').first
        if kind == "claimchip":
            ref, src = arg.split(":", 1)
            return p.locator(f'.claim[data-ref="{ref}"] a.chip', has_text=src).first
        if kind == "claim":
            return p.locator(f'.claim[data-ref="{arg}"]').first
        if kind == "outline":
            return p.locator(".blpin nav a, .blpin nav button", has_text=arg).first
        if kind == "sec":  # a report section, found by its claim numbers (titles change with the role)
            return self.section(arg).locator("h2").first
        if kind == "subgraph":
            return self.section(arg).locator("details.ev > summary").first
        if kind == "h2any":
            return p.locator("h1, h2, h3", has_text=arg).filter(visible=True).first
        if kind == "prune":
            return p.locator("h2, h3, h4", has_text=re.compile(r"prun", re.I)).filter(visible=True).first
        if kind == "h2":
            return p.locator("h2", has_text=arg).filter(visible=True).first
        if kind == "button":
            return p.locator("button", has_text=arg).filter(visible=True).first
        if kind == "text":
            return p.get_by_text(arg).filter(visible=True).first
        raise ValueError(target)

    def section(self, n: str):
        p = self.page
        return p.locator("section.card.accent", has=p.locator(f'.claim[data-ref^="{n}."]')).filter(visible=True).first

    def find(self, target: str, desc: str, timeout: float = 6000):
        el = self.loc(target)
        try:
            el.wait_for(state="visible", timeout=timeout)
            return el
        except Exception:
            shot = self.out / f"missing_{len(self.missing) + 1}.png"
            self.page.screenshot(path=str(shot))
            shot.with_suffix(".html").write_text(self.page.content(), encoding="utf-8")
            self.missing.append((desc, target, shot.name))
            print(f"  LABEL NOT FOUND: {desc}  [{target}]  screenshot {shot.name}")
            if self.strict:
                raise LabelMissing(desc)
            return None

    def outline(self, el, secs=0.6):
        el.scroll_into_view_if_needed(timeout=5000)
        el.evaluate("e => { e.dataset.oldOutline = e.style.outline; e.style.outline = '3px solid #f5a623';"
                    " e.style.outlineOffset = '3px'; }")
        self.page.wait_for_timeout(int(secs * 1000))
        el.evaluate("e => { e.style.outline = e.dataset.oldOutline || ''; }")

    def smooth_to(self, el):
        el.evaluate("e => e.scrollIntoView({behavior: 'smooth', block: 'center'})")
        self.page.wait_for_timeout(900)

    def do(self, kind: str, arg: str, desc: str, url: str):
        p = self.page
        if "thrombotic" in arg.lower():
            raise RuntimeError("never open the PTGS2 -> thrombotic disease drawer")
        if kind == "click":
            el = self.find(arg, desc)
            if el:
                self.outline(el)
                el.click()
        elif kind == "hover":
            el = self.find(arg, desc)
            if el:
                el.hover()
                self.outline(el, 0.8)
        elif kind in ("scrollto", "waitfor"):
            el = self.find(arg, desc, timeout=20000 if kind == "waitfor" else 6000)
            if el and kind == "scrollto":
                self.smooth_to(el)
        elif kind == "esc":
            p.keyboard.press("Escape")
        elif kind == "shot":
            if arg == "first":
                p.screenshot(path=str(self.out / "o05_first_screen.png"))
            elif arg == "drawer":
                p.wait_for_timeout(1200)
                p.locator("#drawer").screenshot(path=str(self.out / "o05_evidence_panel.png"))
        elif kind == "card":
            p.evaluate("""u => { const d = document.createElement('div');
              d.style.cssText = 'position:fixed;inset:0;z-index:99999;display:flex;flex-direction:column;align-items:center;'
                + 'justify-content:center;background:#0f1720;color:#fff;font:600 46px/1.3 system-ui,sans-serif;opacity:0;'
                + 'transition:opacity .8s';
              d.innerHTML = '<div>TrialMindKG</div><div style="font-size:26px;font-weight:400;margin:18px 0 34px;color:#c8d3df">'
                + 'From a failed trial to an evidence-backed next step</div><div style="font-size:30px;color:#f5a623">' + u + '</div>';
              document.body.appendChild(d); requestAnimationFrame(() => d.style.opacity = 1); }""", url)
        elif kind == "neptune":
            self.neptune_run(desc)
        elif kind == "pharostab":
            self.open_pharos(desc)
        elif kind == "limits":
            # the verifier list sits in the answer's "What is not known" block; scroll there even if
            # the browser reports it as not yet visible, then settle on the block itself if needed
            p = self.page
            h = p.locator('[data-stage="limits"] h2', has_text=arg).first
            blk = p.locator('[data-stage="limits"]').first
            for el in (h, blk):
                if el.count():
                    el.evaluate("e => e.scrollIntoView({behavior: 'smooth', block: 'center'})")
                    p.wait_for_timeout(900)
                    if el.is_visible():
                        return
            self.find('[data-stage="limits"]', desc, timeout=3000)

    def open_pharos(self, desc):
        p = self.page
        pill = p.locator("#pstrip [data-demo]").filter(visible=True)
        if pill.count() == 0:
            tab = p.locator("#tabPharos").filter(visible=True)
            if tab.count():
                self.outline(tab.first)
                tab.first.click()
            else:
                nav = p.locator('[data-go="start"]').filter(visible=True)
                if nav.count():
                    nav.first.click()
            p.wait_for_timeout(600)
        el = self.find("#pstrip", desc)
        if el:
            self.smooth_to(el)

    def neptune_run(self, desc):
        p = self.page
        box = p.locator("#pansw .nepctl").first
        if not box.is_visible():
            nav = p.locator('[data-go="start"]').filter(visible=True)
            if nav.count():
                nav.first.click()
                p.wait_for_timeout(800)
        if not box.is_visible():
            pill = p.locator("#pstrip [data-demo]", has_text=TESTED_Q).filter(visible=True)
            if pill.count():
                self.smooth_to(pill.first)
                pill.first.click()
        try:
            box.wait_for(state="visible", timeout=20000)
        except Exception:
            self.find("#pansw .nepctl", desc, timeout=1000)
            return
        box.scroll_into_view_if_needed()
        btn = box.locator("button", has_text=re.compile(r"^Run"))
        if btn.count() == 0:
            print("  Amazon Neptune is not available: holding on its line, no run")
            return
        before = box.locator(".nepline").inner_text() if box.locator(".nepline").count() else ""
        self.outline(btn.first)
        btn.first.click()
        t0 = time.time()
        while time.time() - t0 < 120:
            p.wait_for_timeout(500)
            line = box.locator(".nepline").inner_text() if box.locator(".nepline").count() else ""
            if line and line != before and "Running" not in line and box.locator(".spin").count() == 0:
                print(f"  Neptune line: {line[:140]}")
                p.wait_for_timeout(2500)
                return
        print("  Neptune result line did not appear within 120 s")


def rehearse(args, segs) -> list:
    """Fast pass over every step with no video and no Neptune: returns the labels that were not found."""
    from playwright.sync_api import sync_playwright

    chk = args.out / "check"
    chk.mkdir(parents=True, exist_ok=True)
    steps = plan(segs)
    vw, vh = args.viewport
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        page = browser.new_context(viewport={"width": vw, "height": vh}, device_scale_factor=1).new_page()
        page.goto(args.url, wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(2000)
        take = Take(page, chk, False)
        for pid, _ in parts(segs):
            for _f, kind, arg, desc in steps[pid]:
                if kind in ("shot", "card", "neptune"):
                    continue
                take.do(kind, arg, desc, args.url)
                page.wait_for_timeout(300)
        browser.close()
    return take.missing


def record(args, segs, audio: dict[str, Path], ff: str) -> tuple[Path, list[tuple[str, float, float]]]:
    from playwright.sync_api import sync_playwright

    out = args.out
    steps = plan(segs)
    vw, vh = args.viewport
    marks: list[tuple[str, float, float]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(viewport={"width": vw, "height": vh}, device_scale_factor=args.scale,
                                  record_video_dir=str(out / "_raw"), record_video_size={"width": vw, "height": vh},
                                  color_scheme="light", locale="en-US")
        page = ctx.new_page()
        v0 = time.monotonic()
        page.goto(args.url, wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(2500)
        take = Take(page, out, args.strict)
        for pid, _text in parts(segs, getattr(args, "neptune_shown", True)):
            if args.segment and pid.rstrip("ab") != str(args.segment):
                continue
            dur = wav_seconds(audio[pid]) if pid in audio else len(_text.split()) / WORDS_PER_S
            t0 = time.monotonic()
            print(f"segment {pid}  ({dur:.1f} s narration)")
            for frac, kind, arg, desc in steps[pid]:
                wait = t0 + frac * dur - time.monotonic()
                if wait > 0:
                    page.wait_for_timeout(int(wait * 1000))
                print(f"  {time.monotonic() - t0:5.1f} s  {desc}")
                take.do(kind, arg, desc, args.url)
            rest = t0 + dur + 0.3 - time.monotonic()
            if rest > 0:
                page.wait_for_timeout(int(rest * 1000))
            marks.append((pid, t0 - v0, time.monotonic() - v0))
        page.wait_for_timeout(500)
        vid = page.video.path()
        ctx.close()
        browser.close()
    take_path = out / "take.webm"
    shutil.move(vid, take_path)
    shutil.rmtree(out / "_raw", ignore_errors=True)
    if take.missing:
        print(f"\n{len(take.missing)} label(s) not found (their steps were skipped, nothing else was clicked):")
        for d, t, s in take.missing:
            print(f"  - {d}  [{t}]  {s}")
    return take_path, marks


# ----------------------------------------------------------------------------- assembly

def assemble(args, segs, audio, take: Path, marks, ff: str) -> Path:
    out = args.out
    texts = dict(parts(segs, getattr(args, "neptune_shown", True)))
    pieces = []  # (video file, ss, to, part id)
    for pid, a, b in marks:
        if pid == "6a" and args.neptune_clip:
            clip = Path(args.neptune_clip).expanduser()
            d = media_seconds(ff, clip)
            pieces.append((clip, max(0.0, d - 8.0), d, pid))
        else:
            pieces.append((take, a, b, pid))
    inputs, filt, timeline, t = [], [], [], 0.0
    for i, (vf, ss, to, pid) in enumerate(pieces):
        vdur = to - ss
        speech = wav_seconds(audio[pid])
        L = max(vdur, speech)
        inputs += ["-ss", f"{ss:.3f}", "-t", f"{vdur:.3f}", "-i", str(vf), "-i", str(audio[pid])]
        vi, ai = 2 * i, 2 * i + 1
        filt.append(f"[{vi}:v]fps=30,scale=1920:1080:force_original_aspect_ratio=decrease:flags=lanczos,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=white,"
                    f"setsar=1,tpad=stop_mode=clone:stop_duration={max(0.0, L - vdur):.3f},trim=duration={L:.3f},setpts=PTS-STARTPTS[v{i}]")
        filt.append(f"[{ai}:a]apad,atrim=duration={L:.3f},asetpts=PTS-STARTPTS[a{i}]")
        timeline.append((pid, texts[pid], t, speech))
        t += L
    n = len(pieces)
    filt.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]")
    srt = out / "tmkg_demo.srt"
    srt.write_text(build_srt(timeline), encoding="utf-8")
    soft = out / "tmkg_demo.mp4"
    run([ff, "-y", *inputs, "-i", str(srt), "-filter_complex", ";".join(filt), "-map", "[v]", "-map", "[a]",
         "-map", f"{2 * n}:s", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng",
         "-movflags", "+faststart", str(soft)])
    burned = out / "tmkg_demo_cc.mp4"
    try:
        with tempfile.TemporaryDirectory() as td:
            local = Path(td) / "c.srt"
            shutil.copy(srt, local)
            run([ff, "-y", "-i", str(soft), "-vf",
                 "subtitles=c.srt:force_style='FontName=Helvetica,FontSize=13,PrimaryColour=&H00FFFFFF,"
                 "OutlineColour=&H40000000,BackColour=&H40000000,BorderStyle=3,Outline=6,Shadow=0,MarginV=28'",
                 "-map", "0:v", "-map", "0:a", "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "copy",
                 "-movflags", "+faststart", str(burned)], cwd=td)
    except RuntimeError as e:
        print(f"Burned-in captions failed ({str(e).splitlines()[0]}); tmkg_demo.mp4 carries them as a track.")
        burned = soft
    print(f"\nVideo length {t // 60:.0f}:{t % 60:04.1f}" + ("  WARNING: over 4:00" if t > 240 else ""))
    return burned


def media_seconds(ff: str, f: Path) -> float:
    r = subprocess.run([ff, "-i", str(f)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    if not m:
        raise RuntimeError(f"cannot read the length of {f}")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


# ----------------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--out", type=Path, default=Path("~/Movies/tmkg_demo").expanduser())
    ap.add_argument("--dry-run", action="store_true", help="list every step and hold time, no browser")
    ap.add_argument("--segment", type=int, help="record one segment only (no final MP4)")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--screenshot", action="store_true", help="only the two white-paper PNGs (1440x900, scale 2)")
    ap.add_argument("--neptune", action="store_true", help="wake Amazon Neptune if asleep, record it answering, stop it after")
    ap.add_argument("--neptune-clip", help="use this screen recording (its last 8 s) for the Neptune moment")
    ap.add_argument("--tts", choices=["auto", "edge", "say"], default="auto")
    ap.add_argument("--voice", help="edge-tts voice (default en-US-AriaNeural) or macOS say voice")
    ap.add_argument("--viewport", type=lambda s: tuple(map(int, s.split("x"))), default=(1280, 720),
                    help="CSS viewport, recorded at this size and scaled to 1920x1080 (like a 150%% zoom)")
    ap.add_argument("--scale", type=float, default=2)
    ap.add_argument("--strict", action="store_true", help="stop at the first missing label")
    ap.add_argument("--skip-check", action="store_true", help="record without the label check first")
    args = ap.parse_args(argv)
    if not args.dry_run:
        args.out.mkdir(parents=True, exist_ok=True)
        sys.stdout = _Tee(args.out / "run_log.txt")

    segs = parse_script(SCRIPT_MD.read_text(encoding="utf-8"))
    steps = plan(segs)
    if args.dry_run:
        total = 0.0
        for pid, text in parts(segs):
            est = len(text.split()) / WORDS_PER_S
            total += est
            print(f"\nsegment {pid}  {len(text.split())} words  ~{est:.1f} s")
            for frac, kind, arg, desc in steps[pid]:
                print(f"  {frac * est:5.1f} s  {kind:8s} {desc}")
        print(f"\nestimated length {total // 60:.0f}:{total % 60:04.1f}")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    if args.screenshot:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch(headless=not args.headed)
            pg = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2).new_page()
            pg.goto(args.url, wait_until="networkidle", timeout=90000)
            pg.wait_for_timeout(2000)
            t = Take(pg, args.out, True)
            t.do("shot", "first", "", args.url)
            for _f, kind, arg, desc in steps["2"][:4] + steps["3"][:3]:
                if kind in ("click", "waitfor", "shot"):
                    t.do(kind, arg, desc, args.url)
            b.close()
        for f in ("o05_first_screen.png", "o05_evidence_panel.png"):
            print(args.out / f)
        return 0

    ff = ffmpeg_exe()
    if not args.skip_check:
        print("Check: walking every step once, no video (about a minute)")
        miss = rehearse(args, segs)
        if miss:
            print(f"\nCHECK FAILED: {len(miss)} label(s) not found; nothing was recorded and Amazon Neptune was not woken.")
            print(f"Page copies are in {args.out / 'check'}. Send them for a fix, or add --skip-check to record anyway.")
            return 2
        print("Check passed: every label was found.\n")
    woke = False
    if args.neptune and not args.neptune_clip:
        woke = neptune_wake(args.url)
    try:
        # the voice claims a Neptune answer only when a recorded clip of one is supplied; a live run can still
        # fail on screen after the voice is made, so the live take uses the neutral sentence
        nep_ok = bool(args.neptune_clip)
    except Exception:
        nep_ok = False
    args.neptune_shown = nep_ok
    if not nep_ok:
        print("Segment 6 first sentence spoken as: " + NO_NEPTUNE_6A)
    print("Voice:")
    audio = tts(parts(segs, nep_ok), args.out, args.tts, args.voice, ff)
    try:
        take, marks = record(args, segs, audio, ff)
    finally:
        if woke:
            neptune_stop()
    print(f"\ntake: {take}  ({media_seconds(ff, take):.1f} s)")
    if args.segment:
        return 0
    final = assemble(args, segs, audio, take, marks, ff)
    print(f"\nDONE  upload this file: {final}\n      captions file:     {args.out / 'tmkg_demo.srt'}")
    if args.neptune and not woke:
        print("Amazon Neptune was already awake; stop it when you are done (rule D7):\n  aws neptune stop-db-cluster "
              f"--db-cluster-identifier {NEPTUNE_CLUSTER} --region {NEPTUNE_REGION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
