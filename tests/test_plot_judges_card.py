"""scripts/plot_judges_card.py on toy result dicts (no data files needed)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import plot_judges_card as m  # noqa: E402


def _run(values, digest="d1", n_test=(10, 11, 12)):
    folds = [{"auroc_gnnpath": v, "n_test": n} for v, n in zip(values, n_test)]
    return {"per_seed": [{"folds": folds, "test_set_digest": digest}], "settings": {"test_set": {"digest": digest}}}


def _three(base=(0.60, 0.61, 0.62), a=(0.70, 0.72, 0.71), b=(0.69, 0.70, 0.73), **kw):
    return [("Pharos alone", _run(base)), ("Agent-built graph", _run(a, **kw)), ("Agent-built, pruned", _run(b))]


def test_series_pairs_folds_and_uses_compare_deltas():
    folds, values, deltas = m.series(_three())
    assert folds == [(0, 0), (0, 1), (0, 2)]
    assert values["Agent-built graph"] == [0.70, 0.72, 0.71]
    d = deltas["Agent-built graph"]
    assert d["n"] == 3 and d["wins"] == 3 and abs(d["mean"] - 0.1) < 1e-9 and d["digest_match"]
    assert set(deltas) == {"Agent-built graph", "Agent-built, pruned"}


def test_series_refuses_other_test_set_or_missing_folds():
    with pytest.raises(ValueError, match="different test set"):
        m.series(_three(digest="other"))
    runs = _three()
    runs[2][1]["per_seed"][0]["folds"].pop()
    with pytest.raises(ValueError, match="same folds"):
        m.series(runs)


def test_title_is_a_claim_only_when_every_fold_is_higher():
    _, _, deltas = m.series(_three())
    assert m.title_for(deltas) == m.CLAIM_TITLE.format(n=3)
    _, _, deltas = m.series(_three(b=(0.69, 0.60, 0.73)))      # one fold of the pruned run below the base
    assert m.title_for(deltas) == m.PLAIN_TITLE
    assert m.title_for({}) == m.PLAIN_TITLE


def test_delta_label_three_decimals():
    assert m.delta_label({"mean": 0.12351, "ci_low": 0.11666, "ci_high": 0.13036}) == \
        "+0.124 vs Pharos alone\n(95% CI +0.117 to +0.130)"


def test_footer_counts_and_fallback():
    r = _run((0.6, 0.6, 0.6))
    assert m.footer(r, 3) == ("n = 3 folds of 33 curated gene\u2013disease pairs (10 to 12 per fold); "
                              "thin lines join the same fold")
    assert m.footer(r, 4) == "n = 4 folds; thin lines join the same fold"


def test_draw_writes_png_without_internal_codes(tmp_path):
    out = tmp_path / "f.png"
    m.draw(_three(), out)
    assert out.stat().st_size > 1000
    for text in [m.CLAIM_TITLE, m.PLAIN_TITLE] + [lab for lab, _ in m.RUNS]:
        assert "G0" not in text and "G2" not in text and "G3" not in text and "@" not in text and "/" not in text
