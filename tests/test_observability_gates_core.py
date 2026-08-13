"""`observability_gates_core.py` testleri -- gate sonuc ayristirici +
exit code haritalamasi (gorevin acikca istedigi test odagi). Sabit,
elle olusturulmus girdilerle -- gercek bir sunucu/Alertmanager/pytest
calistirmadan tamamen deterministik."""

from __future__ import annotations

import pytest

from observability_gates_core import (
    EXIT_FAIL,
    EXIT_PARTIAL,
    EXIT_PASS,
    STATUS_FAIL,
    STATUS_PARTIAL,
    STATUS_PASS,
    STATUS_SKIPPED,
    GateResult,
    evaluate_gate_a_metrics_availability,
    evaluate_gate_b_scrape_success,
    evaluate_gate_c_synthetic_alerts,
    evaluate_gate_d_alertmanager_receive,
    evaluate_gate_e_classify_regression,
    overall_exit_code,
    overall_status,
    parse_gate_results_json,
    render_gate_report_md,
    write_gate_results_json,
)


def test_gate_result_rejects_invalid_status():
    with pytest.raises(ValueError):
        GateResult("X", "NOT_A_STATUS", "detay")


def test_overall_exit_code_all_pass_returns_0():
    results = [GateResult("A", STATUS_PASS, "ok"), GateResult("B", STATUS_PASS, "ok")]
    assert overall_exit_code(results) == EXIT_PASS
    assert overall_status(results) == STATUS_PASS


def test_overall_exit_code_partial_no_fail_returns_1():
    results = [GateResult("A", STATUS_PASS, "ok"), GateResult("B", STATUS_PARTIAL, "kismi")]
    assert overall_exit_code(results) == EXIT_PARTIAL
    assert overall_status(results) == STATUS_PARTIAL


def test_overall_exit_code_skipped_counts_as_partial_bucket():
    results = [GateResult("A", STATUS_PASS, "ok"), GateResult("D", STATUS_SKIPPED, "altyapi yok")]
    assert overall_exit_code(results) == EXIT_PARTIAL


def test_overall_exit_code_any_fail_wins_over_partial_and_pass():
    results = [
        GateResult("A", STATUS_PASS, "ok"),
        GateResult("B", STATUS_PARTIAL, "kismi"),
        GateResult("C", STATUS_FAIL, "basarisiz"),
    ]
    assert overall_exit_code(results) == EXIT_FAIL
    assert overall_status(results) == STATUS_FAIL


def test_overall_exit_code_empty_results_is_pass_by_vacuous_truth():
    assert overall_exit_code([]) == EXIT_PASS


def test_evaluate_gate_a_full_availability_is_pass():
    r = evaluate_gate_a_metrics_availability(10, 10, simulated=True, window_label="10 ornek")
    assert r.status == STATUS_PASS
    assert r.evidence["availability"] == 1.0


def test_evaluate_gate_a_degraded_availability_is_partial():
    r = evaluate_gate_a_metrics_availability(99, 100, simulated=False, window_label="24s")
    assert r.status == STATUS_PARTIAL


def test_evaluate_gate_a_low_availability_is_fail():
    r = evaluate_gate_a_metrics_availability(5, 10, simulated=True, window_label="10 ornek")
    assert r.status == STATUS_FAIL


def test_evaluate_gate_a_zero_samples_is_fail_not_crash():
    r = evaluate_gate_a_metrics_availability(0, 0, simulated=True, window_label="bos")
    assert r.status == STATUS_FAIL
    assert r.evidence["availability"] == 0.0


def test_evaluate_gate_b_scrape_success_thresholds():
    assert evaluate_gate_b_scrape_success(100, 100, simulated=True).status == STATUS_PASS
    assert evaluate_gate_b_scrape_success(96, 100, simulated=True).status == STATUS_PARTIAL
    assert evaluate_gate_b_scrape_success(50, 100, simulated=True).status == STATUS_FAIL


def test_evaluate_gate_c_all_modes_visible_is_pass():
    r = evaluate_gate_c_synthetic_alerts(
        {"fallback-spike": True, "null-intent-spike": True, "preflight-unknown": True, "circuit-open-stuck": True}
    )
    assert r.status == STATUS_PASS
    assert r.evidence["missing"] == []


def test_evaluate_gate_c_partial_modes_visible_is_partial():
    r = evaluate_gate_c_synthetic_alerts(
        {"fallback-spike": True, "null-intent-spike": False, "preflight-unknown": True, "circuit-open-stuck": False}
    )
    assert r.status == STATUS_PARTIAL
    assert set(r.evidence["missing"]) == {"null-intent-spike", "circuit-open-stuck"}


def test_evaluate_gate_c_no_modes_visible_is_fail():
    r = evaluate_gate_c_synthetic_alerts({"fallback-spike": False, "null-intent-spike": False})
    assert r.status == STATUS_FAIL


def test_evaluate_gate_d_not_installed_is_skipped():
    r = evaluate_gate_d_alertmanager_receive(
        alertmanager_installed=False, alertmanager_reachable=False, alert_received=False
    )
    assert r.status == STATUS_SKIPPED


def test_evaluate_gate_d_installed_but_unreachable_is_fail():
    r = evaluate_gate_d_alertmanager_receive(
        alertmanager_installed=True, alertmanager_reachable=False, alert_received=False
    )
    assert r.status == STATUS_FAIL


def test_evaluate_gate_d_reachable_but_alert_missing_is_fail():
    r = evaluate_gate_d_alertmanager_receive(
        alertmanager_installed=True, alertmanager_reachable=True, alert_received=False
    )
    assert r.status == STATUS_FAIL


def test_evaluate_gate_d_full_path_is_pass():
    r = evaluate_gate_d_alertmanager_receive(
        alertmanager_installed=True, alertmanager_reachable=True, alert_received=True
    )
    assert r.status == STATUS_PASS


def test_evaluate_gate_e_pytest_success_is_pass():
    r = evaluate_gate_e_classify_regression(0, "12 passed")
    assert r.status == STATUS_PASS


def test_evaluate_gate_e_pytest_failure_is_fail():
    r = evaluate_gate_e_classify_regression(1, "1 failed, 11 passed")
    assert r.status == STATUS_FAIL


def test_render_gate_report_md_includes_all_gates_and_overall_status():
    results = [GateResult("A", STATUS_PASS, "ok", evidence={"x": 1}), GateResult("B", STATUS_FAIL, "kotu")]
    md = render_gate_report_md(results, generated_at="2026-08-13T00:00:00Z", mode="simulation")
    assert "A" in md and "B" in md
    assert "FAIL" in md  # overall status
    assert '"x": 1' in md


def test_write_and_parse_gate_results_json_roundtrip(tmp_path):
    results = [GateResult("A", STATUS_PASS, "ok"), GateResult("D", STATUS_SKIPPED, "altyapi yok")]
    out_path = tmp_path / "gate_results.json"

    write_gate_results_json(results, out_path, generated_at="2026-08-13T00:00:00Z", mode="simulation")
    parsed = parse_gate_results_json(out_path)

    assert parsed["overall_status"] == STATUS_PARTIAL
    assert parsed["overall_exit_code"] == EXIT_PARTIAL
    assert len(parsed["gates"]) == 2
    assert parsed["gates"][0]["name"] == "A"


def test_parse_gate_results_json_missing_field_raises(tmp_path):
    bad_path = tmp_path / "bad.json"
    bad_path.write_text('{"generated_at": "x"}', encoding="utf-8")

    with pytest.raises(ValueError):
        parse_gate_results_json(bad_path)


def test_parse_gate_results_json_invalid_json_raises(tmp_path):
    bad_path = tmp_path / "bad.json"
    bad_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError):
        parse_gate_results_json(bad_path)
