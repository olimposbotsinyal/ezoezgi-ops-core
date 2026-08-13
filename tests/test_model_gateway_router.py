"""Model gateway router testleri (B036 resilience layer).

Gercek Ollama/local_alt/remote servisi gerektirmez -- sahte (fake)
saglayicilar ve sahte bir audit logger enjekte edilir. Gercek
data/audit/audit.log.jsonl dosyasina asla yazilmaz.
"""

from __future__ import annotations

import pytest

from model_gateway.base import (
    REASON_POLICY_BLOCK,
    REASON_PRIMARY_UNHEALTHY,
    AllProvidersFailedError,
    GenerateRequest,
    HealthStatus,
    ProviderError,
    ProviderResponse,
)
from model_gateway.config import GatewayConfig
from model_gateway.health import CircuitBreaker
from model_gateway.router import ModelGatewayRouter


class _FakeProvider:
    """Sabit bir sonuc donduren veya her zaman basarisiz olan sahte saglayici."""

    def __init__(self, name: str, *, succeeds: bool = True, reason_code: str = REASON_PRIMARY_UNHEALTHY):
        self.name = name
        self._succeeds = succeeds
        self._reason_code = reason_code
        self.calls = 0

    def generate(self, request: GenerateRequest) -> ProviderResponse:
        self.calls += 1
        if not self._succeeds:
            raise ProviderError(self._reason_code, f"{self.name} kasitli olarak basarisiz")
        return ProviderResponse(text=f"[{self.name}] {request.prompt}", provider=self.name, model="fake-model")

    def healthcheck(self) -> HealthStatus:
        return HealthStatus(healthy=self._succeeds)

    def metadata(self) -> dict:
        return {"provider": self.name}


class _FakeAuditLogger:
    """AuditLogger ile ayni `.log()` imzasina sahip, dosyaya yazmayan casus."""

    def __init__(self) -> None:
        self.records: list[dict] = []

    def log(self, *, alias, task, status, risk_level="low", details=None, request_id=None):
        record = {
            "alias": alias,
            "task": task,
            "status": status,
            "risk_level": risk_level,
            "details": details or {},
        }
        self.records.append(record)
        return record


def _make_config(**overrides) -> GatewayConfig:
    base = dict(
        provider_order=("ollama",),
        ollama_enabled=True,
        ollama_host="http://localhost:11434",
        ollama_model="llama3",
        local_alt_enabled=False,
        local_alt_type="llamacpp",
        local_alt_host="http://localhost:8080",
        local_alt_model="",
        remote_enabled=False,
        remote_provider="",
        remote_model="",
        remote_timeout_ms=10000,
        remote_policy_gate="required",
        fallback_max_hops=2,
        circuit_breaker_fails=3,
        circuit_breaker_reset_sec=120,
    )
    base.update(overrides)
    return GatewayConfig(**base)


def _make_router(config: GatewayConfig, providers: dict, audit_logger: _FakeAuditLogger, policy_check=None) -> ModelGatewayRouter:
    return ModelGatewayRouter(
        config=config,
        providers=providers,
        audit_logger=audit_logger,
        circuit_breaker=CircuitBreaker(
            fail_threshold=config.circuit_breaker_fails,
            reset_seconds=config.circuit_breaker_reset_sec,
        ),
        policy_check=policy_check,
    )


def test_router_primary_ok():
    """Birincil (ollama) saglayici basariliysa dogrudan onun sonucu donmeli,
    hicbir fallback denemesi olmamali."""
    ollama = _FakeProvider("ollama", succeeds=True)
    audit = _FakeAuditLogger()
    config = _make_config(provider_order=("ollama",))
    router = _make_router(config, {"ollama": ollama}, audit)

    response = router.generate(GenerateRequest(prompt="merhaba"))

    assert response.provider == "ollama"
    assert response.text == "[ollama] merhaba"
    assert ollama.calls == 1
    # Yalnizca SUCCESS event'i loglanmali, hicbir FALLBACK/SKIPPED yok.
    statuses = [r["status"] for r in audit.records]
    assert statuses == ["SUCCESS"]


def test_router_fallback_to_secondary():
    """Birincil basarisiz olursa ikincile gecmeli; gecis acikca loglanmali."""
    ollama = _FakeProvider("ollama", succeeds=False, reason_code=REASON_PRIMARY_UNHEALTHY)
    local_alt = _FakeProvider("local_alt", succeeds=True)
    audit = _FakeAuditLogger()
    config = _make_config(
        provider_order=("ollama", "local_alt"),
        local_alt_enabled=True,
        fallback_max_hops=2,
    )
    router = _make_router(config, {"ollama": ollama, "local_alt": local_alt}, audit)

    response = router.generate(GenerateRequest(prompt="test"))

    assert response.provider == "local_alt"
    assert ollama.calls == 1
    assert local_alt.calls == 1

    statuses = [r["status"] for r in audit.records]
    assert statuses == ["FALLBACK", "SUCCESS"]
    assert audit.records[0]["details"]["reason_code"] == REASON_PRIMARY_UNHEALTHY
    assert audit.records[0]["details"]["provider"] == "ollama"


def test_router_remote_blocked_by_policy():
    """Politika izin vermiyorsa remote HIC cagrilmamali, POLICY_BLOCK ile atlanmali."""
    ollama = _FakeProvider("ollama", succeeds=False, reason_code=REASON_PRIMARY_UNHEALTHY)
    remote = _FakeProvider("remote", succeeds=True)
    audit = _FakeAuditLogger()
    config = _make_config(
        provider_order=("ollama", "remote"),
        remote_enabled=True,
        remote_policy_gate="required",
        fallback_max_hops=2,
    )
    router = _make_router(
        config,
        {"ollama": ollama, "remote": remote},
        audit,
        policy_check=lambda: (False, "test policy: remote yasak"),
    )

    with pytest.raises(AllProvidersFailedError) as exc_info:
        router.generate(GenerateRequest(prompt="test"))

    # remote'un generate() metodu HIC cagrilmamis olmali.
    assert remote.calls == 0
    reason_codes = [reason for _, reason, _ in exc_info.value.attempts]
    assert REASON_POLICY_BLOCK in reason_codes

    policy_block_record = next(r for r in audit.records if r["details"]["reason_code"] == REASON_POLICY_BLOCK)
    assert "REMOTE_BLOCKED_BY_POLICY" in policy_block_record["details"]["detail"]


def test_audit_event_on_fallback():
    """Her fallback gecisi icin audit logger'a provider+reason_code+detail
    iceren yapisal bir event eklenmeli."""
    ollama = _FakeProvider("ollama", succeeds=False, reason_code=REASON_PRIMARY_UNHEALTHY)
    local_alt = _FakeProvider("local_alt", succeeds=True)
    audit = _FakeAuditLogger()
    config = _make_config(
        provider_order=("ollama", "local_alt"),
        local_alt_enabled=True,
    )
    router = _make_router(config, {"ollama": ollama, "local_alt": local_alt}, audit)

    router.generate(GenerateRequest(prompt="test"))

    fallback_events = [r for r in audit.records if r["status"] == "FALLBACK"]
    assert len(fallback_events) == 1
    details = fallback_events[0]["details"]
    assert details["provider"] == "ollama"
    assert details["reason_code"] == REASON_PRIMARY_UNHEALTHY
    assert details["detail"]  # bos olmayan, aciklayici bir mesaj olmali
    assert fallback_events[0]["task"] == "MODEL_GATEWAY_GENERATE"
