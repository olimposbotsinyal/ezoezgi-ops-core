"""Model gateway config -- env-first, opsiyonel YAML override.

Oncelik sirasi: ortam degiskeni > config/model_gateway.yaml > kod-ici
varsayilan. Bu, projenin geri kalaninda zaten kullanilan desenle tutarli
(bkz. model_client.py -- OLLAMA_BASE_URL/OLLAMA_TIMEOUT_SECONDS).

Varsayilan davranis KESINLIKLE local-first: `MODEL_PROVIDER_ORDER`
belirtilmezse yalnizca "ollama" denenir, local_alt ve remote varsayilan
olarak KAPALI (*_ENABLED=false). Bu dosyadaki hicbir varsayilan deger,
remote saglayiciyi varsayilan olarak devreye sokmaz.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_YAML_PATH = Path("config/model_gateway.yaml")

# B036 kok neden bulgusu: Vulkan backend'i bu makinede 0xc0000005 ile
# cokuyor (bkz. reports/runtime_incident_20260813T004855Z/gpu_isolation_matrix.md).
# Bu deger, ollama saglayicisi kullanildiginda HER ZAMAN zorlanir.
OLLAMA_VULKAN_ENFORCED_VALUE = "false"


@dataclass(frozen=True)
class GatewayConfig:
    provider_order: tuple[str, ...]

    ollama_enabled: bool
    ollama_host: str
    ollama_model: str

    local_alt_enabled: bool
    local_alt_type: str
    local_alt_host: str
    local_alt_model: str

    remote_enabled: bool
    remote_provider: str
    remote_model: str
    remote_timeout_ms: int
    remote_policy_gate: str

    fallback_max_hops: int
    circuit_breaker_fails: int
    circuit_breaker_reset_sec: int

    # CPU-only calisma zamani dogrulamasi (bkz. runtime_verify.py) --
    # KANIT TEMELLI, sihirli/kesin bir garanti degil. Varsayilanlar
    # bilerek muhafazakar (STRICT=true): operator bir marker dosyasi
    # olusturmadikca, dogrulama genellikle UNKNOWN doner ve STRICT modda
    # bu, Ollama'nin birincil olarak SECILMEMESI anlamina gelir. Bu,
    # "sessiz davranis degisikligi yok" ilkesiyle celismemesi icin
    # docs/ops/MODEL_FALLBACK_RUNBOOK.md'de acikca belgelendi -- operator
    # eylemi (marker dosyasi) olmadan pratik varsayilan davranis
    # degisebilir, bu KASITLI ve DOKUMANTE bir tasarim karari.
    # Varsayilanlar burada da tanimli (load_config()'inkiyle ayni) --
    # GatewayConfig'i dogrudan (load_config() disinda, ornegin testlerde)
    # olusturan mevcut kodun bu alanlari bilmesi gerekmesin diye. Gercek
    # `load_config()` yolu zaten hepsini acikca set eder, bu varsayilanlar
    # yalnizca dogrudan constructor cagrilari icin bir guvenlik agi.
    ollama_cpu_verify_enabled: bool = True
    ollama_cpu_verify_strict: bool = True
    ollama_cpu_verify_timeout_ms: int = 1200
    ollama_cpu_verify_methods: tuple[str, ...] = ("http", "process", "marker")
    ollama_cpu_marker_file: str = "./runtime/ollama_cpu_mode.ok"
    ollama_on_unverified: str = "RESTRICT_PRIMARY"
    startup_preflight_required: bool = True
    ollama_cpu_verify_cache_ttl_sec: int = 60

    # Gozlemlenebilirlik (metrics.py) + SLO/alert esikleri -- bkz.
    # docs/ops/SLO_MODEL_GATEWAY.md, docs/ops/ALERT_PLAYBOOK_MODEL_GATEWAY.md.
    # Metrik kaydi ASLA yonlendirme davranisini etkilemez -- yalnizca gozlem.
    metrics_enabled: bool = True
    metrics_exporter: str = "noop"  # "noop" | "prometheus"
    # `scripts/ops/serve_metrics.py`'nin dinleyecegi adres -- yalnizca o
    # STANDALONE surec tarafindan kullanilir, router/compat'i etkilemez.
    metrics_http_host: str = "127.0.0.1"
    metrics_http_port: int = 9108
    slo_window_days: int = 7
    alert_null_intent_warn: float = 0.01
    alert_null_intent_crit: float = 0.02
    alert_fallback_spike_multiplier: float = 3.0
    daily_smoke_enabled: bool = True

    # Cross-process metrik sink'i (bkz. metrics_sink.py, metrics_aggregate.py).
    # `jsonl_append` YENI VARSAYILAN -- surecler-arasi gorunurluk saglar
    # (onceki `in_memory`-yalnizca davranisin bilinen sinirlamasini kapatir,
    # bkz. docs/ops/MONITORING_STACK_RUNBOOK.md "KRITIK mimari sinirlama").
    metrics_sink: str = "jsonl_append"  # "jsonl_append" | "in_memory"
    metrics_jsonl_path: str = "./data/metrics/model_gateway_metrics.jsonl"
    metrics_jsonl_max_mb: float = 50.0
    metrics_jsonl_retention_days: int = 7
    metrics_agg_window_min: int = 60


def _load_yaml_overrides(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def _get_bool(env_key: str, yaml_overrides: dict[str, Any], yaml_key: str, default: bool) -> bool:
    raw = os.getenv(env_key)
    if raw is not None:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if yaml_key in yaml_overrides:
        return bool(yaml_overrides[yaml_key])
    return default


def _get_int(env_key: str, yaml_overrides: dict[str, Any], yaml_key: str, default: int) -> int:
    raw = os.getenv(env_key)
    if raw is not None:
        try:
            return int(raw)
        except ValueError:
            return default
    if yaml_key in yaml_overrides:
        try:
            return int(yaml_overrides[yaml_key])
        except (TypeError, ValueError):
            return default
    return default


def _get_str(env_key: str, yaml_overrides: dict[str, Any], yaml_key: str, default: str) -> str:
    raw = os.getenv(env_key)
    if raw is not None:
        return raw
    if yaml_key in yaml_overrides:
        return str(yaml_overrides[yaml_key])
    return default


def load_config(yaml_path: Path | None = None) -> GatewayConfig:
    overrides = _load_yaml_overrides(yaml_path or DEFAULT_YAML_PATH)

    provider_order_raw = _get_str(
        "MODEL_PROVIDER_ORDER", overrides, "model_provider_order", "ollama"
    )
    provider_order = tuple(p.strip() for p in provider_order_raw.split(",") if p.strip())
    if not provider_order:
        provider_order = ("ollama",)

    return GatewayConfig(
        provider_order=provider_order,
        ollama_enabled=_get_bool("OLLAMA_ENABLED", overrides, "ollama_enabled", True),
        ollama_host=_get_str("OLLAMA_HOST", overrides, "ollama_host", "http://localhost:11434"),
        ollama_model=_get_str("OLLAMA_MODEL", overrides, "ollama_model", "llama3"),
        local_alt_enabled=_get_bool("LOCAL_ALT_ENABLED", overrides, "local_alt_enabled", False),
        local_alt_type=_get_str("LOCAL_ALT_TYPE", overrides, "local_alt_type", "llamacpp"),
        local_alt_host=_get_str(
            "LOCAL_ALT_HOST", overrides, "local_alt_host", "http://localhost:8080"
        ),
        local_alt_model=_get_str("LOCAL_ALT_MODEL", overrides, "local_alt_model", ""),
        remote_enabled=_get_bool("REMOTE_ENABLED", overrides, "remote_enabled", False),
        remote_provider=_get_str("REMOTE_PROVIDER", overrides, "remote_provider", ""),
        remote_model=_get_str("REMOTE_MODEL", overrides, "remote_model", ""),
        remote_timeout_ms=_get_int("REMOTE_TIMEOUT_MS", overrides, "remote_timeout_ms", 10000),
        remote_policy_gate=_get_str(
            "REMOTE_POLICY_GATE", overrides, "remote_policy_gate", "required"
        ),
        fallback_max_hops=_get_int("FALLBACK_MAX_HOPS", overrides, "fallback_max_hops", 2),
        circuit_breaker_fails=_get_int(
            "CIRCUIT_BREAKER_FAILS", overrides, "circuit_breaker_fails", 3
        ),
        circuit_breaker_reset_sec=_get_int(
            "CIRCUIT_BREAKER_RESET_SEC", overrides, "circuit_breaker_reset_sec", 120
        ),
        ollama_cpu_verify_enabled=_get_bool(
            "OLLAMA_CPU_VERIFY_ENABLED", overrides, "ollama_cpu_verify_enabled", True
        ),
        ollama_cpu_verify_strict=_get_bool(
            "OLLAMA_CPU_VERIFY_STRICT", overrides, "ollama_cpu_verify_strict", True
        ),
        ollama_cpu_verify_timeout_ms=_get_int(
            "OLLAMA_CPU_VERIFY_TIMEOUT_MS", overrides, "ollama_cpu_verify_timeout_ms", 1200
        ),
        ollama_cpu_verify_methods=_get_methods_tuple(
            "OLLAMA_CPU_VERIFY_METHODS", overrides, "ollama_cpu_verify_methods", "http,process,marker"
        ),
        ollama_cpu_marker_file=_get_str(
            "OLLAMA_CPU_MARKER_FILE",
            overrides,
            "ollama_cpu_marker_file",
            "./runtime/ollama_cpu_mode.ok",
        ),
        ollama_on_unverified=_get_str(
            "OLLAMA_ON_UNVERIFIED", overrides, "ollama_on_unverified", "RESTRICT_PRIMARY"
        ),
        startup_preflight_required=_get_bool(
            "STARTUP_PREFLIGHT_REQUIRED", overrides, "startup_preflight_required", True
        ),
        ollama_cpu_verify_cache_ttl_sec=_get_int(
            "OLLAMA_CPU_VERIFY_CACHE_TTL_SEC", overrides, "ollama_cpu_verify_cache_ttl_sec", 60
        ),
        metrics_enabled=_get_bool("METRICS_ENABLED", overrides, "metrics_enabled", True),
        metrics_exporter=_get_str("METRICS_EXPORTER", overrides, "metrics_exporter", "noop"),
        metrics_http_host=_get_str("METRICS_HTTP_HOST", overrides, "metrics_http_host", "127.0.0.1"),
        metrics_http_port=_get_int("METRICS_HTTP_PORT", overrides, "metrics_http_port", 9108),
        slo_window_days=_get_int("SLO_WINDOW_DAYS", overrides, "slo_window_days", 7),
        alert_null_intent_warn=_get_float(
            "ALERT_NULL_INTENT_WARN", overrides, "alert_null_intent_warn", 0.01
        ),
        alert_null_intent_crit=_get_float(
            "ALERT_NULL_INTENT_CRIT", overrides, "alert_null_intent_crit", 0.02
        ),
        alert_fallback_spike_multiplier=_get_float(
            "ALERT_FALLBACK_SPIKE_MULTIPLIER", overrides, "alert_fallback_spike_multiplier", 3.0
        ),
        daily_smoke_enabled=_get_bool(
            "DAILY_SMOKE_ENABLED", overrides, "daily_smoke_enabled", True
        ),
        metrics_sink=_get_str("METRICS_SINK", overrides, "metrics_sink", "jsonl_append"),
        metrics_jsonl_path=_get_str(
            "METRICS_JSONL_PATH",
            overrides,
            "metrics_jsonl_path",
            "./data/metrics/model_gateway_metrics.jsonl",
        ),
        metrics_jsonl_max_mb=_get_float(
            "METRICS_JSONL_MAX_MB", overrides, "metrics_jsonl_max_mb", 50.0
        ),
        metrics_jsonl_retention_days=_get_int(
            "METRICS_JSONL_RETENTION_DAYS", overrides, "metrics_jsonl_retention_days", 7
        ),
        metrics_agg_window_min=_get_int(
            "METRICS_AGG_WINDOW_MIN", overrides, "metrics_agg_window_min", 60
        ),
    )


def _get_float(env_key: str, yaml_overrides: dict[str, Any], yaml_key: str, default: float) -> float:
    raw = os.getenv(env_key)
    if raw is not None:
        try:
            return float(raw)
        except ValueError:
            return default
    if yaml_key in yaml_overrides:
        try:
            return float(yaml_overrides[yaml_key])
        except (TypeError, ValueError):
            return default
    return default


def _get_methods_tuple(
    env_key: str, yaml_overrides: dict[str, Any], yaml_key: str, default: str
) -> tuple[str, ...]:
    raw = _get_str(env_key, yaml_overrides, yaml_key, default)
    methods = tuple(m.strip() for m in raw.split(",") if m.strip())
    return methods or tuple(m.strip() for m in default.split(","))
