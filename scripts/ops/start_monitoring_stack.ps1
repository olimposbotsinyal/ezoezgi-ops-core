<#
Model gateway izleme yigini baslatici (Docker YOK -- bkz.
docs/ops/MONITORING_STACK_RUNBOOK.md "Neden Docker degil").

Bu makinede Docker kurulu degil (kesif asamasinda dogrulandi). Bu script
uc bilesenin HER BIRINI baslatmaya calisir ve HANGISININ gercekten
baslatilabildigini durustce raporlar:

  1) scripts/ops/serve_metrics.py -- HER ZAMAN calisir (stdlib, bagimlilik yok)
  2) prometheus.exe -- yalnizca PATH'te veya -PrometheusExe ile verilen
     yolda bulunursa
  3) alertmanager.exe -- yalnizca PATH'te veya -AlertmanagerExe ile
     verilen yolda bulunursa

PID'ler reports/monitoring_stack/pids.json'a yazilir --
stop_monitoring_stack.ps1 bunu okuyup durdurur.
#>

param(
    [string]$PythonExe = "d:\Projects\ezoezgi-ops\.venv\Scripts\python.exe",
    [string]$RepoRoot = "d:\Projects\ezoezgi-ops",
    [string]$PrometheusExe = "",
    [string]$AlertmanagerExe = "",
    [int]$MetricsPort = 9108
)

$ErrorActionPreference = "Stop"
$pidsDir = Join-Path $RepoRoot "reports\monitoring_stack"
New-Item -ItemType Directory -Force -Path $pidsDir | Out-Null
$pidsFile = Join-Path $pidsDir "pids.json"

$pids = @{}

# --- 1) serve_metrics.py -- her zaman calisir -------------------------
$env:PYTHONPATH = "$RepoRoot\services\model-gateway\src;$RepoRoot\services\tr-en-bridge\src;$RepoRoot\apps\orchestrator\src"
$metricsProc = Start-Process -FilePath $PythonExe `
    -ArgumentList "scripts\ops\serve_metrics.py", "--port", "$MetricsPort" `
    -WorkingDirectory $RepoRoot `
    -RedirectStandardOutput "$pidsDir\serve_metrics_stdout.log" `
    -RedirectStandardError "$pidsDir\serve_metrics_stderr.log" `
    -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 2
$pids["serve_metrics"] = $metricsProc.Id
Write-Output "OK: serve_metrics.py baslatildi (PID $($metricsProc.Id), http://127.0.0.1:$MetricsPort/metrics)"

# --- 2) Prometheus -- yalnizca bulunursa ------------------------------
$promExe = if ($PrometheusExe) { $PrometheusExe } else { (Get-Command "prometheus.exe" -ErrorAction SilentlyContinue).Source }
if ($promExe -and (Test-Path $promExe)) {
    $promProc = Start-Process -FilePath $promExe `
        -ArgumentList "--config.file=infra\monitoring\prometheus\prometheus.yml" `
        -WorkingDirectory $RepoRoot `
        -RedirectStandardOutput "$pidsDir\prometheus_stdout.log" `
        -RedirectStandardError "$pidsDir\prometheus_stderr.log" `
        -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 2
    $pids["prometheus"] = $promProc.Id
    Write-Output "OK: Prometheus baslatildi (PID $($promProc.Id))"
} else {
    Write-Output "SKIPPED_NOT_INSTALLED: prometheus.exe bulunamadi (PATH veya -PrometheusExe ile belirtin)"
}

# --- 3) Alertmanager -- yalnizca bulunursa -----------------------------
$amExe = if ($AlertmanagerExe) { $AlertmanagerExe } else { (Get-Command "alertmanager.exe" -ErrorAction SilentlyContinue).Source }
if ($amExe -and (Test-Path $amExe)) {
    $amProc = Start-Process -FilePath $amExe `
        -ArgumentList "--config.file=infra\monitoring\alertmanager\alertmanager.yml" `
        -WorkingDirectory $RepoRoot `
        -RedirectStandardOutput "$pidsDir\alertmanager_stdout.log" `
        -RedirectStandardError "$pidsDir\alertmanager_stderr.log" `
        -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 2
    $pids["alertmanager"] = $amProc.Id
    Write-Output "OK: Alertmanager baslatildi (PID $($amProc.Id))"
} else {
    Write-Output "SKIPPED_NOT_INSTALLED: alertmanager.exe bulunamadi (PATH veya -AlertmanagerExe ile belirtin)"
}

$pids | ConvertTo-Json | Set-Content -Path $pidsFile -Encoding utf8
Write-Output "PID kaydi: $pidsFile"

if (-not $pids.ContainsKey("prometheus") -or -not $pids.ContainsKey("alertmanager")) {
    Write-Output "NOT: Prometheus/Alertmanager bu makinede kurulu degil -- yalnizca metrics endpoint'i canli."
    Write-Output "Kurulum: https://prometheus.io/download/ (prometheus + alertmanager binary'lerini indirin, PATH'e ekleyin)"
}
