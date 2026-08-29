[CmdletBinding()]
param(
    [switch]$Offline,
    [switch]$SkipTests,
    [switch]$SkipGitHubBackup,
    [switch]$SkipR2,
    [string]$CredentialFile
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

function Import-R2Credentials {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
    $allowed = @("CLOUDFLARE_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
        $separator = $trimmed.IndexOf("=")
        if ($separator -lt 1) { continue }
        $name = $trimmed.Substring(0, $separator).Trim()
        if ($name -notin $allowed) { continue }
        $value = $trimmed.Substring($separator + 1).Trim()
        if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ($value -and -not [Environment]::GetEnvironmentVariable($name, "Process")) {
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

if (-not $SkipR2) {
    if (-not $CredentialFile) {
        $CredentialFile = if ($env:JT_CREDENTIALS_FILE) {
            $env:JT_CREDENTIALS_FILE
        } else {
            Join-Path (Split-Path -Parent $PSScriptRoot) "2_credentials\torrent-aggregator-index.env"
        }
    }
    Import-R2Credentials -Path $CredentialFile
    $missingCredentials = @("CLOUDFLARE_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY") | Where-Object {
        -not [Environment]::GetEnvironmentVariable($_, "Process")
    }
    if ($missingCredentials.Count) {
        throw "Missing R2 credentials: $($missingCredentials -join ', '). Run .\save-r2-credentials.ps1 first or provide -CredentialFile."
    }
}

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if (-not $launcher) { throw "Python 3.12+ is required. Install Python, then run this script again." }
    & $launcher.Source -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Unable to create .venv." }
}

& $python -c "import boto3, bs4, bencodepy, feedparser, requests, yaml" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
}

if (-not $SkipTests) {
    & $python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Local tests failed; nothing was published." }
}

& $python scripts/audit_governance.py --config config/sources.yml --policy config/search-policy.json --output artifacts/backend-governance-static.json
if ($LASTEXITCODE -ne 0) { throw "Source-policy audit failed; nothing was published." }

$buildArguments = @("scripts/build_index.py", "--config", "config/sources.yml", "--policy", "config/search-policy.json")
if ($Offline) { $buildArguments += "--offline" }
& $python @buildArguments
if ($LASTEXITCODE -ne 0) { throw "Index build failed; nothing was published." }

$minimumItems = if ($Offline) { 1 } else { 30000 }
& $python scripts/validate_v4_backend.py --public-dir public --minimum-items $minimumItems --max-bytes 68719476736
if ($LASTEXITCODE -ne 0) { throw "Generated v4 index validation failed; nothing was published." }
if ($Offline) {
    & $python scripts/verify_final_phase.py --output artifacts/backend-final-verification.json
} else {
    & $python scripts/verify_final_phase.py --generated-dir public --output artifacts/backend-final-verification.json
}
if ($LASTEXITCODE -ne 0) { throw "Final governance verification failed; nothing was published." }

& $python scripts/publish_release.py prepare --public-dir public --backup-dir backups --manifest staging/release.json
if ($LASTEXITCODE -ne 0) { throw "Local backup preparation failed; nothing was published." }
$release = Get-Content -LiteralPath staging/release.json -Raw | ConvertFrom-Json

if (-not $SkipGitHubBackup) {
    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $gh) { throw "GitHub CLI is required for release backups. Install gh or rerun with -SkipGitHubBackup." }
    & $gh.Source auth status
    if ($LASTEXITCODE -ne 0) { throw "GitHub CLI is not authenticated in this Windows session. Run: gh auth login --hostname github.com --git-protocol https --web" }
    $repo = "officialjivaro/torrent-aggregator-index"
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # A missing release is expected on the first publish. Windows PowerShell 5.1
        # otherwise promotes gh's stderr to a terminating NativeCommandError.
        $ErrorActionPreference = "Continue"
        $releaseLookupOutput = @(& $gh.Source release view $release.tag --repo $repo --json tagName 2>&1)
        $releaseLookupExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    $releaseLookupMessage = ($releaseLookupOutput | Out-String).Trim()
    if ($releaseLookupExitCode -eq 0) {
        $assetPaths = @($release.assets | ForEach-Object { $_.path })
        & $gh.Source release upload $release.tag @assetPaths --repo $repo --clobber
    } elseif ($releaseLookupMessage -match "(?i)release not found") {
        $assetPaths = @($release.assets | ForEach-Object { $_.path })
        $notes = "Static JTorrent index backup. Release $($release.release_id); $($release.item_count) records; generated $($release.generated_at). R2 remains the live serving origin."
        & $gh.Source release create $release.tag @assetPaths --repo $repo --title "JTorrent index $($release.release_id)" --notes $notes
    } else {
        throw "GitHub Release lookup failed: $releaseLookupMessage"
    }
    if ($LASTEXITCODE -ne 0) { throw "GitHub Release backup failed; R2 was not changed." }
}

if (-not $SkipR2) {
    & $python scripts/publish_release.py r2 --public-dir public
    if ($LASTEXITCODE -ne 0) { throw "R2 publish failed. The local and GitHub Release backups remain intact, and the prior R2 current pointer was preserved." }
}

Write-Host "Index update complete: $($release.release_id) with $($release.item_count) records."
