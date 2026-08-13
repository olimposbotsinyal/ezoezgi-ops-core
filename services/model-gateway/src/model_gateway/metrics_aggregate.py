"""JSONL metrik dosyasini okuyup tek bir Prometheus goruntusune birlestirir.

`scripts/ops/serve_metrics.py`, `METRICS_SINK=jsonl_append` modunda,
KENDI surecinin (surec-ici, sinirli) registry'si yerine bu modulu
kullanarak TUM sureclerin (ayni dosyaya yazan) olaylarini birlestirir --
boylece cross-process gorunurluk saglanir.

Sert hata ayrimi (onemli): dosya HENUZ YOK (hicbir surec daha metrik
yazmadi) NORMAL bir durumdur -- bos bir goruntu doner, hata degil.
Dosya VAR ama okunamiyor (izin hatasi, disk hatasi vb.) SERT bir
hatadir -- `AggregationError` firlatilir, caller (serve_metrics.py) bunu
503 + tanilama nedenine cevirir.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from model_gateway.metrics import MetricsRegistry
from model_gateway.metrics_sink import MetricEvent

logger = logging.getLogger("model_gateway.metrics_aggregate")


class AggregationError(Exception):
    """Dosya VAR ama okunamiyor -- sert hata, /metrics 503 donmeli."""


class _ReadFailureCounter:
    def __init__(self) -> None:
        self._count = 0
        self._lock = threading.Lock()

    def increment(self) -> None:
        with self._lock:
            self._count += 1

    @property
    def count(self) -> int:
        with self._lock:
            return self._count


read_failures = _ReadFailureCounter()


def _iter_jsonl_files(base_path: Path) -> list[Path]:
    """Ana dosya + hala retention penceresi icindeki rotasyon dosyalari
    (eskiden yeniye). Rotasyon dosyalari da pencereye dahil edilir ki
    gun donumu hemen sonrasinda veri kaybi olmasin."""
    files = []
    pattern = f"{base_path.stem}.*.rotated{base_path.suffix}"
    rotated = sorted(base_path.parent.glob(pattern))
    files.extend(rotated)
    if base_path.exists():
        files.append(base_path)
    return files


def read_recent_events(path: Path, window_minutes: int) -> list[MetricEvent]:
    """`path` (+ hala mevcut rotasyon dosyalari) icindeki, son
    `window_minutes` icindeki olaylari okur. Dosya(lar) hic yoksa bos
    liste doner (NORMAL). Dosya var ama TUMU okunamazsa `AggregationError`
    firlatir."""
    files = _iter_jsonl_files(path)
    if not files:
        return []

    cutoff = time.time() - (window_minutes * 60)
    events: list[MetricEvent] = []
    any_file_readable = False
    last_error: Exception | None = None

    for file_path in files:
        try:
            with file_path.open("r", encoding="utf-8") as f:
                any_file_readable = True
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        event = MetricEvent.from_dict(data)
                    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                        continue  # bozuk tek satir -- tum dosyayi gecersiz kilmaz
                    if event.ts >= cutoff:
                        events.append(event)
        except OSError as exc:
            last_error = exc
            logger.warning("metrics JSONL dosyasi okunamadi: %s (%s)", file_path, exc)
            continue

    if not any_file_readable and last_error is not None:
        read_failures.increment()
        raise AggregationError(f"Hicbir metrics JSONL dosyasi okunamadi: {last_error}")

    return events


def compact_to_registry(events: list[MetricEvent]) -> MetricsRegistry:
    """Olaylari tek bir taze `MetricsRegistry`'ye "replay" eder --
    counter'lar toplanir, histogram'lar tum degerleriyle yeniden
    gozlemlenir, gauge'lar zaman damgasina gore EN SON deger kazanir."""
    registry = MetricsRegistry(enabled=True, exporter="prometheus")

    # Gauge'larda "son deger kazanir" icin ts'e gore sirala.
    for event in sorted(events, key=lambda e: e.ts):
        if event.metric_type == "counter":
            registry.inc_counter(event.name, event.labels, event.value)
        elif event.metric_type == "histogram":
            registry.observe_histogram(event.name, event.labels, event.value)
        elif event.metric_type == "gauge":
            registry.set_gauge(event.name, event.labels, event.value)

    return registry


def aggregate(path: Path, window_minutes: int) -> MetricsRegistry:
    """Tek adimda oku + birlestir. `AggregationError` sert hatalarda
    yukari birakilir (caller 503'e cevirir)."""
    events = read_recent_events(path, window_minutes)
    return compact_to_registry(events)


def self_metrics() -> dict[str, float]:
    """`/metrics` yanitina eklenecek oz-metrikler (bkz. serve_metrics.py)."""
    return {"metrics_aggregator_read_failures_total": float(read_failures.count)}
