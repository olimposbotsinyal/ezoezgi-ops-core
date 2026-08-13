"""`build_observability_signoff.py` testleri -- imza paketi butunlugu
(gorevin acikca istedigi test odagi): tum zorunlu alanlarin varligi,
GO/NO-GO karar mantigi, gate/test verisi eksikken FABRIKE EDILMIS bir
sonuc uretilmedigi."""

from __future__ import annotations

from build_observability_signoff import (
    CONDITIONAL_GO,
    GO,
    KNOWN_LIMITATIONS,
    NO_GO,
    RunSummary,
    build_signoff,
    go_no_go_recommendation,
    render_signoff_md,
    write_signoff,
)


def test_go_no_go_no_test_result_is_no_go():
    decision, rationale = go_no_go_recommendation(test_exit_code=None, gate_results=None)
    assert decision == NO_GO
    assert "test" in rationale.lower()


def test_go_no_go_test_failure_is_no_go_even_with_passing_gates():
    decision, rationale = go_no_go_recommendation(
        test_exit_code=1, gate_results={"overall_exit_code": 0}
    )
    assert decision == NO_GO


def test_go_no_go_tests_pass_but_no_gate_results_is_no_go():
    """Fabrike edilmis bir GO asla uretilmemeli -- gate hic calismadiysa
    NO-GO donmeli, varsayilan olarak PASS SAYILMAMALI."""
    decision, rationale = go_no_go_recommendation(test_exit_code=0, gate_results=None)
    assert decision == NO_GO
    assert "gate" in rationale.lower()


def test_go_no_go_gate_fail_is_no_go():
    decision, _ = go_no_go_recommendation(test_exit_code=0, gate_results={"overall_exit_code": 2})
    assert decision == NO_GO


def test_go_no_go_gate_partial_is_conditional_go():
    decision, rationale = go_no_go_recommendation(test_exit_code=0, gate_results={"overall_exit_code": 1})
    assert decision == CONDITIONAL_GO
    assert rationale


def test_go_no_go_all_green_is_go():
    decision, _ = go_no_go_recommendation(test_exit_code=0, gate_results={"overall_exit_code": 0})
    assert decision == GO


def test_build_signoff_contains_all_required_fields():
    data = build_signoff(
        git_sha="abc123",
        git_log=["abc123 some commit"],
        test_summary=RunSummary(ran=True, exit_code=0, summary_line="10 passed"),
        gate_results={"overall_exit_code": 0, "overall_status": "PASS", "gates": []},
        generated_at="2026-08-13T00:00:00Z",
    )

    required_top_level = {
        "generated_at", "git_sha", "git_recent_log", "test_summary", "gate_results",
        "go_no_go_decision", "go_no_go_rationale", "known_limitations", "rollback_reference",
    }
    assert required_top_level.issubset(data.keys())
    assert data["go_no_go_decision"] == GO
    assert data["known_limitations"] == KNOWN_LIMITATIONS
    assert data["rollback_reference"]["dry_run_default"] is True
    assert "rollback_observability.ps1" in data["rollback_reference"]["script"]


def test_build_signoff_with_missing_gate_results_is_honest_not_fabricated():
    data = build_signoff(
        git_sha="abc123", git_log=[],
        test_summary=RunSummary(ran=True, exit_code=0, summary_line="10 passed"),
        gate_results=None,
        generated_at="2026-08-13T00:00:00Z",
    )
    assert data["gate_results"] is None
    assert data["go_no_go_decision"] == NO_GO


def test_render_signoff_md_includes_decision_tests_gates_limitations_rollback():
    data = build_signoff(
        git_sha="abc123", git_log=["abc123 commit msg"],
        test_summary=RunSummary(ran=True, exit_code=0, summary_line="242 passed"),
        gate_results={
            "overall_exit_code": 0, "overall_status": "PASS",
            "gates": [{"name": "A", "status": "PASS", "detail": "ok"}],
            "_source_file": "reports/go_live_gates_x/gate_results.json",
        },
        generated_at="2026-08-13T00:00:00Z",
    )
    md = render_signoff_md(data)

    assert "GO" in md
    assert "242 passed" in md
    assert "A: PASS" in md
    for limitation in KNOWN_LIMITATIONS:
        assert limitation.split(" -- ")[0][:30] in md  # her sinirlamadan bir parca gorunmeli
    assert "rollback_observability.ps1" in md
    assert "abc123 commit msg" in md


def test_decision_matrix_regression_prior_real_no_go_scenario():
    """2026-08-13 (bu gorevden ONCE) bu makinede GERCEKTEN gozlenen
    NO-GO senaryosunu regresyona karsi kilitler: tam suite yesildi
    (271/271) ama gate suite'i FAIL donuyordu (Gate E, B037 -- saf
    PowerShell'de echo PATH sorunu -- FAIL, Gate D Alertmanager
    kurulu olmadigindan SKIPPED). Bu, B037 duzeltmesinden VE gercek
    Alertmanager kurulumundan ONCEKI gercek durumdu."""
    prior_real_gate_results = {
        "overall_exit_code": 2,  # FAIL (Gate E FAIL, Gate D yalnizca SKIPPED)
        "overall_status": "FAIL",
        "gates": [
            {"name": "A_metrics_availability", "status": "PASS", "detail": "..."},
            {"name": "B_scrape_success_rate", "status": "PASS", "detail": "..."},
            {"name": "C_synthetic_alerts_visible", "status": "PASS", "detail": "..."},
            {"name": "D_alertmanager_receive_path", "status": "SKIPPED", "detail": "..."},
            {"name": "E_classify_regression_smoke", "status": "FAIL", "detail": "..."},
        ],
    }
    decision, rationale = go_no_go_recommendation(test_exit_code=0, gate_results=prior_real_gate_results)
    assert decision == NO_GO
    assert "FAIL" in rationale


def test_decision_matrix_regression_current_real_go_scenario():
    """2026-08-13 (B037 duzeltmesi + gercek Prometheus v3.13.2 +
    Alertmanager v0.33.1 kurulumu SONRASI) bu makinede GERCEKTEN elde
    edilen GO senaryosunu regresyona karsi kilitler: tam suite yesil
    (271/271, hem Bash hem native PowerShell) VE gate suite'i tam PASS
    (Gate D artik gercek bir alert alarak PASS, Gate E B037 duzeltmesi
    sonrasi PASS)."""
    current_real_gate_results = {
        "overall_exit_code": 0,
        "overall_status": "PASS",
        "gates": [
            {"name": "A_metrics_availability", "status": "PASS", "detail": "..."},
            {"name": "B_scrape_success_rate", "status": "PASS", "detail": "..."},
            {"name": "C_synthetic_alerts_visible", "status": "PASS", "detail": "..."},
            {"name": "D_alertmanager_receive_path", "status": "PASS", "detail": "Alertmanager alma yolu uctan uca dogrulandi"},
            {"name": "E_classify_regression_smoke", "status": "PASS", "detail": "..."},
        ],
    }
    decision, rationale = go_no_go_recommendation(test_exit_code=0, gate_results=current_real_gate_results)
    assert decision == GO
    assert rationale


def test_write_signoff_creates_both_md_and_json(tmp_path):
    data = build_signoff(
        git_sha="abc123", git_log=[],
        test_summary=RunSummary(ran=True, exit_code=0, summary_line="ok"),
        gate_results=None,
        generated_at="2026-08-13T00:00:00Z",
    )
    md_path, json_path = write_signoff(data, tmp_path / "out")

    assert md_path.exists()
    assert json_path.exists()
    assert md_path.name == "SIGNOFF.md"
    assert json_path.name == "SIGNOFF.json"

    import json

    reloaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert reloaded["go_no_go_decision"] == NO_GO
