"""CC-35 (review m9, m13, m14): the first screen names sources, not layer slugs, and has one plain sentence; the worked
examples start as one row; the Build tab says it is the trial graph; the fonts are self-hosted (no Google Fonts call); a
trial-report section with no verified option is not titled as if it had options."""
import re
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
import app as appmod  # noqa: E402
import specialists  # noqa: E402

V3 = (ROOT / "pipelines" / "app_page_v3.html").read_text(encoding="utf-8")


def test_no_google_fonts_and_the_local_fonts_are_served():
    assert "googleapis" not in V3 and "gstatic" not in V3 and '<link rel="stylesheet" href="/static/fonts/fonts.css">' in V3
    css = (ROOT / "static" / "fonts" / "fonts.css").read_text()
    files = set(re.findall(r"url\(/static/fonts/([\w.-]+\.woff2)\)", css))
    assert len(files) >= 3 and all((ROOT / "static" / "fonts" / f).stat().st_size > 1000 for f in files)
    for fam in ("Inter Tight", "Source Serif 4", "JetBrains Mono"):
        assert f"font-family: '{fam}'" in css
    assert len(list((ROOT / "static" / "fonts").glob("OFL_*.txt"))) == 3          # each font's licence ships with it
    c = TestClient(appmod.app)
    assert c.get("/static/fonts/fonts.css").status_code == 200
    assert c.get("/static/fonts/" + sorted(files)[0]).status_code == 200
    assert "COPY static ./static" in (ROOT / "aws" / "Dockerfile").read_text()


def test_first_screen_words():
    assert 'id="lede"' in V3 and "answers questions with the evidence behind every link" in V3
    assert "function layerNames(layers)" in V3 and "${ph.layers.join" not in V3 and "owner's Mac" not in V3
    assert ">About</button></nav>" in V3 and 'id="tabBuild">Build &amp; validation</button>' in V3      # PK3: the phone bar follows the top navigation
    assert "const XROW = 3" in V3 and "Show all ${xs.length} examples" in V3


def test_an_empty_redesign_section_is_not_called_options():
    assert specialists.section_title("redesign_advisor", 2) == "Evidence-Supported Redesign Options"
    assert specialists.section_title("redesign_advisor", 0) == "Redesign options: none supported by the evidence found"
    assert specialists.section_title("failure_diagnostician", 0) == specialists.ROLES["failure_diagnostician"][0]
