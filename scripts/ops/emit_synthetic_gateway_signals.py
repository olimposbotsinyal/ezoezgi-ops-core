"""Sentetik model gateway sinyal ureticisi -- E2E alert pipeline dogrulamasi.

Yikici DEGILDIR: yalnizca `model_gateway.metrics` registry'sine deger
yazar; hicbir gercek Ollama/router/dosya-sistemi cagrisi yapmaz, hicbir
gercek durumu (circuit breaker, saglayici sagligi vb.) degistirmez --
yalnizca METRIK GORUNTUSUNU etkiler. Sinirli sayida ornek uretir
(`--count`, varsayilan 20) -- sinirsiz/surekli calismaz.

TUM enjekte edilen orneklerde `synthetic="true"` etiketi ZORUNLUDUR --
gercek uretim verisinden Prometheus sorgularinda ayirt edilebilmesi icin
(ornegin `rate(model_gateway_fallback_total{synthetic!="true"}[5m])` ile
gercek trafik filtrelenebilir).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MODE_FALLBACK_SPIKE = "fallback-spike"
MODE_NULL_INTENT_SPIKE = "null-intent-spike"
MODE_PREFLIGHT_UNKNOWN = "preflight-unknown"
MODE_CIRCUIT_OPEN_STUCK = "circuit-open-stuck"

MODES = (MODE_FALLBACK_SPIKE, MODE_NULL_INTENT_SPIKE, MODE_PREFLIGHT_UNKNOWN, MODE_CIRCUIT_OPEN_STUCK)


def emit_fallback_spike(metrics: Any, count: int) -> dict[str, Any]:
    for _ in range(count):
        metrics.inc_counter(
            "model_gateway_fallback_total",
            {
                "from_provider": "ollama",
                "to_provider": "local_alt",
                "reason_code": "RUNTIME_CRASH",
                "synthetic": "true",
            },
        )
        metrics.inc_counter(
            "model_gateway_requests_total",
            {"provider": "ollama", "result": "failure", "synthetic": "true"},
        )
    return {"metric": "model_gateway_fallback_total", "samples": count}


def emit_null_intent_spike(metrics: Any, count: int) -> dict[str, Any]:
    for _ in range(count):
        metrics.inc_counter(
            "model_gateway_null_intent_total",
            {"terminal_reason": "FALLBACK_EXHAUSTED", "synthetic": "true"},
        )
    return {"metric": "model_gateway_null_intent_total", "samples": count}


def emit_preflight_unknown(metrics: Any, count: int) -> dict[str, Any]:
    for _ in range(count):
        metrics.inc_counter(
            "model_gateway_preflight_total",
            {"status": "UNKNOWN", "reason_code": "SIGNAL_NOT_AVAILABLE", "synthetic": "true"},
        )
    return {"metric": "model_gateway_preflight_total", "samples": count}


def emit_circuit_open_stuck(metrics: Any, count: int) -> dict[str, Any]:
    # count burada anlamsiz (gauge tek bir deger) ama arayuz tutarliligi
    # icin parametre olarak kabul edilir; gauge'u "sikismis acik" durumuna
    # ayarlar -- GERCEK circuit breaker durumunu DEGISTIRMEZ, yalnizca
    # metrik goruntusunu.
    metrics.set_gauge("model_gateway_circuit_open", {"provider": "ollama", "synthetic": "true"}, 1.0)
    return {"metric": "model_gateway_circuit_open", "samples": 1, "note": "gauge=1 (simulated, real circuit unaffected)"}


_MODE_FUNCS = {
    MODE_FALLBACK_SPIKE: emit_fallback_spike,
    MODE_NULL_INTENT_SPIKE: emit_null_intent_spike,
    MODE_PREFLIGHT_UNKNOWN: emit_preflight_unknown,
    MODE_CIRCUIT_OPEN_STUCK: emit_circuit_open_stuck,
}


def run_mode(mode: str, metrics: Any, count: int) -> dict[str, Any]:
    if mode not in _MODE_FUNCS:
        raise ValueError(f"bilinmeyen mod: {mode} (gecerli: {', '.join(MODES)})")

    generated_at = datetime.now(timezone.utc).isoformat()
    detail = _MODE_FUNCS[mode](metrics, count)

    return {
        "mode": mode,
        "generated_at": generated_at,
        "synthetic": True,
        **detail,
    }


def emit_via_http(target_url: str, mode: str, count: int) -> dict[str, Any]:
    """`serve_metrics.py --enable-debug-injection` calisan bir surece,
    AYNI surecin metrik registry'sine yazmasi icin HTTP uzerinden
    enjeksiyon istegi gonderir -- boylece sentetik sinyal, gercekten
    Prometheus'un scrape edecegi AYNI /metrics ciktisinda gorunur (bkz.
    docs/ops/MONITORING_STACK_RUNBOOK.md "surec-ici singleton" sinirlamasi).
    """
    import urllib.request

    body = json.dumps({"mode": mode, "count": count}).encode("utf-8")
    req = urllib.request.Request(
        f"{target_url.rstrip('/')}/debug/inject_synthetic",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Model gateway sentetik alert sinyal ureticisi")
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--output-dir", default=None, help="Varsayilan: reports/alert_e2e_<UTC>/")
    parser.add_argument(
        "--target-url",
        default=None,
        help=(
            "Verilirse, yerel bir registry yerine bu adresteki "
            "serve_metrics.py'ye (--enable-debug-injection ile calisan) "
            "HTTP POST ile enjekte eder -- boylece sinyal, o surecin "
            "/metrics ciktisinda gercekten gorunur."
        ),
    )
    args = parser.parse_args()

    generated_at = datetime.now(timezone.utc).isoformat()

    if args.target_url:
        detail = emit_via_http(args.target_url, args.mode, args.count)
        result = {"mode": args.mode, "generated_at": generated_at, "synthetic": True, "via": "http", **detail}
    else:
        from model_gateway.config import load_config
        from model_gateway.metrics import configure_metrics, get_metrics

        config = load_config()
        configure_metrics(config.metrics_enabled, config.metrics_exporter)
        metrics = get_metrics()
        result = run_mode(args.mode, metrics, args.count)
        result["via"] = "local"

    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = Path("reports") / f"alert_e2e_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"synthetic_{args.mode}.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False))
    print(f"written={out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
