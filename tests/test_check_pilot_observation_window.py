"""`check_pilot_observation_window.py` testleri -- uctan uca CLI
davranisi: `evaluate_pilot_promotion.py::compute_evidence_summary`'yi
(GERCEK kanit tarama mantigi) yeniden kullanarak gozlem penceresi
raporu uretir. Hesaplama mantigi zaten `test_pilot_promotion_core.py::compute_observation_window`
ile deterministik test edildi -- burada odak CLI/dosya-tarama
kablolamasidir."""

from __future__ import annotations

import json
from pathlib import Path

from check_pilot_observation_window import main


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_minimal_criteria(path: Path, **overrides) -> None:
    base = dict(
        observation_min_days=14, min_runs=3, max_false_positive_rate=0.1,
        max_unresolved_critical=0, required_evidence_paths=["reports/x_*/e.json"], blocker_conditions=[],
    )
    base.update(overrides)
    path.write_text(
        json.dumps({"features": {name: base for name in [
            "emergency_chain_matching", "auto_rollback_on_verify_fail", "emergency_legitimacy_required",
        ]}}),
        encoding="utf-8",
    )


def test_main_writes_observation_window_report_for_all_features(tmp_path, monkeypatch):
    criteria_path = tmp_path / "criteria.json"
    _write_minimal_criteria(criteria_path)
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        "sys.argv",
        [
            "check_pilot_observation_window.py", "--repo-root", str(tmp_path), "--criteria-path", str(criteria_path),
            "--output-dir", str(out_dir),
        ],
    )
    exit_code = main()
    assert exit_code == 0
    payload = json.loads((out_dir / "observation_window_report.json").read_text(encoding="utf-8"))
    assert len(payload["windows"]) == 3
    assert {w["feature"] for w in payload["windows"]} == {
        "emergency_chain_matching", "auto_rollback_on_verify_fail", "emergency_legitimacy_required",
    }
    assert (out_dir / "observation_window_report.md").exists()


def test_main_reflects_real_evidence_run_counts(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    criteria_path = tmp_path / "criteria.json"
    _write_minimal_criteria(criteria_path, required_evidence_paths=["reports/x_*/e.json"])
    now = datetime.now(timezone.utc).isoformat()
    _write_json(tmp_path / "reports/x_1/e.json", {"generated_at": now})
    _write_json(tmp_path / "reports/x_2/e.json", {"generated_at": now})
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        "sys.argv",
        [
            "check_pilot_observation_window.py", "--repo-root", str(tmp_path), "--criteria-path", str(criteria_path),
            "--output-dir", str(out_dir),
        ],
    )
    main()
    payload = json.loads((out_dir / "observation_window_report.json").read_text(encoding="utf-8"))
    chain = next(w for w in payload["windows"] if w["feature"] == "emergency_chain_matching")
    assert chain["run_count"] == 2
    assert chain["remaining_runs"] == 1


def test_main_exit_code_two_on_invalid_criteria_manifest(tmp_path, monkeypatch):
    criteria_path = tmp_path / "bad.json"
    criteria_path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "check_pilot_observation_window.py", "--repo-root", str(tmp_path), "--criteria-path", str(criteria_path),
            "--output-dir", str(tmp_path / "out"),
        ],
    )
    assert main() == 2


def test_main_exit_code_two_on_malformed_manifest_structure(tmp_path, monkeypatch):
    criteria_path = tmp_path / "bad_structure.json"
    criteria_path.write_text(json.dumps({"not_features": {}}), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "check_pilot_observation_window.py", "--repo-root", str(tmp_path), "--criteria-path", str(criteria_path),
            "--output-dir", str(tmp_path / "out"),
        ],
    )
    assert main() == 2


def test_main_against_real_repo_criteria_and_default_output_dir(monkeypatch, tmp_path):
    """Gercek repo kriter manifestosuyla calisir (varsayilan --output-dir
    ile) -- bu, evaluate_pilot_promotion.py ile AYNI dosya-tarama
    mantigini gercek repo uzerinde genuinely kullanabildigimizi
    dogrular."""
    monkeypatch.setattr("sys.argv", ["check_pilot_observation_window.py", "--output-dir", str(tmp_path / "out")])
    exit_code = main()
    assert exit_code == 0
    assert (tmp_path / "out" / "observation_window_report.json").exists()
