"""T8 -- model_client.py testleri (graceful fail dogrulamasi)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from model_client import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    OllamaModelClient,
)

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


# --- Timeout cozumleme (2026-08-13 hotfix: 2.0s -> 30.0s + env override) ----


def test_default_timeout_is_30_seconds(monkeypatch):
    monkeypatch.delenv("OLLAMA_TIMEOUT_SECONDS", raising=False)

    client = OllamaModelClient()

    assert DEFAULT_TIMEOUT_SECONDS == 30.0
    assert client.timeout == 30.0


def test_timeout_env_override(monkeypatch):
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS", "12.5")

    client = OllamaModelClient()

    assert client.timeout == 12.5


def test_invalid_timeout_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS", "not-a-number")

    client = OllamaModelClient()

    assert client.timeout == DEFAULT_TIMEOUT_SECONDS


def test_explicit_timeout_arg_overrides_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS", "12.5")

    client = OllamaModelClient(timeout=1.0)

    assert client.timeout == 1.0


def test_timeout_is_passed_to_health_check_http_call(monkeypatch):
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS", "7.0")
    client = OllamaModelClient()

    fake_response = MagicMock()
    fake_response.status = 200
    fake_response.__enter__.return_value = fake_response

    with patch("model_client.urllib.request.urlopen", return_value=fake_response) as mock_urlopen:
        client.health_check()

    _, kwargs = mock_urlopen.call_args
    assert kwargs.get("timeout") == 7.0


def test_timeout_is_passed_to_generate_http_call(monkeypatch):
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS", "7.0")
    client = OllamaModelClient()

    fake_health_response = MagicMock()
    fake_health_response.status = 200
    fake_health_response.__enter__.return_value = fake_health_response

    fake_generate_response = MagicMock()
    fake_generate_response.read.return_value = b'{"response": "ok"}'
    fake_generate_response.__enter__.return_value = fake_generate_response

    with patch(
        "model_client.urllib.request.urlopen",
        side_effect=[fake_health_response, fake_generate_response],
    ) as mock_urlopen:
        client.generate("test prompt")

    assert mock_urlopen.call_count == 2
    for call in mock_urlopen.call_args_list:
        assert call.kwargs.get("timeout") == 7.0
