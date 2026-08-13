"""Metrik sink soyutlamasi -- surecler-arasi (cross-process) gorunurluk.

Onceki tasarimin bilinen sinirlamasi: `MetricsRegistry`, surec-ici bir
bellek yapisiydi -- ayri, kisa omurlu Python sureclerinden yapilan
`classify()` cagrilari birbirinin metriklerini GOREMIYORDU (bkz.
docs/ops/MONITORING_STACK_RUNBOOK.md "KRITIK mimari sinirlama", B036
gorev serisi). Bu modul, o sinirlamayi KAPATMAK icin append-only bir
JSONL dosya sink'i ekler -- her surec kendi olayini dosyaya yazar,
`metrics_aggregate.py` bu dosyayi okuyup birlestirir.

Iki sink turu:
  - `InMemorySink`: onceki davranis, TEK surec icinde gorunur (varsayilan
    degil artik, ama backward-compat icin korunuyor).
  - `JsonlAppendSink`: her metrik olayini `data/metrics/model_gateway_metrics.jsonl`'e
    (varsayilan yol) append eder -- boyut/gun bazli rotasyon,
    retention, sinirli-kardinalite korumasi ile.

Guvenlik ilkesi (degismedi): sink yazma HICBIR ZAMAN classify()/router
davranisini etkilemez/bloklamaz -- her yazma zaman-sinirli (timeout-bounded)
ve hata durumunda sessizce (ama sayarak) basarisiz olur.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger("model_gateway.metrics_sink")

DEFAULT_LOCK_TIMEOUT_SECONDS = 0.5
DEFAULT_CARDINALITY_CAP = 200
_WARNING_THROTTLE_SECONDS = 30.0


@dataclass
class MetricEvent:
    ts: float  # epoch saniye
    metric_type: str  # "counter" | "histogram" | "gauge"
    name: str
    labels: dict[str, str] = field(default_factory=dict)
    value: float = 0.0

    def to_json_line(self) -> str:
        return json.dumps(
            {
                "ts": self.ts,
                "type": self.metric_type,
                "name": self.name,
                "labels": self.labels,
                "value": self.value,
            },
            ensure_ascii=False,
        )

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "MetricEvent":
        return MetricEvent(
            ts=float(data["ts"]),
            metric_type=str(data["type"]),
            name=str(data["name"]),
            labels={str(k): str(v) for k, v in (data.get("labels") or {}).items()},
            value=float(data.get("value", 0.0)),
        )


class MetricsSink(Protocol):
    def write(self, event: MetricEvent) -> bool: ...


class _ThrottledLogger:
    """Ayni uyariyi saniyede binlerce kez basmamak icin basit kisitlayici."""

    def __init__(self) -> None:
        self._last_logged_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def warn_throttled(self, key: str, message: str) -> None:
        now = time.monotonic()
        with self._lock:
            last = self._last_logged_at.get(key, 0.0)
            if now - last < _WARNING_THROTTLE_SECONDS:
                return
            self._last_logged_at[key] = now
        logger.warning(message)


_throttle = _ThrottledLogger()


class InMemorySink:
    """Onceki davranis -- verilen registry'ye dogrudan yazar (surec-ici,
    cross-process GORUNURLUGU YOK). Backward-compat/opt-out icin."""

    def __init__(self, registry: Any) -> None:
        self._registry = registry

    def write(self, event: MetricEvent) -> bool:
        try:
            if event.metric_type == "counter":
                self._registry.inc_counter(event.name, event.labels, event.value)
            elif event.metric_type == "histogram":
                self._registry.observe_histogram(event.name, event.labels, event.value)
            elif event.metric_type == "gauge":
                self._registry.set_gauge(event.name, event.labels, event.value)
            else:
                return False
            return True
        except Exception:
            return False


def _acquire_lock(lock_path: Path, timeout_seconds: float) -> bool:
    """Taşınabilir, basit dosya kilidi: `O_CREAT|O_EXCL` ile atomik
    olusturma + kisa sureli yeniden deneme. Windows dahil tum
    platformlarda calisir (msvcrt/fcntl gibi platforma ozel API
    gerektirmez)."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return True
        except FileExistsError:
            if time.monotonic() >= deadline:
                return False
            # Yarim kalmis/eski bir kilit dosyasi olabilir -- cok eski ise
            # (>5s) kurtarma amacli temizle (crash sonrasi kilitli kalma
            # riskine karsi).
            try:
                if time.monotonic() - lock_path.stat().st_mtime > 5.0:
                    lock_path.unlink(missing_ok=True)
            except OSError:
                pass
            time.sleep(0.01)
        except OSError:
            return False


def _release_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


class JsonlAppendSink:
    """Append-only JSONL dosya sink'i -- cross-process gorunurluk saglar.

    Her `write()` cagrisi: (1) sinirli-kardinalite kontrolu, (2) kisa
    sureli, zaman-sinirli bir dosya kilidi, (3) rotasyon kontrolu
    (boyut/gun), (4) tek satirlik JSON append. Kilit alinamazsa veya
    yazma basarisiz olursa `write()` False doner ve `write_failures`
    sayaci artar -- HICBIR ZAMAN exception firlatmaz, cagiran (classify()
    yolu) asla bloklanmaz/cokmez.
    """

    def __init__(
        self,
        path: Path,
        *,
        max_mb: float = 50.0,
        retention_days: int = 7,
        cardinality_cap: int = DEFAULT_CARDINALITY_CAP,
        lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    ) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._max_bytes = int(max_mb * 1024 * 1024)
        self._retention_days = retention_days
        self._cardinality_cap = cardinality_cap
        self._lock_timeout = lock_timeout_seconds
        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")

        self._local_lock = threading.Lock()
        self._seen_label_sets: dict[str, set[tuple[tuple[str, str], ...]]] = {}
        self._last_rollover_day: str | None = None

        self.write_failures = 0
        self.events_dropped = 0

    def _cardinality_ok(self, event: MetricEvent) -> bool:
        """Surec-ici, EN IYI-CABA (best-effort) kardinalite koruma --
        gercek cross-process kardinaliteyi (tum sureclerin toplami)
        kesin olarak izlemek paylasilan bir sayac gerektirir; bu, tek
        bir surecin gordugu farkli etiket kombinasyonu sayisini sinirlar
        (ayni cok-etiketli/gurultulu kullanim deseni buradan da yakalanir)."""
        with self._local_lock:
            seen = self._seen_label_sets.setdefault(event.name, set())
            key = tuple(sorted(event.labels.items()))
            if key in seen:
                return True
            if len(seen) >= self._cardinality_cap:
                return False
            seen.add(key)
            return True

    def _maybe_rotate(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        needs_day_rollover = self._last_rollover_day is not None and self._last_rollover_day != today
        needs_size_rollover = False
        try:
            if self._path.exists() and self._path.stat().st_size >= self._max_bytes:
                needs_size_rollover = True
        except OSError:
            pass

        if needs_day_rollover or needs_size_rollover:
            self._rotate()
        self._last_rollover_day = today

    def _rotate(self) -> None:
        if not self._path.exists():
            return
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        rotated_path = self._path.with_name(f"{self._path.stem}.{ts}.rotated{self._path.suffix}")
        try:
            self._path.rename(rotated_path)
        except OSError as exc:
            _throttle.warn_throttled("rotate_failed", f"metrics JSONL rotasyonu basarisiz: {exc}")

    def write(self, event: MetricEvent) -> bool:
        if not self._cardinality_ok(event):
            with self._local_lock:
                self.events_dropped += 1
            _throttle.warn_throttled(
                f"cardinality_{event.name}",
                f"metrik '{event.name}' icin kardinalite siniri asildi, ornek atlandi",
            )
            return False

        if not _acquire_lock(self._lock_path, self._lock_timeout):
            with self._local_lock:
                self.write_failures += 1
            _throttle.warn_throttled(
                "lock_timeout", f"metrics JSONL kilidi zaman asimina ugradi ({self._path})"
            )
            return False

        try:
            self._maybe_rotate()
            with self._path.open("a", encoding="utf-8") as f:
                f.write(event.to_json_line() + "\n")
            return True
        except OSError as exc:
            with self._local_lock:
                self.write_failures += 1
            _throttle.warn_throttled("write_failed", f"metrics JSONL yazma basarisiz: {exc}")
            return False
        finally:
            _release_lock(self._lock_path)

    def enforce_retention(self) -> int:
        """Retention penceresinden eski rotasyon dosyalarini siler.
        Silinen dosya sayisini doner."""
        cutoff = time.time() - (self._retention_days * 86400)
        deleted = 0
        pattern = f"{self._path.stem}.*.rotated{self._path.suffix}"
        for rotated in self._path.parent.glob(pattern):
            try:
                if rotated.stat().st_mtime < cutoff:
                    rotated.unlink()
                    deleted += 1
            except OSError:
                continue
        return deleted

    def self_metrics(self) -> dict[str, float]:
        """`/metrics` yanitina eklenecek oz-metrikler (bkz. serve_metrics.py)."""
        with self._local_lock:
            return {
                "metrics_sink_write_failures_total": float(self.write_failures),
                "metrics_events_dropped_total": float(self.events_dropped),
            }
