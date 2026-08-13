"""`threshold_apply_core.py` testleri -- gorevin acikca istedigi test
odagi: dry-run dosya mutasyonu YAPMAZ, apply yedek+ledger+audit-payload
uretir, rollback checksum'i TAM olarak geri yukler (round-trip),
regex yamasi YALNIZCA hedeflenen satiri degistirir (gercek repo
alert dosyasi uzerinde)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from threshold_apply_core import (
    AUTO_ROLLBACK_MODE_SAFE,
    AUTO_ROLLBACK_MODE_STRICT,
    ApplyOutcome,
    RollbackOutcome,
    aggregate_verification_state,
    append_ledger_entry,
    apply_proposal_to_alerts_text,
    build_apply_audit_details,
    build_auto_rollback_result,
    build_fail_check,
    build_ledger_entry,
    build_pass_check,
    build_rollback_audit_details,
    build_skipped_check,
    create_backup,
    decide_auto_rollback,
    load_approved_checksums,
    load_ledger_entries,
    patch_alert_expr_value,
    render_apply_report_md,
    render_rollback_report_md,
    restore_backup,
    write_apply_report,
    write_rollback_report,
)

REAL_ALERTS_PATH = Path("infra/monitoring/prometheus/model_gateway_alerts.yaml")


@pytest.fixture()
def real_alerts_text():
    return REAL_ALERTS_PATH.read_text(encoding="utf-8")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- Regex yamasi -- GERCEK repo dosyasi uzerinde, YALNIZCA hedeflenen satir ----


def test_patch_high_null_intent_warn_changes_only_that_line(real_alerts_text):
    new_text, changed, reason = patch_alert_expr_value(real_alerts_text, "HIGH_NULL_INTENT_RATE", "warn", 0.05)
    assert changed is True
    assert reason is None
    assert "> 0.05" in new_text

    old_lines = real_alerts_text.splitlines()
    new_lines = new_text.splitlines()
    diff_lines = [i for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b]
    assert len(old_lines) == len(new_lines)
    assert len(diff_lines) == 1
    assert "[6h]" in old_lines[diff_lines[0]]


def test_patch_high_null_intent_crit_changes_only_that_line(real_alerts_text):
    new_text, changed, reason = patch_alert_expr_value(real_alerts_text, "HIGH_NULL_INTENT_RATE", "crit", 0.1)
    assert changed is True
    assert reason is None

    old_lines = real_alerts_text.splitlines()
    new_lines = new_text.splitlines()
    diff_lines = [i for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b]
    assert len(diff_lines) == 1
    assert "[1h]" in old_lines[diff_lines[0]]
    assert "> 0.1" in new_lines[diff_lines[0]]


def test_patch_fallback_spike_warn_changes_only_that_line(real_alerts_text):
    new_text, changed, reason = patch_alert_expr_value(real_alerts_text, "FALLBACK_SPIKE", "warn", 5.0)
    assert changed is True
    assert reason is None

    old_lines = real_alerts_text.splitlines()
    new_lines = new_text.splitlines()
    diff_lines = [i for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b]
    assert len(diff_lines) == 1
    assert "(5 * sum" in new_lines[diff_lines[0]]


def test_patch_returns_unchanged_text_for_unknown_alert_name(real_alerts_text):
    new_text, changed, reason = patch_alert_expr_value(real_alerts_text, "NOT_A_REAL_ALERT", "warn", 1.0)
    assert changed is False
    assert new_text == real_alerts_text
    assert reason is not None


def test_patch_returns_unchanged_text_for_unknown_threshold_kind(real_alerts_text):
    new_text, changed, reason = patch_alert_expr_value(real_alerts_text, "HIGH_NULL_INTENT_RATE", "median", 1.0)
    assert changed is False
    assert new_text == real_alerts_text
    assert reason is not None


def test_patch_returns_non_patchable_reason_for_known_non_patchable_alert(real_alerts_text):
    new_text, changed, reason = patch_alert_expr_value(real_alerts_text, "CIRCUIT_OPEN_STUCK", "warn", 1.0)
    assert changed is False
    assert new_text == real_alerts_text
    assert "boolean durum kontrolu" in reason


def test_patch_fails_loud_on_zero_matches():
    text_without_target = "groups:\n  - name: empty\n    rules: []\n"
    new_text, changed, reason = patch_alert_expr_value(text_without_target, "HIGH_NULL_INTENT_RATE", "warn", 0.05)
    assert changed is False
    assert new_text == text_without_target
    assert "0 eslesme" in reason


def test_patch_fails_loud_on_more_than_one_match_when_not_multi_target():
    duplicated_line = "(sum(rate(model_gateway_fallback_total[15m])) > (3.0 * sum(rate(model_gateway_fallback_total[24h])) / (24*4))\n"
    duplicated_text = duplicated_line + duplicated_line

    new_text, changed, reason = patch_alert_expr_value(duplicated_text, "FALLBACK_SPIKE", "warn", 5.0)
    assert changed is False
    assert new_text == duplicated_text
    assert "BEKLENENDEN FAZLA eslesti" in reason
    assert "2 eslesme" in reason


def test_patch_allows_more_than_one_match_when_explicitly_multi_target(monkeypatch):
    from threshold_apply_core import ALERT_EXPR_PATCH_PATTERNS, PatchTarget

    duplicated_line = "(sum(rate(model_gateway_fallback_total[15m])) > (3.0 * sum(rate(model_gateway_fallback_total[24h])) / (24*4))\n"
    duplicated_text = duplicated_line + duplicated_line

    original = ALERT_EXPR_PATCH_PATTERNS["FALLBACK_SPIKE"]["warn"]
    monkeypatch.setitem(
        ALERT_EXPR_PATCH_PATTERNS["FALLBACK_SPIKE"], "warn", PatchTarget(original.pattern, multi_target=True)
    )
    new_text, changed, reason = patch_alert_expr_value(duplicated_text, "FALLBACK_SPIKE", "warn", 5.0)
    assert changed is True
    assert reason is None
    assert new_text.count("(5 * sum") == 2


@pytest.mark.parametrize(
    "value,expected",
    [(0.1, "0.1"), (3.0, "3"), (0.05, "0.05"), (0.123456789, "0.123457"), (0.0, "0")],
)
def test_format_threshold_value_strips_trailing_zeros(value, expected):
    from threshold_apply_core import _format_threshold_value

    assert _format_threshold_value(value) == expected


def test_apply_proposal_to_alerts_text_patches_all_keys_success(real_alerts_text):
    proposal = {"alert_name": "HIGH_NULL_INTENT_RATE", "proposed_values": {"warn": 0.05, "crit": 0.1}}
    new_text, succeeded, failed = apply_proposal_to_alerts_text(real_alerts_text, proposal)
    assert set(succeeded) == {"warn", "crit"}
    assert failed == []
    assert "> 0.05" in new_text
    assert "> 0.1" in new_text


def test_apply_proposal_to_alerts_text_reports_failed_for_unknown_alert(real_alerts_text):
    proposal = {"alert_name": "NOT_A_REAL_ALERT", "proposed_values": {"warn": 0.05}}
    new_text, succeeded, failed = apply_proposal_to_alerts_text(real_alerts_text, proposal)
    assert succeeded == []
    assert len(failed) == 1
    assert failed[0]["kind"] == "warn"
    assert failed[0]["reason"]
    assert new_text == real_alerts_text


def test_apply_proposal_to_alerts_text_is_idempotent_when_reapplied(real_alerts_text):
    proposal = {"alert_name": "HIGH_NULL_INTENT_RATE", "proposed_values": {"warn": 0.05, "crit": 0.1}}
    once_text, _, _ = apply_proposal_to_alerts_text(real_alerts_text, proposal)
    twice_text, succeeded, failed = apply_proposal_to_alerts_text(once_text, proposal)
    assert failed == []
    assert twice_text == once_text


# --- Yedekleme / geri yukleme -- checksum round-trip ----------------------------


def test_create_backup_produces_byte_identical_copy(tmp_path):
    source = tmp_path / "target.yaml"
    source.write_text("orijinal icerik\n", encoding="utf-8")
    backup_dir = tmp_path / "backups"

    backup_path = create_backup(source, backup_dir, label="PROPOSAL-1")

    assert backup_path.name == "target.yaml.PROPOSAL-1.backup"
    assert backup_path.read_bytes() == source.read_bytes()


def test_restore_backup_overwrites_target_with_backup_bytes(tmp_path):
    source = tmp_path / "target.yaml"
    source.write_text("orijinal icerik\n", encoding="utf-8")
    backup_dir = tmp_path / "backups"
    backup_path = create_backup(source, backup_dir, label="PROPOSAL-1")

    source.write_text("degistirilmis icerik\n", encoding="utf-8")
    assert source.read_bytes() != backup_path.read_bytes()

    restore_backup(backup_path, source)
    assert source.read_bytes() == backup_path.read_bytes()


def test_backup_then_apply_then_restore_round_trip_preserves_checksum(tmp_path):
    """`rollback restores exact checksum` -- gercek apply/rollback
    scriptlerinin izledigi TAM akis: checksum al -> yedekle -> yamala
    -> checksum al -> geri yukle -> checksum tekrar al -> ILK checksum
    ile BIREBIR eslesmeli."""
    target = tmp_path / "alerts.yaml"
    target.write_text(REAL_ALERTS_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    backup_dir = tmp_path / "backups"

    old_checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    backup_path = create_backup(target, backup_dir, label="roundtrip")

    patched_text, succeeded, failed = apply_proposal_to_alerts_text(
        target.read_text(encoding="utf-8"),
        {"alert_name": "HIGH_NULL_INTENT_RATE", "proposed_values": {"warn": 0.5, "crit": 0.9}},
    )
    assert failed == []
    target.write_text(patched_text, encoding="utf-8")
    new_checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    assert new_checksum != old_checksum

    restore_backup(backup_path, target)
    restored_checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    assert restored_checksum == old_checksum


# --- Onayli-degisiklik defteri (ledger) -----------------------------------------


def test_append_and_load_approved_checksums_round_trip(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    entry1 = build_ledger_entry(
        proposal_id="A-1", old_checksum="aaa", new_checksum="bbb", apply_report_path="r1.json"
    )
    entry2 = build_ledger_entry(
        proposal_id="A-2", old_checksum="bbb", new_checksum="ccc", apply_report_path="r2.json"
    )
    append_ledger_entry(ledger_path, entry1)
    append_ledger_entry(ledger_path, entry2)

    checksums = load_approved_checksums(ledger_path)
    assert checksums == ["bbb", "ccc"]


def test_load_approved_checksums_returns_empty_list_for_missing_file(tmp_path):
    assert load_approved_checksums(tmp_path / "does_not_exist.jsonl") == []


def test_load_approved_checksums_skips_malformed_lines(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    good_entry = build_ledger_entry(proposal_id="A-1", old_checksum="aaa", new_checksum="bbb", apply_report_path="r1.json")
    ledger_path.write_text(
        json.dumps(good_entry) + "\n" + "{not valid json\n" + json.dumps({"missing": "new_checksum"}) + "\n",
        encoding="utf-8",
    )
    assert load_approved_checksums(ledger_path) == ["bbb"]


def test_build_ledger_entry_contains_expected_fields():
    entry = build_ledger_entry(
        proposal_id="A-1", old_checksum="aaa", new_checksum="bbb", apply_report_path="r1.json", timestamp="2026-08-13T00:00:00+00:00"
    )
    assert entry == {
        "timestamp": "2026-08-13T00:00:00+00:00",
        "proposal_id": "A-1",
        "alert_name": "",
        "old_checksum": "aaa",
        "new_checksum": "bbb",
        "apply_report_path": "r1.json",
        "is_emergency": False,
        "retro_review_due_utc": None,
    }


def test_build_ledger_entry_carries_emergency_fields_when_provided():
    entry = build_ledger_entry(
        proposal_id="A-1", old_checksum="aaa", new_checksum="bbb", apply_report_path="r1.json",
        alert_name="HIGH_NULL_INTENT_RATE", is_emergency=True, retro_review_due_utc="2026-08-15T00:00:00+00:00",
    )
    assert entry["alert_name"] == "HIGH_NULL_INTENT_RATE"
    assert entry["is_emergency"] is True
    assert entry["retro_review_due_utc"] == "2026-08-15T00:00:00+00:00"


# --- Audit detay payload'lari ----------------------------------------------------


def test_build_apply_audit_details_contains_expected_fields(tmp_path):
    proposal = {
        "proposal_id": "A-1", "alert_name": "HIGH_NULL_INTENT_RATE",
        "current_values": {"warn": 0.01}, "proposed_values": {"warn": 0.05},
    }
    review_record = {"reviewer": "alice", "decision": "APPROVE", "rationale": "ok"}
    details = build_apply_audit_details(
        proposal=proposal, review_record=review_record, backup_path=tmp_path / "b.backup",
        patched_kinds=["warn"], old_checksum="aaa", new_checksum="bbb",
    )
    assert details["proposal_id"] == "A-1"
    assert details["reviewer"] == "alice"
    assert details["review_decision"] == "APPROVE"
    assert details["old_file_checksum"] == "aaa"
    assert details["new_file_checksum"] == "bbb"
    assert details["patched_kinds"] == ["warn"]


def test_build_rollback_audit_details_contains_expected_fields(tmp_path):
    details = build_rollback_audit_details(
        proposal_id="A-1", backup_path=tmp_path / "b.backup", restored_checksum="aaa", reason="test"
    )
    assert details["proposal_id"] == "A-1"
    assert details["restored_checksum"] == "aaa"
    assert details["reason"] == "test"


# --- Rapor uretimi ---------------------------------------------------------------


def test_write_apply_report_success_case(tmp_path):
    outcome = ApplyOutcome(
        applied=True, dry_run=False, patched_kinds=["warn", "crit"],
        old_checksum="aaa", new_checksum="bbb", backup_path=str(tmp_path / "b.backup"),
    )
    proposal = {"proposal_id": "A-1", "alert_name": "HIGH_NULL_INTENT_RATE"}
    paths = write_apply_report(outcome, proposal, tmp_path / "out", generated_at="2026-08-13T00:00:00+00:00")

    assert paths["md"].exists()
    assert paths["json"].exists()
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["applied"] is True
    assert payload["dry_run"] is False
    assert payload["old_checksum"] == "aaa"
    assert payload["new_checksum"] == "bbb"
    assert "A-1" in paths["md"].read_text(encoding="utf-8")


def test_write_apply_report_dry_run_case_has_no_mutation_fields(tmp_path):
    outcome = ApplyOutcome(applied=False, dry_run=True, reasons=["DRY-RUN modu"])
    proposal = {"proposal_id": "A-1", "alert_name": "HIGH_NULL_INTENT_RATE"}
    paths = write_apply_report(outcome, proposal, tmp_path / "out", generated_at="2026-08-13T00:00:00+00:00")

    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["applied"] is False
    assert payload["dry_run"] is True
    assert payload["old_checksum"] is None
    assert payload["new_checksum"] is None
    assert "DRY-RUN modu" in render_apply_report_md(outcome, proposal, generated_at="2026-08-13T00:00:00+00:00")


def test_write_rollback_report_success_case(tmp_path):
    outcome = RollbackOutcome(restored=True, restored_checksum="aaa")
    paths = write_rollback_report(outcome, tmp_path / "out", proposal_id="A-1", generated_at="2026-08-13T00:00:00+00:00")

    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["restored"] is True
    assert payload["restored_checksum"] == "aaa"
    assert "A-1" in paths["md"].read_text(encoding="utf-8")


def test_write_rollback_report_failure_case_includes_reasons(tmp_path):
    outcome = RollbackOutcome(restored=False, reasons=["Yedek dosyasi bulunamadi"])
    paths = write_rollback_report(outcome, tmp_path / "out", proposal_id="A-1", generated_at="2026-08-13T00:00:00+00:00")

    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["restored"] is False
    assert "Yedek dosyasi bulunamadi" in payload["reasons"]
    assert "Yedek dosyasi bulunamadi" in render_rollback_report_md(outcome, proposal_id="A-1", generated_at="2026-08-13T00:00:00+00:00")


def test_write_rollback_report_includes_linkage_and_drift_snapshot(tmp_path):
    """Gorev v1.1 madde 5: rollback raporu checksum'in yaninda kaynak
    apply/review baglantisini VE rollback-sonrasi drift anlik
    goruntusunu ICERMELIDIR."""
    outcome = RollbackOutcome(
        restored=True, restored_checksum="aaa",
        source_apply_report_path="reports/threshold_apply_X/apply_report.json",
        source_review_record_path="reports/threshold_reviews/Y/review_record.json",
        post_rollback_drift_status="NONE",
    )
    paths = write_rollback_report(outcome, tmp_path / "out", proposal_id="A-1", generated_at="2026-08-13T00:00:00+00:00")

    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["source_apply_report_path"] == "reports/threshold_apply_X/apply_report.json"
    assert payload["source_review_record_path"] == "reports/threshold_reviews/Y/review_record.json"
    assert payload["post_rollback_drift_status"] == "NONE"
    md = render_rollback_report_md(outcome, proposal_id="A-1", generated_at="2026-08-13T00:00:00+00:00")
    assert "reports/threshold_apply_X/apply_report.json" in md
    assert "NONE" in md


# --- Ledger tam-girdi yukleyici (load_ledger_entries) ---------------------------


def test_load_ledger_entries_returns_full_dicts(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    entry = build_ledger_entry(
        proposal_id="A-1", old_checksum="aaa", new_checksum="bbb", apply_report_path="r1.json",
        alert_name="HIGH_NULL_INTENT_RATE", is_emergency=True, retro_review_due_utc="2026-08-15T00:00:00+00:00",
    )
    append_ledger_entry(ledger_path, entry)

    entries = load_ledger_entries(ledger_path)
    assert len(entries) == 1
    assert entries[0]["alert_name"] == "HIGH_NULL_INTENT_RATE"
    assert entries[0]["is_emergency"] is True


def test_load_ledger_entries_returns_empty_list_for_missing_file(tmp_path):
    assert load_ledger_entries(tmp_path / "does_not_exist.jsonl") == []


def test_load_ledger_entries_skips_malformed_lines(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    good_entry = build_ledger_entry(proposal_id="A-1", old_checksum="aaa", new_checksum="bbb", apply_report_path="r1.json")
    ledger_path.write_text(json.dumps(good_entry) + "\n" + "{not valid json\n", encoding="utf-8")
    entries = load_ledger_entries(ledger_path)
    assert len(entries) == 1
    assert entries[0]["proposal_id"] == "A-1"


# --- VerifyReload sonuc durumu -- PASS / FAIL / VERIFICATION_SKIPPED semantigi --


def test_build_pass_fail_skipped_check_shapes():
    assert build_pass_check("x", "ok") == {"check": "x", "state": "PASS", "reason": "ok"}
    assert build_fail_check("x", "bad") == {"check": "x", "state": "FAIL", "reason": "bad"}
    assert build_skipped_check("x", "n/a") == {"check": "x", "state": "VERIFICATION_SKIPPED", "reason": "n/a"}


def test_aggregate_verification_state_all_pass_is_pass():
    checks = [build_pass_check("a", "ok"), build_pass_check("b", "ok")]
    assert aggregate_verification_state(checks) == "PASS"


def test_aggregate_verification_state_any_fail_is_fail():
    checks = [build_pass_check("a", "ok"), build_fail_check("b", "bozuk"), build_skipped_check("c", "n/a")]
    assert aggregate_verification_state(checks) == "FAIL"


def test_aggregate_verification_state_all_skipped_is_skipped():
    checks = [build_skipped_check("a", "n/a"), build_skipped_check("b", "n/a")]
    assert aggregate_verification_state(checks) == "VERIFICATION_SKIPPED"


def test_aggregate_verification_state_mixed_pass_and_skipped_is_pass():
    """En az bir PASS + geri kalani SKIPPED (hic FAIL yok) -- GENEL
    durum PASS'tir (calisan kontrol basarili, calismayan kontroller
    fabrike edilmedi/dogru sekilde atlandi)."""
    checks = [build_pass_check("a", "ok"), build_skipped_check("b", "n/a")]
    assert aggregate_verification_state(checks) == "PASS"


def test_aggregate_verification_state_empty_list_is_skipped_not_pass():
    """Hicbir kontrol calismadiysa (bos liste), bu ASLA PASS ile
    KARISTIRILAMAZ -- fabrike edilmis bir basari olmamali."""
    assert aggregate_verification_state([]) == "VERIFICATION_SKIPPED"


# --- v1.2 PILOT: auto-rollback karar mantigi (decide_auto_rollback) -------------


def test_auto_rollback_never_triggers_on_pass():
    triggered, reason = decide_auto_rollback(
        verification_state="PASS", auto_rollback_enabled=True, mode=AUTO_ROLLBACK_MODE_STRICT
    )
    assert triggered is False
    assert "PASS" in reason


def test_auto_rollback_never_triggers_on_skipped():
    triggered, reason = decide_auto_rollback(
        verification_state="VERIFICATION_SKIPPED", auto_rollback_enabled=True, mode=AUTO_ROLLBACK_MODE_STRICT
    )
    assert triggered is False
    assert "VERIFICATION_SKIPPED" in reason


def test_auto_rollback_never_triggers_on_none_state():
    triggered, reason = decide_auto_rollback(
        verification_state=None, auto_rollback_enabled=True, mode=AUTO_ROLLBACK_MODE_STRICT
    )
    assert triggered is False


def test_auto_rollback_never_triggers_when_disabled_even_on_fail():
    """Gorev kisiti: 'feature remains OFF by default' -- bayrak KAPALIYSA
    FAIL bile olsa TETIKLENMEMELIDIR."""
    triggered, reason = decide_auto_rollback(
        verification_state="FAIL", auto_rollback_enabled=False, mode=AUTO_ROLLBACK_MODE_STRICT
    )
    assert triggered is False
    assert "KAPALI" in reason


def test_auto_rollback_strict_mode_triggers_on_fail_regardless_of_checksum():
    triggered, reason = decide_auto_rollback(
        verification_state="FAIL", auto_rollback_enabled=True, mode=AUTO_ROLLBACK_MODE_STRICT,
        current_file_checksum="anything", expected_checksum_after_apply="something_else",
    )
    assert triggered is True
    assert "strict" in reason


def test_auto_rollback_safe_mode_triggers_when_checksum_matches():
    triggered, reason = decide_auto_rollback(
        verification_state="FAIL", auto_rollback_enabled=True, mode=AUTO_ROLLBACK_MODE_SAFE,
        current_file_checksum="X", expected_checksum_after_apply="X",
    )
    assert triggered is True
    assert "safe" in reason


def test_auto_rollback_safe_mode_blocks_when_checksum_diverged():
    """Gorev kisiti: safe mod, ARADA baska bir surec/kisi dosyayi
    degistirmisse (checksum artik apply'in yazdigi degerle eslesmiyor),
    o degisikligi EZMEMEK icin tetiklenmemelidir."""
    triggered, reason = decide_auto_rollback(
        verification_state="FAIL", auto_rollback_enabled=True, mode=AUTO_ROLLBACK_MODE_SAFE,
        current_file_checksum="CONCURRENTLY_CHANGED", expected_checksum_after_apply="X",
    )
    assert triggered is False
    assert "BEKLENENDEN FARKLI" in reason


def test_auto_rollback_rejects_invalid_mode():
    triggered, reason = decide_auto_rollback(
        verification_state="FAIL", auto_rollback_enabled=True, mode="yolo",
    )
    assert triggered is False
    assert "gecersiz" in reason


def test_build_auto_rollback_result_shape_when_triggered():
    result = build_auto_rollback_result(triggered=True, decision_reason="test", restored=True, restored_checksum="abc")
    assert result == {
        "triggered": True, "decision_reason": "test", "restored": True, "restored_checksum": "abc",
    }


def test_build_auto_rollback_result_shape_when_not_triggered():
    result = build_auto_rollback_result(triggered=False, decision_reason="test")
    assert result == {
        "triggered": False, "decision_reason": "test", "restored": False, "restored_checksum": None,
    }


def test_apply_outcome_auto_rollback_field_defaults_to_none():
    """Geriye donuk uyumluluk: yeni alan verilmezse mevcut cagricilar
    (Commit T/U/V) DEGISIKLIKSIZ calismaya devam eder."""
    outcome = ApplyOutcome(applied=True, dry_run=False)
    assert outcome.auto_rollback is None


def test_render_apply_report_md_includes_operator_warning_when_triggered():
    outcome = ApplyOutcome(
        applied=True, dry_run=False, verification_state="FAIL",
        auto_rollback=build_auto_rollback_result(triggered=True, decision_reason="x", restored=True, restored_checksum="abc"),
    )
    md = render_apply_report_md(outcome, {"proposal_id": "A-1", "alert_name": "X"}, generated_at="2026-08-13T00:00:00+00:00")
    assert "OPERATOR UYARISI" in md
    assert "OTOMATIK GERI ALMA" in md


def test_render_apply_report_md_shows_not_triggered_reason_when_evaluated_but_skipped():
    outcome = ApplyOutcome(
        applied=True, dry_run=False,
        auto_rollback=build_auto_rollback_result(triggered=False, decision_reason="safe mod engelledi"),
    )
    md = render_apply_report_md(outcome, {"proposal_id": "A-1", "alert_name": "X"}, generated_at="2026-08-13T00:00:00+00:00")
    assert "safe mod engelledi" in md
    assert "OPERATOR UYARISI" not in md


def test_write_apply_report_serializes_auto_rollback_field(tmp_path):
    outcome = ApplyOutcome(
        applied=True, dry_run=False,
        auto_rollback=build_auto_rollback_result(triggered=True, decision_reason="x", restored=True, restored_checksum="abc"),
    )
    paths = write_apply_report(outcome, {"proposal_id": "A-1", "alert_name": "X"}, tmp_path / "out", generated_at="2026-08-13T00:00:00+00:00")
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["auto_rollback"]["triggered"] is True
    assert payload["auto_rollback"]["restored_checksum"] == "abc"
