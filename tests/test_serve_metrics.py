"""serve_metrics.py testleri.

Gercek config/registry kullanilmaz -- `build_server()`'a sahte
`metrics_enabled_fn`/`render_fn` enjekte edilir. Sunucu, isletim
sisteminin atadigi bos bir portta (127.0.0.1:0) baslatilir, gercek bir
HTTP istegi yapilir, sonra kapatilir -- dis ag erisimi gerektirmez.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from serve_metrics import build_server


def _start_test_server(*, metrics_enabled_fn, render_fn, inject_fn=None):
    server = build_server(
        "127.0.0.1", 0, metrics_enabled_fn=metrics_enabled_fn, render_fn=render_fn, inject_fn=inject_fn
    )
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


def test_debug_injection_endpoint_closed_by_default():
    """`inject_fn=None` (varsayilan) iken /debug/inject_synthetic 404
    donmeli -- production/varsayilan modda mutasyon endpoint'i kapali."""
    server, thread, port = _start_test_server(metrics_enabled_fn=lambda: True, render_fn=lambda: "x")
    try:
        body = json.dumps({"mode": "fallback-spike", "count": 5}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/debug/inject_synthetic", data=body, method="POST"
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            status = None
        except urllib.error.HTTPError as exc:
            status = exc.code
    finally:
        _stop_test_server(server, thread)

    assert status == 404


def test_debug_injection_endpoint_calls_inject_fn_when_enabled():
    """Enjeksiyon acikken, POST body'sindeki mode/count inject_fn'e
    dogru sekilde iletilmeli -- AYNI surecin registry'sine yazmayi
    simule eder (E2E dogrulamanin dayandigi mekanizma)."""
    calls = []

    def fake_inject(mode, count):
        calls.append((mode, count))
        return {"mode": mode, "samples": count}

    server, thread, port = _start_test_server(
        metrics_enabled_fn=lambda: True, render_fn=lambda: "x", inject_fn=fake_inject
    )
    try:
        body = json.dumps({"mode": "null-intent-spike", "count": 9}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/debug/inject_synthetic", data=body, method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = resp.status
            response_body = json.loads(resp.read().decode("utf-8"))
    finally:
        _stop_test_server(server, thread)

    assert status == 200
    assert calls == [("null-intent-spike", 9)]
    assert response_body == {"mode": "null-intent-spike", "samples": 9}
