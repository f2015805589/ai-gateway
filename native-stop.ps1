# ============================================================
# Stop the natively-run AI gateway processes.
# ============================================================
$ErrorActionPreference = 'SilentlyContinue'

Write-Host 'Stopping native AI gateway processes ...'

# new-api.exe
Get-Process -Name 'new-api' -ErrorAction SilentlyContinue | Stop-Process -Force

# cc-adapter + manager (python processes with these command lines)
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'cc_adapter' -or $_.CommandLine -match 'app\.main:app' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Write-Host 'Done.' -ForegroundColor Green