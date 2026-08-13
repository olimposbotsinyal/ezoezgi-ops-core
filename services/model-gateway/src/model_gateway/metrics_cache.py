"""`/metrics` icin kisa-sureli TTL onbellegi -- ust uste hizli gelen
scrape isteklerinin her seferinde `IncrementalAggregator.refresh()`'i
(disk okuma + replay) tekrar tetiklemesini onler.

Onbellek, TAMAMEN RENDER EDILMIS metni (aggregator verisi + oz-metrikler
BIRLIKTE) tek bir birim olarak saklar -- boylece bir cache HIT, TTL
penceresi icinde ayni anlik goruntuyu tutarli sekilde geri doner (tipki
gercek bir Prometheus/Varnish onbellegi gibi). `AggregationError`
firlarsa hicbir sey onbelleklenmez -- bir sonraki istek yeniden dener
(hata durumu asla "yapiskan" kalmaz).
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from model_gateway.metrics_aggregate import IncrementalAggregator

DEFAULT_TTL_SECONDS = 5.0


class CachedMetricsRenderer:
    def __init__(
        self,
        aggregator: "IncrementalAggregator",
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        *,
        extra_counters_fn: Callable[[], dict[str, float]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._aggregator = aggregator
        self._ttl_seconds = ttl_seconds
        self._extra_counters_fn = extra_counters_fn
        self._clock = clock

        self._state_lock = threading.Lock()
        self._cached_text: str | None = None
        self._cached_at: float = 0.0

        self.cache_hits = 0
        self.cache_misses = 0
        self.last_rebuild_duration_ms = 0.0

    def render(self) -> str:
        now = self._clock()
        with self._state_lock:
            if self._cached_text is not None and (now - self._cached_at) < self._ttl_seconds:
                self.cache_hits += 1
                return self._cached_text

        # Kilit DISINDA calistir -- refresh() potansiyel olarak yavas
        # (disk G/Ç) olabilir, onbellek kilidini o sure boyunca tutmak
        # eszamanli istekleri gereksiz yere bloklar. `IncrementalAggregator`
        # kendi ic kilidiyle zaten thread-safe. Bu, TTL penceresi disinda
        # eszamanli "miss" firtinasinda birden fazla thread'in ayni anda
        # refresh() cagirmasina izin verir (dogru ama optimal olmayan --
        # varsayilan TTL=5s ile pratikte nadir bir durum).
        start = time.perf_counter()
        registry = self._aggregator.refresh()  # AggregationError -- onbelleklenmeden yukari birakilir
        duration_ms = (time.perf_counter() - start) * 1000.0

        with self._state_lock:
            self.cache_misses += 1
            self.last_rebuild_duration_ms = duration_ms

            for name, value in (self._extra_counters_fn() if self._extra_counters_fn else {}).items():
                registry.set_counter_absolute(name, {}, value)
            registry.set_gauge("metrics_aggregator_rebuild_duration_ms", {}, duration_ms)
            registry.set_counter_absolute("metrics_aggregator_cache_hits_total", {}, float(self.cache_hits))
            registry.set_counter_absolute("metrics_aggregator_cache_misses_total", {}, float(self.cache_misses))

            text = registry.render_prometheus_text()
            self._cached_text = text
            self._cached_at = now
            return text

    def self_metrics(self) -> dict[str, float]:
        with self._state_lock:
            return {
                "metrics_aggregator_cache_hits_total": float(self.cache_hits),
                "metrics_aggregator_cache_misses_total": float(self.cache_misses),
                "metrics_aggregator_rebuild_duration_ms": float(self.last_rebuild_duration_ms),
            }
