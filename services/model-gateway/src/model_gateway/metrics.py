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
    ile gecerli Prometheus exposition-format metnini uretebilir. Bu, canli
    bir `/metrics` HTTP endpoint'i SUNMAZ (bu servis suan bir HTTP sunucusu
    calistirmiyor -- boyle bir sunucu eklemek mimari bir genisleme olur,
    bu gorevin kapsami disinda) -- ama `scripts/ops/daily_gateway_smoke.ps1`
    gibi araclar `snapshot()`'i dosyaya yazip inceleyebilir, veya ileride
    bir HTTP sunucusu eklendiginde `render_prometheus_text()` dogrudan
    kullanilabilir.

Tasarim ilkesi: metrik kayit cagrilari router/runtime_verify/compat gibi
"gercek" davranisi ASLA etkilememeli, degistirmemeli -- yalnizca gozlem.
Bu yuzden her kayit metodu hata yutar (best-effort) ve `enabled=False`
iken tamamen no-op'tur.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

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
    """Bellek-ici, thread-safe metrik kayit defteri."""

    def __init__(self, enabled: bool = True, exporter: str = "noop") -> None:
        self.enabled = enabled
        self.exporter = exporter
        self._lock = threading.Lock()
        self._counters: dict[_MetricKey, float] = {}
        self._histograms: dict[_MetricKey, _HistogramData] = {}
        self._gauges: dict[_MetricKey, float] = {}

    def inc_counter(self, name: str, labels: LabelDict | None = None, value: float = 1.0) -> None:
        if not self.enabled:
            return
        try:
            with self._lock:
                k = _key(name, labels)
                self._counters[k] = self._counters.get(k, 0.0) + value
        except Exception:
            pass  # metrik kaydi asla gercek davranisi etkilemez

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

    def set_gauge(self, name: str, labels: LabelDict | None = None, value: float = 0.0) -> None:
        if not self.enabled:
            return
        try:
            with self._lock:
                k = _key(name, labels)
                self._gauges[k] = value
        except Exception:
            pass

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


def configure_metrics(enabled: bool, exporter: str) -> MetricsRegistry:
    """`load_config()` sonucuna gore global registry'yi (yeniden) kurar."""
    global _default_registry
    _default_registry = MetricsRegistry(enabled=enabled, exporter=exporter)
    return _default_registry
