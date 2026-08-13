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
    DECISION_NEEDS_DATA,
    DECISION_REJECT,
    build_proposal,
    build_review_record,
    check_apply_eligibility,
    compute_checksum,
    validate_against_schema,
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
