<#
Gozlemlenebilirlik rollback drill -- guvenli temel duruma (Asama 1,
observe-only) donus.

**VARSAYILAN MOD: DRY-RUN.** Hicbir dosya degistirilmez -- yalnizca ne
DEGISECEGI raporlanir. Gercekten uygulamak icin `-Apply` acikca
gecilmelidir.

Ne yapar (Apply modunda, yalnizca gerekliyse):
  - `infra/monitoring/alertmanager/alertmanager.yml`'deki route
    receiver'larini (varsa `warning-sink`/`critical-sink`) tekrar
    `null-receiver`'a (Asama 1) dondurur -- METIN-TABANLI, HEDEFLI
    degisiklik (tam bir YAML parse+re-dump YAPILMAZ ki dosyadaki
    degerli Asama 1/2/3 aciklama satirlari KORUNSUN).

Ne yapmaz (hicbir modda):
  - `/metrics` endpoint'ini KAPATMAZ (METRICS_ENABLED dokunulmaz).
  - `data/audit/audit.log.jsonl`'i SILMEZ/degistirmez.
  - `data/metrics/*.jsonl`'i SILMEZ/degistirmez.
  - Kod/commit revert YAPMAZ -- yalnizca ALERTMANAGER ROUTING config'i
    kapsam dahilindedir (gorev tanimi: "disable live routing").

Idempotent: zaten guvenli durumda olan bir alan icin hicbir degisiklik
yapilmaz -- art arda calistirmak zararsizdir.
#>

param(
    [string]$PythonExe = "d:\Projects\ezoezgi-ops\.venv\Scripts\python.exe",
    [string]$RepoRoot = "d:\Projects\ezoezgi-ops",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "$RepoRoot\services\model-gateway\src;$RepoRoot\scripts\ops"

$amPath = Join-Path $RepoRoot "infra\monitoring\alertmanager\alertmanager.yml"
$ts = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$outDir = Join-Path $RepoRoot "reports\rollback_drill_$ts"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$mode = if ($Apply) { "apply" } else { "dry_run" }
Write-Output "=== Gozlemlenebilirlik rollback -- mod: $mode ==="

$driverScript = @"
import sys, json, re
sys.path.insert(0, r'$RepoRoot\scripts\ops')
sys.path.insert(0, r'$RepoRoot\services\model-gateway\src')
from datetime import datetime, timezone
from pathlib import Path

import yaml

from rollback_observability_core import plan_rollback, render_rollback_report
from model_gateway.config import load_config

am_path = Path(r'$amPath')
raw_text = am_path.read_text(encoding='utf-8')
parsed = yaml.safe_load(raw_text)

route = parsed.get('route', {})
route_receiver = route.get('receiver', '?')
sub_routes = route.get('routes', [])
warning_receiver = next((r.get('receiver', '?') for r in sub_routes if r.get('match', {}).get('severity') == 'warning'), '?')
critical_receiver = next((r.get('receiver', '?') for r in sub_routes if r.get('match', {}).get('severity') == 'critical'), '?')

config = load_config()

plan = plan_rollback(
    route_receiver=route_receiver,
    warning_route_receiver=warning_receiver,
    critical_route_receiver=critical_receiver,
    metrics_enabled=config.metrics_enabled,
    dry_run=not $($Apply.ToString()),
)

generated_at = datetime.now(timezone.utc).isoformat()
report = render_rollback_report(plan, generated_at=generated_at)

applied_changes = []
if $($Apply.ToString()) and plan.any_change_needed:
    new_text = raw_text
    for action in plan.actions:
        if not action.needs_change or action.target == 'METRICS_ENABLED':
            continue
        # HEDEFLI metin degisikligi -- yalnizca ilgili receiver DEGERINI
        # degistirir, dosyanin geri kalanini (aciklamalar dahil) OLDUGU
        # GIBI birakir.
        pattern = re.compile(
            r'receiver:\s*\"' + re.escape(action.current_value) + r'\"'
        )
        replacement = f'receiver: \"{action.safe_value}\"'
        new_text, n = pattern.subn(replacement, new_text, count=1)
        if n > 0:
            applied_changes.append(f'{action.target}: {action.current_value} -> {action.safe_value}')
    if applied_changes:
        am_path.write_text(new_text, encoding='utf-8')

out_dir = Path(r'$outDir')
(out_dir / 'rollback_report.md').write_text(report, encoding='utf-8')
(out_dir / 'rollback_plan.json').write_text(
    json.dumps(
        {
            'generated_at': generated_at,
            'mode': '$mode',
            'any_change_needed': plan.any_change_needed,
            'applied_changes': applied_changes,
            'actions': [
                {
                    'target': a.target, 'current_value': a.current_value,
                    'safe_value': a.safe_value, 'needs_change': a.needs_change,
                }
                for a in plan.actions
            ],
        },
        indent=2, ensure_ascii=False,
    ),
    encoding='utf-8',
)

print(report)
if applied_changes:
    print('APPLIED:')
    for c in applied_changes:
        print(f'  - {c}')
elif $($Apply.ToString()):
    print('APPLY modu istendi ama degisiklik gerekmiyordu (zaten guvenli durumda).')
print(f'evidence_dir={out_dir}')
"@

$driverPath = Join-Path $env:TEMP "rollback_observability_driver_$ts.py"
Set-Content -Path $driverPath -Value $driverScript -Encoding utf8
& $PythonExe $driverPath
$exitCode = $LASTEXITCODE
Remove-Item -Path $driverPath -Force -ErrorAction SilentlyContinue

if (-not $Apply) {
    Write-Output ""
    Write-Output "DRY-RUN tamamlandi -- hicbir dosya degistirilmedi. Gercekten uygulamak icin: -Apply"
}

exit $exitCode
