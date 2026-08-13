<#
Go-live gate orkestrasyonu -- Model Gateway Observability (Gate A-E).

**ONEMLI:** Bu script kendi, GECICI `serve_metrics.py` surecini
`--enable-debug-injection` ILE, `METRICS_SINK=in_memory` ile baslatir
(bkz. `verify_alert_pipeline.ps1` ile AYNI izolasyon ilkesi) -- gercek
`data/metrics/*.jsonl` dosyasina sentetik test verisi YAZMAZ, gercek
uretim surecini etkilemez.

Gate'ler (gorev tanimindan):
  A) /metrics kullanilabilirligi -- bu makinede varsayilan olarak
     SIMULASYON modu (kisa ornek pencere, GERCEK 24 saatlik gozlem
     DEGIL -- bkz. -Real24h anahtari, yalnizca ETIKETLEME icin, gercek
     bir 24 saatlik kosu bu script'in tek calistirmasinda YAPILAMAZ).
  B) Scrape basari orani >= %99 -- Prometheus bu makinede kurulu
     olmadigindan, /metrics endpoint'inin kendisine tekrar istek
     PROXY'si olarak olculur (gercek Prometheus `up{}` degil).
  C) 4 sentetik alert modunun (fallback-spike, null-intent-spike,
     preflight-unknown, circuit-open-stuck) /metrics ciktisinda
     gorunurlugu.
  D) Alertmanager alma yolu -- bu makinede Alertmanager KURULU
     DEGILSE durustce SKIPPED doner (bkz. docs/ops/MONITORING_STACK_RUNBOOK.md
     "Kurulum").
  E) classify regresyon smoke -- ilgili pytest dosyalari.

Cikis kodu: 0 (tum gate'ler PASS), 1 (en az biri PARTIAL/SKIPPED, hicbiri
FAIL degil), 2 (en az biri FAIL). ASLA fabrika edilmis 0 donmez.
#>

param(
    [string]$PythonExe = "d:\Projects\ezoezgi-ops\.venv\Scripts\python.exe",
    [string]$RepoRoot = "d:\Projects\ezoezgi-ops",
    [int]$MetricsPort = 9109,
    [int]$AlertmanagerPort = 9093,
    [int]$AvailabilitySamples = 10,
    [double]$AvailabilityIntervalSec = 0.2,
    [int]$ScrapeSuccessSamples = 20,
    [int]$SyntheticCount = 5,
    [switch]$Real24h
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "$RepoRoot\services\model-gateway\src;$RepoRoot\services\tr-en-bridge\src;$RepoRoot\apps\orchestrator\src;$RepoRoot\scripts\ops"
$env:METRICS_SINK = "in_memory"  # gecici gate sureci -- gercek data/metrics/*.jsonl'e YAZMAZ

$ts = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$outDir = Join-Path $RepoRoot "reports\go_live_gates_$ts"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$mode = if ($Real24h) { "real_24h_claimed" } else { "simulation" }

Write-Output "=== Adim 1: gecici metrics+injection sureci baslatiliyor (izole, METRICS_SINK=in_memory) ==="
$metricsProc = Start-Process -FilePath $PythonExe `
    -ArgumentList "scripts\ops\serve_metrics.py", "--port", "$MetricsPort", "--enable-debug-injection" `
    -WorkingDirectory $RepoRoot `
    -RedirectStandardOutput "$outDir\serve_metrics_stdout.log" `
    -RedirectStandardError "$outDir\serve_metrics_stderr.log" `
    -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 2
Write-Output "OK: PID $($metricsProc.Id)"

try {
    Write-Output "=== Gate A: /metrics kullanilabilirligi ($AvailabilitySamples ornek, mod=$mode) ==="
    $gateASuccess = 0
    for ($i = 0; $i -lt $AvailabilitySamples; $i++) {
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$MetricsPort/metrics" -TimeoutSec 5
            if ($resp.StatusCode -eq 200) { $gateASuccess++ }
        } catch {
            Write-Output "  ornek $i basarisiz: $_"
        }
        if ($i -lt $AvailabilitySamples - 1) { Start-Sleep -Seconds $AvailabilityIntervalSec }
    }
    Write-Output "Gate A: $gateASuccess/$AvailabilitySamples basarili"

    Write-Output "=== Gate B: scrape basari orani ($ScrapeSuccessSamples ornek) ==="
    $gateBSuccess = 0
    for ($i = 0; $i -lt $ScrapeSuccessSamples; $i++) {
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$MetricsPort/metrics" -TimeoutSec 5
            if ($resp.StatusCode -eq 200) { $gateBSuccess++ }
        } catch {
            Write-Output "  ornek $i basarisiz: $_"
        }
    }
    Write-Output "Gate B: $gateBSuccess/$ScrapeSuccessSamples basarili"

    Write-Output "=== Gate C: 4 sentetik alert modu enjekte ediliyor + gorunurluk kontrol ediliyor ==="
    $modes = @("fallback-spike", "null-intent-spike", "preflight-unknown", "circuit-open-stuck")
    $modeVisibility = @{}
    foreach ($m in $modes) {
        try {
            $body = @{ mode = $m; count = $SyntheticCount } | ConvertTo-Json -Compress
            $injectResp = Invoke-WebRequest -Uri "http://127.0.0.1:$MetricsPort/debug/inject_synthetic" `
                -Method POST -Body $body -ContentType "application/json" -TimeoutSec 5
            $injectData = $injectResp.Content | ConvertFrom-Json
            $metricName = $injectData.metric

            $metricsResp = Invoke-WebRequest -Uri "http://127.0.0.1:$MetricsPort/metrics" -TimeoutSec 5
            $visible = $metricsResp.Content -match [regex]::Escape($metricName)
            $modeVisibility[$m] = [bool]$visible
            Write-Output "  $m -> metric=$metricName gorunur=$visible"
        } catch {
            Write-Output "  $m basarisiz: $_"
            $modeVisibility[$m] = $false
        }
    }
    $modeVisibilityJson = $modeVisibility | ConvertTo-Json -Compress
    Set-Content -Path "$outDir\gate_c_mode_visibility.json" -Value $modeVisibilityJson -Encoding utf8

    Write-Output "=== Gate D: Alertmanager alma yolu kontrol ediliyor ==="
    # ONEMLI: erisilebilirlik (API yaniti) BIRINCIL sinyaldir -- Get-Command
    # PATH tabanlidir ve yalnizca sistem PATH'ine eklenmis bir kurulumu
    # bulur; gercekte calisan bir Alertmanager surecinin (ornegin elle
    # indirilip PATH DISINDA bir yoldan baslatilmis) API'si erisilebilirse
    # bu, "kurulu ve calisiyor" icin daha DOGRUDAN/GUVENILIR bir kanittir.
    # PATH kontrolu yalnizca API erisilemedigi durumda "hic kurulu degil"
    # (SKIPPED) ile "kurulu ama şu an calismiyor/erisilemez" (FAIL) ayrimini
    # yapmak icin ikincil bir sinyal olarak kullanilir.
    $amReachable = $false
    $amAlertReceived = $false
    $amAlertNames = @()
    try {
        $amResp = Invoke-WebRequest -Uri "http://127.0.0.1:$AlertmanagerPort/api/v2/alerts" -TimeoutSec 5
        $amReachable = $true
        $amAlerts = $amResp.Content | ConvertFrom-Json
        if ($amAlerts) {
            $amAlertNames = @($amAlerts | ForEach-Object { $_.labels.alertname })
        }
        $amAlertReceived = ($amAlertNames.Count -gt 0)
    } catch {
        Write-Output "  Alertmanager API'sine erisilemedi: $_"
    }

    $amInstalled = $amReachable -or [bool](Get-Command alertmanager.exe -ErrorAction SilentlyContinue)
    if (-not $amReachable -and -not $amInstalled) {
        Write-Output "  Alertmanager bu makinede kurulu degil (beklenen durum, bkz. MONITORING_STACK_RUNBOOK.md)"
    } elseif ($amReachable) {
        Write-Output "  Alertmanager erisilebilir -- alinan alert'ler: $($amAlertNames -join ', ')"
    }
    Write-Output "Gate D: installed=$amInstalled reachable=$amReachable alert_received=$amAlertReceived alerts=[$($amAlertNames -join ', ')]"

    Write-Output "=== Gate E: classify regresyon smoke (ilgili pytest dosyalari) ==="
    $gateETestFiles = @(
        "tests\test_ollama_nlu.py",
        "tests\test_model_client.py",
        "tests\test_nlu_provider_flag.py",
        "tests\test_model_gateway_router.py",
        "tests\test_model_gateway_runtime_verify.py"
    )
    # ON-KONTROL (bu gorevden bagimsiz, onceden var olan bir ortam notu):
    # `tools/cli-runner/src/runner.py`, `shutil.which("echo")` ile calisir.
    # Git Bash'in PATH'inde gercek bir echo.exe bulunur, ama SAF bir
    # PowerShell surecinin PATH'inde `echo` yalnizca bir dil anahtar
    # kelimesi/alias'tir, gercek bir yurutulebilir DEGILDIR -- bu durumda
    # `test_nlu_provider_flag.py`'nin orchestrator-smoke alt-testleri
    # `EXECUTABLE_NOT_FOUND` ile basarisiz olabilir. Bu, classify/fallback
    # SOZLESMESINDE bir regresyon DEGILDIR -- echo aracinin PATH
    # cozumlemesiyle ilgili, bu gorevin kapsami DISINDA bir sinirlamadir
    # (bkz. docs/BACKLOG.md). Burada SESSIZCE gizlenmez -- yalnizca
    # tanilama notuyla ZENGINLESTIRILIR; gate durumu yine de GERCEK pytest
    # exit code'una gore belirlenir, fabrike edilmis bir PASS asla uretilmez.
    $realEchoFound = [bool](Get-Command echo -CommandType Application -ErrorAction SilentlyContinue)
    if (-not $realEchoFound) {
        Write-Output "  ON-KONTROL: bu PowerShell surecinin PATH'inde gercek bir 'echo' yurutulebilir dosyasi YOK -- test_nlu_provider_flag.py'nin orchestrator-smoke alt-testleri EXECUTABLE_NOT_FOUND ile basarisiz olabilir (bkz. asagidaki tani notu)."
    }

    Push-Location $RepoRoot
    $pytestOutput = & $PythonExe -m pytest $gateETestFiles 2>&1
    $gateEExitCode = $LASTEXITCODE
    Pop-Location
    $gateESummary = ($pytestOutput | Select-Object -Last 1) -join " "

    if ($gateEExitCode -ne 0 -and -not $realEchoFound) {
        $gateESummary += " [TANI: bu surecin PATH'inde gercek 'echo' yurutulebilir dosyasi bulunamadi -- basarisizlik(lar) muhtemelen echo_runner.py PATH cozumlemesinden kaynaklaniyor, classify/fallback sozlesmesiyle ILGISIZ, onceden var olan bir ortam sinirlamasi (bkz. docs/BACKLOG.md); yine de gate durumu GERCEK sonuca gore FAIL kaliyor]"
    }

    Write-Output "Gate E: exit_code=$gateEExitCode summary=$gateESummary"
    Set-Content -Path "$outDir\gate_e_pytest_output.log" -Value ($pytestOutput -join "`n") -Encoding utf8

    Write-Output "=== Karar hesaplaniyor + rapor yaziliyor ==="
    $driverScript = @"
import sys, json
sys.path.insert(0, r'$RepoRoot\scripts\ops')
from observability_gates_core import (
    evaluate_gate_a_metrics_availability, evaluate_gate_b_scrape_success,
    evaluate_gate_c_synthetic_alerts, evaluate_gate_d_alertmanager_receive,
    evaluate_gate_e_classify_regression, overall_exit_code, overall_status,
    render_gate_report_md, write_gate_results_json,
)
from pathlib import Path
from datetime import datetime, timezone

with open(r'$outDir\gate_c_mode_visibility.json', encoding='utf-8-sig') as f:
    mode_visibility = json.load(f)

results = [
    evaluate_gate_a_metrics_availability(
        $gateASuccess, $AvailabilitySamples,
        simulated=$(if ($Real24h) { "False" } else { "True" }),
        window_label="$AvailabilitySamples ornek x ${AvailabilityIntervalSec}s",
    ),
    evaluate_gate_b_scrape_success($gateBSuccess, $ScrapeSuccessSamples, simulated=True),
    evaluate_gate_c_synthetic_alerts(mode_visibility),
    evaluate_gate_d_alertmanager_receive(
        alertmanager_installed=$($amInstalled.ToString()),
        alertmanager_reachable=$($amReachable.ToString()),
        alert_received=$($amAlertReceived.ToString()),
    ),
    evaluate_gate_e_classify_regression($gateEExitCode, r'''$gateESummary'''),
]

generated_at = datetime.now(timezone.utc).isoformat()
out_dir = Path(r'$outDir')
report_md = render_gate_report_md(results, generated_at=generated_at, mode='$mode')
(out_dir / 'gate_report.md').write_text(report_md, encoding='utf-8')
write_gate_results_json(results, out_dir / 'gate_results.json', generated_at=generated_at, mode='$mode')

print(report_md)
print(f'evidence_dir={out_dir}')
sys.exit(overall_exit_code(results))
"@
    $driverPath = Join-Path $env:TEMP "run_observability_gates_driver_$ts.py"
    Set-Content -Path $driverPath -Value $driverScript -Encoding utf8
    & $PythonExe $driverPath
    $exitCode = $LASTEXITCODE
    Remove-Item -Path $driverPath -Force -ErrorAction SilentlyContinue
} finally {
    Write-Output "=== Temizlik: gecici metrics sureci durduruluyor (PID $($metricsProc.Id)) ==="
    Stop-Process -Id $metricsProc.Id -Force -ErrorAction SilentlyContinue
}

exit $exitCode
