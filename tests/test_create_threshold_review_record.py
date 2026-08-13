"""`create_threshold_review_record.py` testleri -- uctan uca CLI
davranisi: gecerli bir proposal'dan review_record uretimi, bos
rationale/gecersiz decision/bozuk proposal icin acik hata kodlari."""

from __future__ import annotations

import json

import pytest

from threshold_governance_core import build_proposal


def _write_sample_proposal(tmp_path):
    proposal = build_proposal(
        alert_name="HIGH_NULL_INTENT_RATE",
        source_window="14d",
        current_values={"warn": 0.01, "crit": 0.02},
        proposed_values={"warn": 0.05, "crit": 0.1},
        confidence="HIGH",
        sample_adequacy="YETERLI",
        risk_note="test",
        evidence_paths=["x.json"],
    )
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps(proposal, indent=2, ensure_ascii=False), encoding="utf-8")
    return path, proposal


def test_main_creates_review_record_with_matching_checksum(tmp_path, monkeypatch):
    proposal_path, proposal = _write_sample_proposal(tmp_path)
    out_base = tmp_path / "reviews"

    monkeypatch.setattr(
        "sys.argv",
        [
            "create_threshold_review_record.py",
            "--proposal-path", str(proposal_path),
            "--reviewer", "alice",
            "--decision", "APPROVE",
            "--rationale", "Guven yuksek, uyguluyorum.",
            "--output-base-dir", str(out_base),
        ],
    )
    import create_threshold_review_record as mod

    exit_code = mod.main()
    assert exit_code == 0

    review_path = out_base / proposal["proposal_id"] / "review_record.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert review["decision"] == "APPROVE"
    assert review["reviewer"] == "alice"
    assert review["linked_proposal_checksum"] == proposal["checksum"]


def test_main_rejects_empty_rationale(tmp_path, monkeypatch, capsys):
    proposal_path, _ = _write_sample_proposal(tmp_path)

    monkeypatch.setattr(
        "sys.argv",
        [
            "create_threshold_review_record.py",
            "--proposal-path", str(proposal_path),
            "--reviewer", "alice",
            "--decision", "APPROVE",
            "--rationale", "   ",
            "--output-base-dir", str(tmp_path / "reviews"),
        ],
    )
    import create_threshold_review_record as mod

    exit_code = mod.main()
    assert exit_code == 2
    assert not (tmp_path / "reviews").exists()


def test_main_rejects_invalid_decision_via_argparse(tmp_path, monkeypatch):
    proposal_path, _ = _write_sample_proposal(tmp_path)

    monkeypatch.setattr(
        "sys.argv",
        [
            "create_threshold_review_record.py",
            "--proposal-path", str(proposal_path),
            "--reviewer", "alice",
            "--decision", "MAYBE",
            "--rationale", "x",
        ],
    )
    import create_threshold_review_record as mod

    with pytest.raises(SystemExit):
        mod.main()


def test_main_rejects_malformed_proposal_file(tmp_path, monkeypatch):
    bad_path = tmp_path / "bad_proposal.json"
    bad_path.write_text("{not valid json", encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        [
            "create_threshold_review_record.py",
            "--proposal-path", str(bad_path),
            "--reviewer", "alice",
            "--decision", "APPROVE",
            "--rationale", "x",
            "--output-base-dir", str(tmp_path / "reviews"),
        ],
    )
    import create_threshold_review_record as mod

    exit_code = mod.main()
    assert exit_code == 2


def test_main_rejects_proposal_missing_required_fields(tmp_path, monkeypatch):
    incomplete_path = tmp_path / "incomplete.json"
    incomplete_path.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        [
            "create_threshold_review_record.py",
            "--proposal-path", str(incomplete_path),
            "--reviewer", "alice",
            "--decision", "APPROVE",
            "--rationale", "x",
            "--output-base-dir", str(tmp_path / "reviews"),
        ],
    )
    import create_threshold_review_record as mod

    exit_code = mod.main()
    assert exit_code == 2


# --- APPROVE_EMERGENCY (v1.1) -- CLI seviyesinde zorlama -----------------------


def _emergency_argv(proposal_path, out_base, **overrides):
    fields = dict(
        **{
            "--proposal-path": str(proposal_path),
            "--reviewer": "alice",
            "--decision": "APPROVE_EMERGENCY",
            "--rationale": "acil",
            "--incident-id": "INC-9",
            "--justification": "Prod'da gurultu, hemen mudahale gerekiyor",
            "--timebox-hours": "6",
            "--retro-review-due-utc": "2026-08-15T00:00:00+00:00",
            "--output-base-dir": str(out_base),
        }
    )
    fields.update(overrides)
    argv = ["create_threshold_review_record.py"]
    for k, v in fields.items():
        if v is None:
            continue
        argv += [k, v]
    return argv


def test_main_creates_emergency_review_record_with_all_fields(tmp_path, monkeypatch):
    proposal_path, proposal = _write_sample_proposal(tmp_path)
    out_base = tmp_path / "reviews"

    monkeypatch.setattr("sys.argv", _emergency_argv(proposal_path, out_base))
    import create_threshold_review_record as mod

    exit_code = mod.main()
    assert exit_code == 0

    review_path = out_base / proposal["proposal_id"] / "review_record.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert review["decision"] == "APPROVE_EMERGENCY"
    assert review["incident_id"] == "INC-9"
    assert review["timebox_hours"] == 6.0
    assert review["retro_review_due_utc"] == "2026-08-15T00:00:00+00:00"


def test_main_rejects_emergency_missing_incident_id(tmp_path, monkeypatch, capsys):
    proposal_path, proposal = _write_sample_proposal(tmp_path)
    out_base = tmp_path / "reviews"

    monkeypatch.setattr("sys.argv", _emergency_argv(proposal_path, out_base, **{"--incident-id": None}))
    import create_threshold_review_record as mod

    exit_code = mod.main()
    assert exit_code == 2
    assert not (out_base / proposal["proposal_id"]).exists()
    assert "incident_id" in capsys.readouterr().err


def test_main_rejects_emergency_timebox_over_24_hours(tmp_path, monkeypatch, capsys):
    proposal_path, proposal = _write_sample_proposal(tmp_path)
    out_base = tmp_path / "reviews"

    monkeypatch.setattr("sys.argv", _emergency_argv(proposal_path, out_base, **{"--timebox-hours": "25"}))
    import create_threshold_review_record as mod

    exit_code = mod.main()
    assert exit_code == 2
    assert not (out_base / proposal["proposal_id"]).exists()
    assert "timebox_hours" in capsys.readouterr().err


def test_main_rejects_emergency_missing_retro_review_due_utc(tmp_path, monkeypatch, capsys):
    proposal_path, proposal = _write_sample_proposal(tmp_path)
    out_base = tmp_path / "reviews"

    monkeypatch.setattr("sys.argv", _emergency_argv(proposal_path, out_base, **{"--retro-review-due-utc": None}))
    import create_threshold_review_record as mod

    exit_code = mod.main()
    assert exit_code == 2
    assert not (out_base / proposal["proposal_id"]).exists()
    assert "retro_review_due_utc" in capsys.readouterr().err


def test_main_normal_approve_does_not_require_emergency_fields(tmp_path, monkeypatch):
    """Normal APPROVE, acil durum alanlari HIC verilmeden calismaya
    devam etmeli -- geriye donuk uyumluluk."""
    proposal_path, proposal = _write_sample_proposal(tmp_path)
    out_base = tmp_path / "reviews"

    monkeypatch.setattr(
        "sys.argv",
        [
            "create_threshold_review_record.py",
            "--proposal-path", str(proposal_path),
            "--reviewer", "alice",
            "--decision", "APPROVE",
            "--rationale", "normal onay",
            "--output-base-dir", str(out_base),
        ],
    )
    import create_threshold_review_record as mod

    exit_code = mod.main()
    assert exit_code == 0
    review = json.loads((out_base / proposal["proposal_id"] / "review_record.json").read_text(encoding="utf-8"))
    assert "incident_id" not in review


def test_main_attaches_legitimacy_report_path_when_provided(tmp_path, monkeypatch):
    """v1.2 PILOT: --legitimacy-report-path opsiyoneldir, verilirse
    review_record.json'a BILGI AMACLI eklenir -- exit code/basari
    davranisini ETKILEMEZ."""
    proposal_path, proposal = _write_sample_proposal(tmp_path)
    out_base = tmp_path / "reviews"

    monkeypatch.setattr(
        "sys.argv",
        [
            "create_threshold_review_record.py",
            "--proposal-path", str(proposal_path),
            "--reviewer", "alice",
            "--decision", "APPROVE",
            "--rationale", "normal onay",
            "--legitimacy-report-path", "reports/emergency_legitimacy_X/legitimacy_report.json",
            "--output-base-dir", str(out_base),
        ],
    )
    import create_threshold_review_record as mod

    exit_code = mod.main()
    assert exit_code == 0
    review = json.loads((out_base / proposal["proposal_id"] / "review_record.json").read_text(encoding="utf-8"))
    assert review["legitimacy_report_path"] == "reports/emergency_legitimacy_X/legitimacy_report.json"
