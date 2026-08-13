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


# --- Gercek jira modu (mock'lu -- gercek ag cagrisi YOK) -------------------------


def _patch_jira_check(monkeypatch, evidence):
    import legitimacy_provider_client

    monkeypatch.setattr(legitimacy_provider_client, "check_ticket_via_jira", lambda ticket_id, credentials: evidence)


def test_main_jira_provider_pass_writes_provider_evidence(tmp_path, monkeypatch):
    from legitimacy_provider_client import ProviderEvidence

    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "a@b.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "tok-secret")
    _patch_jira_check(
        monkeypatch,
        ProviderEvidence(mode="jira", checked=True, found=True, status_code=200, detail="bilet bulundu (HTTP 200)", attempts=1),
    )
    out_dir = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv",
        ["check_emergency_legitimacy.py", "--incident-id", "OPS-1234", "--provider", "jira", "--output-dir", str(out_dir)],
    )
    exit_code = main()
    assert exit_code == 0

    payload = json.loads((out_dir / "legitimacy_report.json").read_text(encoding="utf-8"))
    assert payload["legitimacy_status"] == "PASS"
    assert payload["provider_evidence"]["found"] is True
    assert "tok-secret" not in json.dumps(payload)


def test_main_jira_provider_fail_when_ticket_not_found(tmp_path, monkeypatch):
    from legitimacy_provider_client import ProviderEvidence

    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "a@b.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "tok-secret")
    _patch_jira_check(
        monkeypatch,
        ProviderEvidence(mode="jira", checked=True, found=False, status_code=404, detail="bilet BULUNAMADI (HTTP 404)", attempts=1),
    )
    out_dir = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv",
        ["check_emergency_legitimacy.py", "--incident-id", "OPS-1234", "--provider", "jira", "--output-dir", str(out_dir)],
    )
    exit_code = main()
    assert exit_code == 2

    payload = json.loads((out_dir / "legitimacy_report.json").read_text(encoding="utf-8"))
    assert payload["legitimacy_status"] == "FAIL"
    assert payload["provider_evidence"]["found"] is False


def test_main_jira_provider_not_configured_is_skipped_not_pass(tmp_path, monkeypatch):
    """**Promotion-candidate sprint semantik duzeltmesi:** eskiden
    (kimlik bilgisi eksikken) sonuc sessizce PASS donuyordu -- bu
    YANLIS-GUVEN veriyordu ("hic kontrol edilmedi" ile "gercekten
    dogrulandi" ayni gorunuyordu). Artik `checked=False` + `provider=jira`
    HER ZAMAN SKIPPED doner, ASLA PASS degil ("no implicit pass on
    unchecked provider"). Exit code yine de 0'dir (SKIPPED bir hata
    DEGILDIR, yalnizca "henuz gercek kanit yok" anlamina gelir). Gercek
    ag katmanina hic inilmedigi `test_legitimacy_provider_client.py::test_check_ticket_via_jira_not_configured_makes_no_call`
    ile ayrica dogrulanir."""
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    out_dir = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv",
        ["check_emergency_legitimacy.py", "--incident-id", "OPS-1234", "--provider", "jira", "--output-dir", str(out_dir)],
    )
    exit_code = main()
    assert exit_code == 0
    payload = json.loads((out_dir / "legitimacy_report.json").read_text(encoding="utf-8"))
    assert payload["legitimacy_status"] == "SKIPPED"
    assert payload["provider_evidence"]["checked"] is False


def test_main_jira_provider_skips_network_call_when_ticket_format_invalid(tmp_path, monkeypatch):
    calls = []

    def fake_check(ticket_id, credentials):
        calls.append(ticket_id)
        raise AssertionError("gecersiz bilet formatinda ag cagrisi yapilmamali")

    import legitimacy_provider_client

    monkeypatch.setattr(legitimacy_provider_client, "check_ticket_via_jira", fake_check)
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "a@b.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "tok-secret")
    out_dir = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv",
        ["check_emergency_legitimacy.py", "--incident-id", "not-a-ticket", "--provider", "jira", "--output-dir", str(out_dir)],
    )
    exit_code = main()
    assert exit_code == 2
    assert calls == []


def test_main_jira_provider_skipped_without_incident_id_makes_no_call(tmp_path, monkeypatch):
    calls = []

    def fake_check(ticket_id, credentials):
        calls.append(ticket_id)
        raise AssertionError("incident_id yokken ag cagrisi yapilmamali")

    import legitimacy_provider_client

    monkeypatch.setattr(legitimacy_provider_client, "check_ticket_via_jira", fake_check)
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "a@b.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "tok-secret")
    out_dir = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv", ["check_emergency_legitimacy.py", "--provider", "jira", "--output-dir", str(out_dir)]
    )
    exit_code = main()
    assert exit_code == 0
    assert calls == []
    payload = json.loads((out_dir / "legitimacy_report.json").read_text(encoding="utf-8"))
    assert payload["legitimacy_status"] == "SKIPPED"
