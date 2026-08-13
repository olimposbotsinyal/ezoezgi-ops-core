<#
Onaylanmis bir esik degisikligi ONERISINI (proposal) GERCEKTEN uygular
-- `infra/monitoring/prometheus/model_gateway_alerts.yaml`'deki ilgili
sayisal esik(ler)i degistirir.

**VARSAYILAN MOD: DRY-RUN.** Hicbir dosya degistirilmez -- yalnizca
uygunluk (eligibility) kontrol edilir + ne DEGISECEGI raporlanir.
Gercekten uygulamak icin `-Apply` acikca gecilmelidir.

**Uygunluk kurallari (TUMU saglanmali, `threshold_governance_core.py::check_apply_eligibility`):**
  1. Proposal, `threshold_proposal_schema_v1.json` semasina uygun olmali.
  2. review_record'un `decision`'i TAM OLARAK "APPROVE" olmali (REJECT/
     NEEDS_DATA/baska HERHANGI bir deger REDDEDILIR).
  3. review_record'un `proposal_id`'si proposal ile eslesmeli.
  4. review_record'daki `linked_proposal_checksum`, proposal'in GERCEKTEN
     yeniden hesaplanmis checksum'iyla BIREBIR eslesmeli (proposal
     review'dan SONRA degismisse -- kasitli veya yanlislikla -- apply
     REDDEDILIR).

Basarili bir apply:
  - Hedef dosyanin (model_gateway_alerts.yaml) bir YEDEGINI olusturur
    (reports/threshold_apply_<UTC>/backups/).
  - Yalnizca ILGILI sayisal esik degerini metin-tabanli, HEDEFLI bir
    regex ile degistirir (rollback_observability.ps1 ile AYNI ilke --
    tam bir YAML parse+re-dump YAPILMAZ, dosyadaki aciklamalar KORUNUR).
  - `infra/monitoring/baseline/approved_checksums_ledger.jsonl`'e bir
    girdi EKLER -- `detect_observability_drift.py` bunu, bu degisikligi
    "onayli/beklenen" olarak taniyip CRITICAL drift olarak
    ISARETLEMEMEK icin kullanir.
  - `data/audit/audit.log.jsonl`'e (GERCEK, mevcut `AuditLogger` ile --
    apps/orchestrator/src/audit_logger.py) bir denetim kaydi EKLER.
  - `reports/threshold_apply_<UTC>/apply_report.md`+`.json` yazar.

Cikis kodu: 0 (uygun -- dry-run onizlemesi basarili VEYA gercek apply
basarili), 2 (uygunsuz/engellendi -- fabrike edilmis bir basari ASLA
donmez).
#>

param(
    [Parameter(Mandatory = $true)][string]$ProposalPath,
    [Parameter(Mandatory = $true)][string]$ReviewRecordPath,
    [string]$PythonExe = "d:\Projects\ezoezgi-ops\.venv\Scripts\python.exe",
    [string]$RepoRoot = "d:\Projects\ezoezgi-ops",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "$RepoRoot\services\model-gateway\src;$RepoRoot\apps\orchestrator\src;$RepoRoot\scripts\ops"

$ts = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$outDir = Join-Path $RepoRoot "reports\threshold_apply_$ts"

$mode = if ($Apply) { "apply" } else { "dry_run" }
Write-Output "=== Esik degisikligi uygulama -- mod: $mode ==="

$driverScript = @"
import sys, json
sys.path.insert(0, r'$RepoRoot\scripts\ops')
sys.path.insert(0, r'$RepoRoot\apps\orchestrator\src')
from datetime import datetime, timezone
from pathlib import Path

from threshold_governance_core import check_apply_eligibility
from threshold_apply_core import (
    apply_proposal_to_alerts_text, create_backup, append_ledger_entry, build_ledger_entry,
    build_apply_audit_details, ApplyOutcome, write_apply_report,
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

if not eligibility.eligible:
    outcome = ApplyOutcome(applied=False, dry_run=not apply_mode, reasons=eligibility.reasons)
    write_apply_report(outcome, proposal, out_dir, generated_at=generated_at)
    print('BLOCKED -- apply UYGULANMAYACAK:')
    for r in eligibility.reasons:
        print(f'  - {r}')
    print(f'evidence_dir={out_dir}')
    sys.exit(2)

print('UYGUNLUK: TUM kontroller GECTI (sema gecerli, decision=APPROVE, checksum eslesiyor).')

if not apply_mode:
    outcome = ApplyOutcome(
        applied=False, dry_run=True,
        reasons=['DRY-RUN modu: hicbir dosya degistirilmedi. Gercekten uygulamak icin -Apply gecin.'],
    )
    write_apply_report(outcome, proposal, out_dir, generated_at=generated_at)
    print(f'DRY-RUN: uygulanirsa degisecek anahtarlar: {list(proposal["proposed_values"].keys())}')
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

new_text, patched_kinds, failed_kinds = apply_proposal_to_alerts_text(old_text, proposal)

if failed_kinds:
    outcome = ApplyOutcome(
        applied=False, dry_run=False,
        reasons=[f'Su esik turleri icin desen dosyada BULUNAMADI (hicbir sey degistirilmedi): {failed_kinds}'],
        backup_path=str(backup_path),
    )
    write_apply_report(outcome, proposal, out_dir, generated_at=generated_at)
    print(f'BLOCKED (kismi basarisizlik -- HICBIR SEY yazilmadi): {outcome.reasons}')
    sys.exit(2)

target_path.write_text(new_text, encoding='utf-8')
new_checksum = compute_file_sha256(target_path)

ledger_entry = build_ledger_entry(
    proposal_id=proposal['proposal_id'], old_checksum=old_checksum, new_checksum=new_checksum,
    apply_report_path=str(out_dir / 'apply_report.json'), timestamp=generated_at,
)
append_ledger_entry(ledger_path, ledger_entry)

audit_details = build_apply_audit_details(
    proposal=proposal, review_record=review_record, backup_path=backup_path,
    patched_kinds=patched_kinds, old_checksum=old_checksum, new_checksum=new_checksum,
)
audit_logger = AuditLogger(log_path=repo_root / 'data' / 'audit' / 'audit.log.jsonl')
audit_record = audit_logger.log(
    alias=None, task='THRESHOLD_CHANGE_APPLY', status='APPLIED', risk_level='medium', details=audit_details,
)

outcome = ApplyOutcome(
    applied=True, dry_run=False, reasons=[], patched_kinds=patched_kinds,
    old_checksum=old_checksum, new_checksum=new_checksum, backup_path=str(backup_path),
)
write_apply_report(outcome, proposal, out_dir, generated_at=generated_at)

print(f'BASARILI: {patched_kinds} yamalandi.')
print(f'old_checksum={old_checksum}')
print(f'new_checksum={new_checksum}')
print(f'backup={backup_path}')
print(f'audit_request_id={audit_record["request_id"]}')
print(f'ledger_entry_eklendi={ledger_path}')
print(f'evidence_dir={out_dir}')
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

exit $exitCode
