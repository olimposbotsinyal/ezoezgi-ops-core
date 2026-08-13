<#
Daha once `apply_threshold_proposal.ps1 -Apply` ile UYGULANMIS bir esik
degisikligini GERI ALIR -- `apply_report.json`'daki yedek (backup)
dosyasindan `infra/monitoring/prometheus/model_gateway_alerts.yaml`'i
ESKI HALINE dondurur.

**VARSAYILAN MOD: DRY-RUN.** Hicbir dosya degistirilmez -- yalnizca ne
geri yuklenecegi raporlanir. Gercekten geri almak icin `-Apply` acikca
gecilmelidir.

Girdi: bir `apply_report.json` yolu (`-ApplyReportPath`) -- ilgili
`backup_path`, `proposal_id`, `old_checksum` bilgilerini buradan okur.

Basarili bir rollback:
  - Hedef dosyayi (`model_gateway_alerts.yaml`) TAM OLARAK yedekteki
    haline (byte-byte) dondurur.
  - Geri yuklenen dosyanin checksum'inin, orijinal `apply_report.json`'daki
    `old_checksum` ile eslestigini DOGRULAR (eslesmezse acikca uyarir --
    yedek dosyasi kendisi bozulmus/degismis olabilir).
  - `data/audit/audit.log.jsonl`'e bir ROLLBACK denetim kaydi EKLER.
  - `reports/threshold_rollback_<UTC>/rollback_report.md`+`.json` yazar.

NOT: Bu script `approved_checksums_ledger.jsonl`'e YENI bir girdi
EKLEMEZ -- rollback, dosyayi ONCEKI (zaten onayli/bilinen) durumuna
dondurur, bu durum halihazirda ya baseline'da ya da ledger'da zaten
mevcuttur.

Cikis kodu: 0 (basarili -- dry-run onizlemesi VEYA gercek rollback),
2 (basarisiz/engellendi).
#>

param(
    [Parameter(Mandatory = $true)][string]$ApplyReportPath,
    [string]$PythonExe = "d:\Projects\ezoezgi-ops\.venv\Scripts\python.exe",
    [string]$RepoRoot = "d:\Projects\ezoezgi-ops",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "$RepoRoot\services\model-gateway\src;$RepoRoot\apps\orchestrator\src;$RepoRoot\scripts\ops"

$ts = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$outDir = Join-Path $RepoRoot "reports\threshold_rollback_$ts"

$mode = if ($Apply) { "apply" } else { "dry_run" }
Write-Output "=== Esik degisikligi geri alma (rollback) -- mod: $mode ==="

$driverScript = @"
import sys, json
sys.path.insert(0, r'$RepoRoot\scripts\ops')
sys.path.insert(0, r'$RepoRoot\apps\orchestrator\src')
from datetime import datetime, timezone
from pathlib import Path

from threshold_apply_core import restore_backup, build_rollback_audit_details, RollbackOutcome, write_rollback_report
from detect_observability_drift import compute_file_sha256
from audit_logger import AuditLogger

repo_root = Path(r'$RepoRoot')
apply_report_path = Path(r'$ApplyReportPath')
target_path = repo_root / 'infra' / 'monitoring' / 'prometheus' / 'model_gateway_alerts.yaml'
out_dir = Path(r'$outDir')
apply_mode = $(if ($Apply) { "True" } else { "False" })

generated_at = datetime.now(timezone.utc).isoformat()

try:
    apply_report = json.loads(apply_report_path.read_text(encoding='utf-8'))
except (OSError, json.JSONDecodeError) as exc:
    outcome = RollbackOutcome(restored=False, reasons=[f'apply_report.json okunamadi: {exc}'])
    write_rollback_report(outcome, out_dir, proposal_id='UNKNOWN', generated_at=generated_at)
    print(f'BLOCKED: {outcome.reasons}')
    sys.exit(2)

proposal_id = apply_report.get('proposal_id', 'UNKNOWN')
backup_path_str = apply_report.get('backup_path')
old_checksum_expected = apply_report.get('old_checksum')

if not apply_report.get('applied'):
    outcome = RollbackOutcome(restored=False, reasons=['Bu apply_report.json BASARILI bir apply temsil etmiyor (applied=false) -- geri alinacak bir sey yok.'])
    write_rollback_report(outcome, out_dir, proposal_id=proposal_id, generated_at=generated_at)
    print(f'BLOCKED: {outcome.reasons}')
    sys.exit(2)

if not backup_path_str:
    outcome = RollbackOutcome(restored=False, reasons=['apply_report.json icinde backup_path yok.'])
    write_rollback_report(outcome, out_dir, proposal_id=proposal_id, generated_at=generated_at)
    print(f'BLOCKED: {outcome.reasons}')
    sys.exit(2)

backup_path = Path(backup_path_str)
if not backup_path.exists():
    outcome = RollbackOutcome(restored=False, reasons=[f'Yedek dosyasi bulunamadi: {backup_path}'])
    write_rollback_report(outcome, out_dir, proposal_id=proposal_id, generated_at=generated_at)
    print(f'BLOCKED: {outcome.reasons}')
    sys.exit(2)

current_checksum = compute_file_sha256(target_path)
print(f'Hedef dosyanin SU ANKI checksum\'i: {current_checksum}')
print(f'Yedekten geri yuklenecek (beklenen eski checksum): {old_checksum_expected}')

if not apply_mode:
    outcome = RollbackOutcome(
        restored=False,
        reasons=['DRY-RUN modu: hicbir dosya degistirilmedi. Gercekten geri almak icin -Apply gecin.'],
    )
    write_rollback_report(outcome, out_dir, proposal_id=proposal_id, generated_at=generated_at)
    print(f'DRY-RUN: {backup_path} -> {target_path} geri yuklenecekti.')
    print(f'evidence_dir={out_dir}')
    sys.exit(0)

restore_backup(backup_path, target_path)
restored_checksum = compute_file_sha256(target_path)

checksum_matches = (restored_checksum == old_checksum_expected)
reason = 'checksum beklenenle eslesiyor' if checksum_matches else 'UYARI: checksum beklenenle ESLESMIYOR (yedek dosyasi degismis olabilir)'

audit_details = build_rollback_audit_details(
    proposal_id=proposal_id, backup_path=backup_path, restored_checksum=restored_checksum, reason=reason,
)
audit_logger = AuditLogger(log_path=repo_root / 'data' / 'audit' / 'audit.log.jsonl')
audit_record = audit_logger.log(
    alias=None, task='THRESHOLD_CHANGE_ROLLBACK', status='ROLLED_BACK', risk_level='medium', details=audit_details,
)

outcome = RollbackOutcome(restored=True, reasons=[reason], restored_checksum=restored_checksum)
write_rollback_report(outcome, out_dir, proposal_id=proposal_id, generated_at=generated_at)

print(f'BASARILI: {target_path} geri yuklendi.')
print(f'restored_checksum={restored_checksum}')
print(f'checksum_matches_expected={checksum_matches}')
print(f'audit_request_id={audit_record["request_id"]}')
print(f'evidence_dir={out_dir}')
sys.exit(0)
"@

$driverPath = Join-Path $env:TEMP "rollback_threshold_apply_driver_$ts.py"
Set-Content -Path $driverPath -Value $driverScript -Encoding utf8
& $PythonExe $driverPath
$exitCode = $LASTEXITCODE
Remove-Item -Path $driverPath -Force -ErrorAction SilentlyContinue

if (-not $Apply -and $exitCode -eq 0) {
    Write-Output ""
    Write-Output "DRY-RUN tamamlandi -- hicbir dosya degistirilmedi. Gercekten geri almak icin: -Apply"
}

exit $exitCode
