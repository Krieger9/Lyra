# Shared by the Windows start and stop entry points.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runDir = Join-Path $projectRoot '.lyra\run'
$null = New-Item -ItemType Directory -Force -Path $runDir

function Lock-LyraRun {
    try {
        [IO.File]::Open((Join-Path $runDir 'control.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    } catch [IO.IOException] {
        throw 'Another Lyra start or stop is in progress.'
    }
}

function Get-LyraProcess($name) {
    $file = Join-Path $runDir "$name.json"
    if (Test-Path -LiteralPath $file) {
        $record = Get-Content -LiteralPath $file -Raw | ConvertFrom-Json
        $proc = Get-Process -Id $record.id -ErrorAction SilentlyContinue
        if ($proc -and $proc.StartTime.ToUniversalTime().Ticks.ToString() -eq $record.started -and
            $proc.Path -eq $record.path) { return $proc }
        Remove-Item -LiteralPath $file
    }
}

function Start-LyraProcess($name, $executable, $arguments) {
    $childId = & $python (Join-Path $PSScriptRoot 'launch-background.py') $projectRoot $runDir $name $executable @arguments
    if ($LASTEXITCODE -ne 0) { throw "Could not launch $name." }
    $proc = Get-Process -Id $childId -ErrorAction Stop
    try {
        @{ id = $proc.Id; started = $proc.StartTime.ToUniversalTime().Ticks.ToString(); path = $executable } |
            ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runDir "$name.json")
    } catch {
        if (!$proc.HasExited) { taskkill.exe /PID $proc.Id /T /F | Out-Null }
        throw
    }
    return $proc
}

function Stop-LyraProcess($name) {
    $proc = Get-LyraProcess $name
    if ($proc) {
        # The Windows venv launcher and Ollama can each spawn worker processes.
        taskkill.exe /PID $proc.Id /T /F | Out-Null
        if ($LASTEXITCODE -ne 0 -and !$proc.HasExited) { throw "Could not stop $name (PID $($proc.Id))." }
        $null = $proc.WaitForExit(5000)
        Remove-Item -LiteralPath (Join-Path $runDir "$name.json")
        Write-Host "Stopped $name."
    }
}
