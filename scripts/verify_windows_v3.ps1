# IND3 V3 Windows host verification. Run in PowerShell from repository root.
# Example:
# powershell -ExecutionPolicy Bypass -File scripts/verify_windows_v3.ps1 -CetarisFile 'C:\\Temp\\trial(1).xlsx' -DatabaseFile '.\\passdown.db'
param(
    [string]$CetarisFile = "",
    [string]$DatabaseFile = "",
    [string]$RosterJson = "",
    [string]$BaseUrl = "http://127.0.0.1:8765"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    Write-Host "Checking V3-only Python files..."
    python -m compileall -q main.py v3_routes.py v3_identity.py v3_cetaris.py v3_breakdown.py v3_split.py v3_only.py scripts/v3_acceptance.py
    if ($LASTEXITCODE -ne 0) { throw "Python syntax verification failed." }
    Write-Host "Running V3 regression tests..."
    python -m pytest -q tests/test_v3_migration.py tests/test_v3_operational_patch.py tests/test_v3_all_non_pm.py tests/test_v3_split.py tests/test_v3_only.py tests/test_v3_routes.py
    if ($LASTEXITCODE -ne 0) { throw "V3 regression tests failed." }

    if ($DatabaseFile) {
        if (-not (Test-Path -LiteralPath $DatabaseFile)) { throw "Database path does not exist: $DatabaseFile" }
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $backup = "$DatabaseFile.pre-v3-$stamp.bak"
        # SQLite online backup is consistent even if the V3 service is running.\n        python -c "import sqlite3,sys; src=sqlite3.connect(sys.argv[1]); dst=sqlite3.connect(sys.argv[2]); src.backup(dst); dst.close(); src.close()" $DatabaseFile $backup\n        if ($LASTEXITCODE -ne 0) { throw "SQLite backup failed." }
        Write-Host "Database backup created: $backup"
    }

    if ($CetarisFile) {
        if (-not (Test-Path -LiteralPath $CetarisFile)) { throw "Workbook not found: $CetarisFile" }
        $arguments = @("scripts/v3_acceptance.py", $CetarisFile)
        if ($RosterJson) { $arguments += @("--roster-json", $RosterJson) }
        $arguments += @("--output", "v3_acceptance_results.json")
        python @arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Real-file attribution has unresolved ownership or duplicate WOs; inspect v3_acceptance_results.json."
        }
    }

    Write-Host "Checking local V3 server at $BaseUrl..."
    $response = Invoke-WebRequest -Uri "$BaseUrl/v3/login" -UseBasicParsing -MaximumRedirection 5 -TimeoutSec 10
    if ($response.StatusCode -ne 200) { throw "V3 login did not return HTTP 200." }
    $legacy = Invoke-WebRequest -Uri "$BaseUrl/login" -UseBasicParsing -MaximumRedirection 5 -TimeoutSec 10
    if ($legacy.BaseResponse.ResponseUri -and $legacy.BaseResponse.ResponseUri.AbsolutePath -notlike "/v3/*") {
        throw "Legacy login did not redirect to V3."
    }
    Write-Host "PASS: tests, local V3 login, and legacy-route redirect verified."
    Write-Host "MANUAL SIGN-OFF STILL REQUIRED: authenticated login, Cetaris upload, PM/happenings, OOS/RTS, report preview, and LAN laptop access."
}
finally { Pop-Location }
