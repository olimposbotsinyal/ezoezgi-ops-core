"""Ollama tabanli NLU adaptoru (Faz 1, B031).

`bridge.py`'deki mock keyword siniflandiricinin yerini gercek bir model
cagrisiyla almasi icin kullanilir. Bu modul, `bridge.py`'nin mevcut
`(task_en, confidence)` sozlesmesini BILMEZ -- kendi basina canonical bir
NLU semasi uretir:

    {"intent": str, "entities": dict, "confidence": float (0..1), "raw": str}

`bridge.py` (composition point), bu canonical ciktiyi kendi arayuzune
(`detected_alias`/`task_en`/`original_tr`/`confidence`) esler -- bkz.
`bridge.py::_classify`. Bu ayrim, adaptorun orchestrator/bridge
sozlesmesinden bagimsiz test edilebilmesini saglar.

Guvenlik/dayaniklilik ilkeleri:
  - `classify()` HICBIR ZAMAN exception firlatmaz -- her hata durumu
    (servis erisilemez, timeout, bozuk/gecersiz model ciktisi) guvenli bir
    null-intent sonucuna ("UNKNOWN", confidence=0.0) duser.
  - Zorunlu timeout, `OllamaModelClient` uzerinden zaten uygulaniyor
    (varsayilan 2.0s, `OLLAMA_BASE_URL`/`OLLAMA_MODEL` ile birlikte).
  - Sinirli yeniden deneme: yalnizca "model cevap verdi ama ayristirilamadi"
    durumunda (gecici formatlama hatasi ihtimaline karsi) `max_attempts`
    kadar tekrar denenir; servis tamamen erisilemezse (fallback=True)
    tekrar denemenin faydasi olmadigindan hemen null-intent'e duser.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from model_client import OllamaModelClient

logger = logging.getLogger("ollama_nlu")

UNKNOWN_INTENT = "UNKNOWN"
DEFAULT_MAX_ATTEMPTS = 2

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _build_prompt(input_tr: str, known_intents: list[str]) -> str:
    intents_list = ", ".join(known_intents)
    return (
        "Sen bir Türkçe niyet (intent) sınıflandırma asistanısın. "
        "Kullanıcının cümlesini aşağıdaki niyetlerden BİRİNE ata: "
        f"{intents_list}. Hiçbiri uymuyorsa \"{UNKNOWN_INTENT}\" kullan. "
        "Yalnızca geçerli JSON ile cevap ver, başka hiçbir açıklama ekleme. "
        'Format: {"intent": "<NİYET>", "entities": {}, "confidence": <0-1 arası sayı>}\n\n'
        f"Kullanıcı cümlesi: {input_tr}"
    )


def _null_intent(raw_text: str) -> dict[str, Any]:
    return {"intent": UNKNOWN_INTENT, "entities": {}, "confidence": 0.0, "raw": raw_text}


def _parse_response(raw_text: str, known_intents: list[str]) -> dict[str, Any]:
    """Model ciktisini canonical semaya ayristirir; hicbir zaman exception
    firlatmaz -- ayristirma basarisiz olursa guvenli null-intent doner."""
    match = _JSON_BLOCK_RE.search(raw_text or "")
    if not match:
        return _null_intent(raw_text)

    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return _null_intent(raw_text)

    if not isinstance(parsed, dict):
        return _null_intent(raw_text)

    intent = parsed.get("intent")
    if not isinstance(intent, str) or intent not in known_intents:
        intent = UNKNOWN_INTENT

    entities = parsed.get("entities")
    if not isinstance(entities, dict):
        entities = {}

    confidence_raw = parsed.get("confidence")
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.0
    if not (0.0 <= confidence <= 1.0):
        confidence = 0.0

    return {"intent": intent, "entities": entities, "confidence": confidence, "raw": raw_text}


def classify(
    input_tr: str,
    known_intents: list[str],
    context: dict[str, Any] | None = None,
    client: OllamaModelClient | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> dict[str, Any]:
    """TR girdiyi Ollama uzerinden canonical NLU semasina siniflandirir.

    `context` su an modelin promptuna dahil edilmiyor (ileride konusma
    gecmisi/ek baglam icin ayrildi) ama arayuzde bilerek yer aliyor.

    Donen sozluk: `intent` (str), `entities` (dict), `confidence`
    (float 0..1), `raw` (modelin ham metin cevabi). Bu fonksiyon hicbir
    zaman exception firlatmaz.
    """
    context = context or {}
    active_client = client or OllamaModelClient()
    prompt = _build_prompt(input_tr, known_intents)

    last_raw = ""
    for attempt in range(1, max_attempts + 1):
        try:
            result = active_client.generate(prompt)
        except Exception as exc:  # generate() zaten graceful fail eder; ekstra savunma
            logger.warning("Ollama generate çağrısı beklenmedik hata: %s", exc)
            continue

        if result.get("fallback"):
            # model_client servis erisilemez oldugunda zaten mock donuyor --
            # gercek bir model cevabi degil, tekrar denemenin faydasi yok.
            last_raw = result.get("response", "")
            break

        last_raw = result.get("response", "")
        parsed = _parse_response(last_raw, known_intents)
        if parsed["intent"] != UNKNOWN_INTENT or attempt == max_attempts:
            return parsed
        logger.info(
            "Ollama cevabı ayrıştırılamadı (deneme %d/%d), tekrar deneniyor.",
            attempt,
            max_attempts,
        )

    return _null_intent(last_raw)
