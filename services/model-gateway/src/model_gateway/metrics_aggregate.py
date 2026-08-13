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

from model_gateway import metrics_index
from model_gateway.metrics import MetricsRegistry
from model_gateway.metrics_index import FileOffset
from model_gateway.metrics_sink import MetricEvent, _ThrottledLogger

logger = logging.getLogger("model_gateway.metrics_aggregate")
_throttle = _ThrottledLogger()


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


def _split_complete_lines(chunk: bytes, max_lines: int) -> tuple[list[str], int, bool]:
    """`chunk` icindeki TAM (newline ile biten) satirlari ayirir.

    Son satir newline ile bitmiyorsa (baska bir surec hala yaziyor
    olabilir) atlanir -- tuketilen byte sayisina DAHIL EDILMEZ (bir
    sonraki okumada bastan/tam olarak tekrar denenir, boylece asla
    yarim bir JSON satiri parse edilmeye calisilmaz).

    `max_lines` asilirsa fazlasi bu turda ERTELENIR (tuketilmeyen
    satirlar bir sonraki `refresh()` cagrisinda okunur, offset yalnizca
    GERCEKTEN alinan satirlar kadar ilerletilir). Donus: (alinan
    satirlar, tuketilen byte sayisi, butce asildi mi)."""
    if not chunk:
        return [], 0, False
    text = chunk.decode("utf-8", errors="replace")
    raw_lines = text.split("\n")
    complete = raw_lines[:-1]  # son eleman: newline ile bittiyse '', bitmediyse yarim satir -- ikisi de atilir

    had_more = len(complete) > max_lines
    taken = complete[:max_lines] if had_more else complete

    consumed_bytes = sum(len(line.encode("utf-8")) + 1 for line in taken)  # +1: '\n'
    return taken, consumed_bytes, had_more


class IncrementalAggregator:
    """Tam-yeniden-tarama (`aggregate()`) yerine, yalnizca SON okumadan
    beri eklenen byte'lari okuyan, sonuclari surec-ici bir tamponda
    (pencere ile sinirli) tutan aggregator. Ayni Prometheus goruntusunu
    uretir (`compact_to_registry()` DOGRUDAN yeniden kullanilir --
    replay/formatlama mantigi ikisi arasinda BIREBIR aynidir, bkz.
    "output equivalence" testi), yalnizca disk okuma maliyetini azaltir.

    Dosya kimligi `metrics_index.file_identity()` ile (st_dev, st_ino)
    uzerinden takip edilir -- rotasyon (rename) SEFFAF calisir, ozel bir
    "rotasyon algila" adimi gerekmez. Kalici state (`state_path`),
    surec yeniden baslasa bile onceki oturumdan kaldigi yerden devam
    edebilmeyi saglar; eksik/bozuk state dosyasi SERT hata degildir --
    otomatik olarak bos durumdan (mevcut dosyalarin tam okunmasiyla)
    kontrollu sekilde yeniden insa edilir (auto-heal).
    """

    def __init__(
        self,
        jsonl_path: Path,
        state_path: Path,
        window_minutes: int,
        *,
        max_series: int = 2000,
        max_events_per_scrape: int = 5000,
    ) -> None:
        self._jsonl_path = Path(jsonl_path)
        self._state_path = Path(state_path)
        self._window_minutes = window_minutes
        self._max_series = max_series
        self._max_events_per_scrape = max_events_per_scrape
        self._lock = threading.Lock()

        loaded = metrics_index.load_state(self._state_path)
        self._state = loaded or metrics_index.AggregatorState(window_minutes=window_minutes)
        self._events: list[MetricEvent] = list(self._state.events)

        self.series_dropped = 0
        self.events_deferred = 0
        self.lines_ingested_total = 0

    def refresh(self) -> MetricsRegistry:
        """Yeni satirlari okur (varsa), pencere disina cikan olaylari
        budar, kardinalite sinirini uygular, TAZE bir registry'ye
        replay eder ve state'i kalici hale getirir. `AggregationError`,
        hicbir dosya acilamadiginda (ve en az biri beklenirken) firlatir
        -- `read_recent_events()` ile AYNI sert-hata sozlesmesi."""
        with self._lock:
            self._ingest_new_lines()
            self._prune_expired()
            self._enforce_series_cap()
            registry = compact_to_registry(self._events)
            self._state.window_minutes = self._window_minutes
            self._state.events = list(self._events)
            metrics_index.save_state(self._state_path, self._state)
            return registry

    def _ingest_new_lines(self) -> None:
        files = _iter_jsonl_files(self._jsonl_path)
        if not files:
            return  # hicbir surec henuz yazmadi -- NORMAL

        any_opened = False
        last_error: Exception | None = None
        budget = self._max_events_per_scrape
        capped = False

        for file_path in files:
            if budget <= 0:
                capped = True
                break

            identity = metrics_index.file_identity(file_path)
            if identity is None:
                continue  # glob ile stat arasinda silinmis/tasinmis -- atla

            offset_entry = self._state.files.get(identity)
            offset = offset_entry.offset if offset_entry else 0

            try:
                with file_path.open("rb") as fh:
                    any_opened = True
                    fh.seek(offset)
                    chunk = fh.read()
            except OSError as exc:
                last_error = exc
                logger.warning("metrics JSONL dosyasi okunamadi: %s (%s)", file_path, exc)
                continue

            complete_lines, consumed_bytes, had_more = _split_complete_lines(chunk, budget)
            if had_more:
                capped = True

            self._state.files[identity] = FileOffset(path=str(file_path), offset=offset + consumed_bytes)
            budget -= len(complete_lines)

            for line in complete_lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    event = MetricEvent.from_dict(data)
                except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                    continue  # bozuk tek satir -- tum dosyayi gecersiz kilmaz
                self._events.append(event)
                self.lines_ingested_total += 1

        if not any_opened and last_error is not None:
            read_failures.increment()
            raise AggregationError(f"Hicbir metrics JSONL dosyasi okunamadi: {last_error}")

        if capped:
            # NOT: bu metod yalnizca `refresh()` icinden, `self._lock`
            # zaten TUTULUYORKEN cagrilir -- burada tekrar kilit almak
            # (non-reentrant `threading.Lock`) kilitlenmeye yol acar.
            self.events_deferred += 1
            _throttle.warn_throttled(
                "agg_events_per_scrape_cap",
                "metrics aggregator METRICS_AGG_MAX_EVENTS_PER_SCRAPE sinirina ulasti -- "
                "kalan olaylar bir sonraki scrape'e ertelendi",
            )

    def _prune_expired(self) -> None:
        cutoff = time.time() - (self._window_minutes * 60)
        self._events = [e for e in self._events if e.ts >= cutoff]

    def _enforce_series_cap(self) -> None:
        if self._max_series <= 0 or not self._events:
            return
        seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
        dropped_series: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
        kept_events: list[MetricEvent] = []
        for event in self._events:
            key = (event.name, tuple(sorted(event.labels.items())))
            if key in seen:
                kept_events.append(event)
                continue
            if len(seen) < self._max_series:
                seen.add(key)
                kept_events.append(event)
            else:
                dropped_series.add(key)
        if dropped_series:
            self.series_dropped += len(dropped_series)
            _throttle.warn_throttled(
                "agg_series_cap",
                f"metrics aggregator METRICS_AGG_MAX_SERIES sinirina ulasti -- "
                f"{len(dropped_series)} yeni seri atlandi",
            )
        self._events = kept_events

    def self_metrics(self) -> dict[str, float]:
        with self._lock:
            return {
                "metrics_aggregator_series_dropped_total": float(self.series_dropped),
                "metrics_aggregator_events_deferred_total": float(self.events_deferred),
            }
