. "$PSScriptRoot\scripts\local-processes.ps1"

$lock = Lock-LyraRun
try {
    Stop-LyraProcess 'lyra'
    Stop-LyraProcess 'ollama'
    Write-Host 'Lyra application services stopped.'
} finally { $lock.Dispose() }
