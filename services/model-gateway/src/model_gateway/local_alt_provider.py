"""Ikincil yerel saglayici -- LM Studio / llama.cpp server / vLLM.

Bu ucu de OpenAI-uyumlu `/v1/chat/completions` uc noktasini destekleyen
yerel calisma zamanlari (`LOCAL_ALT_TYPE`: lmstudio|llamacpp|vllm) icin
tek, ortak bir istemci -- ucunun tumu de bu API sekline yakinsadigi icin
(pragmatik varsayim, projede henuz hicbiri kurulu/test edilmedi).

Varsayilan olarak KAPALI (`LOCAL_ALT_ENABLED=false`) -- router bu
saglayiciyi yalnizca config'de acikca etkinlestirilmisse dener. Kapaliyken
hicbir HTTP cagrisi yapilmaz.
"""

from __future__ import annotations

import json
import logging
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

logger = logging.getLogger("model_gateway.local_alt_provider")

DEFAULT_TIMEOUT_SECONDS = 15.0


class LocalAltProvider:
    name = "local_alt"

    def __init__(
        self,
        host: str,
        model: str,
        backend_type: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._host = host.rstrip("/")
        self._model = model
        self._backend_type = backend_type
        self._timeout = timeout

    def healthcheck(self) -> HealthStatus:
        try:
            req = urllib.request.Request(f"{self._host}/v1/models", method="GET")
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                ok = 200 <= resp.status < 300
            return HealthStatus(healthy=ok, detail="" if ok else f"HTTP {resp.status}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return HealthStatus(healthy=False, detail=str(exc))

    def generate(self, request: GenerateRequest) -> ProviderResponse:
        health = self.healthcheck()
        if not health.healthy:
            raise ProviderError(
                REASON_PRIMARY_UNHEALTHY, f"{self._backend_type} erisilemez: {health.detail}"
            )

        payload = json.dumps(
            {
                "model": self._model,
                "messages": [{"role": "user", "content": request.prompt}],
                "stream": False,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self._host}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except TimeoutError as exc:
            raise ProviderError(REASON_TIMEOUT, f"{self._backend_type} zaman asimi: {exc}") from exc
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderError(
                REASON_RUNTIME_CRASH, f"{self._backend_type} generate hatasi: {exc}"
            ) from exc

        text = self._extract_text(body)
        return ProviderResponse(text=text, provider=self.name, model=self._model, raw=body)

    @staticmethod
    def _extract_text(body: dict[str, Any]) -> str:
        try:
            return str(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError):
            return ""

    def metadata(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "backend_type": self._backend_type,
            "host": self._host,
            "model": self._model,
        }
