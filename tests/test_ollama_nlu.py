"""Faz 1 B031 -- ollama_nlu.py (Ollama NLU adaptörü) testleri.

Gerçek bir Ollama servisi gerektirmez -- `OllamaModelClient` yerine sahte
(fake) bir client enjekte edilir (`classify(..., client=...)`).
"""

from __future__ import annotations

from ollama_nlu import UNKNOWN_INTENT, classify

KNOWN = ["RUN_ECHO", "SHOW_DAILY_SPENDING", "RUN_DELETE_FILE"]


class _FakeClient:
    """model_client.OllamaModelClient ile aynı `.generate()` sözleşmesine
    sahip sahte istemci."""

    def __init__(self, response_text: str = "", fallback: bool = False) -> None:
        self._response_text = response_text
        self._fallback = fallback
        self.calls = 0

    def generate(self, prompt: str) -> dict:
        self.calls += 1
        return {"response": self._response_text, "model": "fake", "fallback": self._fallback}


class _ExplodingClient:
    """generate() her çağrıda exception fırlatan istemci (savunma testı için)."""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, prompt: str) -> dict:
        self.calls += 1
        raise RuntimeError("simulated network failure")


# --- Başarı yolu -----------------------------------------------------------


def test_success_path_parses_valid_json_response():
    fake = _FakeClient(
        '{"intent": "RUN_ECHO", "entities": {"value": "merhaba"}, "confidence": 0.9}'
    )

    result = classify("Ezo, echo ile merhaba yaz", KNOWN, client=fake)

    assert result == {
        "intent": "RUN_ECHO",
        "entities": {"value": "merhaba"},
        "confidence": 0.9,
        "raw": fake._response_text,
    }
    assert fake.calls == 1


def test_success_path_tolerates_extra_text_around_json_block():
    fake = _FakeClient('Elbette, işte cevap: {"intent": "RUN_ECHO", "confidence": 0.8} Teşekkürler.')

    result = classify("Ezo, echo ile merhaba yaz", KNOWN, client=fake)

    assert result["intent"] == "RUN_ECHO"
    assert result["confidence"] == 0.8


# --- Timeout / servis erişilemez yolu (model_client zaten fallback döner) --


def test_unreachable_service_returns_null_intent_without_retry():
    fake = _FakeClient(fallback=True)

    result = classify("herhangi bir metin", KNOWN, client=fake, max_attempts=3)

    assert result["intent"] == UNKNOWN_INTENT
    assert result["confidence"] == 0.0
    assert fake.calls == 1  # fallback -> tekrar denemenin faydası yok


# --- Bozuk/geçersiz model çıktısı yolu --------------------------------------


def test_malformed_response_retries_then_falls_back_to_null_intent():
    fake = _FakeClient("bu geçerli JSON değil, düz metin")

    result = classify("herhangi bir metin", KNOWN, client=fake, max_attempts=2)

    assert result["intent"] == UNKNOWN_INTENT
    assert fake.calls == 2  # iki kez denendi, ikisi de başarısız


def test_response_with_unknown_intent_value_is_normalized_to_UNKNOWN():
    fake = _FakeClient('{"intent": "SOMETHING_NOT_WHITELISTED", "confidence": 0.7}')

    result = classify("belirsiz cümle", KNOWN, client=fake, max_attempts=1)

    assert result["intent"] == UNKNOWN_INTENT


def test_confidence_out_of_range_is_clamped_to_zero():
    fake = _FakeClient('{"intent": "RUN_ECHO", "confidence": 5}')

    result = classify("Ezo echo", KNOWN, client=fake, max_attempts=1)

    assert result["confidence"] == 0.0


def test_missing_confidence_defaults_to_zero():
    fake = _FakeClient('{"intent": "RUN_ECHO"}')

    result = classify("Ezo echo", KNOWN, client=fake, max_attempts=1)

    assert result["confidence"] == 0.0


def test_non_dict_entities_normalized_to_empty_dict():
    fake = _FakeClient('{"intent": "RUN_ECHO", "confidence": 0.5, "entities": "not-a-dict"}')

    result = classify("Ezo echo", KNOWN, client=fake, max_attempts=1)

    assert result["entities"] == {}


# --- Savunma: adapter hiçbir zaman exception fırlatmaz ---------------------


def test_never_raises_on_client_exception():
    exploding = _ExplodingClient()

    result = classify("test", KNOWN, client=exploding, max_attempts=2)

    assert result["intent"] == UNKNOWN_INTENT
    assert result["confidence"] == 0.0
    assert exploding.calls == 2  # her deneme ayrı ayrı yakalandı


def test_empty_response_text_returns_null_intent():
    fake = _FakeClient("")

    result = classify("test", KNOWN, client=fake, max_attempts=1)

    assert result["intent"] == UNKNOWN_INTENT
