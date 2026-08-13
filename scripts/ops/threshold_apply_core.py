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

# Her (alert_name, esik_turu) icin, dosyadaki BENZERSIZ pencere etiketi
# sayesinde baska hicbir yeri yanlislikla eslestirmeyen bir desen.
# Her desen TAM OLARAK 3 yakalama grubu icerir: (on-ek, SAYISAL_DEGER, son-ek)
# -- FALLBACK_SPIKE'ta son-ek doludur (" * sum"), digerlerinde bostur --
# boylece `patch_alert_expr_value` HER ZAMAN ayni sekilde (m.group(1) + yeni_deger + m.group(3))
# yeniden birlestirebilir.
ALERT_EXPR_PATCH_PATTERNS: dict[str, dict[str, re.Pattern[str]]] = {
    "HIGH_NULL_INTENT_RATE": {
        "warn": re.compile(
            r"(model_gateway_null_intent_total\[6h\]\)\) / sum\(rate\(model_gateway_requests_total\[6h\]\)\)\) > )"
            r"([0-9]+(?:\.[0-9]+)?)()"
        ),
        "crit": re.compile(
            r"(model_gateway_null_intent_total\[1h\]\)\) / sum\(rate\(model_gateway_requests_total\[1h\]\)\)\) > )"
            r"([0-9]+(?:\.[0-9]+)?)()"
        ),
    },
    "FALLBACK_SPIKE": {
        "warn": re.compile(r"(sum\(rate\(model_gateway_fallback_total\[15m\]\)\) > \()([0-9]+(?:\.[0-9]+)?)( \* sum)"),
        "crit": re.compile(r"(sum\(rate\(model_gateway_fallback_total\[15m\]\)\) > \()([0-9]+(?:\.[0-9]+)?)( \* sum)"),
    },
}


def _format_threshold_value(value: float) -> str:
    """Gereksiz sondaki sifirlari temizler (0.100000 -> 0.1, 3.000000 -> 3)."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def patch_alert_expr_value(text: str, alert_name: str, threshold_kind: str, new_value: float) -> tuple[str, bool]:
    """`text` (model_gateway_alerts.yaml icerigi) icinde, `alert_name`/
    `threshold_kind` ('warn'/'crit') icin BILINEN deseni bulup sayisal
    degeri `new_value` ile degistirir. Desen TANIMLI DEGILSE veya
    metinde BULUNAMAZSA metin DEGISTIRILMEDEN doner (`changed=False`) --
    SESSIZCE yanlis bir seyi asla degistirmez."""
    pattern = ALERT_EXPR_PATCH_PATTERNS.get(alert_name, {}).get(threshold_kind)
    if pattern is None:
        return text, False

    formatted = _format_threshold_value(new_value)

    def _replace(m: re.Match[str]) -> str:
        return f"{m.group(1)}{formatted}{m.group(3)}"

    new_text, count = pattern.subn(_replace, text, count=1)
    return new_text, count > 0


def apply_proposal_to_alerts_text(text: str, proposal: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    """`proposal['proposed_values']`'deki HER anahtar (warn/crit) icin
    metni sirayla yamalar. Doner: (yeni_metin, basarili_anahtarlar,
    basarisiz_anahtarlar). Bir anahtar icin desen bulunamazsa/eslesmezse
    o anahtar `basarisiz_anahtarlar`'a eklenir -- cagiran taraf (apply
    script'i) bunu GERCEK bir hata olarak ele almalidir (kismi/sessiz
    basarisizlik asla olmamali)."""
    alert_name = proposal["alert_name"]
    succeeded: list[str] = []
    failed: list[str] = []
    for kind, new_value in proposal["proposed_values"].items():
        text, changed = patch_alert_expr_value(text, alert_name, kind, new_value)
        (succeeded if changed else failed).append(kind)
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
) -> dict[str, Any]:
    return {
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "proposal_id": proposal_id,
        "old_checksum": old_checksum,
        "new_checksum": new_checksum,
        "apply_report_path": apply_report_path,
    }


def append_ledger_entry(ledger_path: Path, entry: dict[str, Any]) -> None:
    """`approved_checksums_ledger.jsonl`'e append-only bir satir ekler
    -- var olan satirlar ASLA degistirilmez/silinmez (audit.log.jsonl
    ile AYNI ilke)."""
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_approved_checksums(ledger_path: Path) -> list[str]:
    """Defterdeki TUM `new_checksum` degerlerini doner -- dosya yoksa
    (henuz hic apply yapilmamis, NORMAL durum) bos liste doner. Bozuk
    tek satirlar SESSIZCE atlanir (tum defteri gecersiz kilmaz)."""
    if not ledger_path.exists():
        return []
    checksums: list[str] = []
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            checksums.append(str(entry["new_checksum"]))
        except (json.JSONDecodeError, KeyError, TypeError):
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
    return {
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
    }


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
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    return {"md": md_path, "json": json_path}


@dataclass
class RollbackOutcome:
    restored: bool
    reasons: list[str] = field(default_factory=list)
    restored_checksum: str | None = None


def render_rollback_report_md(outcome: RollbackOutcome, *, proposal_id: str, generated_at: str) -> str:
    lines = [
        "# Esik Degisikligi Geri Alma (Rollback) Raporu",
        "",
        f"Uretildi (UTC): {generated_at}",
        f"Proposal ID: {proposal_id}",
        f"Sonuc: {'BASARILI -- onceki config geri yuklendi' if outcome.restored else 'BASARISIZ'}",
        "",
    ]
    if outcome.reasons:
        lines.append("## Nedenler")
        lines.append("")
        for r in outcome.reasons:
            lines.append(f"- {r}")
        lines.append("")
    if outcome.restored:
        lines.append(f"- Geri yuklenen dosyanin checksum'i: `{outcome.restored_checksum}`")
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
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    return {"md": md_path, "json": json_path}
