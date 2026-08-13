"""metrics_sink.py + metrics_aggregate.py testleri.

Gercek gecici dosyalar kullanilir (tmp_path) -- ag/dis servis
gerektirmez. Cross-process davranisi, AYNI dosyaya yazan BIRDEN FAZLA
sink/thread ornegiyle simule edilir (gercek ayri OS surecleri
baslatmadan, ama ayni dosya-tabanli kilit/append mekanizmasini
gercekten kullanarak).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from model_gateway.metrics_aggregate import AggregationError, aggregate, compact_to_registry, read_recent_events
from model_gateway.metrics_sink import JsonlAppendSink, MetricEvent
from model_gateway.metrics import MetricsRegistry


def test_jsonl_sink_write_and_read_roundtrip(tmp_path):
    path = tmp_path / "metrics.jsonl"
    sink = JsonlAppendSink(path, max_mb=50, retention_days=7)

    ok = sink.write(MetricEvent(ts=time.time(), metric_type="counter", name="m_total", labels={"a": "1"}, value=1.0))

    assert ok is True
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["name"] == "m_total"
    assert parsed["labels"] == {"a": "1"}


def test_cross_process_aggregation_combines_events_from_multiple_writers(tmp_path):
    """Iki AYRI JsonlAppendSink ornegi (iki 'surec' simulasyonu), AYNI
    dosyaya yazar -- aggregate() ikisinin de olaylarini birlestirmeli."""
    path = tmp_path / "metrics.jsonl"
    writer_a = JsonlAppendSink(path, max_mb=50, retention_days=7)
    writer_b = JsonlAppendSink(path, max_mb=50, retention_days=7)

    writer_a.write(MetricEvent(ts=time.time(), metric_type="counter", name="model_gateway_requests_total", labels={"provider": "ollama", "result": "success"}, value=1.0))
    writer_b.write(MetricEvent(ts=time.time(), metric_type="counter", name="model_gateway_requests_total", labels={"provider": "ollama", "result": "success"}, value=1.0))
    writer_a.write(MetricEvent(ts=time.time(), metric_type="counter", name="model_gateway_requests_total", labels={"provider": "ollama", "result": "success"}, value=1.0))

    registry = aggregate(path, window_minutes=60)

    snap = registry.snapshot()
    counter = next(c for c in snap["counters"] if c["name"] == "model_gateway_requests_total")
    assert counter["value"] == 3.0  # her iki 'surecin' de katkisi toplanmis


def test_concurrent_append_does_not_corrupt_output(tmp_path):
    path = tmp_path / "metrics.jsonl"
    sink = JsonlAppendSink(path, max_mb=50, retention_days=7)
    n_threads = 8
    writes_per_thread = 15

    def worker():
        for _ in range(writes_per_thread):
            sink.write(
                MetricEvent(ts=time.time(), metric_type="counter", name="concurrent_total", labels={}, value=1.0)
            )

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # Dosyanin HER satiri gecerli JSON olmali -- kilit mekanizmasi
    # yazmalarin birbirine karismasini (interleaving/corruption) onlemis
    # olmali.
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    for line in lines:
        json.loads(line)  # gecersizse exception firlatir, test basarisiz olur

    total_value = sum(json.loads(ln)["value"] for ln in lines)
    expected = n_threads * writes_per_thread
    # Kilit zaman asimina ugrayan birkac yazma dusebilir (write_failures) --
    # ama coguluk basarili olmali ve HICBIRI bozuk olmamali.
    assert total_value >= expected * 0.8, f"beklenen ~{expected}, gorulen {total_value}"


def test_rotation_on_size_cap(tmp_path):
    path = tmp_path / "metrics.jsonl"
    # Cok kucuk bir limit -- birkac yazimda asilir.
    sink = JsonlAppendSink(path, max_mb=0.0005, retention_days=7)  # ~500 byte

    for i in range(50):
        sink.write(MetricEvent(ts=time.time(), metric_type="counter", name="rotation_test", labels={"i": str(i)}, value=1.0))

    rotated_files = list(tmp_path.glob("metrics.*.rotated.jsonl"))
    assert len(rotated_files) >= 1, "boyut siniri asildiginda en az bir rotasyon dosyasi olusmali"


def test_retention_deletes_old_rotated_files(tmp_path):
    path = tmp_path / "metrics.jsonl"
    sink = JsonlAppendSink(path, max_mb=50, retention_days=7)

    old_rotated = tmp_path / "metrics.20200101T000000Z.rotated.jsonl"
    old_rotated.write_text('{"ts": 1, "type": "counter", "name": "x", "labels": {}, "value": 1}\n', encoding="utf-8")
    old_time = time.time() - (30 * 86400)  # 30 gun once
    import os

    os.utime(old_rotated, (old_time, old_time))

    recent_rotated = tmp_path / "metrics.20260101T000000Z.rotated.jsonl"
    recent_rotated.write_text('{"ts": 1, "type": "counter", "name": "x", "labels": {}, "value": 1}\n', encoding="utf-8")

    deleted_count = sink.enforce_retention()

    assert deleted_count == 1
    assert not old_rotated.exists()
    assert recent_rotated.exists()


def test_cardinality_guard_drops_excess_label_combinations(tmp_path):
    path = tmp_path / "metrics.jsonl"
    sink = JsonlAppendSink(path, max_mb=50, retention_days=7, cardinality_cap=3)

    results = [
        sink.write(MetricEvent(ts=time.time(), metric_type="counter", name="noisy", labels={"id": str(i)}, value=1.0))
        for i in range(10)
    ]

    assert results.count(True) == 3
    assert results.count(False) == 7
    assert sink.events_dropped == 7


def test_aggregate_no_file_yet_returns_empty_not_error(tmp_path):
    path = tmp_path / "does_not_exist.jsonl"

    events = read_recent_events(path, window_minutes=60)

    assert events == []


def test_aggregate_hard_failure_raises_when_path_is_unreadable(tmp_path):
    """Dosya YOLU var ama gercekten okunamiyor (burada: bir dizin,
    dosya degil) -- sert hata, AggregationError firlatilmali."""
    path = tmp_path / "metrics_as_dir.jsonl"
    path.mkdir()  # dosya yerine dizin -- open() basarisiz olur

    with pytest.raises(AggregationError):
        read_recent_events(path, window_minutes=60)


def test_aggregate_window_filters_old_events(tmp_path):
    path = tmp_path / "metrics.jsonl"
    sink = JsonlAppendSink(path, max_mb=50, retention_days=7)

    old_ts = time.time() - (120 * 60)  # 120 dakika once
    sink.write(MetricEvent(ts=old_ts, metric_type="counter", name="old_metric", labels={}, value=1.0))
    sink.write(MetricEvent(ts=time.time(), metric_type="counter", name="new_metric", labels={}, value=1.0))

    events = read_recent_events(path, window_minutes=60)

    names = {e.name for e in events}
    assert "new_metric" in names
    assert "old_metric" not in names


def test_existing_metric_counters_still_reported_via_registry():
    """Backward-compat: sink=None (varsayilan olmayan ama gecerli bir
    kullanim) ile MetricsRegistry, onceki (B036 gorev serisi) davranisiyla
    birebir ayni calismali."""
    registry = MetricsRegistry(enabled=True, exporter="prometheus")

    registry.inc_counter("model_gateway_requests_total", {"provider": "ollama", "result": "success"})
    registry.observe_histogram("model_gateway_generate_latency_ms", {"provider": "ollama"}, 123.0)
    registry.set_gauge("model_gateway_provider_health", {"provider": "ollama"}, 1.0)

    snap = registry.snapshot()
    assert snap["counters"][0]["value"] == 1.0
    assert snap["histograms"][0]["count"] == 1
    assert snap["gauges"][0]["value"] == 1.0

    text = registry.render_prometheus_text()
    assert "model_gateway_requests_total" in text
