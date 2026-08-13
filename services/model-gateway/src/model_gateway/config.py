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
    )
