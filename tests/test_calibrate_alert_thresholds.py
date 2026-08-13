"""`calibrate_alert_thresholds.py` testleri -- kalibrasyon cikti semasi
(gorevin acikca istedigi test odagi): yetersiz veri -> INSUFFICIENT_DATA
(mevcut varsayilan korunur), yeterli veri -> CALIBRATED (gerekce +
yanlis-pozitif riski dolu), sentetik etiketli olaylarin haric tutulmasi."""

from __future__ import annotations

import time

from calibrate_alert_thresholds import (
    CALIBRATED,
    INSUFFICIENT_DATA,
    MIN_SAMPLE_SIZE,
    calibrate_all,
    calibrate_ratio_alert,
    render_calibration_md,
)
from model_gateway.metrics_sink import MetricEvent


def _event(name, labels, value=1.0, metric_type="counter"):
    return MetricEvent(ts=time.time(), metric_type=metric_type, name=name, labels=labels, value=value)


def test_insufficient_data_below_min_sample_size_keeps_current_defaults():
    result = calibrate_ratio_alert(
        alert_name="X",
        metric_basis="a/b",
        numerator=1,
        denominator=MIN_SAMPLE_SIZE - 1,
        current_warn=0.01,
        current_crit=0.02,
    )
    assert result.status == INSUFFICIENT_DATA
    assert result.suggested_warn == 0.01
    assert result.suggested_crit == 0.02
    assert result.observed_rate is None
    assert "yetersiz" in result.rationale.lower()


def test_calibrated_when_sample_size_meets_minimum():
    result = calibrate_ratio_alert(
        alert_name="X",
        metric_basis="a/b",
        numerator=5,
        denominator=100,
        current_warn=0.01,
        current_crit=0.02,
    )
    assert result.status == CALIBRATED
    assert result.observed_rate == 0.05
    assert result.suggested_warn == 0.10  # 0.05 * 2x
    assert result.suggested_crit == 0.20  # 0.05 * 4x
    assert result.false_positive_risk  # dolu olmali


def test_calibrated_suggestion_never_drops_below_current_default():
    """Gozlenen oran cok dusukse, oneri MEVCUT varsayilanin ALTINA
    dusurulmemeli (kucuk orneklemde asiri-hassas esik riski)."""
    result = calibrate_ratio_alert(
        alert_name="X", metric_basis="a/b", numerator=1, denominator=1000,
        current_warn=0.5, current_crit=0.9,
    )
    assert result.suggested_warn >= result.current_warn
    assert result.suggested_crit >= result.current_crit


def test_calibrate_all_excludes_synthetic_labeled_events_via_caller_filter():
    """`calibrate_all`'a verilen olaylarin ONCEDEN sentetik-filtrelenmis
    olmasi beklenir (main() bunu yapar) -- burada dogrudan karisim
    verilirse synthetic olaylar da sayilir, bu yuzden CLI'nin filtreleme
    sorumlulugunu ayrica dogruluyoruz (bkz. test_main_excludes_synthetic)."""
    events = [_event("model_gateway_requests_total", {"provider": "ollama"}) for _ in range(50)]
    events += [_event("model_gateway_null_intent_total", {}) for _ in range(5)]

    suggestions = calibrate_all(
        events,
        current_null_intent_warn=0.01,
        current_null_intent_crit=0.02,
        current_fallback_spike_multiplier=3.0,
    )
    null_intent = next(s for s in suggestions if s.alert_name == "HIGH_NULL_INTENT_RATE")
    assert null_intent.status == CALIBRATED
    assert null_intent.observed_rate == 5 / 50


def test_render_calibration_md_includes_all_alerts_and_honesty_note():
    events = [_event("model_gateway_requests_total", {}) for _ in range(50)]
    suggestions = calibrate_all(
        events, current_null_intent_warn=0.01, current_null_intent_crit=0.02,
        current_fallback_spike_multiplier=3.0,
    )
    md = render_calibration_md(suggestions, generated_at="2026-08-13T00:00:00Z", window_hours=24, total_events_in_window=50)

    assert "HIGH_NULL_INTENT_RATE" in md
    assert "FALLBACK_SPIKE" in md
    assert "PRIMARY_RESTRICTED_PERSISTENT" in md
    assert "PREFLIGHT_UNKNOWN_PERSISTENT" in md
    assert "DURUSTLUK NOTU" in md


def test_main_excludes_synthetic_events_end_to_end(tmp_path, monkeypatch):
    """Uctan uca: JSONL dosyasina hem gercek hem sentetik-etiketli
    olaylar yazilir -- yalnizca gercek olanlar kalibrasyona girmeli."""
    from model_gateway.metrics_sink import JsonlAppendSink

    jsonl_path = tmp_path / "m.jsonl"
    sink = JsonlAppendSink(jsonl_path, max_mb=50, retention_days=7)
    for _ in range(50):
        sink.write(_event("model_gateway_requests_total", {"provider": "ollama", "result": "success"}))
    for _ in range(5):
        sink.write(_event("model_gateway_null_intent_total", {}))
    for _ in range(1000):
        sink.write(_event("model_gateway_null_intent_total", {"synthetic": "true"}))

    out_dir = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv",
        ["calibrate_alert_thresholds.py", "--jsonl-path", str(jsonl_path), "--output-dir", str(out_dir), "--hours", "24"],
    )
    import calibrate_alert_thresholds as mod

    exit_code = mod.main()
    assert exit_code == 0

    written = (out_dir / "calibration.md").read_text(encoding="utf-8")
    assert "0.1000" in written or "0.10" in written  # 5/50 = 0.10, kalibre edilmis oran gorunmeli
    assert "1000" not in written.split("Pencere:")[1].split("\n")[0]  # sentetik 1000 olay pencere sayacina KATILMAMALI
