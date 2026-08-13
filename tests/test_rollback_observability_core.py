"""`rollback_observability_core.py` testleri -- dry-run/apply plan
mantigi (gorevin acikca istedigi test odagi): zaten guvenli durumda
degisiklik onerilmemeli, escalate edilmis bir durumda degisiklik dogru
tespit edilmeli, `/metrics` (METRICS_ENABLED) hicbir zaman kapatilmaya
CALISILMAMALI, dry_run bayragi rapora doğru yansimali."""

from __future__ import annotations

from rollback_observability_core import SAFE_RECEIVER, plan_rollback, render_rollback_report


def test_already_safe_state_needs_no_changes():
    plan = plan_rollback(
        route_receiver=SAFE_RECEIVER,
        warning_route_receiver=SAFE_RECEIVER,
        critical_route_receiver=SAFE_RECEIVER,
        metrics_enabled=True,
        dry_run=True,
    )
    assert plan.any_change_needed is False
    assert all(not a.needs_change for a in plan.actions)


def test_escalated_warning_and_critical_routes_are_detected():
    plan = plan_rollback(
        route_receiver=SAFE_RECEIVER,
        warning_route_receiver="warning-sink",
        critical_route_receiver="critical-sink",
        metrics_enabled=True,
        dry_run=True,
    )
    assert plan.any_change_needed is True
    warning_action = next(a for a in plan.actions if "warning" in a.target)
    critical_action = next(a for a in plan.actions if "critical" in a.target)
    assert warning_action.needs_change is True
    assert warning_action.safe_value == SAFE_RECEIVER
    assert critical_action.needs_change is True
    assert critical_action.safe_value == SAFE_RECEIVER


def test_metrics_disabled_is_flagged_as_needing_change_back_to_enabled():
    """Rollback /metrics'i ASLA kapatmaz -- ama eger bir sekilde
    kapatilmissa (METRICS_ENABLED=false), bu bir 'guvenli duruma donus
    gerekiyor' bulgusu olarak isaretlenmeli (metrics AKTIF olmasi
    guvenli/beklenen durumdur)."""
    plan = plan_rollback(
        route_receiver=SAFE_RECEIVER,
        warning_route_receiver=SAFE_RECEIVER,
        critical_route_receiver=SAFE_RECEIVER,
        metrics_enabled=False,
        dry_run=True,
    )
    metrics_action = next(a for a in plan.actions if a.target == "METRICS_ENABLED")
    assert metrics_action.needs_change is True
    assert metrics_action.safe_value == "True"


def test_plan_is_idempotent_second_run_on_already_fixed_state_needs_no_changes():
    first = plan_rollback(
        route_receiver=SAFE_RECEIVER, warning_route_receiver="warning-sink",
        critical_route_receiver="critical-sink", metrics_enabled=True, dry_run=True,
    )
    assert first.any_change_needed is True

    # Ilk calistirmadan SONRAKI (varsayimsal) duzeltilmis durum:
    second = plan_rollback(
        route_receiver=SAFE_RECEIVER, warning_route_receiver=SAFE_RECEIVER,
        critical_route_receiver=SAFE_RECEIVER, metrics_enabled=True, dry_run=True,
    )
    assert second.any_change_needed is False


def test_dry_run_flag_is_reflected_in_plan_and_report():
    plan_dry = plan_rollback(
        route_receiver=SAFE_RECEIVER, warning_route_receiver="warning-sink",
        critical_route_receiver=SAFE_RECEIVER, metrics_enabled=True, dry_run=True,
    )
    plan_apply = plan_rollback(
        route_receiver=SAFE_RECEIVER, warning_route_receiver="warning-sink",
        critical_route_receiver=SAFE_RECEIVER, metrics_enabled=True, dry_run=False,
    )
    assert plan_dry.dry_run is True
    assert plan_apply.dry_run is False

    report_dry = render_rollback_report(plan_dry, generated_at="2026-08-13T00:00:00Z")
    report_apply = render_rollback_report(plan_apply, generated_at="2026-08-13T00:00:00Z")
    assert "DRY-RUN" in report_dry
    assert "hicbir dosya degistirilmedi" in report_dry
    assert "APPLY" in report_apply


def test_render_rollback_report_lists_protected_paths():
    plan = plan_rollback(
        route_receiver=SAFE_RECEIVER, warning_route_receiver=SAFE_RECEIVER,
        critical_route_receiver=SAFE_RECEIVER, metrics_enabled=True, dry_run=True,
    )
    report = render_rollback_report(plan, generated_at="2026-08-13T00:00:00Z")
    assert "data/audit/audit.log.jsonl" in report
    assert "data/metrics/*.jsonl" in report


def test_render_rollback_report_marks_needs_change_actions_distinctly():
    plan = plan_rollback(
        route_receiver=SAFE_RECEIVER, warning_route_receiver="warning-sink",
        critical_route_receiver=SAFE_RECEIVER, metrics_enabled=True, dry_run=True,
    )
    report = render_rollback_report(plan, generated_at="2026-08-13T00:00:00Z")
    assert "[DEGISECEK]" in report
    assert "[zaten guvenli -- degisiklik yok]" in report
