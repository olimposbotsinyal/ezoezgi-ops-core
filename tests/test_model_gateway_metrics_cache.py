"""`CachedMetricsRenderer` (TTL onbellek) testleri -- bkz. metrics_cache.py.

`IncrementalAggregator` ile birlikte kullanilir (bkz.
test_model_gateway_metrics_perf.py) ama onbellek davranisi (TTL
hit/miss, hata durumunda onbelleklenmeme) burada ayri test edilir.
"""

from __future__ import annotations

import time

from model_gateway.metrics_aggregate import AggregationError, IncrementalAggregator
from model_gateway.metrics_cache import CachedMetricsRenderer
from model_gateway.metrics_sink import JsonlAppendSink, MetricEvent


def test_cache_ttl_hit_then_miss_after_expiry(tmp_path):
    jsonl = tmp_path / "m.jsonl"
    state = tmp_path / "m.state.json"
    sink = JsonlAppendSink(jsonl, max_mb=50, retention_days=7)
    sink.write(MetricEvent(ts=time.time(), metric_type="counter", name="x", labels={}, value=1.0))

    inc = IncrementalAggregator(jsonl, state, window_minutes=60)
    fake_clock = [0.0]
    cache = CachedMetricsRenderer(inc, ttl_seconds=5.0, clock=lambda: fake_clock[0])

    text1 = cache.render()
    assert cache.cache_misses == 1 and cache.cache_hits == 0

    text2 = cache.render()  # TTL icinde -- HIT, ayni metin
    assert cache.cache_hits == 1
    assert text2 == text1

    sink.write(MetricEvent(ts=time.time(), metric_type="counter", name="x", labels={}, value=1.0))
    fake_clock[0] += 10.0  # TTL'yi asir
    text3 = cache.render()  # MISS -- yeni veri gorunur olmali
    assert cache.cache_misses == 2
    assert "x 2.0" in text3


def test_cache_does_not_cache_on_aggregation_error(tmp_path):
    jsonl = tmp_path / "m_as_dir.jsonl"
    jsonl.mkdir()  # dosya yerine dizin -- refresh() her zaman basarisiz olur
    state = tmp_path / "m.state.json"
    inc = IncrementalAggregator(jsonl, state, window_minutes=60)
    cache = CachedMetricsRenderer(inc, ttl_seconds=5.0)

    for _ in range(2):
        raised = False
        try:
            cache.render()
        except AggregationError:
            raised = True
        assert raised is True

    assert cache.cache_hits == 0  # hicbir basarili sonuc olmadigindan cache'e hic girilmemeli


def test_cache_self_metrics_reflect_rebuild_duration(tmp_path):
    jsonl = tmp_path / "m.jsonl"
    state = tmp_path / "m.state.json"
    sink = JsonlAppendSink(jsonl, max_mb=50, retention_days=7)
    sink.write(MetricEvent(ts=time.time(), metric_type="counter", name="x", labels={}, value=1.0))

    inc = IncrementalAggregator(jsonl, state, window_minutes=60)
    cache = CachedMetricsRenderer(inc, ttl_seconds=5.0)
    cache.render()

    metrics = cache.self_metrics()
    assert metrics["metrics_aggregator_cache_misses_total"] == 1.0
    assert metrics["metrics_aggregator_cache_hits_total"] == 0.0
    assert metrics["metrics_aggregator_rebuild_duration_ms"] >= 0.0
