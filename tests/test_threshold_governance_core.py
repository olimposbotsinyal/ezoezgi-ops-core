"""`threshold_governance_core.py` testleri -- sema doğrulama pass/fail
matrisi (gorevin acikca istedigi test odagi) + checksum bütünlüğü +
apply-uygunluk (eligibility) kontrolleri, gerçek proposal/review dosyası
olmadan tamamen deterministik."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from threshold_governance_core import (
    DECISION_APPROVE,
    DECISION_APPROVE_EMERGENCY,
    DECISION_NEEDS_DATA,
    DECISION_REJECT,
    EMERGENCY_MAX_TIMEBOX_HOURS,
    build_proposal,
    build_review_record,
    check_apply_eligibility,
    compute_checksum,
    validate_against_schema,
    validate_emergency_fields,
)

SCHEMA_PATH = Path("infra/monitoring/governance/threshold_proposal_schema_v1.json")


@pytest.fixture()
def schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _sample_proposal(**overrides):
    """NOT: `overrides`, `build_proposal()`'a DOGRUDAN parametre olarak
    geciliyor (post-hoc `.update()` DEGIL) -- aksi halde ornegin
    `alert_name` degistirilirse `proposal_id`/`checksum` ESKI (stale)
    degerde kalir, bu da testin asil olcmek istedigi seyi (GERCEK bir
    farkli proposal) degil, YAPAY/tutarsiz bir proposal uretir."""
    kwargs = dict(
        alert_name="HIGH_NULL_INTENT_RATE",
        source_window="14d",
        current_values={"warn": 0.01, "crit": 0.02},
        proposed_values={"warn": 0.05, "crit": 0.1},
        confidence="HIGH",
        sample_adequacy="YETERLI (300 ornek)",
        risk_note="kucuk orneklem uyarisi",
        evidence_paths=["reports/alert_calibration_x/calibration_v1.json"],
        generated_at_utc="2026-08-13T00:00:00+00:00",
    )
    kwargs.update(overrides)
    return build_proposal(**kwargs)


# --- Gercek repo semasinin gecerliligi -------------------------------------


def test_real_repo_schema_is_well_formed(schema):
    assert schema["type"] == "object"
    required = set(schema["required"])
    assert required == {
        "proposal_id", "generated_at_utc", "source_window", "current_values",
        "proposed_values", "confidence", "sample_adequacy", "risk_note",
        "evidence_paths", "checksum",
    }


# --- Sema dogrulama PASS/FAIL matrisi ---------------------------------------


def test_schema_validation_passes_for_well_formed_proposal(schema):
    proposal = _sample_proposal()
    errors = validate_against_schema(proposal, schema)
    assert errors == []


def test_schema_validation_fails_when_required_field_missing(schema):
    proposal = _sample_proposal()
    del proposal["risk_note"]
    errors = validate_against_schema(proposal, schema)
    assert any("risk_note" in e for e in errors)


def test_schema_validation_fails_for_wrong_type(schema):
    proposal = _sample_proposal()
    proposal["evidence_paths"] = "not-a-list"  # array olmali
    errors = validate_against_schema(proposal, schema)
    assert any("evidence_paths" in e for e in errors)


def test_schema_validation_fails_for_invalid_enum_value(schema):
    proposal = _sample_proposal()
    proposal["confidence"] = "SUPER_HIGH"  # gecerli degil (LOW/MEDIUM/HIGH)
    errors = validate_against_schema(proposal, schema)
    assert any("confidence" in e for e in errors)


def test_schema_validation_fails_for_invalid_source_window(schema):
    proposal = _sample_proposal()
    proposal["source_window"] = "30d"  # gecerli degil (24h/7d/14d)
    errors = validate_against_schema(proposal, schema)
    assert any("source_window" in e for e in errors)


def test_schema_validation_handles_non_dict_instance_gracefully(schema):
    errors = validate_against_schema("not a dict", schema)
    assert errors  # bos degil, ama exception FIRLATMADI


def test_schema_validation_multiple_errors_all_reported(schema):
    proposal = _sample_proposal()
    del proposal["risk_note"]
    proposal["confidence"] = "INVALID"
    errors = validate_against_schema(proposal, schema)
    assert len(errors) >= 2


# --- Checksum butunlugu ------------------------------------------------------


def test_compute_checksum_is_deterministic():
    data = {"a": 1, "b": 2}
    assert compute_checksum(data) == compute_checksum(data)


def test_compute_checksum_ignores_key_order():
    assert compute_checksum({"a": 1, "b": 2}) == compute_checksum({"b": 2, "a": 1})


def test_compute_checksum_excludes_checksum_field_itself():
    data = {"a": 1, "checksum": "irrelevant-old-value"}
    data_without = {"a": 1}
    assert compute_checksum(data) == compute_checksum(data_without)


def test_compute_checksum_changes_when_data_changes():
    assert compute_checksum({"a": 1}) != compute_checksum({"a": 2})


def test_build_proposal_checksum_matches_recomputation():
    proposal = _sample_proposal()
    assert proposal["checksum"] == compute_checksum(proposal)


def test_build_proposal_id_encodes_alert_name_and_timestamp():
    proposal = _sample_proposal()
    assert proposal["proposal_id"].startswith("HIGH_NULL_INTENT_RATE-")


# --- Review record insasi -----------------------------------------------------


def test_build_review_record_links_proposal_checksum():
    proposal = _sample_proposal()
    review = build_review_record(
        reviewer="alice", decision=DECISION_APPROVE, rationale="looks good", proposal=proposal
    )
    assert review["linked_proposal_checksum"] == proposal["checksum"]
    assert review["proposal_id"] == proposal["proposal_id"]


def test_build_review_record_rejects_invalid_decision():
    proposal = _sample_proposal()
    with pytest.raises(ValueError):
        build_review_record(reviewer="alice", decision="MAYBE", rationale="x", proposal=proposal)


@pytest.mark.parametrize("decision", [DECISION_APPROVE, DECISION_REJECT, DECISION_NEEDS_DATA])
def test_build_review_record_accepts_all_valid_decisions(decision):
    proposal = _sample_proposal()
    review = build_review_record(reviewer="alice", decision=decision, rationale="x", proposal=proposal)
    assert review["decision"] == decision


# --- Apply uygunluk (eligibility) kontrolu ------------------------------------


def test_apply_eligible_when_everything_valid(schema):
    proposal = _sample_proposal()
    review = build_review_record(reviewer="alice", decision=DECISION_APPROVE, rationale="x", proposal=proposal)
    result = check_apply_eligibility(proposal, review, schema)
    assert result.eligible is True
    assert result.reasons == []


def test_apply_blocked_when_decision_is_reject(schema):
    proposal = _sample_proposal()
    review = build_review_record(reviewer="alice", decision=DECISION_REJECT, rationale="x", proposal=proposal)
    result = check_apply_eligibility(proposal, review, schema)
    assert result.eligible is False
    assert any("APPROVE degil" in r for r in result.reasons)


def test_apply_blocked_when_decision_is_needs_data(schema):
    proposal = _sample_proposal()
    review = build_review_record(reviewer="alice", decision=DECISION_NEEDS_DATA, rationale="x", proposal=proposal)
    result = check_apply_eligibility(proposal, review, schema)
    assert result.eligible is False


def test_apply_blocked_when_proposal_tampered_after_review(schema):
    """Checksum uyusmazligi -- review'dan SONRA proposal degistirilmis
    (kasitli veya yanlislikla) -- apply ENGELLENIR."""
    proposal = _sample_proposal()
    review = build_review_record(reviewer="alice", decision=DECISION_APPROVE, rationale="x", proposal=proposal)

    tampered_proposal = dict(proposal)
    tampered_proposal["proposed_values"] = {"warn": 0.99, "crit": 0.99}

    result = check_apply_eligibility(tampered_proposal, review, schema)
    assert result.eligible is False
    assert any("checksum uyusmazligi" in r for r in result.reasons)


def test_apply_blocked_when_review_proposal_id_mismatch(schema):
    proposal_a = _sample_proposal()
    proposal_b = _sample_proposal(alert_name="FALLBACK_SPIKE")
    review_for_a = build_review_record(reviewer="alice", decision=DECISION_APPROVE, rationale="x", proposal=proposal_a)

    result = check_apply_eligibility(proposal_b, review_for_a, schema)
    assert result.eligible is False
    assert any("proposal_id" in r for r in result.reasons)


def test_apply_blocked_when_schema_invalid(schema):
    proposal = _sample_proposal()
    del proposal["risk_note"]
    proposal["checksum"] = compute_checksum(proposal)  # checksum'i yeniden hesapla ki checksum kontrolu ayri basarisiz olmasin
    review = build_review_record(reviewer="alice", decision=DECISION_APPROVE, rationale="x", proposal=proposal)

    result = check_apply_eligibility(proposal, review, schema)
    assert result.eligible is False
    assert any("semasi gecersiz" in r for r in result.reasons)


def test_apply_eligibility_reports_all_failures_at_once(schema):
    """Birden fazla sorun varsa (REJECT + tahrif edilmis), TUMU
    raporlanmali -- ilk hatada durmamali."""
    proposal = _sample_proposal()
    review = build_review_record(reviewer="alice", decision=DECISION_REJECT, rationale="x", proposal=proposal)
    tampered = dict(proposal)
    tampered["proposed_values"] = {"warn": 0.5}

    result = check_apply_eligibility(tampered, review, schema)
    assert result.eligible is False
    assert len(result.reasons) >= 2


# --- Acil durum (APPROVE_EMERGENCY) alan dogrulama matrisi (v1.1) --------------


def _emergency_review(proposal, **overrides):
    kwargs = dict(
        reviewer="alice",
        decision=DECISION_APPROVE_EMERGENCY,
        rationale="acil",
        proposal=proposal,
        approved_at_utc="2026-08-13T00:00:00+00:00",
        incident_id="INC-1",
        justification="Prod'da gurultu, hemen mudahale gerekiyor",
        timebox_hours=6,
        retro_review_due_utc="2026-08-15T00:00:00+00:00",
    )
    kwargs.update(overrides)
    return build_review_record(**kwargs)


def test_build_review_record_includes_emergency_fields_for_approve_emergency():
    proposal = _sample_proposal()
    review = _emergency_review(proposal)
    assert review["decision"] == DECISION_APPROVE_EMERGENCY
    assert review["incident_id"] == "INC-1"
    assert review["justification"]
    assert review["timebox_hours"] == 6
    assert review["retro_review_due_utc"] == "2026-08-15T00:00:00+00:00"


def test_build_review_record_omits_emergency_fields_for_normal_approve():
    """Normal APPROVE/REJECT/NEEDS_DATA icin review_record.json'un ALANLARI
    (Commit S/T ile AYNI sekil) DEGISMEMELI -- geriye donuk uyumluluk."""
    proposal = _sample_proposal()
    review = build_review_record(reviewer="alice", decision=DECISION_APPROVE, rationale="x", proposal=proposal)
    assert "incident_id" not in review
    assert "justification" not in review
    assert "timebox_hours" not in review
    assert "retro_review_due_utc" not in review


def test_validate_emergency_fields_passes_for_well_formed_record():
    proposal = _sample_proposal()
    review = _emergency_review(proposal)
    assert validate_emergency_fields(review) == []


def test_validate_emergency_fields_rejects_missing_incident_id():
    proposal = _sample_proposal()
    review = _emergency_review(proposal, incident_id=None)
    errors = validate_emergency_fields(review)
    assert any("incident_id" in e for e in errors)


def test_validate_emergency_fields_rejects_blank_incident_id():
    proposal = _sample_proposal()
    review = _emergency_review(proposal, incident_id="   ")
    errors = validate_emergency_fields(review)
    assert any("incident_id" in e for e in errors)


def test_validate_emergency_fields_rejects_missing_justification():
    proposal = _sample_proposal()
    review = _emergency_review(proposal, justification=None)
    errors = validate_emergency_fields(review)
    assert any("justification" in e for e in errors)


@pytest.mark.parametrize("bad_timebox", [None, "6", True, False, -1, 0])
def test_validate_emergency_fields_rejects_invalid_timebox(bad_timebox):
    proposal = _sample_proposal()
    review = _emergency_review(proposal, timebox_hours=bad_timebox)
    errors = validate_emergency_fields(review)
    assert any("timebox_hours" in e for e in errors)


def test_validate_emergency_fields_rejects_timebox_over_24_hours():
    proposal = _sample_proposal()
    review = _emergency_review(proposal, timebox_hours=EMERGENCY_MAX_TIMEBOX_HOURS + 0.01)
    errors = validate_emergency_fields(review)
    assert any("timebox_hours" in e for e in errors)


def test_validate_emergency_fields_accepts_timebox_exactly_24_hours():
    proposal = _sample_proposal()
    review = _emergency_review(proposal, timebox_hours=EMERGENCY_MAX_TIMEBOX_HOURS)
    assert validate_emergency_fields(review) == []


def test_validate_emergency_fields_rejects_missing_retro_review_due_utc():
    proposal = _sample_proposal()
    review = _emergency_review(proposal, retro_review_due_utc=None)
    errors = validate_emergency_fields(review)
    assert any("retro_review_due_utc" in e for e in errors)


def test_validate_emergency_fields_rejects_malformed_retro_review_due_utc():
    proposal = _sample_proposal()
    review = _emergency_review(proposal, retro_review_due_utc="not-a-date")
    errors = validate_emergency_fields(review)
    assert any("retro_review_due_utc" in e for e in errors)


def test_validate_emergency_fields_rejects_retro_review_due_utc_without_timezone():
    proposal = _sample_proposal()
    review = _emergency_review(proposal, retro_review_due_utc="2026-08-15T00:00:00")
    errors = validate_emergency_fields(review)
    assert any("saat dilimi" in e for e in errors)


def test_validate_emergency_fields_rejects_retro_review_due_utc_before_approved_at():
    proposal = _sample_proposal()
    review = _emergency_review(proposal, retro_review_due_utc="2020-01-01T00:00:00+00:00")
    errors = validate_emergency_fields(review)
    assert any("ONCE olamaz" in e for e in errors)


def test_validate_emergency_fields_reports_all_failures_at_once():
    proposal = _sample_proposal()
    review = _emergency_review(proposal, incident_id=None, justification=None, timebox_hours=48, retro_review_due_utc=None)
    errors = validate_emergency_fields(review)
    assert len(errors) >= 4


# --- Apply uygunlugu (eligibility) -- APPROVE_EMERGENCY entegrasyonu ------------


def test_apply_eligible_for_well_formed_emergency_review(schema):
    proposal = _sample_proposal()
    review = _emergency_review(proposal)
    result = check_apply_eligibility(proposal, review, schema)
    assert result.eligible is True
    assert result.reasons == []


def test_apply_blocked_when_emergency_fields_invalid(schema):
    proposal = _sample_proposal()
    review = _emergency_review(proposal, timebox_hours=48)
    result = check_apply_eligibility(proposal, review, schema)
    assert result.eligible is False
    assert any("timebox_hours" in r for r in result.reasons)


def test_apply_blocked_when_emergency_retro_review_due_missing(schema):
    proposal = _sample_proposal()
    review = _emergency_review(proposal, retro_review_due_utc=None)
    result = check_apply_eligibility(proposal, review, schema)
    assert result.eligible is False
    assert any("retro_review_due_utc" in r for r in result.reasons)


def test_apply_still_blocked_for_arbitrary_unknown_decision(schema):
    """APPROVE_EMERGENCY EKLENMESI, gecersiz/bilinmeyen bir decision'i
    KABUL ETMEYE baslamamali -- yalnizca APPROVE ve APPROVE_EMERGENCY
    gecerlidir."""
    proposal = _sample_proposal()
    review = build_review_record(reviewer="alice", decision=DECISION_NEEDS_DATA, rationale="x", proposal=proposal)
    result = check_apply_eligibility(proposal, review, schema)
    assert result.eligible is False
    assert any("APPROVE degil" in r for r in result.reasons)


def test_apply_eligibility_checksum_check_still_applies_to_emergency_decision(schema):
    """Acil durum yolu, checksum tahrifat korumasini BYPASS ETMEZ --
    yalnizca iki-goz inceleme gereksinimini gevsetir, butunluk kontrolunu
    DEGIL."""
    proposal = _sample_proposal()
    review = _emergency_review(proposal)
    tampered_proposal = dict(proposal)
    tampered_proposal["proposed_values"] = {"warn": 0.99, "crit": 0.99}

    result = check_apply_eligibility(tampered_proposal, review, schema)
    assert result.eligible is False
    assert any("checksum uyusmazligi" in r for r in result.reasons)
