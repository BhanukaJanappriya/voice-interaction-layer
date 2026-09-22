<#
Stops the background backend and frontend processes started by start.ps1,
using the PIDs saved in logs\*.pid.
#>

$root = Split-Path -Parent $PSScriptRoot
$logsDir = Join-Path $root "logs"

foreach ($name in "backend", "frontend") {
    $pidFile = Join-Path $logsDir "$name.pid"
    if (-not (Test-Path $pidFile)) {
        Write-Host "$name.pid not found; nothing to stop for $name."
        continue
    }

    $processId = Get-Content $pidFile | Select-Object -First 1
    if ($processId -and (Get-Process -Id $processId -ErrorAction SilentlyContinue)) {
        Stop-Process -Id $processId -Force
        Write-Host "Stopped $name (PID $processId)."
    } else {
        Write-Host "$name (PID $processId) was not running."
    }
    Remove-Item $pidFile -Force
}
