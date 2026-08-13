"""Gozlemlenebilirlik drift tespiti -- saf mantik (dosya/sistem G/Ç yok).

`scripts/ops/detect_observability_drift.py` gercek dosyalari/JSONL
olaylarini okuyup bu modulun karsilastirma fonksiyonlarina besler --
boylece "hangi ham girdi hangi DriftFinding'e karsilik gelir" mantigi,
gercek bir /metrics veya alert kural dosyasi olmadan deterministik test
edilebilir (bkz. `observability_gates_core.py`/`verify_alert_pipeline_core.py`
ile AYNI ayrim ilkesi).

Siddet seviyeleri ve exit code haritasi (gorev tanimindan):
  0 -> drift yok
  1 -> kritik olmayan drift (WARN)
  2 -> kritik drift (CRITICAL)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

SEVERITY_NONE = "NONE"
SEVERITY_WARN = "WARN"
SEVERITY_CRITICAL = "CRITICAL"

VALID_SEVERITIES = (SEVERITY_NONE, SEVERITY_WARN, SEVERITY_CRITICAL)

EXIT_NO_DRIFT = 0
EXIT_NON_CRITICAL = 1
EXIT_CRITICAL = 2

CATEGORY_METRICS_SCHEMA = "metrics_schema"
CATEGORY_ALERT_RULES = "alert_rules"
CATEGORY_REMOTE_DEFAULT = "remote_default"
CATEGORY_STRICT_FLAG = "strict_flag"
CATEGORY_EMERGENCY_GOVERNANCE = "emergency_governance"


@dataclass
class DriftFinding:
    category: str
    severity: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.severity not in VALID_SEVERITIES:
            raise ValueError(f"gecersiz siddet: {self.severity!r} (gecerli: {VALID_SEVERITIES})")


def overall_drift_exit_code(findings: list[DriftFinding]) -> int:
    if any(f.severity == SEVERITY_CRITICAL for f in findings):
        return EXIT_CRITICAL
    if any(f.severity == SEVERITY_WARN for f in findings):
        return EXIT_NON_CRITICAL
    return EXIT_NO_DRIFT


def overall_drift_status(findings: list[DriftFinding]) -> str:
    return {
        EXIT_NO_DRIFT: SEVERITY_NONE,
        EXIT_NON_CRITICAL: SEVERITY_WARN,
        EXIT_CRITICAL: SEVERITY_CRITICAL,
    }[overall_drift_exit_code(findings)]


# ---------------------------------------------------------------------------
# Metrik semasi drift'i (metrik adi/etiket/tip -- baseline manifest'e gore)
# ---------------------------------------------------------------------------


def compare_metrics_schema(
    observed: dict[str, dict[str, Any]], baseline: dict[str, Any]
) -> list[DriftFinding]:
    """`observed`: {metrik_adi: {"type": ..., "labels": [...]}} -- gozlenen
    (gercek /metrics penceresinden cikarilmis) sema. `baseline`: tam
    manifest dict'i (`business_metrics` + `self_observability_metrics` +
    `always_allowed_extra_labels`). Yalnizca PENCEREDE GERCEKTEN
    GOZLENEN metrikler karsilastirilir -- trafik olmadigi icin hic
    gorulmeyen bir metrik DRIFT SAYILMAZ (bu, "hic trafik yok" ile
    "metrik kaldirildi" ayrimini yapamayacagimiz icin bilincli bir
    tasarim karari)."""
    findings: list[DriftFinding] = []
    allowed_extra = set(baseline.get("always_allowed_extra_labels", []))
    known: dict[str, Any] = {}
    known.update(baseline.get("business_metrics", {}))
    known.update(baseline.get("self_observability_metrics", {}))

    for name, obs in observed.items():
        if name not in known:
            findings.append(
                DriftFinding(
                    CATEGORY_METRICS_SCHEMA,
                    SEVERITY_WARN,
                    f"Bilinmeyen (manifestte olmayan) metrik gozlendi: '{name}'",
                    evidence={"metric": name, "observed_labels": sorted(obs.get("labels", []))},
                )
            )
            continue

        expected_type = known[name]["type"]
        observed_type = obs.get("type")
        if observed_type and observed_type != expected_type:
            findings.append(
                DriftFinding(
                    CATEGORY_METRICS_SCHEMA,
                    SEVERITY_CRITICAL,
                    f"'{name}' metrik TIPI degisti: beklenen={expected_type}, gozlenen={observed_type}",
                    evidence={"metric": name, "expected_type": expected_type, "observed_type": observed_type},
                )
            )

        expected_labels = set(known[name]["labels"])
        observed_labels = set(obs.get("labels", []))
        unexpected = observed_labels - expected_labels - allowed_extra
        missing = expected_labels - observed_labels
        if unexpected:
            findings.append(
                DriftFinding(
                    CATEGORY_METRICS_SCHEMA,
                    SEVERITY_WARN,
                    f"'{name}' icin beklenmeyen etiket(ler): {sorted(unexpected)}",
                    evidence={"metric": name, "unexpected_labels": sorted(unexpected)},
                )
            )
        if missing:
            findings.append(
                DriftFinding(
                    CATEGORY_METRICS_SCHEMA,
                    SEVERITY_WARN,
                    f"'{name}' icin beklenen ama gozlenmeyen etiket(ler): {sorted(missing)}",
                    evidence={"metric": name, "missing_labels": sorted(missing)},
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Alert kural dosyasi drift'i (checksum karsilastirma)
# ---------------------------------------------------------------------------


def check_alert_rules_checksum_drift(
    observed_checksum: str,
    baseline_checksum: str,
    approved_checksums: list[str] | None = None,
) -> DriftFinding:
    """`approved_checksums`: `infra/monitoring/baseline/approved_checksums_ledger.jsonl`'den
    yuklenmis, GECMISTE `apply_threshold_proposal.ps1 -Apply` ile GERCEKTEN
    onaylanip uygulanmis tum `new_checksum` degerleri (bkz.
    `threshold_apply_core.load_approved_checksums`). Statik baseline'dan
    FARKLI ama bu listede BULUNAN bir checksum, "beklenmedik" degil
    "onayli/bilinen esik degisikligi" anlamina gelir -- CRITICAL yerine
    NONE (bilgilendirici) doner. Parametre GERIYE UYUMLU (varsayilan
    None) -- mevcut 2-pozisyonel-argumanli cagricilar/testler bozulmaz."""
    baseline_checksum = baseline_checksum.strip().lower()
    observed_checksum = observed_checksum.strip().lower()
    approved = {c.strip().lower() for c in (approved_checksums or [])}

    if observed_checksum == baseline_checksum:
        return DriftFinding(
            CATEGORY_ALERT_RULES, SEVERITY_NONE, "Alert kurallari checksum'i onayli durumla eslesiyor"
        )
    if observed_checksum in approved:
        return DriftFinding(
            CATEGORY_ALERT_RULES,
            SEVERITY_NONE,
            "Alert kurallari dosyasi degismis, ancak bu degisiklik ONAYLI DEGISIKLIK DEFTERINDE "
            "(approved_checksums_ledger.jsonl) kayitli -- beklenen/onayli bir esik degisikligi",
            evidence={"baseline_checksum": baseline_checksum, "observed_checksum": observed_checksum},
        )
    return DriftFinding(
        CATEGORY_ALERT_RULES,
        SEVERITY_CRITICAL,
        "Alert kurallari dosyasi ONAY DISI degismis (checksum ne statik baseline'a ne de onayli "
        "degisiklik defterine uyuyor) -- inceleme gerekli",
        evidence={"expected_checksum": baseline_checksum, "observed_checksum": observed_checksum},
    )


# ---------------------------------------------------------------------------
# Acil durum (APPROVE_EMERGENCY) retroaktif inceleme vadesi drift'i
# ---------------------------------------------------------------------------


def check_emergency_review_overdue_drift(
    ledger_entries: list[dict[str, Any]], *, now: datetime, use_chain_matching: bool = False
) -> list[DriftFinding]:
    """`approved_checksums_ledger.jsonl`'deki (bkz. `threshold_apply_core.load_ledger_entries`)
    HER acil durum (`is_emergency=True`) girdisi icin: `retro_review_due_utc`
    vadesi GECMIS (`now > retro_review_due_utc`) VE ayni `alert_name` icin
    SONRAKI bir NORMAL (`is_emergency=False`) apply girdisi (yani "takip
    eden normal onay") YOKSA -- CRITICAL bir `DriftFinding` uretir (gorev
    v1.1 madde 4, BIREBIR). Vadesi henuz gelmemis VEYA takip eden normal
    onayi olan girdiler icin HICBIR finding URETILMEZ (SESSIZCE gecilir --
    bu, "her seyin drift oldugu" gurultusunu onler, yalnizca GERCEKTEN
    ihlal edilen SOP'lar raporlanir).

    `use_chain_matching` (v1.2 PILOT, varsayilan False -- GERIYE UYUMLU,
    varsayilan davranis DEGISMEZ): True ise, "takip eden onay" kontrolu
    `emergency_chain_core.evaluate_chain_closure`'a devredilir -- yalnizca
    alert_name degil, checksum-ZINCIRI SUREKLILIGI de aranir. Uc sonuc:
    RESOLVED (zincir kesintisiz -> finding YOK, v1.1 ile ayni), BROKEN_CHAIN
    (ayni alert icin sonraki bir girdi var ama zincir kopuk -> WARN,
    CRITICAL DEGIL -- "tamamen takipsiz degil ama dogrulanmali"),
    NO_FOLLOWUP (v1.1 ile ayni -> CRITICAL)."""
    findings: list[DriftFinding] = []
    for entry in ledger_entries:
        if not entry.get("is_emergency"):
            continue

        retro_raw = entry.get("retro_review_due_utc")
        if not retro_raw:
            continue
        try:
            retro_dt = datetime.fromisoformat(retro_raw)
        except (ValueError, TypeError):
            continue
        if now <= retro_dt:
            continue

        alert_name = entry.get("alert_name")
        hours_overdue = (now - retro_dt).total_seconds() / 3600
        evidence = {
            "proposal_id": entry.get("proposal_id"),
            "alert_name": alert_name,
            "retro_review_due_utc": retro_raw,
            "hours_overdue": round(hours_overdue, 2),
            "apply_report_path": entry.get("apply_report_path"),
        }

        if use_chain_matching:
            from emergency_chain_core import CLOSURE_BROKEN_CHAIN, CLOSURE_RESOLVED, evaluate_chain_closure

            closure = evaluate_chain_closure(entry, ledger_entries)
            if closure == CLOSURE_RESOLVED:
                continue
            if closure == CLOSURE_BROKEN_CHAIN:
                findings.append(
                    DriftFinding(
                        CATEGORY_EMERGENCY_GOVERNANCE,
                        SEVERITY_WARN,
                        f"ACIL DURUM esik degisikligi (proposal_id={entry.get('proposal_id')}) icin "
                        "ayni alert'e sonraki bir normal apply VAR ama checksum ZINCIRI SUREKSIZ "
                        "(araya baska bir degisiklik girmis olabilir) -- elle dogrulayin",
                        evidence=evidence,
                    )
                )
                continue
            # closure == CLOSURE_NO_FOLLOWUP -> asagidaki CRITICAL yoluna devam eder
        else:
            entry_ts = str(entry.get("timestamp", ""))
            has_followup = any(
                other.get("alert_name") == alert_name
                and not other.get("is_emergency")
                and str(other.get("timestamp", "")) > entry_ts
                for other in ledger_entries
            )
            if has_followup:
                continue

        findings.append(
            DriftFinding(
                CATEGORY_EMERGENCY_GOVERNANCE,
                SEVERITY_CRITICAL,
                f"ACIL DURUM esik degisikligi (proposal_id={entry.get('proposal_id')}) retroaktif "
                f"inceleme vadesini ({retro_raw}) {hours_overdue:.1f} saat gecti, TAKIP EDEN normal "
                "onay YOK -- governance SOP'u ihlal ediliyor",
                evidence=evidence,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Config varsayilan drift'i (remote_enabled, ollama_cpu_verify_strict)
# ---------------------------------------------------------------------------


def check_remote_default_drift(observed_remote_enabled_default: bool, baseline: dict[str, Any]) -> DriftFinding:
    expected = baseline["config_defaults"]["remote_enabled"]
    if observed_remote_enabled_default == expected:
        return DriftFinding(
            CATEGORY_REMOTE_DEFAULT, SEVERITY_NONE, f"remote_enabled varsayilani beklenen gibi: {expected}"
        )
    return DriftFinding(
        CATEGORY_REMOTE_DEFAULT,
        SEVERITY_CRITICAL,
        f"remote_enabled varsayilani DEGISTI: beklenen={expected}, gozlenen={observed_remote_enabled_default} "
        "-- gorev kisitlarini (remote-default degisikligi yok) ihlal edebilir",
        evidence={"expected": expected, "observed": observed_remote_enabled_default},
    )


def check_strict_flag_drift(observed_strict_default: bool, baseline: dict[str, Any]) -> DriftFinding:
    expected = baseline["config_defaults"]["ollama_cpu_verify_strict"]
    if observed_strict_default == expected:
        return DriftFinding(
            CATEGORY_STRICT_FLAG,
            SEVERITY_NONE,
            f"ollama_cpu_verify_strict varsayilani beklenen gibi: {expected}",
        )
    return DriftFinding(
        CATEGORY_STRICT_FLAG,
        SEVERITY_WARN,
        f"ollama_cpu_verify_strict varsayilani degisti: beklenen={expected}, gozlenen={observed_strict_default} "
        "-- kasitli bir operator karari olabilir, ama dogrulanmali",
        evidence={"expected": expected, "observed": observed_strict_default},
    )


# ---------------------------------------------------------------------------
# Rapor uretimi
# ---------------------------------------------------------------------------


def render_drift_report_md(findings: list[DriftFinding], *, generated_at: str, window_label: str) -> str:
    lines = [
        "# Gozlemlenebilirlik Drift Raporu -- Model Gateway",
        "",
        f"Uretildi (UTC): {generated_at}",
        f"Pencere: {window_label}",
        f"Genel sonuc: **{overall_drift_status(findings)}** (exit code {overall_drift_exit_code(findings)})",
        "",
    ]
    if not findings:
        lines.append("Hicbir kontrolde drift bulunamadi.")
    else:
        lines += ["| Kategori | Siddet | Aciklama |", "|---|---|---|"]
        for f in findings:
            lines.append(f"| {f.category} | {f.severity} | {f.detail} |")

    lines += ["", "## Kanitlar (ham veri)", ""]
    for f in findings:
        if f.evidence:
            lines.append(f"### {f.category} -- {f.severity}")
            lines.append("```json")
            lines.append(json.dumps(f.evidence, indent=2, ensure_ascii=False))
            lines.append("```")
            lines.append("")

    return "\n".join(lines) + "\n"


def write_drift_report_json(
    findings: list[DriftFinding], path: Path, *, generated_at: str, window_label: str
) -> None:
    payload = {
        "generated_at": generated_at,
        "window_label": window_label,
        "overall_status": overall_drift_status(findings),
        "overall_exit_code": overall_drift_exit_code(findings),
        "findings": [asdict(f) for f in findings],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
