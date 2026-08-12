"""T8 -- model_client.py testleri (graceful fail dogrulamasi)."""

from __future__ import annotations

from model_client import DEFAULT_BASE_URL, DEFAULT_MODEL, OllamaModelClient

# Port 1, yerel makinede pratikte hicbir zaman dinlenmeyen bir port --
# baglanti hemen reddedilir (timeout beklemeden hizli test).
UNREACHABLE_URL = "http://127.0.0.1:1"


def test_health_check_returns_false_when_service_unavailable():
    client = OllamaModelClient(base_url=UNREACHABLE_URL, timeout=1.0)

    assert client.health_check() is False


def test_generate_returns_mock_fallback_when_service_unavailable():
    client = OllamaModelClient(base_url=UNREACHABLE_URL, timeout=1.0)

    result = client.generate("test prompt")

    assert result["fallback"] is True
    assert "test prompt" in result["response"]
    assert result["model"] == DEFAULT_MODEL


def test_generate_does_not_raise_when_service_unavailable():
    client = OllamaModelClient(base_url=UNREACHABLE_URL, timeout=1.0)

    # Servis yokken bile exception firlamamali -- kontrollu donus.
    try:
        client.generate("herhangi bir prompt")
    except Exception as exc:  # noqa: BLE001 -- testin amaci tam olarak bu
        raise AssertionError(f"generate() istisna firlatmamali, firlatti: {exc}") from exc


def test_env_var_defaults(monkeypatch):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)

    client = OllamaModelClient()

    assert client.base_url == DEFAULT_BASE_URL
    assert client.model == DEFAULT_MODEL


def test_env_var_override(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://example.internal:9999")
    monkeypatch.setenv("OLLAMA_MODEL", "mistral")

    client = OllamaModelClient()

    assert client.base_url == "http://example.internal:9999"
    assert client.model == "mistral"


def test_explicit_args_override_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://example.internal:9999")

    client = OllamaModelClient(base_url="http://explicit:1234")

    assert client.base_url == "http://explicit:1234"
