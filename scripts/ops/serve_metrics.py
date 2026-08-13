"""Model gateway `/metrics` HTTP endpoint -- bagimliliksiz (stdlib `http.server`).

**GUNCELLEME (cross-process metrik sink'i, bkz.
docs/ops/MONITORING_STACK_RUNBOOK.md "Bilinen sinirlamalar"):**
Onceki tasarimin sinirlamasi -- `model_gateway.metrics.get_metrics()`
SUREC-ICI bir singleton oldugu, `classify()` cagrilarinin ise kisa
omurlu, AYRI sureclerde calistigi icin metriklerin asla birlesmedigi --
`METRICS_SINK=jsonl_append` (yeni varsayilan) ile KAPATILDI: her surec
kendi metrik olayini paylasilan bir JSONL dosyasina (bkz.
`model_gateway.metrics_sink.JsonlAppendSink`) append eder, bu script de
`/metrics` her cagrildiginda o dosyayi `model_gateway.metrics_aggregate`
ile okuyup birlestirir (bkz. `main()` icindeki `render_fn` secimi).

Bu, GERCEK-ZAMANLI degil, GOZLEM-ANINDA (read-time) birlestirmedir --
"bilinen odunlesimler" icin asagidaki + RUNBOOK'taki notlara bakin:
  - Eventual consistency penceresi: bir surec `write()` cagirdiktan
    sonra, o olay ancak BIR SONRAKI `/metrics` GET'inde (sonraki
    okumada) gorunur -- gercek zamanli push degil, dosya-tabanli
    "en son okundugunda birlestir" modelidir.
  - Scrape gecikmesi: her `/metrics` istegi butun JSONL dosyasini (+
    hala pencere icindeki rotasyon dosyalarini) yeniden okur/birlestirir
    -- Prometheus scrape araligi (`scrape_interval`), dosya boyutu ve
    disk G/Ç hizina bagli olarak gozlemlenebilir bir gecikme ekler.
  - `METRICS_SINK=in_memory` (eski davranis, opt-in) hala desteklenir --
    bu modda script yalnizca KENDI surecinde biriken metrikleri gosterir
    (onceki sinirlama aynen gecerlidir).

Lightweight: `jsonl_append` modunda dosya sistemini okur, `in_memory`
modunda bellek-ici registry'yi okur -- HER IKI DURUMDA da inference
yoluna DOKUNMAZ (zaten ayri bir surectir).
"""

from __future__ import annotations

import argparse
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

logger = logging.getLogger("serve_metrics")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9108
METRICS_PATH = "/metrics"
INJECT_PATH = "/debug/inject_synthetic"


def make_handler(
    *,
    metrics_enabled_fn: Callable[[], bool],
    render_fn: Callable[[], str],
    inject_fn: Callable[[str, int], dict] | None = None,
    aggregation_error_cls: tuple[type[BaseException], ...] = (),
):
    """Test edilebilirlik icin factory -- gercek config/registry yerine
    sahte fonksiyonlar enjekte edilebilir.

    `inject_fn`, verilirse `POST /debug/inject_synthetic` yolunu acar --
    bu, `scripts/ops/emit_synthetic_gateway_signals.py`'nin AYNI surecin
    metrik registry'sine (bu HTTP sunucusunun KENDI surecine) sentetik
    ornek enjekte edebilmesi icindir (bkz. E2E dogrulama akisi). `None`
    ise (varsayilan) bu yol tamamen KAPALI -- production/varsayilan
    calisma modunda mutasyon endpoint'i acik degildir.

    `aggregation_error_cls`, verilirse `render_fn()` bu turden bir
    exception firlattiginda `/metrics` temiz bir 503 + tanilama nedeni
    doner (bkz. `model_gateway.metrics_aggregate.AggregationError`).
    Varsayilan bos tuple -- eski davranis (hicbir ozel yakalama yok,
    geriye donuk uyumlu) korunur.
    """

    class MetricsHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server sozlesmesi)
            if self.path != METRICS_PATH:
                self._send(404, "not found\n")
                return

            if not metrics_enabled_fn():
                # Gorev talimati: "METRICS_ENABLED=false ise 404 veya
                # minimal noop payload donsun (davranis belgelenmeli)".
                # Burada 404 secildi -- bkz. docs/ops/MONITORING_STACK_RUNBOOK.md.
                self._send(404, "metrics disabled (METRICS_ENABLED=false)\n")
                return

            try:
                body = render_fn()
            except aggregation_error_cls as exc:
                self._send(
                    503,
                    f"metrics aggregation failed: {exc}\n",
                    content_type="text/plain; charset=utf-8",
                )
                return
            self._send(200, body, content_type="text/plain; version=0.0.4; charset=utf-8")

        def do_POST(self) -> None:  # noqa: N802
            if self.path != INJECT_PATH or inject_fn is None:
                self._send(404, "not found\n")
                return

            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
                mode = payload["mode"]
                count = int(payload.get("count", 20))
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                self._send(400, json.dumps({"error": str(exc)}), content_type="application/json")
                return

            try:
                result = inject_fn(mode, count)
            except Exception as exc:  # test/debug amacli -- surecin kendisini cokertmesin
                self._send(500, json.dumps({"error": str(exc)}), content_type="application/json")
                return

            self._send(200, json.dumps(result), content_type="application/json")

        def _send(self, status: int, body: str, content_type: str = "text/plain; charset=utf-8") -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            logger.info("%s - %s", self.address_string(), format % args)

    return MetricsHandler


def build_server(
    host: str,
    port: int,
    *,
    metrics_enabled_fn,
    render_fn,
    inject_fn=None,
    aggregation_error_cls: tuple[type[BaseException], ...] = (),
) -> ThreadingHTTPServer:
    handler = make_handler(
        metrics_enabled_fn=metrics_enabled_fn,
        render_fn=render_fn,
        inject_fn=inject_fn,
        aggregation_error_cls=aggregation_error_cls,
    )
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")

    from model_gateway.config import load_config
    from model_gateway.metrics import configure_metrics, get_metrics

    parser = argparse.ArgumentParser(description="Model gateway /metrics endpoint (stdlib http.server)")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--enable-debug-injection",
        action="store_true",
        help=(
            "POST /debug/inject_synthetic yolunu acar (yalnizca E2E alert "
            "pipeline testleri icin -- varsayilan olarak KAPALI)"
        ),
    )
    args = parser.parse_args()

    config = load_config()
    configure_metrics(
        config.metrics_enabled,
        config.metrics_exporter,
        sink_type=config.metrics_sink,
        jsonl_path=config.metrics_jsonl_path,
        jsonl_max_mb=config.metrics_jsonl_max_mb,
        jsonl_retention_days=config.metrics_jsonl_retention_days,
    )
    registry = get_metrics()

    inject_fn = None
    if args.enable_debug_injection:
        from emit_synthetic_gateway_signals import run_mode

        inject_fn = lambda mode, count: run_mode(mode, registry, count)  # noqa: E731
        logger.warning(
            "DEBUG INJECTION ACIK: POST %s ile disaridan metrik enjekte edilebilir -- "
            "yalnizca izole/test ortaminda kullanin.",
            INJECT_PATH,
        )

    aggregation_error_cls: tuple[type[BaseException], ...] = ()
    if config.metrics_sink == "jsonl_append":
        from pathlib import Path

        from model_gateway import metrics_aggregate

        jsonl_path = Path(config.metrics_jsonl_path)
        agg_window_min = config.metrics_agg_window_min
        aggregation_error_cls = (metrics_aggregate.AggregationError,)

        def render_fn() -> str:
            agg_registry = metrics_aggregate.aggregate(jsonl_path, agg_window_min)
            self_values = {**registry.sink_self_metrics(), **metrics_aggregate.self_metrics()}
            for metric_name, value in self_values.items():
                agg_registry.set_counter_absolute(metric_name, {}, value)
            return agg_registry.render_prometheus_text()

        logger.info(
            "Metrics kaynagi: JSONL aggregator (%s, pencere=%d dk) -- cross-process "
            "gorunurluk aktif (eventual-consistency, bkz. docs/ops/MONITORING_STACK_RUNBOOK.md).",
            jsonl_path,
            agg_window_min,
        )
    else:
        render_fn = registry.render_prometheus_text
        logger.warning(
            "Metrics kaynagi: surec-ici (in_memory) registry -- yalnizca BU surecte "
            "biriken metrikler gorunur (METRICS_SINK=in_memory, opt-in eski davranis)."
        )

    server = build_server(
        args.host,
        args.port,
        metrics_enabled_fn=lambda: config.metrics_enabled,
        render_fn=render_fn,
        inject_fn=inject_fn,
        aggregation_error_cls=aggregation_error_cls,
    )

    logger.info(
        "Metrics endpoint dinliyor: http://%s:%d%s (metrics_enabled=%s, metrics_sink=%s)",
        args.host,
        args.port,
        METRICS_PATH,
        config.metrics_enabled,
        config.metrics_sink,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Kapatiliyor...")
        server.shutdown()


if __name__ == "__main__":
    main()
