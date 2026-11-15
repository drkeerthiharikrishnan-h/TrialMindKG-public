"""FIX1 F7 (gate 1, G1-AC-02): the calibration on the site is the committed one.

The hosted data root is synced from storage and held a calibration file of an older build (prune n 505) while the headline
came from the committed file (prune n 280): the data root's copy won. Report files (calibration, prune report, IntAct check)
are now read from the image copy first; frozen results and other files keep the data root first."""
import importlib.util
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
import numbers_page as npg  # noqa: E402


def stale_root(tmp_path):
    data = tmp_path / "data"
    (data / npg.CALIBRATION).parent.mkdir(parents=True)
    cal = json.loads((ROOT / npg.CALIBRATION).read_text())
    for r in cal["by_verdict"]:
        r["n"] = 505 if r["verdict"] == "prune" else 303449
    (data / npg.CALIBRATION).write_text(json.dumps(cal))
    (data / npg.PRUNE_REPORT).write_text("# an old prune report\n")
    (data / npg.LINKPRED).mkdir(parents=True)
    return data


def test_a_stale_calibration_in_the_data_root_loses_to_the_image_copy(tmp_path):
    data = stale_root(tmp_path)
    out = npg.numbers(data, fallback_root=ROOT)
    prune = next(r for r in out["calibration"]["by_verdict"] if r["verdict"] == "prune")
    committed = next(r for r in json.loads((ROOT / npg.CALIBRATION).read_text())["by_verdict"] if r["verdict"] == "prune")
    assert prune["n"] == committed["n"] != 505
    assert "prune_calibration.json" in out["fallback_files"]
    assert npg.source_file(data, "prune_calibration.json", fallback_root=ROOT) == ROOT / npg.CALIBRATION
    assert npg.source_file(data, "prune_report.md", fallback_root=ROOT) == ROOT / npg.PRUNE_REPORT


def test_without_an_image_copy_the_data_root_copy_is_read(tmp_path):
    data = stale_root(tmp_path)
    empty = tmp_path / "image"
    empty.mkdir()
    out = npg.numbers(data, fallback_root=empty)
    assert next(r for r in out["calibration"]["by_verdict"] if r["verdict"] == "prune")["n"] == 505


def test_frozen_results_keep_the_data_root_first(tmp_path):
    data = tmp_path / "data"
    (data / npg.LINKPRED).mkdir(parents=True)
    src = next((ROOT / npg.LINKPRED).glob("*@frozen_a.json"))
    shutil.copy(src, data / npg.LINKPRED / src.name)
    assert npg._first([data, ROOT], npg.LINKPRED / src.name) == (data / npg.LINKPRED / src.name, 0)
    assert npg._first([data, ROOT], npg.CALIBRATION) == (ROOT / npg.CALIBRATION, 1)


def hosted_check():
    spec = importlib.util.spec_from_file_location("hosted_check_fix1", ROOT / "scripts" / "hosted_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hosted_check_calibration_matches_prune_report(tmp_path):
    hc = hosted_check()
    report = (ROOT / npg.PRUNE_REPORT).read_text()
    good = dict(calibration=npg.numbers(ROOT)["calibration"])
    ok, detail = hc.check_calibration_matches_prune_report(good, report)
    assert ok, detail
    stale = dict(calibration=npg.numbers(stale_root(tmp_path), fallback_root=tmp_path / "none")["calibration"])
    ok, detail = hc.check_calibration_matches_prune_report(stale, report)
    assert not ok and "505" in detail
    assert not hc.check_calibration_matches_prune_report(good, "no table here")[0]
    names = [line.split()[1].rstrip(":") for line in hc.run_assertions(lambda p: b"{}" if p != "/v3" else b"")]
    assert "calibration_matches_prune_report" in names
