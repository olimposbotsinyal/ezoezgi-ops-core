"""`legitimacy_provider_client.py` testleri -- GERCEK Jira "verify-only"
istemcisi, TAMAMEN deterministik (gercek ag/uyku YOK -- `opener`/`sleep_fn`
enjeksiyonu ile sahte fonksiyonlar kullanilir). Kapsam: basari, 404,
401/403, gecici hata + yeniden deneme, tum denemeler tukenmesi, redaction,
yapilandirilmamis (env eksik) durum."""

from __future__ import annotations

import urllib.error

from legitimacy_provider_client import (
    JiraCredentials,
    ProviderEvidence,
    check_ticket_via_jira,
    read_jira_credentials_from_env,
    redact,
)

COMPLETE_CREDS = JiraCredentials(base_url="https://example.atlassian.net", email="a@b.com", api_token="secret-token-123")


class _FakeResponse:
    def __init__(self, status: int):
        self._status = status

    def getcode(self) -> int:
        return self._status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener_returning(status: int):
    def _opener(req, timeout):
        return _FakeResponse(status)

    return _opener


def _opener_raising(exc):
    def _opener(req, timeout):
        raise exc

    return _opener


def _opener_sequence(*results):
    it = iter(results)

    def _opener(req, timeout):
        result = next(it)
        if isinstance(result, Exception):
            raise result
        return _FakeResponse(result)

    return _opener


def test_read_jira_credentials_from_env_reads_all_three():
    creds = read_jira_credentials_from_env(
        {"JIRA_BASE_URL": "https://x.atlassian.net", "JIRA_EMAIL": "e@x.com", "JIRA_API_TOKEN": "tok"}
    )
    assert creds.is_complete()
    assert creds.base_url == "https://x.atlassian.net"


def test_read_jira_credentials_from_env_missing_var_is_incomplete():
    creds = read_jira_credentials_from_env({"JIRA_BASE_URL": "https://x.atlassian.net"})
    assert creds.is_complete() is False


def test_check_ticket_via_jira_not_configured_makes_no_call():
    calls = []

    def opener(req, timeout):
        calls.append(req)
        raise AssertionError("ag cagrisi yapilmamali")

    result = check_ticket_via_jira("OPS-1", credentials=JiraCredentials(), opener=opener, sleep_fn=lambda s: None)
    assert result.checked is False
    assert result.found is False
    assert calls == []


def test_check_ticket_via_jira_success_200():
    result = check_ticket_via_jira(
        "OPS-1", credentials=COMPLETE_CREDS, opener=_opener_returning(200), sleep_fn=lambda s: None
    )
    assert result.checked is True
    assert result.found is True
    assert result.status_code == 200
    assert result.attempts == 1


def test_check_ticket_via_jira_404_no_retry():
    sleeps = []
    result = check_ticket_via_jira(
        "OPS-404",
        credentials=COMPLETE_CREDS,
        opener=_opener_raising(urllib.error.HTTPError("url", 404, "Not Found", {}, None)),
        sleep_fn=lambda s: sleeps.append(s),
    )
    assert result.checked is True
    assert result.found is False
    assert result.status_code == 404
    assert result.attempts == 1
    assert sleeps == []


def test_check_ticket_via_jira_401_no_retry_and_redacted():
    result = check_ticket_via_jira(
        "OPS-1",
        credentials=COMPLETE_CREDS,
        opener=_opener_raising(urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)),
        sleep_fn=lambda s: None,
    )
    assert result.checked is True
    assert result.found is False
    assert result.status_code == 401
    assert result.attempts == 1
    assert "secret-token-123" not in result.detail
    assert "a@b.com" not in result.detail


def test_check_ticket_via_jira_403_no_retry():
    result = check_ticket_via_jira(
        "OPS-1",
        credentials=COMPLETE_CREDS,
        opener=_opener_raising(urllib.error.HTTPError("url", 403, "Forbidden", {}, None)),
        sleep_fn=lambda s: None,
    )
    assert result.attempts == 1
    assert result.status_code == 403


def test_check_ticket_via_jira_transient_error_retries_then_succeeds():
    sleeps = []
    result = check_ticket_via_jira(
        "OPS-1",
        credentials=COMPLETE_CREDS,
        opener=_opener_sequence(urllib.error.URLError("temporary DNS failure"), 200),
        sleep_fn=lambda s: sleeps.append(s),
    )
    assert result.checked is True
    assert result.found is True
    assert result.attempts == 2
    assert len(sleeps) == 1


def test_check_ticket_via_jira_exhausts_retries_and_fails():
    sleeps = []
    result = check_ticket_via_jira(
        "OPS-1",
        credentials=COMPLETE_CREDS,
        max_retries=2,
        opener=_opener_raising(urllib.error.URLError("connection refused")),
        sleep_fn=lambda s: sleeps.append(s),
    )
    assert result.checked is True
    assert result.found is False
    assert result.attempts == 3
    assert len(sleeps) == 2


def test_check_ticket_via_jira_transient_error_detail_is_redacted():
    def opener(req, timeout):
        raise urllib.error.URLError("failed for secret-token-123 at a@b.com")

    result = check_ticket_via_jira(
        "OPS-1", credentials=COMPLETE_CREDS, max_retries=0, opener=opener, sleep_fn=lambda s: None
    )
    assert "secret-token-123" not in result.detail
    assert "a@b.com" not in result.detail


def test_check_ticket_via_jira_unexpected_status_is_not_found():
    result = check_ticket_via_jira(
        "OPS-1", credentials=COMPLETE_CREDS, opener=_opener_returning(302), sleep_fn=lambda s: None
    )
    assert result.checked is True
    assert result.found is False
    assert result.status_code == 302


def test_redact_replaces_all_provided_secrets():
    text = redact("token=abc123 email=x@y.com", ["abc123", "x@y.com"])
    assert "abc123" not in text
    assert "x@y.com" not in text
    assert "***REDACTED***" in text


def test_redact_ignores_falsy_secrets():
    assert redact("hello", [None, ""]) == "hello"


def test_provider_evidence_is_plain_dataclass_with_expected_fields():
    evidence = ProviderEvidence(mode="jira", checked=True, found=True, status_code=200, detail="ok")
    assert evidence.attempts == 1
