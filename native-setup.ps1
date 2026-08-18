# ============================================================
# Native (NO Docker/WSL) setup for the AI gateway.
# Downloads the new-api Windows binary and installs Python deps.
# Requires: Python 3.11+ available as 'python' on PATH.
# ============================================================
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host '[ERROR] python not found on PATH. Install Python 3.11+ and re-run.' -ForegroundColor Red
    exit 1
}

# 1. Python venv
$venv = "$root\.venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    Write-Host '[1/3] Creating Python venv (._venv) ...'
    python -m venv $venv
}
Write-Host '[1/3] venv ready'

# 2. Download new-api Windows binary (official release)
$binDir = "$root\bin"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
$exe = "$binDir\new-api.exe"
if (-not (Test-Path $exe)) {
    Write-Host '[2/3] Downloading new-api Windows binary (~115MB, one-time) ...'
    $ver = 'v1.0.0-rc.24'
    $url = "https://github.com/QuantumNous/new-api/releases/download/$ver/new-api-$ver.exe"
    Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing
}
Write-Host '[2/3] new-api binary ready'

# 3. Python dependencies (cc-adapter + manager)
Write-Host '[3/3] Installing Python dependencies (this can take a few minutes) ...'
& "$venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
& "$venv\Scripts\python.exe" -m pip install -r "$root\manager\requirements.txt" -r "$root\cc-adapter\requirements.txt"

Write-Host ''
Write-Host 'Setup done. Start with native-start.cmd (no Docker needed).' -ForegroundColor Green
Write-Host 'Note: stop the Docker version first if it is running (stop.cmd).'