"""Onaylanmis bir esik degisikligini GERCEKTEN uygulama/geri alma icin
saf mantik + dosya mutasyon yardimcilari.

`scripts/ops/apply_threshold_proposal.ps1` ve `scripts/ops/rollback_threshold_apply.ps1`
bu modulu kullanir. Regex tabanli yama fonksiyonlari (`patch_alert_expr_value`)
KASITLI olarak TAM bir YAML parse+re-dump YAPMAZ -- `rollback_observability.ps1`'de
(bkz. o script'in yorumlari) ayni gerekceyle: `model_gateway_alerts.yaml`
degerli aciklama satirlari icerir, tam yeniden serilestirme bunlari
YOK EDER. Bunun yerine, her alert/esik-turu icin BENZERSIZ (dosyadaki
`[6h]`/`[1h]`/`[15m]` pencere etiketleri sayesinde ayirt edici) bir
regex, YALNIZCA sayisal esik degerini degistirir -- geri kalan HER SEY
(yorumlar, bicimlendirme, diger alert'ler) birebir korunur.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

@dataclass(frozen=True)
class PatchTarget:
    """`pattern`, dosyada TAM OLARAK 1 kez eslesmesi BEKLENEN bir regex --
    `multi_target=True` ACIKCA isaretlenmedikce, 0 VEYA >1 eslesme GUVENLIK
    NEDENIYLE reddedilir (bkz. patch_alert_expr_value). Her desen TAM
    OLARAK 3 yakalama grubu icerir: (on-ek, SAYISAL_DEGER, son-ek)."""

    pattern: re.Pattern[str]
    multi_target: bool = False


# Her (alert_name, esik_turu) icin, dosyadaki BENZERSIZ pencere etiketi
# sayesinde baska hicbir yeri yanlislikla eslestirmeyen bir desen.
# -- FALLBACK_SPIKE'ta son-ek doludur (" * sum"), digerlerinde bostur --
# boylece `patch_alert_expr_value` HER ZAMAN ayni sekilde (m.group(1) + yeni_deger + m.group(3))
# yeniden birlestirebilir.
ALERT_EXPR_PATCH_PATTERNS: dict[str, dict[str, PatchTarget]] = {
    "HIGH_NULL_INTENT_RATE": {
        "warn": PatchTarget(re.compile(
            r"(model_gateway_null_intent_total\[6h\]\)\) / sum\(rate\(model_gateway_requests_total\[6h\]\)\)\) > )"
            r"([0-9]+(?:\.[0-9]+)?)()"
        )),
        "crit": PatchTarget(re.compile(
            r"(model_gateway_null_intent_total\[1h\]\)\) / sum\(rate\(model_gateway_requests_total\[1h\]\)\)\) > )"
            r"([0-9]+(?:\.[0-9]+)?)()"
        )),
    },
    "FALLBACK_SPIKE": {
        "warn": PatchTarget(re.compile(r"(sum\(rate\(model_gateway_fallback_total\[15m\]\)\) > \()([0-9]+(?:\.[0-9]+)?)( \* sum)")),
        "crit": PatchTarget(re.compile(r"(sum\(rate\(model_gateway_fallback_total\[15m\]\)\) > \()([0-9]+(?:\.[0-9]+)?)( \* sum)")),
    },
}

# Esik-tasiyan (numerik bir karsilastirma iceren) ama GERCEK bir
# ortam-degiskeni/esik esleme YOKLUGU nedeniyle KASITLI olarak
# `ALERT_EXPR_PATCH_PATTERNS`'e EKLENMEYEN alertler -- `calibrate_alert_thresholds.ALERT_ENV_VAR_MAP`
# ile SENKRON tutulmalidir (orada warn/crit=None olan alertler burada
# da YAMALANAMAZ olarak isaretlenir). Apply raporunun "non-patchable"
# bolumunde HER ZAMAN gosterilir (gorev v1.1 madde 2) -- boylece "bu
# alert neden desteklenmiyor" sorusu HER apply raporunda acikca
# yanitlanir, sessizce atlanmaz.
NON_PATCHABLE_ALERTS: dict[str, str] = {
    "PRIMARY_RESTRICTED_PERSISTENT": (
        "calibrate_alert_thresholds.ALERT_ENV_VAR_MAP'te warn/crit=None -- ayarlanabilir bir ortam "
        "degiskeni YOK (herhangi bir olusum > 0 tetikler, 'olmamasi gereken durum' tipi bir alert, "
        "oran-tabanli kalibrasyona uygun degil)."
    ),
    "PREFLIGHT_UNKNOWN_PERSISTENT": (
        "calibrate_alert_thresholds.ALERT_ENV_VAR_MAP'te warn/crit=None -- esik (>0.9) kodda sabit, "
        "ayarlanabilir bir ortam degiskeni YOK; degistirmek icin once model_gateway kod tarafinda "
        "bir env var eklenmesi gerekir."
    ),
    "CIRCUIT_OPEN_STUCK": (
        "Sayisal/oransal bir esik degil -- boolean durum kontrolu (`model_gateway_circuit_open == 1`), "
        "esik kalibrasyonu/onay is akisinin kapsami disinda."
    ),
}


def get_non_patchable_alerts() -> dict[str, str]:
    return dict(NON_PATCHABLE_ALERTS)


def _format_threshold_value(value: float) -> str:
    """Gereksiz sondaki sifirlari temizler (0.100000 -> 0.1, 3.000000 -> 3)."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def patch_alert_expr_value(
    text: str, alert_name: str, threshold_kind: str, new_value: float
) -> tuple[str, bool, str | None]:
    """`text` (model_gateway_alerts.yaml icerigi) icinde, `alert_name`/
    `threshold_kind` ('warn'/'crit') icin BILINEN deseni bulup sayisal
    degeri `new_value` ile degistirir. Doner: (yeni_metin, changed, hata_nedeni).
    `hata_nedeni`, `changed=False` oldugunda BOS DEGILDIR (SESSIZ
    basarisizlik ASLA olmaz):
      - alert/esik-turu icin bilinen bir desen yoksa (bkz. `NON_PATCHABLE_ALERTS`
        ozel bir nedenle, bilinmeyen kombinasyonlar genel bir nedenle),
      - desen dosyada HIC eslesmezse (0 eslesme),
      - desen `multi_target=False` iken 1'DEN FAZLA eslesirse (guvenlik
        icin FAIL-LOUD -- yanlis yerin yamalanma riski yerine HICBIR
        SEY degistirilmez)."""
    target = ALERT_EXPR_PATCH_PATTERNS.get(alert_name, {}).get(threshold_kind)
    if target is None:
        if alert_name in NON_PATCHABLE_ALERTS:
            return text, False, NON_PATCHABLE_ALERTS[alert_name]
        return text, False, f"'{alert_name}'/'{threshold_kind}' icin bilinen bir yama deseni YOK"

    matches = list(target.pattern.finditer(text))
    if len(matches) == 0:
        return text, False, "desen dosyada HIC eslesmedi (0 eslesme) -- dosya beklenenden farkli olabilir"
    if len(matches) > 1 and not target.multi_target:
        return text, False, (
            f"desen BEKLENENDEN FAZLA eslesti ({len(matches)} eslesme) -- guvenlik icin IPTAL edildi "
            "(yalnizca `multi_target=True` olarak ACIKCA isaretlenen desenler 1'den fazla eslesmeye izin verir)"
        )

    formatted = _format_threshold_value(new_value)

    def _replace(m: re.Match[str]) -> str:
        return f"{m.group(1)}{formatted}{m.group(3)}"

    if target.multi_target:
        new_text = target.pattern.sub(_replace, text)
    else:
        new_text = target.pattern.sub(_replace, text, count=1)
    return new_text, True, None


def apply_proposal_to_alerts_text(
    text: str, proposal: dict[str, Any]
) -> tuple[str, list[str], list[dict[str, str]]]:
    """`proposal['proposed_values']`'deki HER anahtar (warn/crit) icin
    metni sirayla yamalar. Doner: (yeni_metin, basarili_anahtarlar,
    basarisiz_anahtarlar). `basarisiz_anahtarlar`, `{"kind": ..., "reason": ...}`
    sozlukleri listesidir -- cagiran taraf (apply script'i) bunu GERCEK
    bir hata olarak ele almalidir (kismi/sessiz basarisizlik asla
    olmamali); yeniden AYNI proposal ile cagirmak IDEMPOTENT'tir (metin
    zaten hedef degeri iceriyorsa, sonuc BIREBIR ayni kalir)."""
    alert_name = proposal["alert_name"]
    succeeded: list[str] = []
    failed: list[dict[str, str]] = []
    for kind, new_value in proposal["proposed_values"].items():
        text, changed, reason = patch_alert_expr_value(text, alert_name, kind, new_value)
        if changed:
            succeeded.append(kind)
        else:
            failed.append({"kind": kind, "reason": reason or "bilinmeyen neden"})
    return text, succeeded, failed


# ---------------------------------------------------------------------------
# Yedekleme (backup) + geri yukleme (restore)
# ---------------------------------------------------------------------------


def create_backup(source_path: Path, backup_dir: Path, *, label: str) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{source_path.name}.{label}.backup"
    backup_path.write_bytes(source_path.read_bytes())
    return backup_path


def restore_backup(backup_path: Path, target_path: Path) -> None:
    target_path.write_bytes(backup_path.read_bytes())


# ---------------------------------------------------------------------------
# Onayli-degisiklik defteri (approved checksums ledger) -- drift entegrasyonu
# ---------------------------------------------------------------------------


def build_ledger_entry(
    *,
    proposal_id: str,
    old_checksum: str,
    new_checksum: str,
    apply_report_path: str,
    timestamp: str | None = None,
    alert_name: str = "",
    is_emergency: bool = False,
    retro_review_due_utc: str | None = None,
) -> dict[str, Any]:
    """`alert_name`/`is_emergency`/`retro_review_due_utc` GERIYE UYUMLU
    varsayilanlarla EKLENDI (v1.1) -- `alert_name`, drift'in "ayni alert
    icin sonraki normal onay" (follow-up) kontrolunu yapabilmesi icin;
    `is_emergency`/`retro_review_due_utc`, `check_emergency_review_overdue_drift`'in
    review_record.json'a GERI DONMEDEN, YALNIZCA ledger'dan acil durum
    vadelerini takip edebilmesi icin (bkz. observability_drift_core.py)."""
    return {
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "proposal_id": proposal_id,
        "alert_name": alert_name,
        "old_checksum": old_checksum,
        "new_checksum": new_checksum,
        "apply_report_path": apply_report_path,
        "is_emergency": is_emergency,
        "retro_review_due_utc": retro_review_due_utc,
    }


def append_ledger_entry(ledger_path: Path, entry: dict[str, Any]) -> None:
    """`approved_checksums_ledger.jsonl`'e append-only bir satir ekler
    -- var olan satirlar ASLA degistirilmez/silinmez (audit.log.jsonl
    ile AYNI ilke)."""
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_ledger_entries(ledger_path: Path) -> list[dict[str, Any]]:
    """Defterdeki TUM satirlari (ham dict olarak) doner -- dosya yoksa
    (henuz hic apply yapilmamis, NORMAL durum) bos liste doner. Bozuk
    (JSON olarak ayristirilamayan) satirlar SESSIZCE atlanir (tum
    defteri gecersiz kilmaz). `load_approved_checksums` (checksum-only)
    VE `check_emergency_review_overdue_drift` (tam alan erisimi
    gerektirir) TARAFINDAN kullanilan ORTAK, genel-amacli yukleyici."""
    if not ledger_path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def load_approved_checksums(ledger_path: Path) -> list[str]:
    """Defterdeki TUM `new_checksum` degerlerini doner. `new_checksum`
    alani eksik/gecersiz tek satirlar SESSIZCE atlanir."""
    checksums: list[str] = []
    for entry in load_ledger_entries(ledger_path):
        try:
            checksums.append(str(entry["new_checksum"]))
        except (KeyError, TypeError):
            continue
    return checksums


# ---------------------------------------------------------------------------
# Audit kaydi detaylari (gercek AuditLogger -- apps/orchestrator/src/audit_logger.py
# -- ile birlikte kullanilir, burada yalnizca 'details' payload'i insa edilir)
# ---------------------------------------------------------------------------


def build_apply_audit_details(
    *,
    proposal: dict[str, Any],
    review_record: dict[str, Any],
    backup_path: Path,
    patched_kinds: list[str],
    old_checksum: str,
    new_checksum: str,
) -> dict[str, Any]:
    """`review_record['decision'] == DECISION_APPROVE_EMERGENCY` ise,
    audit kaydinin KENDISINDE de acil durum alanlari (`incident_id` vb.)
    ACIKCA gorunur -- denetim gunlugunun kendisi, hangi degisikliklerin
    acil-durum yoluyla uygulandigini, review_record.json dosyasina TEKRAR
    bakmaya GEREK KALMADAN gosterebilmelidir."""
    details: dict[str, Any] = {
        "proposal_id": proposal["proposal_id"],
        "alert_name": proposal["alert_name"],
        "current_values": proposal["current_values"],
        "proposed_values": proposal["proposed_values"],
        "patched_kinds": patched_kinds,
        "reviewer": review_record["reviewer"],
        "review_decision": review_record["decision"],
        "review_rationale": review_record["rationale"],
        "backup_path": str(backup_path),
        "old_file_checksum": old_checksum,
        "new_file_checksum": new_checksum,
        "is_emergency": review_record.get("decision") == "APPROVE_EMERGENCY",
    }
    if details["is_emergency"]:
        details["emergency_fields"] = {
            "incident_id": review_record.get("incident_id"),
            "justification": review_record.get("justification"),
            "timebox_hours": review_record.get("timebox_hours"),
            "retro_review_due_utc": review_record.get("retro_review_due_utc"),
        }
    return details


def build_rollback_audit_details(
    *, proposal_id: str, backup_path: Path, restored_checksum: str, reason: str
) -> dict[str, Any]:
    return {
        "proposal_id": proposal_id,
        "backup_path": str(backup_path),
        "restored_checksum": restored_checksum,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# VerifyReload dogrulama sonuc yardimcilari (saf mantik -- GERCEK
# promtool/amtool/HTTP cagrilari `apply_threshold_proposal.ps1`'in
# gomulu Python surucusunde kalir, bu YALNIZCA sonuc-sekli/toplama
# mantigidir, boylece PASS/FAIL/VERIFICATION_SKIPPED semantigi gercek
# binary'ler olmadan da deterministik test edilebilir)
# ---------------------------------------------------------------------------

VERIFY_PASS = "PASS"
VERIFY_FAIL = "FAIL"
VERIFY_SKIPPED = "VERIFICATION_SKIPPED"


def build_skipped_check(check_name: str, reason: str) -> dict[str, str]:
    return {"check": check_name, "state": VERIFY_SKIPPED, "reason": reason}


def build_pass_check(check_name: str, reason: str) -> dict[str, str]:
    return {"check": check_name, "state": VERIFY_PASS, "reason": reason}


def build_fail_check(check_name: str, reason: str) -> dict[str, str]:
    return {"check": check_name, "state": VERIFY_FAIL, "reason": reason}


def aggregate_verification_state(checks: list[dict[str, str]]) -> str:
    """`VerifyReload` kontrol sonuclarinin GENEL durumunu belirler:
    HERHANGI biri FAIL ise FAIL; TUMU VERIFICATION_SKIPPED ise
    VERIFICATION_SKIPPED; aksi halde (en az bir PASS, hic FAIL yok) PASS.
    ASLA fabrike bir PASS uretmez -- bos liste de VERIFICATION_SKIPPED
    doner (hicbir kontrol calismadi, bu PASS ile KARISTIRILAMAZ)."""
    if not checks:
        return VERIFY_SKIPPED
    states = [c["state"] for c in checks]
    if VERIFY_FAIL in states:
        return VERIFY_FAIL
    if all(s == VERIFY_SKIPPED for s in states):
        return VERIFY_SKIPPED
    return VERIFY_PASS


# ---------------------------------------------------------------------------
# Rapor uretimi
# ---------------------------------------------------------------------------


@dataclass
class ApplyOutcome:
    applied: bool
    dry_run: bool
    reasons: list[str] = field(default_factory=list)
    patched_kinds: list[str] = field(default_factory=list)
    old_checksum: str | None = None
    new_checksum: str | None = None
    backup_path: str | None = None
    # v1.1 alanlari (hepsi GERIYE UYUMLU varsayilanlarla -- eski cagiranlar/
    # testler DEGISIKLIKSIZ calismaya devam eder):
    is_emergency: bool = False
    emergency_fields: dict[str, Any] | None = None
    non_patchable: dict[str, str] = field(default_factory=dict)
    proposal_path: str | None = None
    review_record_path: str | None = None
    verification: list[dict[str, Any]] | None = None
    verification_state: str | None = None


def render_apply_report_md(outcome: ApplyOutcome, proposal: dict[str, Any], *, generated_at: str) -> str:
    lines = [
        "# Esik Degisikligi Uygulama (Apply) Raporu",
        "",
        f"Uretildi (UTC): {generated_at}",
        f"Proposal ID: {proposal.get('proposal_id', '?')}",
        f"Alert: {proposal.get('alert_name', '?')}",
        f"Mod: {'DRY-RUN (hicbir dosya degistirilmedi)' if outcome.dry_run else 'APPLY (degisiklikler uygulandi)'}",
        f"Sonuc: {'BASARILI' if outcome.applied else 'UYGULANMADI/ENGELLENDI'}",
        "",
    ]
    if outcome.is_emergency:
        lines.append("## ACIL DURUM UYGULAMASI (APPROVE_EMERGENCY)")
        lines.append("")
        lines.append("**Bu degisiklik, normal iki-goz incelemesi BEKLENMEDEN acil durum yoluyla uygulandi.**")
        ef = outcome.emergency_fields or {}
        lines.append(f"- Olay (incident_id): `{ef.get('incident_id')}`")
        lines.append(f"- Gerekce (justification): {ef.get('justification')}")
        lines.append(f"- Zaman kutusu (timebox_hours): {ef.get('timebox_hours')}")
        lines.append(f"- Retroaktif inceleme vadesi (retro_review_due_utc): `{ef.get('retro_review_due_utc')}`")
        lines.append("- **ZORUNLU:** bu vadeye kadar ikinci bir incelemenin GERCEKLESMESI gerekir, aksi halde drift detector bunu CRITICAL olarak isaretler.")
        lines.append("")
    if outcome.reasons:
        lines.append("## Nedenler / Engeller")
        lines.append("")
        for r in outcome.reasons:
            lines.append(f"- {r}")
        lines.append("")
    if outcome.applied:
        lines += [
            "## Uygulanan degisiklikler",
            "",
            f"- Yamalanan esik turleri: {', '.join(outcome.patched_kinds) if outcome.patched_kinds else 'yok'}",
            f"- Eski checksum: `{outcome.old_checksum}`",
            f"- Yeni checksum: `{outcome.new_checksum}`",
            f"- Yedek: `{outcome.backup_path}`",
            "",
        ]
    if outcome.non_patchable:
        lines.append("## Yamalanamayan (non-patchable) alertler -- sistem geneli kayit")
        lines.append("")
        for name, reason in outcome.non_patchable.items():
            lines.append(f"- `{name}`: {reason}")
        lines.append("")
    if outcome.verification is not None:
        lines.append(f"## VerifyReload dogrulamasi -- genel durum: **{outcome.verification_state}**")
        lines.append("")
        lines.append("| Kontrol | Durum | Detay |")
        lines.append("|---|---|---|")
        for v in outcome.verification:
            lines.append(f"| {v.get('check')} | {v.get('state')} | {v.get('reason')} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def write_apply_report(
    outcome: ApplyOutcome, proposal: dict[str, Any], out_dir: Path, *, generated_at: str
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = render_apply_report_md(outcome, proposal, generated_at=generated_at)
    md_path = out_dir / "apply_report.md"
    md_path.write_text(md, encoding="utf-8")

    json_path = out_dir / "apply_report.json"
    payload = {
        "generated_at": generated_at,
        "proposal_id": proposal.get("proposal_id"),
        "alert_name": proposal.get("alert_name"),
        "dry_run": outcome.dry_run,
        "applied": outcome.applied,
        "reasons": outcome.reasons,
        "patched_kinds": outcome.patched_kinds,
        "old_checksum": outcome.old_checksum,
        "new_checksum": outcome.new_checksum,
        "backup_path": outcome.backup_path,
        "is_emergency": outcome.is_emergency,
        "emergency_fields": outcome.emergency_fields,
        "non_patchable": outcome.non_patchable,
        "proposal_path": outcome.proposal_path,
        "review_record_path": outcome.review_record_path,
        "verification": outcome.verification,
        "verification_state": outcome.verification_state,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    return {"md": md_path, "json": json_path}


@dataclass
class RollbackOutcome:
    restored: bool
    reasons: list[str] = field(default_factory=list)
    restored_checksum: str | None = None
    # v1.1 alanlari (GERIYE UYUMLU varsayilanlar): kaynak apply/review
    # baglantisi ("apply_id"/"review_id" -- bu projede standalone kimlikler
    # yerine dosya yollari kullanilir) + rollback-SONRASI drift anlik
    # goruntusu (bkz. gorev madde 5).
    source_apply_report_path: str | None = None
    source_review_record_path: str | None = None
    post_rollback_drift_status: str | None = None


def render_rollback_report_md(outcome: RollbackOutcome, *, proposal_id: str, generated_at: str) -> str:
    lines = [
        "# Esik Degisikligi Geri Alma (Rollback) Raporu",
        "",
        f"Uretildi (UTC): {generated_at}",
        f"Proposal ID: {proposal_id}",
        f"Sonuc: {'BASARILI -- onceki config geri yuklendi' if outcome.restored else 'BASARISIZ'}",
        "",
    ]
    if outcome.source_apply_report_path or outcome.source_review_record_path:
        lines.append("## Kaynak baglanti (linkage)")
        lines.append("")
        lines.append(f"- Kaynak apply raporu: `{outcome.source_apply_report_path}`")
        lines.append(f"- Kaynak review kaydi: `{outcome.source_review_record_path}`")
        lines.append("")
    if outcome.reasons:
        lines.append("## Nedenler")
        lines.append("")
        for r in outcome.reasons:
            lines.append(f"- {r}")
        lines.append("")
    if outcome.restored:
        lines.append(f"- Geri yuklenen dosyanin checksum'i: `{outcome.restored_checksum}`")
        lines.append(f"- Rollback-sonrasi drift durumu: **{outcome.post_rollback_drift_status}**")
        lines.append("")
    return "\n".join(lines) + "\n"


def write_rollback_report(
    outcome: RollbackOutcome, out_dir: Path, *, proposal_id: str, generated_at: str
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = render_rollback_report_md(outcome, proposal_id=proposal_id, generated_at=generated_at)
    md_path = out_dir / "rollback_report.md"
    md_path.write_text(md, encoding="utf-8")

    json_path = out_dir / "rollback_report.json"
    payload = {
        "generated_at": generated_at,
        "proposal_id": proposal_id,
        "restored": outcome.restored,
        "reasons": outcome.reasons,
        "restored_checksum": outcome.restored_checksum,
        "source_apply_report_path": outcome.source_apply_report_path,
        "source_review_record_path": outcome.source_review_record_path,
        "post_rollback_drift_status": outcome.post_rollback_drift_status,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    return {"md": md_path, "json": json_path}
