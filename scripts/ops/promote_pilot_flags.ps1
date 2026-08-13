<#
Bir `evaluate_pilot_promotion.py` calistirmasinin `promotion_report.json`'unu
okur ve YALNIZCA `decision == PROMOTE` olan v1.2 pilot ozellikleri icin
`infra/monitoring/governance/pilot_flags_state.json`'i GERCEKTEN
GUNCELLER (`promoted: true`).

**VARSAYILAN MOD: DRY-RUN.** Hicbir durum dosyasi degistirilmez --
yalnizca hangi ozelliklerin PROMOTE edilecegi raporlanir. Gercekten
uygulamak icin `-Apply` acikca gecilmelidir.

**Kritik guvenlik ilkesi:** Bu script, degerlendiricinin (evaluator)
KENDI kararini yeniden HESAPLAMAZ/SORGULAMAZ -- yalnizca ONCEDEN
uretilmis bir `promotion_report.json`'u OKUR ve ORADAKI kararlara
sadik kalir. `EXTEND_PILOT`/`REJECT` olan ozellikler dokunulmadan
kalir (mevcut `pilot_flags_state.json` degerleri KORUNUR).

Basarili bir apply:
  - `infra/monitoring/governance/pilot_flags_state.json`'i GUNCELLER
    (yalnizca PROMOTE olan ozellikler icin `promoted: true`,
    `promoted_at_utc`, `promotion_report_path`).
  - `data/audit/audit.log.jsonl`'e HER degerlendirilen ozellik icin bir
    kayit EKLER: `task=pilot_flag_promoted` (PROMOTE + uygulandi) veya
    `task=pilot_flag_not_promoted` (EXTEND_PILOT/REJECT -- uygulanmadi).
  - `reports/pilot_promotion_<UTC>/flag_apply_report.md`+`.json` yazar
    (ONCEKI + YENI durumun TAM anlik goruntusu dahil -- rollback bunu
    kullanir).

Cikis kodu: 0 (basarili -- dry-run onizlemesi VEYA gercek apply,
PROMOTE edilen 0 veya daha fazla ozellikle), 2 (basarisiz/engellendi --
gecersiz/okunamayan promotion_report.json).
#>

param(
    [Parameter(Mandatory = $true)][string]$PromotionReportPath,
    [string]$PythonExe = "d:\Projects\ezoezgi-ops\.venv\Scripts\python.exe",
    [string]$RepoRoot = "d:\Projects\ezoezgi-ops",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "$RepoRoot\services\model-gateway\src;$RepoRoot\apps\orchestrator\src;$RepoRoot\scripts\ops"

$ts = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$outDir = Join-Path $RepoRoot "reports\pilot_promotion_$ts"

$mode = if ($Apply) { "apply" } else { "dry_run" }
Write-Output "=== Pilot flag terfi uygulama -- mod: $mode ==="

$driverScript = @"
import sys, json
sys.path.insert(0, r'$RepoRoot\scripts\ops')
sys.path.insert(0, r'$RepoRoot\apps\orchestrator\src')
from datetime import datetime, timezone
from pathlib import Path

from pilot_promotion_core import (
    DECISION_PROMOTE, apply_promotions_to_state, load_pilot_flags_state, write_pilot_flags_state,
    write_flag_apply_report,
)
from audit_logger import AuditLogger

repo_root = Path(r'$RepoRoot')
promotion_report_path = Path(r'$PromotionReportPath')
state_path = repo_root / 'infra' / 'monitoring' / 'governance' / 'pilot_flags_state.json'
out_dir = Path(r'$outDir')
apply_mode = $(if ($Apply) { "True" } else { "False" })

generated_at = datetime.now(timezone.utc).isoformat()

try:
    promotion_report = json.loads(promotion_report_path.read_text(encoding='utf-8'))
except (OSError, json.JSONDecodeError) as exc:
    print(f'HATA: promotion_report.json okunamadi/ayristirilamadi ({promotion_report_path}): {exc}')
    sys.exit(2)

decisions_list = promotion_report.get('decisions')
if not isinstance(decisions_list, list):
    print(f'HATA: promotion_report.json beklenen sekle uymuyor (decisions listesi yok): {promotion_report_path}')
    sys.exit(2)

decisions_by_feature = {d['feature']: d['decision'] for d in decisions_list if 'feature' in d and 'decision' in d}
print(f'Degerlendirme kararlari (kaynak: {promotion_report_path}):')
for feature, decision in decisions_by_feature.items():
    print(f'  - {feature}: {decision}')

current_state = load_pilot_flags_state(state_path)

if not apply_mode:
    would_apply = {f: (d == DECISION_PROMOTE) for f, d in decisions_by_feature.items()}
    write_flag_apply_report(
        decisions_by_feature, would_apply, out_dir, generated_at=generated_at, dry_run=True,
        previous_state=current_state, new_state=current_state,
    )
    print('DRY-RUN: hicbir durum dosyasi degistirilmedi.')
    print(f'evidence_dir={out_dir}')
    sys.exit(0)

new_state, previous_values = apply_promotions_to_state(
    current_state, decisions_by_feature, generated_at=generated_at, promotion_report_path=str(promotion_report_path),
)
write_pilot_flags_state(state_path, new_state)

applied = {}
audit_logger = AuditLogger(log_path=repo_root / 'data' / 'audit' / 'audit.log.jsonl')
for feature, decision in decisions_by_feature.items():
    is_promoted_now = decision == DECISION_PROMOTE
    applied[feature] = is_promoted_now
    audit_task = 'pilot_flag_promoted' if is_promoted_now else 'pilot_flag_not_promoted'
    audit_details = {
        'feature': feature, 'decision': decision,
        'promotion_report_path': str(promotion_report_path),
        'previous_state': previous_values.get(feature, current_state.get(feature)),
        'new_state': new_state.get(feature),
    }
    audit_record = audit_logger.log(
        alias=None, task=audit_task, status='APPLIED' if is_promoted_now else 'SKIPPED',
        risk_level='high' if is_promoted_now else 'low', details=audit_details,
    )
    print(f'  audit: {feature} -> {audit_task} (request_id={audit_record["request_id"]})')

write_flag_apply_report(
    decisions_by_feature, applied, out_dir, generated_at=generated_at, dry_run=False,
    previous_state=current_state, new_state=new_state,
)

print(f'BASARILI: {sum(applied.values())} ozellik PROMOTE edildi, {len(applied) - sum(applied.values())} ozellik degismeden kaldi.')
print(f'pilot_flags_state_guncellendi={state_path}')
print(f'evidence_dir={out_dir}')
sys.exit(0)
"@

$driverPath = Join-Path $env:TEMP "promote_pilot_flags_driver_$ts.py"
Set-Content -Path $driverPath -Value $driverScript -Encoding utf8
& $PythonExe $driverPath
$exitCode = $LASTEXITCODE
Remove-Item -Path $driverPath -Force -ErrorAction SilentlyContinue

if (-not $Apply -and $exitCode -eq 0) {
    Write-Output ""
    Write-Output "DRY-RUN tamamlandi -- hicbir durum dosyasi degistirilmedi. Gercekten uygulamak icin: -Apply"
}

exit $exitCode
