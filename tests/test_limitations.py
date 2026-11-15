"""PK5: docs/LIMITATIONS.md is in user language and every number in it is the one in the committed file it names."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
import headline  # noqa: E402

TEXT = (ROOT / "docs" / "LIMITATIONS.md").read_text(encoding="utf-8")


def test_the_sections_the_packet_asks_for_are_there():
    for part in ("What it is for", "Where the data come from, and how old they are", "What the graph does not cover",
                 "Keep and prune verdicts are estimates", "Predicted links are hypotheses", "Licences and how to cite"):
        assert re.search(rf"^## \d+\. {re.escape(part)}$", TEXT, re.M), part
    assert "not** a medical device" in TEXT and "SYNTHETIC" in TEXT                # CLAUDE.md rule 4
    assert "docs/LIMITATIONS.md" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_numbers_are_the_ones_in_the_committed_files():
    p = headline.prune_numbers(ROOT)
    c = headline.comparisons(ROOT)
    assert f"{p['scored']:,} Pharos links" in TEXT and f"{p['pruned']:,}" in TEXT and f"({p['pruned_share']:.2f}%)" in TEXT
    assert f"{p['nontext_gd_pruned_in_ot']:.1f}% of the pruned" in TEXT and f"{p['nontext_gd_kept_in_ot']:.1f}% of the kept" in TEXT
    assert f"{c['paths_g2']['mean']:+.3f} AUROC" in TEXT
    share = round(100 * (1 - c["paths_nomondo"]["mean"] / c["paths_g2_e008"]["mean"]))
    assert 70 <= share <= 80 and "three quarters" in TEXT
    # "A plain graph neural network ... gained nothing": true only while neither plain model's interval lies above 0
    assert c["rgcn_g2"]["ci_low"] <= 0 and "gained nothing from the added layers" in TEXT
    cal = (ROOT / "outputs" / "pharos" / "prune_calibration.md").read_text(encoding="utf-8")
    m = re.search(r"([\d,]+) non-text-mining \+ ([\d,]+) text-mining", cal)
    assert m and f"{m.group(2)} are text" in TEXT and f"{m.group(1)} are not" in TEXT
    nov = (ROOT / "outputs" / "linkpred" / "predictions_novelty.md").read_text(encoding="utf-8")
    n = re.search(r"The model proposes ([\d,]+) gene-disease links", nov).group(1)
    assert f"proposes {n} gene-disease links" in TEXT
    import json
    card = json.loads((ROOT / "outputs" / "schema_card.json").read_text())
    assert f"{len(card['classes'])} kinds of node" in TEXT
    assert "34 failed drug programs" in TEXT and "34 failed" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_no_internal_words_in_the_user_page():
    """User language: no graph codes, run tags, packet ids or model-operations words."""
    for bad in (r"\bG[0-4]\b", r"\bCS-\d", r"\bCC-\d", r"\bPK\d", r"@diag", r"_cs9", r"\bR-GCN\b", r"\bRotatE\b", r"\bfold", r"\bepoch",
                r"heterodata", r"\bledger\b"):
        assert not re.search(bad, TEXT), bad
