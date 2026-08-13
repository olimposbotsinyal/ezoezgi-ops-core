"""`emergency_legitimacy_core.py` testleri -- v1.2 PILOT mesruiyet
on-kontrolu: ticket format pass/fail/skipped matrisi, provider stub
davranisi (none/mock/jira_stub), hicbirinin GERCEK bir aga/sirra
bagli olmadigi."""

from __future__ import annotations

import pytest

from emergency_legitimacy_core import (
    DEFAULT_TICKET_REGEX,
    PROVIDER_JIRA,
    PROVIDER_JIRA_STUB,
    PROVIDER_MOCK,
    PROVIDER_NONE,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_SKIPPED,
    ProviderCheckResult,
    evaluate_legitimacy,
    render_legitimacy_report_md,
    run_provider_check,
    validate_ticket_format,
    write_legitimacy_report,
)


# --- Ticket format dogrulama -----------------------------------------------------


def test_validate_ticket_format_accepts_default_pattern():
    ok, reason = validate_ticket_format("OPS-1234")
    assert ok is True
    assert "gecerli" in reason


def test_validate_ticket_format_rejects_non_matching_ticket():
    ok, reason = validate_ticket_format("not-a-ticket")
    assert ok is False
    assert "desene uymuyor" in reason


def test_validate_ticket_format_rejects_none():
    ok, reason = validate_ticket_format(None)
    assert ok is False
    assert "bos/eksik" in reason


def test_validate_ticket_format_rejects_blank_string():
    ok, reason = validate_ticket_format("   ")
    assert ok is False


def test_validate_ticket_format_accepts_custom_pattern():
    ok, reason = validate_ticket_format("INC-42", pattern=r"^INC-\d+$")
    assert ok is True


def test_validate_ticket_format_reports_invalid_regex_gracefully():
    ok, reason = validate_ticket_format("OPS-1", pattern="(unbalanced[")
    assert ok is False
    assert "gecersiz" in reason.lower()


# --- Provider stub kontrolu (PILOT -- gercek ag/sir YOK) ------------------------


def test_run_provider_check_none_is_not_checked():
    result = run_provider_check("OPS-1", provider=PROVIDER_NONE)
    assert result.checked is False
    assert result.found is False


@pytest.mark.parametrize("provider", [PROVIDER_MOCK, PROVIDER_JIRA_STUB])
def test_run_provider_check_stub_providers_deterministically_found(provider):
    result = run_provider_check("OPS-1", provider=provider)
    assert result.checked is True
    assert result.found is True
    assert "PILOT STUB" in result.detail
    assert "gercek baglanti" in result.detail.lower()


def test_run_provider_check_unknown_provider_not_checked():
    result = run_provider_check("OPS-1", provider="unknown_provider")
    assert result.checked is False


def test_run_provider_check_missing_ticket_id_not_found():
    result = run_provider_check(None, provider=PROVIDER_MOCK)
    assert result.checked is True
    assert result.found is False


# --- Uctan uca mesruiyet degerlendirmesi ----------------------------------------


def test_evaluate_legitimacy_skipped_when_no_incident_id():
    result = evaluate_legitimacy(incident_id=None)
    assert result.status == STATUS_SKIPPED


def test_evaluate_legitimacy_pass_with_valid_ticket_and_provider_none():
    result = evaluate_legitimacy(incident_id="OPS-1234", provider=PROVIDER_NONE)
    assert result.status == STATUS_PASS


def test_evaluate_legitimacy_pass_with_valid_ticket_and_mock_provider():
    result = evaluate_legitimacy(incident_id="OPS-1234", provider=PROVIDER_MOCK)
    assert result.status == STATUS_PASS


def test_evaluate_legitimacy_fail_on_bad_ticket_format():
    result = evaluate_legitimacy(incident_id="not-a-ticket", provider=PROVIDER_NONE)
    assert result.status == STATUS_FAIL
    assert any("desene uymuyor" in r for r in result.reasons)


def test_evaluate_legitimacy_fail_format_short_circuits_provider_check():
    """Format basarisizsa, provider kontrolu HIC CALISTIRILMAMALI --
    tek bir 'nedenler' satiri (yalnizca format) beklenir."""
    result = evaluate_legitimacy(incident_id="bad-format", provider=PROVIDER_MOCK)
    assert result.status == STATUS_FAIL
    assert len(result.reasons) == 1


def test_evaluate_legitimacy_respects_custom_ticket_regex():
    result = evaluate_legitimacy(incident_id="INC-42", provider=PROVIDER_NONE, ticket_regex=r"^INC-\d+$")
    assert result.status == STATUS_PASS


def test_evaluate_legitimacy_default_regex_is_ops_prefix():
    assert DEFAULT_TICKET_REGEX == r"^OPS-\d+$"


# --- Rapor uretimi ---------------------------------------------------------------


def test_write_legitimacy_report_creates_md_and_json(tmp_path):
    result = evaluate_legitimacy(incident_id="OPS-1234", provider=PROVIDER_MOCK)
    paths = write_legitimacy_report(
        result, tmp_path / "out", generated_at="2026-08-13T00:00:00+00:00", incident_id="OPS-1234", provider=PROVIDER_MOCK
    )
    assert paths["md"].exists()
    assert paths["json"].exists()

    import json

    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["legitimacy_status"] == "PASS"
    assert payload["incident_id"] == "OPS-1234"


def test_render_legitimacy_report_md_includes_non_blocking_disclaimer():
    result = evaluate_legitimacy(incident_id=None)
    md = render_legitimacy_report_md(result, generated_at="2026-08-13T00:00:00+00:00", incident_id=None, provider=PROVIDER_NONE)
    assert "HENUZ hicbir apply akisini ENGELLEMEZ" in md
    assert "SKIPPED" in md


def test_render_legitimacy_report_md_includes_provider_evidence_when_present():
    result = evaluate_legitimacy(incident_id=None)
    md = render_legitimacy_report_md(
        result, generated_at="2026-08-13T00:00:00+00:00", incident_id=None, provider=PROVIDER_JIRA,
        provider_evidence={"checked": True, "found": True, "status_code": 200, "attempts": 1, "detail": "bilet bulundu (HTTP 200)"},
    )
    assert "Saglayici kaniti" in md
    assert "bilet bulundu (HTTP 200)" in md


def test_render_legitimacy_report_md_omits_provider_evidence_section_when_absent():
    result = evaluate_legitimacy(incident_id=None)
    md = render_legitimacy_report_md(result, generated_at="2026-08-13T00:00:00+00:00", incident_id=None, provider=PROVIDER_NONE)
    assert "Saglayici kaniti" not in md


def test_write_legitimacy_report_includes_provider_evidence_in_json(tmp_path):
    result = evaluate_legitimacy(
        incident_id="OPS-1234", provider=PROVIDER_JIRA,
        precomputed_provider_result=ProviderCheckResult(checked=True, found=True, detail="bilet bulundu (HTTP 200)"),
    )
    evidence = {"mode": "jira", "checked": True, "found": True, "status_code": 200, "detail": "bilet bulundu (HTTP 200)", "attempts": 1}
    paths = write_legitimacy_report(
        result, tmp_path / "out", generated_at="2026-08-13T00:00:00+00:00", incident_id="OPS-1234",
        provider=PROVIDER_JIRA, provider_evidence=evidence,
    )

    import json

    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["provider_evidence"] == evidence


def test_write_legitimacy_report_provider_evidence_defaults_to_none(tmp_path):
    result = evaluate_legitimacy(incident_id="OPS-1234", provider=PROVIDER_MOCK)
    paths = write_legitimacy_report(
        result, tmp_path / "out", generated_at="2026-08-13T00:00:00+00:00", incident_id="OPS-1234", provider=PROVIDER_MOCK
    )

    import json

    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["provider_evidence"] is None


# --- Gercek jira modu (precomputed_provider_result / guard) ----------------------


def test_run_provider_check_refuses_to_fake_jira_result():
    result = run_provider_check("OPS-1234", provider=PROVIDER_JIRA)
    assert result.checked is False
    assert result.found is False
    assert "STUB fonksiyonla desteklenmez" in result.detail


def test_evaluate_legitimacy_jira_without_precomputed_result_raises():
    with pytest.raises(ValueError, match="precomputed_provider_result"):
        evaluate_legitimacy(incident_id="OPS-1234", provider=PROVIDER_JIRA)


def test_evaluate_legitimacy_jira_with_precomputed_result_pass():
    result = evaluate_legitimacy(
        incident_id="OPS-1234", provider=PROVIDER_JIRA,
        precomputed_provider_result=ProviderCheckResult(checked=True, found=True, detail="bilet bulundu (HTTP 200)"),
    )
    assert result.status == STATUS_PASS


def test_evaluate_legitimacy_jira_with_precomputed_result_fail():
    result = evaluate_legitimacy(
        incident_id="OPS-1234", provider=PROVIDER_JIRA,
        precomputed_provider_result=ProviderCheckResult(checked=True, found=False, detail="bilet BULUNAMADI (HTTP 404)"),
    )
    assert result.status == STATUS_FAIL


def test_evaluate_legitimacy_jira_bad_ticket_format_short_circuits_before_precomputed_check():
    """Format gecersizse, precomputed_provider_result hic verilmemis
    olsa bile ValueError DEGIL FAIL donmelidir (format kontrolu her
    zaman provider kontrolunden ONCE calisir)."""
    result = evaluate_legitimacy(incident_id="not-a-ticket", provider=PROVIDER_JIRA)
    assert result.status == STATUS_FAIL


def test_evaluate_legitimacy_jira_skipped_without_incident_id_even_if_no_precomputed_result():
    """incident_id verilmemisse, provider='jira' olsa bile ValueError
    DEGIL SKIPPED donmelidir (SKIPPED kontrolu her seyden once calisir)."""
    result = evaluate_legitimacy(incident_id=None, provider=PROVIDER_JIRA)
    assert result.status == STATUS_SKIPPED
