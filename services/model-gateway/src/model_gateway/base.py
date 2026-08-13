"""Model gateway -- saglayici arayuzu ve ortak veri tipleri.

B036 resilience layer: Ollama Windows/Vulkan calisma zamani kararsizligina
(bkz. reports/runtime_incident_20260813T004855Z/) karsi, sirali fallback +
acik audit izli, cok-saglayicili bir model erisim katmani. Bu modul yalnizca
tipleri/sozlesmeyi tanimlar -- gercek HTTP cagrilari provider dosyalarinda.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class GenerateRequest:
    """Bir saglayicidan metin uretimi istemek icin standart istek sekli."""

    prompt: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderResponse:
    """Basarili bir generate() cagrisinin standart sonucu."""

    text: str
    provider: str
    model: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class HealthStatus:
    healthy: bool
    detail: str = ""


# --- Fallback/hata neden kodlari --------------------------------------------
# Router ve audit log tarafindan kullanilir; her fallback gecisinde bu
# kodlardan biri loglanir -- "sessiz fallback yok" kuralinin temeli.

REASON_PRIMARY_UNHEALTHY = "PRIMARY_UNHEALTHY"
REASON_TIMEOUT = "TIMEOUT"
REASON_RUNTIME_CRASH = "RUNTIME_CRASH"
REASON_POLICY_BLOCK = "POLICY_BLOCK"
REASON_DISABLED = "DISABLED"
REASON_CIRCUIT_OPEN = "CIRCUIT_OPEN"

VALID_REASON_CODES = (
    REASON_PRIMARY_UNHEALTHY,
    REASON_TIMEOUT,
    REASON_RUNTIME_CRASH,
    REASON_POLICY_BLOCK,
    REASON_DISABLED,
    REASON_CIRCUIT_OPEN,
)


class ProviderError(Exception):
    """Bir saglayicinin generate()/healthcheck() cagrisi basarisiz oldugunda.

    `reason_code`, VALID_REASON_CODES icinden biri olmali -- router bu kodu
    hem log satirina hem audit event'ine tasir.
    """

    def __init__(self, reason_code: str, detail: str = "") -> None:
        assert reason_code in VALID_REASON_CODES, f"gecersiz reason_code: {reason_code}"
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


class AllProvidersFailedError(Exception):
    """Sirali fallback zincirindeki TUM saglayicilar basarisiz oldugunda
    (veya FALLBACK_MAX_HOPS asildiginda) router tarafindan firlatilir.

    `attempts`, denenen her adimin (provider, reason_code, detail) kaydini
    tutar -- caller (ornegin ollama_nlu.classify()) bunu loglayabilir.
    """

    def __init__(self, attempts: list[tuple[str, str, str]]) -> None:
        self.attempts = attempts
        summary = "; ".join(f"{p}:{r}" for p, r, _ in attempts)
        super().__init__(f"Tum saglayicilar basarisiz oldu: {summary}")


class Provider(Protocol):
    """Her saglayicinin uygulamasi gereken minimal sozlesme."""

    name: str

    def generate(self, request: GenerateRequest) -> ProviderResponse: ...

    def healthcheck(self) -> HealthStatus: ...

    def metadata(self) -> dict[str, Any]: ...
