"""Ollama baglanti noktasi -- graceful fail (PLAN.md T8).

Bu asamada gercek Ollama servisine zorunlu bir bagimlilik yok: servis
ayakta degilse (henuz kurulmadi, kapali, ag yok vb.) istisna firlatmak
yerine mock fallback + warning log ile devam edilir (ADR-001, DECISIONS.md).

Bagimlilik eklememek icin stdlib `urllib` kullanilir; gercek entegrasyon
asamasinda (Faz 1+) ihtiyaca gore bir HTTP kutuphanesine gecilebilir.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("model_client")

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3"
# 2026-08-13: 2.0s -> 30.0s. Canli B031 eval kosusunda (bkz.
# reports/nlu_eval_20260813.md) yerel CPU inference gecikmesi (~6s p95)
# eski 2.0s timeout'u asiyordu -- her istek erken zaman asimina ugrayip
# mock fallback'e duşuyordu (fallback_rate=%100). 30s, gozlemlenen ~6s'nin
# uzerinde guvenli bir pay birakir.
DEFAULT_TIMEOUT_SECONDS = 30.0


def _resolve_timeout(explicit_timeout: float | None) -> float:
    """Timeout degerini oncelik sirasina gore cozer: explicit arg > env > varsayilan."""
    if explicit_timeout is not None:
        return explicit_timeout

    raw = os.getenv("OLLAMA_TIMEOUT_SECONDS")
    if raw is None:
        return DEFAULT_TIMEOUT_SECONDS

    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "Geçersiz OLLAMA_TIMEOUT_SECONDS=%r, varsayılana (%s) dönülüyor.",
            raw,
            DEFAULT_TIMEOUT_SECONDS,
        )
        return DEFAULT_TIMEOUT_SECONDS


class OllamaModelClient:
    """Ollama /api/generate icin ince istemci -- her cagri graceful fail eder."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = (
            base_url or os.getenv("OLLAMA_BASE_URL", DEFAULT_BASE_URL)
        ).rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL", DEFAULT_MODEL)
        self.timeout = _resolve_timeout(timeout)

    def health_check(self) -> bool:
        """Ollama servisi ayakta mi? Hicbir zaman istisna firlatmaz."""
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.warning("Ollama health check basarisiz (%s): %s", self.base_url, exc)
            return False

    def generate(self, prompt: str) -> dict[str, Any]:
        """Ollama'dan yanit ister; basarisiz olursa mock fallback doner.

        Donen sozlukte `fallback: True` ise gercek modele ulasilamadigi ve
        `response` alaninin mock icerik oldugu anlasilir.
        """
        if not self.health_check():
            logger.warning(
                "Ollama servisi (%s) erisilemez durumda, mock fallback kullaniliyor.",
                self.base_url,
            )
            return self._mock_fallback(prompt)

        payload = json.dumps(
            {"model": self.model, "prompt": prompt, "stream": False}
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return {
                "response": body.get("response", ""),
                "model": self.model,
                "fallback": False,
            }
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Ollama generate cagrisi basarisiz, mock fallback kullaniliyor: %s", exc)
            return self._mock_fallback(prompt)

    def _mock_fallback(self, prompt: str) -> dict[str, Any]:
        return {
            "response": f"[mock] {prompt}",
            "model": self.model,
            "fallback": True,
        }
