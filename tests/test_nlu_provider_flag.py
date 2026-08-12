"""Faz 1 B031 -- NLU_PROVIDER feature flag + orchestrator smoke testleri.

Ollama'nın monkeypatch edilmesi (gerçek ağ çağrısı yapılmaz) sayesinde bu
testler hızlı ve deterministiktir; `ollama_nlu.classify()`'nin kendi
davranışı zaten `tests/test_ollama_nlu.py`'de ayrıca test ediliyor.
"""

from __future__ import annotations

import bridge
from orchestrator import STATUS_OK, Orchestrator
from registry import build_default_registry

ALIASES = ["ezo", "ezgi"]


# --- Provider seçimi (saf, ağsız) ------------------------------------------


def test_default_provider_is_mock_when_env_unset(monkeypatch):
    monkeypatch.delenv("NLU_PROVIDER", raising=False)

    assert bridge._get_nlu_provider() == bridge.NLU_PROVIDER_MOCK


def test_provider_can_be_switched_to_ollama_via_env(monkeypatch):
    monkeypatch.setenv("NLU_PROVIDER", "ollama")

    assert bridge._get_nlu_provider() == bridge.NLU_PROVIDER_OLLAMA


def test_provider_env_value_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("NLU_PROVIDER", "OLLAMA")

    assert bridge._get_nlu_provider() == bridge.NLU_PROVIDER_OLLAMA


def test_invalid_provider_value_falls_back_to_mock_safely(monkeypatch):
    monkeypatch.setenv("NLU_PROVIDER", "not-a-real-provider")

    assert bridge._get_nlu_provider() == bridge.NLU_PROVIDER_MOCK


# --- translate_and_extract() sözleşmesi provider'dan bağımsız korunuyor ----


def test_mock_provider_preserves_existing_output_contract(monkeypatch):
    monkeypatch.delenv("NLU_PROVIDER", raising=False)

    extracted = bridge.translate_and_extract("Ezo, echo ile merhaba yaz", ALIASES)

    assert set(extracted.keys()) == {"detected_alias", "task_en", "original_tr", "confidence"}
    assert extracted["task_en"] == "RUN_ECHO"
    assert extracted["detected_alias"] == "ezo"


def test_ollama_provider_success_maps_into_existing_output_contract(monkeypatch):
    monkeypatch.setenv("NLU_PROVIDER", "ollama")

    def fake_ollama_classify(input_tr, known_intents, **kwargs):
        assert input_tr == "Ezo, echo ile merhaba yaz"
        return {"intent": "RUN_ECHO", "entities": {}, "confidence": 0.95, "raw": "..."}

    monkeypatch.setattr(bridge, "ollama_classify", fake_ollama_classify)

    extracted = bridge.translate_and_extract("Ezo, echo ile merhaba yaz", ALIASES)

    assert set(extracted.keys()) == {"detected_alias", "task_en", "original_tr", "confidence"}
    assert extracted["task_en"] == "RUN_ECHO"
    assert extracted["confidence"] == 0.95


def test_ollama_provider_unknown_intent_falls_back_to_mock(monkeypatch):
    monkeypatch.setenv("NLU_PROVIDER", "ollama")

    def fake_ollama_classify(input_tr, known_intents, **kwargs):
        return {"intent": "UNKNOWN", "entities": {}, "confidence": 0.0, "raw": "..."}

    monkeypatch.setattr(bridge, "ollama_classify", fake_ollama_classify)

    # Mock siniflandirici "echo" kelimesini yine de yakalar.
    extracted = bridge.translate_and_extract("Ezo, echo ile merhaba yaz", ALIASES)

    assert extracted["task_en"] == "RUN_ECHO"


def test_ollama_provider_exception_falls_back_to_mock_without_raising(monkeypatch):
    monkeypatch.setenv("NLU_PROVIDER", "ollama")

    def fake_ollama_classify_raises(input_tr, known_intents, **kwargs):
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr(bridge, "ollama_classify", fake_ollama_classify_raises)

    extracted = bridge.translate_and_extract("Ezo, echo ile merhaba yaz", ALIASES)

    assert extracted["task_en"] == "RUN_ECHO"


# --- Orchestrator smoke testi: her iki saglayici da uctan uca calisir -----


def test_orchestrator_smoke_with_mock_provider(monkeypatch):
    monkeypatch.delenv("NLU_PROVIDER", raising=False)

    extracted = bridge.translate_and_extract("Ezo, echo ile merhaba yaz", ALIASES)
    orchestrator = Orchestrator(build_default_registry())
    result_en = orchestrator.handle_task(extracted)

    assert result_en["status"] == STATUS_OK
    assert result_en["value"] == "Merhaba"


def test_orchestrator_smoke_with_ollama_provider_success(monkeypatch):
    monkeypatch.setenv("NLU_PROVIDER", "ollama")

    def fake_ollama_classify(input_tr, known_intents, **kwargs):
        return {"intent": "RUN_ECHO", "entities": {}, "confidence": 0.9, "raw": "..."}

    monkeypatch.setattr(bridge, "ollama_classify", fake_ollama_classify)

    extracted = bridge.translate_and_extract("Ezo, echo ile merhaba yaz", ALIASES)
    orchestrator = Orchestrator(build_default_registry())
    result_en = orchestrator.handle_task(extracted)

    assert result_en["status"] == STATUS_OK
    assert result_en["value"] == "Merhaba"


def test_orchestrator_smoke_with_ollama_provider_failure_still_succeeds_via_fallback(monkeypatch):
    monkeypatch.setenv("NLU_PROVIDER", "ollama")

    def fake_ollama_classify_raises(input_tr, known_intents, **kwargs):
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr(bridge, "ollama_classify", fake_ollama_classify_raises)

    extracted = bridge.translate_and_extract("Ezo, echo ile merhaba yaz", ALIASES)
    orchestrator = Orchestrator(build_default_registry())
    result_en = orchestrator.handle_task(extracted)

    # Ollama basarisiz olsa da mock fallback sayesinde orchestrator basariyla
    # tamamlanir -- finans disi davranissal sureklilik garantisi.
    assert result_en["status"] == STATUS_OK
    assert result_en["value"] == "Merhaba"


def test_risky_task_still_waits_for_approval_regardless_of_nlu_provider(monkeypatch):
    # T13 risk/onay akisi, NLU saglayicisindan tamamen bagimsiz calismali.
    monkeypatch.setenv("NLU_PROVIDER", "ollama")

    def fake_ollama_classify(input_tr, known_intents, **kwargs):
        return {"intent": "RUN_DELETE_FILE", "entities": {}, "confidence": 0.9, "raw": "..."}

    monkeypatch.setattr(bridge, "ollama_classify", fake_ollama_classify)

    extracted = bridge.translate_and_extract("Ezo, tüm dosyaları sil", ALIASES)
    orchestrator = Orchestrator(build_default_registry())
    result_en = orchestrator.handle_task(extracted)

    assert result_en["status"] == "WAITING_APPROVAL"
    assert result_en["risk_level"] == "irreversible"
