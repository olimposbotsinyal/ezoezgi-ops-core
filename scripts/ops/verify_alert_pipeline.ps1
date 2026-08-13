<#
E2E alert pipeline dogrulamasi -- metrics -> Prometheus -> Alertmanager.

**ONEMLI:** Bu script kendi, GECICI `serve_metrics.py` surecini
`--enable-debug-injection` ILE baslatir (bkz. serve_metrics.py -- bu
bayrak yalnizca boyle izole/test amacli koşularda acilmalidir, genel
`start_monitoring_stack.ps1` bunu ASLA varsayilan olarak acmaz). Bu,
sentetik sinyalin AYNI surecin /metrics ciktisinda gercekten gorunmesini
saglar (bkz. docs/ops/MONITORING_STACK_RUNBOOK.md "surec-ici singleton"
sinirlamasi -- ayri surecler arasinda metrik paylasilmaz).

Akis:
  1) Kendi gecici metrics+injection surecini baslat
  2) /metrics erisilebilir mi kontrol et
  3) Prometheus'ta hedef UP mi kontrol et (http://127.0.0.1:9090, varsa)
  4) Bir sentetik sinyali BU surece HTTP uzerinden enjekte et
  5) Alert Prometheus'ta firing/pending mi kontrol et (varsa)
  6) Alert Alertmanager'da gorunuyor mu kontrol et (http://127.0.0.1:9093, varsa)
  7) Kanit paketini reports\alert_e2e_<UTC>\ altina yaz, gecici sureci durdur

Bu makinede Prometheus/Alertmanager kurulu DEGILSE (varsayilan durum,
bkz. docs/ops/MONITORING_STACK_RUNBOOK.md), adim 3/5/6 durustce
"erisilemez" olarak isaretlenir -- cikis kodu 1 (kismi) olur, 0 (tam
basari) fabrika edilmez.

NOT: Bu script izole/test ortaminda veya bakim penceresinde
calistirilmalidir -- gecici surec varsayilan portu (9108) kullanir,
gercek bir uretim metrics surecini gecici olarak degistirebilir.
#>

param(
    [string]$PythonExe = "d:\Projects\ezoezgi-ops\.venv\Scripts\python.exe",
    [string]$RepoRoot = "d:\Projects\ezoezgi-ops",
    [string]$Mode = "fallback-spike",
    [string]$AlertName = "ModelGatewayFallbackSpike",
    [int]$SyntheticCount = 20,
    [int]$MetricsPort = 9108,
    [int]$PrometheusPort = 9090,
    [int]$AlertmanagerPort = 9093
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "$RepoRoot\services\model-gateway\src;$RepoRoot\services\tr-en-bridge\src;$RepoRoot\apps\orchestrator\src;$RepoRoot\scripts\ops"

$ts = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$outDir = Join-Path $RepoRoot "reports\alert_e2e_$ts"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

Write-Output "=== Adim 1: gecici metrics+injection sureci baslatiliyor (izole/test amacli) ==="
$metricsProc = Start-Process -FilePath $PythonExe `
    -ArgumentList "scripts\ops\serve_metrics.py", "--port", "$MetricsPort", "--enable-debug-injection" `
    -WorkingDirectory $RepoRoot `
    -RedirectStandardOutput "$outDir\serve_metrics_stdout.log" `
    -RedirectStandardError "$outDir\serve_metrics_stderr.log" `
    -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 2
Write-Output "OK: PID $($metricsProc.Id)"

try {
    Write-Output "=== Adim 2: /metrics erisilebilirligi kontrol ediliyor ==="
    $metricsReachable = $false
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$MetricsPort/metrics" -TimeoutSec 5
        $metricsReachable = ($resp.StatusCode -eq 200)
    } catch {
        Write-Output "metrics endpoint erisilemedi: $_"
    }

    Write-Output "=== Adim 3: Prometheus hedef durumu kontrol ediliyor ==="
    $prometheusReachable = $false
    $promTargetsJson = "{}"
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$PrometheusPort/api/v1/targets" -TimeoutSec 5
        $prometheusReachable = $true
        $promTargetsJson = $resp.Content
    } catch {
        Write-Output "Prometheus erisilemedi (kurulu degilse beklenen): $_"
    }
    Set-Content -Path "$outDir\prom_targets.json" -Value $promTargetsJson -Encoding utf8

    Write-Output "=== Adim 4: sentetik sinyal AYNI surece HTTP ile enjekte ediliyor (mode=$Mode) ==="
    & $PythonExe "$RepoRoot\scripts\ops\emit_synthetic_gateway_signals.py" `
        --mode $Mode --count $SyntheticCount --output-dir $outDir `
        --target-url "http://127.0.0.1:$MetricsPort"

    Write-Output "=== Adim 4b: enjeksiyon sonrasi /metrics tekrar okunuyor (dogrulama) ==="
    $metricsSampleText = ""
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$MetricsPort/metrics" -TimeoutSec 5
        $metricsSampleText = $resp.Content
    } catch {
        Write-Output "metrics endpoint (adim 4b) erisilemedi: $_"
    }
    Set-Content -Path "$outDir\metrics_sample.txt" -Value $metricsSampleText -Encoding utf8
    $syntheticVisibleInSample = $metricsSampleText -match "synthetic=`"true`""
    Write-Output "Sentetik ornek /metrics ciktisinda gorunuyor mu: $syntheticVisibleInSample"

    Write-Output "=== Adim 5: Prometheus alert durumu kontrol ediliyor ==="
    $promAlertsJson = "{}"
    if ($prometheusReachable) {
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$PrometheusPort/api/v1/alerts" -TimeoutSec 5
            $promAlertsJson = $resp.Content
        } catch {
            Write-Output "Prometheus alerts endpoint'i erisilemedi: $_"
        }
    }
    Set-Content -Path "$outDir\prom_alerts.json" -Value $promAlertsJson -Encoding utf8

    Write-Output "=== Adim 6: Alertmanager durumu kontrol ediliyor ==="
    $alertmanagerReachable = $false
    $amAlertsJson = "[]"
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$AlertmanagerPort/api/v2/alerts" -TimeoutSec 5
        $alertmanagerReachable = $true
        $amAlertsJson = $resp.Content
    } catch {
        Write-Output "Alertmanager erisilemedi (kurulu degilse beklenen): $_"
    }
    Set-Content -Path "$outDir\alertmanager_alerts.json" -Value $amAlertsJson -Encoding utf8

    Write-Output "=== Adim 7: karar hesaplaniyor + kanit paketi yaziliyor ==="

    $driverScript = @"
import sys, json
sys.path.insert(0, r'$RepoRoot\scripts\ops')
from verify_alert_pipeline_core import evaluate_pipeline, parse_prometheus_targets, parse_prometheus_alerts, parse_alertmanager_alerts

def safe_load(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None

targets_data = safe_load(r'$outDir\prom_targets.json')
alerts_data = safe_load(r'$outDir\prom_alerts.json')
am_data = safe_load(r'$outDir\alertmanager_alerts.json')

targets = parse_prometheus_targets(targets_data, 'ezoezgi_model_gateway') if targets_data else []
target_health = targets[0]['health'] if targets else None
prom_alerts = parse_prometheus_alerts(alerts_data, '$AlertName') if alerts_data else []
am_alerts = parse_alertmanager_alerts(am_data, '$AlertName') if am_data else []

result = evaluate_pipeline(
    metrics_reachable=$($metricsReachable.ToString()),
    prometheus_reachable=$($prometheusReachable.ToString()),
    target_health=target_health,
    prom_alert_states=[a.get('state') for a in prom_alerts],
    alertmanager_reachable=$($alertmanagerReachable.ToString()),
    am_alert_found=len(am_alerts) > 0,
)

summary_lines = [
    '# Alert E2E Pipeline Dogrulamasi -- Ozet',
    '',
    f'- Cikis kodu: {result.exit_code}',
    f'- Sentetik mod: $Mode (alertname: $AlertName, count: $SyntheticCount)',
    f'- Sentetik ornek /metrics ciktisinda gorundu mu: $syntheticVisibleInSample',
    '',
    '## Bulgular',
    '',
]
for f in result.findings:
    summary_lines.append(f'- {f}')

with open(r'$outDir\summary.md', 'w', encoding='utf-8') as f:
    f.write('\n'.join(summary_lines) + '\n')

print(f'exit_code={result.exit_code}')
for finding in result.findings:
    print(f'- {finding}')

sys.exit(result.exit_code)
"@

    $driverPath = Join-Path $env:TEMP "verify_alert_pipeline_driver_$ts.py"
    Set-Content -Path $driverPath -Value $driverScript -Encoding utf8
    & $PythonExe $driverPath
    $exitCode = $LASTEXITCODE
    Remove-Item -Path $driverPath -Force -ErrorAction SilentlyContinue
} finally {
    Write-Output "=== Temizlik: gecici metrics sureci durduruluyor (PID $($metricsProc.Id)) ==="
    Stop-Process -Id $metricsProc.Id -Force -ErrorAction SilentlyContinue
}

Write-Output "evidence_dir=$outDir"
exit $exitCode
