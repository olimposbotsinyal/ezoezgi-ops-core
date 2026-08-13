"""Geriye-donuk uyumluluk katmani -- ollama_nlu.py icin.

`ollama_nlu.classify()`, `model_client.OllamaModelClient` ile ayni
`.generate(prompt) -> {"response": str, "model": str, "fallback": bool}`
sozlesmesine sahip bir istemci bekliyor (bkz. test_ollama_nlu.py'deki
`_FakeClient`). Bu sozlesmeyi DEGISTIRMEDEN, gercek uretim yolunu
(varsayilan client=None durumu) yeni router'a baglamak icin bu ince
adaptor var -- `classify(..., client=...)` ile enjekte edilen test
sahteleri bu siniftan tamamen habersiz kalir, hicbir mevcut test kirilmaz.
"""

from __future__ import annotations

import logging
from typing import Any

from model_gateway.base import AllProvidersFailedError, GenerateRequest
from model_gateway.router import ModelGatewayRouter, get_router

logger = logging.getLogger("model_gateway.compat")


class RouterBackedClient:
    """`OllamaModelClient` ile ayni `.generate(prompt)` sozlesmesini
    router uzerinden saglayan adaptor."""

    def __init__(self, router: ModelGatewayRouter | None = None) -> None:
        self._router = router or get_router()

    def generate(self, prompt: str) -> dict[str, Any]:
        try:
            response = self._router.generate(GenerateRequest(prompt=prompt))
        except AllProvidersFailedError as exc:
            logger.warning("Tum saglayicilar basarisiz, fallback=True donuluyor: %s", exc)
            return {"response": "", "model": "none", "fallback": True}

        return {"response": response.text, "model": response.model, "fallback": False}
