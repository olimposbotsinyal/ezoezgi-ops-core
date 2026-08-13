<#
Model gateway olay (incident) kanit paketleyicisi.

Verilen zaman penceresi icin audit dilimini, saglayici/preflight anlik
goruntusunu, maskeli config parmak izini ve son bilinen-iyi git
referansindan bu yana degisen dosyalari toplar.
reports\incidents\gateway_<timestamp>\ altina paketler.

Kullanim:
    powershell -ExecutionPolicy Bypass -File scripts\ops\package_gateway_incident.ps1 `
        -WindowStart "2026-08-13T00:00:00+00:00" -WindowEnd "2026-08-13T06:00:00+00:00" `
        -KnownGoodRef "6ec97b1"

Parametreler verilmezse: son 6 saat + `git describe --tags` (yoksa HEAD~50).
#>

param(
    [string]$PythonExe = "d:\Projects\ezoezgi-ops\.venv\Scripts\python.exe",
    [string]$RepoRoot = "d:\Projects\ezoezgi-ops",
    [string]$WindowStart = "",
    [string]$WindowEnd = "",
    [string]$KnownGoodRef = ""
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $PythonExe)) {
    Write-Output "HARD_FAILURE: python bulunamadi: $PythonExe"
    exit 2
}

if (-not $WindowEnd) {
    $WindowEnd = (Get-Date).ToUniversalTime().ToString("o")
}
if (-not $WindowStart) {
    $WindowStart = (Get-Date).ToUniversalTime().AddHours(-6).ToString("o")
}
if (-not $KnownGoodRef) {
    $KnownGoodRef = "HEAD~50"
}

$ts = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$outDir = Join-Path $RepoRoot "reports\incidents\gateway_$ts"

$driverScript = @"
import sys
sys.path.insert(0, r'$RepoRoot\services\model-gateway\src')
sys.path.insert(0, r'$RepoRoot\services\tr-en-bridge\src')
sys.path.insert(0, r'$RepoRoot\apps\orchestrator\src')
sys.path.insert(0, r'$RepoRoot\scripts\ops')

import os
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import asdict, is_dataclass

from model_gateway.config import load_config
from model_gateway.router import ModelGatewayRouter

from package_gateway_incident_core import (
    build_config_fingerprint,
    collect_audit_slice,
    get_changed_files_since,
    get_git_sha,
    render_incident_summary,
    write_incident_bundle,
)

repo_root = Path(r'$RepoRoot')
config = load_config()
router = ModelGatewayRouter(config=config)

audit_path = repo_root / 'data' / 'audit' / 'audit.log.jsonl'
window_start = '$WindowStart'
window_end = '$WindowEnd'
audit_slice = collect_audit_slice(audit_path, window_start, window_end)

provider_health_raw = router.healthcheck_all()
provider_health = {
    name: {'healthy': s.healthy, 'detail': s.detail} for name, s in provider_health_raw.items()
}

preflight_snapshot = {}
if config.ollama_cpu_verify_enabled:
    from model_gateway.runtime_verify import verify_ollama_cpu_mode
    ollama_health = provider_health.get('ollama', {}).get('healthy', False)
    result = verify_ollama_cpu_mode(
        ollama_healthy=ollama_health,
        base_url=config.ollama_host,
        marker_file=config.ollama_cpu_marker_file,
        methods=config.ollama_cpu_verify_methods,
        timeout_ms=config.ollama_cpu_verify_timeout_ms,
    )
    preflight_snapshot = {
        'status': result.status,
        'reason_code': result.reason_code,
        'evidence': result.evidence,
        'checked_at': result.checked_at,
    }

git_sha = get_git_sha(repo_root)
changed_files = get_changed_files_since(repo_root, '$KnownGoodRef')

config_dict = {
    k: v for k, v in vars(config).items()
} if not is_dataclass(config) else asdict(config)

# Yalnizca model gateway ile ilgili degiskenler -- tum os.environ'u
# dumplamak (kullanici adi/proxy/PATH gibi ilgisiz makine bilgisi icerir)
# hem gereksiz hem de fazla veri toplama olurdu.
GATEWAY_ENV_PREFIXES = (
    'OLLAMA_', 'LOCAL_ALT_', 'REMOTE_', 'MODEL_PROVIDER_ORDER',
    'FALLBACK_', 'CIRCUIT_BREAKER_', 'METRICS_', 'ALERT_', 'SLO_',
    'DAILY_SMOKE_', 'STARTUP_PREFLIGHT_REQUIRED',
)
env_dict = {
    k: v for k, v in os.environ.items()
    if any(k.startswith(p) for p in GATEWAY_ENV_PREFIXES)
}
config_fingerprint = build_config_fingerprint(config_dict, env_dict)

generated_at = datetime.now(timezone.utc).isoformat()
summary = render_incident_summary(
    generated_at=generated_at,
    window_start=window_start,
    window_end=window_end,
    git_sha=git_sha,
    changed_files=changed_files,
    audit_slice=audit_slice,
    provider_health=provider_health,
    preflight_snapshot=preflight_snapshot,
)

out_dir = Path(r'$outDir')
write_incident_bundle(
    out_dir,
    summary_md=summary,
    audit_slice=audit_slice,
    config_fingerprint=config_fingerprint,
    provider_health=provider_health,
    preflight_snapshot=preflight_snapshot,
    changed_files=changed_files,
)

print(f"git_sha={git_sha}")
print(f"audit_records={len(audit_slice)}")
print(f"changed_files={len(changed_files)}")
print(f"evidence_dir={out_dir}")
"@

$driverPath = Join-Path $env:TEMP "package_gateway_incident_driver_$ts.py"
Set-Content -Path $driverPath -Value $driverScript -Encoding utf8

& $PythonExe $driverPath
$exitCode = $LASTEXITCODE

Remove-Item -Path $driverPath -Force -ErrorAction SilentlyContinue

if ($exitCode -ne 0) {
    Write-Output "HARD_FAILURE: paketleme basarisiz oldu (exit $exitCode)"
}

exit $exitCode
