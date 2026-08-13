"""`run_emergency_chain_trial.py` testleri -- uctan uca CLI davranisi:
salt-okunur, ledger'dan v1.1/v1.2 karsilastirma raporu uretir. Mantigin
kendisi `test_emergency_chain_core.py` ile deterministik test edildi."""

from __future__ import annotations

import json

from run_emergency_chain_trial import main


def _write_ledger(path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def test_main_produces_empty_comparison_for_empty_ledger(tmp_path, monkeypatch):
    ledger_path = tmp_path / "ledger.jsonl"
    ledger_path.write_text("", encoding="utf-8")
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        "sys.argv",
        ["run_emergency_chain_trial.py", "--ledger-path", str(ledger_path), "--output-dir", str(out_dir)],
    )
    exit_code = main()
    assert exit_code == 0
    payload = json.loads((out_dir / "chain_eval.json").read_text(encoding="utf-8"))
    assert payload["rows"] == []
    assert "karsilastirilacak bir sey yok" in (out_dir / "chain_eval.md").read_text(encoding="utf-8")


def test_main_flags_outcome_difference_for_broken_chain(tmp_path, monkeypatch):
    ledger_path = tmp_path / "ledger.jsonl"
    entries = [
        {
            "timestamp": "2020-01-01T00:00:00+00:00", "proposal_id": "A-1", "alert_name": "HIGH_NULL_INTENT_RATE",
            "old_checksum": "x0", "new_checksum": "x1", "apply_report_path": "r1.json",
            "is_emergency": True, "retro_review_due_utc": "2020-01-02T00:00:00+00:00",
        },
        {
            "timestamp": "2020-01-03T00:00:00+00:00", "proposal_id": "A-2", "alert_name": "HIGH_NULL_INTENT_RATE",
            "old_checksum": "UNRELATED", "new_checksum": "x2", "apply_report_path": "r2.json",
            "is_emergency": False, "retro_review_due_utc": None,
        },
    ]
    _write_ledger(ledger_path, entries)
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        "sys.argv",
        ["run_emergency_chain_trial.py", "--ledger-path", str(ledger_path), "--output-dir", str(out_dir)],
    )
    exit_code = main()
    assert exit_code == 0
    payload = json.loads((out_dir / "chain_eval.json").read_text(encoding="utf-8"))
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["outcome_differs"] is True
    md = (out_dir / "chain_eval.md").read_text(encoding="utf-8")
    assert "EVET" in md
