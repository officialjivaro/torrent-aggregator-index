# Daily scheduled entry point: one logged, serialized run of update-index.ps1.
# Publication still goes through every check in update-index.ps1, including the publish gate.
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$logDir = Join-Path $PSScriptRoot "artifacts\scheduled"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot "staging") | Out-Null
$log = Join-Path $logDir ("update-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmm"))

# Publishers must never run concurrently; an exclusive handle on the lock file enforces that.
$lockPath = Join-Path $PSScriptRoot "staging\update.lock"
try {
    $lock = [System.IO.File]::Open($lockPath, "OpenOrCreate", "ReadWrite", "None")
} catch {
    "Another index update is already running; skipped at $(Get-Date -Format o)." | Out-File -FilePath $log -Encoding utf8
    exit 0
}

try {
    "Scheduled index update started $(Get-Date -Format o)" | Out-File -FilePath $log -Encoding utf8
    # Redirect at the OS level through cmd: Windows PowerShell 5.1 turns redirected native
    # stderr (Python warnings and logged tracebacks) into terminating errors.
    $updater = Join-Path $PSScriptRoot "update-index.ps1"
    cmd.exe /d /c "powershell.exe -NoProfile -File `"$updater`" >> `"$log`" 2>&1"
    $code = $LASTEXITCODE
    "Scheduled index update finished with exit code $code at $(Get-Date -Format o)" | Out-File -FilePath $log -Append -Encoding utf8
    if ($code -ne 0) { exit $code }
} finally {
    $lock.Dispose()
    Get-ChildItem -LiteralPath $logDir -Filter "update-*.log" | Sort-Object Name -Descending | Select-Object -Skip 30 | Remove-Item -Force
}
