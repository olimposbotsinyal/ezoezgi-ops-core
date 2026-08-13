"""verify_alert_pipeline_core.py testleri -- sabit JSON fixture'lariyla,
gercek Prometheus/Alertmanager gerektirmez."""

from __future__ import annotations

from verify_alert_pipeline_core import (
    EXIT_FAILED,
    EXIT_PARTIAL,
    EXIT_SUCCESS,
    evaluate_pipeline,
    parse_alertmanager_alerts,
    parse_prometheus_alerts,
    parse_prometheus_targets,
)

PROM_TARGETS_FIXTURE = {
    "status": "success",
    "data": {
        "activeTargets": [
            {
                "labels": {"job": "ezoezgi_model_gateway", "instance": "127.0.0.1:9108"},
                "health": "up",
                "lastError": "",
            },
            {"labels": {"job": "other_job", "instance": "x:1"}, "health": "down", "lastError": "boom"},
        ]
    },
}

PROM_ALERTS_FIXTURE = {
    "status": "success",
    "data": {
        "alerts": [
            {"labels": {"alertname": "ModelGatewayFallbackSpike"}, "state": "firing"},
            {"labels": {"alertname": "OtherAlert"}, "state": "pending"},
        ]
    },
}

AM_ALERTS_FIXTURE = [
    {"labels": {"alertname": "ModelGatewayFallbackSpike"}, "status": {"state": "active"}},
]


def test_parse_prometheus_targets_filters_by_job():
    targets = parse_prometheus_targets(PROM_TARGETS_FIXTURE, "ezoezgi_model_gateway")

    assert len(targets) == 1
    assert targets[0]["health"] == "up"


def test_parse_prometheus_targets_handles_malformed_input():
    assert parse_prometheus_targets({"unexpected": "shape"}, "job") == []
    assert parse_prometheus_targets(None, "job") == []


def test_parse_prometheus_alerts_filters_by_alertname():
    alerts = parse_prometheus_alerts(PROM_ALERTS_FIXTURE, "ModelGatewayFallbackSpike")

    assert len(alerts) == 1
    assert alerts[0]["state"] == "firing"


def test_parse_alertmanager_alerts_filters_by_alertname():
    alerts = parse_alertmanager_alerts(AM_ALERTS_FIXTURE, "ModelGatewayFallbackSpike")

    assert len(alerts) == 1


def test_verify_pipeline_parses_prometheus_alert_states_end_to_end():
    """Fixture'lari gercek parse fonksiyonlariyla isleyip evaluate_pipeline'a
    besler -- tam basarili senaryo."""
    targets = parse_prometheus_targets(PROM_TARGETS_FIXTURE, "ezoezgi_model_gateway")
    target_health = targets[0]["health"] if targets else None
    prom_alerts = parse_prometheus_alerts(PROM_ALERTS_FIXTURE, "ModelGatewayFallbackSpike")
    am_alerts = parse_alertmanager_alerts(AM_ALERTS_FIXTURE, "ModelGatewayFallbackSpike")

    result = evaluate_pipeline(
        metrics_reachable=True,
        prometheus_reachable=True,
        target_health=target_health,
        prom_alert_states=[a["state"] for a in prom_alerts],
        alertmanager_reachable=True,
        am_alert_found=len(am_alerts) > 0,
    )

    assert result.exit_code == EXIT_SUCCESS
    assert result.alert_in_alertmanager is True


def test_evaluate_pipeline_metrics_unreachable_is_failed():
    result = evaluate_pipeline(
        metrics_reachable=False,
        prometheus_reachable=False,
        target_health=None,
        prom_alert_states=[],
        alertmanager_reachable=False,
        am_alert_found=False,
    )

    assert result.exit_code == EXIT_FAILED


def test_evaluate_pipeline_no_prometheus_is_partial():
    """Bu makinenin gercek durumu: metrics UP ama Prometheus kurulu degil."""
    result = evaluate_pipeline(
        metrics_reachable=True,
        prometheus_reachable=False,
        target_health=None,
        prom_alert_states=[],
        alertmanager_reachable=False,
        am_alert_found=False,
    )

    assert result.exit_code == EXIT_PARTIAL


def test_evaluate_pipeline_alert_not_routed_to_alertmanager_is_partial():
    result = evaluate_pipeline(
        metrics_reachable=True,
        prometheus_reachable=True,
        target_health="up",
        prom_alert_states=["firing"],
        alertmanager_reachable=True,
        am_alert_found=False,
    )

    assert result.exit_code == EXIT_PARTIAL


def test_evaluate_pipeline_target_down_is_failed():
    result = evaluate_pipeline(
        metrics_reachable=True,
        prometheus_reachable=True,
        target_health="down",
        prom_alert_states=[],
        alertmanager_reachable=False,
        am_alert_found=False,
    )

    assert result.exit_code == EXIT_FAILED
