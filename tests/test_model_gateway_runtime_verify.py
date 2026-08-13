"""runtime_verify.py testleri -- kanit temelli CPU-only dogrulama.

Gercek ag/subprocess cagrisi yapilmaz -- ic problar (`_check_marker_file`,
`_check_recent_crash_evidence`, `_check_http_diagnostic`) monkeypatch ile
sahtelenir.
"""

from __future__ import annotations

import model_gateway.runtime_verify as rv


def test_ollama_unreachable_short_circuits_without_running_probes(monkeypatch):
    calls = []
    monkeypatch.setattr(rv, "_check_marker_file", lambda *a, **k: calls.append("marker") or {})
    monkeypatch.setattr(rv, "_check_recent_crash_evidence", lambda *a, **k: calls.append("process") or {})
    monkeypatch.setattr(rv, "_check_http_diagnostic", lambda *a, **k: calls.append("http") or {})

    result = rv.verify_ollama_cpu_mode(
        ollama_healthy=False,
        base_url="http://localhost:11434",
        marker_file="./runtime/ollama_cpu_mode.ok",
        methods=("http", "process", "marker"),
        timeout_ms=1000,
    )

    assert result.status == rv.STATUS_UNVERIFIED
    assert result.reason_code == rv.REASON_OLLAMA_UNREACHABLE
    assert calls == []  # hicbir probe calismamali -- erisilemezlik yeterli kanit


def test_fresh_marker_yields_verified(monkeypatch):
    monkeypatch.setattr(rv, "_check_marker_file", lambda *a, **k: {"present": True, "fresh": True})
    monkeypatch.setattr(rv, "_check_recent_crash_evidence", lambda *a, **k: {"available": False})
    monkeypatch.setattr(rv, "_check_http_diagnostic", lambda *a, **k: {"available": False})

    result = rv.verify_ollama_cpu_mode(
        ollama_healthy=True,
        base_url="http://localhost:11434",
        marker_file="./runtime/ollama_cpu_mode.ok",
        methods=("marker",),
        timeout_ms=1000,
    )

    assert result.status == rv.STATUS_VERIFIED
    assert result.reason_code == rv.REASON_CPU_MODE_VERIFIED


def test_crash_evidence_yields_unverified_env_mismatch_even_with_fresh_marker(monkeypatch):
    """Negatif kanit (yakin zamanli cokus) her zaman kazanir -- pozitif
    marker kanitindan daha guclu, guvenlik tarafinda hata yapilir."""
    monkeypatch.setattr(rv, "_check_marker_file", lambda *a, **k: {"present": True, "fresh": True})
    monkeypatch.setattr(
        rv, "_check_recent_crash_evidence", lambda *a, **k: {"available": True, "crash_count": 3}
    )
    monkeypatch.setattr(rv, "_check_http_diagnostic", lambda *a, **k: {"available": False})

    result = rv.verify_ollama_cpu_mode(
        ollama_healthy=True,
        base_url="http://localhost:11434",
        marker_file="./runtime/ollama_cpu_mode.ok",
        methods=("process", "marker"),
        timeout_ms=1000,
    )

    assert result.status == rv.STATUS_UNVERIFIED
    assert result.reason_code == rv.REASON_ENV_MISMATCH


def test_vram_usage_detected_via_http_yields_env_mismatch(monkeypatch):
    monkeypatch.setattr(rv, "_check_marker_file", lambda *a, **k: {"present": False, "fresh": False})
    monkeypatch.setattr(rv, "_check_recent_crash_evidence", lambda *a, **k: {"available": False})
    monkeypatch.setattr(
        rv,
        "_check_http_diagnostic",
        lambda *a, **k: {"available": True, "loaded_models": 1, "any_vram_used": True},
    )

    result = rv.verify_ollama_cpu_mode(
        ollama_healthy=True,
        base_url="http://localhost:11434",
        marker_file="./runtime/ollama_cpu_mode.ok",
        methods=("http", "marker"),
        timeout_ms=1000,
    )

    assert result.status == rv.STATUS_UNVERIFIED
    assert result.reason_code == rv.REASON_ENV_MISMATCH


def test_no_signal_anywhere_yields_unknown(monkeypatch):
    monkeypatch.setattr(rv, "_check_marker_file", lambda *a, **k: {"present": False, "fresh": False})
    monkeypatch.setattr(rv, "_check_recent_crash_evidence", lambda *a, **k: {"available": False})
    monkeypatch.setattr(rv, "_check_http_diagnostic", lambda *a, **k: {"available": False})

    result = rv.verify_ollama_cpu_mode(
        ollama_healthy=True,
        base_url="http://localhost:11434",
        marker_file="./runtime/ollama_cpu_mode.ok",
        methods=("http", "process", "marker"),
        timeout_ms=1000,
    )

    assert result.status == rv.STATUS_UNKNOWN
    assert result.reason_code == rv.REASON_SIGNAL_NOT_AVAILABLE


def test_marker_probe_never_starved_by_slow_probes(monkeypatch):
    """Zaman butcesi diger problar tarafindan tuketilse bile marker
    kontrolu HER ZAMAN calismali (bkz. modul ici yorum -- near-zero-cost)."""
    marker_calls = []

    def slow_http(*a, **k):
        return {"available": False, "reason": "yavas"}

    monkeypatch.setattr(rv, "_check_marker_file", lambda *a, **k: marker_calls.append(1) or {"present": True, "fresh": True})
    monkeypatch.setattr(rv, "_check_http_diagnostic", slow_http)
    monkeypatch.setattr(rv, "_check_recent_crash_evidence", lambda *a, **k: {"available": False})

    result = rv.verify_ollama_cpu_mode(
        ollama_healthy=True,
        base_url="http://localhost:11434",
        marker_file="./runtime/ollama_cpu_mode.ok",
        methods=("http", "process", "marker"),
        timeout_ms=1,  # asiri kisitli -- http/process suresi asilir
    )

    assert marker_calls == [1]
    assert result.status == rv.STATUS_VERIFIED


def test_unexpected_exception_yields_check_error(monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("beklenmeyen hata")

    monkeypatch.setattr(rv, "_check_marker_file", _raise)

    result = rv.verify_ollama_cpu_mode(
        ollama_healthy=True,
        base_url="http://localhost:11434",
        marker_file="./runtime/ollama_cpu_mode.ok",
        methods=("marker",),
        timeout_ms=1000,
    )

    assert result.status == rv.STATUS_UNKNOWN
    assert result.reason_code == rv.REASON_CHECK_ERROR
