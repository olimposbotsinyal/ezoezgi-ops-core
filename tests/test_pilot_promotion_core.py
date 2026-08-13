"""`pilot_promotion_core.py` testleri -- gorevin acikca istedigi test
odagi: kriter manifestosu dogrulama + varsayilanlar, degerlendirici
karar matrisi (PROMOTE/EXTEND_PILOT/REJECT), `pilot_flags_state.json`
durum-gecisi (yalnizca PROMOTE olanlar degisir), rollback'in ONCEKI
degerleri BIREBIR geri getirdigi -- tamamen sentetik/deterministik,
gercek dosya sistemi olmadan."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pilot_promotion_core import (
    DECISION_EXTEND_PILOT,
    DECISION_PROMOTE,
    DECISION_REJECT,
    FEATURE_NAMES,
    EvidenceSummary,
    PromotionDecision,
    apply_promotions_to_state,
    compute_observation_window,
    compute_rehearsal_detail,
    default_pilot_flags_state,
    evaluate_feature,
    get_feature_criteria,
    is_feature_promoted,
    is_legitimacy_enforcement_active,
    load_pilot_flags_state,
    overall_exit_code,
    render_flag_apply_report_md,
    render_observation_window_md,
    render_promotion_report_md,
    render_rehearsal_report_md,
    restore_previous_state,
    validate_criteria_manifest,
    validate_fpr_summary_schema,
    validate_legitimacy_report_schema,
    validate_weekly_review_schema,
    write_flag_apply_report,
    write_observation_window_report,
    write_pilot_flags_state,
    write_promotion_report,
    write_rehearsal_report,
)

CRITERIA_PATH = Path("infra/monitoring/governance/pilot_promotion_criteria_v1.json")


@pytest.fixture()
def manifest():
    return json.loads(CRITERIA_PATH.read_text(encoding="utf-8"))


def _criteria(**overrides):
    base = dict(
        observation_min_days=14,
        min_runs=3,
        max_false_positive_rate=0.1,
        max_unresolved_critical=0,
        required_evidence_paths=["some/pattern/*.json"],
        blocker_conditions=[],
    )
    base.update(overrides)
    return base


def _evidence(**overrides):
    base = dict(
        runs=5, observation_days=20.0, false_positive_rate=0.05, unresolved_critical=0,
        blockers_tripped=[], missing_evidence_patterns=[], evidence_files=[],
    )
    base.update(overrides)
    return EvidenceSummary(**base)


# --- Kriter manifestosu dogrulama -----------------------------------------------


def test_real_repo_manifest_is_valid(manifest):
    assert validate_criteria_manifest(manifest) == []


def test_real_repo_manifest_has_all_three_features(manifest):
    assert set(manifest["features"].keys()) == set(FEATURE_NAMES)


def test_validate_criteria_manifest_rejects_missing_features_key():
    errors = validate_criteria_manifest({})
    assert any("features" in e for e in errors)


def test_validate_criteria_manifest_rejects_missing_feature():
    manifest = {"features": {name: _criteria() for name in FEATURE_NAMES if name != "emergency_chain_matching"}}
    errors = validate_criteria_manifest(manifest)
    assert any("emergency_chain_matching" in e for e in errors)


def test_validate_criteria_manifest_rejects_missing_required_field():
    criteria = _criteria()
    del criteria["min_runs"]
    manifest = {"features": {name: criteria for name in FEATURE_NAMES}}
    errors = validate_criteria_manifest(manifest)
    assert any("min_runs" in e for e in errors)


def test_validate_criteria_manifest_rejects_invalid_fp_rate_range():
    manifest = {"features": {name: _criteria(max_false_positive_rate=1.5) for name in FEATURE_NAMES}}
    errors = validate_criteria_manifest(manifest)
    assert any("max_false_positive_rate" in e for e in errors)


def test_validate_criteria_manifest_rejects_non_positive_observation_days():
    manifest = {"features": {name: _criteria(observation_min_days=0) for name in FEATURE_NAMES}}
    errors = validate_criteria_manifest(manifest)
    assert any("observation_min_days" in e for e in errors)


def test_validate_criteria_manifest_handles_non_dict_gracefully():
    errors = validate_criteria_manifest("not a dict")
    assert errors  # bos degil ama exception FIRLATMADI


def test_get_feature_criteria_returns_correct_feature(manifest):
    criteria = get_feature_criteria(manifest, "emergency_chain_matching")
    assert criteria["observation_min_days"] == 14


def test_get_feature_criteria_override_does_not_mutate_manifest(manifest):
    get_feature_criteria(manifest, "emergency_legitimacy_required", observation_days_override=28)
    assert manifest["features"]["emergency_legitimacy_required"]["observation_min_days"] == 28  # zaten 28'di, degismedi


def test_get_feature_criteria_override_changes_returned_value(manifest):
    criteria = get_feature_criteria(manifest, "emergency_chain_matching", observation_days_override=28)
    assert criteria["observation_min_days"] == 28
    # orijinal manifesto DEGISMEMELI
    assert manifest["features"]["emergency_chain_matching"]["observation_min_days"] == 14


# --- Degerlendirici karar matrisi (evaluate_feature) ----------------------------


def test_evaluate_feature_promotes_when_all_criteria_met():
    result = evaluate_feature("emergency_chain_matching", _criteria(), _evidence())
    assert result.decision == DECISION_PROMOTE
    assert result.violated_criteria == []
    assert len(result.fulfilled_criteria) >= 4


def test_evaluate_feature_rejects_when_blocker_tripped_even_if_criteria_met():
    """Blocker'lar KOSULSUZDUR -- sayisal kriterler TAMAMEN karsilansa
    bile REJECT'i geri ALAMAZLAR."""
    result = evaluate_feature("x", _criteria(), _evidence(blockers_tripped=["remote_default_changed"]))
    assert result.decision == DECISION_REJECT
    assert "blocker_condition:remote_default_changed" in result.violated_criteria


def test_evaluate_feature_extends_when_runs_insufficient():
    result = evaluate_feature("x", _criteria(min_runs=10), _evidence(runs=2))
    assert result.decision == DECISION_EXTEND_PILOT
    assert any("min_runs" in v for v in result.violated_criteria)


def test_evaluate_feature_extends_when_observation_window_too_short():
    result = evaluate_feature("x", _criteria(observation_min_days=30), _evidence(observation_days=5.0))
    assert result.decision == DECISION_EXTEND_PILOT
    assert any("observation_min_days" in v for v in result.violated_criteria)


def test_evaluate_feature_extends_when_false_positive_rate_missing():
    """Kanitta false_positive_rate verisi YOKSA (None), bu ASLA PROMOTE'a
    izin vermez -- fabrike edilmis bir oran ASLA varsayilmaz."""
    result = evaluate_feature("x", _criteria(), _evidence(false_positive_rate=None))
    assert result.decision == DECISION_EXTEND_PILOT
    assert any("false_positive_rate verisi YOK" in v for v in result.violated_criteria)


def test_evaluate_feature_extends_when_false_positive_rate_too_high():
    result = evaluate_feature("x", _criteria(max_false_positive_rate=0.1), _evidence(false_positive_rate=0.5))
    assert result.decision == DECISION_EXTEND_PILOT
    assert any("max_false_positive_rate" in v for v in result.violated_criteria)


def test_evaluate_feature_extends_when_unresolved_critical_exceeds_max():
    result = evaluate_feature("x", _criteria(max_unresolved_critical=0), _evidence(unresolved_critical=2))
    assert result.decision == DECISION_EXTEND_PILOT
    assert any("max_unresolved_critical" in v for v in result.violated_criteria)


def test_evaluate_feature_extends_when_evidence_pattern_missing():
    result = evaluate_feature("x", _criteria(), _evidence(missing_evidence_patterns=["reports/foo_*/bar.json"]))
    assert result.decision == DECISION_EXTEND_PILOT
    assert any("required_evidence_paths" in v for v in result.violated_criteria)


def test_evaluate_feature_reports_all_violations_at_once():
    result = evaluate_feature(
        "x", _criteria(min_runs=100, observation_min_days=100, max_unresolved_critical=0),
        _evidence(runs=0, observation_days=0, false_positive_rate=None, unresolved_critical=5),
    )
    assert result.decision == DECISION_EXTEND_PILOT
    assert len(result.violated_criteria) >= 4


def test_promotion_decision_rejects_invalid_decision_value():
    with pytest.raises(ValueError):
        PromotionDecision(feature="x", decision="MAYBE")


# --- Genel exit code haritalamasi ------------------------------------------------


def test_overall_exit_code_all_promote_is_zero():
    decisions = [PromotionDecision(feature=n, decision=DECISION_PROMOTE) for n in FEATURE_NAMES]
    assert overall_exit_code(decisions) == 0


def test_overall_exit_code_any_extend_no_reject_is_one():
    decisions = [
        PromotionDecision(feature="a", decision=DECISION_PROMOTE),
        PromotionDecision(feature="b", decision=DECISION_EXTEND_PILOT),
    ]
    assert overall_exit_code(decisions) == 1


def test_overall_exit_code_any_reject_is_two_even_with_promote():
    decisions = [
        PromotionDecision(feature="a", decision=DECISION_PROMOTE),
        PromotionDecision(feature="b", decision=DECISION_REJECT),
        PromotionDecision(feature="c", decision=DECISION_EXTEND_PILOT),
    ]
    assert overall_exit_code(decisions) == 2


# --- pilot_flags_state.json -- saf durum-gecisi ---------------------------------


def test_default_pilot_flags_state_all_unpromoted():
    state = default_pilot_flags_state()
    assert set(state.keys()) == set(FEATURE_NAMES)
    for name in FEATURE_NAMES:
        assert state[name]["promoted"] is False


def test_is_feature_promoted_false_by_default():
    assert is_feature_promoted(default_pilot_flags_state(), "emergency_chain_matching") is False


def test_apply_promotions_only_changes_promote_decisions():
    state = default_pilot_flags_state()
    decisions = {
        "emergency_chain_matching": DECISION_PROMOTE,
        "auto_rollback_on_verify_fail": DECISION_EXTEND_PILOT,
        "emergency_legitimacy_required": DECISION_REJECT,
    }
    new_state, previous = apply_promotions_to_state(
        state, decisions, generated_at="2026-08-13T00:00:00+00:00", promotion_report_path="r.json"
    )
    assert new_state["emergency_chain_matching"]["promoted"] is True
    assert new_state["auto_rollback_on_verify_fail"]["promoted"] is False
    assert new_state["emergency_legitimacy_required"]["promoted"] is False
    assert list(previous.keys()) == ["emergency_chain_matching"]


def test_apply_promotions_does_not_mutate_original_state():
    state = default_pilot_flags_state()
    apply_promotions_to_state(
        state, {"emergency_chain_matching": DECISION_PROMOTE},
        generated_at="2026-08-13T00:00:00+00:00", promotion_report_path="r.json",
    )
    assert state["emergency_chain_matching"]["promoted"] is False  # orijinal DEGISMEDI


def test_restore_previous_state_reverts_exactly():
    state = default_pilot_flags_state()
    new_state, previous = apply_promotions_to_state(
        state, {"emergency_chain_matching": DECISION_PROMOTE},
        generated_at="2026-08-13T00:00:00+00:00", promotion_report_path="r.json",
    )
    assert is_feature_promoted(new_state, "emergency_chain_matching") is True

    restored = restore_previous_state(new_state, previous)
    assert restored == state


def test_is_legitimacy_enforcement_active_requires_both_env_and_promotion():
    state_not_promoted = default_pilot_flags_state()
    state_promoted, _ = apply_promotions_to_state(
        state_not_promoted, {"emergency_legitimacy_required": DECISION_PROMOTE},
        generated_at="2026-08-13T00:00:00+00:00", promotion_report_path="r.json",
    )

    assert is_legitimacy_enforcement_active(state_not_promoted, "1") is False  # env=1 ama PROMOTE degil
    assert is_legitimacy_enforcement_active(state_promoted, "0") is False  # PROMOTE ama env=0
    assert is_legitimacy_enforcement_active(state_promoted, None) is False
    assert is_legitimacy_enforcement_active(state_promoted, "1") is True  # IKISI de saglaniyor


def test_load_pilot_flags_state_returns_default_for_missing_file(tmp_path):
    state = load_pilot_flags_state(tmp_path / "does_not_exist.json")
    assert state == default_pilot_flags_state()


def test_load_pilot_flags_state_returns_default_for_malformed_json(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not valid json", encoding="utf-8")
    state = load_pilot_flags_state(path)
    assert state == default_pilot_flags_state()


def test_write_then_load_pilot_flags_state_round_trip(tmp_path):
    path = tmp_path / "state.json"
    original = default_pilot_flags_state()
    original["emergency_chain_matching"]["promoted"] = True
    original["emergency_chain_matching"]["promoted_at_utc"] = "2026-08-13T00:00:00+00:00"
    write_pilot_flags_state(path, original)

    loaded = load_pilot_flags_state(path)
    assert loaded == original


# --- Rapor uretimi ---------------------------------------------------------------


def test_render_promotion_report_md_includes_decision_table():
    decisions = [
        PromotionDecision(feature="emergency_chain_matching", decision=DECISION_PROMOTE, rationale="ok"),
        PromotionDecision(feature="auto_rollback_on_verify_fail", decision=DECISION_EXTEND_PILOT, rationale="bekle"),
    ]
    md = render_promotion_report_md(decisions, generated_at="2026-08-13T00:00:00+00:00")
    assert "emergency_chain_matching" in md
    assert "PROMOTE" in md
    assert "EXTEND_PILOT" in md


def test_write_promotion_report_creates_md_and_json(tmp_path):
    decisions = [PromotionDecision(feature="x", decision=DECISION_PROMOTE, rationale="ok")]
    paths = write_promotion_report(decisions, tmp_path / "out", generated_at="2026-08-13T00:00:00+00:00")
    assert paths["md"].exists()
    assert paths["json"].exists()
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["overall_exit_code"] == 0
    assert payload["decisions"][0]["feature"] == "x"


def test_render_flag_apply_report_md_dry_run_notes_no_mutation():
    md = render_flag_apply_report_md(
        {"x": DECISION_PROMOTE}, {"x": False}, generated_at="2026-08-13T00:00:00+00:00", dry_run=True
    )
    assert "DRY-RUN" in md


def test_write_flag_apply_report_serializes_previous_and_new_state(tmp_path):
    prev = default_pilot_flags_state()
    new = default_pilot_flags_state()
    new["x"] = {"promoted": True, "promoted_at_utc": "t", "promotion_report_path": "r.json"}
    paths = write_flag_apply_report(
        {"x": DECISION_PROMOTE}, {"x": True}, tmp_path / "out",
        generated_at="2026-08-13T00:00:00+00:00", dry_run=False, previous_state=prev, new_state=new,
    )
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["new_state"]["x"]["promoted"] is True
    assert payload["dry_run"] is False


# --- Minimum kanit semasi kontrolleri (invalid_evidence_schema) ------------------


def test_validate_fpr_summary_schema_accepts_valid_payload():
    payload = {
        "generated_at": "2026-08-13T00:00:00+00:00",
        "features": {"x": {"total_signals": 1, "adjudicated_signals": 0, "confirmed_false_positives": 0, "false_positive_rate": None, "confidence_band": None, "status": "INSUFFICIENT_DATA"}},
    }
    assert validate_fpr_summary_schema(payload) == []


def test_validate_fpr_summary_schema_rejects_non_dict():
    assert validate_fpr_summary_schema("not a dict") != []


def test_validate_fpr_summary_schema_rejects_missing_generated_at():
    payload = {"features": {}}
    errors = validate_fpr_summary_schema(payload)
    assert any("generated_at" in e for e in errors)


def test_validate_fpr_summary_schema_rejects_missing_features_key():
    payload = {"generated_at": "2026-08-13T00:00:00+00:00"}
    errors = validate_fpr_summary_schema(payload)
    assert any("features" in e for e in errors)


def test_validate_fpr_summary_schema_rejects_missing_required_feature_field():
    payload = {"generated_at": "2026-08-13T00:00:00+00:00", "features": {"x": {"total_signals": 1}}}
    errors = validate_fpr_summary_schema(payload)
    assert any("adjudicated_signals" in e for e in errors)


def test_validate_fpr_summary_schema_rejects_invalid_status():
    payload = {
        "generated_at": "2026-08-13T00:00:00+00:00",
        "features": {"x": {"total_signals": 1, "adjudicated_signals": 0, "confirmed_false_positives": 0, "status": "BOGUS"}},
    }
    errors = validate_fpr_summary_schema(payload)
    assert any("status" in e for e in errors)


def test_validate_fpr_summary_schema_rejects_non_numeric_rate():
    payload = {
        "generated_at": "2026-08-13T00:00:00+00:00",
        "features": {"x": {"total_signals": 1, "adjudicated_signals": 5, "confirmed_false_positives": 0, "status": "COMPUTED", "false_positive_rate": "not a number"}},
    }
    errors = validate_fpr_summary_schema(payload)
    assert any("false_positive_rate" in e for e in errors)


def test_validate_weekly_review_schema_accepts_valid_payload():
    payload = {
        "iso_week": "2026-W33", "generated_at": "2026-08-13T00:00:00+00:00", "status": "GREEN",
        "reasons": [], "fallback_ratio": 0.01, "null_intent_ratio": None, "top_alerts": [],
    }
    assert validate_weekly_review_schema(payload) == []


def test_validate_weekly_review_schema_rejects_non_dict():
    assert validate_weekly_review_schema([1, 2, 3]) != []


def test_validate_weekly_review_schema_rejects_missing_required_field():
    payload = {"iso_week": "2026-W33", "generated_at": "2026-08-13T00:00:00+00:00"}
    errors = validate_weekly_review_schema(payload)
    assert any("status" in e for e in errors)
    assert any("reasons" in e for e in errors)


def test_validate_weekly_review_schema_rejects_non_list_reasons():
    payload = {"iso_week": "x", "generated_at": "t", "status": "GREEN", "reasons": "not a list"}
    errors = validate_weekly_review_schema(payload)
    assert any("reasons" in e for e in errors)


def test_validate_weekly_review_schema_rejects_non_numeric_ratio():
    payload = {"iso_week": "x", "generated_at": "t", "status": "GREEN", "reasons": [], "fallback_ratio": "high"}
    errors = validate_weekly_review_schema(payload)
    assert any("fallback_ratio" in e for e in errors)


def test_validate_legitimacy_report_schema_accepts_valid_pass_payload():
    payload = {"legitimacy_status": "PASS", "provider_evidence": None}
    assert validate_legitimacy_report_schema(payload) == []


def test_validate_legitimacy_report_schema_accepts_valid_provider_evidence():
    payload = {"legitimacy_status": "FAIL", "provider_evidence": {"checked": True, "found": False}}
    assert validate_legitimacy_report_schema(payload) == []


def test_validate_legitimacy_report_schema_rejects_non_dict():
    assert validate_legitimacy_report_schema(42) != []


def test_validate_legitimacy_report_schema_rejects_missing_status():
    errors = validate_legitimacy_report_schema({})
    assert any("legitimacy_status" in e for e in errors)


def test_validate_legitimacy_report_schema_rejects_invalid_status():
    errors = validate_legitimacy_report_schema({"legitimacy_status": "BOGUS"})
    assert any("gecersiz legitimacy_status" in e for e in errors)


def test_validate_legitimacy_report_schema_rejects_malformed_provider_evidence():
    errors = validate_legitimacy_report_schema({"legitimacy_status": "PASS", "provider_evidence": "not a dict"})
    assert any("provider_evidence" in e for e in errors)


def test_validate_legitimacy_report_schema_rejects_provider_evidence_missing_fields():
    errors = validate_legitimacy_report_schema({"legitimacy_status": "PASS", "provider_evidence": {}})
    assert any("checked" in e for e in errors)
    assert any("found" in e for e in errors)


# --- Karar provasi (rehearsal) ----------------------------------------------------


def test_compute_rehearsal_detail_reports_runs_and_days_needed():
    criteria = _criteria(min_runs=5, observation_min_days=14)
    evidence = _evidence(runs=2, observation_days=3.0)
    decision = evaluate_feature("x", criteria, evidence)
    detail = compute_rehearsal_detail("x", criteria, evidence, decision)
    assert detail["runs_needed"] == 3
    assert detail["days_needed"] == 11.0
    assert detail["decision"] == DECISION_EXTEND_PILOT


def test_compute_rehearsal_detail_zero_needed_when_criteria_met():
    criteria = _criteria(min_runs=3, observation_min_days=14)
    evidence = _evidence(runs=5, observation_days=20.0)
    decision = evaluate_feature("x", criteria, evidence)
    detail = compute_rehearsal_detail("x", criteria, evidence, decision)
    assert detail["runs_needed"] == 0
    assert detail["days_needed"] == 0.0
    assert detail["decision"] == DECISION_PROMOTE


def test_compute_rehearsal_detail_includes_blockers_on_reject():
    criteria = _criteria(blocker_conditions=["secrets_committed"])
    evidence = _evidence(blockers_tripped=["secrets_committed"])
    decision = evaluate_feature("x", criteria, evidence)
    detail = compute_rehearsal_detail("x", criteria, evidence, decision)
    assert detail["decision"] == DECISION_REJECT
    assert "secrets_committed" in detail["blockers_tripped"]


def test_compute_rehearsal_detail_reports_fp_rate_missing():
    criteria = _criteria()
    evidence = _evidence(false_positive_rate=None)
    decision = evaluate_feature("x", criteria, evidence)
    detail = compute_rehearsal_detail("x", criteria, evidence, decision)
    assert "veri yok" in detail["false_positive_rate_note"]


def test_compute_rehearsal_detail_includes_skipped_evidence_count():
    criteria = _criteria()
    evidence = _evidence(skipped_evidence_count=2)
    decision = evaluate_feature("x", criteria, evidence)
    detail = compute_rehearsal_detail("x", criteria, evidence, decision)
    assert detail["skipped_evidence_count"] == 2


def test_render_rehearsal_report_md_mentions_no_mutation():
    criteria = _criteria(min_runs=5)
    evidence = _evidence(runs=1)
    decision = evaluate_feature("x", criteria, evidence)
    detail = compute_rehearsal_detail("x", criteria, evidence, decision)
    md = render_rehearsal_report_md([detail], generated_at="2026-08-13T00:00:00+00:00")
    assert "PROVA" in md
    assert "DEGISTIRILMEDI" in md
    assert "4" in md  # runs_needed


def test_render_rehearsal_report_md_notes_skipped_evidence():
    criteria = _criteria()
    evidence = _evidence(skipped_evidence_count=3)
    decision = evaluate_feature("x", criteria, evidence)
    detail = compute_rehearsal_detail("x", criteria, evidence, decision)
    md = render_rehearsal_report_md([detail], generated_at="2026-08-13T00:00:00+00:00")
    assert "3 kanit girdisi SKIPPED" in md


def test_write_rehearsal_report_creates_md_and_json_with_rehearsal_flag(tmp_path):
    criteria = _criteria(min_runs=5)
    evidence = _evidence(runs=1)
    decision = evaluate_feature("x", criteria, evidence)
    detail = compute_rehearsal_detail("x", criteria, evidence, decision)
    paths = write_rehearsal_report([detail], tmp_path / "out", generated_at="2026-08-13T00:00:00+00:00")
    assert paths["md"].exists()
    assert paths["json"].exists()
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["rehearsal"] is True
    assert payload["details"][0]["feature"] == "x"


# --- Gozlem penceresi (observation window) hesaplayicisi -------------------------


def test_compute_observation_window_reports_remaining_runs_and_days():
    criteria = _criteria(min_runs=5, observation_min_days=14)
    evidence = _evidence(runs=2, observation_days=3.0)
    window = compute_observation_window("x", criteria, evidence)
    assert window["run_count"] == 2
    assert window["observed_days"] == 3.0
    assert window["remaining_runs"] == 3
    assert window["remaining_days"] == 11.0
    assert window["window_satisfied"] is False


def test_compute_observation_window_satisfied_when_criteria_met():
    criteria = _criteria(min_runs=3, observation_min_days=14)
    evidence = _evidence(runs=5, observation_days=20.0)
    window = compute_observation_window("x", criteria, evidence)
    assert window["remaining_runs"] == 0
    assert window["remaining_days"] == 0.0
    assert window["window_satisfied"] is True


def test_compute_observation_window_never_reports_negative_remaining():
    """Kanit gerekenden FAZLA olsa bile (`runs > min_runs`), eksik
    sayilar ASLA NEGATIF olmamalidir -- `max(0, ...)` garantisi."""
    criteria = _criteria(min_runs=1, observation_min_days=1)
    evidence = _evidence(runs=100, observation_days=100.0)
    window = compute_observation_window("x", criteria, evidence)
    assert window["remaining_runs"] == 0
    assert window["remaining_days"] == 0.0


def test_compute_observation_window_includes_skipped_evidence_count():
    criteria = _criteria()
    evidence = _evidence(skipped_evidence_count=4)
    window = compute_observation_window("x", criteria, evidence)
    assert window["skipped_evidence_count"] == 4


def test_render_observation_window_md_includes_table_and_status():
    criteria = _criteria(min_runs=5, observation_min_days=14)
    evidence = _evidence(runs=2, observation_days=3.0)
    window = compute_observation_window("emergency_chain_matching", criteria, evidence)
    md = render_observation_window_md([window], generated_at="2026-08-13T00:00:00+00:00")
    assert "emergency_chain_matching" in md
    assert "hayir" in md  # window_satisfied=False


def test_render_observation_window_md_shows_evet_when_satisfied():
    criteria = _criteria(min_runs=1, observation_min_days=1)
    evidence = _evidence(runs=5, observation_days=20.0)
    window = compute_observation_window("x", criteria, evidence)
    md = render_observation_window_md([window], generated_at="2026-08-13T00:00:00+00:00")
    assert "EVET" in md


def test_write_observation_window_report_creates_md_and_json(tmp_path):
    criteria = _criteria(min_runs=5, observation_min_days=14)
    evidence = _evidence(runs=2, observation_days=3.0)
    window = compute_observation_window("x", criteria, evidence)
    paths = write_observation_window_report([window], tmp_path / "out", generated_at="2026-08-13T00:00:00+00:00")
    assert paths["md"].exists()
    assert paths["json"].exists()
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["windows"][0]["feature"] == "x"
    assert payload["windows"][0]["remaining_runs"] == 3
