param([switch]$Login)

$ErrorActionPreference = 'Stop'
$codexPath = (Get-Command codex -CommandType Application -ErrorAction SilentlyContinue).Path
if (!$codexPath) {
    # Codex Desktop keeps its CLI in a versioned directory outside the user's PATH.
    $desktopBin = Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\bin'
    $bundledCli = Get-ChildItem -Path "$desktopBin\*\codex.exe" -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    $codexPath = $bundledCli.FullName
}
if (!$codexPath) {
    throw 'Codex CLI was not found. Install it with npm install -g @openai/codex, open a new PowerShell window, and rerun this script.'
}

. "$PSScriptRoot\scripts\lyra-token.ps1"
$token = Get-LyraAccessToken -Login:$Login
$expiry = [DateTimeOffset]::FromUnixTimeSeconds([long]$token.expires_on).ToLocalTime()
Write-Host "Lyra access token expires at $expiry."

$previousToken = $env:LYRA_MCP_TOKEN
Push-Location -LiteralPath $PSScriptRoot
try {
    $env:LYRA_MCP_TOKEN = $token.accessToken
    & $codexPath
} finally {
    $env:LYRA_MCP_TOKEN = $previousToken
    Pop-Location
}
