# ============================================================
# Stop the AI gateway stack. Data is kept (./data) for the next start.
# Usage: double-click stop.cmd, or:
#   powershell -NoProfile -ExecutionPolicy Bypass -File stop.ps1
# ============================================================
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host '[ERROR] docker not found.' -ForegroundColor Red
    exit 1
}

Write-Host 'Stopping AI gateway stack (containers stopped, data kept) ...'
docker compose -f "$root\docker-compose.yml" down
Write-Host ''
Write-Host 'Done. Restart anytime with start.cmd (data is preserved).' -ForegroundColor Green