"""Offline checks of scripts/record_demo.py: no browser, no network, no audio."""
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("record_demo", REPO / "scripts" / "record_demo.py")
rd = importlib.util.module_from_spec(spec)
sys.modules["record_demo"] = rd
spec.loader.exec_module(rd)
SEGS = rd.parse_script((REPO / "docs" / "VIDEO_SCRIPT.md").read_text(encoding="utf-8"))


def test_seven_segments_with_narration():
    assert [s.num for s in SEGS] == list(range(1, 8))
    assert all(s.words > 20 for s in SEGS)
    assert SEGS[0].narration.startswith("A Phase III trial fails.")


def test_narration_fits_under_four_minutes():
    total = sum(s.words for s in SEGS) / rd.WORDS_PER_S
    assert 180 < total < 240, total


def test_every_part_has_steps_and_steps_are_ordered():
    steps = rd.plan(SEGS)
    ids = [pid for pid, _ in rd.parts(SEGS)]
    assert ids == ["1", "2", "3", "4", "5", "6a", "6b", "7"]
    for pid in ids:
        fr = [x[0] for x in steps[pid]]
        assert fr and fr == sorted(fr) and 0 <= fr[0] and fr[-1] < 1


def test_labels_named_by_the_script_are_used():
    flat = " ".join(d for v in rd.plan(SEGS).values() for *_, d in v)
    for label in ["Role: choose", "Clinician", "Worked examples for this role (from the failed-trial programs)",
                  "Run this example", "Subgraph used (...)", "Pharos alone", "Agent-built graph",
                  "Run this on Amazon Neptune", "Downloads", "The agent loop, step by step"]:
        assert label in flat, label


def test_never_ptgs2_thrombotic_and_never_types():
    src = (REPO / "scripts" / "record_demo.py").read_text()
    assert ".fill(" not in src and ".type(" not in src
    assert not any("thrombotic" in a.lower() for v in rd.plan(SEGS).values() for _, _, a, _ in v)


def test_segment6_split_and_honest_fallback():
    with_n = dict(rd.parts(SEGS, True))
    without = dict(rd.parts(SEGS, False))
    assert "answers a question there" in with_n["6a"]
    assert "answers a question there" not in without["6a"]
    assert with_n["6b"] == without["6b"] and with_n["6b"].startswith("It uses standard identifiers")


def test_srt_is_well_formed_and_monotonic():
    tl, t = [], 0.0
    for pid, text in rd.parts(SEGS):
        d = len(text.split()) / rd.WORDS_PER_S + 1
        tl.append((pid, text, t, d))
        t += d
    srt = rd.build_srt(tl)
    blocks = [b.splitlines() for b in srt.strip().split("\n\n")]
    starts = [b[1].split(" --> ")[0] for b in blocks]
    assert starts == sorted(starts)
    assert all(len(b[2].split()) <= 12 for b in blocks)
    joined = " ".join(b[2] for b in blocks)
    assert "0.124 AUROC" in joined and "TrialMindKG" in joined


def test_spoken_forms_only_change_audio():
    assert "A U ROC" in rd.spoken("by 0.124 AUROC")
    assert rd.caption_chunks("by 0.124 AUROC.") == ["by 0.124 AUROC."]


def test_dry_run_prints_every_step(capsys):
    assert rd.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "segment 6a" in out and "estimated length" in out
