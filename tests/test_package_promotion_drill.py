"""`package_promotion_drill.py` testleri -- uctan uca CLI davranisi:
kaynak `apply_report.json` + `audit.log.jsonl`'i okuyup dogru
paketledigini/kabul kararini verdigini dogrular. Ayristirma/dogrulama
mantigi zaten `test_promotion_drill_core.py` ile deterministik test
edildi -- burada odak CLI/dosya-okuma kablolamasidir."""

from __future__ import annotations

import json

from package_promotion_drill import main

VALID_APPLY_REPORT = {
    "proposal_id": "X-20260813T000000",
    "generated_at": "2026-08-13T18:04:43.999833+00:00",
    "verification_state": "FAIL",
    "old_checksum": "AAA",
    "new_checksum": "BBB",
    "auto_rollback": {"triggered": True, "restored": True, "restored_checksum": "AAA"},
}


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_audit_log(path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(ln) for ln in lines) + "\n", encoding="utf-8")


def test_main_accepts_valid_drill_with_matching_audit_entry(tmp_path, monkeypatch):
    source_path = tmp_path / "reports/source/apply_report.json"
    _write_json(source_path, VALID_APPLY_REPORT)
    _write_audit_log(
        tmp_path / "data/audit/audit.log.jsonl",
        [{"task": "auto_rollback_triggered", "details": {"proposal_id": "X-20260813T000000"}, "timestamp": "t1", "status": "ROLLED_BACK"}],
    )
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        "sys.argv",
        [
            "package_promotion_drill.py", "--repo-root", str(tmp_path), "--source-apply-report-path", str(source_path),
            "--output-dir", str(out_dir),
        ],
    )
    exit_code = main()
    assert exit_code == 0
    summary = json.loads((out_dir / "drill_summary.json").read_text(encoding="utf-8"))
    assert summary["accepted"] is True
    assert summary["audit_entry_found"] is True
    assert summary["is_fresh_run"] is False


def test_main_rejects_when_audit_entry_missing(tmp_path, monkeypatch):
    source_path = tmp_path / "reports/source/apply_report.json"
    _write_json(source_path, VALID_APPLY_REPORT)
    _write_audit_log(tmp_path / "data/audit/audit.log.jsonl", [])
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        "sys.argv",
        [
            "package_promotion_drill.py", "--repo-root", str(tmp_path), "--source-apply-report-path", str(source_path),
            "--output-dir", str(out_dir),
        ],
    )
    exit_code = main()
    assert exit_code == 2
    summary = json.loads((out_dir / "drill_summary.json").read_text(encoding="utf-8"))
    assert summary["accepted"] is False
    assert summary["audit_entry_found"] is False


def test_main_rejects_when_verification_state_not_fail(tmp_path, monkeypatch):
    payload = dict(VALID_APPLY_REPORT, verification_state="PASS")
    source_path = tmp_path / "reports/source/apply_report.json"
    _write_json(source_path, payload)
    _write_audit_log(
        tmp_path / "data/audit/audit.log.jsonl",
        [{"task": "auto_rollback_triggered", "details": {"proposal_id": "X-20260813T000000"}, "timestamp": "t1"}],
    )
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        "sys.argv",
        [
            "package_promotion_drill.py", "--repo-root", str(tmp_path), "--source-apply-report-path", str(source_path),
            "--output-dir", str(out_dir),
        ],
    )
    assert main() == 2


def test_main_marks_fresh_run_flag_correctly(tmp_path, monkeypatch):
    source_path = tmp_path / "reports/source/apply_report.json"
    _write_json(source_path, VALID_APPLY_REPORT)
    _write_audit_log(
        tmp_path / "data/audit/audit.log.jsonl",
        [{"task": "auto_rollback_triggered", "details": {"proposal_id": "X-20260813T000000"}, "timestamp": "t1"}],
    )
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        "sys.argv",
        [
            "package_promotion_drill.py", "--repo-root", str(tmp_path), "--source-apply-report-path", str(source_path),
            "--output-dir", str(out_dir), "--fresh-run",
        ],
    )
    main()
    summary = json.loads((out_dir / "drill_summary.json").read_text(encoding="utf-8"))
    assert summary["is_fresh_run"] is True


def test_main_returns_two_when_source_apply_report_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "package_promotion_drill.py", "--repo-root", str(tmp_path),
            "--source-apply-report-path", str(tmp_path / "does_not_exist.json"),
            "--output-dir", str(tmp_path / "out"),
        ],
    )
    assert main() == 2


def test_main_against_real_repo_default_source_is_accepted(monkeypatch, tmp_path):
    """Gercek repo'daki BILINEN gercek drill kanitina karsi (varsayilan
    kaynak yolu) calisir -- bu, Promotion-candidate/Final-prep
    gorevlerinde referans verilen GERCEK kanitin genuinely kabul
    kriterlerini karsiladigini dogrular."""
    out_dir = tmp_path / "out"
    monkeypatch.setattr("sys.argv", ["package_promotion_drill.py", "--output-dir", str(out_dir)])
    exit_code = main()
    assert exit_code == 0
    summary = json.loads((out_dir / "drill_summary.json").read_text(encoding="utf-8"))
    assert summary["accepted"] is True
    assert summary["audit_entry_found"] is True
