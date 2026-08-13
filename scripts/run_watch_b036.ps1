<#
B036 upstream watch kontrolunu calistiran ince PowerShell sarmalayicisi.

scripts/watch_b036_upstream.py'yi cagirir, stdout'u zaman damgali bir log
dosyasina kaydeder. Hicbir runtime deneyi calistirmaz -- yalnizca Python
script'ini invoke eder.

Kullanim:
    powershell -ExecutionPolicy Bypass -File scripts\run_watch_b036.ps1

Cikis kodu:
    0 -> kontrol tamamlandi (NONE/DIAGNOSTIC_REQUEST/PATCH_REFERENCE/
         NEW_RELEASE_HINT hepsi bu kategoride, "hata" degil) VEYA
         CHECK_FAILED_NETWORK (gecici ag hatasi, sert hata degil)
    1  -> sert hata (Python calistirilamadi, dosya sistemi hatasi vb.)
#>

param(
    [string]$PythonExe = "d:\Projects\ezoezgi-ops\.venv\Scripts\python.exe",
    [string]$Script = "scripts\watch_b036_upstream.py",
    [string]$OutDir = "reports\runtime_incident_20260813T004855Z\watch_runs"
)

$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$ts = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$logPath = Join-Path $OutDir "watch_$ts.log"

try {
    $output = & $PythonExe $Script 2>&1
    $exitCode = $LASTEXITCODE
} catch {
    "HARD_FAILURE: $($_.Exception.Message)" | Out-File -FilePath $logPath -Encoding utf8
    Write-Output "HARD_FAILURE: $($_.Exception.Message)"
    exit 1
}

$output | Out-File -FilePath $logPath -Encoding utf8
$output | ForEach-Object { Write-Output $_ }

Write-Output ""
Write-Output "Log kaydedildi: $logPath"

if ($exitCode -eq 0) {
    exit 0
} else {
    # watch_b036_upstream.py hard failure disinda hep 0 dondurur;
    # 0 disi bir kod gercekten beklenmedik bir sert hata anlamina gelir.
    Write-Output "HARD_FAILURE: python script beklenmedik exit code dondurdu ($exitCode)"
    exit 1
}
