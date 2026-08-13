"""`build_promotion_runpack_index.py` testleri -- uctan uca CLI
davranisi: adim sonuclari JSON dosyasini okur, auto-rollback OZ-
taramasini calistirir, `runpack_index.json`/`runpack_summary.md` yazar.
Mantigin kendisi `test_promotion_evidence_pack_core.py` ile zaten
deterministik test edildi -- burada odak CLI kablolamasidir."""

from __future__ import annotations

import json

from build_promotion_runpack_index import main


def _write_steps(path, steps):
    path.write_text(json.dumps(steps), encoding="utf-8")


def test_main_writes_index_and_summary_with_evaluator_exit_code(tmp_path, monkeypatch):
    steps_path = tmp_path / "steps.json"
    _write_steps(steps_path, [{"step": "chain_trial", "exit_code": 0, "status": "OK", "evidence_path": "x"}])
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        "sys.argv",
        [
            "build_promotion_runpack_index.py", "--repo-root", str(tmp_path), "--steps-json-path", str(steps_path),
            "--incident-id", "OPS-1", "--evaluator-exit-code", "0", "--output-dir", str(out_dir),
        ],
    )
    exit_code = main()
    assert exit_code == 0

    payload = json.loads((out_dir / "runpack_index.json").read_text(encoding="utf-8"))
    assert payload["promotion_readiness_outlook"] == "PROMOTE-ready"
    assert any(s["step"] == "auto_rollback_evidence_scan" for s in payload["steps"])
    assert any(s["step"] == "chain_trial" for s in payload["steps"])
    assert (out_dir / "runpack_summary.md").exists()


def test_main_without_evaluator_exit_code_yields_unknown_outlook(tmp_path, monkeypatch):
    steps_path = tmp_path / "steps.json"
    _write_steps(steps_path, [])
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        "sys.argv",
        [
            "build_promotion_runpack_index.py", "--repo-root", str(tmp_path), "--steps-json-path", str(steps_path),
            "--output-dir", str(out_dir),
        ],
    )
    exit_code = main()
    assert exit_code == 0
    payload = json.loads((out_dir / "runpack_index.json").read_text(encoding="utf-8"))
    assert payload["promotion_readiness_outlook"] == "UNKNOWN"


def test_main_includes_real_auto_rollback_evidence_counts(tmp_path, monkeypatch):
    steps_path = tmp_path / "steps.json"
    _write_steps(steps_path, [])
    (tmp_path / "reports/threshold_apply_X").mkdir(parents=True)
    (tmp_path / "reports/threshold_apply_X/apply_report.json").write_text(
        json.dumps({"auto_rollback": {"triggered": True}}), encoding="utf-8"
    )
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        "sys.argv",
        [
            "build_promotion_runpack_index.py", "--repo-root", str(tmp_path), "--steps-json-path", str(steps_path),
            "--output-dir", str(out_dir),
        ],
    )
    main()
    payload = json.loads((out_dir / "runpack_index.json").read_text(encoding="utf-8"))
    scan_step = next(s for s in payload["steps"] if s["step"] == "auto_rollback_evidence_scan")
    assert "ON=1" in scan_step["notes"]
    assert "OFF=0" in scan_step["notes"]


def test_main_returns_two_on_malformed_steps_file(tmp_path, monkeypatch):
    steps_path = tmp_path / "steps.json"
    steps_path.write_text("{not valid json", encoding="utf-8")
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        "sys.argv",
        [
            "build_promotion_runpack_index.py", "--repo-root", str(tmp_path), "--steps-json-path", str(steps_path),
            "--output-dir", str(out_dir),
        ],
    )
    exit_code = main()
    assert exit_code == 2
    assert not (out_dir / "runpack_index.json").exists()


def test_main_tolerates_utf8_bom_in_steps_file(tmp_path, monkeypatch):
    """Regresyon: `run_promotion_evidence_pack.ps1`, adim sonuclarini
    Windows PowerShell 5.1'in `Set-Content -Encoding utf8`'iyle yazar --
    bu (PS 7+'in aksine) DAIMA bir UTF-8 BOM ekler. Gercek bir calistirmada
    (2026-08-13) bu, `json.JSONDecodeError: Unexpected UTF-8 BOM` ile
    KESFEDILDI -- bu test bunu KALICI olarak yakalar."""
    steps_path = tmp_path / "steps.json"
    steps_path.write_bytes(b"\xef\xbb\xbf" + json.dumps([{"step": "x", "exit_code": 0, "status": "OK", "evidence_path": None}]).encode("utf-8"))
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        "sys.argv",
        [
            "build_promotion_runpack_index.py", "--repo-root", str(tmp_path), "--steps-json-path", str(steps_path),
            "--output-dir", str(out_dir),
        ],
    )
    exit_code = main()
    assert exit_code == 0
    assert (out_dir / "runpack_index.json").exists()


def test_main_returns_two_when_steps_is_not_a_list(tmp_path, monkeypatch):
    steps_path = tmp_path / "steps.json"
    _write_steps(steps_path, {"not": "a list"})
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        "sys.argv",
        [
            "build_promotion_runpack_index.py", "--repo-root", str(tmp_path), "--steps-json-path", str(steps_path),
            "--output-dir", str(out_dir),
        ],
    )
    exit_code = main()
    assert exit_code == 2
