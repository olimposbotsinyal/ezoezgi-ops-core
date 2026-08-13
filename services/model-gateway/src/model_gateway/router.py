"""Model gateway router -- sirali fallback orkestrasyonu.

Saglayicilari `MODEL_PROVIDER_ORDER` sirasina gore dener; her atlama
(fallback) acikca loglanir VE data/audit/audit.log.jsonl'e append edilir
(ADR-009) -- "sessiz fallback yok" kurali burada uygulanir.

`AuditLogger` (apps/orchestrator/src/audit_logger.py) kasitli olarak
yeniden kullanildi: kendi docstring'inde "genel amacli, tum riskli/
izlenmesi gereken aksiyonlar icin" oldugu belirtiliyor -- orchestrator'un
task-graph/registry/risk-engine mantigina BAGIMLI degil, yalnizca
append-only JSONL yazan stateless bir yardimci. bridge.py'nin
apps/orchestrator'a bagimli olmama kurali (MASTER_ROADMAP.md §3),
orkestrasyon mantigina bagimliligi hedefliyordu -- bu paylasilan audit
yardimcisini kapsamiyor.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from audit_logger import AuditLogger

from model_gateway import local_alt_provider, ollama_provider, remote_provider
from model_gateway.base import (
    REASON_CIRCUIT_OPEN,
    REASON_DISABLED,
    REASON_POLICY_BLOCK,
    AllProvidersFailedError,
    GenerateRequest,
    Provider,
    ProviderError,
    ProviderResponse,
)
from model_gateway.config import GatewayConfig, load_config
from model_gateway.health import CircuitBreaker
from model_gateway.policy import is_remote_allowed

logger = logging.getLogger("model_gateway.router")


def _build_providers(config: GatewayConfig) -> dict[str, Provider]:
    return {
        "ollama": ollama_provider.OllamaProvider(host=config.ollama_host, model=config.ollama_model),
        "local_alt": local_alt_provider.LocalAltProvider(
            host=config.local_alt_host,
            model=config.local_alt_model,
            backend_type=config.local_alt_type,
        ),
        "remote": remote_provider.RemoteProvider(
            provider_id=config.remote_provider,
            model=config.remote_model,
            timeout_ms=config.remote_timeout_ms,
        ),
    }


class ModelGatewayRouter:
    """Config'deki `provider_order`'a gore sirali fallback uygular.

    Her deneme (basarili veya basarisiz) `data/audit/audit.log.jsonl`'e
    yazilir. Tum saglayicilar basarisiz olursa `AllProvidersFailedError`
    firlatilir -- caller (ornegin ollama_nlu.classify() uzerinden
    model_gateway.compat.RouterBackedClient) bunu kendi guvenli
    davranisina (null-intent) cevirmekten sorumludur.
    """

    def __init__(
        self,
        config: GatewayConfig | None = None,
        providers: dict[str, Provider] | None = None,
        audit_logger: AuditLogger | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        policy_check: Callable[[], tuple[bool, str]] | None = None,
    ) -> None:
        self._config = config or load_config()
        self._providers = providers or _build_providers(self._config)
        self._audit_logger = audit_logger or AuditLogger()
        self._circuit_breaker = circuit_breaker or CircuitBreaker(
            fail_threshold=self._config.circuit_breaker_fails,
            reset_seconds=self._config.circuit_breaker_reset_sec,
        )
        self._policy_check = policy_check or is_remote_allowed

    def _is_enabled(self, name: str) -> bool:
        c = self._config
        if name == "ollama":
            return c.ollama_enabled
        if name == "local_alt":
            return c.local_alt_enabled
        if name == "remote":
            return c.remote_enabled
        return False

    def _audit(self, *, provider: str, event: str, reason_code: str, detail: str) -> None:
        self._audit_logger.log(
            alias=None,
            task="MODEL_GATEWAY_GENERATE",
            status=event,
            risk_level="low",
            details={
                "provider": provider,
                "reason_code": reason_code,
                "detail": detail,
            },
        )

    def _skip(self, name: str, reason_code: str, detail: str, attempts: list) -> None:
        logger.info("Saglayici atlandi: %s (%s) -- %s", name, reason_code, detail)
        self._audit(provider=name, event="SKIPPED", reason_code=reason_code, detail=detail)
        attempts.append((name, reason_code, detail))

    def generate(self, request: GenerateRequest) -> ProviderResponse:
        attempts: list[tuple[str, str, str]] = []
        real_attempts = 0
        max_real_attempts = 1 + self._config.fallback_max_hops

        for name in self._config.provider_order:
            provider = self._providers.get(name)
            if provider is None:
                self._skip(name, REASON_DISABLED, f"bilinmeyen saglayici adi: {name}", attempts)
                continue

            if not self._is_enabled(name):
                self._skip(name, REASON_DISABLED, "saglayici config'de kapali", attempts)
                continue

            if name == "remote" and self._config.remote_policy_gate == "required":
                allowed, detail = self._policy_check()
                if not allowed:
                    self._skip(name, REASON_POLICY_BLOCK, f"REMOTE_BLOCKED_BY_POLICY: {detail}", attempts)
                    continue

            if self._circuit_breaker.is_open(name):
                self._skip(name, REASON_CIRCUIT_OPEN, "devre acik (art arda hata esigi asildi)", attempts)
                continue

            if real_attempts >= max_real_attempts:
                logger.info("FALLBACK_MAX_HOPS asildi, %s denenmeyecek", name)
                break

            real_attempts += 1
            try:
                response = provider.generate(request)
            except ProviderError as exc:
                self._circuit_breaker.record_failure(name)
                logger.warning("Saglayici basarisiz: %s (%s) -- %s", name, exc.reason_code, exc.detail)
                self._audit(provider=name, event="FALLBACK", reason_code=exc.reason_code, detail=exc.detail)
                attempts.append((name, exc.reason_code, exc.detail))
                continue

            self._circuit_breaker.record_success(name)
            logger.info("Saglayici basarili: %s", name)
            self._audit(provider=name, event="SUCCESS", reason_code="", detail="")
            return response

        raise AllProvidersFailedError(attempts)

    def healthcheck_all(self) -> dict[str, Any]:
        return {name: p.healthcheck() for name, p in self._providers.items()}


_default_router: ModelGatewayRouter | None = None


def get_router() -> ModelGatewayRouter:
    """Surec-genelinde tek bir router ornegi (lazy singleton)."""
    global _default_router
    if _default_router is None:
        _default_router = ModelGatewayRouter()
    return _default_router
