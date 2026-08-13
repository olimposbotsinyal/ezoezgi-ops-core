"""`observability_drift_core.py` testleri -- drift siddet siniflandirmasi
+ exit code haritalamasi (gorevin acikca istedigi test odagi): sabit,
elle olusturulmus girdilerle -- gercek bir /metrics veya alert kural
dosyasi olmadan tamamen deterministik."""

from __future__ import annotations

import pytest

from observability_drift_core import (
    CATEGORY_ALERT_RULES,
    EXIT_CRITICAL,
    EXIT_NO_DRIFT,
    EXIT_NON_CRITICAL,
    SEVERITY_CRITICAL,
    SEVERITY_NONE,
    SEVERITY_WARN,
    DriftFinding,
    check_alert_rules_checksum_drift,
    check_remote_default_drift,
    check_strict_flag_drift,
    compare_metrics_schema,
    overall_drift_exit_code,
    overall_drift_status,
    render_drift_report_md,
)

BASELINE = {
    "always_allowed_extra_labels": ["synthetic"],
    "business_metrics": {
        "model_gateway_requests_total": {"type": "counter", "labels": ["provider", "result"]},
        "model_gateway_null_intent_total": {"type": "counter", "labels": ["terminal_reason"]},
    },
    "self_observability_metrics": {
        "metrics_sink_write_failures_total": {"type": "counter", "labels": []},
    },
    "config_defaults": {"remote_enabled": False, "ollama_cpu_verify_strict": True},
}


def test_drift_finding_rejects_invalid_severity():
    with pytest.raises(ValueError):
        DriftFinding("x", "NOT_A_SEVERITY", "detay")


def test_overall_exit_code_no_findings_is_no_drift():
    assert overall_drift_exit_code([]) == EXIT_NO_DRIFT
    assert overall_drift_status([]) == SEVERITY_NONE


def test_overall_exit_code_warn_only_is_non_critical():
    findings = [DriftFinding("x", SEVERITY_NONE, "ok"), DriftFinding("y", SEVERITY_WARN, "kucuk fark")]
    assert overall_drift_exit_code(findings) == EXIT_NON_CRITICAL
    assert overall_drift_status(findings) == SEVERITY_WARN


def test_overall_exit_code_critical_wins_over_warn():
    findings = [DriftFinding("x", SEVERITY_WARN, "kucuk"), DriftFinding("y", SEVERITY_CRITICAL, "buyuk")]
    assert overall_drift_exit_code(findings) == EXIT_CRITICAL
    assert overall_drift_status(findings) == SEVERITY_CRITICAL


# --- Metrik semasi drift'i ----------------------------------------------


def test_compare_metrics_schema_no_drift_when_matches_baseline():
    observed = {"model_gateway_requests_total": {"type": "counter", "labels": ["provider", "result"]}}
    findings = compare_metrics_schema(observed, BASELINE)
    assert findings == []


def test_compare_metrics_schema_allows_synthetic_extra_label():
    """'synthetic' etiketi HER ZAMAN izinlidir -- drift SAYILMAZ."""
    observed = {
        "model_gateway_requests_total": {"type": "counter", "labels": ["provider", "result", "synthetic"]}
    }
    findings = compare_metrics_schema(observed, BASELINE)
    assert findings == []


def test_compare_metrics_schema_flags_unknown_metric_as_warn():
    observed = {"totally_unknown_metric": {"type": "counter", "labels": ["x"]}}
    findings = compare_metrics_schema(observed, BASELINE)
    assert len(findings) == 1
    assert findings[0].severity == SEVERITY_WARN
    assert "Bilinmeyen" in findings[0].detail


def test_compare_metrics_schema_flags_unexpected_label_as_warn():
    observed = {
        "model_gateway_null_intent_total": {"type": "counter", "labels": ["terminal_reason", "unexpected"]}
    }
    findings = compare_metrics_schema(observed, BASELINE)
    assert any(f.severity == SEVERITY_WARN and "unexpected" in str(f.evidence) for f in findings)


def test_compare_metrics_schema_flags_missing_expected_label_as_warn():
    observed = {"model_gateway_requests_total": {"type": "counter", "labels": ["provider"]}}  # 'result' eksik
    findings = compare_metrics_schema(observed, BASELINE)
    assert any("result" in str(f.evidence) for f in findings)


def test_compare_metrics_schema_flags_type_change_as_critical():
    observed = {"model_gateway_requests_total": {"type": "gauge", "labels": ["provider", "result"]}}
    findings = compare_metrics_schema(observed, BASELINE)
    assert any(f.severity == SEVERITY_CRITICAL for f in findings)


def test_compare_metrics_schema_never_observed_metric_is_not_drift():
    """Pencerede hic trafik olmadigi icin hic gorulmeyen bir metrik
    (manifestte var ama gozlenen bos) DRIFT SAYILMAZ."""
    findings = compare_metrics_schema({}, BASELINE)
    assert findings == []


# --- Alert kural checksum drift'i ---------------------------------------


def test_alert_checksum_match_is_none_severity():
    finding = check_alert_rules_checksum_drift("abc123", "abc123")
    assert finding.severity == SEVERITY_NONE


def test_alert_checksum_mismatch_is_critical():
    finding = check_alert_rules_checksum_drift("abc123", "def456")
    assert finding.severity == SEVERITY_CRITICAL
    assert finding.evidence["expected_checksum"] == "def456"
    assert finding.evidence["observed_checksum"] == "abc123"


def test_alert_checksum_comparison_is_case_and_whitespace_insensitive():
    finding = check_alert_rules_checksum_drift("ABC123\n", " abc123 ")
    assert finding.severity == SEVERITY_NONE


# --- Onayli-degisiklik istisnasi (approved_checksums_ledger.jsonl entegrasyonu) --


def test_alert_checksum_mismatch_with_matching_approved_checksum_is_none_severity():
    """`apply_threshold_proposal.ps1 -Apply` ile GERCEKTEN onaylanip
    uygulanmis bir degisiklik -- ledger'da kayitli -- CRITICAL DEGIL,
    bilgilendirici (NONE) olmali."""
    finding = check_alert_rules_checksum_drift("newhash123", "oldhash456", approved_checksums=["newhash123"])
    assert finding.severity == SEVERITY_NONE
    assert finding.category == CATEGORY_ALERT_RULES


def test_alert_checksum_mismatch_without_matching_approved_checksum_is_still_critical():
    """Ledger var ama gozlenen checksum ORADA DA yok -- gercekten
    onaysiz bir degisiklik -- CRITICAL kalmali."""
    finding = check_alert_rules_checksum_drift(
        "unexpectedhash789", "oldhash456", approved_checksums=["someotherhash"]
    )
    assert finding.severity == SEVERITY_CRITICAL


def test_alert_checksum_backward_compatible_when_approved_checksums_omitted():
    """Yeni parametre GERIYE UYUMLU -- eski (2-pozisyonel-argumanli)
    cagiranlar (mevcut testler/CLI) DAVRANIS DEGISIKLIGI YASAMAMALI."""
    finding = check_alert_rules_checksum_drift("abc123", "def456")
    assert finding.severity == SEVERITY_CRITICAL


def test_alert_checksum_approved_list_comparison_is_case_and_whitespace_insensitive():
    finding = check_alert_rules_checksum_drift(
        "NewHash123\n", "oldhash456", approved_checksums=[" NEWHASH123 "]
    )
    assert finding.severity == SEVERITY_NONE


# --- Config varsayilan drift'i -------------------------------------------


def test_remote_default_matches_expected_is_none():
    finding = check_remote_default_drift(False, BASELINE)
    assert finding.severity == SEVERITY_NONE


def test_remote_default_flipped_true_is_critical():
    """remote_enabled varsayilaninin True'ya donmesi KRITIK'tir --
    gorev kisitlarini ('remote-default degisikligi yok') ihlal eder."""
    finding = check_remote_default_drift(True, BASELINE)
    assert finding.severity == SEVERITY_CRITICAL
    assert finding.category == "remote_default"


def test_strict_flag_matches_expected_is_none():
    finding = check_strict_flag_drift(True, BASELINE)
    assert finding.severity == SEVERITY_NONE


def test_strict_flag_flipped_false_is_warn_not_critical():
    """STRICT modunun kapatilmasi, kasitli bir operator karari olabilir
    -- WARN'dir, CRITICAL degil (remote_enabled'dan farkli olarak)."""
    finding = check_strict_flag_drift(False, BASELINE)
    assert finding.severity == SEVERITY_WARN


# --- Rapor uretimi ---------------------------------------------------------


def test_render_drift_report_md_no_findings_says_no_drift():
    md = render_drift_report_md([], generated_at="2026-08-13T00:00:00Z", window_label="test")
    assert "NONE" in md
    assert "Hicbir kontrolde drift bulunamadi" in md


def test_render_drift_report_md_includes_findings_and_evidence():
    findings = [
        DriftFinding("metrics_schema", SEVERITY_WARN, "bilinmeyen metrik", evidence={"metric": "x"}),
    ]
    md = render_drift_report_md(findings, generated_at="2026-08-13T00:00:00Z", window_label="test")
    assert "bilinmeyen metrik" in md
    assert '"metric": "x"' in md
