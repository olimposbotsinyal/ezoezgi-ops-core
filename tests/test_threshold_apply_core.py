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
    ApplyOutcome,
    RollbackOutcome,
    append_ledger_entry,
    apply_proposal_to_alerts_text,
    build_apply_audit_details,
    build_ledger_entry,
    build_rollback_audit_details,
    create_backup,
    load_approved_checksums,
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
    new_text, changed = patch_alert_expr_value(real_alerts_text, "HIGH_NULL_INTENT_RATE", "warn", 0.05)
    assert changed is True
    assert "> 0.05" in new_text

    old_lines = real_alerts_text.splitlines()
    new_lines = new_text.splitlines()
    diff_lines = [i for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b]
    assert len(old_lines) == len(new_lines)
    assert len(diff_lines) == 1
    assert "[6h]" in old_lines[diff_lines[0]]


def test_patch_high_null_intent_crit_changes_only_that_line(real_alerts_text):
    new_text, changed = patch_alert_expr_value(real_alerts_text, "HIGH_NULL_INTENT_RATE", "crit", 0.1)
    assert changed is True

    old_lines = real_alerts_text.splitlines()
    new_lines = new_text.splitlines()
    diff_lines = [i for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b]
    assert len(diff_lines) == 1
    assert "[1h]" in old_lines[diff_lines[0]]
    assert "> 0.1" in new_lines[diff_lines[0]]


def test_patch_fallback_spike_warn_changes_only_that_line(real_alerts_text):
    new_text, changed = patch_alert_expr_value(real_alerts_text, "FALLBACK_SPIKE", "warn", 5.0)
    assert changed is True

    old_lines = real_alerts_text.splitlines()
    new_lines = new_text.splitlines()
    diff_lines = [i for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b]
    assert len(diff_lines) == 1
    assert "(5 * sum" in new_lines[diff_lines[0]]


def test_patch_returns_unchanged_text_for_unknown_alert_name(real_alerts_text):
    new_text, changed = patch_alert_expr_value(real_alerts_text, "NOT_A_REAL_ALERT", "warn", 1.0)
    assert changed is False
    assert new_text == real_alerts_text


def test_patch_returns_unchanged_text_for_unknown_threshold_kind(real_alerts_text):
    new_text, changed = patch_alert_expr_value(real_alerts_text, "HIGH_NULL_INTENT_RATE", "median", 1.0)
    assert changed is False
    assert new_text == real_alerts_text


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
    assert failed == ["warn"]
    assert new_text == real_alerts_text


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
        "old_checksum": "aaa",
        "new_checksum": "bbb",
        "apply_report_path": "r1.json",
    }


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
