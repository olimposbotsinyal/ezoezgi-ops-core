"""Model gateway router -- sirali fallback orkestrasyonu + CPU-verify kapisi.

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

CPU-verify kapisi (runtime_verify.py): Ollama secilmeden once, config'de
etkinse, `verify_ollama_cpu_mode()` cagrilir (kisa bir TTL ile
onbelleklenir -- her istek icin tekrar tekrar calistirilmaz). STRICT modda
(varsayilan), dogrulama VERIFIED donmezse Ollama birincil olarak
SECILMEZ -- bkz. docs/ops/MODEL_FALLBACK_RUNBOOK.md "CPU-only dogrulama
kanit temellidir, sihirli degildir" bolumu icin ONEMLI davranissal sonuc.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable

from audit_logger import AuditLogger

from model_gateway import local_alt_provider, ollama_provider, remote_provider
from model_gateway.base import (
    REASON_CIRCUIT_OPEN,
    REASON_DISABLED,
    REASON_FALLBACK_EXHAUSTED,
    REASON_POLICY_BLOCK,
    REASON_PRIMARY_RESTRICTED_CPU_UNVERIFIED,
    AllProvidersFailedError,
    GenerateRequest,
    Provider,
    ProviderError,
    ProviderResponse,
)
from model_gateway.config import GatewayConfig, load_config
from model_gateway.health import CircuitBreaker
from model_gateway.metrics import MetricsRegistry, get_metrics
from model_gateway.policy import is_remote_allowed
from model_gateway.runtime_verify import STATUS_VERIFIED, VerificationResult, verify_ollama_cpu_mode

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
        verify_fn: Callable[..., VerificationResult] | None = None,
        metrics: MetricsRegistry | None = None,
    ) -> None:
        self._config = config or load_config()
        self._providers = providers or _build_providers(self._config)
        self._audit_logger = audit_logger or AuditLogger()
        self._circuit_breaker = circuit_breaker or CircuitBreaker(
            fail_threshold=self._config.circuit_breaker_fails,
            reset_seconds=self._config.circuit_breaker_reset_sec,
        )
        self._policy_check = policy_check or is_remote_allowed
        self._verify_fn = verify_fn or verify_ollama_cpu_mode
        self._verification_cache: VerificationResult | None = None
        self._verification_cached_at: float | None = None
        if metrics is not None:
            self._metrics = metrics
        elif self._config.metrics_enabled:
            self._metrics = get_metrics()
        else:
            self._metrics = MetricsRegistry(enabled=False)

    def _is_enabled(self, name: str) -> bool:
        c = self._config
        if name == "ollama":
            return c.ollama_enabled
        if name == "local_alt":
            return c.local_alt_enabled
        if name == "remote":
            return c.remote_enabled
        return False

    def _audit(
        self,
        *,
        task: str,
        event: str,
        trace_id: str,
        details: dict[str, Any],
    ) -> None:
        self._audit_logger.log(
            alias=None,
            task=task,
            status=event,
            risk_level="low",
            details={"trace_id": trace_id, **details},
        )

    def _skip(self, name: str, reason_code: str, detail: str, attempts: list, trace_id: str) -> None:
        logger.info("Saglayici atlandi: %s (%s) -- %s", name, reason_code, detail)
        self._metrics.inc_counter(
            "model_gateway_restrictions_total", {"provider": name, "reason_code": reason_code}
        )
        self._audit(
            task="MODEL_GATEWAY_GENERATE",
            event="SKIPPED",
            trace_id=trace_id,
            details={
                "provider": name,
                "reason_code": reason_code,
                "detail": detail,
                "provider_order": list(self._config.provider_order),
                "event_type": "ROUTER_FALLBACK_APPLIED",
            },
        )
        attempts.append((name, reason_code, detail))

    def _get_cpu_verification(self, trace_id: str) -> VerificationResult:
        """Kisa TTL'li onbellek -- her istekte yeniden calistirmaz.

        Onbellek isabetinde (cache hit) OLLAMA_CPU_PREFLIGHT_CHECKED
        event'i EKLENMEZ (gereksiz audit gurultusu -- yeni bir karar
        verilmedi, var olan sonuc yeniden kullanildi). Yalnizca gercek
        bir kontrol calistiginda audit'lenir.
        """
        now = time.monotonic()
        ttl = self._config.ollama_cpu_verify_cache_ttl_sec
        if (
            self._verification_cache is not None
            and self._verification_cached_at is not None
            and (now - self._verification_cached_at) < ttl
        ):
            return self._verification_cache

        ollama = self._providers.get("ollama")
        health = ollama.healthcheck() if ollama is not None else None
        self._metrics.set_gauge(
            "model_gateway_provider_health", {"provider": "ollama"}, 1.0 if health and health.healthy else 0.0
        )
        result = self._verify_fn(
            ollama_healthy=bool(health and health.healthy),
            base_url=self._config.ollama_host,
            marker_file=self._config.ollama_cpu_marker_file,
            methods=self._config.ollama_cpu_verify_methods,
            timeout_ms=self._config.ollama_cpu_verify_timeout_ms,
            metrics=self._metrics,
        )
        self._verification_cache = result
        self._verification_cached_at = now

        logger.info(
            "Ollama CPU-verify kontrolu calisti: status=%s reason_code=%s",
            result.status,
            result.reason_code,
        )
        self._audit(
            task="OLLAMA_CPU_PREFLIGHT_CHECKED",
            event=result.status,
            trace_id=trace_id,
            details={
                "verification_status": result.status,
                "verification_reason_code": result.reason_code,
                "evidence": result.evidence,
                "checked_at": result.checked_at,
                "strict_mode": self._config.ollama_cpu_verify_strict,
            },
        )
        return result

    def _apply_cpu_verify_gate(self, trace_id: str) -> tuple[bool, str, str]:
        """Ollama'yi denemeden once cagrilir. Donen: (allowed, reason_code, detail).

        `allowed=False` ise Ollama bu turda hic denenmez (STRICT modda
        dogrulanmamis). `allowed=True` ise ya VERIFIED'dir ya da
        STRICT=false altinda bilerek gecirilmistir (bu durumda ayrica
        uyari loglanir/audit'lenir).
        """
        if not self._config.ollama_cpu_verify_enabled:
            return True, "", ""

        result = self._get_cpu_verification(trace_id)
        if result.status == STATUS_VERIFIED:
            return True, "", ""

        if self._config.ollama_cpu_verify_strict:
            detail = (
                f"CPU-only mod dogrulanamadi (status={result.status}, "
                f"reason={result.reason_code}) -- STRICT modda Ollama birincil olarak secilmiyor"
            )
            self._audit(
                task="OLLAMA_PRIMARY_RESTRICTED",
                event="RESTRICTED",
                trace_id=trace_id,
                details={
                    "verification_status": result.status,
                    "verification_reason_code": result.reason_code,
                    "action_taken": "RESTRICT_PRIMARY",
                    "provider_order": list(self._config.provider_order),
                },
            )
            return False, REASON_PRIMARY_RESTRICTED_CPU_UNVERIFIED, detail

        logger.warning(
            "Ollama CPU-modu dogrulanamadi ama STRICT=false -- birincil yine de deneniyor "
            "(status=%s, reason=%s)",
            result.status,
            result.reason_code,
        )
        self._audit(
            task="OLLAMA_CPU_PREFLIGHT_CHECKED",
            event="WARN_ONLY",
            trace_id=trace_id,
            details={
                "verification_status": result.status,
                "verification_reason_code": result.reason_code,
                "decision": "CPU_MODE_UNVERIFIED_WARN_ONLY",
                "action_taken": "ALLOW_PRIMARY_WITH_WARNING",
            },
        )
        return True, "", ""

    def generate(self, request: GenerateRequest) -> ProviderResponse:
        trace_id = uuid.uuid4().hex
        attempts: list[tuple[str, str, str]] = []
        real_attempts = 0
        max_real_attempts = 1 + self._config.fallback_max_hops

        for name in self._config.provider_order:
            provider = self._providers.get(name)
            if provider is None:
                self._skip(name, REASON_DISABLED, f"bilinmeyen saglayici adi: {name}", attempts, trace_id)
                continue

            if not self._is_enabled(name):
                self._skip(name, REASON_DISABLED, "saglayici config'de kapali", attempts, trace_id)
                continue

            if name == "remote" and self._config.remote_policy_gate == "required":
                allowed, detail = self._policy_check()
                if not allowed:
                    self._skip(name, REASON_POLICY_BLOCK, f"REMOTE_BLOCKED_BY_POLICY: {detail}", attempts, trace_id)
                    continue

            if name == "ollama":
                allowed, reason_code, detail = self._apply_cpu_verify_gate(trace_id)
                if not allowed:
                    self._skip(name, reason_code, detail, attempts, trace_id)
                    continue

            circuit_open = self._circuit_breaker.is_open(name)
            self._metrics.set_gauge("model_gateway_circuit_open", {"provider": name}, 1.0 if circuit_open else 0.0)
            if circuit_open:
                self._skip(name, REASON_CIRCUIT_OPEN, "devre acik (art arda hata esigi asildi)", attempts, trace_id)
                continue

            if real_attempts >= max_real_attempts:
                logger.info("FALLBACK_MAX_HOPS asildi, %s denenmeyecek", name)
                break

            real_attempts += 1
            call_started_at = time.monotonic()
            try:
                response = provider.generate(request)
            except ProviderError as exc:
                latency_ms = (time.monotonic() - call_started_at) * 1000.0
                self._metrics.observe_histogram("model_gateway_generate_latency_ms", {"provider": name}, latency_ms)
                self._metrics.inc_counter("model_gateway_requests_total", {"provider": name, "result": "failure"})
                self._metrics.inc_counter(
                    "model_gateway_fallback_total",
                    {
                        "from_provider": name,
                        "to_provider": _next_provider_name(self._config.provider_order, name),
                        "reason_code": exc.reason_code,
                    },
                )
                self._circuit_breaker.record_failure(name)
                logger.warning("Saglayici basarisiz: %s (%s) -- %s", name, exc.reason_code, exc.detail)
                self._audit(
                    task="MODEL_GATEWAY_GENERATE",
                    event="FALLBACK",
                    trace_id=trace_id,
                    details={
                        "provider": name,
                        "reason_code": exc.reason_code,
                        "detail": exc.detail,
                        "provider_order": list(self._config.provider_order),
                        "event_type": "ROUTER_FALLBACK_APPLIED",
                    },
                )
                attempts.append((name, exc.reason_code, exc.detail))
                continue

            latency_ms = (time.monotonic() - call_started_at) * 1000.0
            self._metrics.observe_histogram("model_gateway_generate_latency_ms", {"provider": name}, latency_ms)
            self._metrics.inc_counter("model_gateway_requests_total", {"provider": name, "result": "success"})
            self._circuit_breaker.record_success(name)
            logger.info("Saglayici basarili: %s", name)
            self._audit(
                task="MODEL_GATEWAY_GENERATE",
                event="SUCCESS",
                trace_id=trace_id,
                details={
                    "provider": name,
                    "reason_code": "",
                    "detail": "",
                    "selected_provider": name,
                    "provider_order": list(self._config.provider_order),
                },
            )
            return response

        self._audit(
            task="MODEL_GATEWAY_GENERATE",
            event="EXHAUSTED",
            trace_id=trace_id,
            details={
                "reason_code": REASON_FALLBACK_EXHAUSTED,
                "attempts": [{"provider": p, "reason_code": r, "detail": d} for p, r, d in attempts],
                "provider_order": list(self._config.provider_order),
                "action_taken": "NULL_INTENT_FALLBACK",
            },
        )
        raise AllProvidersFailedError(attempts, trace_id=trace_id)

    def healthcheck_all(self) -> dict[str, Any]:
        results = {name: p.healthcheck() for name, p in self._providers.items()}
        for name, status in results.items():
            self._metrics.set_gauge("model_gateway_provider_health", {"provider": name}, 1.0 if status.healthy else 0.0)
        return results


def _next_provider_name(provider_order: tuple[str, ...], current: str) -> str:
    """`fallback_total`'in `to_provider` etiketi icin -- siradaki
    ADAY saglayici (henuz denenip denenmeyecegi bilinmez, yalnizca
    provider_order'daki bir sonraki giris)."""
    try:
        idx = provider_order.index(current)
    except ValueError:
        return "none"
    if idx + 1 < len(provider_order):
        return provider_order[idx + 1]
    return "none"


_default_router: ModelGatewayRouter | None = None


def get_router() -> ModelGatewayRouter:
    """Surec-genelinde tek bir router ornegi (lazy singleton)."""
    global _default_router
    if _default_router is None:
        _default_router = ModelGatewayRouter()
    return _default_router
