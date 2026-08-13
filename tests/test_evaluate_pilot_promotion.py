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

from evaluate_pilot_promotion import (
    REPO_ROOT_DEFAULT,
    check_classify_contract_drift,
    compute_evidence_summary,
    main,
)


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

    summary = compute_evidence_summary("emergency_chain_matching", criteria, repo_root=tmp_path, now=now, secrets_found=[], contract_changed=False)
    assert summary.runs == 2
    assert summary.observation_days == 19.0  # 2026-08-01 -> 2026-08-20
    assert summary.missing_evidence_patterns == []


def test_compute_evidence_summary_reports_missing_pattern(tmp_path):
    criteria = {"required_evidence_paths": ["reports/does_not_exist_*/x.json"], "blocker_conditions": []}
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    summary = compute_evidence_summary("x", criteria, repo_root=tmp_path, now=now, secrets_found=[], contract_changed=False)
    assert summary.runs == 0
    assert summary.missing_evidence_patterns == ["reports/does_not_exist_*/x.json"]


def test_compute_evidence_summary_averages_false_positive_rate_when_present(tmp_path):
    criteria = {"required_evidence_paths": ["reports/x_*/e.json"], "blocker_conditions": []}
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    _write_json(tmp_path / "reports/x_1/e.json", {"generated_at": "2026-08-01T00:00:00+00:00", "false_positive_rate": 0.1})
    _write_json(tmp_path / "reports/x_2/e.json", {"generated_at": "2026-08-02T00:00:00+00:00", "false_positive_rate": 0.3})

    summary = compute_evidence_summary("x", criteria, repo_root=tmp_path, now=now, secrets_found=[], contract_changed=False)
    assert summary.false_positive_rate == 0.2


def test_compute_evidence_summary_fp_rate_none_when_absent_everywhere(tmp_path):
    criteria = {"required_evidence_paths": ["reports/x_*/e.json"], "blocker_conditions": []}
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    _write_json(tmp_path / "reports/x_1/e.json", {"generated_at": "2026-08-01T00:00:00+00:00"})

    summary = compute_evidence_summary("x", criteria, repo_root=tmp_path, now=now, secrets_found=[], contract_changed=False)
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

    summary = compute_evidence_summary("x", criteria, repo_root=tmp_path, now=now, secrets_found=[], contract_changed=False)
    assert "remote_default_changed" in summary.blockers_tripped


def test_compute_evidence_summary_no_remote_blocker_when_drift_clean(tmp_path):
    criteria = {"required_evidence_paths": ["reports/x_*/e.json"], "blocker_conditions": ["remote_default_changed"]}
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    _write_json(tmp_path / "reports/x_1/e.json", {"generated_at": "2026-08-01T00:00:00+00:00"})
    _write_json(
        tmp_path / "reports/drift_20260810T000000Z/drift_report.json",
        {"generated_at": "2026-08-10T00:00:00+00:00", "findings": []},
    )

    summary = compute_evidence_summary("x", criteria, repo_root=tmp_path, now=now, secrets_found=[], contract_changed=False)
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

    summary = compute_evidence_summary("x", criteria, repo_root=tmp_path, now=now, secrets_found=[], contract_changed=False)
    assert summary.unresolved_critical == 0  # yalnizca EN SON rapor kullanilmali (temiz)


def test_compute_evidence_summary_provider_stub_only_blocker_trips_when_all_stub(tmp_path):
    criteria = {"required_evidence_paths": ["reports/x_*/e.json"], "blocker_conditions": ["provider_is_stub_only"]}
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    _write_json(tmp_path / "reports/x_1/e.json", {"generated_at": "2026-08-01T00:00:00+00:00", "provider": "mock"})
    _write_json(tmp_path / "reports/x_2/e.json", {"generated_at": "2026-08-02T00:00:00+00:00", "provider": "jira_stub"})

    summary = compute_evidence_summary("emergency_legitimacy_required", criteria, repo_root=tmp_path, now=now, secrets_found=[], contract_changed=False)
    assert "provider_is_stub_only" in summary.blockers_tripped


def test_compute_evidence_summary_provider_stub_only_blocker_clears_with_real_provider(tmp_path):
    criteria = {"required_evidence_paths": ["reports/x_*/e.json"], "blocker_conditions": ["provider_is_stub_only"]}
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    _write_json(tmp_path / "reports/x_1/e.json", {"generated_at": "2026-08-01T00:00:00+00:00", "provider": "mock"})
    _write_json(
        tmp_path / "reports/x_2/e.json",
        {
            "generated_at": "2026-08-02T00:00:00+00:00", "provider": "jira_real",
            "provider_evidence": {"checked": True, "found": True},
        },
    )

    summary = compute_evidence_summary("emergency_legitimacy_required", criteria, repo_root=tmp_path, now=now, secrets_found=[], contract_changed=False)
    assert "provider_is_stub_only" not in summary.blockers_tripped


def test_compute_evidence_summary_provider_stub_only_blocker_trips_when_real_provider_unchecked(tmp_path):
    """Kritik semantik duzeltme: provider alani stub-disi ('jira') olsa
    BILE, provider_evidence.checked=false ise (ornegin ortam
    yapilandirilmamis, SKIPPED donmus) bu blocker'i TEMIZLEMEMELIDIR --
    'no implicit pass on unchecked provider' ilkesi burada da gecerlidir."""
    criteria = {"required_evidence_paths": ["reports/x_*/e.json"], "blocker_conditions": ["provider_is_stub_only"]}
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    _write_json(
        tmp_path / "reports/x_1/e.json",
        {
            "generated_at": "2026-08-01T00:00:00+00:00", "provider": "jira",
            "provider_evidence": {"checked": False, "found": False},
        },
    )

    summary = compute_evidence_summary("emergency_legitimacy_required", criteria, repo_root=tmp_path, now=now, secrets_found=[], contract_changed=False)
    assert "provider_is_stub_only" in summary.blockers_tripped


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


# --- v1.2 evidence maturity: fpr_summary.json / review.json tuketimi -----------


def test_compute_evidence_summary_prefers_fpr_summary_over_per_file_average(tmp_path):
    criteria = {
        "required_evidence_paths": ["reports/x_*/e.json"],
        "blocker_conditions": [],
    }
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    # Kanit dosyasinda gomulu false_positive_rate=0.5 var (Commit Y davranisi) --
    # ama fpr_summary.json BASKA (dogru) bir deger soyluyor, o ONCELIKLI olmali.
    _write_json(tmp_path / "reports/x_1/e.json", {"generated_at": "2026-08-01T00:00:00+00:00", "false_positive_rate": 0.5})
    fpr_summary = {
        "generated_at": "2026-08-15T00:00:00+00:00",
        "features": {"emergency_chain_matching": {"status": "COMPUTED", "false_positive_rate": 0.1}},
    }

    summary = compute_evidence_summary(
        "emergency_chain_matching", criteria, repo_root=tmp_path, now=now,
        secrets_found=[], contract_changed=False, fpr_summary=fpr_summary,
    )
    assert summary.false_positive_rate == 0.1


def test_compute_evidence_summary_falls_back_to_per_file_average_without_fpr_summary(tmp_path):
    """Geriye donuk uyumluluk: fpr_summary hic verilmezse (None), eski
    (Commit Y) davranis -- kanit dosyalarina gomulu degerlerin
    ortalamasi -- korunur."""
    criteria = {"required_evidence_paths": ["reports/x_*/e.json"], "blocker_conditions": []}
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    _write_json(tmp_path / "reports/x_1/e.json", {"generated_at": "2026-08-01T00:00:00+00:00", "false_positive_rate": 0.2})

    summary = compute_evidence_summary(
        "x", criteria, repo_root=tmp_path, now=now, secrets_found=[], contract_changed=False, fpr_summary=None,
    )
    assert summary.false_positive_rate == 0.2


def test_compute_evidence_summary_fpr_summary_insufficient_data_yields_none(tmp_path):
    criteria = {"required_evidence_paths": ["reports/x_*/e.json"], "blocker_conditions": []}
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    _write_json(tmp_path / "reports/x_1/e.json", {"generated_at": "2026-08-01T00:00:00+00:00"})
    fpr_summary = {"features": {"x": {"status": "INSUFFICIENT_DATA", "false_positive_rate": None}}}

    summary = compute_evidence_summary(
        "x", criteria, repo_root=tmp_path, now=now, secrets_found=[], contract_changed=False, fpr_summary=fpr_summary,
    )
    assert summary.false_positive_rate is None


def test_find_latest_fpr_summary_picks_most_recent(tmp_path):
    from evaluate_pilot_promotion import _find_latest_fpr_summary

    _write_json(tmp_path / "reports/pilot_metrics_A/fpr_summary.json", {"generated_at": "2026-08-01T00:00:00+00:00", "features": {"tag": "old"}})
    _write_json(tmp_path / "reports/pilot_metrics_B/fpr_summary.json", {"generated_at": "2026-08-10T00:00:00+00:00", "features": {"tag": "new"}})

    result = _find_latest_fpr_summary(tmp_path)
    assert result["features"]["tag"] == "new"


def test_find_latest_fpr_summary_none_when_missing(tmp_path):
    from evaluate_pilot_promotion import _find_latest_fpr_summary

    assert _find_latest_fpr_summary(tmp_path) is None


def test_find_latest_weekly_review_json_picks_most_recent_entry(tmp_path):
    from evaluate_pilot_promotion import _find_latest_weekly_review_json

    path = tmp_path / "reports/weekly_observability_2026-W32/review.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"generated_at": "2026-08-06T00:00:00+00:00", "status": "GREEN"}) + "\n"
        + json.dumps({"generated_at": "2026-08-13T00:00:00+00:00", "status": "YELLOW"}) + "\n",
        encoding="utf-8",
    )
    result = _find_latest_weekly_review_json(tmp_path)
    assert result["status"] == "YELLOW"


def test_find_latest_weekly_review_json_none_when_missing(tmp_path):
    from evaluate_pilot_promotion import _find_latest_weekly_review_json

    assert _find_latest_weekly_review_json(tmp_path) is None


def test_find_latest_observation_window_report_picks_most_recent(tmp_path):
    from evaluate_pilot_promotion import _find_latest_observation_window_report

    _write_json(
        tmp_path / "reports/pilot_promotion_A/observation_window_report.json",
        {"generated_at": "2026-08-01T00:00:00+00:00", "windows": [{"feature": "x", "remaining_runs": 5}]},
    )
    _write_json(
        tmp_path / "reports/pilot_promotion_B/observation_window_report.json",
        {"generated_at": "2026-08-13T00:00:00+00:00", "windows": [{"feature": "x", "remaining_runs": 0}]},
    )
    result = _find_latest_observation_window_report(tmp_path)
    assert result["windows"][0]["remaining_runs"] == 0


def test_find_latest_observation_window_report_none_when_missing(tmp_path):
    from evaluate_pilot_promotion import _find_latest_observation_window_report

    assert _find_latest_observation_window_report(tmp_path) is None


def test_main_references_observation_window_report_when_present(tmp_path, monkeypatch):
    """`evaluate_pilot_promotion.py` (normal mod), EN SON gozlem
    penceresi raporunu bulursa `observation_window_context.json` olarak
    kendi cikti dizinine kopyalamalidir (gorev kisiti: "Evaluator/
    rehearsal should reference this report when present")."""
    criteria_path = tmp_path / "criteria.json"
    criteria_path.write_text(
        json.dumps(
            {
                "features": {
                    name: {
                        "observation_min_days": 1, "min_runs": 1, "max_false_positive_rate": 0.1,
                        "max_unresolved_critical": 0, "required_evidence_paths": ["reports/does_not_exist_*/x.json"],
                        "blocker_conditions": [],
                    }
                    for name in ["emergency_chain_matching", "auto_rollback_on_verify_fail", "emergency_legitimacy_required"]
                }
            }
        ),
        encoding="utf-8",
    )
    _write_json(
        tmp_path / "reports/pilot_promotion_PRIOR/observation_window_report.json",
        {"generated_at": "2026-08-13T00:00:00+00:00", "windows": [{"feature": "x", "remaining_runs": 3}]},
    )
    out_dir = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv",
        [
            "evaluate_pilot_promotion.py", "--repo-root", str(tmp_path), "--criteria-path", str(criteria_path),
            "--skip-secret-scan", "--output-dir", str(out_dir),
        ],
    )
    main()
    payload = json.loads((out_dir / "observation_window_context.json").read_text(encoding="utf-8"))
    assert payload["windows"][0]["remaining_runs"] == 3


def test_main_rehearsal_also_references_observation_window_report(tmp_path, monkeypatch):
    criteria_path = tmp_path / "criteria.json"
    criteria_path.write_text(
        json.dumps(
            {
                "features": {
                    name: {
                        "observation_min_days": 1, "min_runs": 1, "max_false_positive_rate": 0.1,
                        "max_unresolved_critical": 0, "required_evidence_paths": ["reports/does_not_exist_*/x.json"],
                        "blocker_conditions": [],
                    }
                    for name in ["emergency_chain_matching", "auto_rollback_on_verify_fail", "emergency_legitimacy_required"]
                }
            }
        ),
        encoding="utf-8",
    )
    _write_json(
        tmp_path / "reports/pilot_promotion_PRIOR/observation_window_report.json",
        {"generated_at": "2026-08-13T00:00:00+00:00", "windows": [{"feature": "x", "remaining_runs": 7}]},
    )
    out_dir = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv",
        [
            "evaluate_pilot_promotion.py", "--repo-root", str(tmp_path), "--criteria-path", str(criteria_path),
            "--skip-secret-scan", "--rehearsal", "--output-dir", str(out_dir),
        ],
    )
    main()
    payload = json.loads((out_dir / "observation_window_context.json").read_text(encoding="utf-8"))
    assert payload["windows"][0]["remaining_runs"] == 7


def test_main_no_observation_window_context_written_when_absent(tmp_path, monkeypatch):
    criteria_path = tmp_path / "criteria.json"
    criteria_path.write_text(
        json.dumps(
            {
                "features": {
                    name: {
                        "observation_min_days": 1, "min_runs": 1, "max_false_positive_rate": 0.1,
                        "max_unresolved_critical": 0, "required_evidence_paths": ["reports/does_not_exist_*/x.json"],
                        "blocker_conditions": [],
                    }
                    for name in ["emergency_chain_matching", "auto_rollback_on_verify_fail", "emergency_legitimacy_required"]
                }
            }
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv",
        [
            "evaluate_pilot_promotion.py", "--repo-root", str(tmp_path), "--criteria-path", str(criteria_path),
            "--skip-secret-scan", "--output-dir", str(out_dir),
        ],
    )
    main()
    assert not (out_dir / "observation_window_context.json").exists()


# --- v1.2 evidence maturity: gercek blocker kontrolleri -------------------------


def test_scan_repo_for_secrets_finds_nothing_on_clean_synthetic_repo(tmp_path):
    from evaluate_pilot_promotion import scan_repo_for_secrets
    import subprocess

    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    (tmp_path / "clean.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "clean.py"], cwd=str(tmp_path), capture_output=True)

    config_path = tmp_path / "does_not_exist_config.json"
    findings = scan_repo_for_secrets(tmp_path, config_path)
    assert findings == []


def test_scan_repo_for_secrets_finds_genuine_pattern(tmp_path):
    from evaluate_pilot_promotion import scan_repo_for_secrets
    import subprocess

    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    (tmp_path / "leaky.py").write_text('aws_key = "AKIAABCDEFGHIJKLMNOP"\n', encoding="utf-8")
    subprocess.run(["git", "add", "leaky.py"], cwd=str(tmp_path), capture_output=True)

    config_path = tmp_path / "does_not_exist_config.json"
    findings = scan_repo_for_secrets(tmp_path, config_path)
    assert len(findings) == 1
    assert findings[0].pattern_name == "aws_access_key_id"


def test_scan_repo_for_secrets_real_repo_and_real_config_finds_nothing():
    """Regresyon (2026-08-13, Commit AB sirasinda kesfedildi): gercek
    repo + gercek `secret_scan_patterns_v1.json` ile taranirsa,
    `tests/test_secret_scan_core.py`/`tests/test_evaluate_pilot_promotion.py`'nin
    KENDI sentetik-sir test fixture'lari (sahte-ama-desene-uyan
    degerler) 'secrets_committed'i YANLISLIKLA tetikliyordu -- bu,
    evaluator'i HER ZAMAN REJECT'e zorluyordu. Dosya-bazli allowlist
    istisnalari (bkz. `secret_scan_patterns_v1.json`'daki
    `$allowlist_paths_note`) bunu duzeltti; bu test bir regresyonu
    KALICI olarak yakalar."""
    from evaluate_pilot_promotion import scan_repo_for_secrets

    findings = scan_repo_for_secrets(
        REPO_ROOT_DEFAULT, REPO_ROOT_DEFAULT / "infra" / "monitoring" / "governance" / "secret_scan_patterns_v1.json"
    )
    assert findings == []


def test_check_classify_contract_drift_matches_real_repo_baseline():
    result, reason = check_classify_contract_drift(
        REPO_ROOT_DEFAULT, REPO_ROOT_DEFAULT / "infra" / "contracts" / "classify_contract_checksum_v1.txt"
    )
    assert result is False
    assert "eslesiyor" in reason


def test_check_classify_contract_drift_true_when_source_missing(tmp_path):
    result, reason = check_classify_contract_drift(tmp_path, tmp_path / "does_not_exist_baseline.txt")
    assert result is True


def test_check_classify_contract_drift_true_when_signature_changes(tmp_path):
    src_dir = tmp_path / "services" / "tr-en-bridge" / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "ollama_nlu.py").write_text("def classify(x: int) -> int:\n    return x\n", encoding="utf-8")
    baseline_path = tmp_path / "baseline.txt"
    baseline_path.write_text("0" * 64 + "\n", encoding="utf-8")  # kesinlikle YANLIS checksum

    result, reason = check_classify_contract_drift(tmp_path, baseline_path)
    assert result is True
    assert "DEGISMIS" in reason


def test_check_classify_contract_drift_false_when_signature_matches(tmp_path):
    from classify_contract_core import compute_contract_checksum, extract_function_signature

    src_dir = tmp_path / "services" / "tr-en-bridge" / "src"
    src_dir.mkdir(parents=True)
    source = "def classify(x: int) -> int:\n    return x\n"
    (src_dir / "ollama_nlu.py").write_text(source, encoding="utf-8")
    checksum = compute_contract_checksum(extract_function_signature(source, "classify"))
    baseline_path = tmp_path / "baseline.txt"
    baseline_path.write_text(checksum + "\n", encoding="utf-8")

    result, reason = check_classify_contract_drift(tmp_path, baseline_path)
    assert result is False


def test_check_blockers_secrets_committed_trips_when_findings_present():
    from evaluate_pilot_promotion import _check_blockers
    from secret_scan_core import SecretFinding

    findings = [SecretFinding(path="x.py", line_number=1, pattern_name="aws_access_key_id", matched_snippet="AKIA***")]
    tripped = _check_blockers(
        "x", ["secrets_committed"], evidence_payloads=[], drift_report=None,
        secrets_found=findings, contract_changed=False,
    )
    assert "secrets_committed" in tripped


def test_check_blockers_secrets_committed_clear_when_no_findings():
    from evaluate_pilot_promotion import _check_blockers

    tripped = _check_blockers(
        "x", ["secrets_committed"], evidence_payloads=[], drift_report=None,
        secrets_found=[], contract_changed=False,
    )
    assert tripped == []


def test_check_blockers_classify_contract_changed_trips():
    from evaluate_pilot_promotion import _check_blockers

    tripped = _check_blockers(
        "x", ["classify_contract_changed"], evidence_payloads=[], drift_report=None,
        secrets_found=[], contract_changed=True,
    )
    assert "classify_contract_changed" in tripped


# --- SKIPPED legitimacy kaniti: non-promotable ------------------------------------


def test_compute_evidence_summary_excludes_skipped_legitimacy_from_runs(tmp_path):
    """Kritik semantik duzeltme: `legitimacy_status=SKIPPED` olan bir
    legitimacy_report.json, emergency_legitimacy_required icin bir
    'run' olarak SAYILMAMALIDIR -- gercekten kontrol edilmedi."""
    criteria = {
        "required_evidence_paths": ["reports/emergency_legitimacy_*/legitimacy_report.json"],
        "blocker_conditions": [],
    }
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    _write_json(
        tmp_path / "reports/emergency_legitimacy_A/legitimacy_report.json",
        {"generated_at": "2026-08-01T00:00:00+00:00", "legitimacy_status": "SKIPPED", "provider": "jira"},
    )
    _write_json(
        tmp_path / "reports/emergency_legitimacy_B/legitimacy_report.json",
        {"generated_at": "2026-08-02T00:00:00+00:00", "legitimacy_status": "PASS", "provider": "jira"},
    )

    summary = compute_evidence_summary(
        "emergency_legitimacy_required", criteria, repo_root=tmp_path, now=now, secrets_found=[], contract_changed=False
    )
    assert summary.runs == 1
    assert summary.skipped_evidence_count == 1


def test_compute_evidence_summary_all_skipped_legitimacy_yields_zero_runs(tmp_path):
    criteria = {
        "required_evidence_paths": ["reports/emergency_legitimacy_*/legitimacy_report.json"],
        "blocker_conditions": [],
    }
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    _write_json(
        tmp_path / "reports/emergency_legitimacy_A/legitimacy_report.json",
        {"generated_at": "2026-08-01T00:00:00+00:00", "legitimacy_status": "SKIPPED", "provider": "jira"},
    )

    summary = compute_evidence_summary(
        "emergency_legitimacy_required", criteria, repo_root=tmp_path, now=now, secrets_found=[], contract_changed=False
    )
    assert summary.runs == 0
    assert summary.skipped_evidence_count == 1
    assert summary.observation_days == 0.0


def test_compute_evidence_summary_skipped_exclusion_only_applies_to_legitimacy_feature(tmp_path):
    """`legitimacy_status` alani BASKA bir ozelligin kanit dosyasinda
    (ornegin chain_eval.json) GORULSE BILE (olasi degil ama), YALNIZCA
    emergency_legitimacy_required icin ozel muamele gorur."""
    criteria = {"required_evidence_paths": ["reports/x_*/e.json"], "blocker_conditions": []}
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    _write_json(
        tmp_path / "reports/x_1/e.json",
        {"generated_at": "2026-08-01T00:00:00+00:00", "legitimacy_status": "SKIPPED"},
    )

    summary = compute_evidence_summary("emergency_chain_matching", criteria, repo_root=tmp_path, now=now, secrets_found=[], contract_changed=False)
    assert summary.runs == 1
    assert summary.skipped_evidence_count == 0


# --- invalid_evidence_schema blocker + collect_evidence_schema_errors ------------


def test_check_blockers_invalid_evidence_schema_trips():
    from evaluate_pilot_promotion import _check_blockers

    tripped = _check_blockers(
        "x", ["invalid_evidence_schema"], evidence_payloads=[], drift_report=None,
        secrets_found=[], contract_changed=False, evidence_schema_invalid=True,
    )
    assert "invalid_evidence_schema" in tripped


def test_check_blockers_invalid_evidence_schema_clear_by_default():
    from evaluate_pilot_promotion import _check_blockers

    tripped = _check_blockers(
        "x", ["invalid_evidence_schema"], evidence_payloads=[], drift_report=None,
        secrets_found=[], contract_changed=False,
    )
    assert tripped == []


def test_collect_evidence_schema_errors_none_when_all_absent(tmp_path):
    from evaluate_pilot_promotion import collect_evidence_schema_errors

    errors = collect_evidence_schema_errors(tmp_path, fpr_summary=None, weekly_context=None)
    assert errors == []


def test_collect_evidence_schema_errors_detects_invalid_fpr_summary(tmp_path):
    from evaluate_pilot_promotion import collect_evidence_schema_errors

    errors = collect_evidence_schema_errors(tmp_path, fpr_summary={"generated_at": "t"}, weekly_context=None)
    assert any("fpr_summary.json" in e for e in errors)


def test_collect_evidence_schema_errors_detects_invalid_weekly_context(tmp_path):
    from evaluate_pilot_promotion import collect_evidence_schema_errors

    errors = collect_evidence_schema_errors(tmp_path, fpr_summary=None, weekly_context={"iso_week": "x"})
    assert any("review.json" in e for e in errors)


def test_collect_evidence_schema_errors_detects_invalid_legitimacy_report_on_disk(tmp_path):
    from evaluate_pilot_promotion import collect_evidence_schema_errors

    _write_json(tmp_path / "reports/emergency_legitimacy_A/legitimacy_report.json", {"provider": "jira"})

    errors = collect_evidence_schema_errors(tmp_path, fpr_summary=None, weekly_context=None)
    assert any("legitimacy_status" in e for e in errors)


def test_collect_evidence_schema_errors_valid_legitimacy_report_no_errors(tmp_path):
    from evaluate_pilot_promotion import collect_evidence_schema_errors

    _write_json(
        tmp_path / "reports/emergency_legitimacy_A/legitimacy_report.json",
        {"legitimacy_status": "PASS", "provider_evidence": None},
    )

    errors = collect_evidence_schema_errors(tmp_path, fpr_summary=None, weekly_context=None)
    assert errors == []


def test_real_repo_current_evidence_has_no_schema_errors():
    """Gercek repo uzerinde calistirildiginda, MEVCUT kanit dosyalarinin
    (varsa) hicbiri semayi ihlal etmemelidir -- bir regresyon testi."""
    from evaluate_pilot_promotion import (
        _find_latest_fpr_summary,
        _find_latest_weekly_review_json,
        collect_evidence_schema_errors,
    )

    fpr_summary = _find_latest_fpr_summary(REPO_ROOT_DEFAULT)
    weekly_context = _find_latest_weekly_review_json(REPO_ROOT_DEFAULT)
    errors = collect_evidence_schema_errors(REPO_ROOT_DEFAULT, fpr_summary=fpr_summary, weekly_context=weekly_context)
    assert errors == []


# --- --rehearsal CLI modu ----------------------------------------------------------


def test_main_rehearsal_writes_rehearsal_report_not_promotion_report(tmp_path, monkeypatch):
    criteria_path = tmp_path / "criteria.json"
    criteria_path.write_text(
        json.dumps(
            {
                "features": {
                    name: {
                        "observation_min_days": 1, "min_runs": 100, "max_false_positive_rate": 0.1,
                        "max_unresolved_critical": 0, "required_evidence_paths": ["reports/does_not_exist_*/x.json"],
                        "blocker_conditions": [],
                    }
                    for name in ["emergency_chain_matching", "auto_rollback_on_verify_fail", "emergency_legitimacy_required"]
                }
            }
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv",
        [
            "evaluate_pilot_promotion.py", "--repo-root", str(tmp_path), "--criteria-path", str(criteria_path),
            "--skip-secret-scan", "--rehearsal", "--output-dir", str(out_dir),
        ],
    )
    exit_code = main()
    assert exit_code == 1  # hepsi EXTEND_PILOT (min_runs=100 asla karsilanmaz), hicbiri REJECT degil
    assert (out_dir / "rehearsal_report.md").exists()
    assert (out_dir / "rehearsal_report.json").exists()
    assert not (out_dir / "promotion_report.md").exists()
    assert not (out_dir / "promotion_report.json").exists()

    payload = json.loads((out_dir / "rehearsal_report.json").read_text(encoding="utf-8"))
    assert payload["rehearsal"] is True
    assert all(d["runs_needed"] == 100 for d in payload["details"])


def test_main_rehearsal_does_not_mutate_pilot_flags_state(tmp_path, monkeypatch):
    """Gorev kisiti: 'no flag/state mutation'. Rehearsal modu
    `pilot_flags_state.json`'a ASLA yazmaz -- zaten normal modda da
    yazmiyordu (yalnizca `promote_pilot_flags.ps1` yazar), ama bu test
    bunu ACIKCA kilitler."""
    state_path = tmp_path / "infra/monitoring/governance/pilot_flags_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"marker": "untouched"}), encoding="utf-8")

    criteria_path = tmp_path / "criteria.json"
    criteria_path.write_text(
        json.dumps(
            {
                "features": {
                    name: {
                        "observation_min_days": 1, "min_runs": 1, "max_false_positive_rate": 0.1,
                        "max_unresolved_critical": 0, "required_evidence_paths": ["reports/does_not_exist_*/x.json"],
                        "blocker_conditions": [],
                    }
                    for name in ["emergency_chain_matching", "auto_rollback_on_verify_fail", "emergency_legitimacy_required"]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "evaluate_pilot_promotion.py", "--repo-root", str(tmp_path), "--criteria-path", str(criteria_path),
            "--skip-secret-scan", "--rehearsal", "--output-dir", str(tmp_path / "out"),
        ],
    )
    main()
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"marker": "untouched"}
