"""Gozlemlenebilirlik rollback -- guvenli temel duruma (Asama 1,
observe-only) donus icin SAF karar/plan mantigi.

Gercek dosya sistemi/YAML mutasyonu `rollback_observability.ps1`
tarafindan yapilir (metin-tabanli, HEDEFLI degisiklik -- `alertmanager.yml`
icindeki degerli aciklama satirlarini korumak icin bilerek TAM bir
YAML parse+re-dump YAPILMAZ, bkz. o script'in yorumlari). Bu modul
yalnizca "mevcut durum" -> "yapilacak eylemler listesi" kararini verir,
boylece dry-run/apply ayrimi ve "zaten guvenli, degisiklik gereksiz"
mantigi gercek bir Alertmanager config dosyasi olmadan deterministik
test edilebilir.

Guvenlik ilkesi: bu modul HICBIR ZAMAN dosya yazmaz/silmez -- yalnizca
bir `RollbackPlan` uretir. `data/audit/audit.log.jsonl` ve
`data/metrics/*.jsonl` bu paketin hicbir yerinde dokunulmaz/silinmez.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SAFE_RECEIVER = "null-receiver"


@dataclass
class RollbackAction:
    description: str
    target: str
    current_value: str
    safe_value: str
    needs_change: bool


@dataclass
class RollbackPlan:
    actions: list[RollbackAction] = field(default_factory=list)
    dry_run: bool = True

    @property
    def any_change_needed(self) -> bool:
        return any(a.needs_change for a in self.actions)


def plan_rollback(
    *,
    route_receiver: str,
    warning_route_receiver: str,
    critical_route_receiver: str,
    metrics_enabled: bool,
    dry_run: bool,
) -> RollbackPlan:
    """Alertmanager route receiver'larini + `METRICS_ENABLED` bayragini
    guvenli temel duruma (Asama 1: tum route'lar `null-receiver`,
    metrics ACIK) getirmek icin gereken eylemleri hesaplar. Zaten
    guvenli durumda olan bir alan icin `needs_change=False` doner --
    gereksiz mutasyon YAPILMAZ (idempotent: art arda iki kez
    calistirmak ayni sonucu verir)."""
    actions = [
        RollbackAction(
            description="Varsayilan route receiver'i observe-only'e (null-receiver) dondur",
            target="route.receiver",
            current_value=route_receiver,
            safe_value=SAFE_RECEIVER,
            needs_change=(route_receiver != SAFE_RECEIVER),
        ),
        RollbackAction(
            description="Warning route receiver'ini observe-only'e dondur",
            target="route.routes[severity=warning].receiver",
            current_value=warning_route_receiver,
            safe_value=SAFE_RECEIVER,
            needs_change=(warning_route_receiver != SAFE_RECEIVER),
        ),
        RollbackAction(
            description="Critical route receiver'ini observe-only'e dondur",
            target="route.routes[severity=critical].receiver",
            current_value=critical_route_receiver,
            safe_value=SAFE_RECEIVER,
            needs_change=(critical_route_receiver != SAFE_RECEIVER),
        ),
        RollbackAction(
            description="/metrics endpoint'i ACIK kalmali -- rollback bunu ASLA kapatmaz",
            target="METRICS_ENABLED",
            current_value=str(metrics_enabled),
            safe_value="True",
            needs_change=(metrics_enabled is not True),
        ),
    ]
    return RollbackPlan(actions=actions, dry_run=dry_run)


def render_rollback_report(plan: RollbackPlan, *, generated_at: str) -> str:
    lines = [
        "# Observability Rollback Raporu",
        "",
        f"Uretildi (UTC): {generated_at}",
        f"Mod: {'DRY-RUN (hicbir dosya degistirilmedi)' if plan.dry_run else 'APPLY (degisiklikler uygulandi)'}",
        f"Degisiklik gerekli mi: {plan.any_change_needed}",
        "",
        "| Hedef | Mevcut | Guvenli deger | Degisiklik gerekli mi |",
        "|---|---|---|---|",
    ]
    for a in plan.actions:
        lines.append(f"| {a.target} | {a.current_value} | {a.safe_value} | {a.needs_change} |")

    lines += ["", "## Eylemler", ""]
    for a in plan.actions:
        marker = "DEGISECEK" if a.needs_change else "zaten guvenli -- degisiklik yok"
        lines.append(f"- [{marker}] {a.description}")

    lines += [
        "",
        "## Korunanlar (bu script ASLA dokunmaz)",
        "",
        "- `data/audit/audit.log.jsonl` -- audit iz kaydi",
        "- `data/metrics/*.jsonl` -- cross-process metrik verisi",
        "- `/metrics` endpoint'inin kendisi (`METRICS_ENABLED` her zaman ACIK tutulur)",
    ]
    return "\n".join(lines) + "\n"
