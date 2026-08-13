"""`promotion_drill_core.py` testleri -- kontrollu VerifyReload FAIL +
auto-rollback drill kanit AYRISTIRICISI/DOGRULAYICISI. Tamamen
deterministik -- gercek bir apply/rollback calistirmasi veya ag
cagrisi GEREKMEZ."""

from __future__ import annotations

import json

from promotion_drill_core import (
    find_matching_audit_entry,
    parse_drill_evidence,
    render_drill_summary_md,
    validate_drill_acceptance,
    write_drill_package,
)

VALID_APPLY_REPORT = {
    "proposal_id": "X-20260813T000000",
    "generated_at": "2026-08-13T18:04:43.999833+00:00",
    "verification_state": "FAIL",
    "old_checksum": "AAA",
    "new_checksum": "BBB",
    "auto_rollback": {"triggered": True, "restored": True, "restored_checksum": "AAA"},
}


def test_parse_drill_evidence_extracts_all_fields():
    parsed = parse_drill_evidence(VALID_APPLY_REPORT)
    assert parsed["proposal_id"] == "X-20260813T000000"
    assert parsed["verification_state"] == "FAIL"
    assert parsed["auto_rollback_triggered"] is True
    assert parsed["auto_rollback_restored"] is True
    assert parsed["auto_rollback_restored_checksum"] == "AAA"


def test_parse_drill_evidence_handles_missing_auto_rollback_block():
    parsed = parse_drill_evidence({"proposal_id": "x", "verification_state": "PASS"})
    assert parsed["auto_rollback_triggered"] is None
    assert parsed["auto_rollback_restored"] is None


def test_validate_drill_acceptance_accepts_valid_evidence():
    parsed = parse_drill_evidence(VALID_APPLY_REPORT)
    accepted, violations = validate_drill_acceptance(parsed)
    assert accepted is True
    assert violations == []


def test_validate_drill_acceptance_rejects_wrong_verification_state():
    payload = dict(VALID_APPLY_REPORT, verification_state="PASS")
    parsed = parse_drill_evidence(payload)
    accepted, violations = validate_drill_acceptance(parsed)
    assert accepted is False
    assert any("verification_state" in v for v in violations)


def test_validate_drill_acceptance_rejects_not_triggered():
    payload = json.loads(json.dumps(VALID_APPLY_REPORT))
    payload["auto_rollback"]["triggered"] = False
    parsed = parse_drill_evidence(payload)
    accepted, violations = validate_drill_acceptance(parsed)
    assert accepted is False
    assert any("triggered" in v for v in violations)


def test_validate_drill_acceptance_rejects_not_restored():
    payload = json.loads(json.dumps(VALID_APPLY_REPORT))
    payload["auto_rollback"]["restored"] = False
    parsed = parse_drill_evidence(payload)
    accepted, violations = validate_drill_acceptance(parsed)
    assert accepted is False
    assert any("restored" in v for v in violations)


def test_validate_drill_acceptance_rejects_checksum_mismatch():
    payload = json.loads(json.dumps(VALID_APPLY_REPORT))
    payload["auto_rollback"]["restored_checksum"] = "DIFFERENT-CHECKSUM"
    parsed = parse_drill_evidence(payload)
    accepted, violations = validate_drill_acceptance(parsed)
    assert accepted is False
    assert any("checksum" in v for v in violations)


def test_validate_drill_acceptance_rejects_missing_checksums():
    parsed = parse_drill_evidence({"verification_state": "FAIL", "auto_rollback": {"triggered": True, "restored": True}})
    accepted, violations = validate_drill_acceptance(parsed)
    assert accepted is False
    assert any("checksum" in v for v in violations)


def test_validate_drill_acceptance_reports_multiple_violations_at_once():
    parsed = parse_drill_evidence({"verification_state": "PASS"})
    accepted, violations = validate_drill_acceptance(parsed)
    assert accepted is False
    assert len(violations) >= 3  # verification_state + triggered + restored (+ checksum)


# --- find_matching_audit_entry ----------------------------------------------------


def test_find_matching_audit_entry_finds_exact_match():
    entries = [
        {"task": "other_task", "details": {"proposal_id": "X"}},
        {"task": "auto_rollback_triggered", "details": {"proposal_id": "X"}, "timestamp": "t1"},
    ]
    result = find_matching_audit_entry(entries, "X")
    assert result["timestamp"] == "t1"


def test_find_matching_audit_entry_none_when_no_match():
    entries = [{"task": "auto_rollback_triggered", "details": {"proposal_id": "OTHER"}}]
    assert find_matching_audit_entry(entries, "X") is None


def test_find_matching_audit_entry_none_when_proposal_id_is_none():
    entries = [{"task": "auto_rollback_triggered", "details": {"proposal_id": None}}]
    assert find_matching_audit_entry(entries, None) is None


def test_find_matching_audit_entry_ignores_wrong_task_name():
    entries = [{"task": "pilot_flag_promoted", "details": {"proposal_id": "X"}}]
    assert find_matching_audit_entry(entries, "X") is None


def test_find_matching_audit_entry_picks_last_when_multiple_match():
    entries = [
        {"task": "auto_rollback_triggered", "details": {"proposal_id": "X"}, "timestamp": "t1"},
        {"task": "auto_rollback_triggered", "details": {"proposal_id": "X"}, "timestamp": "t2"},
    ]
    result = find_matching_audit_entry(entries, "X")
    assert result["timestamp"] == "t2"


# --- render/write ------------------------------------------------------------------


def test_render_drill_summary_md_discloses_non_fresh_run():
    parsed = parse_drill_evidence(VALID_APPLY_REPORT)
    md = render_drill_summary_md(
        generated_at="t", source_apply_report_path="path/to/apply_report.json", source_generated_at="2026-08-13T18:04:43+00:00",
        parsed=parsed, accepted=True, violations=[], audit_entry={"timestamp": "t1", "status": "ROLLED_BACK"},
        is_fresh_run=False,
    )
    assert "SEFFAFLIK NOTU" in md
    assert "TAZE bir drill DEGILDIR" in md
    assert "KABUL EDILDI" in md


def test_render_drill_summary_md_fresh_run_has_no_transparency_caveat():
    parsed = parse_drill_evidence(VALID_APPLY_REPORT)
    md = render_drill_summary_md(
        generated_at="t", source_apply_report_path="path", source_generated_at="t", parsed=parsed,
        accepted=True, violations=[], audit_entry=None, is_fresh_run=True,
    )
    assert "SEFFAFLIK NOTU" not in md
    assert "TAZE bir drill'dir" in md


def test_render_drill_summary_md_shows_violations_when_rejected():
    parsed = parse_drill_evidence({"verification_state": "PASS"})
    md = render_drill_summary_md(
        generated_at="t", source_apply_report_path="p", source_generated_at=None, parsed=parsed,
        accepted=False, violations=["verification_state='PASS' (beklenen: 'FAIL')"], audit_entry=None, is_fresh_run=False,
    )
    assert "REDDEDILDI" in md
    assert "verification_state='PASS'" in md


def test_write_drill_package_creates_all_expected_files(tmp_path):
    parsed = parse_drill_evidence(VALID_APPLY_REPORT)
    paths = write_drill_package(
        tmp_path / "out", generated_at="t", source_apply_report_path="p", source_generated_at="t",
        source_payload=VALID_APPLY_REPORT, parsed=parsed, accepted=True, violations=[],
        audit_entry={"timestamp": "t1"}, is_fresh_run=False,
    )
    for key in ("drill_evidence", "audit_snippet", "checksum_proof", "summary_md", "summary_json"):
        assert paths[key].exists()

    checksum_proof = json.loads(paths["checksum_proof"].read_text(encoding="utf-8"))
    assert checksum_proof["checksum_matches_pre_apply_state"] is True

    summary = json.loads(paths["summary_json"].read_text(encoding="utf-8"))
    assert summary["accepted"] is True
    assert summary["audit_entry_found"] is True
    assert summary["is_fresh_run"] is False
