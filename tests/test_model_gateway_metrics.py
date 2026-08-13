"""Model gateway metrikleri testleri.

Router uzerinden gercek metrik artislarini dogrular -- gercek ag/dosya
I/O yapilmaz, hepsi sahte saglayicilar ve izole bir MetricsRegistry ile.
"""

from __future__ import annotations

from model_gateway.base import REASON_FALLBACK_EXHAUSTED, REASON_PRIMARY_UNHEALTHY, GenerateRequest, HealthStatus, ProviderError, ProviderResponse
from model_gateway.compat import RouterBackedClient
from model_gateway.config import GatewayConfig
from model_gateway.health import CircuitBreaker
from model_gateway.metrics import MetricsRegistry
from model_gateway.router import ModelGatewayRouter


class _FakeProvider:
    def __init__(self, name: str, *, succeeds: bool = True, reason_code: str = REASON_PRIMARY_UNHEALTHY):
        self.name = name
        self._succeeds = succeeds
        self._reason_code = reason_code

    def generate(self, request: GenerateRequest) -> ProviderResponse:
        if not self._succeeds:
            raise ProviderError(self._reason_code, "kasitli basarisizlik")
        return ProviderResponse(text="ok", provider=self.name, model="fake-model")

    def healthcheck(self) -> HealthStatus:
        return HealthStatus(healthy=self._succeeds)

    def metadata(self) -> dict:
        return {"provider": self.name}


class _FakeAuditLogger:
    def log(self, *, alias, task, status, risk_level="low", details=None, request_id=None):
        return {}


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


def _find_counter(snapshot: dict, name: str, **labels) -> float | None:
    for c in snapshot["counters"]:
        if c["name"] == name and all(c["labels"].get(k) == v for k, v in labels.items()):
            return c["value"]
    return None


def test_metrics_increment_on_primary_success():
    ollama = _FakeProvider("ollama", succeeds=True)
    metrics = MetricsRegistry(enabled=True)
    config = _make_config(provider_order=("ollama",))
    router = ModelGatewayRouter(
        config=config,
        providers={"ollama": ollama},
        audit_logger=_FakeAuditLogger(),
        circuit_breaker=CircuitBreaker(fail_threshold=3, reset_seconds=120),
        metrics=metrics,
    )

    router.generate(GenerateRequest(prompt="merhaba"))

    snap = metrics.snapshot()
    assert _find_counter(snap, "model_gateway_requests_total", provider="ollama", result="success") == 1.0
    latency_hist = [h for h in snap["histograms"] if h["name"] == "model_gateway_generate_latency_ms"]
    assert len(latency_hist) == 1
    assert latency_hist[0]["count"] == 1


def test_metrics_increment_on_fallback_path():
    ollama = _FakeProvider("ollama", succeeds=False, reason_code=REASON_PRIMARY_UNHEALTHY)
    local_alt = _FakeProvider("local_alt", succeeds=True)
    metrics = MetricsRegistry(enabled=True)
    config = _make_config(provider_order=("ollama", "local_alt"), local_alt_enabled=True)
    router = ModelGatewayRouter(
        config=config,
        providers={"ollama": ollama, "local_alt": local_alt},
        audit_logger=_FakeAuditLogger(),
        circuit_breaker=CircuitBreaker(fail_threshold=3, reset_seconds=120),
        metrics=metrics,
    )

    router.generate(GenerateRequest(prompt="test"))

    snap = metrics.snapshot()
    assert _find_counter(snap, "model_gateway_requests_total", provider="ollama", result="failure") == 1.0
    assert _find_counter(snap, "model_gateway_requests_total", provider="local_alt", result="success") == 1.0
    assert (
        _find_counter(
            snap,
            "model_gateway_fallback_total",
            from_provider="ollama",
            to_provider="local_alt",
            reason_code=REASON_PRIMARY_UNHEALTHY,
        )
        == 1.0
    )


def test_metrics_increment_on_restriction_path():
    """Tum saglayicilar (disabled dahil) atlanip tukendiginde:
    restrictions_total + (compat uzerinden) null_intent_total artmali."""
    metrics = MetricsRegistry(enabled=True)
    config = _make_config(provider_order=("ollama",), ollama_enabled=False)  # kasitli: hemen atlanacak
    router = ModelGatewayRouter(
        config=config,
        providers={"ollama": _FakeProvider("ollama")},
        audit_logger=_FakeAuditLogger(),
        circuit_breaker=CircuitBreaker(fail_threshold=3, reset_seconds=120),
        metrics=metrics,
    )
    client = RouterBackedClient(router=router, metrics=metrics)

    result = client.generate("test")

    assert result == {"response": "", "model": "none", "fallback": True}
    snap = metrics.snapshot()
    assert _find_counter(snap, "model_gateway_restrictions_total", provider="ollama") is not None
    assert (
        _find_counter(snap, "model_gateway_null_intent_total", terminal_reason=REASON_FALLBACK_EXHAUSTED) == 1.0
    )
