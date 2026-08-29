[CmdletBinding()]
param(
    [int]$MaxChunks = -1,
    [string]$Archive,
    [switch]$NoDownload
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if (-not $launcher) { throw "Python 3.12+ is required." }
    & $launcher.Source -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Unable to create .venv." }
}

& $python -c "import requests, yaml" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
}

$arguments = @("scripts/import_history.py")
if ($MaxChunks -ge 0) { $arguments += @("--max-chunks", $MaxChunks) }
if ($Archive) { $arguments += @("--archive", $Archive) }
if ($NoDownload) { $arguments += "--no-download" }
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw "Historical import stopped. It is resumable; run the same command again after resolving the reported error." }

Write-Host "Historical import complete. Run .\update-index.cmd to refresh backups and publish the v4 index."
