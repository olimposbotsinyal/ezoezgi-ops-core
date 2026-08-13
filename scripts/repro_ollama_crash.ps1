<#
B036 - Ollama Windows 0xc0000005 crash icin minimal reproduksiyon scripti.

Kullanim:
    powershell -ExecutionPolicy Bypass -File scripts\repro_ollama_crash.ps1
    powershell -ExecutionPolicy Bypass -File scripts\repro_ollama_crash.ps1 -Model llama3 -BaseUrl http://127.0.0.1:11434

On kosul: `ollama serve` zaten calisiyor olmali (bu script kendi baslatmiyor,
gozlemlenebilir ortam degiskenleriyle calistirilmis mevcut sürece karsi test
yapmak icindir).

Cikis kodu:
    0  -> HTTP 200 ve govdede coküs imzasi yok
    1  -> HTTP 500 VE/VEYA govdede 0xc0000005 / crash imzasi bulundu
    2  -> /api/tags'e ulasilamadi (sunucu ayakta degil / baglanti hatasi)
#>

param(
    [string]$BaseUrl = "http://127.0.0.1:11434",
    [string]$Model = "qwen2.5:3b-instruct",
    [string]$Prompt = "Merhaba, bugun nasilsin?"
)

$ErrorActionPreference = "Stop"

Write-Output "=== B036 repro script ==="
Write-Output "Zaman (UTC): $((Get-Date).ToUniversalTime().ToString('o'))"
Write-Output "BaseUrl: $BaseUrl"
Write-Output "Model: $Model"
Write-Output ""

Write-Output "--- Ilgili ortam degiskenleri ---"
$relevantEnvVars = @(
    "OLLAMA_VULKAN",
    "OLLAMA_LLM_LIBRARY",
    "OLLAMA_NUM_GPU",
    "OLLAMA_IGPU_ENABLE",
    "OLLAMA_HOST",
    "OLLAMA_TIMEOUT_SECONDS",
    "GGML_VK_VISIBLE_DEVICES",
    "CUDA_VISIBLE_DEVICES"
)
foreach ($varName in $relevantEnvVars) {
    $val = [Environment]::GetEnvironmentVariable($varName)
    Write-Output "$varName=$val"
}
Write-Output ""

Write-Output "--- GET /api/tags ---"
try {
    $tagsResp = Invoke-WebRequest -Uri "$BaseUrl/api/tags" -Method Get -TimeoutSec 15
    Write-Output "HTTP Status: $($tagsResp.StatusCode)"
    Write-Output "Govde: $($tagsResp.Content)"
} catch {
    Write-Output "HATA: /api/tags'e ulasilamadi - $($_.Exception.Message)"
    Write-Output ""
    Write-Output "=== SONUC: erisilemedi (sunucu ayakta degil olabilir) ==="
    exit 2
}
Write-Output ""

Write-Output "--- POST /api/generate (stream=false) ---"
$body = @{
    model  = $Model
    prompt = $Prompt
    stream = $false
} | ConvertTo-Json -Compress

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$statusCode = $null
$respBody = $null
try {
    $genResp = Invoke-WebRequest -Uri "$BaseUrl/api/generate" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 90
    $statusCode = [int]$genResp.StatusCode
    $respBody = $genResp.Content
} catch {
    if ($_.Exception.Response) {
        $statusCode = [int]$_.Exception.Response.StatusCode.value__
    } else {
        $statusCode = -1
    }
    if ($_.ErrorDetails.Message) {
        $respBody = $_.ErrorDetails.Message
    } else {
        $respBody = $_.Exception.Message
    }
}
$sw.Stop()
$latencySeconds = [math]::Round($sw.Elapsed.TotalSeconds, 3)

Write-Output "HTTP Status: $statusCode"
Write-Output "Gecikme (s): $latencySeconds"
Write-Output "Govde: $respBody"
Write-Output ""

$crashSignatureFound = $false
if ($respBody -match "0xc0000005" -or $respBody -match "process has terminated") {
    $crashSignatureFound = $true
}

$isHttp500 = ($statusCode -eq 500)

Write-Output "--- Sonuc ---"
Write-Output "HTTP 500: $isHttp500"
Write-Output "Cokus imzasi (0xc0000005 / process has terminated): $crashSignatureFound"

if ($isHttp500 -or $crashSignatureFound) {
    Write-Output "=== SONUC: COKUS TESPIT EDILDI (exit 1) ==="
    exit 1
} else {
    Write-Output "=== SONUC: BASARILI, cokus yok (exit 0) ==="
    exit 0
}
