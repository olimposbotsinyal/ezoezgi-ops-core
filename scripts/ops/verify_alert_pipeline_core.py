"""E2E alert pipeline dogrulama -- cekirdek yardimci (JSON ayristirma + karar).

`scripts/ops/verify_alert_pipeline.ps1` bu modulu cagirir. Prometheus/
Alertmanager'in gercek REST API yanit sekillerini ayristiran saf
fonksiyonlar burada -- boylece testler gercek bir Prometheus/Alertmanager
sureci calistirmadan, sabit JSON fixture'lariyla ayristirma+karar
mantigini dogrulayabilir.

Cikis kodu kararlari (gorev tanimindan):
  0 -> basari (metrics UP + Prometheus target UP + alert firing/pending +
       Alertmanager'da goruldu)
  1 -> kismi (metrics UP ama Prometheus/Alertmanager erisilemez/alert
       hicbirine yansimadi -- bu makinede varsayilan/beklenen durum,
       cunku Prometheus/Alertmanager kurulu degil)
  2 -> basarisiz (metrics endpoint'inin kendisi bile erisilemez)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

EXIT_SUCCESS = 0
EXIT_PARTIAL = 1
EXIT_FAILED = 2


def parse_prometheus_targets(data: dict[str, Any], job_name: str) -> list[dict[str, Any]]:
    """Prometheus `/api/v1/targets` yanitindan verilen job'a ait hedefleri
    cikarir. Beklenmeyen sekil icin bos liste doner (hata firlatmaz)."""
    try:
        active = data["data"]["activeTargets"]
    except (KeyError, TypeError):
        return []
    return [t for t in active if t.get("labels", {}).get("job") == job_name]


def parse_prometheus_alerts(data: dict[str, Any], alertname: str) -> list[dict[str, Any]]:
    """Prometheus `/api/v1/alerts` yanitindan verilen alertname'e ait
    kayitlari cikarir (state: pending/firing/inactive)."""
    try:
        alerts = data["data"]["alerts"]
    except (KeyError, TypeError):
        return []
    return [a for a in alerts if a.get("labels", {}).get("alertname") == alertname]


def parse_alertmanager_alerts(data: list[dict[str, Any]], alertname: str) -> list[dict[str, Any]]:
    """Alertmanager `/api/v2/alerts` yanitindan (bir liste) verilen
    alertname'e ait kayitlari cikarir."""
    if not isinstance(data, list):
        return []
    return [a for a in data if a.get("labels", {}).get("alertname") == alertname]


@dataclass
class PipelineVerification:
    exit_code: int
    findings: list[str] = field(default_factory=list)
    metrics_reachable: bool = False
    prometheus_reachable: bool = False
    target_up: bool = False
    alert_firing_or_pending: bool = False
    alertmanager_reachable: bool = False
    alert_in_alertmanager: bool = False


def evaluate_pipeline(
    *,
    metrics_reachable: bool,
    prometheus_reachable: bool,
    target_health: str | None,
    prom_alert_states: list[str],
    alertmanager_reachable: bool,
    am_alert_found: bool,
) -> PipelineVerification:
    findings: list[str] = []

    if not metrics_reachable:
        findings.append("metrics endpoint erisilemez -- en temel adim basarisiz")
        return PipelineVerification(exit_code=EXIT_FAILED, findings=findings, metrics_reachable=False)

    findings.append("metrics endpoint erisilebilir")

    if not prometheus_reachable:
        findings.append(
            "Prometheus erisilemez (bu makinede kurulu degilse beklenen durum) -- kismi basari"
        )
        return PipelineVerification(
            exit_code=EXIT_PARTIAL, findings=findings, metrics_reachable=True, prometheus_reachable=False
        )

    findings.append("Prometheus erisilebilir")
    target_up = target_health == "up"
    if not target_up:
        findings.append(f"Prometheus hedefi UP degil (health={target_health!r})")
        return PipelineVerification(
            exit_code=EXIT_FAILED,
            findings=findings,
            metrics_reachable=True,
            prometheus_reachable=True,
            target_up=False,
        )

    findings.append("Prometheus hedefi UP")
    alert_active = any(s in ("firing", "pending") for s in prom_alert_states)
    if not alert_active:
        findings.append("Sentetik sinyal sonrasi alert Prometheus'ta firing/pending olarak gorunmedi")
        return PipelineVerification(
            exit_code=EXIT_PARTIAL,
            findings=findings,
            metrics_reachable=True,
            prometheus_reachable=True,
            target_up=True,
            alert_firing_or_pending=False,
        )

    findings.append("Alert Prometheus'ta firing/pending")

    if not alertmanager_reachable:
        findings.append("Alertmanager erisilemez (bu makinede kurulu degilse beklenen durum) -- kismi basari")
        return PipelineVerification(
            exit_code=EXIT_PARTIAL,
            findings=findings,
            metrics_reachable=True,
            prometheus_reachable=True,
            target_up=True,
            alert_firing_or_pending=True,
            alertmanager_reachable=False,
        )

    findings.append("Alertmanager erisilebilir")

    if not am_alert_found:
        findings.append("Alert Alertmanager'da gorunmedi -- routing sorunlu olabilir")
        return PipelineVerification(
            exit_code=EXIT_PARTIAL,
            findings=findings,
            metrics_reachable=True,
            prometheus_reachable=True,
            target_up=True,
            alert_firing_or_pending=True,
            alertmanager_reachable=True,
            alert_in_alertmanager=False,
        )

    findings.append("Alert Alertmanager'da gorundu -- tam E2E dogrulama basarili")
    return PipelineVerification(
        exit_code=EXIT_SUCCESS,
        findings=findings,
        metrics_reachable=True,
        prometheus_reachable=True,
        target_up=True,
        alert_firing_or_pending=True,
        alertmanager_reachable=True,
        alert_in_alertmanager=True,
    )
