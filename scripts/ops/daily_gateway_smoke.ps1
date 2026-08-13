<#
Model gateway gunluk smoke kontrolu (Windows-first).

daily_gateway_smoke_core.py'deki saf mantigi gercek config/router/
runtime_verify/ollama_nlu ile calistirir, kanit paketini
reports/daily_smoke_<UTC>/ altina yazar.

Kullanim:
    powershell -ExecutionPolicy Bypass -File scripts\ops\daily_gateway_smoke.ps1

Cikis kodu (daily_gateway_smoke_core.py ile ayni):
    0 -> saglikli
    1 -> bozulmus ama calisiyor
    2 -> aksiyon gerekiyor
#>

param(
    [string]$PythonExe = "d:\Projects\ezoezgi-ops\.venv\Scripts\python.exe",
    [string]$RepoRoot = "d:\Projects\ezoezgi-ops"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $PythonExe)) {
    Write-Output "HARD_FAILURE: python bulunamadi: $PythonExe"
    exit 2
}

$ts = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$outDir = Join-Path $RepoRoot "reports\daily_smoke_$ts"

$driverScript = @"
import sys
sys.path.insert(0, r'$RepoRoot\services\model-gateway\src')
sys.path.insert(0, r'$RepoRoot\services\tr-en-bridge\src')
sys.path.insert(0, r'$RepoRoot\apps\orchestrator\src')
sys.path.insert(0, r'$RepoRoot\scripts\ops')

from pathlib import Path

from model_gateway.config import load_config
from model_gateway.metrics import get_metrics, configure_metrics
from model_gateway.policy import is_remote_allowed
from model_gateway.router import ModelGatewayRouter
from model_gateway.runtime_verify import verify_ollama_cpu_mode
from ollama_nlu import classify as ollama_classify

from daily_gateway_smoke_core import run_daily_smoke, write_evidence_bundle
from datetime import datetime, timezone

config = load_config()
configure_metrics(
    config.metrics_enabled,
    config.metrics_exporter,
    sink_type=config.metrics_sink,
    jsonl_path=config.metrics_jsonl_path,
    jsonl_max_mb=config.metrics_jsonl_max_mb,
    jsonl_retention_days=config.metrics_jsonl_retention_days,
)
metrics = get_metrics()
router = ModelGatewayRouter(config=config, metrics=metrics)

audit_path = Path(r'$RepoRoot') / 'data' / 'audit' / 'audit.log.jsonl'

result = run_daily_smoke(
    config=config,
    router=router,
    verify_fn=verify_ollama_cpu_mode,
    classify_fn=ollama_classify,
    is_remote_allowed_fn=is_remote_allowed,
    metrics_snapshot_fn=metrics.snapshot,
    audit_log_path=audit_path,
)

out_dir = Path(r'$outDir')
generated_at = datetime.now(timezone.utc).isoformat()
write_evidence_bundle(result, out_dir, generated_at)

print(f"exit_code={result.exit_code}")
for f in result.findings:
    print(f"- {f}")
print(f"evidence_dir={out_dir}")

sys.exit(result.exit_code)
"@

$driverPath = Join-Path $env:TEMP "daily_gateway_smoke_driver_$ts.py"
Set-Content -Path $driverPath -Value $driverScript -Encoding utf8

& $PythonExe $driverPath
$exitCode = $LASTEXITCODE

Remove-Item -Path $driverPath -Force -ErrorAction SilentlyContinue

exit $exitCode
