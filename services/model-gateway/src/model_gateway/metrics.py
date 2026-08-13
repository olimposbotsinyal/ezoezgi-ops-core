"""Model gateway metrikleri -- minimal, bagimliliksiz, takilabilir (pluggable).

Bu projede henuz gercek bir Prometheus/OpenTelemetry kurulumu yok
(`infra/monitoring/prometheus/`, `infra/monitoring/grafana/` hala bos
iskelet klasorler -- bkz. docs/BACKLOG.md B015). Bu yuzden burada,
`prometheus_client` gibi harici bir bagimlilik EKLEMEDEN, thread-safe,
bellek-ici bir sayac/histogram/gauge kayit defteri uygulanir. `METRICS_EXPORTER`
degeri:

  - `noop`: hicbir sey biriktirmez, tum cagrilar guvenli no-op'tur
    (varsayilana yakin en dusuk maliyet).
  - `prometheus`: metrikleri bellekte biriktirir VE `render_prometheus_text()`
    ile gecerli Prometheus exposition-format metnini uretebilir.

Cross-process gorunurluk (bkz. metrics_sink.py, metrics_aggregate.py):
`MetricsRegistry`, opsiyonel bir `sink` alabilir -- verildiginde her
`inc_counter`/`observe_histogram`/`set_gauge` cagrisi, KENDI surecinin
bellek-ici durumunu guncellemeye EK OLARAK, sink'e de bir `MetricEvent`
yazar. `METRICS_SINK=jsonl_append` (yeni varsayilan) ile bu sink,
paylasilan bir dosyaya append eder -- boylece `scripts/ops/serve_metrics.py`,
`metrics_aggregate.py` uzerinden TUM sureclerin olaylarini birlestirip
sunabilir. Bu mekanizma OPSIYONELDIR ve geriye-donuk uyumludur:
`sink=None` (varsayilan) ile `MetricsRegistry` onceki (surec-ici)
davranisiyla birebir ayni calisir.

Tasarim ilkesi: metrik kayit cagrilari router/runtime_verify/compat gibi
"gercek" davranisi ASLA etkilememeli, degistirmemeli -- yalnizca gozlem.
Bu yuzden her kayit metodu hata yutar (best-effort) ve `enabled=False`
iken tamamen no-op'tur. Sink yazma islemi de kendi icinde ayrica
hata-yutan/zaman-sinirli (bkz. metrics_sink.py) -- burada ek bir
try/except ile cift guvenceye alinir.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from model_gateway.metrics_sink import MetricsSink

LabelDict = dict[str, str]
_MetricKey = tuple[str, tuple[tuple[str, str], ...]]


def _key(name: str, labels: LabelDict | None) -> _MetricKey:
    return (name, tuple(sorted((labels or {}).items())))


@dataclass
class _HistogramData:
    count: int = 0
    total: float = 0.0
    values: list[float] = field(default_factory=list)


class MetricsRegistry:
    """Bellek-ici, thread-safe metrik kayit defteri.

    `sink` verilirse (bkz. metrics_sink.py), her kayit cagrisi ek olarak
    sink'e yazar -- cross-process gorunurluk icin. `sink=None`
    (varsayilan) onceki, tamamen surec-ici davranisla birebir aynidir.
    """

    def __init__(
        self, enabled: bool = True, exporter: str = "noop", sink: "MetricsSink | None" = None
    ) -> None:
        self.enabled = enabled
        self.exporter = exporter
        self._sink = sink
        self._lock = threading.Lock()
        self._counters: dict[_MetricKey, float] = {}
        self._histograms: dict[_MetricKey, _HistogramData] = {}
        self._gauges: dict[_MetricKey, float] = {}

    def _write_to_sink(self, metric_type: str, name: str, labels: LabelDict | None, value: float) -> None:
        if self._sink is None:
            return
        try:
            from model_gateway.metrics_sink import MetricEvent

            self._sink.write(
                MetricEvent(ts=time.time(), metric_type=metric_type, name=name, labels=labels or {}, value=value)
            )
        except Exception:
            pass  # sink zaten kendi ici hata-yutuyor; bu yalnizca ek guvence

    def inc_counter(self, name: str, labels: LabelDict | None = None, value: float = 1.0) -> None:
        if not self.enabled:
            return
        try:
            with self._lock:
                k = _key(name, labels)
                self._counters[k] = self._counters.get(k, 0.0) + value
        except Exception:
            pass  # metrik kaydi asla gercek davranisi etkilemez
        self._write_to_sink("counter", name, labels, value)

    def observe_histogram(self, name: str, labels: LabelDict | None = None, value: float = 0.0) -> None:
        if not self.enabled:
            return
        try:
            with self._lock:
                k = _key(name, labels)
                data = self._histograms.setdefault(k, _HistogramData())
                data.count += 1
                data.total += value
                data.values.append(value)
        except Exception:
            pass
        self._write_to_sink("histogram", name, labels, value)

    def set_gauge(self, name: str, labels: LabelDict | None = None, value: float = 0.0) -> None:
        if not self.enabled:
            return
        try:
            with self._lock:
                k = _key(name, labels)
                self._gauges[k] = value
        except Exception:
            pass
        self._write_to_sink("gauge", name, labels, value)

    def set_counter_absolute(self, name: str, labels: LabelDict | None, value: float) -> None:
        """`inc_counter`'in aksine, mevcut degere EKLEMEZ -- dogrudan
        SET eder. Yalnizca oz-metrikler (self-metrics) gibi zaten
        kumulatif bir toplami temsil eden degerleri kayit defterine
        yansitmak icin kullanilir (bkz. serve_metrics.py)."""
        if not self.enabled:
            return
        try:
            with self._lock:
                k = _key(name, labels)
                self._counters[k] = value
        except Exception:
            pass

    def sink_self_metrics(self) -> dict[str, float]:
        """Bagli sink'in kendi oz-metriklerini (varsa) doner -- ornegin
        `JsonlAppendSink.self_metrics()` (write_failures/events_dropped).
        Sink yoksa veya oz-metrik desteklemiyorsa bos sozluk doner (bkz.
        serve_metrics.py)."""
        if self._sink is None:
            return {}
        method = getattr(self._sink, "self_metrics", None)
        if method is None:
            return {}
        try:
            return dict(method())
        except Exception:
            return {}

    def snapshot(self) -> dict[str, Any]:
        """JSON-serilestirilebilir bir anlik goruntu -- daily smoke gibi
        araclarin `metrics_snapshot.json`'a yazmasi icin."""
        with self._lock:
            counters = [
                {"name": name, "labels": dict(labels), "value": value}
                for (name, labels), value in sorted(self._counters.items())
            ]
            histograms = [
                {
                    "name": name,
                    "labels": dict(labels),
                    "count": data.count,
                    "sum": round(data.total, 3),
                    "avg": round(data.total / data.count, 3) if data.count else 0.0,
                    "p50": _percentile(data.values, 0.50),
                    "p95": _percentile(data.values, 0.95),
                }
                for (name, labels), data in sorted(self._histograms.items())
            ]
            gauges = [
                {"name": name, "labels": dict(labels), "value": value}
                for (name, labels), value in sorted(self._gauges.items())
            ]
        return {
            "enabled": self.enabled,
            "exporter": self.exporter,
            "counters": counters,
            "histograms": histograms,
            "gauges": gauges,
        }

    def render_prometheus_text(self) -> str:
        """Gecerli Prometheus exposition-format metni uretir."""
        lines: list[str] = []
        with self._lock:
            for (name, labels), value in sorted(self._counters.items()):
                lines.append(f"# TYPE {name} counter")
                lines.append(f"{name}{_format_labels(labels)} {value}")
            for (name, labels), data in sorted(self._histograms.items()):
                lines.append(f"# TYPE {name} summary")
                lines.append(f"{name}_count{_format_labels(labels)} {data.count}")
                lines.append(f"{name}_sum{_format_labels(labels)} {round(data.total, 3)}")
            for (name, labels), value in sorted(self._gauges.items()):
                lines.append(f"# TYPE {name} gauge")
                lines.append(f"{name}{_format_labels(labels)} {value}")
        return "\n".join(lines) + ("\n" if lines else "")

    def reset(self) -> None:
        """Yalnizca testler icin -- uretimde cagrilmaz."""
        with self._lock:
            self._counters.clear()
            self._histograms.clear()
            self._gauges.clear()


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in labels)
    return "{" + inner + "}"


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return round(s[f], 3)
    return round(s[f] + (s[c] - s[f]) * (k - f), 3)


class NoopMetricsRegistry(MetricsRegistry):
    """Acikca kapali/no-op bir kayit defteri -- METRICS_ENABLED=false icin."""

    def __init__(self) -> None:
        super().__init__(enabled=False, exporter="noop")


_default_registry: MetricsRegistry | None = None


def get_metrics() -> MetricsRegistry:
    """Surec-genelinde tek bir metrik kayit defteri (lazy singleton).

    `configure_metrics()` cagrilmadiysa varsayilan olarak ETKIN, `noop`
    exporter ile baslar (guvenli varsayilan -- veri biriktirir ama disari
    hicbir sey yaymaz).
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = MetricsRegistry(enabled=True, exporter="noop")
    return _default_registry


def configure_metrics(
    enabled: bool,
    exporter: str,
    *,
    sink_type: str = "in_memory",
    jsonl_path: str | None = None,
    jsonl_max_mb: float = 50.0,
    jsonl_retention_days: int = 7,
) -> MetricsRegistry:
    """`load_config()` sonucuna gore global registry'yi (yeniden) kurar.

    Yeni (opsiyonel, keyword-only) `sink_type`/`jsonl_*` parametreleri --
    verilmezse (`sink_type="in_memory"`, varsayilan) davranis ONCEKIYLE
    BIREBIR AYNI kalir (backward compat). `sink_type="jsonl_append"` VE
    `jsonl_path` verilirse, cagirilan surec artik paylasilan bir dosyaya
    yazar -- cross-process gorunurluk icin bkz. metrics_sink.py.
    """
    global _default_registry
    sink = None
    if enabled and sink_type == "jsonl_append" and jsonl_path:
        from pathlib import Path

        from model_gateway.metrics_sink import JsonlAppendSink

        sink = JsonlAppendSink(
            Path(jsonl_path), max_mb=jsonl_max_mb, retention_days=jsonl_retention_days
        )
    _default_registry = MetricsRegistry(enabled=enabled, exporter=exporter, sink=sink)
    return _default_registry
