"""`calibrate_alert_thresholds.py` v1 testleri -- kalibrasyon cikti
semasi (gorevin acikca istedigi test odagi): yetersiz veri ->
INSUFFICIENT_DATA (mevcut varsayilan korunur), yeterli veri -> CALIBRATED
(gerekce + yanlis-pozitif riski dolu), sentetik etiketli olaylarin haric
tutulmasi, GUVEN SKORU (confidence) hesaplamasi, ONERI (proposal) yamasi
uretiminin OTOMATIK UYGULAMA icermedigi."""

from __future__ import annotations

import time

from calibrate_alert_thresholds import (
    CALIBRATED,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    INSUFFICIENT_DATA,
    MIN_SAMPLE_SIZE,
    ROBUST_SAMPLE_SIZE,
    calibrate_all,
    calibrate_multi_window,
    calibrate_ratio_alert,
    describe_change_impact,
    describe_sample_adequacy,
    render_calibration_v1_md,
    render_proposed_patch_yaml,
    score_confidence,
)
from model_gateway.metrics_sink import MetricEvent


def _event(name, labels, value=1.0, metric_type="counter", ts=None):
    return MetricEvent(ts=ts if ts is not None else time.time(), metric_type=metric_type, name=name, labels=labels, value=value)


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


# --- Guven skoru (confidence) -----------------------------------------


def test_confidence_low_when_no_window_has_sufficient_data():
    insufficient = calibrate_ratio_alert(
        alert_name="X", metric_basis="a/b", numerator=1, denominator=5, current_warn=0.01, current_crit=0.02
    )
    confidence, rationale = score_confidence({"24h": insufficient, "7d": insufficient, "14d": insufficient})
    assert confidence == CONFIDENCE_LOW
    assert "INSUFFICIENT_DATA" in rationale or "yeterli" in rationale.lower()


def test_confidence_low_when_only_one_window_has_sufficient_data():
    calibrated = calibrate_ratio_alert(
        alert_name="X", metric_basis="a/b", numerator=5, denominator=100, current_warn=0.01, current_crit=0.02
    )
    insufficient = calibrate_ratio_alert(
        alert_name="X", metric_basis="a/b", numerator=1, denominator=5, current_warn=0.01, current_crit=0.02
    )
    confidence, rationale = score_confidence({"24h": calibrated, "7d": insufficient, "14d": insufficient})
    assert confidence == CONFIDENCE_LOW
    assert "24h" in rationale


def test_confidence_high_when_multiple_windows_agree():
    consistent_a = calibrate_ratio_alert(
        alert_name="X", metric_basis="a/b", numerator=5, denominator=100, current_warn=0.01, current_crit=0.02
    )
    consistent_b = calibrate_ratio_alert(
        alert_name="X", metric_basis="a/b", numerator=15, denominator=300, current_warn=0.01, current_crit=0.02
    )
    confidence, rationale = score_confidence({"7d": consistent_a, "14d": consistent_b})
    assert confidence == CONFIDENCE_HIGH
    assert rationale


def test_confidence_medium_when_windows_disagree():
    low_rate = calibrate_ratio_alert(
        alert_name="X", metric_basis="a/b", numerator=1, denominator=100, current_warn=0.01, current_crit=0.02
    )
    high_rate = calibrate_ratio_alert(
        alert_name="X", metric_basis="a/b", numerator=50, denominator=100, current_warn=0.01, current_crit=0.02
    )
    confidence, rationale = score_confidence({"7d": low_rate, "14d": high_rate})
    assert confidence == CONFIDENCE_MEDIUM
    assert "TUTARSIZ" in rationale or "tutarsiz" in rationale.lower()


# --- Ornek yeterliligi + degisiklik-etkisi notu -------------------------


def test_describe_sample_adequacy_thresholds():
    assert "YETERSIZ" in describe_sample_adequacy(MIN_SAMPLE_SIZE - 1)
    assert "SINIRDA" in describe_sample_adequacy(MIN_SAMPLE_SIZE)
    assert "YETERLI" in describe_sample_adequacy(ROBUST_SAMPLE_SIZE)


def test_describe_change_impact_no_change():
    impact = describe_change_impact(0.01, 0.01)
    assert "AYNI" in impact


def test_describe_change_impact_loosens_or_tightens():
    looser = describe_change_impact(0.01, 0.05)
    assert "GEVSETIR" in looser
    tighter = describe_change_impact(0.05, 0.01)
    assert "SIKILASTIRIR" in tighter


# --- Coklu pencere kalibrasyonu + oneri (proposal) uretimi --------------


def _write_multi_window_events(base_ts, requests=100, null_intent=5):
    events = []
    for _ in range(requests):
        events.append(_event("model_gateway_requests_total", {"provider": "ollama", "result": "success"}, ts=base_ts))
    for _ in range(null_intent):
        events.append(_event("model_gateway_null_intent_total", {"terminal_reason": "x"}, ts=base_ts))
    return events


def test_calibrate_multi_window_produces_one_result_per_alert():
    now = time.time()
    events_by_window = {
        "24h": _write_multi_window_events(now, requests=100, null_intent=5),
        "7d": _write_multi_window_events(now, requests=300, null_intent=15),
        "14d": _write_multi_window_events(now, requests=300, null_intent=15),
    }
    results = calibrate_multi_window(
        events_by_window,
        current_null_intent_warn=0.01,
        current_null_intent_crit=0.02,
        current_fallback_spike_multiplier=3.0,
    )
    alert_names = {r.alert_name for r in results}
    assert alert_names == {
        "HIGH_NULL_INTENT_RATE",
        "FALLBACK_SPIKE",
        "PRIMARY_RESTRICTED_PERSISTENT",
        "PREFLIGHT_UNKNOWN_PERSISTENT",
    }

    null_intent_result = next(r for r in results if r.alert_name == "HIGH_NULL_INTENT_RATE")
    assert null_intent_result.confidence == CONFIDENCE_HIGH
    assert null_intent_result.primary_suggestion.status == CALIBRATED
    assert null_intent_result.env_vars == {
        "ALERT_NULL_INTENT_WARN": "0.1000",
        "ALERT_NULL_INTENT_CRIT": "0.2000",
    }


def test_calibrate_multi_window_alerts_without_env_var_have_empty_env_vars():
    now = time.time()
    events_by_window = {
        "24h": _write_multi_window_events(now),
        "7d": _write_multi_window_events(now),
        "14d": _write_multi_window_events(now),
    }
    results = calibrate_multi_window(
        events_by_window, current_null_intent_warn=0.01, current_null_intent_crit=0.02,
        current_fallback_spike_multiplier=3.0,
    )
    restricted = next(r for r in results if r.alert_name == "PRIMARY_RESTRICTED_PERSISTENT")
    assert restricted.env_vars == {}  # bu alert'in ozel bir ortam degiskeni yok


def test_render_proposed_patch_yaml_never_auto_applies():
    """Oneri dosyasi ACIKCA 'ASLA otomatik uygulanmaz' politikasini
    belirtmeli ve gercek bir config mutasyonu ICERMEMELI (yalnizca metin)."""
    now = time.time()
    events_by_window = {
        "24h": _write_multi_window_events(now, requests=100, null_intent=5),
        "7d": _write_multi_window_events(now, requests=300, null_intent=15),
        "14d": _write_multi_window_events(now, requests=300, null_intent=15),
    }
    results = calibrate_multi_window(
        events_by_window, current_null_intent_warn=0.01, current_null_intent_crit=0.02,
        current_fallback_spike_multiplier=3.0,
    )
    yaml_text = render_proposed_patch_yaml(results, generated_at="2026-08-13T00:00:00Z")

    assert "NEVER_AUTO_APPLY" in yaml_text
    assert "PROPOSAL_ONLY_NOT_APPLIED" in yaml_text
    assert "OTOMATIK UYGULANMAZ" in yaml_text
    assert "ALERT_NULL_INTENT_WARN" in yaml_text


def test_render_proposed_patch_yaml_omits_insufficient_data_alerts():
    insufficient_only = {
        "24h": [_event("model_gateway_requests_total", {}, ts=time.time()) for _ in range(5)],
        "7d": [_event("model_gateway_requests_total", {}, ts=time.time()) for _ in range(5)],
        "14d": [_event("model_gateway_requests_total", {}, ts=time.time()) for _ in range(5)],
    }
    results = calibrate_multi_window(
        insufficient_only, current_null_intent_warn=0.01, current_null_intent_crit=0.02,
        current_fallback_spike_multiplier=3.0,
    )
    yaml_text = render_proposed_patch_yaml(results, generated_at="2026-08-13T00:00:00Z")
    assert "ALERT_NULL_INTENT_WARN" not in yaml_text
    assert "bu calistirmada uygulanabilir bir oneri yok" in yaml_text


def test_render_calibration_v1_md_includes_all_alerts_and_honesty_note():
    now = time.time()
    events_by_window = {
        "24h": _write_multi_window_events(now),
        "7d": _write_multi_window_events(now),
        "14d": _write_multi_window_events(now),
    }
    results = calibrate_multi_window(
        events_by_window, current_null_intent_warn=0.01, current_null_intent_crit=0.02,
        current_fallback_spike_multiplier=3.0,
    )
    md = render_calibration_v1_md(
        results, generated_at="2026-08-13T00:00:00Z", window_event_counts={"24h": 105, "7d": 105, "14d": 105}
    )

    assert "HIGH_NULL_INTENT_RATE" in md
    assert "FALLBACK_SPIKE" in md
    assert "PRIMARY_RESTRICTED_PERSISTENT" in md
    assert "PREFLIGHT_UNKNOWN_PERSISTENT" in md
    assert "DURUSTLUK NOTU" in md
    assert "NEVER_AUTO_APPLY" in md or "ASLA config'i otomatik degistirmez" in md


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
        ["calibrate_alert_thresholds.py", "--jsonl-path", str(jsonl_path), "--output-dir", str(out_dir)],
    )
    import calibrate_alert_thresholds as mod

    exit_code = mod.main()
    assert exit_code == 0

    written = (out_dir / "calibration_v1.md").read_text(encoding="utf-8")
    assert "0.1000" in written  # 5/50 = 0.10, kalibre edilmis oran gorunmeli
    assert "1000" not in written.split("Pencere olay sayilari")[1].split("\n")[0]  # sentetik 1000 olay sayilmamali
    assert (out_dir / "calibration_v1.json").exists()
    assert (out_dir / "proposed_threshold_patch.yaml").exists()
