"""`/metrics` scrape performans karsilastirmasi -- ONCESI (tam-yeniden-
tarama, `metrics_aggregate.aggregate()`) vs SONRASI (artimli aggregator
+ TTL onbellek, `IncrementalAggregator` + `CachedMetricsRenderer`).

Yikici DEGILDIR: yalnizca gecici bir dizinde (otomatik temizlenir)
sentetik bir JSONL veri kumesi uretir, hicbir gercek Ollama/router
cagrisi yapmaz, `data/metrics/`'e hicbir kalici veri yazmaz.

Olcumler "kaba" (rough) olarak etiketlenmistir -- bkz. gorev talimati:
`time.perf_counter()` ile duvar-saati gecikmesi (p50/p95), kaba CPU
suresi (`time.process_time()` farki, tum tekrarlar toplami) ve kaba
bellek ayak izi (`tracemalloc`, tepe deger) -- bunlar dogrulama-sinifi
kiyaslamalar degildir, yalnizca "yon dogru mu" sorusuna hizli/portatif
bir cevap saglar (harici bir profiler/benchmark kutuphanesi eklemeden,
bu makinede zaten kurulu olmayan araclara bagimlilik yaratmadan).

Cikti: `reports/metrics_perf_<UTC>/summary.md` (insan-okur ozet) +
`raw.json` (ham sayilar).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DEFAULT_EVENT_COUNT = 5000
DEFAULT_DISTINCT_SERIES = 20
DEFAULT_ROTATE_MB = 0.05
DEFAULT_WINDOW_MINUTES = 60
DEFAULT_REPEATS = 15
DEFAULT_CACHE_TTL_SEC = 5.0


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return round(s[f], 4)
    return round(s[f] + (s[c] - s[f]) * (k - f), 4)


def _generate_dataset(base_dir: Path, *, event_count: int, distinct_series: int, rotate_mb: float) -> Path:
    from model_gateway.metrics_sink import JsonlAppendSink, MetricEvent

    jsonl_path = base_dir / "bench_metrics.jsonl"
    sink = JsonlAppendSink(
        jsonl_path, max_mb=rotate_mb, retention_days=7, cardinality_cap=distinct_series + 10
    )
    now = time.time()
    for i in range(event_count):
        sink.write(
            MetricEvent(
                ts=now,
                metric_type="counter",
                name="bench_requests_total",
                labels={"shard": str(i % distinct_series)},
                value=1.0,
            )
        )
    return jsonl_path


def _measure(fn: Callable[[], Any], repeats: int) -> dict[str, float]:
    latencies_ms: list[float] = []
    tracemalloc.start()
    cpu_start = time.process_time()
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)
    cpu_ms = (time.process_time() - cpu_start) * 1000.0
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return {
        "p50_ms": _percentile(latencies_ms, 0.50),
        "p95_ms": _percentile(latencies_ms, 0.95),
        "min_ms": round(min(latencies_ms), 4),
        "max_ms": round(max(latencies_ms), 4),
        "mean_ms": round(statistics.fmean(latencies_ms), 4),
        "cpu_time_ms_total": round(cpu_ms, 4),
        "peak_memory_kb": round(peak / 1024.0, 2),
        "repeats": repeats,
    }


def run_benchmark(
    *,
    event_count: int,
    distinct_series: int,
    rotate_mb: float,
    window_minutes: int,
    repeats: int,
    cache_ttl_sec: float,
) -> dict[str, Any]:
    from model_gateway import metrics_aggregate
    from model_gateway.metrics_cache import CachedMetricsRenderer

    with tempfile.TemporaryDirectory(prefix="metrics_bench_") as tmp:
        base = Path(tmp)
        jsonl_path = _generate_dataset(
            base, event_count=event_count, distinct_series=distinct_series, rotate_mb=rotate_mb
        )
        rotated_count = len(list(base.glob("bench_metrics.*.rotated.jsonl")))

        results: dict[str, dict[str, float]] = {}

        # ONCESI: her cagrida butun JSONL gecmisini (ana + rotasyon
        # dosyalari) bastan okur/parse eder/replay eder.
        results["full_rescan_before"] = _measure(
            lambda: metrics_aggregate.aggregate(jsonl_path, window_minutes), repeats
        )

        # SONRASI (onbelleksiz): soguk yakalama HARIC -- yalnizca
        # "steady-state" (yeni yazma yokken tekrar scrape) maliyeti.
        state_path_inc = base / "bench_inc.state.json"
        incremental = metrics_aggregate.IncrementalAggregator(jsonl_path, state_path_inc, window_minutes)
        incremental.refresh()  # soguk yakalama -- olcum disi
        results["incremental_steady_state_after"] = _measure(lambda: incremental.refresh(), repeats)

        # SONRASI (+ TTL onbellek): serve_metrics.py'nin gercekte
        # kullandigi tam yol.
        state_path_cache = base / "bench_cache.state.json"
        incremental_cached = metrics_aggregate.IncrementalAggregator(jsonl_path, state_path_cache, window_minutes)
        cached_renderer = CachedMetricsRenderer(incremental_cached, ttl_seconds=cache_ttl_sec)
        cached_renderer.render()  # soguk yakalama -- olcum disi
        results["incremental_plus_cache_after"] = _measure(lambda: cached_renderer.render(), repeats)

        return {
            "dataset": {
                "event_count": event_count,
                "distinct_series": distinct_series,
                "rotate_mb": rotate_mb,
                "rotated_files_created": rotated_count,
                "window_minutes": window_minutes,
                "repeats": repeats,
                "cache_ttl_sec": cache_ttl_sec,
            },
            "results": results,
        }


def _write_report(data: dict[str, Any], out_dir: Path, generated_at: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_path = out_dir / "raw.json"
    raw_path.write_text(json.dumps({"generated_at": generated_at, **data}, indent=2), encoding="utf-8")

    r = data["results"]
    d = data["dataset"]
    full = r["full_rescan_before"]
    inc = r["incremental_steady_state_after"]
    cached = r["incremental_plus_cache_after"]

    def speedup(before: float, after: float) -> str:
        if after <= 0:
            return "n/a"
        return f"{before / after:.1f}x"

    lines = [
        "# Metrics Scrape Performance Benchmark",
        "",
        f"Generated: {generated_at}",
        (
            f"Dataset: event_count={d['event_count']}, distinct_series={d['distinct_series']}, "
            f"rotate_mb={d['rotate_mb']}, rotated_files_created={d['rotated_files_created']}, "
            f"window_minutes={d['window_minutes']}, repeats={d['repeats']}, "
            f"cache_ttl_sec={d['cache_ttl_sec']}"
        ),
        "",
        "| Scenario | p50 (ms) | p95 (ms) | mean (ms) | CPU total (ms) | Peak memory (KB) |",
        "|---|---|---|---|---|---|",
        (
            f"| full_rescan (ONCESI) | {full['p50_ms']} | {full['p95_ms']} | {full['mean_ms']} | "
            f"{full['cpu_time_ms_total']} | {full['peak_memory_kb']} |"
        ),
        (
            f"| incremental, onbeleksiz (SONRASI) | {inc['p50_ms']} | {inc['p95_ms']} | {inc['mean_ms']} | "
            f"{inc['cpu_time_ms_total']} | {inc['peak_memory_kb']} |"
        ),
        (
            f"| incremental + TTL onbellek (SONRASI) | {cached['p50_ms']} | {cached['p95_ms']} | "
            f"{cached['mean_ms']} | {cached['cpu_time_ms_total']} | {cached['peak_memory_kb']} |"
        ),
        "",
        f"Hizlanma (p50, full_rescan'a gore): incremental onbeleksiz **{speedup(full['p50_ms'], inc['p50_ms'])}**, "
        f"incremental + onbellek **{speedup(full['p50_ms'], cached['p50_ms'])}**.",
        "",
        "> Olcumler bu makineye/ana kaba (rough) tahminlerdir -- "
        "dogrulama-sinifi kiyaslama degildir (bkz. modul docstring'i). "
        "Ham sayilar: `raw.json`.",
    ]
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines))
    print(f"raw_json={raw_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Model gateway /metrics scrape performans kiyaslamasi")
    parser.add_argument("--event-count", type=int, default=DEFAULT_EVENT_COUNT)
    parser.add_argument("--distinct-series", type=int, default=DEFAULT_DISTINCT_SERIES)
    parser.add_argument("--rotate-mb", type=float, default=DEFAULT_ROTATE_MB)
    parser.add_argument("--window-minutes", type=int, default=DEFAULT_WINDOW_MINUTES)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--cache-ttl-sec", type=float, default=DEFAULT_CACHE_TTL_SEC)
    parser.add_argument("--output-dir", default=None, help="Varsayilan: reports/metrics_perf_<UTC>/")
    args = parser.parse_args()

    data = run_benchmark(
        event_count=args.event_count,
        distinct_series=args.distinct_series,
        rotate_mb=args.rotate_mb,
        window_minutes=args.window_minutes,
        repeats=args.repeats,
        cache_ttl_sec=args.cache_ttl_sec,
    )

    generated_at = datetime.now(timezone.utc).isoformat()
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = Path("reports") / f"metrics_perf_{ts}"

    _write_report(data, out_dir, generated_at)
    return 0


if __name__ == "__main__":
    sys.exit(main())
