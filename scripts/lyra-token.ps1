param([switch]$McpHeaders)

function Get-LyraAccessToken([switch]$Login) {
    try {
        $config = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/auth/config?site=admin' -TimeoutSec 5
    } catch {
        throw 'Could not read the Lyra admin sign-in settings. Start Lyra with .\start.ps1 and check its Entra configuration.'
    }
    $tenant = [guid](([uri]$config.authority).AbsolutePath.Trim('/'))
    $scope = $config.scope
    if ($Login) {
        az login --tenant $tenant --scope $scope --allow-no-subscriptions --output none
        if ($LASTEXITCODE -ne 0) { throw 'Azure CLI sign-in failed.' }
    }
    $result = az account get-access-token --tenant $tenant --scope $scope --output json
    if ($LASTEXITCODE -ne 0) { throw 'Token acquisition failed. Sign in to Azure CLI with the Lyra admin scope.' }
    $token = $result | ConvertFrom-Json
    if (!$token.accessToken -or !$token.expires_on) { throw 'Azure CLI returned no usable token or expiration time.' }
    return $token
}

if ($McpHeaders) {
    $ErrorActionPreference = 'Stop'
    $token = Get-LyraAccessToken
    @{Authorization = "Bearer $($token.accessToken)"} | ConvertTo-Json -Compress
}
