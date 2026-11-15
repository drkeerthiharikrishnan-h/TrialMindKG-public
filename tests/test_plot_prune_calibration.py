"""scripts/plot_prune_calibration.py on a toy calibration dict (no data files needed)."""
import sys
sys.path.insert(0, "scripts")

import plot_prune_calibration as m


def _cal(strong=(0.05, 0.16, 0.06, 0.49), any_=(0.07, 0.18, 0.08, 0.54)):
    dec = [{"decile": i + 1, "conf_min": 0.1 * i, "conf_max": 0.1 * (i + 1), "n": 100 + (i == 0), "in_ot": a, "ot_strong": s}
           for i, (a, s) in enumerate(zip(any_, strong))]
    return {"by_decile": dec, "ot_threshold": 0.2, "n_non_text": 401,
            "by_verdict": [{"verdict": "keep", "n": 398}, {"verdict": "prune", "n": 3}],
            "by_source": [{"source": "UniProt Disease", "n": 55}, {"source": "CTD", "n": 346}],
            "by_decile_source": [{"decile": 2, "source": "UniProt Disease", "n": 20}, {"decile": 2, "source": "CTD", "n": 80}]}


def test_series_in_percent_and_sorted():
    cal = _cal()
    cal["by_decile"].reverse()
    dec, any_, strong = m.series(cal)
    assert [d["decile"] for d in dec] == [1, 2, 3, 4]
    assert [round(v, 6) for v in strong] == [5, 16, 6, 49] and [round(v, 6) for v in any_] == [7, 18, 8, 54]


def test_title_is_a_claim_only_when_true():
    assert m.title_for([7, 18, 8, 54], [5, 16, 6, 49]) == m.CLAIM_TITLE
    assert m.title_for([7, 18, 8, 54], [5, 50, 6, 49]) == m.PLAIN_TITLE      # another tenth is higher on one line
    assert m.title_for([7, 54, 8, 54], [5, 16, 6, 49]) == m.PLAIN_TITLE      # a tie is not "most often"
    assert m.title_for([7], [5]) == m.PLAIN_TITLE


def test_decile_note_reads_the_tables():
    cal = _cal()
    assert m.decile_note(cal, 2, "UniProt Disease") == "tenth 2 includes 20 of the 55\nUniProt Disease edges"
    assert m.decile_note(cal, 3, "UniProt Disease") is None and m.decile_note(cal, 2, "Monarch") is None
    del cal["by_decile_source"]
    assert m.decile_note(cal, 2, "UniProt Disease") is None                  # older JSON without the table


def test_footer_counts():
    cal = _cal()
    dec, _, _ = m.series(cal)
    f = m.footer(cal, dec)
    assert f.startswith("401 gene\u2013disease edges") and "100 to 101 per tenth" in f and "3 of these edges are pruned" in f
    del cal["by_verdict"]
    assert "pruned" not in m.footer(cal, dec)


def test_plot_writes_a_png_without_internal_terms(tmp_path):
    out = tmp_path / "figs" / "cal.png"
    drawn = m.plot_deciles(_cal(), out)
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and out.stat().st_size > 10_000
    assert drawn["title"] == m.CLAIM_TITLE and drawn["note"].startswith("tenth 2 includes 20 of the 55")
    shown = " ".join([drawn["title"], drawn["note"], drawn["footer"]])
    for bad in ("outputs/", ".json", "G0", "G2", "G3", "tinx_by_verdict", "ot_strong"):
        assert bad not in shown


def test_main_default_note_can_be_changed(tmp_path, capsys):
    import json
    cal = tmp_path / "cal.json"
    cal.write_text(json.dumps(_cal()))
    m.main(["--cal", str(cal), "--out", str(tmp_path / "x.png"), "--note-decile", "2", "--note-source", "CTD"])
    assert "tenth 2 includes 80 of the 346" in capsys.readouterr().out and (tmp_path / "x.png").is_file()
