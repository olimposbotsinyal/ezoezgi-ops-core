"""serve_metrics.py testleri.

Gercek config/registry kullanilmaz -- `build_server()`'a sahte
`metrics_enabled_fn`/`render_fn` enjekte edilir. Sunucu, isletim
sisteminin atadigi bos bir portta (127.0.0.1:0) baslatilir, gercek bir
HTTP istegi yapilir, sonra kapatilir -- dis ag erisimi gerektirmez.
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.request

from serve_metrics import build_server


def _start_test_server(*, metrics_enabled_fn, render_fn):
    server = build_server("127.0.0.1", 0, metrics_enabled_fn=metrics_enabled_fn, render_fn=render_fn)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    return server, thread, port


def _stop_test_server(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_metrics_endpoint_exposes_prometheus_text():
    sample_text = (
        "# TYPE model_gateway_requests_total counter\n"
        'model_gateway_requests_total{provider="ollama",result="success"} 3.0\n'
    )
    server, thread, port = _start_test_server(
        metrics_enabled_fn=lambda: True, render_fn=lambda: sample_text
    )
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as resp:
            status = resp.status
            content_type = resp.headers.get("Content-Type", "")
            body = resp.read().decode("utf-8")
    finally:
        _stop_test_server(server, thread)

    assert status == 200
    assert "text/plain" in content_type
    assert body == sample_text
    assert "model_gateway_requests_total" in body


def test_metrics_endpoint_disabled_behavior():
    """METRICS_ENABLED=false -> /metrics 404 doner (dokumante edilmis
    davranis, bkz. docs/ops/MONITORING_STACK_RUNBOOK.md)."""
    server, thread, port = _start_test_server(
        metrics_enabled_fn=lambda: False, render_fn=lambda: "should-not-be-called"
    )
    try:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5)
            raised = False
            status = None
        except urllib.error.HTTPError as exc:
            raised = True
            status = exc.code
            body = exc.read().decode("utf-8")
    finally:
        _stop_test_server(server, thread)

    assert raised is True
    assert status == 404
    assert "disabled" in body.lower()


def test_metrics_endpoint_unknown_path_returns_404():
    server, thread, port = _start_test_server(metrics_enabled_fn=lambda: True, render_fn=lambda: "x")
    try:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/not-metrics", timeout=5)
            status = None
        except urllib.error.HTTPError as exc:
            status = exc.code
    finally:
        _stop_test_server(server, thread)

    assert status == 404
