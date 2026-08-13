"""`evaluate_pilot_promotion.py` testleri -- uctan uca CLI davranisi:
GERCEK (ama sentetik, tmp_path altinda kurulmus) kanit dosyalarindan
dogru EvidenceSummary'nin cikarildigi ve buna gore dogru karar
verildigi. Karar mantiginin kendisi `test_pilot_promotion_core.py` ile
zaten deterministik test edildi -- burada odak, dosya TARAMA/toplama
kablolamasidir (glob, generated_at cikarimi, blocker tespiti)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from evaluate_pilot_promotion import compute_evidence_summary, main


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_compute_evidence_summary_counts_runs_and_observation_window(tmp_path):
    criteria = {
        "required_evidence_paths": ["reports/emergency_chain_trial_*/chain_eval.json"],
        "blocker_conditions": [],
    }
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    _write_json(
        tmp_path / "reports/emergency_chain_trial_A/chain_eval.json",
        {"generated_at": "2026-08-01T00:00:00+00:00", "rows": []},
    )
    _write_json(
        tmp_path / "reports/emergency_chain_trial_B/chain_eval.json",
        {"generated_at": "2026-08-10T00:00:00+00:00", "rows": []},
    )

    summary = compute_evidence_summary("emergency_chain_matching", criteria, repo_root=tmp_path, now=now)
    assert summary.runs == 2
    assert summary.observation_days == 19.0  # 2026-08-01 -> 2026-08-20
    assert summary.missing_evidence_patterns == []


def test_compute_evidence_summary_reports_missing_pattern(tmp_path):
    criteria = {"required_evidence_paths": ["reports/does_not_exist_*/x.json"], "blocker_conditions": []}
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    summary = compute_evidence_summary("x", criteria, repo_root=tmp_path, now=now)
    assert summary.runs == 0
    assert summary.missing_evidence_patterns == ["reports/does_not_exist_*/x.json"]


def test_compute_evidence_summary_averages_false_positive_rate_when_present(tmp_path):
    criteria = {"required_evidence_paths": ["reports/x_*/e.json"], "blocker_conditions": []}
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    _write_json(tmp_path / "reports/x_1/e.json", {"generated_at": "2026-08-01T00:00:00+00:00", "false_positive_rate": 0.1})
    _write_json(tmp_path / "reports/x_2/e.json", {"generated_at": "2026-08-02T00:00:00+00:00", "false_positive_rate": 0.3})

    summary = compute_evidence_summary("x", criteria, repo_root=tmp_path, now=now)
    assert summary.false_positive_rate == 0.2


def test_compute_evidence_summary_fp_rate_none_when_absent_everywhere(tmp_path):
    criteria = {"required_evidence_paths": ["reports/x_*/e.json"], "blocker_conditions": []}
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    _write_json(tmp_path / "reports/x_1/e.json", {"generated_at": "2026-08-01T00:00:00+00:00"})

    summary = compute_evidence_summary("x", criteria, repo_root=tmp_path, now=now)
    assert summary.false_positive_rate is None


def test_compute_evidence_summary_detects_remote_default_blocker(tmp_path):
    criteria = {"required_evidence_paths": ["reports/x_*/e.json"], "blocker_conditions": ["remote_default_changed"]}
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    _write_json(tmp_path / "reports/x_1/e.json", {"generated_at": "2026-08-01T00:00:00+00:00"})
    _write_json(
        tmp_path / "reports/drift_20260810T000000Z/drift_report.json",
        {
            "generated_at": "2026-08-10T00:00:00+00:00",
            "findings": [{"category": "remote_default", "severity": "CRITICAL", "detail": "x", "evidence": {}}],
        },
    )

    summary = compute_evidence_summary("x", criteria, repo_root=tmp_path, now=now)
    assert "remote_default_changed" in summary.blockers_tripped


def test_compute_evidence_summary_no_remote_blocker_when_drift_clean(tmp_path):
    criteria = {"required_evidence_paths": ["reports/x_*/e.json"], "blocker_conditions": ["remote_default_changed"]}
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    _write_json(tmp_path / "reports/x_1/e.json", {"generated_at": "2026-08-01T00:00:00+00:00"})
    _write_json(
        tmp_path / "reports/drift_20260810T000000Z/drift_report.json",
        {"generated_at": "2026-08-10T00:00:00+00:00", "findings": []},
    )

    summary = compute_evidence_summary("x", criteria, repo_root=tmp_path, now=now)
    assert summary.blockers_tripped == []


def test_compute_evidence_summary_uses_latest_drift_report_for_unresolved_critical(tmp_path):
    criteria = {"required_evidence_paths": ["reports/x_*/e.json"], "blocker_conditions": []}
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    _write_json(tmp_path / "reports/x_1/e.json", {"generated_at": "2026-08-01T00:00:00+00:00"})
    _write_json(
        tmp_path / "reports/drift_OLD/drift_report.json",
        {
            "generated_at": "2026-08-05T00:00:00+00:00",
            "findings": [{"category": "emergency_governance", "severity": "CRITICAL", "detail": "old", "evidence": {}}],
        },
    )
    _write_json(
        tmp_path / "reports/drift_NEW/drift_report.json",
        {"generated_at": "2026-08-15T00:00:00+00:00", "findings": []},
    )

    summary = compute_evidence_summary("x", criteria, repo_root=tmp_path, now=now)
    assert summary.unresolved_critical == 0  # yalnizca EN SON rapor kullanilmali (temiz)


def test_compute_evidence_summary_provider_stub_only_blocker_trips_when_all_stub(tmp_path):
    criteria = {"required_evidence_paths": ["reports/x_*/e.json"], "blocker_conditions": ["provider_is_stub_only"]}
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    _write_json(tmp_path / "reports/x_1/e.json", {"generated_at": "2026-08-01T00:00:00+00:00", "provider": "mock"})
    _write_json(tmp_path / "reports/x_2/e.json", {"generated_at": "2026-08-02T00:00:00+00:00", "provider": "jira_stub"})

    summary = compute_evidence_summary("emergency_legitimacy_required", criteria, repo_root=tmp_path, now=now)
    assert "provider_is_stub_only" in summary.blockers_tripped


def test_compute_evidence_summary_provider_stub_only_blocker_clears_with_real_provider(tmp_path):
    criteria = {"required_evidence_paths": ["reports/x_*/e.json"], "blocker_conditions": ["provider_is_stub_only"]}
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    _write_json(tmp_path / "reports/x_1/e.json", {"generated_at": "2026-08-01T00:00:00+00:00", "provider": "mock"})
    _write_json(tmp_path / "reports/x_2/e.json", {"generated_at": "2026-08-02T00:00:00+00:00", "provider": "jira_real"})

    summary = compute_evidence_summary("emergency_legitimacy_required", criteria, repo_root=tmp_path, now=now)
    assert "provider_is_stub_only" not in summary.blockers_tripped


# --- Uctan uca CLI --------------------------------------------------------------


def test_main_exit_code_two_on_invalid_criteria_manifest(tmp_path, monkeypatch):
    bad_criteria = tmp_path / "bad_criteria.json"
    bad_criteria.write_text("{not valid json", encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        [
            "evaluate_pilot_promotion.py",
            "--repo-root", str(tmp_path),
            "--criteria-path", str(bad_criteria),
            "--output-dir", str(tmp_path / "out"),
        ],
    )
    exit_code = main()
    assert exit_code == 2


def _write_full_manifest(path: Path, *, min_runs=1, observation_min_days=1, max_fp=0.5):
    features = {}
    for name in ["emergency_chain_matching", "auto_rollback_on_verify_fail", "emergency_legitimacy_required"]:
        features[name] = {
            "observation_min_days": observation_min_days,
            "min_runs": min_runs,
            "max_false_positive_rate": max_fp,
            "max_unresolved_critical": 0,
            "required_evidence_paths": [f"reports/{name}_*/evidence.json"],
            "blocker_conditions": [],
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"features": features}), encoding="utf-8")


def test_main_promotes_all_when_criteria_easily_met(tmp_path, monkeypatch):
    """Determinist uctan uca CLI testi -- kolay gecilebilir esiklerle
    (min_runs=1, observation_min_days=1) TUM ozellikler icin yeterli
    sentetik kanit saglanir, TUMU PROMOTE olmali (exit 0)."""
    criteria_path = tmp_path / "criteria.json"
    _write_full_manifest(criteria_path, min_runs=1, observation_min_days=1, max_fp=0.5)

    for name in ["emergency_chain_matching", "auto_rollback_on_verify_fail", "emergency_legitimacy_required"]:
        _write_json(
            tmp_path / f"reports/{name}_1/evidence.json",
            {"generated_at": "2020-01-01T00:00:00+00:00", "false_positive_rate": 0.0},
        )

    out_dir = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv",
        [
            "evaluate_pilot_promotion.py",
            "--repo-root", str(tmp_path),
            "--criteria-path", str(criteria_path),
            "--output-dir", str(out_dir),
        ],
    )
    exit_code = main()
    assert exit_code == 0

    payload = json.loads((out_dir / "promotion_report.json").read_text(encoding="utf-8"))
    assert all(d["decision"] == "PROMOTE" for d in payload["decisions"])


def test_main_extends_all_when_no_evidence_exists(tmp_path, monkeypatch):
    criteria_path = tmp_path / "criteria.json"
    _write_full_manifest(criteria_path, min_runs=5, observation_min_days=14)

    out_dir = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv",
        [
            "evaluate_pilot_promotion.py",
            "--repo-root", str(tmp_path),
            "--criteria-path", str(criteria_path),
            "--output-dir", str(out_dir),
        ],
    )
    exit_code = main()
    assert exit_code == 1

    payload = json.loads((out_dir / "promotion_report.json").read_text(encoding="utf-8"))
    assert all(d["decision"] == "EXTEND_PILOT" for d in payload["decisions"])
