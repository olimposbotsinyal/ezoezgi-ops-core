<#
B036 - Post-upstream stabilizasyon deney partisi (Experiment Batch #1).

Test matrisindeki A-E senaryolarini calistirir, her cagriyi
results.jsonl'e append eder, sunucu loglarini raw_logs/ altina yazar.

Kullanim:
    powershell -ExecutionPolicy Bypass -File scripts\repro_b036_batch.ps1

Var sayilan senaryolar (bkz. exp_01_post_upstream/test_matrix.md):
    A) Vulkan ON, baseline
    B) Vulkan OFF (CPU-only kontrol)
    C) Vulkan ON + acik OLLAMA_NUM_PARALLEL=1 (varsayilanla ayni davranis beklenir)
    D) Vulkan ON + azaltilmis context (num_ctx=256)
    E) Vulkan ON + her cagri oncesi temiz sunucu yeniden baslatma

Mevcut sürücü/donanım degismedigi icin bu script host_fingerprint.md'yi
tekrar toplamaz; yalnizca ollama --version + ilgili env degiskenlerini
env_snapshot.txt'e yazar.
#>

param(
    [string]$OutDir = "reports\runtime_incident_20260813T004855Z\exp_01_post_upstream",
    [string]$OllamaExe = "C:\Users\Serkan\AppData\Local\Programs\Ollama\ollama.exe",
    [string]$Model = "qwen2.5:3b-instruct",
    [int]$RunsPerScenario = 20
)

$ErrorActionPreference = "Stop"
$ResultsPath = Join-Path $OutDir "results.jsonl"
$RawLogsDir = Join-Path $OutDir "raw_logs"
New-Item -ItemType Directory -Force -Path $RawLogsDir | Out-Null

function Write-JsonlResult {
    param($Obj)
    $json = $Obj | ConvertTo-Json -Compress -Depth 5
    $noBomUtf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::AppendAllText($ResultsPath, $json + "`n", $noBomUtf8)
}

function Start-OllamaServer {
    param([hashtable]$EnvVars, [string]$LogPrefix)
    foreach ($k in @("OLLAMA_VULKAN", "OLLAMA_LLM_LIBRARY", "OLLAMA_NUM_GPU", "OLLAMA_NUM_PARALLEL")) {
        Remove-Item "Env:\$k" -ErrorAction SilentlyContinue
    }
    foreach ($k in $EnvVars.Keys) {
        Set-Item -Path "Env:\$k" -Value $EnvVars[$k]
    }
    $stdout = Join-Path $RawLogsDir "$LogPrefix.stdout.log"
    $stderr = Join-Path $RawLogsDir "$LogPrefix.stderr.log"
    $p = Start-Process -FilePath $OllamaExe -ArgumentList "serve" -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 4
    return $p
}

function Stop-OllamaServer {
    param($Proc)
    if ($Proc -and -not $Proc.HasExited) {
        Stop-Process -Id $Proc.Id -Force -ErrorAction SilentlyContinue
    }
    foreach ($k in @("OLLAMA_VULKAN", "OLLAMA_LLM_LIBRARY", "OLLAMA_NUM_GPU", "OLLAMA_NUM_PARALLEL")) {
        Remove-Item "Env:\$k" -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}

function Invoke-OneGenerate {
    param([string]$ModelName, [string]$Prompt, $Options)
    $bodyObj = @{ model = $ModelName; prompt = $Prompt; stream = $false }
    if ($Options) { $bodyObj["options"] = $Options }
    $body = $bodyObj | ConvertTo-Json -Compress
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $statusCode = $null
    $respBody = $null
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/generate" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 90
        $statusCode = [int]$resp.StatusCode
        $respBody = $resp.Content
    } catch {
        if ($_.Exception.Response) { $statusCode = [int]$_.Exception.Response.StatusCode.value__ } else { $statusCode = -1 }
        if ($_.ErrorDetails.Message) { $respBody = $_.ErrorDetails.Message } else { $respBody = $_.Exception.Message }
    }
    $sw.Stop()
    $crash = $false
    if ($respBody -match "0xc0000005" -or $respBody -match "process has terminated") { $crash = $true }
    return [PSCustomObject]@{
        http_status      = $statusCode
        latency_seconds  = [math]::Round($sw.Elapsed.TotalSeconds, 3)
        crash_signature  = $crash
        response_snippet = if ($respBody.Length -gt 220) { $respBody.Substring(0, 220) } else { $respBody }
    }
}

function Run-Scenario {
    param(
        [string]$Id,
        [hashtable]$EnvVars,
        [bool]$RestartPerRun,
        [string]$ModelName,
        $Options,
        [int]$Runs
    )
    Write-Output "=== Senaryo $Id baslatiliyor (restart_per_run=$RestartPerRun, runs=$Runs) ==="
    $proc = $null
    if (-not $RestartPerRun) {
        $proc = Start-OllamaServer -EnvVars $EnvVars -LogPrefix "scenario_$Id"
    }
    for ($i = 1; $i -le $Runs; $i++) {
        if ($RestartPerRun) {
            $proc = Start-OllamaServer -EnvVars $EnvVars -LogPrefix "scenario_${Id}_run$i"
        }
        $ts = (Get-Date).ToUniversalTime().ToString("o")
        $result = Invoke-OneGenerate -ModelName $ModelName -Prompt "B036 batch test run $i - merhaba" -Options $Options
        $record = [ordered]@{
            scenario         = $Id
            run_index        = $i
            timestamp_utc    = $ts
            env              = $EnvVars
            model            = $ModelName
            options          = $Options
            http_status      = $result.http_status
            latency_seconds  = $result.latency_seconds
            crash_signature  = $result.crash_signature
            response_snippet = $result.response_snippet
        }
        Write-JsonlResult -Obj $record
        Write-Output "  [$Id run $i/$Runs] HTTP=$($result.http_status) latency=$($result.latency_seconds)s crash=$($result.crash_signature)"
        if ($RestartPerRun) {
            Stop-OllamaServer -Proc $proc
        }
    }
    if (-not $RestartPerRun) {
        Stop-OllamaServer -Proc $proc
    }
    Write-Output "=== Senaryo $Id tamamlandi ==="
}

# --- env_snapshot.txt ---
$snapshotPath = Join-Path $OutDir "env_snapshot.txt"
"Zaman (UTC): $((Get-Date).ToUniversalTime().ToString('o'))" | Out-File $snapshotPath -Encoding utf8
(& $OllamaExe --version) | Out-File $snapshotPath -Append -Encoding utf8
"Not: Donanim/surucu bilgisi degismedi, bkz. host_fingerprint.md (ayni klasorun bir ust seviyesinde)." | Out-File $snapshotPath -Append -Encoding utf8

# --- Senaryolar ---
Run-Scenario -Id "A" -EnvVars @{} -RestartPerRun $false -ModelName $Model -Options $null -Runs $RunsPerScenario
Run-Scenario -Id "B" -EnvVars @{ OLLAMA_VULKAN = "false" } -RestartPerRun $false -ModelName $Model -Options $null -Runs $RunsPerScenario
Run-Scenario -Id "C" -EnvVars @{ OLLAMA_NUM_PARALLEL = "1" } -RestartPerRun $false -ModelName $Model -Options $null -Runs $RunsPerScenario
Run-Scenario -Id "D" -EnvVars @{} -RestartPerRun $false -ModelName $Model -Options @{ num_ctx = 256 } -Runs $RunsPerScenario
Run-Scenario -Id "E" -EnvVars @{} -RestartPerRun $true -ModelName $Model -Options $null -Runs $RunsPerScenario

Write-Output "=== TUM SENARYOLAR TAMAMLANDI ==="
