"""daily_gateway_smoke_core.py testleri.

Gercek Ollama/dosya sistemi/ag gerektirmez -- router, config, verify_fn,
classify_fn hepsi sahte (fake) nesnelerdir. Gercek data/audit/audit.log.jsonl
dosyasina asla yazilmaz (gecici bir dosya kullanilir).
"""

from __future__ import annotations

from dataclasses import dataclass

from daily_gateway_smoke_core import (
    EXIT_ACTION_REQUIRED,
    EXIT_DEGRADED,
    EXIT_HEALTHY,
    run_daily_smoke,
)


@dataclass
class _FakeHealth:
    healthy: bool
    detail: str = ""


class _FakeRouter:
    def __init__(self, ollama_healthy: bool = True):
        self._ollama_healthy = ollama_healthy

    def healthcheck_all(self):
        return {"ollama": _FakeHealth(healthy=self._ollama_healthy)}


class _FakeConfig:
    ollama_enabled = True
    local_alt_enabled = False
    remote_enabled = False
    remote_policy_gate = "required"
    ollama_cpu_verify_enabled = True
    ollama_cpu_verify_strict = True
    ollama_host = "http://localhost:11434"
    ollama_cpu_marker_file = "./runtime/ollama_cpu_mode.ok"
    ollama_cpu_verify_methods = ("marker",)
    ollama_cpu_verify_timeout_ms = 1000


@dataclass
class _FakeVerificationResult:
    status: str
    reason_code: str
    evidence: dict
    checked_at: str = "2026-01-01T00:00:00+00:00"


def _make_verify_fn(status: str, reason_code: str):
    def _fn(**kwargs):
        return _FakeVerificationResult(status=status, reason_code=reason_code, evidence={})

    return _fn


def _make_classify_fn(raw: str, intent: str = "RUN_ECHO", confidence: float = 0.9):
    def _fn(prompt, known_intents):
        return {"intent": intent, "entities": {}, "confidence": confidence, "raw": raw}

    return _fn


def _write_audit_line(path, before_call):
    """Testte 'gercek bir aksiyonun audit'e yazdigi' senaryosunu simule
    etmek icin classify_fn cagrildiginda audit dosyasina bir satir ekler."""
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"event": "probe"}\n')


def test_daily_smoke_returns_healthy_when_real_response_and_audit_appends(tmp_path):
    audit_path = tmp_path / "audit.log.jsonl"
    audit_path.write_text("", encoding="utf-8")

    def classify_fn(prompt, known_intents):
        _write_audit_line(audit_path, True)
        return {"intent": "RUN_ECHO", "entities": {}, "confidence": 0.9, "raw": "gercek model cevabi"}

    result = run_daily_smoke(
        config=_FakeConfig(),
        router=_FakeRouter(ollama_healthy=True),
        verify_fn=_make_verify_fn("VERIFIED", "CPU_MODE_VERIFIED"),
        classify_fn=classify_fn,
        is_remote_allowed_fn=lambda: (False, "policy kapali"),
        metrics_snapshot_fn=lambda: {"counters": []},
        audit_log_path=audit_path,
    )

    assert result.exit_code == EXIT_HEALTHY
    assert result.classify_probe["got_real_response"] is True
    assert result.audit_verification["appended"] is True


def test_daily_smoke_returns_degraded_when_strict_restriction_is_expected(tmp_path):
    audit_path = tmp_path / "audit.log.jsonl"
    audit_path.write_text("", encoding="utf-8")

    def classify_fn(prompt, known_intents):
        _write_audit_line(audit_path, True)
        return {"intent": "UNKNOWN", "entities": {}, "confidence": 0.0, "raw": ""}

    result = run_daily_smoke(
        config=_FakeConfig(),
        router=_FakeRouter(ollama_healthy=True),
        verify_fn=_make_verify_fn("UNKNOWN", "SIGNAL_NOT_AVAILABLE"),
        classify_fn=classify_fn,
        is_remote_allowed_fn=lambda: (False, "policy kapali"),
        metrics_snapshot_fn=lambda: {"counters": []},
        audit_log_path=audit_path,
    )

    assert result.exit_code == EXIT_DEGRADED
    assert result.classify_probe["got_real_response"] is False


def test_daily_smoke_returns_action_required_when_audit_does_not_append(tmp_path):
    audit_path = tmp_path / "audit.log.jsonl"
    audit_path.write_text("", encoding="utf-8")

    def classify_fn(prompt, known_intents):
        # KASITLI: audit'e yazmiyor -- audit zincirinin kirik oldugu senaryo.
        return {"intent": "UNKNOWN", "entities": {}, "confidence": 0.0, "raw": ""}

    result = run_daily_smoke(
        config=_FakeConfig(),
        router=_FakeRouter(ollama_healthy=True),
        verify_fn=_make_verify_fn("VERIFIED", "CPU_MODE_VERIFIED"),
        classify_fn=classify_fn,
        is_remote_allowed_fn=lambda: (False, "policy kapali"),
        metrics_snapshot_fn=lambda: {"counters": []},
        audit_log_path=audit_path,
    )

    assert result.exit_code == EXIT_ACTION_REQUIRED
    assert result.audit_verification["appended"] is False


def test_daily_smoke_returns_action_required_when_no_provider_healthy(tmp_path):
    audit_path = tmp_path / "audit.log.jsonl"
    audit_path.write_text("", encoding="utf-8")

    def classify_fn(prompt, known_intents):
        _write_audit_line(audit_path, True)
        return {"intent": "UNKNOWN", "entities": {}, "confidence": 0.0, "raw": ""}

    result = run_daily_smoke(
        config=_FakeConfig(),
        router=_FakeRouter(ollama_healthy=False),
        verify_fn=_make_verify_fn("UNVERIFIED", "OLLAMA_UNREACHABLE"),
        classify_fn=classify_fn,
        is_remote_allowed_fn=lambda: (False, "policy kapali"),
        metrics_snapshot_fn=lambda: {"counters": []},
        audit_log_path=audit_path,
    )

    assert result.exit_code == EXIT_ACTION_REQUIRED


def test_daily_smoke_flags_weakened_remote_policy_gate_as_critical(tmp_path):
    audit_path = tmp_path / "audit.log.jsonl"
    audit_path.write_text("", encoding="utf-8")

    class _WeakenedConfig(_FakeConfig):
        remote_policy_gate = "optional"  # KASITLI: zayiflatilmis

    def classify_fn(prompt, known_intents):
        _write_audit_line(audit_path, True)
        return {"intent": "RUN_ECHO", "entities": {}, "confidence": 0.9, "raw": "gercek cevap"}

    result = run_daily_smoke(
        config=_WeakenedConfig(),
        router=_FakeRouter(ollama_healthy=True),
        verify_fn=_make_verify_fn("VERIFIED", "CPU_MODE_VERIFIED"),
        classify_fn=classify_fn,
        is_remote_allowed_fn=lambda: (False, "policy kapali"),
        metrics_snapshot_fn=lambda: {"counters": []},
        audit_log_path=audit_path,
    )

    assert result.exit_code == EXIT_ACTION_REQUIRED
    assert result.config_sanity["critical"] is True
