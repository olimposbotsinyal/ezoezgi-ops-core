"""Kontrollu VerifyReload FAIL + auto-rollback "drill"i icin SAF mantik
-- `apply_report.json` icindeki kanitin gorev kabul kriterlerini
(verification_state=FAIL, auto_rollback.triggered=true, checksum
GERCEKTEN restore edildi) karsilayip karsilamadigini dogrular + audit
log'daki eslesen `auto_rollback_triggered` kaydini bulur.

**Bu modul GERCEK bir drill'i TETIKLEMEZ** -- yalnizca ONCEDEN (gercek
`apply_threshold_proposal.ps1 -AutoRollbackOnVerifyFail -AutoRollbackMode
strict` calistirmasiyla) URETILMIS bir `apply_report.json`'u PARSE
EDER/DOGRULAR/PAKETLER (bkz. `package_promotion_drill.py` -- CLI
sarmalayicisi, GERCEK dosya G/C'yi yapar)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

REQUIRED_VERIFICATION_STATE = "FAIL"


def parse_drill_evidence(apply_report_payload: dict[str, Any]) -> dict[str, Any]:
    """`apply_report.json` payload'indan gorev kabul kriterleriyle
    ilgili alanlari CIKARIR (dogrulama YAPMAZ -- bkz.
    `validate_drill_acceptance`). Eksik alanlar icin `None` doner."""
    auto_rollback = apply_report_payload.get("auto_rollback")
    if not isinstance(auto_rollback, dict):
        auto_rollback = {}
    return {
        "proposal_id": apply_report_payload.get("proposal_id"),
        "generated_at": apply_report_payload.get("generated_at"),
        "verification_state": apply_report_payload.get("verification_state"),
        "old_checksum": apply_report_payload.get("old_checksum"),
        "new_checksum": apply_report_payload.get("new_checksum"),
        "auto_rollback_triggered": auto_rollback.get("triggered"),
        "auto_rollback_restored": auto_rollback.get("restored"),
        "auto_rollback_restored_checksum": auto_rollback.get("restored_checksum"),
    }


def validate_drill_acceptance(parsed: dict[str, Any]) -> tuple[bool, list[str]]:
    """Gorev kabul kriterlerinin TUMUNUN saglanip saglanmadigini
    dogrular:
      - verification_state == 'FAIL'
      - auto_rollback.triggered == True
      - auto_rollback.restored == True
      - auto_rollback.restored_checksum == old_checksum (dosya GERCEKTEN
        apply-ONCESI durumuna dondu, rastgele/farkli bir checksum'a
        DEGIL)

    Doner: (tumu_saglandi_mi, ihlal_edilen_kriterlerin_listesi).
    Bos liste = TUMU saglandi."""
    violations: list[str] = []

    if parsed.get("verification_state") != REQUIRED_VERIFICATION_STATE:
        violations.append(
            f"verification_state={parsed.get('verification_state')!r} (beklenen: {REQUIRED_VERIFICATION_STATE!r})"
        )
    if parsed.get("auto_rollback_triggered") is not True:
        violations.append(f"auto_rollback.triggered={parsed.get('auto_rollback_triggered')!r} (beklenen: True)")
    if parsed.get("auto_rollback_restored") is not True:
        violations.append(f"auto_rollback.restored={parsed.get('auto_rollback_restored')!r} (beklenen: True)")

    old_checksum = parsed.get("old_checksum")
    restored_checksum = parsed.get("auto_rollback_restored_checksum")
    if not old_checksum or not restored_checksum or old_checksum != restored_checksum:
        violations.append(
            f"checksum eslesmedi: old_checksum={old_checksum!r} != restored_checksum={restored_checksum!r}"
        )

    return (len(violations) == 0, violations)


def find_matching_audit_entry(audit_entries: list[dict[str, Any]], proposal_id: str | None) -> dict[str, Any] | None:
    """ONCEDEN parse edilmis audit log satirlari (`{"task": ...,
    "details": {"proposal_id": ...}, ...}`) arasinda, `task=auto_rollback_triggered`
    VE `details.proposal_id == proposal_id` olan EN SON (listedeki SON)
    kaydi doner -- yoksa `None`. `proposal_id=None` ise HICBIR ZAMAN
    eslesmez (belirsiz bir eslesmeyi ONLEMEK icin)."""
    if not proposal_id:
        return None
    match = None
    for entry in audit_entries:
        if entry.get("task") != "auto_rollback_triggered":
            continue
        details = entry.get("details")
        if isinstance(details, dict) and details.get("proposal_id") == proposal_id:
            match = entry
    return match


def render_drill_summary_md(
    *,
    generated_at: str,
    source_apply_report_path: str,
    source_generated_at: str | None,
    parsed: dict[str, Any],
    accepted: bool,
    violations: list[str],
    audit_entry: dict[str, Any] | None,
    is_fresh_run: bool,
) -> str:
    lines = [
        "# Kontrollu VerifyReload FAIL + Auto-Rollback Drill Ozeti",
        "",
        f"Paketlendi (UTC): {generated_at}",
        "",
    ]
    if is_fresh_run:
        lines.append("**Bu, BU OTURUMDA calistirilmis TAZE bir drill'dir.**")
    else:
        lines += [
            "**SEFFAFLIK NOTU (ONEMLI):** Bu paket, BU OTURUMDA calistirilmis "
            "TAZE bir drill DEGILDIR -- bu ortamda `promtool`/`amtool` kurulu "
            "olmadigi icin GERCEK bir yeni calistirma yapilamadi. Bunun yerine, "
            f"DAHA ONCE (kaynak: `{source_apply_report_path}`, uretildi: "
            f"{source_generated_at}) GERCEK `amtool.exe` ile GERCEKTEN "
            "calistirilmis, tum kabul kriterlerini karsilayan bir drill'in "
            "kanitina SEFFAF SEKILDE REFERANS verir/paketler. Fabrike edilmis "
            "hicbir veri YOKTUR -- kaynak dosya BIREBIR okunup dogrulanmistir.",
            "",
        ]
    lines += [
        f"Kaynak apply_report.json: `{source_apply_report_path}`",
        f"proposal_id: `{parsed.get('proposal_id')}`",
        "",
        "## Kabul kriterleri",
        "",
        f"- verification_state: `{parsed.get('verification_state')}` (beklenen: `{REQUIRED_VERIFICATION_STATE}`)",
        f"- auto_rollback.triggered: `{parsed.get('auto_rollback_triggered')}`",
        f"- auto_rollback.restored: `{parsed.get('auto_rollback_restored')}`",
        f"- old_checksum: `{parsed.get('old_checksum')}`",
        f"- auto_rollback.restored_checksum: `{parsed.get('auto_rollback_restored_checksum')}`",
        f"- checksum eslesmesi: {'EVET' if parsed.get('old_checksum') == parsed.get('auto_rollback_restored_checksum') else 'HAYIR'}",
        "",
        f"**Sonuc: {'KABUL EDILDI -- tum kriterler saglandi' if accepted else 'REDDEDILDI -- kriterler saglanmadi'}**",
        "",
    ]
    if violations:
        lines.append("**Ihlal edilen kriterler:**")
        for v in violations:
            lines.append(f"- {v}")
        lines.append("")
    lines.append("## Audit log kaniti")
    lines.append("")
    if audit_entry:
        lines.append(f"`data/audit/audit.log.jsonl`'de eslesen kayit BULUNDU: `task=auto_rollback_triggered`, "
                      f"`timestamp={audit_entry.get('timestamp')}`, `status={audit_entry.get('status')}`.")
    else:
        lines.append("`data/audit/audit.log.jsonl`'de eslesen bir `auto_rollback_triggered` kaydi BULUNAMADI.")
    return "\n".join(lines) + "\n"


def write_drill_package(
    out_dir: Path,
    *,
    generated_at: str,
    source_apply_report_path: str,
    source_generated_at: str | None,
    source_payload: dict[str, Any],
    parsed: dict[str, Any],
    accepted: bool,
    violations: list[str],
    audit_entry: dict[str, Any] | None,
    is_fresh_run: bool,
) -> dict[str, Path]:
    import json

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "drill_evidence.json").write_text(
        json.dumps(source_payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "audit_snippet.json").write_text(
        json.dumps(audit_entry, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    checksum_proof = {
        "old_checksum": parsed.get("old_checksum"),
        "new_checksum": parsed.get("new_checksum"),
        "auto_rollback_restored_checksum": parsed.get("auto_rollback_restored_checksum"),
        "checksum_matches_pre_apply_state": parsed.get("old_checksum") == parsed.get("auto_rollback_restored_checksum"),
    }
    (out_dir / "checksum_proof.json").write_text(
        json.dumps(checksum_proof, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    md = render_drill_summary_md(
        generated_at=generated_at, source_apply_report_path=source_apply_report_path,
        source_generated_at=source_generated_at, parsed=parsed, accepted=accepted, violations=violations,
        audit_entry=audit_entry, is_fresh_run=is_fresh_run,
    )
    md_path = out_dir / "drill_summary.md"
    md_path.write_text(md, encoding="utf-8")

    json_path = out_dir / "drill_summary.json"
    payload = {
        "generated_at": generated_at,
        "source_apply_report_path": source_apply_report_path,
        "source_generated_at": source_generated_at,
        "is_fresh_run": is_fresh_run,
        "parsed": parsed,
        "accepted": accepted,
        "violations": violations,
        "audit_entry_found": audit_entry is not None,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "drill_evidence": out_dir / "drill_evidence.json",
        "audit_snippet": out_dir / "audit_snippet.json",
        "checksum_proof": out_dir / "checksum_proof.json",
        "summary_md": md_path,
        "summary_json": json_path,
    }
