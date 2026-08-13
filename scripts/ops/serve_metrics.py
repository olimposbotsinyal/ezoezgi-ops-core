"""Model gateway `/metrics` HTTP endpoint -- bagimliliksiz (stdlib `http.server`).

**KRITIK MIMARI SINIRLAMA (durustce belirtilmeli, bkz.
docs/ops/MONITORING_STACK_RUNBOOK.md "Bilinen sinirlamalar"):**
`model_gateway.metrics.get_metrics()` SUREC-ICI bir singleton'dur. Bu
script AYRI, bagimsiz bir surec olarak calisir -- yalnizca KENDI
surecinde biriken metrikleri gosterebilir. Bu projede `classify()`
cagrilari kisa omurlu, ayri komut satiri calistirmalari/testler
uzerinden yapiliyor (kalici bir "servis sureci" yok) -- bu yuzden bu
endpoint VARSAYILAN OLARAK gercek uretim trafiginin metriklerini
YAKALAMAZ. Gercekci kullanim alanlari:
  (a) Bu surec icinde uretilen metriklerin (ornegin
      `scripts/ops/emit_synthetic_gateway_signals.py` ile) canli
      gozlemi/E2E alert pipeline dogrulamasi,
  (b) Ileride kalici bir servis sureci eklendiginde (mimari genisleme,
      bu gorevin kapsami disinda) dogrudan entegrasyon noktasi.

Lightweight: yalnizca bellek-ici registry'yi okur, inference yoluna
DOKUNMAZ (zaten ayri bir surectir).
"""

from __future__ import annotations

import argparse
import logging
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

logger = logging.getLogger("serve_metrics")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9108
METRICS_PATH = "/metrics"


def make_handler(*, metrics_enabled_fn: Callable[[], bool], render_fn: Callable[[], str]):
    """Test edilebilirlik icin factory -- gercek config/registry yerine
    sahte fonksiyonlar enjekte edilebilir."""

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

            body = render_fn()
            self._send(200, body, content_type="text/plain; version=0.0.4; charset=utf-8")

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


def build_server(host: str, port: int, *, metrics_enabled_fn, render_fn) -> ThreadingHTTPServer:
    handler = make_handler(metrics_enabled_fn=metrics_enabled_fn, render_fn=render_fn)
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")

    from model_gateway.config import load_config
    from model_gateway.metrics import configure_metrics, get_metrics

    parser = argparse.ArgumentParser(description="Model gateway /metrics endpoint (stdlib http.server)")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    config = load_config()
    configure_metrics(config.metrics_enabled, config.metrics_exporter)
    registry = get_metrics()

    server = build_server(
        args.host,
        args.port,
        metrics_enabled_fn=lambda: config.metrics_enabled,
        render_fn=registry.render_prometheus_text,
    )

    logger.info(
        "Metrics endpoint dinliyor: http://%s:%d%s (metrics_enabled=%s)",
        args.host,
        args.port,
        METRICS_PATH,
        config.metrics_enabled,
    )
    logger.warning(
        "ONEMLI: bu surec, yalnizca KENDI surecinde biriken metrikleri gosterir -- "
        "gercek classify() trafigi ayri kisa-omurlu sureclerden geliyorsa burada GORUNMEZ."
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Kapatiliyor...")
        server.shutdown()


if __name__ == "__main__":
    main()
