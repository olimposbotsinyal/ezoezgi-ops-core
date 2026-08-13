<#
Haftalik gozlemlenebilirlik gozden gecirme (governance) -- Model Gateway.

Bu script, periyodik (haftalik) calistirilmasi ONERILEN bir "saglik
denetimi ritueli"dir -- go-live sonrasi ("post-GO") surekli
gozlemlenebilirlik borcu birikmesin diye:

  1) Gate snapshot (HAFIF mod -- `run_observability_gates.ps1 -Lightweight`,
     Gate E/tam pytest suite ATLANIR, yalnizca operasyonel altyapi
     saglik sinyalleri A/B/C/D kontrol edilir).
  2) Drift tespiti (`detect_observability_drift.py`) -- metrik semasi/
     alert kurallari/config varsayilanlari onayli durumdan sapmis mi?
  3) Son 7 gunluk (sentetik haric) fallback + null-intent oranlari
     (`calibrate_alert_thresholds.py`'nin AYNI hesaplama mantigi
     yeniden kullanilir -- iki ayri yerde farkli formul riski yok).
  4) `reports/weekly_observability_<YYYY-WW>/review.md`'ye bir girdi
     EKLER (var olanlari SILMEZ -- kalici bir gunluk/log olusturur).
  5) GREEN/YELLOW/RED durumu + gerekceleri yazdirir, durumla eslesen
     exit code (0/1/2) ile cikar -- zamanlanmis bir gorevden (Task
     Scheduler) tetiklenip sonucunun otomatik izlenebilmesi icin.

Bu script, gorev tanimindaki "auto-evidence" gereksinimini karsilar --
her calistirma otomatik olarak kendi kanit dizinini (gate/drift
raporlari + review.md girdisi) uretir.
#>

param(
    [string]$PythonExe = "d:\Projects\ezoezgi-ops\.venv\Scripts\python.exe",
    [string]$RepoRoot = "d:\Projects\ezoezgi-ops"
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "$RepoRoot\services\model-gateway\src;$RepoRoot\scripts\ops"

$ts = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")

Write-Output "=== Adim 1: Gate snapshot (lightweight mod) ==="
powershell -ExecutionPolicy Bypass -File "$RepoRoot\scripts\ops\run_observability_gates.ps1" -Lightweight
$gateExitCode = $LASTEXITCODE
Write-Output "Gate snapshot exit_code=$gateExitCode"

Write-Output "=== Adim 2: Drift tespiti ==="
$driftOutDir = "$RepoRoot\reports\drift_$ts"
& $PythonExe "$RepoRoot\scripts\ops\detect_observability_drift.py" --output-dir $driftOutDir | Out-Null
$driftExitCode = $LASTEXITCODE
Write-Output "Drift tespiti exit_code=$driftExitCode (kanit: $driftOutDir)"

Write-Output "=== Adim 3-5: Oranlar hesaplaniyor, karar veriliyor, review.md'ye eklendi ==="
$driverScript = @"
import sys
sys.path.insert(0, r'$RepoRoot\scripts\ops')
sys.path.insert(0, r'$RepoRoot\services\model-gateway\src')
from datetime import datetime, timezone
from pathlib import Path

from calibrate_alert_thresholds import calibrate_all, _is_synthetic
from model_gateway import metrics_aggregate
from model_gateway.config import load_config
from weekly_review_core import classify_weekly_status, render_review_entry_md, append_review_entry, status_to_exit_code

config = load_config()
jsonl_path = Path(config.metrics_jsonl_path)

events = metrics_aggregate.read_recent_events(jsonl_path, window_minutes=7 * 24 * 60)
non_synthetic = [e for e in events if not _is_synthetic(e)]

suggestions = calibrate_all(
    non_synthetic,
    current_null_intent_warn=config.alert_null_intent_warn,
    current_null_intent_crit=config.alert_null_intent_crit,
    current_fallback_spike_multiplier=config.alert_fallback_spike_multiplier,
)
null_intent = next(s for s in suggestions if s.alert_name == 'HIGH_NULL_INTENT_RATE')
fallback = next(s for s in suggestions if s.alert_name == 'FALLBACK_SPIKE')

gate_exit = $gateExitCode
drift_exit = $driftExitCode

result = classify_weekly_status(
    gate_exit_code=gate_exit,
    drift_exit_code=drift_exit,
    fallback_ratio=fallback.observed_rate,
    null_intent_ratio=null_intent.observed_rate,
    null_intent_warn=config.alert_null_intent_warn,
    null_intent_crit=config.alert_null_intent_crit,
)

now = datetime.now(timezone.utc)
iso_year, iso_week_num, _ = now.isocalendar()
iso_week = f'{iso_year}-W{iso_week_num:02d}'
generated_at = now.isoformat()

entry_md = render_review_entry_md(result, generated_at=generated_at, iso_week=iso_week)
review_path = Path(r'$RepoRoot') / 'reports' / f'weekly_observability_{iso_week}' / 'review.md'
append_review_entry(review_path, entry_md, iso_week=iso_week)

print(entry_md)
print(f'review_path={review_path}')
print(f'gate_snapshot_exit={gate_exit}')
print(f'drift_exit={drift_exit}')
sys.exit(status_to_exit_code(result.status))
"@
$driverPath = Join-Path $env:TEMP "weekly_observability_review_driver_$ts.py"
Set-Content -Path $driverPath -Value $driverScript -Encoding utf8
& $PythonExe $driverPath
$exitCode = $LASTEXITCODE
Remove-Item -Path $driverPath -Force -ErrorAction SilentlyContinue

exit $exitCode
