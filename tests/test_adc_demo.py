"""Tests for the ADC demo logic. All offline."""
import adc_demo as ad

def test_exact_ci_edge_cases():
    assert ad.cp_ci(0, 10)[0] == 0.0 and ad.cp_ci(10, 10)[1] == 1.0

def test_sample_size_for_upri_like_effect():
    n, _ = ad.n_to_exclude(0.16)
    assert n == 588

def test_synthetic_data_is_labelled_and_reproducible():
    a, b = ad.make_synthetic_internal(), ad.make_synthetic_internal()
    assert len(a["ADSL"]) == 96
    assert (a["ADSL"]["DATACLASS"] == ad.SYN).all()
    assert a["ADRS"]["CONFIRMED"].equals(b["ADRS"]["CONFIRMED"])

def test_analysis_reports_all_tps_bands():
    s = ad.analyse_internal(ad.make_synthetic_internal())
    assert set(s["orr_by_tps"]) == {"<10", "10-74", ">=75"}
