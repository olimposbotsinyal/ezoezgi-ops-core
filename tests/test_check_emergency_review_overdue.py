"""`check_emergency_review_overdue.py` testleri -- uctan uca CLI
davranisi: takipsiz vadesi gecmis acil durum VARSA exit 2 + rapor
uretimi, YOKSA exit 0. Mantigin kendisi zaten `test_observability_drift_core.py::check_emergency_review_overdue_drift`
ile deterministik test edildi -- burada yalnizca CLI'nin dogru
girdi/cikti kablolamasini (ledger okuma, rapor yazma, exit code) test
ederiz."""

from __future__ import annotations

import json

from check_emergency_review_overdue import main


def _write_ledger(path, *, is_emergency, retro_review_due_utc):
    entry = {
        "timestamp": "2020-01-01T00:00:00+00:00",
        "proposal_id": "HIGH_NULL_INTENT_RATE-20200101T000000",
        "alert_name": "HIGH_NULL_INTENT_RATE",
        "old_checksum": "aaa",
        "new_checksum": "bbb",
        "apply_report_path": "reports/threshold_apply_20200101T000000Z/apply_report.json",
        "is_emergency": is_emergency,
        "retro_review_due_utc": retro_review_due_utc,
    }
    path.write_text(json.dumps(entry) + "\n", encoding="utf-8")


def test_main_returns_zero_when_no_overdue_emergency(tmp_path, monkeypatch):
    ledger_path = tmp_path / "ledger.jsonl"
    _write_ledger(ledger_path, is_emergency=False, retro_review_due_utc=None)
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        "sys.argv",
        ["check_emergency_review_overdue.py", "--ledger-path", str(ledger_path), "--output-dir", str(out_dir)],
    )
    exit_code = main()
    assert exit_code == 0
    assert (out_dir / "overdue_report.md").exists()
    assert (out_dir / "overdue_report.json").exists()
    payload = json.loads((out_dir / "overdue_report.json").read_text(encoding="utf-8"))
    assert payload["findings"] == []


def test_main_returns_two_when_overdue_emergency_without_followup(tmp_path, monkeypatch):
    ledger_path = tmp_path / "ledger.jsonl"
    _write_ledger(ledger_path, is_emergency=True, retro_review_due_utc="2020-01-02T00:00:00+00:00")
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        "sys.argv",
        ["check_emergency_review_overdue.py", "--ledger-path", str(ledger_path), "--output-dir", str(out_dir)],
    )
    exit_code = main()
    assert exit_code == 2
    payload = json.loads((out_dir / "overdue_report.json").read_text(encoding="utf-8"))
    assert len(payload["findings"]) == 1
    assert payload["findings"][0]["category"] == "emergency_governance"


def test_main_returns_zero_for_missing_ledger_file(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv",
        [
            "check_emergency_review_overdue.py",
            "--ledger-path", str(tmp_path / "does_not_exist.jsonl"),
            "--output-dir", str(out_dir),
        ],
    )
    exit_code = main()
    assert exit_code == 0
