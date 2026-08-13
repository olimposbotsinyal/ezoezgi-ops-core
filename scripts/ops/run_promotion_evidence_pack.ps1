<#
Promosyon-aday kanit runpack'i (v1.2 pilot) -- gorev tanimindaki
"Evidence runpack script (repeatable)" gereksinimini karsilar.

Ne yapar (hepsi TEK bir calistirmada, sirayla, TEKRAR-URETILEBILIR):
  1) Chain trial (v1.1 vs v1.2) -- salt-okunur (`run_emergency_chain_trial.py`).
  2) Legitimacy on-kontrolu, provider=mock (sentetik, sir gerektirmez).
  3) Legitimacy on-kontrolu, provider=jira -- YALNIZCA JIRA_BASE_URL/
     JIRA_EMAIL/JIRA_API_TOKEN ortam degiskenleri TANIMLIYSA GERCEK bir
     kontrol dener; degilse `check_emergency_legitimacy.py`'nin KENDI
     ic mantigi ACIKCA SKIPPED doner (fabrike bir sonuc ASLA uretilmez
     -- bkz. `emergency_legitimacy_core.py` "no implicit pass on
     unchecked provider"). `-SkipJira` verilirse bu adim hic
     CALISTIRILMAZ (ornegin CI/test determinizmi icin).
  4) FPR ozeti (`compute_pilot_false_positive_rate.py`).
  5) Haftalik gozden gecirme JSON export'u (`export_weekly_observability_json.py`
     -- hic `review.md` yoksa sessizce "islenecek bir sey yok" doner,
     BU BIR HATA DEGILDIR).
  6) Degerlendirici (`evaluate_pilot_promotion.py`, NORMAL mod --
     `promotion_report.md`/`.json` yazar; 1-5. adimlarin URETTIGI TAZE
     kaniti da otomatik olarak icerir).
  7) Tum adim sonuclarinin BIRLESTIRILMESI -- `build_promotion_runpack_index.py`
     (`promotion_evidence_pack_core.py`'nin saf mantigini kullanir)
     `reports/promotion_candidate_<UTC>/runpack_index.json`+`runpack_summary.md`
     yazar.

**auto-rollback OFF/ON adimi (7. adimin bir parcasi) GOZLEMSELDIR** --
gorev metni "execute verify-fail scenarios (auto-rollback OFF/ON)"
diyor, ama bu script KASITLI olarak GERCEK bir VerifyReload FAIL/auto-
rollback senaryosunu KENDISI TETIKLEMEZ (bu, gercek Alertmanager/
promtool + onayli bir proposal/review gerektiren, potansiyel olarak
DURUM-DEGISTIREN bir islemdir -- "Default runtime behavior remains
conservative" gorev kisitiyla CELISIR). Bunun yerine, REPO icinde
ONCEDEN var olan `apply_report.json` kanitlarini tarayip
`auto_rollback.triggered=true` (ON) vs `false`/yok (OFF) SAYIMINI
raporlar -- GERCEK bir senaryo calistirmak icin bkz.
docs/ops/MONITORING_STACK_RUNBOOK.md "How to run v1.2 trials safely"
(elle, `apply_threshold_proposal.ps1 -AutoRollbackOnVerifyFail`).

Hicbir adim `-Apply`/durum-degistiren bir islem CALISTIRMAZ -- bu paket
TAMAMEN salt-okunur/kanit-toplayicidir. `pilot_flags_state.json`'a ASLA
YAZMAZ. Bir adim BASARISIZ olursa (ornegin exit code != 0), PAKETIN
TAMAMI durmaz -- diger adimlar yine de calisir (kismi kanit, hicbir
kanit olmamasindan DAHA DEGERLIDIR); nihai script exit code'u
degerlendiricinin (`evaluate_pilot_promotion.py`) exit code'unu
yansitir (0=hepsi PROMOTE, 1=en az biri EXTEND_PILOT, 2=herhangi biri
REJECT VEYA degerlendirici hic calisamadiysa).
#>

param(
    [string]$PythonExe = "d:\Projects\ezoezgi-ops\.venv\Scripts\python.exe",
    [string]$RepoRoot = "d:\Projects\ezoezgi-ops",
    [string]$IncidentId = "OPS-9001",
    [switch]$SkipJira
)

$ErrorActionPreference = "Continue"

$ts = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$outDir = Join-Path $RepoRoot "reports\promotion_candidate_$ts"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$stepResults = [System.Collections.ArrayList]@()

function Invoke-PackStep {
    # NOT: PowerShell'de bir fonksiyonun DONUS DEGERI, `return`
    # ifadesindeki degerle SINIRLI DEGILDIR -- yakalanmamis/yonlendirilmemis
    # HERHANGI bir ciktinin TAMAMI (ornegin bir `Write-Output` cagrisi)
    # fonksiyonun cikti akisina KARISIR VE cagiranin `(Invoke-PackStep ...)`
    # ile YAKALADIGI degerin bir PARCASI olur -- boylece tek bir
    # [ordered]@{} yerine [string, string, ..., [ordered]@{}] SEKLINDE
    # KARISIK bir DIZI donerdi (gercek bir calistirmada KEsFEDILEN ve
    # `ConvertTo-Json` sonrasi Python tarafinda "list indices must be
    # integers, not str" hatasina yol acan GERCEK bir hataydi). Bu yuzden
    # ilerleme mesajlari `Write-Host` (konsola DOGRUDAN yazar, cikti
    # akisina KARISMAZ) ile yazdirilir -- YALNIZCA `return [ordered]@{...}`
    # fonksiyonun GERCEK cikti akisidir.
    param([string]$Name, [string[]]$ScriptArgs)
    Write-Host "=== $Name ==="
    $stepOutput = & $PythonExe @ScriptArgs 2>&1
    $stepOutput | ForEach-Object { Write-Host $_ }
    $code = $LASTEXITCODE
    $evidenceDir = $null
    foreach ($line in $stepOutput) {
        if ("$line" -match '^evidence_dir=(.+)$') { $evidenceDir = $Matches[1].Trim() }
    }
    $status = if ($code -eq 0) { "OK" } elseif ($code -eq 1) { "PARTIAL" } else { "FAIL" }
    return [ordered]@{ step = $Name; exit_code = $code; status = $status; evidence_path = $evidenceDir }
}

[void]$stepResults.Add((Invoke-PackStep -Name "chain_trial" -ScriptArgs @(
    "$RepoRoot\scripts\ops\run_emergency_chain_trial.py", "--repo-root", $RepoRoot
)))

[void]$stepResults.Add((Invoke-PackStep -Name "legitimacy_mock" -ScriptArgs @(
    "$RepoRoot\scripts\ops\check_emergency_legitimacy.py", "--repo-root", $RepoRoot,
    "--incident-id", $IncidentId, "--provider", "mock"
)))

if ($SkipJira) {
    Write-Output "=== legitimacy_jira === ATLANDI (-SkipJira bayragi verildi -- gercek ag denenmedi)"
    [void]$stepResults.Add([ordered]@{ step = "legitimacy_jira"; exit_code = $null; status = "SKIPPED_BY_FLAG"; evidence_path = $null })
} else {
    [void]$stepResults.Add((Invoke-PackStep -Name "legitimacy_jira" -ScriptArgs @(
        "$RepoRoot\scripts\ops\check_emergency_legitimacy.py", "--repo-root", $RepoRoot,
        "--incident-id", $IncidentId, "--provider", "jira"
    )))
}

[void]$stepResults.Add((Invoke-PackStep -Name "fpr_summary" -ScriptArgs @(
    "$RepoRoot\scripts\ops\compute_pilot_false_positive_rate.py", "--repo-root", $RepoRoot
)))

[void]$stepResults.Add((Invoke-PackStep -Name "weekly_review_json" -ScriptArgs @(
    "$RepoRoot\scripts\ops\export_weekly_observability_json.py", "--repo-root", $RepoRoot
)))

[void]$stepResults.Add((Invoke-PackStep -Name "evaluator" -ScriptArgs @(
    "$RepoRoot\scripts\ops\evaluate_pilot_promotion.py", "--repo-root", $RepoRoot
)))

$evaluatorStep = $stepResults | Where-Object { $_.step -eq "evaluator" } | Select-Object -First 1
$evaluatorExitCode = $evaluatorStep.exit_code

$stepsJsonPath = Join-Path $outDir "_step_results.json"
(ConvertTo-Json -InputObject $stepResults -Depth 5) | Set-Content -Path $stepsJsonPath -Encoding utf8

Write-Output "=== bundle (runpack_index.json + runpack_summary.md) ==="
$bundleArgs = @(
    "$RepoRoot\scripts\ops\build_promotion_runpack_index.py",
    "--repo-root", $RepoRoot, "--steps-json-path", $stepsJsonPath,
    "--incident-id", $IncidentId, "--output-dir", $outDir
)
if ($null -ne $evaluatorExitCode) {
    $bundleArgs += @("--evaluator-exit-code", $evaluatorExitCode)
}
& $PythonExe @bundleArgs
$bundlerExitCode = $LASTEXITCODE

Remove-Item -Path $stepsJsonPath -Force -ErrorAction SilentlyContinue

Write-Output "evidence_dir=$outDir"

if ($bundlerExitCode -ne 0) {
    exit $bundlerExitCode
}
if ($null -eq $evaluatorExitCode) {
    exit 2
}
exit $evaluatorExitCode
