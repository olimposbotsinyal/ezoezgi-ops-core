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
    default_pilot_flags_state,
    evaluate_feature,
    get_feature_criteria,
    is_feature_promoted,
    is_legitimacy_enforcement_active,
    load_pilot_flags_state,
    overall_exit_code,
    render_flag_apply_report_md,
    render_promotion_report_md,
    restore_previous_state,
    validate_criteria_manifest,
    write_flag_apply_report,
    write_pilot_flags_state,
    write_promotion_report,
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
