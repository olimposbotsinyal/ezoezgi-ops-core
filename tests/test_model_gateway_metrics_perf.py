"""Artimli aggregator (`IncrementalAggregator`) testleri -- performans
donusumunun (Commit K) METRIK SEMANTIGINI degistirmedigini
(tam-yeniden-tarama ile birebir ayni sonuc) ve yeni guardrail/
self-metric davranislarini dogrular. Onbellek (`CachedMetricsRenderer`,
Commit L) testleri icin bkz. test_model_gateway_metrics_cache.py.

Gercek gecici dosyalar kullanilir (tmp_path) -- ag/dis servis gerektirmez.
"""

from __future__ import annotations

import time

from model_gateway.metrics_aggregate import IncrementalAggregator, aggregate
from model_gateway.metrics_index import AggregatorState, FileOffset, file_identity, load_state, save_state
from model_gateway.metrics_sink import JsonlAppendSink, MetricEvent


def test_incremental_read_processes_only_new_events(tmp_path):
    """Ikinci `refresh()` cagrisi, yalnizca ARADA eklenen olayi
    islemeli -- ilk cagrida zaten okunmus satirlari YENIDEN parse
    etmemeli (bkz. `lines_ingested_total` sayaci)."""
    jsonl = tmp_path / "m.jsonl"
    state = tmp_path / "m.state.json"
    sink = JsonlAppendSink(jsonl, max_mb=50, retention_days=7)

    sink.write(MetricEvent(ts=time.time(), metric_type="counter", name="x", labels={}, value=1.0))
    inc = IncrementalAggregator(jsonl, state, window_minutes=60)
    inc.refresh()
    assert inc.lines_ingested_total == 1

    sink.write(MetricEvent(ts=time.time(), metric_type="counter", name="x", labels={}, value=1.0))
    r2 = inc.refresh()
    assert inc.lines_ingested_total == 2  # 1 (onceki) + 1 (yeni) -- 2 kez okunmus DEGIL
    assert r2.snapshot()["counters"][0]["value"] == 2.0

    # Ucuncu refresh -- yeni yazma yok, ingest sayaci artmamali.
    inc.refresh()
    assert inc.lines_ingested_total == 2


def test_output_equivalence_vs_full_rescan_reference(tmp_path):
    """Ayni veri kumesinde, artimli aggregator ile tam-yeniden-tarama
    (`aggregate()`, referans) BIREBIR ayni Prometheus goruntusunu
    uretmeli -- performans optimizasyonu metrik semantigini degistirmez."""
    jsonl = tmp_path / "m.jsonl"
    state = tmp_path / "m.state.json"
    sink = JsonlAppendSink(jsonl, max_mb=50, retention_days=7)

    for i in range(5):
        sink.write(
            MetricEvent(
                ts=time.time(),
                metric_type="counter",
                name="model_gateway_requests_total",
                labels={"provider": "ollama", "result": "success"},
                value=1.0,
            )
        )
    sink.write(MetricEvent(ts=time.time(), metric_type="histogram", name="latency_ms", labels={}, value=42.0))
    sink.write(MetricEvent(ts=time.time(), metric_type="gauge", name="health", labels={}, value=1.0))

    inc = IncrementalAggregator(jsonl, state, window_minutes=60)
    inc_registry = inc.refresh()
    ref_registry = aggregate(jsonl, window_minutes=60)

    assert inc_registry.snapshot() == ref_registry.snapshot()
    assert inc_registry.render_prometheus_text() == ref_registry.render_prometheus_text()


def test_rotated_file_continuity_no_double_count_no_loss(tmp_path):
    """Rotasyon (dosya adi degisimi) sirasinda hicbir olay iki kez
    sayilmamali VE hicbir olay kaybolmamali -- dosya kimligi (st_dev,
    st_ino) rename'e karsi seffaf calismali."""
    jsonl = tmp_path / "m.jsonl"
    state = tmp_path / "m.state.json"
    sink = JsonlAppendSink(jsonl, max_mb=0.0005, retention_days=7)  # kucuk -- rotasyonu zorlar
    inc = IncrementalAggregator(jsonl, state, window_minutes=60)

    total = 0
    for i in range(40):
        sink.write(MetricEvent(ts=time.time(), metric_type="counter", name="rot_total", labels={}, value=1.0))
        total += 1
        if i % 7 == 0:
            inc.refresh()  # rotasyon sinirinin ORTASINDA da refresh cagir

    final = inc.refresh()
    value = next(c["value"] for c in final.snapshot()["counters"] if c["name"] == "rot_total")
    assert value == total

    rotated_files = list(tmp_path.glob("m.*.rotated.jsonl"))
    assert len(rotated_files) >= 1, "test rotasyonu gercekten tetiklemeli"


def test_corrupted_state_auto_heals(tmp_path):
    """Bozuk bir state dosyasi crash'e yol acmamali -- kontrollu
    sekilde bos durumdan yeniden insa edilmeli (mevcut JSONL'den tam
    okuma), veri kaybi olmadan."""
    jsonl = tmp_path / "m.jsonl"
    state = tmp_path / "m.state.json"
    state.write_text("{not valid json", encoding="utf-8")

    sink = JsonlAppendSink(jsonl, max_mb=50, retention_days=7)
    sink.write(MetricEvent(ts=time.time(), metric_type="counter", name="x", labels={}, value=1.0))

    inc = IncrementalAggregator(jsonl, state, window_minutes=60)
    registry = inc.refresh()  # crash etmemeli

    assert registry.snapshot()["counters"][0]["value"] == 1.0


def test_missing_state_file_is_normal_not_an_error(tmp_path):
    jsonl = tmp_path / "m.jsonl"
    state = tmp_path / "does_not_exist.state.json"
    assert load_state(state) is None  # normal, hata degil


def test_state_roundtrip_via_load_save(tmp_path):
    state_path = tmp_path / "m.state.json"
    original = AggregatorState(window_minutes=60)
    original.files["1:2"] = FileOffset(path="m.jsonl", offset=123)
    original.events = [MetricEvent(ts=1.0, metric_type="counter", name="x", labels={"a": "1"}, value=2.0)]

    assert save_state(state_path, original) is True
    loaded = load_state(state_path)

    assert loaded is not None
    assert loaded.window_minutes == 60
    assert loaded.files["1:2"].offset == 123
    assert loaded.events[0].name == "x"
    assert loaded.events[0].value == 2.0


def test_file_identity_stable_across_rename(tmp_path):
    """`file_identity()`, dosya rename edildiginde AYNI kalmali --
    rotasyon takibinin dayandigi temel garanti."""
    original_path = tmp_path / "a.jsonl"
    original_path.write_text("x\n", encoding="utf-8")

    identity_before = file_identity(original_path)
    renamed_path = tmp_path / "a.renamed.jsonl"
    original_path.rename(renamed_path)
    identity_after = file_identity(renamed_path)

    assert identity_before is not None
    assert identity_before == identity_after


def test_max_series_cap_drops_excess_series_and_increments_self_metric(tmp_path):
    jsonl = tmp_path / "m.jsonl"
    state = tmp_path / "m.state.json"
    sink = JsonlAppendSink(jsonl, max_mb=50, retention_days=7, cardinality_cap=1000)
    for i in range(10):
        sink.write(MetricEvent(ts=time.time(), metric_type="counter", name="noisy", labels={"id": str(i)}, value=1.0))

    inc = IncrementalAggregator(jsonl, state, window_minutes=60, max_series=3)
    registry = inc.refresh()

    assert len(registry.snapshot()["counters"]) == 3
    assert inc.series_dropped == 7
    assert inc.self_metrics()["metrics_aggregator_series_dropped_total"] == 7.0


def test_max_events_per_scrape_cap_defers_and_catches_up(tmp_path):
    jsonl = tmp_path / "m.jsonl"
    state = tmp_path / "m.state.json"
    sink = JsonlAppendSink(jsonl, max_mb=50, retention_days=7)
    for _ in range(20):
        sink.write(MetricEvent(ts=time.time(), metric_type="counter", name="ev", labels={}, value=1.0))

    inc = IncrementalAggregator(jsonl, state, window_minutes=60, max_events_per_scrape=5)
    r1 = inc.refresh()
    assert r1.snapshot()["counters"][0]["value"] == 5.0
    assert inc.events_deferred >= 1
    assert inc.self_metrics()["metrics_aggregator_events_deferred_total"] >= 1.0

    # Birkac refresh sonra tumu yakalanmali (veri kaybi yok, yalnizca ertelenmis).
    for _ in range(5):
        r = inc.refresh()
    assert r.snapshot()["counters"][0]["value"] == 20.0


def test_existing_metric_counters_still_reported_via_incremental_path(tmp_path):
    """Geriye-donuk uyumluluk: mevcut sayac/histogram/gauge davranisi,
    artimli yol uzerinden de birebir ayni calismali."""
    jsonl = tmp_path / "m.jsonl"
    state = tmp_path / "m.state.json"
    sink = JsonlAppendSink(jsonl, max_mb=50, retention_days=7)
    sink.write(
        MetricEvent(
            ts=time.time(),
            metric_type="counter",
            name="model_gateway_requests_total",
            labels={"provider": "ollama", "result": "success"},
            value=1.0,
        )
    )
    sink.write(MetricEvent(ts=time.time(), metric_type="histogram", name="model_gateway_generate_latency_ms", labels={"provider": "ollama"}, value=123.0))
    sink.write(MetricEvent(ts=time.time(), metric_type="gauge", name="model_gateway_provider_health", labels={"provider": "ollama"}, value=1.0))

    inc = IncrementalAggregator(jsonl, state, window_minutes=60)
    registry = inc.refresh()
    snap = registry.snapshot()

    assert snap["counters"][0]["value"] == 1.0
    assert snap["histograms"][0]["count"] == 1
    assert snap["gauges"][0]["value"] == 1.0
    text = registry.render_prometheus_text()
    assert "model_gateway_requests_total" in text
