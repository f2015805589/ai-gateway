# ============================================================
# Start the AI gateway natively on Windows (no Docker/WSL).
# Runs: new-api.exe (3000/3001) + cc-adapter (8080) + manager (8888)
# ============================================================
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$venvPy = "$root\.venv\Scripts\python.exe"

# 0. Prereqs
if (-not (Test-Path "$root\bin\new-api.exe") -or -not (Test-Path $venvPy)) {
    Write-Host '[ERROR] Run native-setup.ps1 first.' -ForegroundColor Red
    exit 1
}

# 1. Load .env into this process
if (Test-Path "$root\.env") {
    Get-Content "$root\.env" | Where-Object { $_ -match '^[A-Za-z_][A-Za-z0-9_]*=' } | ForEach-Object {
        $kv = $_ -split '=', 2
        Set-Item -Path "Env:$($kv[0])" -Value $kv[1]
    }
}
# manager reads adapter credentials under these names
$env:ADAPTER_ADMIN_PASSWORD = $env:CC_ADAPTER_ADMIN_PASSWORD
$env:ADAPTER_ACCESS_KEY = $env:CC_ADAPTER_ACCESS_KEY
$env:NEW_API_URL = 'http://localhost:3000'
$env:CC_ADAPTER_URL = 'http://localhost:8080'
$env:DATA_DIR = "$root\data\manager"

function Start-Background($name, $cmd, $argv, $workdir) {
    $p = Start-Process -FilePath $cmd -ArgumentList $argv -WorkingDirectory $workdir -PassThru -WindowStyle Minimized
    Start-Sleep -Milliseconds 800
    Write-Host ("  started $name (pid=$($p.Id))")
}

# 2. Start new-api (data under data\native\new-api; SQLite auto-created)
$naDir = "$root\data\native\new-api"
New-Item -ItemType Directory -Force -Path $naDir | Out-Null
Start-Process -FilePath "$root\bin\new-api.exe" -WorkingDirectory $naDir -WindowStyle Minimized | Out-Null
Write-Host '  started new-api (3000 API / 3001 panel)'

# 3. cc-adapter
Start-Background 'cc-adapter' $venvPy @('-m', 'cc_adapter') "$root\cc-adapter"

# 4. manager
Start-Background 'manager' $venvPy @('-m', 'uvicorn', 'app.main:app', '--app-dir', "$root\manager", '--host', '127.0.0.1', '--port', '8888') $root

Start-Sleep -Seconds 8
Write-Host ''
Write-Host 'Dashboard : http://localhost:8888' -ForegroundColor Cyan
Write-Host 'API       : http://localhost:3000/v1' -ForegroundColor Cyan
Write-Host 'Panel     : http://localhost:3001' -ForegroundColor Cyan
Start-Process 'http://localhost:8888'
Write-Host 'Stop them with native-stop.cmd'