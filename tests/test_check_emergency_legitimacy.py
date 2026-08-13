"""`check_emergency_legitimacy.py` testleri -- uctan uca CLI davranisi:
gecerli/gecersiz/eksik incident_id icin dogru exit code + rapor
uretimi. Mantigin kendisi `test_emergency_legitimacy_core.py` ile
deterministik test edildi -- burada yalnizca CLI kablolamasi (argv,
env var okuma, exit code) test edilir."""

from __future__ import annotations

import json

from check_emergency_legitimacy import main


def test_main_returns_zero_and_skipped_without_incident_id(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    monkeypatch.setattr("sys.argv", ["check_emergency_legitimacy.py", "--output-dir", str(out_dir)])
    exit_code = main()
    assert exit_code == 0
    payload = json.loads((out_dir / "legitimacy_report.json").read_text(encoding="utf-8"))
    assert payload["legitimacy_status"] == "SKIPPED"


def test_main_returns_zero_and_pass_for_valid_ticket(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv",
        ["check_emergency_legitimacy.py", "--incident-id", "OPS-1234", "--provider", "none", "--output-dir", str(out_dir)],
    )
    exit_code = main()
    assert exit_code == 0
    payload = json.loads((out_dir / "legitimacy_report.json").read_text(encoding="utf-8"))
    assert payload["legitimacy_status"] == "PASS"


def test_main_returns_two_and_fail_for_invalid_ticket_format(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv",
        ["check_emergency_legitimacy.py", "--incident-id", "not-a-ticket", "--output-dir", str(out_dir)],
    )
    exit_code = main()
    assert exit_code == 2
    payload = json.loads((out_dir / "legitimacy_report.json").read_text(encoding="utf-8"))
    assert payload["legitimacy_status"] == "FAIL"


def test_main_respects_gov_ticket_regex_env_var(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    monkeypatch.setenv("GOV_TICKET_REGEX", r"^INC-\d+$")
    monkeypatch.setattr(
        "sys.argv",
        ["check_emergency_legitimacy.py", "--incident-id", "INC-99", "--output-dir", str(out_dir)],
    )
    exit_code = main()
    assert exit_code == 0
    payload = json.loads((out_dir / "legitimacy_report.json").read_text(encoding="utf-8"))
    assert payload["legitimacy_status"] == "PASS"


def test_main_cli_ticket_regex_overrides_env_var(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    monkeypatch.setenv("GOV_TICKET_REGEX", r"^INC-\d+$")
    monkeypatch.setattr(
        "sys.argv",
        [
            "check_emergency_legitimacy.py", "--incident-id", "OPS-99",
            "--ticket-regex", r"^OPS-\d+$", "--output-dir", str(out_dir),
        ],
    )
    exit_code = main()
    assert exit_code == 0
