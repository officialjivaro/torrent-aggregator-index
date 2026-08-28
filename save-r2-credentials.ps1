[CmdletBinding()]
param(
    [string]$AccountId,
    [string]$CredentialFile,
    [switch]$IncludeApiToken
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not $CredentialFile) {
    $CredentialFile = Join-Path (Split-Path -Parent $PSScriptRoot) "2_credentials\torrent-aggregator-index.env"
}
if (-not $AccountId) {
    $AccountId = (Read-Host "Cloudflare account ID").Trim()
}
if (-not $AccountId) { throw "Cloudflare account ID is required." }

function Read-SecretText {
    param([Parameter(Mandatory)][string]$Prompt)

    $secure = Read-Host $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

$accessKey = Read-SecretText -Prompt "R2 Access Key ID"
$secretKey = Read-SecretText -Prompt "R2 Secret Access Key"
if (-not $accessKey -or -not $secretKey) { throw "Both R2 credentials are required." }
$apiToken = if ($IncludeApiToken) { Read-SecretText -Prompt "Cloudflare API token" } else { $null }
if ($IncludeApiToken -and -not $apiToken) { throw "The Cloudflare API token is required when -IncludeApiToken is used." }

$parent = Split-Path -Parent $CredentialFile
if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
    New-Item -ItemType Directory -Path $parent | Out-Null
}

$content = @(
    "# Local credentials for torrent-aggregator-index. Never commit this file."
    "CLOUDFLARE_ACCOUNT_ID=$AccountId"
    "R2_ACCESS_KEY_ID=$accessKey"
    "R2_SECRET_ACCESS_KEY=$secretKey"
)
if ($apiToken) { $content += "CLOUDFLARE_API_TOKEN=$apiToken" }
[IO.File]::WriteAllLines($CredentialFile, $content, [Text.UTF8Encoding]::new($false))

if ($env:OS -eq "Windows_NT") {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls.exe $CredentialFile /inheritance:r | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Unable to disable inherited permissions on the credential file." }
    & icacls.exe $CredentialFile /grant:r "$($currentUser):(F)" "*S-1-5-18:(F)" "*S-1-5-32-544:(F)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Unable to restrict access to the credential file." }
}

$accessKey = $null
$secretKey = $null
$apiToken = $null
Write-Host "R2 credentials saved to $CredentialFile"
