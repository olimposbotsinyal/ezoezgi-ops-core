<#
Onaylanmis bir esik degisikligi ONERISINI (proposal) GERCEKTEN uygular
-- `infra/monitoring/prometheus/model_gateway_alerts.yaml`'deki ilgili
sayisal esik(ler)i degistirir.

**VARSAYILAN MOD: DRY-RUN.** Hicbir dosya degistirilmez -- yalnizca
uygunluk (eligibility) kontrol edilir + ne DEGISECEGI raporlanir.
Gercekten uygulamak icin `-Apply` acikca gecilmelidir.

**Uygunluk kurallari (TUMU saglanmali, `threshold_governance_core.py::check_apply_eligibility`):**
  1. Proposal, `threshold_proposal_schema_v1.json` semasina uygun olmali.
  2. review_record'un `decision`'i "APPROVE" VEYA "APPROVE_EMERGENCY"
     olmali (REJECT/NEEDS_DATA/baska HERHANGI bir deger REDDEDILIR).
  3. review_record'un `proposal_id`'si proposal ile eslesmeli.
  4. review_record'daki `linked_proposal_checksum`, proposal'in GERCEKTEN
     yeniden hesaplanmis checksum'iyla BIREBIR eslesmeli (proposal
     review'dan SONRA degismisse -- kasitli veya yanlislikla -- apply
     REDDEDILIR).
  5. `decision == APPROVE_EMERGENCY` ise EK OLARAK: `incident_id`,
     `justification`, `timebox_hours` (0, 24] araliginda, `retro_review_due_utc`
     (gecerli, `approved_at_utc`'den SONRA bir ISO8601 UTC tarih) ZORUNLUDUR
     (bkz. `threshold_governance_core.py::validate_emergency_fields`).

Basarili bir apply:
  - Hedef dosyanin (model_gateway_alerts.yaml) bir YEDEGINI olusturur
    (reports/threshold_apply_<UTC>/backups/).
  - Yalnizca ILGILI sayisal esik degerini metin-tabanli, HEDEFLI bir
    regex ile degistirir (rollback_observability.ps1 ile AYNI ilke --
    tam bir YAML parse+re-dump YAPILMAZ, dosyadaki aciklamalar KORUNUR).
    Guvenlik icin: her desen TAM OLARAK 1 kez eslesmelidir -- 0 veya
    >1 eslesme (multi_target=True olarak isaretlenmedikce) FAIL-LOUD
    ENGELLENIR (bkz. threshold_apply_core.py::patch_alert_expr_value).
  - `infra/monitoring/baseline/approved_checksums_ledger.jsonl`'e bir
    girdi EKLER (`alert_name`/`is_emergency`/`retro_review_due_utc` dahil)
    -- `detect_observability_drift.py` bunu, bu degisikligi "onayli/
    beklenen" olarak taniyip CRITICAL drift olarak ISARETLEMEMEK icin
    kullanir; acil durum girdileri EK OLARAK `check_emergency_review_overdue.py`
    tarafindan vade takibi icin okunur.
  - `data/audit/audit.log.jsonl`'e (GERCEK, mevcut `AuditLogger` ile --
    apps/orchestrator/src/audit_logger.py) bir denetim kaydi EKLER --
    acil durum uygulamalari `is_emergency=true` + tam acil durum
    alanlariyla ACIKCA etiketlenir.
  - `reports/threshold_apply_<UTC>/apply_report.md`+`.json` yazar --
    ACIL DURUM bolumu, "non-patchable" alert kaydi VE (istenirse)
    VerifyReload dogrulama sonuclarini icerir.

**-VerifyReload (opsiyonel, yalnizca GERCEK -Apply SONRASI calisir):**
Uc dogrulama adimi calistirir, HICBIRINI FABRIKE ETMEZ:
  1. `promtool check rules` (patch edilmis dosya uzerinde) -- `-PromtoolPath`
     saglanmadiysa/binary bulunamazsa VERIFICATION_SKIPPED.
  2. `amtool check-config` (Alertmanager config) -- `-AmtoolPath`/
     `-AlertmanagerConfigPath` saglanmadiysa/bulunamazsa VERIFICATION_SKIPPED.
  3. Prometheus/Alertmanager runtime endpoint erisilebilirligi --
     `-PrometheusHealthUrl`/`-AlertmanagerReadyUrl` saglanmadiysa
     VERIFICATION_SKIPPED; saglandiysa ama erisilemezse FAIL (asla PASS
     UYDURULMAZ).
Genel durum: herhangi bir kontrol FAIL ise FAIL; tumu SKIPPED ise
VERIFICATION_SKIPPED; aksi halde PASS.

Cikis kodu: 0 (uygun -- dry-run onizlemesi basarili VEYA gercek apply
basarili, VerifyReload FAIL degil), 2 (uygunsuz/engellendi -- fabrike
edilmis bir basari ASLA donmez), 3 (apply BASARILI ama VerifyReload
FAIL -- dosya UYGULANDI, geri almak icin rollback_threshold_apply.ps1
kullanin).
#>

param(
    [Parameter(Mandatory = $true)][string]$ProposalPath,
    [Parameter(Mandatory = $true)][string]$ReviewRecordPath,
    [string]$PythonExe = "d:\Projects\ezoezgi-ops\.venv\Scripts\python.exe",
    [string]$RepoRoot = "d:\Projects\ezoezgi-ops",
    [switch]$Apply,
    [switch]$VerifyReload,
    [string]$PromtoolPath = "",
    [string]$AmtoolPath = "",
    [string]$AlertmanagerConfigPath = "",
    [string]$PrometheusHealthUrl = "",
    [string]$AlertmanagerReadyUrl = ""
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "$RepoRoot\services\model-gateway\src;$RepoRoot\apps\orchestrator\src;$RepoRoot\scripts\ops"

$ts = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$outDir = Join-Path $RepoRoot "reports\threshold_apply_$ts"

$mode = if ($Apply) { "apply" } else { "dry_run" }
Write-Output "=== Esik degisikligi uygulama -- mod: $mode ==="

$effectiveAmConfigPath = if ($AlertmanagerConfigPath) { $AlertmanagerConfigPath } else { "$RepoRoot\infra\monitoring\profiles\persistent\alertmanager.yml" }

$driverScript = @"
import sys, json, subprocess, urllib.request, urllib.error
sys.path.insert(0, r'$RepoRoot\scripts\ops')
sys.path.insert(0, r'$RepoRoot\apps\orchestrator\src')
from datetime import datetime, timezone
from pathlib import Path

from threshold_governance_core import check_apply_eligibility
from threshold_apply_core import (
    apply_proposal_to_alerts_text, create_backup, append_ledger_entry, build_ledger_entry,
    build_apply_audit_details, get_non_patchable_alerts, ApplyOutcome, write_apply_report,
    build_pass_check, build_fail_check, build_skipped_check, aggregate_verification_state,
)
from detect_observability_drift import compute_file_sha256
from audit_logger import AuditLogger

repo_root = Path(r'$RepoRoot')
proposal_path = Path(r'$ProposalPath')
review_path = Path(r'$ReviewRecordPath')
schema_path = repo_root / 'infra' / 'monitoring' / 'governance' / 'threshold_proposal_schema_v1.json'
target_path = repo_root / 'infra' / 'monitoring' / 'prometheus' / 'model_gateway_alerts.yaml'
ledger_path = repo_root / 'infra' / 'monitoring' / 'baseline' / 'approved_checksums_ledger.jsonl'
out_dir = Path(r'$outDir')
apply_mode = $(if ($Apply) { "True" } else { "False" })
verify_reload = $(if ($VerifyReload) { "True" } else { "False" })
promtool_path = r'$PromtoolPath'
amtool_path = r'$AmtoolPath'
am_config_path = r'$effectiveAmConfigPath'
prom_health_url = r'$PrometheusHealthUrl'
am_ready_url = r'$AlertmanagerReadyUrl'

generated_at = datetime.now(timezone.utc).isoformat()

try:
    schema = json.loads(schema_path.read_text(encoding='utf-8'))
    proposal = json.loads(proposal_path.read_text(encoding='utf-8'))
    review_record = json.loads(review_path.read_text(encoding='utf-8'))
except (OSError, json.JSONDecodeError) as exc:
    outcome = ApplyOutcome(applied=False, dry_run=not apply_mode, reasons=[f'proposal/review/sema dosyasi okunamadi: {exc}'])
    proposal_stub = {'proposal_id': 'UNKNOWN', 'alert_name': 'UNKNOWN'}
    write_apply_report(outcome, proposal_stub, out_dir, generated_at=generated_at)
    print(f'BLOCKED: {outcome.reasons}')
    sys.exit(2)

eligibility = check_apply_eligibility(proposal, review_record, schema)

is_emergency = review_record.get('decision') == 'APPROVE_EMERGENCY'
emergency_fields = None
if is_emergency:
    emergency_fields = {
        'incident_id': review_record.get('incident_id'),
        'justification': review_record.get('justification'),
        'timebox_hours': review_record.get('timebox_hours'),
        'retro_review_due_utc': review_record.get('retro_review_due_utc'),
    }

if not eligibility.eligible:
    outcome = ApplyOutcome(
        applied=False, dry_run=not apply_mode, reasons=eligibility.reasons,
        is_emergency=is_emergency, emergency_fields=emergency_fields,
        proposal_path=str(proposal_path), review_record_path=str(review_path),
    )
    write_apply_report(outcome, proposal, out_dir, generated_at=generated_at)
    print('BLOCKED -- apply UYGULANMAYACAK:')
    for r in eligibility.reasons:
        print(f'  - {r}')
    print(f'evidence_dir={out_dir}')
    sys.exit(2)

decision_label = review_record.get('decision')
print(f'UYGUNLUK: TUM kontroller GECTI (sema gecerli, decision={decision_label}, checksum eslesiyor).')
if is_emergency:
    print(f'!! ACIL DURUM (APPROVE_EMERGENCY) uygulamasi -- incident_id={emergency_fields["incident_id"]}, retro_review_due_utc={emergency_fields["retro_review_due_utc"]}')

if not apply_mode:
    outcome = ApplyOutcome(
        applied=False, dry_run=True,
        reasons=['DRY-RUN modu: hicbir dosya degistirilmedi. Gercekten uygulamak icin -Apply gecin.'],
        is_emergency=is_emergency, emergency_fields=emergency_fields,
        non_patchable=get_non_patchable_alerts(),
        proposal_path=str(proposal_path), review_record_path=str(review_path),
    )
    write_apply_report(outcome, proposal, out_dir, generated_at=generated_at)
    print(f'DRY-RUN: uygulanirsa degisecek anahtarlar: {list(proposal["proposed_values"].keys())}')
    if verify_reload:
        print('NOT: -VerifyReload dry-run modunda calismaz (uygulanan bir degisiklik yok).')
    print(f'evidence_dir={out_dir}')
    sys.exit(0)

# --- GERCEK UYGULAMA ---
# ONEMLI: dosya checksum'i icin `compute_file_sha256` (HAM BYTE'larin
# SHA256'si) kullanilir -- `threshold_governance_core.compute_checksum`
# (PROPOSAL SOZLUKLERI icin, kanonik JSON uzerinden) DEGIL. Bu ikisi
# FARKLI amaclar icindir; alerts dosyasinin checksum'i, `detect_observability_drift.py`'nin
# GOZLEMLEYECEGI ile BIREBIR ayni yontemle hesaplanmalidir, aksi halde
# `approved_checksums_ledger.jsonl`'deki degerler drift tespitiyle HICBIR
# ZAMAN eslesmez.
old_checksum = compute_file_sha256(target_path)
old_text = target_path.read_text(encoding='utf-8')

backup_dir = out_dir / 'backups'
backup_path = create_backup(target_path, backup_dir, label=proposal['proposal_id'])

new_text, patched_kinds, failed_targets = apply_proposal_to_alerts_text(old_text, proposal)

if failed_targets:
    reasons = [f"{f['kind']}: {f['reason']}" for f in failed_targets]
    outcome = ApplyOutcome(
        applied=False, dry_run=False,
        reasons=['Su esik turleri yamalanmadi (hicbir sey degistirilmedi, TUM-YA-DA-HICBIRI):'] + reasons,
        backup_path=str(backup_path), is_emergency=is_emergency, emergency_fields=emergency_fields,
        non_patchable=get_non_patchable_alerts(),
        proposal_path=str(proposal_path), review_record_path=str(review_path),
    )
    write_apply_report(outcome, proposal, out_dir, generated_at=generated_at)
    print(f'BLOCKED (kismi basarisizlik -- HICBIR SEY yazilmadi): {outcome.reasons}')
    sys.exit(2)

target_path.write_text(new_text, encoding='utf-8')
new_checksum = compute_file_sha256(target_path)

ledger_entry = build_ledger_entry(
    proposal_id=proposal['proposal_id'], old_checksum=old_checksum, new_checksum=new_checksum,
    apply_report_path=str(out_dir / 'apply_report.json'), timestamp=generated_at,
    alert_name=proposal.get('alert_name', ''), is_emergency=is_emergency,
    retro_review_due_utc=(emergency_fields or {}).get('retro_review_due_utc'),
)
append_ledger_entry(ledger_path, ledger_entry)

audit_details = build_apply_audit_details(
    proposal=proposal, review_record=review_record, backup_path=backup_path,
    patched_kinds=patched_kinds, old_checksum=old_checksum, new_checksum=new_checksum,
)
audit_logger = AuditLogger(log_path=repo_root / 'data' / 'audit' / 'audit.log.jsonl')
audit_record = audit_logger.log(
    alias=None, task='THRESHOLD_CHANGE_APPLY', status='APPLIED',
    risk_level='high' if is_emergency else 'medium', details=audit_details,
)

# --- VerifyReload (opsiyonel, yalnizca GERCEK apply BASARILI olduysa) ---
verification = None
verification_state = None
if verify_reload:
    def _promtool_check():
        if not promtool_path:
            return build_skipped_check('promtool_check_rules', 'PromtoolPath saglanmadi')
        p = Path(promtool_path)
        if not p.exists():
            return build_skipped_check('promtool_check_rules', f'promtool bulunamadi: {promtool_path}')
        try:
            result = subprocess.run([str(p), 'check', 'rules', str(target_path)], capture_output=True, text=True, timeout=30)
            output = (result.stdout + result.stderr).strip()
            if result.returncode == 0:
                return build_pass_check('promtool_check_rules', output or 'SUCCESS')
            return build_fail_check('promtool_check_rules', output or f'exit_code={result.returncode}')
        except Exception as exc:
            return build_fail_check('promtool_check_rules', f'calistirilamadi: {exc}')

    def _amtool_check():
        if not amtool_path:
            return build_skipped_check('amtool_check_config', 'AmtoolPath saglanmadi')
        p = Path(amtool_path)
        if not p.exists():
            return build_skipped_check('amtool_check_config', f'amtool bulunamadi: {amtool_path}')
        cfg = Path(am_config_path)
        if not cfg.exists():
            return build_skipped_check('amtool_check_config', f'alertmanager config bulunamadi: {am_config_path}')
        try:
            result = subprocess.run([str(p), 'check-config', str(cfg)], capture_output=True, text=True, timeout=30)
            output = (result.stdout + result.stderr).strip()
            if result.returncode == 0:
                return build_pass_check('amtool_check_config', output or 'SUCCESS')
            return build_fail_check('amtool_check_config', output or f'exit_code={result.returncode}')
        except Exception as exc:
            return build_fail_check('amtool_check_config', f'calistirilamadi: {exc}')

    def _http_readiness_check(check_name, url, timeout_sec=5):
        if not url:
            return build_skipped_check(check_name, 'URL saglanmadi (opsiyonel parametre)')
        try:
            with urllib.request.urlopen(url, timeout=timeout_sec) as resp:
                status = resp.getcode()
                if 200 <= status < 300:
                    return build_pass_check(check_name, f'HTTP {status}')
                return build_fail_check(check_name, f'HTTP {status}')
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            return build_fail_check(check_name, f'erisilemedi: {exc}')

    verification = [
        _promtool_check(),
        _amtool_check(),
        _http_readiness_check('prometheus_health', prom_health_url),
        _http_readiness_check('alertmanager_ready', am_ready_url),
    ]
    verification_state = aggregate_verification_state(verification)

outcome = ApplyOutcome(
    applied=True, dry_run=False, reasons=[], patched_kinds=patched_kinds,
    old_checksum=old_checksum, new_checksum=new_checksum, backup_path=str(backup_path),
    is_emergency=is_emergency, emergency_fields=emergency_fields,
    non_patchable=get_non_patchable_alerts(),
    proposal_path=str(proposal_path), review_record_path=str(review_path),
    verification=verification, verification_state=verification_state,
)
write_apply_report(outcome, proposal, out_dir, generated_at=generated_at)

print(f'BASARILI: {patched_kinds} yamalandi.')
print(f'old_checksum={old_checksum}')
print(f'new_checksum={new_checksum}')
print(f'backup={backup_path}')
print(f'audit_request_id={audit_record["request_id"]}')
print(f'ledger_entry_eklendi={ledger_path}')
if verify_reload:
    print(f'verify_reload_state={verification_state}')
    for v in verification:
        print(f"  - {v['check']}: {v['state']} ({v['reason']})")
print(f'evidence_dir={out_dir}')

if verification_state == 'FAIL':
    sys.exit(3)
sys.exit(0)
"@

$driverPath = Join-Path $env:TEMP "apply_threshold_proposal_driver_$ts.py"
Set-Content -Path $driverPath -Value $driverScript -Encoding utf8
& $PythonExe $driverPath
$exitCode = $LASTEXITCODE
Remove-Item -Path $driverPath -Force -ErrorAction SilentlyContinue

if (-not $Apply -and $exitCode -eq 0) {
    Write-Output ""
    Write-Output "DRY-RUN tamamlandi -- hicbir dosya degistirilmedi. Gercekten uygulamak icin: -Apply"
}
if ($exitCode -eq 3) {
    Write-Output ""
    Write-Output "UYARI: Degisiklik UYGULANDI ama -VerifyReload dogrulamasi FAIL verdi -- reports\threshold_apply_$ts\apply_report.md inceleyin, gerekirse rollback_threshold_apply.ps1 kullanin."
}

exit $exitCode
