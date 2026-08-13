"""Ucuncu (remote) saglayici -- yalnizca policy_gate izin verirse denenir.

Kapsam siniri (durustce belirtilmeli): bu istemci yalnizca OpenAI-uyumlu
`/v1/chat/completions` sekli konusur (`REMOTE_PROVIDER=openai` veya ayni
API sekline sahip bir servis icin). Anthropic gibi farkli istek/yanit
sekline sahip saglayicilar icin ayri bir adaptor gerekir -- bu ilk
surumde yok, bkz. docs/ops/MODEL_FALLBACK_RUNBOOK.md "Bilinen sinirlamalar".

Varsayilan olarak KAPALI (`REMOTE_ENABLED=false`) VE politika kapisina
tabi (`policy.is_remote_allowed()` -- bkz. policies/risk/tool_risk_policy.yaml
`remote_model_policy`). Router, bu iki kontrolden herhangi biri basarisiz
olursa bu saglayiciyi HIC cagirmaz -- burada tanimli generate()/healthcheck()
kodu yalnizca ikisi de gecildiginde calisir.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

from model_gateway.base import (
    REASON_PRIMARY_UNHEALTHY,
    REASON_RUNTIME_CRASH,
    REASON_TIMEOUT,
    GenerateRequest,
    HealthStatus,
    ProviderError,
    ProviderResponse,
)

logger = logging.getLogger("model_gateway.remote_provider")

DEFAULT_API_BASE = "https://api.openai.com"
API_KEY_ENV_VAR = "REMOTE_API_KEY"


class RemoteProvider:
    name = "remote"

    def __init__(
        self,
        provider_id: str,
        model: str,
        timeout_ms: int,
        api_base: str = DEFAULT_API_BASE,
    ) -> None:
        self._provider_id = provider_id
        self._model = model
        self._timeout = max(timeout_ms, 1) / 1000.0
        self._api_base = api_base.rstrip("/")

    def _api_key(self) -> str | None:
        return os.getenv(API_KEY_ENV_VAR)

    def healthcheck(self) -> HealthStatus:
        api_key = self._api_key()
        if not api_key:
            return HealthStatus(healthy=False, detail=f"{API_KEY_ENV_VAR} tanimli degil")
        return HealthStatus(healthy=True, detail="API key mevcut (canli baglanti test edilmedi)")

    def generate(self, request: GenerateRequest) -> ProviderResponse:
        api_key = self._api_key()
        if not api_key:
            raise ProviderError(
                REASON_PRIMARY_UNHEALTHY, f"{API_KEY_ENV_VAR} tanimli degil, remote cagrilamaz"
            )

        payload = json.dumps(
            {
                "model": self._model,
                "messages": [{"role": "user", "content": request.prompt}],
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self._api_base}/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except TimeoutError as exc:
            raise ProviderError(REASON_TIMEOUT, f"Remote zaman asimi: {exc}") from exc
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderError(REASON_RUNTIME_CRASH, f"Remote cagri hatasi: {exc}") from exc

        text = self._extract_text(body)
        return ProviderResponse(text=text, provider=self.name, model=self._model, raw=body)

    @staticmethod
    def _extract_text(body: dict[str, Any]) -> str:
        try:
            return str(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError):
            return ""

    def metadata(self) -> dict[str, Any]:
        return {"provider": self.name, "provider_id": self._provider_id, "model": self._model}
