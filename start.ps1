. "$PSScriptRoot\scripts\local-processes.ps1"

function Test-Ready($url) {
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        return $response.StatusCode -eq 200
    } catch { return $false }
}

function Wait-Ready($proc, $url, $name) {
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while (!$proc.HasExited -and [DateTime]::UtcNow -lt $deadline) {
        if (Test-Ready $url) { return }
        Start-Sleep -Milliseconds 250
    }
    throw "$name failed to start. See $runDir\$name.err.log."
}

$lock = Lock-LyraRun
$started = @()
try {
    $python = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (!(Test-Path -LiteralPath $python)) { throw 'Create the Python environment as described in ReadMe.md.' }
    if (!(Test-Path -LiteralPath (Join-Path $projectRoot 'node_modules\@azure\msal-browser\lib\msal-browser.min.js'))) {
        throw 'Run npm ci in the Lyra project first.'
    }
    # Use the same .env parser and environment precedence as FastAPI.
    $modelUrl = 'import os, sys; from dotenv import load_dotenv; load_dotenv(sys.argv[1]); print(os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"))' |
        & $python - (Join-Path $projectRoot '.env')
    if ($LASTEXITCODE -ne 0) { throw 'Could not read Ollama settings. Check the Python environment.' }
    $modelUri = [uri]$modelUrl

    $api = Get-LyraProcess 'lyra'
    if (!$api) {
        $listeners = [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
        if ($listeners.Port -contains 8765) { throw 'Port 8765 is occupied. Stop the existing server before starting Lyra.' }
        $api = Start-LyraProcess 'lyra' $python @('-m','uvicorn','app:app','--host','127.0.0.1','--port','8765','--no-access-log')
        $started += 'lyra'
    }
    try { Wait-Ready $api 'http://127.0.0.1:8765/' 'lyra' }
    catch { throw "$_ Check that the PostgreSQL instance configured by DATABASE_URL is running." }

    if ($modelUri.IsLoopback) {
        $tagsUrl = $modelUrl.TrimEnd('/') + '/api/tags'
        if (!(Test-Ready $tagsUrl)) {
            $ollama = Get-LyraProcess 'ollama'
            if (!$ollama) {
                $oldEndpoint = $env:OLLAMA_HOST
                try {
                    $env:OLLAMA_HOST = $modelUrl
                    $ollama = Start-LyraProcess 'ollama' (Get-Command ollama.exe).Source @('serve')
                    $started += 'ollama'
                } finally { $env:OLLAMA_HOST = $oldEndpoint }
            }
            Wait-Ready $ollama $tagsUrl 'ollama'
        }
    }
    Write-Host 'Lyra is ready at http://localhost:8765/'
} catch {
    foreach ($name in $started) { Stop-LyraProcess $name }
    throw
} finally { $lock.Dispose() }
