"""Model gateway router testleri (B036 resilience layer).

Gercek Ollama/local_alt/remote servisi gerektirmez -- sahte (fake)
saglayicilar ve sahte bir audit logger enjekte edilir. Gercek
data/audit/audit.log.jsonl dosyasina asla yazilmaz.
"""

from __future__ import annotations

import pytest

from model_gateway.base import (
    REASON_POLICY_BLOCK,
    REASON_PRIMARY_RESTRICTED_CPU_UNVERIFIED,
    REASON_PRIMARY_UNHEALTHY,
    AllProvidersFailedError,
    GenerateRequest,
    HealthStatus,
    ProviderError,
    ProviderResponse,
)
from model_gateway.compat import RouterBackedClient
from model_gateway.runtime_verify import (
    STATUS_UNKNOWN,
    STATUS_VERIFIED,
    REASON_CPU_MODE_VERIFIED,
    REASON_SIGNAL_NOT_AVAILABLE,
    VerificationResult,
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
        # CPU-verify: mevcut (bu dosyadaki ilk 4) testler bu ozellikten
        # habersiz -- test-yerel varsayilan bilerek KAPALI, boylece
        # fallback/policy/audit testleri yeni preflight event'inden
        # etkilenmeden odaklanmis kalir. Yeni CPU-verify testleri bunu
        # acikca True'ya cevirir.
        ollama_cpu_verify_enabled=False,
        ollama_cpu_verify_strict=True,
        ollama_cpu_verify_timeout_ms=1200,
        ollama_cpu_verify_methods=("marker",),
        ollama_cpu_marker_file="./runtime/ollama_cpu_mode.ok",
        ollama_on_unverified="RESTRICT_PRIMARY",
        startup_preflight_required=True,
        ollama_cpu_verify_cache_ttl_sec=60,
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


# --- CPU-only calisma zamani dogrulama kapisi (runtime_verify.py) ----------


def _make_verify_fn(status: str, reason_code: str, evidence: dict | None = None):
    def _fn(**kwargs):
        return VerificationResult(status=status, reason_code=reason_code, evidence=evidence or {}, checked_at="2026-01-01T00:00:00+00:00")

    return _fn


def test_preflight_verified_allows_primary():
    """Dogrulama VERIFIED donerse Ollama normal sekilde birincil olarak
    denenmeli -- hicbir kisitlama uygulanmamali."""
    ollama = _FakeProvider("ollama", succeeds=True)
    audit = _FakeAuditLogger()
    config = _make_config(
        provider_order=("ollama",),
        ollama_cpu_verify_enabled=True,
        ollama_cpu_verify_strict=True,
    )
    router = _make_router(config, {"ollama": ollama}, audit)
    router._verify_fn = _make_verify_fn(STATUS_VERIFIED, REASON_CPU_MODE_VERIFIED)

    response = router.generate(GenerateRequest(prompt="merhaba"))

    assert response.provider == "ollama"
    assert ollama.calls == 1
    restricted_events = [r for r in audit.records if r["task"] == "OLLAMA_PRIMARY_RESTRICTED"]
    assert restricted_events == []


def test_preflight_unverified_restricts_primary_strict():
    """STRICT=true iken dogrulama VERIFIED DONMEZSE, Ollama hic denenmemeli
    -- PRIMARY_RESTRICTED_CPU_UNVERIFIED ile atlanip sonraki saglayiciya
    (varsa) gecilmeli."""
    ollama = _FakeProvider("ollama", succeeds=True)  # basarili olsa BILE denenmemeli
    local_alt = _FakeProvider("local_alt", succeeds=True)
    audit = _FakeAuditLogger()
    config = _make_config(
        provider_order=("ollama", "local_alt"),
        local_alt_enabled=True,
        ollama_cpu_verify_enabled=True,
        ollama_cpu_verify_strict=True,
    )
    router = _make_router(config, {"ollama": ollama, "local_alt": local_alt}, audit)
    router._verify_fn = _make_verify_fn(STATUS_UNKNOWN, REASON_SIGNAL_NOT_AVAILABLE)

    response = router.generate(GenerateRequest(prompt="test"))

    assert ollama.calls == 0  # HIC cagrilmamali
    assert response.provider == "local_alt"

    skipped = [r for r in audit.records if r["status"] == "SKIPPED" and r["details"]["provider"] == "ollama"]
    assert len(skipped) == 1
    assert skipped[0]["details"]["reason_code"] == REASON_PRIMARY_RESTRICTED_CPU_UNVERIFIED


def test_preflight_unverified_warn_only_allows_primary():
    """STRICT=false iken dogrulama VERIFIED donmese bile Ollama yine de
    denenmeli -- yalnizca uyari loglanip audit'lenmeli."""
    ollama = _FakeProvider("ollama", succeeds=True)
    audit = _FakeAuditLogger()
    config = _make_config(
        provider_order=("ollama",),
        ollama_cpu_verify_enabled=True,
        ollama_cpu_verify_strict=False,
    )
    router = _make_router(config, {"ollama": ollama}, audit)
    router._verify_fn = _make_verify_fn(STATUS_UNKNOWN, REASON_SIGNAL_NOT_AVAILABLE)

    response = router.generate(GenerateRequest(prompt="test"))

    assert ollama.calls == 1
    assert response.provider == "ollama"

    warn_events = [r for r in audit.records if r["details"].get("decision") == "CPU_MODE_UNVERIFIED_WARN_ONLY"]
    assert len(warn_events) == 1


def test_router_emits_audit_on_restriction():
    """PRIMARY_RESTRICTED_CPU_UNVERIFIED durumunda hem OLLAMA_CPU_PREFLIGHT_CHECKED
    hem de OLLAMA_PRIMARY_RESTRICTED audit event'leri, gerekli alanlarla
    (verification status/reason_code, action_taken) yazilmali."""
    ollama = _FakeProvider("ollama", succeeds=True)
    audit = _FakeAuditLogger()
    config = _make_config(
        provider_order=("ollama",),
        ollama_cpu_verify_enabled=True,
        ollama_cpu_verify_strict=True,
    )
    router = _make_router(config, {"ollama": ollama}, audit)
    router._verify_fn = _make_verify_fn(STATUS_UNKNOWN, REASON_SIGNAL_NOT_AVAILABLE, evidence={"marker": {"present": False}})

    with pytest.raises(AllProvidersFailedError):
        router.generate(GenerateRequest(prompt="test"))

    preflight_events = [r for r in audit.records if r["task"] == "OLLAMA_CPU_PREFLIGHT_CHECKED"]
    assert len(preflight_events) == 1
    assert preflight_events[0]["details"]["verification_status"] == STATUS_UNKNOWN
    assert preflight_events[0]["details"]["verification_reason_code"] == REASON_SIGNAL_NOT_AVAILABLE
    assert preflight_events[0]["details"]["evidence"] == {"marker": {"present": False}}

    restricted_events = [r for r in audit.records if r["task"] == "OLLAMA_PRIMARY_RESTRICTED"]
    assert len(restricted_events) == 1
    assert restricted_events[0]["details"]["action_taken"] == "RESTRICT_PRIMARY"
    assert restricted_events[0]["details"]["verification_status"] == STATUS_UNKNOWN

    # Her iki event de ayni trace_id'yi tasimali -- tek bir generate() cagrisinin
    # tam karar izini korelasyonlamak icin.
    assert preflight_events[0]["details"]["trace_id"] == restricted_events[0]["details"]["trace_id"]


def test_no_provider_preserves_null_intent_contract():
    """Tum saglayicilar tukendiginde (AllProvidersFailedError), RouterBackedClient
    -- ollama_nlu.classify()'in bekledigi TAM ONCEKI sozlesmeyi
    ({"response": "", "model": "none", "fallback": True}) korumali."""

    class _AlwaysFailingRouter:
        def generate(self, request):
            raise AllProvidersFailedError([("ollama", REASON_PRIMARY_UNHEALTHY, "test")])

    client = RouterBackedClient(router=_AlwaysFailingRouter())

    result = client.generate("herhangi bir prompt")

    assert result == {"response": "", "model": "none", "fallback": True}
