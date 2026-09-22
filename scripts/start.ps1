<#
Starts the backend (FastAPI/uvicorn) and frontend (static file server) as
background processes with no attached console window, using pythonw.exe
instead of python.exe. Output goes to logs\*.log instead of a terminal, and
process IDs are saved to logs\*.pid so stop.ps1 can find and stop them
later. Run this once, then close the terminal if you want; the servers
keep running because they are not attached to it.
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

Start-Background "backend" (Join-Path $root "backend") "-m uvicorn server:app --host 127.0.0.1 --port 8000"
Start-Background "frontend" (Join-Path $root "frontend") "-m http.server 5500"

Write-Host ""
Write-Host "Open http://127.0.0.1:5500/index.html in your browser."
Write-Host "Run scripts\stop.ps1 to stop both servers."
