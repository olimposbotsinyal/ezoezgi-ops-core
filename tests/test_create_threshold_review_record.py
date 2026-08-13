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
