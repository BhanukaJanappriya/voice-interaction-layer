<#
Starts the app, one FastAPI/uvicorn process that serves the frontend, the
admin portal, the voice WebSocket, the APIs and the MCP server, as a
background process with no attached console window, using pythonw.exe
instead of python.exe. Output goes to logs\backend.log instead of a
terminal, and the process ID is saved to logs\backend.pid so stop.ps1 can
find and stop it later. Run this once, then close the terminal if you
want; the server keeps running because it is not attached to it.
#>

$root = Split-Path -Parent $PSScriptRoot
$venvPythonw = Join-Path $root "venv\Scripts\pythonw.exe"
$logsDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

if (-not (Test-Path $venvPythonw)) {
    Write-Host "Could not find $venvPythonw. Create the virtual environment first (see README.md Setup)."
    exit 1
}

function Start-Background($name, $workingDir, $arguments) {
    $pidFile = Join-Path $logsDir "$name.pid"
    if (Test-Path $pidFile) {
        $existingId = Get-Content $pidFile | Select-Object -First 1
        if ($existingId -and (Get-Process -Id $existingId -ErrorAction SilentlyContinue)) {
            Write-Host "$name is already running (PID $existingId). Run stop.ps1 first if you want to restart it."
            return
        }
    }

    $proc = Start-Process -FilePath $venvPythonw `
        -ArgumentList $arguments `
        -WorkingDirectory $workingDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logsDir "$name.log") `
        -RedirectStandardError (Join-Path $logsDir "$name.err.log") `
        -PassThru

    $proc.Id | Out-File -FilePath $pidFile -Encoding ascii
    Write-Host "$name started in the background (PID $($proc.Id)); log: logs\$name.log"
}

Start-Background "backend" $root "-m backend --host 127.0.0.1"

Write-Host ""
Write-Host "Open http://127.0.0.1:8000/ in your browser (admin portal: http://127.0.0.1:8000/admin/)."
Write-Host "Run scripts\stop.ps1 to stop it."
