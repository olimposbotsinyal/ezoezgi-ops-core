<#
Model gateway izleme yigini durdurucu -- start_monitoring_stack.ps1'in
yazdigi reports/monitoring_stack/pids.json'daki tum surecleri durdurur.
#>

param(
    [string]$RepoRoot = "d:\Projects\ezoezgi-ops"
)

$pidsFile = Join-Path $RepoRoot "reports\monitoring_stack\pids.json"

if (-not (Test-Path $pidsFile)) {
    Write-Output "NOT_RUNNING: $pidsFile bulunamadi -- calisan bir yigin kaydi yok"
    exit 0
}

$pids = Get-Content $pidsFile -Raw | ConvertFrom-Json

foreach ($name in $pids.PSObject.Properties.Name) {
    $processId = $pids.$name
    try {
        Stop-Process -Id $processId -Force -ErrorAction Stop
        Write-Output "OK: $name durduruldu (PID $processId)"
    } catch {
        Write-Output "SKIPPED: $name (PID $processId) zaten calismiyordu"
    }
}

Remove-Item -Path $pidsFile -Force -ErrorAction SilentlyContinue
Write-Output "Tum izleme yigini surecleri durduruldu."
