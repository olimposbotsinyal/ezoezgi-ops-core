"""Ollama saglayicisi -- model_client.OllamaModelClient'i sarar.

model_client.py'ye HICBIR degisiklik yapilmadi (mevcut 12 testi ve
sozlesmesi korunuyor) -- bu modul yalnizca onun uzerine, router'in
anlayabilecegi acik hata siniflandirmasini ekliyor.

Hata siniflandirmasi (B036 triage bulgularina dayanir, bkz.
reports/runtime_incident_20260813T004855Z/gpu_isolation_matrix.md):
  - health_check() basarisiz -> REASON_PRIMARY_UNHEALTHY (servis tumden
    erisilemez -- /api/tags bile yanit vermiyor)
  - health_check() basarili AMA generate() yine de fallback=True donduruyor
    -> REASON_RUNTIME_CRASH (bilinen desen: /api/tags hafif oldugu icin
    Vulkan cokusunu tetiklemez, ama /api/generate llama-server'i baslatir
    ve cokertir -- health check yanilmaz gorunur)

Onemli sinirlama (dogru soylenmeli): OLLAMA_VULKAN=false bu surecin kendi
ortam degiskenine yazilir. `ollama serve` bu Python kodu tarafindan
BASLATILMIYOR (harici, ayri bir sistem sureci) -- bu yuzden bu deger,
zaten calisan bir Ollama sunucusunu ETKILEMEZ. Gercek zorlama, sunucunun
`OLLAMA_VULKAN=false` ile manuel/harici olarak baslatilmasiyla saglanir
(bkz. docs/ops/OLLAMA_WORKAROUND_CPU_ONLY.md). Bu sinirlama
docs/ops/MODEL_FALLBACK_RUNBOOK.md'de acikca belgelendi.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from model_client import OllamaModelClient

from model_gateway.base import (
    REASON_PRIMARY_UNHEALTHY,
    REASON_RUNTIME_CRASH,
    GenerateRequest,
    HealthStatus,
    ProviderError,
    ProviderResponse,
)

logger = logging.getLogger("model_gateway.ollama_provider")

# B036 kok neden bulgusuna gore Windows Event Viewer / HTTP 500 govdesinde
# gorulen tam imza (bkz. event_viewer_crash_entries.txt, matrix_A_result.txt).
CRASH_SIGNATURE_MARKERS = ("0xc0000005", "process has terminated")


class OllamaProvider:
    name = "ollama"

    def __init__(self, host: str, model: str, client: OllamaModelClient | None = None) -> None:
        self._host = host
        self._model = model
        # OLLAMA_VULKAN=false: bu surecin KENDI ortamina yaziliyor -- bkz.
        # modul docstring'indeki sinirlama notu. Zaten calisan harici bir
        # `ollama serve` surecini etkilemez.
        os.environ.setdefault("OLLAMA_VULKAN", "false")
        self._client = client or OllamaModelClient(base_url=host, model=model)

    def healthcheck(self) -> HealthStatus:
        ok = self._client.health_check()
        return HealthStatus(healthy=ok, detail="" if ok else "Ollama /api/tags erisilemedi")

    def generate(self, request: GenerateRequest) -> ProviderResponse:
        if not self._client.health_check():
            raise ProviderError(REASON_PRIMARY_UNHEALTHY, "Ollama /api/tags erisilemedi")

        result = self._client.generate(request.prompt)
        if result.get("fallback"):
            # health_check basariliydi ama generate() yine de basarisiz --
            # bilinen B036 deseni (Vulkan cokusu /api/generate sirasinda
            # olusuyor, /api/tags'i etkilemiyor).
            raise ProviderError(
                REASON_RUNTIME_CRASH,
                "Ollama /api/tags saglikliydi ama /api/generate basarisiz oldu "
                "(bilinen B036 deseni -- muhtemelen 0xc0000005 Vulkan cokusu)",
            )

        return ProviderResponse(
            text=result.get("response", ""),
            provider=self.name,
            model=result.get("model", self._model),
            raw=result,
        )

    def metadata(self) -> dict[str, Any]:
        return {"provider": self.name, "host": self._host, "model": self._model}
