"""`compute_pilot_false_positive_rate.py` testleri -- sinyal toplama
(GERCEK-sekilli sentetik kanit dosyalarindan) + uctan uca CLI davranisi."""

from __future__ import annotations

import json
from pathlib import Path

from compute_pilot_false_positive_rate import (
    collect_auto_rollback_signals,
    collect_chain_matching_signals,
    collect_legitimacy_signals,
    main,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_collect_chain_matching_signals_finds_broken_chain_rows(tmp_path):
    _write_json(
        tmp_path / "reports/emergency_chain_trial_1/chain_eval.json",
        {"rows": [
            {"proposal_id": "A-1", "v1_2_checksum_chain": "BROKEN_CHAIN"},
            {"proposal_id": "A-2", "v1_2_checksum_chain": "RESOLVED"},
        ]},
    )
    signals = collect_chain_matching_signals(tmp_path)
    assert len(signals) == 1
    assert signals[0].signal_id == "A-1"
    assert signals[0].feature == "emergency_chain_matching"


def test_collect_auto_rollback_signals_finds_triggered_only(tmp_path):
    _write_json(
        tmp_path / "reports/threshold_apply_1/apply_report.json",
        {"proposal_id": "B-1", "auto_rollback": {"triggered": True}},
    )
    _write_json(
        tmp_path / "reports/threshold_apply_2/apply_report.json",
        {"proposal_id": "B-2", "auto_rollback": {"triggered": False}},
    )
    _write_json(
        tmp_path / "reports/threshold_apply_3/apply_report.json",
        {"proposal_id": "B-3", "auto_rollback": None},
    )
    signals = collect_auto_rollback_signals(tmp_path)
    assert len(signals) == 1
    assert signals[0].signal_id == "B-1"


def test_collect_legitimacy_signals_finds_fail_only(tmp_path):
    _write_json(
        tmp_path / "reports/emergency_legitimacy_1/legitimacy_report.json",
        {"incident_id": "OPS-1", "legitimacy_status": "FAIL"},
    )
    _write_json(
        tmp_path / "reports/emergency_legitimacy_2/legitimacy_report.json",
        {"incident_id": "OPS-2", "legitimacy_status": "PASS"},
    )
    signals = collect_legitimacy_signals(tmp_path)
    assert len(signals) == 1
    assert signals[0].signal_id == "OPS-1"


# --- Uctan uca CLI ------------------------------------------------------------------


def test_main_produces_insufficient_data_with_no_adjudications(tmp_path, monkeypatch):
    _write_json(
        tmp_path / "reports/emergency_chain_trial_1/chain_eval.json",
        {"rows": [{"proposal_id": "A-1", "v1_2_checksum_chain": "BROKEN_CHAIN"}]},
    )
    empty_adjudications = tmp_path / "adjudications.json"
    empty_adjudications.write_text(json.dumps({"adjudications": []}), encoding="utf-8")

    out_dir = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv",
        [
            "compute_pilot_false_positive_rate.py",
            "--repo-root", str(tmp_path),
            "--adjudications-path", str(empty_adjudications),
            "--output-dir", str(out_dir),
        ],
    )
    exit_code = main()
    assert exit_code == 0

    payload = json.loads((out_dir / "fpr_summary.json").read_text(encoding="utf-8"))
    assert payload["features"]["emergency_chain_matching"]["status"] == "INSUFFICIENT_DATA"
    assert payload["features"]["emergency_chain_matching"]["total_signals"] == 1


def test_main_computes_rate_with_sufficient_adjudications(tmp_path, monkeypatch):
    for i in range(3):
        _write_json(
            tmp_path / f"reports/emergency_chain_trial_{i}/chain_eval.json",
            {"rows": [{"proposal_id": f"A-{i}", "v1_2_checksum_chain": "BROKEN_CHAIN"}]},
        )
    adjudications_path = tmp_path / "adjudications.json"
    adjudications_path.write_text(
        json.dumps({
            "adjudications": [
                {"feature": "emergency_chain_matching", "signal_id": "A-0", "verdict": "CONFIRMED_FALSE_POSITIVE", "adjudicated_by": "alice", "adjudicated_at_utc": "t"},
                {"feature": "emergency_chain_matching", "signal_id": "A-1", "verdict": "CONFIRMED_TRUE_POSITIVE", "adjudicated_by": "alice", "adjudicated_at_utc": "t"},
                {"feature": "emergency_chain_matching", "signal_id": "A-2", "verdict": "CONFIRMED_TRUE_POSITIVE", "adjudicated_by": "alice", "adjudicated_at_utc": "t"},
            ]
        }),
        encoding="utf-8",
    )

    out_dir = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv",
        [
            "compute_pilot_false_positive_rate.py",
            "--repo-root", str(tmp_path),
            "--adjudications-path", str(adjudications_path),
            "--output-dir", str(out_dir),
        ],
    )
    exit_code = main()
    assert exit_code == 0

    payload = json.loads((out_dir / "fpr_summary.json").read_text(encoding="utf-8"))
    assert payload["features"]["emergency_chain_matching"]["status"] == "COMPUTED"
    assert payload["features"]["emergency_chain_matching"]["false_positive_rate"] == 1 / 3


def test_main_rejects_malformed_adjudications_file(tmp_path, monkeypatch):
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps({"adjudications": [{"feature": "x"}]}), encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        [
            "compute_pilot_false_positive_rate.py",
            "--repo-root", str(tmp_path),
            "--adjudications-path", str(bad_path),
            "--output-dir", str(tmp_path / "out"),
        ],
    )
    exit_code = main()
    assert exit_code == 2


def test_main_handles_missing_adjudications_file_gracefully(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv",
        [
            "compute_pilot_false_positive_rate.py",
            "--repo-root", str(tmp_path),
            "--adjudications-path", str(tmp_path / "does_not_exist.json"),
            "--output-dir", str(out_dir),
        ],
    )
    exit_code = main()
    assert exit_code == 0
    payload = json.loads((out_dir / "fpr_summary.json").read_text(encoding="utf-8"))
    assert all(f["status"] == "INSUFFICIENT_DATA" for f in payload["features"].values())
