<#
Daha once `promote_pilot_flags.ps1 -Apply` ile UYGULANMIS bir pilot
flag terfisini GERI ALIR -- `flag_apply_report.json`'daki `previous_state`
anlik goruntusunden `infra/monitoring/governance/pilot_flags_state.json`'i
TERFI ONCESI haline dondurur.

**VARSAYILAN MOD: DRY-RUN.** Hicbir durum dosyasi degistirilmez --
yalnizca ne geri yuklenecegi raporlanir. Gercekten geri almak icin
`-Apply` acikca gecilmelidir.

Girdi: bir `flag_apply_report.json` yolu (`-FlagApplyReportPath`).

Basarili bir rollback:
  - `pilot_flags_state.json`'i, o apply'dan HEMEN ONCEKI durumuna
    (`flag_apply_report.json`'daki `previous_state`) BIREBIR dondurur.
  - `data/audit/audit.log.jsonl`'e bir `task=pilot_flag_rollback`
    denetim kaydi EKLER.
  - `reports/pilot_flag_rollback_<UTC>/rollback_report.md`+`.json` yazar.

Cikis kodu: 0 (basarili -- dry-run onizlemesi VEYA gercek rollback),
2 (basarisiz/engellendi).
#>

param(
    [Parameter(Mandatory = $true)][string]$FlagApplyReportPath,
    [string]$PythonExe = "d:\Projects\ezoezgi-ops\.venv\Scripts\python.exe",
    [string]$RepoRoot = "d:\Projects\ezoezgi-ops",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "$RepoRoot\services\model-gateway\src;$RepoRoot\apps\orchestrator\src;$RepoRoot\scripts\ops"

$ts = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$outDir = Join-Path $RepoRoot "reports\pilot_flag_rollback_$ts"

$mode = if ($Apply) { "apply" } else { "dry_run" }
Write-Output "=== Pilot flag terfisi geri alma (rollback) -- mod: $mode ==="

$driverScript = @"
import sys, json
sys.path.insert(0, r'$RepoRoot\scripts\ops')
sys.path.insert(0, r'$RepoRoot\apps\orchestrator\src')
from datetime import datetime, timezone
from pathlib import Path

from pilot_promotion_core import load_pilot_flags_state, write_pilot_flags_state
from audit_logger import AuditLogger

repo_root = Path(r'$RepoRoot')
flag_apply_report_path = Path(r'$FlagApplyReportPath')
state_path = repo_root / 'infra' / 'monitoring' / 'governance' / 'pilot_flags_state.json'
out_dir = Path(r'$outDir')
apply_mode = $(if ($Apply) { "True" } else { "False" })

generated_at = datetime.now(timezone.utc).isoformat()

def _write_simple_report(restored, reasons):
    out_dir.mkdir(parents=True, exist_ok=True)
    md_lines = [
        '# Pilot Flag Terfisi Geri Alma (Rollback) Raporu', '',
        f'Uretildi (UTC): {generated_at}',
        f'Kaynak flag_apply_report: {flag_apply_report_path}',
        f'Sonuc: {"BASARILI" if restored else "BASARISIZ/ENGELLENDI"}', '',
    ]
    if reasons:
        md_lines.append('## Nedenler')
        md_lines.append('')
        for r in reasons:
            md_lines.append(f'- {r}')
    (out_dir / 'rollback_report.md').write_text('\n'.join(md_lines) + '\n', encoding='utf-8')
    payload = {
        'generated_at': generated_at, 'source_flag_apply_report': str(flag_apply_report_path),
        'restored': restored, 'reasons': reasons,
    }
    (out_dir / 'rollback_report.json').write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')

try:
    flag_apply_report = json.loads(flag_apply_report_path.read_text(encoding='utf-8'))
except (OSError, json.JSONDecodeError) as exc:
    _write_simple_report(False, [f'flag_apply_report.json okunamadi: {exc}'])
    print(f'BLOCKED: flag_apply_report.json okunamadi: {exc}')
    sys.exit(2)

previous_state = flag_apply_report.get('previous_state')
if not isinstance(previous_state, dict):
    _write_simple_report(False, ['flag_apply_report.json icinde gecerli bir previous_state yok -- geri alinacak bir sey yok'])
    print('BLOCKED: previous_state eksik/gecersiz')
    sys.exit(2)

if flag_apply_report.get('dry_run'):
    _write_simple_report(False, ['Kaynak flag_apply_report.json bir DRY-RUN kaydidir (hicbir sey uygulanmamisti) -- geri alinacak bir sey yok'])
    print('BLOCKED: kaynak rapor dry-run kaydi, geri alinacak gercek bir degisiklik yok')
    sys.exit(2)

current_state = load_pilot_flags_state(state_path)
print(f'Su anki durum: {json.dumps(current_state, ensure_ascii=False)}')
print(f'Geri yuklenecek (onceki) durum: {json.dumps(previous_state, ensure_ascii=False)}')

if not apply_mode:
    _write_simple_report(False, ['DRY-RUN modu: hicbir durum dosyasi degistirilmedi. Gercekten geri almak icin -Apply gecin.'])
    print('DRY-RUN: pilot_flags_state.json onceki duruma geri yuklenecekti.')
    print(f'evidence_dir={out_dir}')
    sys.exit(0)

write_pilot_flags_state(state_path, previous_state)

audit_logger = AuditLogger(log_path=repo_root / 'data' / 'audit' / 'audit.log.jsonl')
audit_record = audit_logger.log(
    alias=None, task='pilot_flag_rollback', status='ROLLED_BACK', risk_level='high',
    details={
        'source_flag_apply_report': str(flag_apply_report_path),
        'restored_state': previous_state,
    },
)

_write_simple_report(True, ['pilot_flags_state.json basariyla onceki duruma geri yuklendi'])
print('BASARILI: pilot_flags_state.json onceki duruma geri yuklendi.')
print(f'audit_request_id={audit_record["request_id"]}')
print(f'evidence_dir={out_dir}')
sys.exit(0)
"@

$driverPath = Join-Path $env:TEMP "rollback_pilot_flags_driver_$ts.py"
Set-Content -Path $driverPath -Value $driverScript -Encoding utf8
& $PythonExe $driverPath
$exitCode = $LASTEXITCODE
Remove-Item -Path $driverPath -Force -ErrorAction SilentlyContinue

if (-not $Apply -and $exitCode -eq 0) {
    Write-Output ""
    Write-Output "DRY-RUN tamamlandi -- hicbir durum dosyasi degistirilmedi. Gercekten geri almak icin: -Apply"
}

exit $exitCode
