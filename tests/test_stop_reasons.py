"""Tests for pipelines/stop_reasons.py."""
import json
import nim, stop_reasons as sr


def test_quote_ok_is_verbatim_modulo_whitespace():
    assert sr.quote_ok("stopped  for futility", "Trial was stopped for\nfutility.")
    assert not sr.quote_ok("stopped for safety", "Trial was stopped for futility.")
    assert not sr.quote_ok("", "anything")


def test_offline_categories():
    n = nim.NIM(backend="offline")
    assert sr.categorize(n, "Phase III: stopped for futility; no clinical benefit.", "curated")["category"] == \
        "Futility at interim analysis"
    assert sr.categorize(n, "", "registry")["category"] == "Not stated in text"


def test_quote_not_in_text_is_rejected(monkeypatch):
    n = nim.NIM(backend="offline")
    monkeypatch.setitem(nim._HANDLERS, "extractor",
                        lambda m, s: json.dumps({"category": "Safety", "evidence_quote": "deaths in the treatment arm"}))
    r = sr.categorize(n, "The sponsor ended the study for business reasons.", "registry")
    assert r["status"].startswith("REJECTED")


def test_run_covers_every_trial_twice(tmp_path):
    rows = sr.run(nim.NIM(backend="offline"), path=tmp_path / "s.csv")
    assert len(rows) == 90 and {r["source"] for r in rows} == {"registry why_stopped", "curated what_failed"}
