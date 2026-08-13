"""`detect_observability_drift.py` testleri -- baseline manifest/checksum
PARSER dogrulamasi (gorevin acikca istedigi test odagi) + gercek repo
baseline dosyalarinin (infra/monitoring/baseline/) gecerliligi + uctan
uca "hic drift yok" / "gercekten drift var" senaryolari."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from detect_observability_drift import (
    DEFAULT_ALERTS_PATH,
    DEFAULT_CHECKSUM_PATH,
    DEFAULT_MANIFEST_PATH,
    REPO_ROOT_DEFAULT,
    compute_file_sha256,
    load_baseline_checksum,
    load_baseline_manifest,
    observe_metrics_schema_from_jsonl,
    read_config_source_defaults,
    run_drift_detection,
)
from model_gateway.metrics_sink import JsonlAppendSink, MetricEvent
from observability_drift_core import CATEGORY_ALERT_RULES, SEVERITY_CRITICAL, SEVERITY_NONE, overall_drift_exit_code


def test_load_baseline_manifest_parses_valid_json(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"business_metrics": {}, "config_defaults": {"remote_enabled": False}}), encoding="utf-8")
    data = load_baseline_manifest(path)
    assert data["config_defaults"]["remote_enabled"] is False


def test_load_baseline_manifest_invalid_json_raises(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_baseline_manifest(path)


def test_load_baseline_checksum_skips_comments_and_blank_lines(tmp_path):
    path = tmp_path / "checksum.txt"
    path.write_text("# yorum satiri\n# baska yorum\n\nabc123def456\n", encoding="utf-8")
    checksum = load_baseline_checksum(path)
    assert checksum == "abc123def456"


def test_load_baseline_checksum_no_valid_line_raises(tmp_path):
    path = tmp_path / "checksum.txt"
    path.write_text("# yalnizca yorum\n\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_baseline_checksum(path)


def test_compute_file_sha256_is_deterministic(tmp_path):
    path = tmp_path / "f.txt"
    path.write_text("merhaba dunya", encoding="utf-8")
    h1 = compute_file_sha256(path)
    h2 = compute_file_sha256(path)
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex digest


def test_observe_metrics_schema_from_jsonl_extracts_union_of_labels(tmp_path):
    jsonl_path = tmp_path / "m.jsonl"
    sink = JsonlAppendSink(jsonl_path, max_mb=50, retention_days=7)
    now = time.time()
    sink.write(MetricEvent(ts=now, metric_type="counter", name="x", labels={"a": "1"}, value=1.0))
    sink.write(MetricEvent(ts=now, metric_type="counter", name="x", labels={"b": "2"}, value=1.0))

    schema = observe_metrics_schema_from_jsonl(jsonl_path, window_minutes=60)
    assert schema["x"]["type"] == "counter"
    assert sorted(schema["x"]["labels"]) == ["a", "b"]


def test_observe_metrics_schema_from_jsonl_empty_file_returns_empty_schema(tmp_path):
    jsonl_path = tmp_path / "does_not_exist.jsonl"
    schema = observe_metrics_schema_from_jsonl(jsonl_path, window_minutes=60)
    assert schema == {}


# --- Gercek repo baseline dosyalarinin gecerliligi ------------------------


def test_real_repo_metrics_manifest_is_well_formed():
    manifest_path = REPO_ROOT_DEFAULT / DEFAULT_MANIFEST_PATH
    data = load_baseline_manifest(manifest_path)

    assert "business_metrics" in data
    assert "self_observability_metrics" in data
    assert "config_defaults" in data
    assert data["config_defaults"]["remote_enabled"] is False
    assert data["config_defaults"]["ollama_cpu_verify_strict"] is True
    assert "synthetic" in data.get("always_allowed_extra_labels", [])

    for name, entry in {**data["business_metrics"], **data["self_observability_metrics"]}.items():
        assert entry["type"] in ("counter", "gauge", "histogram"), f"{name}: gecersiz tip"
        assert isinstance(entry["labels"], list), f"{name}: labels bir liste olmali"


def test_real_repo_alerts_checksum_matches_actual_file():
    """Baseline checksum dosyasi, GERCEK alert kurallari dosyasinin
    GUNCEL SHA256'siyla eslesmeli -- eslesmezse, ya dosya onaysiz
    degisti (gercek bir sorun) ya da baseline unutuldu (bakim borcu)."""
    checksum_path = REPO_ROOT_DEFAULT / DEFAULT_CHECKSUM_PATH
    alerts_path = REPO_ROOT_DEFAULT / DEFAULT_ALERTS_PATH

    baseline_checksum = load_baseline_checksum(checksum_path)
    actual_checksum = compute_file_sha256(alerts_path)

    assert baseline_checksum == actual_checksum, (
        "infra/monitoring/prometheus/model_gateway_alerts.yaml degismis ama "
        "infra/monitoring/baseline/alerts_checksum_v1.txt guncellenmemis -- "
        "bkz. o dosyanin 'Guncelleme proseduru' notu."
    )


def test_read_config_source_defaults_reflects_real_repo_config():
    defaults = read_config_source_defaults(REPO_ROOT_DEFAULT)
    assert defaults["remote_enabled"] is False
    assert defaults["ollama_cpu_verify_strict"] is True


def test_read_config_source_defaults_is_isolated_from_calling_process_env(monkeypatch):
    """Cagiran surecin REMOTE_ENABLED/OLLAMA_CPU_VERIFY_STRICT ortam
    degiskenleri zaten set edilmis olsa bile, fonksiyon KAYNAK-KODDA
    tanimli GERCEK varsayilani okumali (env'den ETKILENMEMELI)."""
    monkeypatch.setenv("REMOTE_ENABLED", "true")
    monkeypatch.setenv("OLLAMA_CPU_VERIFY_STRICT", "false")

    defaults = read_config_source_defaults(REPO_ROOT_DEFAULT)

    assert defaults["remote_enabled"] is False
    assert defaults["ollama_cpu_verify_strict"] is True


# --- Uctan uca drift tespiti ------------------------------------------------


def test_run_drift_detection_clean_repo_state_has_no_critical_drift(tmp_path):
    """Bos bir JSONL (hic trafik yok, NORMAL durum) + degismemis gercek
    alert/config dosyalariyla calistirildiginda, kritik bir drift
    OLMAMALI (exit code 0 veya en fazla 1 -- asla 2)."""
    empty_jsonl = tmp_path / "empty.jsonl"
    findings, _ = run_drift_detection(
        repo_root=REPO_ROOT_DEFAULT,
        manifest_path=REPO_ROOT_DEFAULT / DEFAULT_MANIFEST_PATH,
        checksum_path=REPO_ROOT_DEFAULT / DEFAULT_CHECKSUM_PATH,
        alerts_path=REPO_ROOT_DEFAULT / DEFAULT_ALERTS_PATH,
        jsonl_path=empty_jsonl,
        window_minutes=60,
    )
    assert overall_drift_exit_code(findings) == 0


def test_run_drift_detection_tampered_alerts_file_is_critical(tmp_path):
    tampered_alerts = tmp_path / "tampered.yaml"
    real_alerts = (REPO_ROOT_DEFAULT / DEFAULT_ALERTS_PATH).read_text(encoding="utf-8")
    tampered_alerts.write_text(real_alerts + "\n# tampered\n", encoding="utf-8")

    empty_jsonl = tmp_path / "empty.jsonl"
    findings, _ = run_drift_detection(
        repo_root=REPO_ROOT_DEFAULT,
        manifest_path=REPO_ROOT_DEFAULT / DEFAULT_MANIFEST_PATH,
        checksum_path=REPO_ROOT_DEFAULT / DEFAULT_CHECKSUM_PATH,
        alerts_path=tampered_alerts,
        jsonl_path=empty_jsonl,
        window_minutes=60,
    )
    assert overall_drift_exit_code(findings) == 2
    assert any(f.severity == SEVERITY_CRITICAL and f.category == "alert_rules" for f in findings)


def test_run_drift_detection_recognizes_approved_change_via_ledger(tmp_path):
    """Degisiklik `apply_threshold_proposal.ps1 -Apply` ile GERCEKTEN
    onaylanip uygulanmis ve `approved_checksums_ledger.jsonl`'e
    kaydedilmisse -- baseline'dan sapmis olsa bile CRITICAL DEGIL
    (bkz. threshold_apply_core.build_ledger_entry ile ayni sekil)."""
    tampered_alerts = tmp_path / "tampered.yaml"
    real_alerts = (REPO_ROOT_DEFAULT / DEFAULT_ALERTS_PATH).read_text(encoding="utf-8")
    tampered_alerts.write_text(real_alerts + "\n# onayli degisiklik\n", encoding="utf-8")
    tampered_checksum = compute_file_sha256(tampered_alerts)

    ledger_path = tmp_path / "approved_checksums_ledger.jsonl"
    ledger_path.write_text(
        json.dumps(
            {
                "timestamp": "2026-08-13T00:00:00+00:00",
                "proposal_id": "HIGH_NULL_INTENT_RATE-20260813T000000",
                "old_checksum": load_baseline_checksum(REPO_ROOT_DEFAULT / DEFAULT_CHECKSUM_PATH),
                "new_checksum": tampered_checksum,
                "apply_report_path": "reports/threshold_apply_20260813T000000Z/apply_report.json",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    empty_jsonl = tmp_path / "empty.jsonl"
    findings, _ = run_drift_detection(
        repo_root=REPO_ROOT_DEFAULT,
        manifest_path=REPO_ROOT_DEFAULT / DEFAULT_MANIFEST_PATH,
        checksum_path=REPO_ROOT_DEFAULT / DEFAULT_CHECKSUM_PATH,
        alerts_path=tampered_alerts,
        jsonl_path=empty_jsonl,
        window_minutes=60,
        ledger_path=ledger_path,
    )

    alert_finding = next(f for f in findings if f.category == CATEGORY_ALERT_RULES)
    assert alert_finding.severity == SEVERITY_NONE
    assert overall_drift_exit_code(findings) in (0, 1)


def test_run_drift_detection_flags_overdue_emergency_with_no_followup(tmp_path):
    """Uctan uca: ledger'da vadesi cok gecmis, takipsiz bir acil durum
    girdisi varsa, `run_drift_detection` bunu CATEGORY_EMERGENCY_GOVERNANCE
    CRITICAL bulgusu olarak dondurmelidir (gorev v1.1 madde 4)."""
    ledger_path = tmp_path / "approved_checksums_ledger.jsonl"
    ledger_path.write_text(
        json.dumps(
            {
                "timestamp": "2020-01-01T00:00:00+00:00",
                "proposal_id": "HIGH_NULL_INTENT_RATE-20200101T000000",
                "alert_name": "HIGH_NULL_INTENT_RATE",
                "old_checksum": "aaa",
                "new_checksum": "bbb",
                "apply_report_path": "reports/threshold_apply_20200101T000000Z/apply_report.json",
                "is_emergency": True,
                "retro_review_due_utc": "2020-01-02T00:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    empty_jsonl = tmp_path / "empty.jsonl"
    findings, _ = run_drift_detection(
        repo_root=REPO_ROOT_DEFAULT,
        manifest_path=REPO_ROOT_DEFAULT / DEFAULT_MANIFEST_PATH,
        checksum_path=REPO_ROOT_DEFAULT / DEFAULT_CHECKSUM_PATH,
        alerts_path=REPO_ROOT_DEFAULT / DEFAULT_ALERTS_PATH,
        jsonl_path=empty_jsonl,
        window_minutes=60,
        ledger_path=ledger_path,
    )

    emergency_findings = [f for f in findings if f.category == "emergency_governance"]
    assert len(emergency_findings) == 1
    assert emergency_findings[0].severity == SEVERITY_CRITICAL
    assert overall_drift_exit_code(findings) == 2
