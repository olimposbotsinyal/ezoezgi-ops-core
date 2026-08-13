"""emit_synthetic_gateway_signals.py testleri.

Gercek dosya sistemi/servis gerektirmez -- izole bir MetricsRegistry'ye
yazar, yalnizca donen sonucu ve registry snapshot'ini kontrol eder.
"""

from __future__ import annotations

from emit_synthetic_gateway_signals import MODES, run_mode
from model_gateway.metrics import MetricsRegistry


def test_synthetic_signal_labels_include_synthetic_true():
    for mode in MODES:
        metrics = MetricsRegistry(enabled=True)

        result = run_mode(mode, metrics, count=3)

        assert result["mode"] == mode
        assert result["synthetic"] is True

        snap = metrics.snapshot()
        all_labeled_samples = snap["counters"] + snap["gauges"]
        assert len(all_labeled_samples) > 0, f"{mode} hicbir metrik ornegi uretmedi"
        for sample in all_labeled_samples:
            assert sample["labels"].get("synthetic") == "true", (
                f"{mode} icin {sample['name']} synthetic=true etiketi tasimiyor"
            )


def test_fallback_spike_emits_expected_count():
    metrics = MetricsRegistry(enabled=True)

    run_mode("fallback-spike", metrics, count=7)

    snap = metrics.snapshot()
    fallback_counter = next(c for c in snap["counters"] if c["name"] == "model_gateway_fallback_total")
    assert fallback_counter["value"] == 7.0


def test_circuit_open_stuck_sets_gauge_to_one():
    metrics = MetricsRegistry(enabled=True)

    run_mode("circuit-open-stuck", metrics, count=1)

    snap = metrics.snapshot()
    gauge = next(g for g in snap["gauges"] if g["name"] == "model_gateway_circuit_open")
    assert gauge["value"] == 1.0
    assert gauge["labels"]["synthetic"] == "true"


def test_unknown_mode_raises_value_error():
    import pytest

    metrics = MetricsRegistry(enabled=True)

    with pytest.raises(ValueError):
        run_mode("not-a-real-mode", metrics, count=1)
